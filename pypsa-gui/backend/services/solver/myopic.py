"""
The myopic / limited-foresight driver.

Carved out of `services/solver_service.py`, and it sits at the top of this
package's DAG: it is the one module that depends on nearly all the others —
`runtime` for the stop check, `diagnostics` for post-solve emission,
`objective` for the result rescale, `assumptions` for outage resolution and
index normalisation, and `vintage_store` for the freeze store it shares with
`assumptions`. It still imports nothing from `solver_service`.

The highest-risk cluster in the file: it mutates capacities between iterations
and owns the freeze/defer bookkeeping that decides what a later period is
allowed to build.

`_NOM_TRIPLES` belongs here. All four of its uses are in this loop, despite an
earlier reading that put it with the modelling assumptions.
"""
import math
import threading

import pandas as pd

from services.pypsa_service import PyPSAService
from services.solver.assumptions import (
    _compute_loss_atol,
    _normalise_dynamic_indexes,
    resolve_branch_outages,
)
from services.solver.diagnostics import (
    _emit_core_post_solve_diagnostics,
    _log_cost_decomposition_post_solve,
    _log_sclopf_post_solve,
)
from services.solver.objective import _rescale_results_for_objective
from services.solver.runtime import _check_stop
from services.solver.vintage_store import _MYOPIC_VINTAGE_SOURCE, _frozen_vintage_store


# ── Myopic foresight driver ───────────────────────────────────────────────────
# Each investment period is solved sequentially; capacities decided in one
# period are frozen (extendable=False, p_nom = p_nom_opt) before the next
# iteration. Two flavours, both routed through _run_myopic_foresight:
#   • Phase 1: pure myopic — each iteration sees ONLY its period's snapshots.
#     Zero forward visibility (cfg.lf_aggregate_future == False).
#   • Phase 2: limited foresight — each iteration sees its period at full
#     hourly detail PLUS representative-week snapshots for every future
#     period, clustered via tsam. The future-period snapshots carry weight
#     overrides on n.snapshot_weightings so the LP costs scale to "this
#     representative stands in for N actual weeks". Opt-in via
#     cfg.lf_aggregate_future == True.
# Both flavours share rolling-loop, freeze-after-solve, vintage-results
# capture, and undo bookkeeping.

# (component_class, attr_name, capacity_field) triples for the six PyPSA
# component classes that carry a nominal capacity decision. Stored once at
# module level so freeze + downstream helpers stay consistent.
_NOM_TRIPLES = [
    ("Generator",    "generators",    "p_nom"),
    ("StorageUnit",  "storage_units", "p_nom"),
    ("Store",        "stores",        "e_nom"),
    ("Link",         "links",         "p_nom"),
    ("Line",         "lines",         "s_nom"),
    ("Transformer",  "transformers",  "s_nom"),
]


def _build_iteration_snapshots(n, current_period, all_periods, cfg):
    """
    Return ``(snapshots, weight_overrides)`` for one myopic iteration.

    ``snapshots`` is a slice of ``n.snapshots`` PyPSA's ``n.optimize()``
    accepts directly via the ``snapshots=`` kwarg.

    ``weight_overrides`` is a `pd.Series` keyed by snapshot of multipliers
    to write into ``n.snapshot_weightings`` for the duration of this LP. The
    rolling driver records originals into ``undo_actions`` and reverts after
    the solve. ``None`` when no overrides are needed (Phase 1 path).

    Phase 1 — pure myopic, ``cfg.lf_aggregate_future == False``:
        Only ``current_period``'s snapshots; no weight overrides.

    Phase 2 — limited foresight, ``cfg.lf_aggregate_future == True``:
        ``current_period`` at full detail **+** representative-week /
        typical-day snapshots for every period > current_period (clustered
        via ``services/time_aggregation_service``). Each representative
        snapshot's weight is scaled by its cluster size so the LP's
        objective / energy-balance terms see the future-period costs at the
        right magnitude.
    """
    if not isinstance(n.snapshots, pd.MultiIndex):
        # Single-period (flat) snapshots — myopic degenerates to a single
        # solve over the whole index. The validation layer should have
        # caught this and refused the strategy, but be defensive.
        return n.snapshots, None
    period_level = n.snapshots.get_level_values(0)
    current_mask = period_level == current_period
    # `n.snapshots[mask]` preserves the MultiIndex with both levels intact —
    # critical because PyPSA's multi-period LP keys investment_period
    # weightings by the level-0 value of each snapshot.
    current_slice = n.snapshots[current_mask]

    if not bool(getattr(cfg, "lf_aggregate_future", False)):
        return current_slice, None

    # Phase 2: append representative snapshots from each future period,
    # accumulating the weight overrides as we go. tsam clustering is cached
    # per-(period, cfg-fingerprint) so subsequent rolling iterations don't
    # re-run the same clustering work.
    from services.time_aggregation_service import aggregate_period_snapshots
    future_periods = [p for p in all_periods if p > current_period]
    if not future_periods:
        return current_slice, None

    combined = current_slice
    overrides = pd.Series(dtype=float)
    for fp in future_periods:
        agg = aggregate_period_snapshots(n, fp, cfg)
        if len(agg.snapshots) == 0:
            continue
        combined = combined.append(agg.snapshots)
        overrides = pd.concat([overrides, agg.weights])
    # Drop any accidental duplicates (shouldn't happen — periods are
    # disjoint by construction — but be defensive against odd MultiIndex
    # quirks in pandas).
    if combined.has_duplicates:
        combined = pd.MultiIndex.from_tuples(
            list(dict.fromkeys(combined.to_list())),
            names=combined.names,
        )
    return combined, (overrides if not overrides.empty else None)




def _clear_myopic_build_periods(n) -> None:
    """
    Drop `vintage_results` entries left by a PREVIOUS myopic run.

    `apply_vintage_bounds` resets the whole dict at solve start, but it returns
    early when the user has no per-period bounds — which is exactly the case
    this feature exists for. Without this, a myopic run's build periods would
    accumulate across solves and the Capacity Expansion chart would show
    capacity that is no longer there.
    """
    meta = getattr(n, "meta", None)
    if not isinstance(meta, dict):
        return
    root = meta.get("vintage_results")
    if not isinstance(root, dict):
        return
    for comp_class in list(root.keys()):
        by_asset = root.get(comp_class)
        if not isinstance(by_asset, dict):
            continue
        for name in [
            k for k, v in by_asset.items()
            if isinstance(v, dict) and v.get("source") == _MYOPIC_VINTAGE_SOURCE
        ]:
            by_asset.pop(name, None)
        if not by_asset:
            root.pop(comp_class, None)


def _record_myopic_build_period(
    n, comp_class: str, pnom_field: str, names, initial, decided, period,
) -> None:
    """
    Record WHICH PERIOD decided each asset's capacity, through the existing
    ``vintage_results`` channel the Capacity Expansion view already reads.

    Why this is needed: a myopic run freezes assets that carry the default
    ``build_year = 0``, and `_freeze_period_capacities` deliberately does not
    touch ``build_year`` — that column drives PyPSA's activity mask, so writing
    the freeze period into it would change which periods the asset is active in
    and when it retires. Changing the model to populate a chart is the wrong
    trade.

    But the chart groups strictly by ``build_year > 0``, so a myopic run without
    per-period vintage bounds produced an EMPTY "Capacity expansion by period"
    section — precisely the run where the user most needs to see that everything
    was decided in the first period and nothing could be added later.

    ``vintage_results`` already carries ``{class: {asset: {capacity_field,
    initial_capacity, periods: [{build_year, p_nom_opt, ...}]}}}`` and the
    frontend already emits one row per entry, so filling it in needs no
    frontend change. ``p_nom_opt`` here is the vintage's OWN contribution (the
    frontend accumulates it onto ``initial_capacity``), i.e. the delta this
    period added — not the cumulative total.

    Transient vintage rows are skipped: `vintage_service` writes the real
    per-vintage breakdown for their PARENT at restore, and a competing entry
    under the vintage's own name would double-count it in the chart.
    """
    meta = getattr(n, "meta", None)
    if not isinstance(meta, dict):
        return
    try:
        transient = PyPSAService.get_transient_rows(comp_class)
    except Exception:  # noqa: BLE001 — never let bookkeeping break a solve
        transient = set()
    try:
        root = meta.setdefault("vintage_results", {})
        by_asset = root.setdefault(comp_class, {})
        for nm in names:
            name = str(nm)
            if name in transient:
                continue
            existing = by_asset.get(name)
            # Never overwrite vintage_service's richer breakdown.
            if isinstance(existing, dict) and existing.get("source") != _MYOPIC_VINTAGE_SOURCE:
                continue
            ini = float(initial.loc[nm])
            opt = float(decided.loc[nm]) if nm in decided.index else float(initial.loc[nm])
            delta = opt - ini
            if not math.isfinite(delta) or delta <= 1e-6:
                continue  # nothing was added in this period — no row to draw
            by_asset[name] = {
                "capacity_field": pnom_field,
                "initial_capacity": ini,
                "source": _MYOPIC_VINTAGE_SOURCE,
                "periods": [{
                    "build_year": int(period),
                    pnom_field + "_opt": delta,
                    "p_nom_opt": delta,
                }],
            }
        if not by_asset:
            root.pop(comp_class, None)
    except Exception:  # noqa: BLE001 — a chart hint must never abort a solve
        return


def _freeze_period_capacities(n, period, undo_actions, phase) -> int:
    """
    For every extendable asset *active in `period`* (build_year ≤ period
    < build_year + lifetime), set ``p_nom = p_nom_opt`` and flip
    ``p_nom_extendable = False``. Subsequent myopic iterations then see the
    asset as fixed existing capacity rather than a fresh decision variable.

    Returns the number of assets frozen. Original values are recorded in
    ``undo_actions`` so the post-rolling restore puts the network back to
    its pre-solve state — exactly like the vintage-bounds expansion path
    does.
    """
    frozen = 0
    for comp_class, attr, pf in _NOM_TRIPLES:
        df = getattr(n, attr, None)
        if df is None or df.empty:
            continue
        ext_col = f"{pf}_extendable"
        opt_col = f"{pf}_opt"
        if ext_col not in df.columns or opt_col not in df.columns:
            continue
        if "build_year" not in df.columns or "lifetime" not in df.columns:
            continue
        by = df["build_year"]
        lt = df["lifetime"]
        is_active = (by <= period) & (period < by + lt)
        is_extendable = df[ext_col].astype(bool)
        targets = is_active & is_extendable
        if not targets.any():
            continue
        names = df.index[targets]
        # Snapshot originals BEFORE mutation so the undo entry is faithful.
        orig_ext = df.loc[names, ext_col].copy()
        orig_pnom = df.loc[names, pf].copy()
        opt_vals = df.loc[names, opt_col].fillna(0).astype(float)
        # Multi-port Link p_nom_opt recovery. PyPSA 1.1.2's
        # `_from_xarray` (pypsa/components/array.py:31-52) calls
        # `expand_dims(name=c.names)` on the linopy variable's solution;
        # rows whose names weren't in the variable's coords get filled
        # with NaN which converts to -0.0 when written to the static
        # column. Observed on multi-port Link vintages (heat pumps with
        # bus2 set). The real solved values are still in
        # `n.model.solution[f"{comp_class}-{pf}"]` — recover them before
        # they propagate into the freeze store + downstream
        # vintage_results reporting.
        recovered = 0
        if hasattr(n, "model") and n.model is not None:
            try:
                sol = n.model.solution.get(f"{comp_class}-{pf}")
            except (AttributeError, KeyError):
                sol = None
            if sol is not None:
                try:
                    sol_dim = sol.dims[0]
                except (AttributeError, IndexError):
                    sol_dim = None
                if sol_dim:
                    for i, nm in enumerate(names):
                        cur = float(opt_vals.iat[i])
                        # -0.0 has signbit set; ordinary 0.0 does not.
                        # Treat -0.0 as the "missing-coord" sentinel
                        # that array.py's expand_dims produces; ordinary
                        # 0.0 (the LP genuinely picked zero) stays as-is.
                        if cur == 0.0 and math.copysign(1.0, cur) == -1.0:
                            try:
                                real = float(sol.sel({sol_dim: str(nm)}).item())
                            except (KeyError, ValueError, TypeError):
                                real = None
                            if real is not None and math.isfinite(real) and real >= 0:
                                opt_vals.iat[i] = real
                                recovered += 1
        if recovered:
            phase(
                f"Period {period}: recovered {recovered} {comp_class} p_nom_opt value(s) "
                f"from linopy solution (multi-port Link safeguard)."
            )
        df.loc[names, pf] = opt_vals
        df.loc[names, ext_col] = False
        undo_actions.append(("col", attr, pf, names, orig_pnom))
        undo_actions.append(("col", attr, ext_col, names, orig_ext))
        # Persist this period's authoritative p_nom_opt before subsequent
        # iterations can overwrite it. PyPSA's `assign_solution` on later
        # periods does not reliably preserve p_nom_opt on non-extendable
        # rows — observed on multi-port Links (e.g. heat pumps with bus2
        # set) where the value gets reset to -0.0 after the next period's
        # LP, leaving `_capture_and_drop_vintages` to read 0 at restore
        # and report "no expansion" in vintage_results despite the LP
        # having sized the vintage and dispatched it.
        #
        # First attempt used `n.meta["__vintage_frozen_capacities"]`, but
        # PyPSA's `n.optimize()` appears to reset `n.meta` somewhere in
        # its setup, wiping our writes between myopic iterations. Move
        # the stash to a thread-local store that PyPSA can't touch. This
        # myopic loop runs its iterations sequentially on the solve's
        # worker thread, so the per-thread store persists across them; two
        # concurrent solves on different threads get separate stores.
        store = _frozen_vintage_store()
        for nm, val in zip(names, opt_vals):
            try:
                store[(comp_class, str(nm))] = float(val)
            except (TypeError, ValueError):
                pass
        _record_myopic_build_period(
            n, comp_class, pf, names, orig_pnom, opt_vals, period,
        )
        frozen += len(names)
    if frozen:
        phase(f"Period {period}: froze {frozen} extendable asset(s) at p_nom_opt.")
    return frozen


def _capture_extendable_p_nom_opt_to_frozen_store(n, phase) -> int:
    """
    Capture-only counterpart to ``_freeze_period_capacities`` for the
    non-myopic solve paths (full-horizon single-shot, SCLOPF, rolling).

    Walks every extendable asset across ``_NOM_TRIPLES`` and writes its
    current ``p_nom_opt`` into the per-thread freeze store
    (``_frozen_vintage_store()``). **No mutation** — does not touch
    ``p_nom`` or ``p_nom_extendable``. Call immediately after a successful
    ``n.optimize()`` and before any rescaling / post-solve pass.

    Why this exists: PyPSA's ``assign_solution`` writes
    ``p_nom_opt = -0.0`` to multi-port Links (e.g. heat pumps with
    ``bus2`` set) post-solve on non-myopic full-horizon runs too — not
    only between myopic iterations as previously assumed. Without this
    capture, ``_capture_and_drop_vintages`` falls back to the live
    ``p_nom_opt`` at restore time, observes ``-0.0`` (the NaN-only
    filter at vintage_service.py:594 passes it through), and reports
    "no expansion" in ``vintage_results`` despite the LP having sized
    the vintage and dispatched it.

    Single-port assets read correctly from the live ``p_nom_opt`` and
    are unaffected by this helper, but capturing them is harmless and
    keeps the freeze store consistent across solve modes.
    """
    captured = 0
    recovered = 0
    store = _frozen_vintage_store()
    has_model = hasattr(n, "model") and n.model is not None
    for comp_class, attr, pf in _NOM_TRIPLES:
        df = getattr(n, attr, None)
        if df is None or df.empty:
            continue
        ext_col = f"{pf}_extendable"
        opt_col = f"{pf}_opt"
        if ext_col not in df.columns or opt_col not in df.columns:
            continue
        ext_mask = df[ext_col].astype(bool)
        if not ext_mask.any():
            continue
        # Recovery channel for the -0.0 PyPSA writeback bug — see the
        # matching block in `_freeze_period_capacities` for full context.
        sol = None
        sol_dim = None
        if has_model:
            try:
                sol = n.model.solution.get(f"{comp_class}-{pf}")
            except (AttributeError, KeyError):
                sol = None
            if sol is not None:
                try:
                    sol_dim = sol.dims[0]
                except (AttributeError, IndexError):
                    sol_dim = None
        for nm, val in df.loc[ext_mask, opt_col].items():
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            if sol is not None and sol_dim and fval == 0.0 and math.copysign(1.0, fval) == -1.0:
                try:
                    real = float(sol.sel({sol_dim: str(nm)}).item())
                except (KeyError, ValueError, TypeError):
                    real = None
                if real is not None and math.isfinite(real) and real >= 0:
                    fval = real
                    recovered += 1
            store[(comp_class, str(nm))] = fval
            captured += 1
    if captured:
        msg = f"Captured p_nom_opt for {captured} extendable asset(s) into freeze store"
        if recovered:
            msg += f" ({recovered} recovered from linopy solution — multi-port Link safeguard)"
        else:
            msg += " (multi-port Link safeguard)"
        phase(msg + ".")
    return captured


def _defer_future_vintage_builds(n, current_period, undo_actions, phase) -> int:
    """
    For every extendable asset with ``build_year > current_period``, set
    ``p_nom_extendable = False`` AND ``p_nom = 0`` so the current iteration's
    LP cannot decide that vintage's capacity. The decision is left to the
    iteration whose ``current_period == vintage.build_year`` — i.e. each
    vintage gets exactly one shot at being sized, by the iteration that
    sees the full hourly view of its own period.

    Why this is required for myopic-foresight correctness:

      Without deferral, iter P sees future-period vintages (``build_year > P``)
      as extendable AND active in their period's snapshots. With limited-
      foresight aggregation those future periods are represented by tiny
      representative slices weighted up to their full annual hours, so the
      LP makes a build decision on partial information.

      Worse, the decision is then THROWN AWAY: when iter (build_year) runs
      and re-sees that vintage as extendable, ``_freeze_period_capacities``
      hasn't frozen it yet (it only freezes assets with
      ``build_year ≤ current_period``), so the LP re-decides from scratch.
      The result is an unstable build pattern that doesn't respond
      coherently to per-period cost signals (e.g. CO2 prices).

      Auto-discount on objective weights compounds the problem: future-
      period CAPEX gets PV-discounted (e.g. 0.873 for +2 years at 7 %), so
      the LP prefers to "build later" until the iteration where that becomes
      its only option. The build then anchors on the representative slice
      rather than the period's true peak.

    Effect across a 3-period horizon [2026, 2027, 2028]:
      * iter 2026 → defer Battery@2027, Battery@2028: only Battery@2026
        is decided.
      * iter 2027 → defer Battery@2028; Battery@2026 already frozen by
        prior iteration; Battery@2027 is decided here.
      * iter 2028 → nothing to defer; Battery@2028 is decided.

    Originals are recorded in ``undo_actions`` so the caller's per-iteration
    finally block reverts the deferral before the NEXT iteration runs —
    keeping the on-disk network identical to its pre-solve state.

    Returns the number of vintages deferred (logging / smoke-test only).
    """
    deferred = 0
    for comp_class, attr, pf in _NOM_TRIPLES:
        df = getattr(n, attr, None)
        if df is None or df.empty:
            continue
        ext_col = f"{pf}_extendable"
        if ext_col not in df.columns or "build_year" not in df.columns:
            continue
        future_mask = df["build_year"] > current_period
        ext_mask = df[ext_col].astype(bool)
        targets = future_mask & ext_mask
        if not targets.any():
            continue
        names = df.index[targets]
        orig_ext = df.loc[names, ext_col].copy()
        orig_pnom = df.loc[names, pf].copy()
        df.loc[names, ext_col] = False
        df.loc[names, pf] = 0.0
        undo_actions.append(("col", attr, ext_col, names, orig_ext))
        undo_actions.append(("col", attr, pf, names, orig_pnom))
        deferred += len(names)
    if deferred:
        phase(
            f"Period {current_period}: deferred {deferred} future-vintage "
            f"decision(s) to their build-year iteration (myopic foresight)."
        )
    return deferred


def _outages_active_in_period(
    network,
    current_period: int,
    outages: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """
    Filter the SCLOPF outage list down to branches that exist in
    ``current_period``. A line/transformer with ``build_year > current_period``
    isn't built yet, so contingencies against it aren't meaningful — it
    silently re-enters the contingency set in the iteration whose
    ``current_period >= build_year`` (and stays in until
    ``current_period >= build_year + lifetime``).

    Returns the filtered list, preserving the input ordering so downstream
    `tuple(...)` calls remain stable across iterations.
    """
    if not outages:
        return []
    active: list[tuple[str, str]] = []
    for comp, name in outages:
        attr = "lines" if comp == "Line" else "transformers" if comp == "Transformer" else None
        if attr is None:
            continue
        df = getattr(network, attr, None)
        if df is None or df.empty or name not in df.index:
            continue
        # build_year / lifetime are optional columns; default to "always active".
        try:
            by = float(df.at[name, "build_year"]) if "build_year" in df.columns else 0.0
        except (TypeError, ValueError):
            by = 0.0
        try:
            lt = float(df.at[name, "lifetime"]) if "lifetime" in df.columns else float("inf")
        except (TypeError, ValueError):
            lt = float("inf")
        # Active iff build_year ≤ period < build_year + lifetime. Matches the
        # convention `_freeze_period_capacities` uses everywhere else in this file.
        if by <= current_period < by + lt:
            active.append((comp, name))
    return active


def _run_myopic_foresight(
    network,
    cfg: "SolverConfig",
    phase,
    merged_solver_options: dict,
    extra_fn,
    tmp_log,
    stop_event: threading.Event | None = None,
    iteration_undo: list | None = None,
) -> tuple[str, str, list]:
    """
    Sequential per-period multi-investment-period LP. Returns
    ``(status, condition, undo_actions)`` — the caller stacks the returned
    undo on top of ``_apply_modelling_assumptions``' restore.

    Algorithm:
      1. For each investment period in ascending order:
         a. Build the iteration's snapshot index (current period only in
            Phase 1; current + aggregated future in Phase 2).
         b. Solve ``n.optimize(snapshots=<window>,
                               multi_investment_periods=True, …)``.
         c. Freeze every extendable asset active in this period at its
            p_nom_opt (see ``_freeze_period_capacities``).
      2. If any iteration returns non-ok, abort and propagate the status.
         The caller's ``finally`` block runs all undos.

    Vintage interaction: future-period vintages (``build_year > current``)
    are explicitly deferred via ``_defer_future_vintage_builds`` before each
    LP call — pinned to ``p_nom=0, extendable=False`` so the current
    iteration cannot decide their capacity. The deferral reverts in the
    per-iteration ``finally`` block so the NEXT iteration can decide its
    own vintage. The natural-decider iteration (``build_year ==
    current_period``) then sees the vintage as extendable and sizes it
    against the full hourly view of its own period — finally locking via
    ``_freeze_period_capacities`` once the LP solves. The vintage_results
    capture callback runs once at the very end during restore and
    aggregates the frozen p_nom_opt across vintages.
    """

    if not isinstance(network.snapshots, pd.MultiIndex):
        raise RuntimeError(
            "Myopic foresight requires multi-investment-period snapshots; "
            "got a flat DatetimeIndex. Validation should reject this earlier."
        )
    periods = sorted(int(p) for p in network.investment_periods)
    if not periods:
        raise RuntimeError(
            "Myopic foresight needs at least one investment period configured."
        )

    # SCLOPF setup — resolve the static contingency set once. Network topology
    # doesn't change between iterations (only p_nom / extendable flag), so the
    # outage candidate list is stable. The per-iteration filter then drops
    # candidates whose build_year > current_period (not yet built) and
    # candidates whose lifetime has expired (no longer in service). If the
    # resolver returns an empty set, fall back to plain myopic LOPF with a
    # diagnostic — matches the existing full-horizon SCLOPF behaviour.
    use_sclopf = bool(getattr(cfg, "sclopf", False))
    all_outages: list[tuple[str, str]] = []
    sclopf_scope = getattr(cfg, "sclopf_scope", "horizon") or "horizon"
    if use_sclopf:
        all_outages = resolve_branch_outages(network, cfg)
        if not all_outages:
            phase(
                "SCLOPF requested but no branches matched the selection — "
                "falling back to plain myopic LOPF for every iteration."
            )
            use_sclopf = False
        else:
            phase(
                f"SCLOPF + myopic active: {len(all_outages)} contingency "
                f"candidate(s) (scope={sclopf_scope}). Per-iteration filter "
                f"will drop branches with build_year > current_period."
            )

    # Accept the caller's list by reference so a SolveAborted mid-loop
    # leaves the partial undo entries in the caller's scope. Without this
    # the inner function's local list disappears when SolveAborted
    # propagates up, and `_freeze_period_capacities` rows from completed
    # periods (extendable=False, p_nom=p_nom_opt) leak onto the
    # post-solve network state.
    if iteration_undo is None:
        iteration_undo = []
    # Clear the per-period objective accumulator so re-solves don't compound
    # across runs. Populated inside the loop below, consumed by the outer
    # status worker to compute the horizon-total objective.
    network._myopic_period_objectives = []
    # Drop the previous myopic run's build-period records before this run
    # writes its own — see `_clear_myopic_build_periods` for why
    # `apply_vintage_bounds`' reset does not cover this case.
    _clear_myopic_build_periods(network)
    for i, current_period in enumerate(periods, start=1):
        # Honour abort between period iterations. Single-period LPs are
        # still uninterruptible (the C-level solver doesn't yield), but on
        # a multi-period myopic run the user gets up to N abort points,
        # each at the start of the next period's LP build.
        _check_stop(stop_event, phase, f"before myopic period {current_period} ({i}/{len(periods)})")
        # Normalise dynamic indexes BEFORE each iteration's LP build. The
        # previous iteration's `n.optimize()` ran `assign_duals` which
        # populated `mu_*` dual `_t` frames over THIS iteration's `sns`
        # subset; if iteration N+1 has a different (e.g. wider) `sns`,
        # those frames carry stale rows that break the next LP's internal
        # `.sel(snapshot=…)` calls with cryptic `dim_0` / `cannot include
        # dtype 'M' in a buffer` errors. The single up-front normalise
        # at run_simulation's entry only protects the FIRST iteration —
        # subsequent iterations need their own pass.
        fixed_idx = _normalise_dynamic_indexes(network, phase)
        if fixed_idx:
            phase(
                f"Myopic [{i}/{len(periods)}] period {current_period}: "
                f"normalised {fixed_idx} stale dynamic index/indexes pre-LP."
            )
        sns, weight_overrides = _build_iteration_snapshots(
            network, current_period, periods, cfg,
        )
        if len(sns) == 0:
            phase(f"Period {current_period}: no snapshots in slice — skipping.")
            continue
        # Skip the LP when no extendable assets are active in `current_period`.
        # This happens after earlier iterations froze every relevant capacity
        # (typical for users without vintage_bounds whose extendable assets all
        # have build_year ≤ current_period). PyPSA's `create_model` walks
        # `c.periodized_cost` even on empty ext_i and trips internal nyears
        # checks in that path, so it's both faster AND more robust to skip.
        # Capacity for this period is already fully determined by frozen rows
        # from earlier iterations; vintage_results capture is unaffected
        # because the parent's p_nom_opt is recomputed at restore() from the
        # frozen vintages, not from this LP.
        has_active_extendable = False
        for _cls, _attr, _pf in _NOM_TRIPLES:
            df = getattr(network, _attr, None)
            if df is None or df.empty:
                continue
            ext_col = f"{_pf}_extendable"
            if ext_col not in df.columns or "build_year" not in df.columns or "lifetime" not in df.columns:
                continue
            by = df["build_year"]
            lt = df["lifetime"]
            is_active = (by <= current_period) & (current_period < by + lt)
            is_ext = df[ext_col].astype(bool)
            if (is_active & is_ext).any():
                has_active_extendable = True
                break
        if not has_active_extendable:
            # No EXPANSION decisions remain for this period (capacity is frozen
            # from earlier iterations). BUT we must still verify the frozen
            # fleet can OPERATIONALLY serve this period's load — and produce its
            # dispatch. The previous behaviour `continue`d here, skipping the
            # solve entirely, which (a) masked operational infeasibility as a
            # clean "optimal" run (the frozen capacity literally cannot serve a
            # higher-demand later period → infeasible, reported as success), and
            # (b) left the period with no dispatch results. Solve dispatch-only
            # over this period's snapshots instead.
            phase(
                f"Myopic [{i}/{len(periods)}] period {current_period}: no new "
                f"extendable assets — running operational dispatch to verify "
                f"feasibility (capacity already frozen)."
            )
            try:
                op_status, op_condition = network.optimize(
                    snapshots=sns,
                    solver_name=cfg.solver_name,
                    multi_investment_periods=True,
                    extra_functionality=extra_fn,
                    log_fn=str(tmp_log),
                    solver_options=merged_solver_options,
                    assign_all_duals=True,
                )
            except Exception as exc:
                # PyPSA can trip its `periodized_cost` / `nyears` check when the
                # extendable index is empty under multi_investment_periods
                # (notably with differing period durations). Retry as a plain
                # single-period operational solve over the same snapshots — same
                # feasibility verdict + dispatch, avoiding that code path.
                phase(
                    f"Myopic period {current_period}: multi-period dispatch solve "
                    f"tripped ({type(exc).__name__}: {exc}); retrying operational."
                )
                op_status, op_condition = network.optimize(
                    snapshots=sns,
                    solver_name=cfg.solver_name,
                    extra_functionality=extra_fn,
                    log_fn=str(tmp_log),
                    solver_options=merged_solver_options,
                    assign_all_duals=True,
                )
            _rescale_results_for_objective(network)
            if op_status != "ok":
                phase(
                    f"Myopic period {current_period} is operationally INFEASIBLE "
                    f"({op_status}/{op_condition}) — the capacity frozen by earlier "
                    f"periods cannot serve this period's load. Aborting (later "
                    f"periods would only drift further)."
                )
                return op_status, op_condition, iteration_undo
            # Accumulate the period's operating cost into the horizon total, the
            # same way the expansion path does (so the reported objective covers
            # every period, not just the ones that expanded).
            try:
                _per_obj = float(network.objective) if getattr(network, "objective", None) is not None else 0.0
            except Exception:
                _per_obj = 0.0
            try:
                _per_const = float(getattr(network, "objective_constant", 0.0) or 0.0)
            except Exception:
                _per_const = 0.0
            if not hasattr(network, "_myopic_period_objectives"):
                network._myopic_period_objectives = []
            network._myopic_period_objectives.append(
                (int(current_period), _per_obj, _per_const)
            )
            continue
        # Phase 2: rewrite snapshot_weightings so the LP's view of each
        # future period sums to that period's TRUE total hours.
        #
        # PyPSA computes `n.nyears` (used by `periodized_cost`) by summing
        # snapshot_weightings.objective per period across the FULL index —
        # not just the LP's `sns` slice. If we only set weights for the
        # representative snapshots and leave the remaining future-period
        # snapshots at their default 1.0, `nyears[future_period]` ends up
        # double-counted (1.0 × non_repr + cluster_size × repr) and the
        # nyears-equality check trips with "overnight_cost cannot be used
        # when investment periods have different durations".
        #
        # Fix: for every future period covered by the aggregation, zero out
        # ALL of its snapshot_weightings first, then overlay the
        # representative weights. Result: nyears for each future period
        # equals (sum of representative weights) which by construction
        # matches the original period's total hours / 8760.
        per_iter_undo: list = []
        # Defer all future-period vintage decisions to their own iteration.
        # See `_defer_future_vintage_builds` docstring for why this is required
        # for myopic-foresight correctness. The defer is reverted in this
        # iteration's finally block so the NEXT iteration sees future
        # vintages as extendable again — each vintage is decided exactly once,
        # by the iteration whose `current_period == vintage.build_year`.
        _defer_future_vintage_builds(network, current_period, per_iter_undo, phase)
        if weight_overrides is not None and len(weight_overrides) > 0:
            sw = getattr(network, "snapshot_weightings", None)
            if sw is not None and not sw.empty:
                weight_cols = [c for c in ("objective", "generators", "stores") if c in sw.columns]
                # Future-period snapshot mask: every snapshot in sw whose
                # period level is strictly greater than current_period. The
                # `sns` slice excludes most of these but `sw` still carries
                # them, so we zero them on the network-wide weights table.
                future_periods_in_iter = sorted({s[0] for s in weight_overrides.index})
                sw_period_level = sw.index.get_level_values(0)
                future_mask = sw_period_level.isin(future_periods_in_iter)
                future_idx = sw.index[future_mask]
                target_idx = weight_overrides.index.intersection(sw.index)
                if len(target_idx) > 0 and weight_cols:
                    for col in weight_cols:
                        orig = sw.loc[future_idx, col].copy()
                        sw.loc[future_idx, col] = 0.0  # zero EVERY future-period snapshot
                        sw.loc[target_idx, col] = weight_overrides.loc[target_idx].astype(float)
                        # Single undo entry per column covers both the zero
                        # and the overlay — restoring the original future
                        # weights wipes both transforms in one step.
                        per_iter_undo.append(("col", "snapshot_weightings", col, future_idx, orig))
                    phase(
                        f"Limited foresight: rescaled {len(future_idx)} future-period "
                        f"snapshot(s) — {len(target_idx)} representatives weighted "
                        f"avg ×{weight_overrides.mean():.2f}, the rest zeroed."
                    )
        full_n = len(sns)
        future_n = full_n - sum(1 for s in sns if s[0] == current_period)
        phase(
            f"Myopic [{i}/{len(periods)}] period {current_period}: "
            f"solving {full_n} snapshot(s) "
            f"({full_n - future_n} hourly + {future_n} aggregated future)."
        )
        tl_kwarg = _compute_loss_atol(network) if cfg.transmission_losses else False
        # SCLOPF dispatch per iteration. Filter the static outage list down
        # to branches active in this period; if every candidate is in the
        # future, fall back to plain LOPF for this iteration. The outage
        # set re-grows in later iterations as those branches come online.
        iter_outages: list[tuple[str, str]] = []
        do_sclopf_this_iter = False
        if use_sclopf:
            iter_outages = _outages_active_in_period(network, current_period, all_outages)
            if not iter_outages:
                phase(
                    f"Myopic [{i}/{len(periods)}] period {current_period}: "
                    f"no active contingency candidates (all build_year > {current_period} "
                    f"or expired) — plain LOPF this iteration."
                )
            else:
                do_sclopf_this_iter = True
                phase(
                    f"Myopic [{i}/{len(periods)}] period {current_period}: "
                    f"SCLOPF with {len(iter_outages)} contingency(ies)."
                )
        try:
            if do_sclopf_this_iter:
                # `optimize_security_constrained` doesn't take a
                # `transmission_losses` kwarg (PyPSA's secant loss
                # formulation isn't wired into the BODF contingency LP).
                # Validation blocks the combination upstream — assert here
                # so a config-bypassed user gets a clean error instead of a
                # cryptic kwargs failure inside PyPSA.
                if tl_kwarg:
                    raise RuntimeError(
                        "SCLOPF + transmission_losses is not supported by "
                        "PyPSA. Disable one of the two."
                    )
                # Decide what snapshot subset the SCLOPF LP actually sees,
                # based on the iteration scope:
                #   • "horizon"        — every snapshot in `sns` (current
                #                        hourly + future-period representatives
                #                        when limited foresight is on)
                #   • "current_period" — only current-period hourly snapshots.
                #                        Future-period lookahead is dropped
                #                        from THIS iteration entirely; future
                #                        decisions land in their own myopic
                #                        iterations via the defer mechanism.
                # The non-SCLOPF (plain LOPF) path always uses the full `sns`
                # — switching scope semantics for the SCLOPF iteration only.
                if sclopf_scope == "current_period":
                    try:
                        period_level = sns.get_level_values(0)
                        sclopf_sns = sns[period_level == current_period]
                    except (AttributeError, KeyError, TypeError):
                        # Flat DatetimeIndex (single-period) or malformed sns:
                        # period filtering doesn't apply. Use the full sns.
                        sclopf_sns = sns
                    if len(sclopf_sns) == 0:
                        # Defensive: shouldn't fire on a normally-built
                        # iteration snapshot index, but if it does, fall back
                        # to the full iteration sns so the LP can't be empty.
                        sclopf_sns = sns
                    if len(sclopf_sns) < len(sns):
                        phase(
                            f"Myopic [{i}/{len(periods)}] period {current_period}: "
                            f"sclopf_scope='current_period' — dropping "
                            f"{len(sns) - len(sclopf_sns)} future-period snapshot(s) "
                            f"from this iteration's contingency LP."
                        )
                else:
                    sclopf_sns = sns
                # PyPSA's `solve_model` (which `optimize_security_constrained`
                # ends up calling internally) hardcodes
                # `extra_functionality(n, n.snapshots)` — passing the FULL
                # network snapshot index, NOT the iteration's `sns` subset.
                # The plain `n.optimize(...)` path passes `sns` instead. Our
                # wrappers (_wrap_with_curtailment_cost, _wrap_with_capex_budget,
                # _wrap_with_objective_scale) assume the iteration subset and
                # slice `p_max_pu` / coefficient arrays by it; if they get
                # `n.snapshots` they'll misalign against linopy variables that
                # only exist over `sns`. Wrap `extra_fn` to force the right
                # snapshot view on the SCLOPF path so both branches behave
                # identically.
                _iter_sns = sclopf_sns  # close over the LP's actual subset
                def _sclopf_extra_fn(net, _ignored_snapshots, _orig=extra_fn, _sns=_iter_sns):
                    if _orig is None:
                        return
                    _orig(net, _sns)
                # `optimize_security_constrained` doesn't accept
                # `assign_all_duals` directly — it forwards **kwargs to
                # linopy.Model.solve. assign_duals lives on n.model.solve
                # not on the model_kwargs path. We rely on PyPSA's default
                # dual assignment for SCLOPF results; the diagnostics below
                # that read marginal-price duals still work.
                status, condition = network.optimize.optimize_security_constrained(
                    branch_outages=tuple(iter_outages),
                    snapshots=sclopf_sns,
                    multi_investment_periods=True,
                    extra_functionality=_sclopf_extra_fn,
                    solver_name=cfg.solver_name,
                    log_fn=str(tmp_log),
                    **merged_solver_options,
                )
            else:
                status, condition = network.optimize(
                    snapshots=sns,
                    solver_name=cfg.solver_name,
                    transmission_losses=tl_kwarg,
                    multi_investment_periods=True,
                    extra_functionality=extra_fn,
                    log_fn=str(tmp_log),
                    solver_options=merged_solver_options,
                    assign_all_duals=True,
                )
            # Rescale n.objective + LP duals back to user-facing € BEFORE
            # the next myopic iteration starts (each iteration sets the
            # scale fresh via the extra_functionality wrapper).
            _rescale_results_for_objective(network)
            # Accumulate the per-period LP objective so the OUTER worker can
            # surface the FULL HORIZON total. Without this, `n.objective` at
            # the end of the myopic loop reflects only the FINAL period's
            # LP-variable, and `n._objective_constant` only the final
            # iteration's constant — leaving a ~15% gap vs the statistics-
            # based cost_breakdown.total. Verified on heat-with-time-series
            # 2026-05-26 solve: LP-side total €3.27B vs cost_breakdown €3.87B
            # (€600M = the two prior periods' contributions). Each entry is
            # (period, variable_objective, objective_constant_at_iter_end).
            # Wrap the entire accumulator in try/except — a bookkeeping
            # failure here MUST NOT abort the solve. Previous version
            # referenced `period` (undefined; loop variable is
            # `current_period`) and raised NameError mid-loop, killing the
            # whole myopic run with "name 'period' is not defined".
            try:
                try:
                    _per_obj = float(network.objective) if getattr(network, "objective", None) is not None else 0.0
                except Exception:
                    _per_obj = 0.0
                try:
                    _per_const = float(getattr(network, "objective_constant", 0.0) or 0.0)
                except Exception:
                    _per_const = 0.0
                if not hasattr(network, "_myopic_period_objectives"):
                    network._myopic_period_objectives = []
                network._myopic_period_objectives.append(
                    (int(current_period), _per_obj, _per_const)
                )
            except Exception as _acc_exc:
                phase(
                    f"Per-period objective accumulator skipped ({_acc_exc}) — "
                    "horizon total will fall back to single-LP n.objective + constant."
                )
        finally:
            # Revert THIS iteration's weight overrides immediately so the
            # next iteration starts from clean weights. Reversed order,
            # same "col" tuple shape as _apply_modelling_assumptions.
            # Each entry wrapped in its own try/except — a dtype mismatch
            # or stale-index failure on one undo MUST NOT skip the rest,
            # or vintage-defer transforms leak into the next iteration
            # (cascading miscapacity across periods). Mirrors the outer
            # myopic_undo walk in run_simulation. QA-flagged regression
            # vector that the previous bare loop didn't protect against.
            for action in reversed(per_iter_undo):
                try:
                    _, attr_name, col, idx, orig = action
                    target = getattr(network, attr_name, None)
                    if target is None:
                        continue
                    valid = [i for i in idx if i in target.index]
                    if valid:
                        target.loc[valid, col] = orig.loc[valid]
                except Exception as _undo_exc:
                    phase(
                        f"per_iter_undo entry failed ({_undo_exc}) — continuing "
                        "with the remaining entries so the iteration cleanup "
                        "completes."
                    )
        if status != "ok":
            phase(
                f"Myopic iteration for period {current_period} failed: "
                f"{status}/{condition}. Aborting before later periods get "
                f"a chance to drift further from the optimum."
            )
            return status, condition, iteration_undo

        # Post-iteration diagnostics. Together they make the LP's behaviour
        # fully visible:
        #   [CURT-POST]    — per-vintage build + curtailment (smoking gun for
        #                    merit-order distortion and VOLL over-build)
        #   [STORAGE-POST] — per-storage build + cycling (does the LP actually
        #                    build storage to absorb renewable excess?)
        #   [CAP-SUM]      — total active capacity per carrier (parent + all
        #                    vintages summed — easier to see overall expansion)
        #   [BUS-BAL]      — per-bus energy balance (detect transmission
        #                    bottlenecks limiting renewable absorption)
        # BARE (no try/except) on purpose — a diagnostic failure here propagates
        # to abort this myopic iteration (see _emit_core_post_solve_diagnostics).
        _emit_core_post_solve_diagnostics(network, sns, current_period, phase)
        # SCLOPF-specific post-solve check: for each active contingency,
        # report the worst-case post-outage line loading and whether the
        # constraint actually binds. Only fires when this iteration ran
        # through the SCLOPF path (do_sclopf_this_iter). The function is
        # defensive — wrap in try/except so a quirk in PyPSA's BODF
        # calculation can't crash the rest of the post-solve work.
        if do_sclopf_this_iter:
            try:
                _log_sclopf_post_solve(network, sns, current_period, iter_outages, phase)
            except Exception as exc:
                phase(f"[SCLOPF-POST] diagnostic skipped: {type(exc).__name__}: {exc}")
        # Cost-decomposition by period × category + LP duals on capacity
        # bounds. Helps the user reason about WHY the LP built X in period P
        # rather than period Q — surfaces both the cost components and the
        # marginal capacity value (μ on p_nom_max). Wrapped in try/except
        # since dual readout depends on assign_all_duals; we don't want a
        # diagnostic to crash the rest of the post-solve work.
        try:
            _log_cost_decomposition_post_solve(network, cfg, sns, current_period, phase)
        except Exception as exc:
            phase(f"[COST-DECOMP] diagnostic skipped: {type(exc).__name__}: {exc}")

        # Freeze this period's decisions before iterating forward.
        _freeze_period_capacities(network, current_period, iteration_undo, phase)
    # Defensive sweep: under SCLOPF + multi-period + limited-foresight,
    # PyPSA's `_set_dynamic_data` is observed to leave passive-branch _t
    # outputs (transformers_t.p0/p1 especially) with NaN entries at
    # snapshots not in the current iteration's LP slice — even though that
    # function's last step is supposed to be a `fillna(0.0)`. The result is
    # that result endpoints + scenario comparison report 0/NaN for
    # branches that DID carry flow in the LP, simply because the iteration
    # that produced the values left holes at snapshots from earlier iters.
    # We don't have a clean PyPSA-side fix, so do a final pass that walks
    # every passive-branch output and patches the holes — and LOG when
    # patching actually happens so the bug stays visible.
    _patch_passive_branch_holes(network, phase)
    phase(f"Myopic foresight complete: {len(periods)} period(s) solved.")
    return "ok", "optimal", iteration_undo


def _patch_passive_branch_holes(n, phase) -> None:
    """
    Final-pass cleanup for passive-branch result tables. Patches any
    NaN remaining in ``lines_t.p0/p1`` / ``transformers_t.p0/p1`` after the
    myopic loop ends.

    Why this exists: in SCLOPF + multi-period + limited-foresight, the
    last myopic iteration's call to PyPSA's ``assign_solution`` →
    ``_set_dynamic_data`` doesn't reliably fillna(0.0) the snapshot rows
    that weren't part of that iteration's LP slice. Earlier iterations'
    values survive (because ``_set_dynamic_data`` does
    ``loc[df.index, df.columns] = df`` rather than replacing the whole
    frame), but any snapshot that was never in any iter's slice — or any
    snapshot in the FIRST iter's slice that got reindexed away by a later
    iter — ends up NaN.

    Symptom on the user side: transformer loading shows zero / "no data"
    for periods that should have full data, even though the LP found
    feasible flows for those snapshots. The downstream view treats NaN
    correctly (skips it), but the right behaviour is for those snapshots
    to read 0 — the LP COULDN'T put flow there because earlier iters
    froze the topology; 0 is the correct LP output, not "missing".
    """
    if n is None or not isinstance(n.snapshots, pd.MultiIndex):
        return  # flat network: PyPSA's bug doesn't fire here
    sns = n.snapshots
    total_patched = 0
    for comp_attr, df_keys in (
        ("lines_t",        ("p0", "p1")),
        ("transformers_t", ("p0", "p1")),
    ):
        accessor = getattr(n, comp_attr, None)
        if accessor is None:
            continue
        for key in df_keys:
            df = getattr(accessor, key, None)
            if df is None or df.empty:
                continue
            # Align row index to n.snapshots first (defensive — handles
            # the rare case where the dataframe carries a stale subset
            # index from a previous solve).
            if not df.index.equals(sns):
                try:
                    df = df.reindex(sns)
                except Exception:
                    continue
            n_na = int(df.isna().sum().sum())
            if n_na == 0:
                continue
            # PyPSA leaves NaN at snapshots the LP didn't touch; 0 is the
            # correct LP-stage value (no flow). fillna(0.0) is what
            # ``_set_dynamic_data`` was supposed to do at solve time.
            df_filled = df.fillna(0.0)
            setattr(accessor, key, df_filled)
            total_patched += n_na
            phase(
                f"[PATCH] {comp_attr}.{key}: filled {n_na} NaN snapshot-rows "
                f"with 0 (PyPSA SCLOPF+multi-period leaves holes; "
                f"recovering for downstream views)."
            )
    if total_patched > 0:
        phase(f"[PATCH] Total passive-branch NaN cells repaired: {total_patched}")

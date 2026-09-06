"""
Stage-2 AC power-flow: `run_ac_pf_stage` and its helpers.

Carved out of `services/solver_service.py` (which keeps the LP/SCLOPF build,
myopic foresight, objective-scaling, modelling-assumptions, and the MIP/presolve
+ `_normalise_dynamic_indexes` + `_clear_dispatch_fix` helpers the LOPF path
needs). State flows ONE way at module level: this module imports `SolverConfig`,
`_normalise_dynamic_indexes`, and `_DISPATCH_FIX_ACCESSORS` back from
solver_service, plus `PyPSAService`. solver_service imports `run_ac_pf_stage`
from here LAZILY (inside `run_simulation`'s auto-chain) — so there is NO
import-time cycle; this module is never imported at solver_service's top level.
External callers (`routers.simulation`, `services.chat_tools`) import
`run_ac_pf_stage` from here.
"""
from __future__ import annotations

import pandas as pd

from services.adequacy.slack import slack_generator_mask
from services.pypsa_service import PyPSAService
from services.solver_service import (
    _DISPATCH_FIX_ACCESSORS,
    _normalise_dynamic_indexes,
    SolverConfig,
)


# ── Stage 2: post-solve AC Power Flow ────────────────────────────────────────

# Names of the result DataFrames we snapshot before and after AC PF so the
# frontend can flip between LOPF and AC PF result sets via `?source=...`.
# Keep narrow: only the fields the existing /results/* endpoints surface, plus
# voltage fields which become meaningful only after a PF run.
_RESULT_DYNAMIC_ATTRS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # q0/q1 are populated by PyPSA's n.pf() (AC PF stage); LP stage produces
    # only p0/p1. Snapshotting them here keeps the AC PF result-source endpoint
    # consistent — frontend asks for `?source=ac_pf` and gets Q values.
    # mu_upper/mu_lower are populated by the LP solve with assign_all_duals=True;
    # they're the congestion-rent duals (€/MWh per snapshot) that drive the
    # /results/line_duals endpoint.
    ("lines_t",         ("p0", "p1", "q0", "q1", "loss", "mu_upper", "mu_lower")),
    ("transformers_t",  ("p0", "p1", "q0", "q1", "loss", "mu_upper", "mu_lower")),
    # status / start_up / shut_down are MILP outputs populated only when one
    # or more generators has committable=True. Snapshotting them here lets
    # the /results/unit_commitment endpoint serve them after the dispatch-fix
    # cycle of an auto-chained AC PF run.
    ("generators_t",    ("p", "q", "status", "start_up", "shut_down")),
    ("storage_units_t", ("p", "state_of_charge")),
    ("stores_t",        ("p", "e")),
    ("loads_t",         ("p", "q")),
    ("buses_t",         ("marginal_price", "v_mag_pu", "v_ang")),
)


def _snapshot_result_state(n) -> dict:
    """
    Deep-copy every result DataFrame we serve from /results/* into a dict
    keyed by `'<components>_t.<attr>'`. Used to preserve the LP-stage result
    state before `n.pf()` overwrites the in-memory DataFrames.
    """
    import copy
    out: dict[str, pd.DataFrame] = {}
    for accessor_name, attrs in _RESULT_DYNAMIC_ATTRS:
        try:
            accessor = getattr(n, accessor_name, None)
        except Exception:
            continue
        if accessor is None:
            continue
        for attr in attrs:
            try:
                df = getattr(accessor, attr, None)
            except Exception:
                df = None
            if df is None:
                continue
            try:
                out[f"{accessor_name}.{attr}"] = copy.deepcopy(df)
            except Exception:
                # Some PyPSA accessors return non-DataFrame helpers; skip
                # anything that isn't pandas-shaped.
                pass
    return out


def _pick_ac_pf_slack_bus(n, cfg: SolverConfig) -> str:
    """
    Resolve the slack bus for the AC PF.

    Order of precedence:
      1. User-specified `cfg.ac_pf_slack_bus` (must exist as a bus).
      2. Bus already marked with `control='Slack'` (smallest index, deterministic).
      3. Auto-pick: bus with the largest aggregate p_nom across generators.
      4. Bus with the largest mean dispatch (sum of |generators_t.p|).
      5. Fallback: the first bus in n.buses.

    Returns the bus name. Side-effect: sets n.buses.at[bus, 'control'] = 'Slack'
    if not already so. PyPSA's pf() requires exactly one slack per connected
    sub-network; we set it on the resolved bus and trust the caller to pass
    a single-component network for v1 (Phase 7 validation will guard this).
    """
    bus_names = n.buses.index.astype(str).tolist()
    if not bus_names:
        raise ValueError("No buses — cannot pick slack")
    # (1) user override
    chosen = ""
    user_bus = (cfg.ac_pf_slack_bus or "").strip()
    if user_bus and user_bus in bus_names:
        chosen = user_bus
    # (2) existing Slack
    if not chosen and "control" in n.buses.columns:
        already = n.buses[n.buses["control"].astype(str).str.lower() == "slack"]
        if not already.empty:
            chosen = str(already.index[0])
    # (3) largest aggregate p_nom
    if not chosen and not n.generators.empty:
        gen_by_bus = n.generators.groupby("bus")["p_nom"].sum()
        if not gen_by_bus.empty:
            chosen = str(gen_by_bus.idxmax())
    # (4) largest mean dispatch
    if not chosen and hasattr(n.generators_t, "p") and not n.generators_t.p.empty:
        means = n.generators_t.p.abs().mean()
        if not means.empty:
            gen_bus = str(n.generators.at[str(means.idxmax()), "bus"])
            chosen = gen_bus
    # (5) fallback
    if not chosen:
        chosen = bus_names[0]
    if "control" in n.buses.columns:
        n.buses.loc[chosen, "control"] = "Slack"
    return chosen


def _strip_voll_slacks(n) -> list[str]:
    """
    Remove VOLL slack generators (added by _apply_modelling_assumptions)
    before AC PF. The restore step in run_simulation already removes them when
    the LP finishes — but if the user re-runs Stage 2 standalone after a
    different LP setup, defensively scan for the same naming convention here.
    Returns the list of removed names.

    Convention: see services/adequacy/slack.py — the single owner of the
    slack naming/carrier convention (created in step 3 of
    _apply_modelling_assumptions), including the legacy `voll_slack`
    spellings kept so a netcdf produced by an older build that crashed
    pre-restore still cleans up here. The shared mask matches on carrier
    OR name prefix — defence in depth against either side drifting.
    """
    if n.generators.empty:
        return []
    mask = slack_generator_mask(n.generators)
    removed = n.generators.index[mask].astype(str).tolist()
    if not removed:
        return []
    n.remove("Generator", removed)
    # Also clear any transient-row marks for these names so the GET
    # filter doesn't keep hiding rows that no longer exist.
    for nm in removed:
        PyPSAService.unmark_transient("Generator", nm)
    return removed



def _snapshot_dispatch_fix_state(n) -> dict:
    """
    Capture state mutated by AC PF prep so it can be restored after the
    `n.pf()` call. Two layers:

    1. `*_t.p_set` (dispatch-fix overrides) — covered by the original
       implementation. `_fix_dispatch_for_ac_pf` writes these from the
       LP solution to constrain PF input.
    2. `buses["control"]` and `generators["control"]` — added later.
       `_pick_ac_pf_slack_bus` silently mutates one bus's control to
       "Slack" when no slack was previously declared, and
       `_fix_dispatch_for_ac_pf` coerces blank/unknown generator
       controls to "PQ". Without snapshotting these the user's
       on-disk network silently gains a synthetic slack bus and
       has all blank generator controls overwritten on every AC PF —
       surprising at save time and undocumented from the user's side.

    Returns a dict suitable for `_restore_dispatch_fix_state(n, snapshot)`.
    """
    snap: dict = {}
    for accessor_name in _DISPATCH_FIX_ACCESSORS:
        accessor = getattr(n, accessor_name, None)
        if accessor is None:
            continue
        for attr in ("p_set", "p_dispatch_set", "p_store_set"):
            df = getattr(accessor, attr, None)
            if df is not None:
                snap[f"{accessor_name}.{attr}"] = df.copy()
    # Static-column captures use a distinct key prefix so restore can route
    # them through `.loc[:, col] = series` instead of `setattr`.
    try:
        if not n.buses.empty and "control" in n.buses.columns:
            snap["__col__buses.control"] = n.buses["control"].copy()
    except Exception:
        pass
    try:
        if not n.generators.empty and "control" in n.generators.columns:
            snap["__col__generators.control"] = n.generators["control"].copy()
    except Exception:
        pass
    return snap


def _restore_dispatch_fix_state(n, snap: dict) -> None:
    """
    Restore *_t.p_set + static control columns from a snapshot.
    Tolerant of missing accessors / absent columns.
    """
    for key, df in snap.items():
        if key.startswith("__col__"):
            comp_attr = key[len("__col__"):]
            comp_name, _, col = comp_attr.partition(".")
            comp_df = getattr(n, comp_name, None)
            if comp_df is None or col not in comp_df.columns:
                continue
            try:
                # Restrict to the index that existed at capture time — if a
                # row was added during AC PF (shouldn't happen, defensive)
                # the new row keeps its default value.
                shared = comp_df.index.intersection(df.index)
                if len(shared) > 0:
                    comp_df.loc[shared, col] = df.loc[shared]
            except Exception:
                pass
            continue
        accessor_name, _, attr = key.partition(".")
        accessor = getattr(n, accessor_name, None)
        if accessor is None:
            continue
        try:
            setattr(accessor, attr, df)
        except Exception:
            pass


def _fix_dispatch_for_ac_pf(n) -> None:
    """
    Copy the LP-optimal dispatch into the *_t.p_set DataFrames so the AC PF
    treats it as fixed input. Also assign default `control='PQ'` to any
    generator without one (PyPSA's PF needs a control type per generator).

    Notes:
      • Storage units / stores: p_set is the net injection (positive = into
        the bus). PyPSA's storage_units_t.p uses the same sign convention,
        so a direct copy is correct.
      • Loads: already populated from input — no change.
      • Generators with control='PV' are left alone on the P side (set p_set
        from LP) but get v_mag_pu_set from the connected bus's existing
        value (PyPSA's PF default). v1: we don't expose voltage setpoints.
    """
    # Generators: control defaults
    if not n.generators.empty and "control" in n.generators.columns:
        # Blank / NaN / unknown → PQ. Leave explicit PV / Slack alone.
        ctrl = n.generators["control"].astype(str).str.strip()
        unknown = ~ctrl.isin(["PQ", "PV", "Slack"])
        n.generators.loc[unknown, "control"] = "PQ"
    # Dispatch fix
    for accessor_name, p_col in (
        ("generators_t",    "p"),
        ("storage_units_t", "p"),
        ("stores_t",        "p"),
    ):
        try:
            accessor = getattr(n, accessor_name, None)
            if accessor is None:
                continue
            df = getattr(accessor, p_col, None)
            if df is None or df.empty:
                continue
            setattr(accessor, "p_set", df.copy())
        except Exception:
            # Best-effort — a missing accessor or unwritable attribute should
            # not block the PF call. The PF will diverge loudly if needed.
            pass


def run_ac_pf_stage(
    network,
    config: SolverConfig,
    log_queue,
) -> dict:
    """
    Run Stage 2 (AC PF) on a network that has LP-stage dispatch populated.

    Caller must hold `PyPSAService.get_lock()`. Returns a dict ready to drop
    into `_state` (in routers.simulation) with the convergence map, slack
    bus used, list of stripped VOLL slacks, and snapshots of both result
    states.

    Phase markers are pushed onto `log_queue` so the existing SSE stream
    consumer renders Stage 2 progress in the live log.
    """

    def phase(msg: str) -> None:
        log_queue.put(f"[PHASE] {msg}")

    # Two gates, both required:
    #
    #   1. `n.is_solved` covers the genuine "no LP has ever run" case for
    #      freshly-loaded networks.
    #
    #   2. `dispatch_status(network) == 'fresh'` is the load-bearing check:
    #      `n.is_solved` is unreliable post-reload — PyPSA persists the
    #      flag across netcdf round-trips (it's backed by `_objective`).
    #      A network saved post-solve and reloaded reports `is_solved=True`
    #      even when result `_t` tables are empty or carry dispatch for a
    #      topology that no longer matches (user added a bus after the
    #      solve). Running PF on stale dispatch produces garbage flows.
    #
    # The looser `n.generators_t.p.empty` check the old code used here
    # caught only the EMPTY case, not the STALE case. `dispatch_status`
    # catches both: returns 'none' when empty and 'stale' when topology
    # diverged. We accept only 'fresh'.
    from services.dispatch_status import dispatch_status
    if not getattr(network, "is_solved", False):
        raise RuntimeError("Stage 2 requires a solved network (run LOPF first).")
    status = dispatch_status(network)
    if status == "none":
        raise RuntimeError(
            "Stage 2 requires generator dispatch in n.generators_t.p — re-run LOPF."
        )
    if status == "stale":
        raise RuntimeError(
            "Stage 2 cannot run: dispatch tables in n.generators_t.p / lines_t.p0 / "
            "storage_units_t / loads_t carry results for a different topology "
            "(rows or column names diverged from the current network). PyPSA does "
            "not auto-clear `_t` tables on topology mutations — re-run LOPF to "
            "regenerate dispatch consistent with the current model."
        )

    # 0. Normalise dynamic indexes BEFORE any PF work. PyPSA's `n.pf()`
    #    iterates `_t` accessors and calls `.sel(snapshot=…)` internally;
    #    any stale MultiIndex (or missing `index.name = "snapshot"`) left
    #    over from a previous multi-period LP solve makes that fail with
    #    cryptic `dim_0` / `cannot include dtype 'M' in a buffer` errors
    #    that look like a PF divergence but are actually a stale dual
    #    `_t` frame. `run_simulation` already calls this before LOPF; the
    #    standalone `/api/simulation/run_ac_pf` path skipped it and would
    #    silently fail on any network where the user demoted from
    #    multi-period back to flat between LOPF and AC PF. Belt-and-
    #    suspenders: cheap to re-run, idempotent on a healthy network.
    fixed_idx = _normalise_dynamic_indexes(network, phase)
    if fixed_idx:
        phase(f"Stage 2: Normalised {fixed_idx} stale dynamic index/indexes pre-PF.")

    # 1. Snapshot LP results BEFORE n.pf() overwrites them.
    phase("Stage 2: Snapshotting LOPF result state...")
    lopf_snapshot = _snapshot_result_state(network)

    # 2. Strip VOLL slack generators (defensive — usually already removed
    #    by run_simulation's restore_modelling, but be robust for standalone
    #    invocations or future code paths that skip the restore.)
    stripped = _strip_voll_slacks(network)
    if stripped:
        phase(f"Stage 2: Stripped {len(stripped)} VOLL slack generator(s) "
              f"({', '.join(stripped[:5])}{'...' if len(stripped) > 5 else ''})")

    # 3. Fix dispatch from the LP solution into *_t.p_set.
    #    Snapshot the prior p_set first so we can revert in finally — otherwise
    #    these values survive autosave and PyPSA's create_model() adds
    #    Generator-p_set equality constraints on the next solve, making
    #    SCLOPF infeasible (no redispatch room for contingency LODF caps).
    phase("Stage 2: Fixing dispatch from LOPF (generators, storage units, stores)...")
    dispatch_fix_snapshot = _snapshot_dispatch_fix_state(network)
    _fix_dispatch_for_ac_pf(network)

    # 4. Resolve slack bus.
    slack_bus = _pick_ac_pf_slack_bus(network, config)
    phase(f"Stage 2: Slack bus = '{slack_bus}'")

    # 5. Run AC PF per snapshot. PyPSA's pf() returns a structure containing
    #    a 'converged' DataFrame indexed by (snapshot, sub_network). For a
    #    single sub-network we collapse to a 1-D mapping; multi-subnet
    #    networks still yield a per-snapshot bool by AND-ing across subnets.
    phase(f"Stage 2: Running AC PF on {len(network.snapshots)} snapshot(s) "
          f"(x_tol={config.ac_pf_x_tol:g})...")
    converged_map: dict[str, bool] = {}
    try:
        try:
            pf_result = network.pf(
                snapshots=network.snapshots,
                x_tol=config.ac_pf_x_tol,
                use_seed=False,
                distribute_slack=False,
            )
        except Exception as exc:
            # Per-snapshot non-convergence shouldn't raise — but bad config (e.g.
            # zero-impedance lines, missing slack) does. Surface and re-raise so
            # the caller's `[PHASE] Failed` branch picks it up.
            phase(f"Stage 2: AC PF errored before completion: {exc}")
            raise
    finally:
        # Revert *_t.p_set so autosave writes a clean network. PyPSA's PF has
        # already consumed p_set as input by this point; the result data
        # lives in *_t.p / *_t.q / buses_t.v_mag_pu etc.
        _restore_dispatch_fix_state(network, dispatch_fix_snapshot)

    # PyPSA's pf() return value: a Dict with 'converged' (DataFrame:
    # snapshots × sub_networks of bool). Older PyPSA returned a Series.
    # Be resilient to both. Keys are emitted in ISO-T format
    # (e.g. "2026-05-11T00:00:00") to match the format used by
    # `/results/lines.index` etc., so the frontend can correlate
    # convergence to result rows without timestamp normalisation.
    def _iso(ts) -> str:
        """
        ISO format for a snapshot tuple OR Timestamp.

        Multi-period: `ts` is a `(period, timestep)` tuple. Tuples have no
        `.isoformat()` so the previous `str(ts)` fallback produced garbage
        like "(2026, Timestamp('...'))" — the same bug P1 fixed for the
        /results/* TS payloads. Here we just return the timestep ISO; the
        period is carried separately in the parallel `converged_per_snapshot`
        list (see below).
        """
        if isinstance(ts, tuple) and len(ts) == 2:
            t = ts[1]
            return t.isoformat() if hasattr(t, "isoformat") else str(t)
        return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

    def _period(ts):
        """Period level for a snapshot tuple; None for flat snapshots."""
        if isinstance(ts, tuple) and len(ts) == 2:
            try:
                return int(ts[0])
            except (TypeError, ValueError):
                return str(ts[0])
        return None

    # converged_map is the legacy `{iso: bool}` dict, kept for backward
    # compatibility with consumers that don't care about multi-period (the
    # convergence-bar KPI in the status section). We ALSO emit a parallel
    # list `converged_per_snapshot_list` of {snapshot, period, ok} so the
    # multi-period UI can disambiguate same-iso entries from different
    # periods. Frontend should prefer the list and treat the dict as a
    # convenience for sub-renders.
    converged_list: list[dict] = []

    try:
        conv_df = pf_result.get("converged") if isinstance(pf_result, dict) else pf_result
        if conv_df is None:
            # No info — assume all converged (PyPSA would have raised otherwise).
            for sn in network.snapshots:
                converged_map[_iso(sn)] = True
                converged_list.append({"snapshot": _iso(sn), "period": _period(sn), "ok": True})
        elif hasattr(conv_df, "all") and hasattr(conv_df, "index"):
            # DataFrame: AND across columns (sub_networks).
            per_snap = conv_df.all(axis=1) if conv_df.ndim == 2 else conv_df
            for sn, ok in per_snap.items():
                ok_b = bool(ok)
                converged_map[_iso(sn)] = ok_b
                converged_list.append({"snapshot": _iso(sn), "period": _period(sn), "ok": ok_b})
        else:
            for sn in network.snapshots:
                converged_map[_iso(sn)] = True
                converged_list.append({"snapshot": _iso(sn), "period": _period(sn), "ok": True})
    except Exception:
        for sn in network.snapshots:
            converged_map[_iso(sn)] = True
            converged_list.append({"snapshot": _iso(sn), "period": _period(sn), "ok": True})

    n_ok = sum(1 for v in converged_map.values() if v)
    n_total = len(converged_map) or len(network.snapshots)
    phase(f"Stage 2: AC PF complete — {n_ok}/{n_total} snapshot(s) converged.")

    # 6. Snapshot AC PF result state.
    ac_pf_snapshot = _snapshot_result_state(network)

    return {
        "lopf_results": lopf_snapshot,
        "ac_pf_results": ac_pf_snapshot,
        "ac_pf_convergence": converged_map,
        "ac_pf_convergence_list": converged_list,
        "ac_pf_slack_bus_used": slack_bus,
        "ac_pf_stripped_voll_slacks": stripped,
        "ac_pf_converged_count": n_ok,
        "ac_pf_total_snapshots": n_total,
    }



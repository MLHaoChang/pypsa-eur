"""
Modelling assumptions applied transiently at solve time, plus the outage,
MIP/presolve and dynamic-index helpers the LOPF path needs before it builds.

Carved out of `services/solver_service.py`. Depends only on
`services/solver/periodized_costs.py` and `services/solver/vintage_store.py`,
never on `solver_service` itself — dependencies run one way down the package's
DAG.

The load-carrier canonicaliser travels with this module rather than staying
behind. `_canonical_load_carrier_key` had exactly one caller inside
solver_service, `_apply_modelling_assumptions`, and leaving it at the head of
the façade would have made this module import back from `solver_service` — the
cycle the DAG exists to avoid. `routers/results.py` imports it from the façade
and is unaffected.

`_frozen_vintage_store` lives in `vintage_store.py` for the mirror-image
reason: both this module and `myopic.py` read that store, so it belongs to
neither and sits below both.

`services/ac_pf_service.py` imports `_DISPATCH_FIX_ACCESSORS` and
`_normalise_dynamic_indexes` from `services.solver_service`, and deliberately
still does — repointing it at this module would be a behaviour-neutral tidy-up
that widened the diff of a refactor whose whole claim is that no call site
changed.
"""
import math
import time

import pandas as pd

from services.pypsa_service import PyPSAService
from services.solver.periodized_costs import fill_periodized_cost_defaults
from services.solver.vintage_store import _frozen_vintage_store
from services.vintage_service import apply_vintage_bounds


# ── Load carrier canonicalisation ────────────────────────────────────────────
# Mirrors `loadCarrierKey` in pypsa-gui/frontend/src/pages/results/Dispatch.tsx
# so the per-carrier load-scaler lookups on this backend match what the
# frontend's Multi-period planning UI writes. Collapses common spellings
# (empty / 'AC' / 'electricity') into a single 'electrical' bucket.

_LOAD_ELECTRICAL_ALIASES = frozenset({
    "", "ac", "electricity", "electric", "electrical", "el",
})
# Substring-based matching for heat and hydrogen — covers PyPSA-Eur naming
# conventions: "urban central heat", "rural heat", "low T heat", "district
# heat", "H2 for industry", "H2 for power", "hydrogen storage", etc.
# Electrical stays exact-match because its aliases ('', 'AC', 'el') are too
# short for safe substring fallback.
_LOAD_HEAT_TOKENS = ("heat", "thermal")
_LOAD_HYDROGEN_TOKENS = ("hydrogen", "h2")


def _canonical_load_carrier_key(raw) -> str:
    """
    Return the canonical bucket key for a PyPSA `loads.carrier` value.

    Matches the frontend's `loadCarrierKey` so a user typing 'AC' on a load
    and entering `load_scalers_by_carrier['electrical']['2027'] = 1.1` in
    the multi-period planning UI gets that load scaled, despite the names
    not matching verbatim.
    """
    if raw is None:
        return "electrical"
    try:
        c = str(raw).strip().lower()
    except Exception:
        return "unspecified"
    if c in _LOAD_ELECTRICAL_ALIASES:
        return "electrical"
    if not c:
        return "electrical"
    if any(tok in c for tok in _LOAD_HYDROGEN_TOKENS):
        return "hydrogen"
    if any(tok in c for tok in _LOAD_HEAT_TOKENS):
        return "heat"
    return c or "unspecified"
# ─────────────────────────────────────────────────────────────────────────────
# Modelling assumptions (applied transiently at solve time)
# ─────────────────────────────────────────────────────────────────────────────

def resolve_branch_outages(n, cfg: "SolverConfig") -> "list[tuple[str, str]]":
    """
    Build the (component, name) list to pass to PyPSA's
    `optimize_security_constrained(branch_outages=…)`.

    Rules — union of:
      • all lines if `sclopf_include_all_lines`
      • all transformers if `sclopf_include_all_transformers`
      • every line whose max(v_nom of its two buses) ≥
        `sclopf_voltage_threshold_kv` (skipped when threshold == 0)
      • every transformer where max(v_nom_0, v_nom_1) ≥ threshold
        (transformers expose v_nom via their connected buses, so we
        resolve it the same way as lines)
      • any names listed in `sclopf_extra_lines` / `sclopf_extra_transformers`

    Returned as a list of (component_class, asset_name) tuples — PyPSA's
    `branch_outages` accepts a list of strings (lines only) OR a
    `pd.MultiIndex` with (component, name) levels for mixed-class outages.
    The caller converts to MultiIndex when there are transformers in play.
    """
    seen: set[tuple[str, str]] = set()

    def _branch_v_nom_max(bus0: str, bus1: str) -> float:
        try:
            v0 = float(n.buses.at[bus0, "v_nom"]) if bus0 in n.buses.index else 0.0
            v1 = float(n.buses.at[bus1, "v_nom"]) if bus1 in n.buses.index else 0.0
            return max(v0, v1)
        except Exception:
            return 0.0

    thr = float(cfg.sclopf_voltage_threshold_kv or 0.0)

    # Lines
    if not n.lines.empty:
        names = set()
        if cfg.sclopf_include_all_lines:
            names.update(n.lines.index.astype(str))
        if thr > 0:
            for name in n.lines.index:
                if _branch_v_nom_max(str(n.lines.at[name, "bus0"]),
                                     str(n.lines.at[name, "bus1"])) >= thr:
                    names.add(str(name))
        for nm in cfg.sclopf_extra_lines or []:
            if str(nm) in n.lines.index:
                names.add(str(nm))
        for nm in names:
            seen.add(("Line", nm))

    # Transformers
    if not n.transformers.empty:
        names = set()
        if cfg.sclopf_include_all_transformers:
            names.update(n.transformers.index.astype(str))
        if thr > 0:
            for name in n.transformers.index:
                if _branch_v_nom_max(str(n.transformers.at[name, "bus0"]),
                                     str(n.transformers.at[name, "bus1"])) >= thr:
                    names.add(str(name))
        for nm in cfg.sclopf_extra_transformers or []:
            if str(nm) in n.transformers.index:
                names.add(str(nm))
        for nm in names:
            seen.add(("Transformer", nm))

    return sorted(seen)


def _compute_loss_atol(n) -> dict | bool:
    """
    Build the `transmission_losses` kwarg for n.optimize() with an `atol`
    auto-scaled to the network's smallest per-line full-flow loss.

    Why: PyPSA's secant loss approximation places its first breakpoint at
    `p_1 = 2·sqrt(atol / r_pu_eff)`. When atol (default 1 MW) is much larger
    than the line's full-flow loss `r_pu_eff · s_nom²`, p_1 overshoots s_nom
    by orders of magnitude, the loop exits with a single secant, and the
    resulting lower-bound slope (∝ sqrt(atol · r_pu_eff)) collides with the
    upper bound `r_pu_eff · s_nom²`. The LP then accepts only |flow| up to
    ~`r_pu_eff · s_nom² / slope = sqrt(r_pu_eff · s_nom⁴ / atol)` — visibly
    a constant tiny flow, regardless of dispatch. Surface: "I enabled losses
    and now bus1-bus2 carries a flat 3.75 MW even though Gas 1 is idle".

    Fix: pick atol = 1% of the smallest meaningful full-flow loss across all
    passive branches, capped at PyPSA's default (1 MW) so well-conditioned
    networks aren't slowed down. A floor of 1e-9 prevents pathological
    sub-microwatt values that just inflate constraint counts.

    Returns the dict PyPSA expects, or True if we couldn't measure a loss
    (no lossy branches, missing v_nom) — letting PyPSA fall back to its
    default behaviour.
    """
    losses = []
    # Lines: r_pu_eff = r / v_nom² with v_nom from bus0
    if not n.lines.empty:
        bus_vnom = n.buses["v_nom"] if "v_nom" in n.buses.columns else None
        for name in n.lines.index:
            r = n.lines.at[name, "r"]
            s_nom = n.lines.at[name, "s_nom"]
            bus0 = str(n.lines.at[name, "bus0"])
            if not (math.isfinite(r) and r > 0):
                continue
            if not (math.isfinite(s_nom) and s_nom > 0):
                continue
            v_nom = bus_vnom.get(bus0) if bus_vnom is not None else None
            if v_nom is None or not math.isfinite(v_nom) or v_nom <= 0:
                continue
            losses.append((r / v_nom**2) * s_nom**2)
    # Transformers: r_pu_eff = (r / s_nom) × tap_ratio
    if not n.transformers.empty:
        for name in n.transformers.index:
            r = n.transformers.at[name, "r"]
            s_nom = n.transformers.at[name, "s_nom"]
            tap = n.transformers.at[name, "tap_ratio"] \
                if "tap_ratio" in n.transformers.columns else 1.0
            if not (math.isfinite(r) and r > 0):
                continue
            if not (math.isfinite(s_nom) and s_nom > 0):
                continue
            if not math.isfinite(tap) or tap == 0:
                tap = 1.0
            losses.append((r * tap / s_nom) * s_nom**2)
    if not losses:
        return True
    min_loss = min(losses)
    atol = max(1e-9, min(1.0, min_loss * 0.01))
    return {"mode": "secants", "atol": atol}


_PRESOLVE_KEYS_BY_SOLVER = {
    # Each entry: (option_key, on_value, off_value). User-supplied values in
    # solver_options always win over the toggle so power users can pin specific
    # behaviour (e.g. "choose" for HiGHS auto-detection).
    "highs":   ("presolve", "on", "off"),
    "gurobi":  ("Presolve", 2, 0),
    "cplex":   ("preprocessing_presolve", "y", "n"),
    "copt":    ("Presolve", 1, 0),
    "scip":    ("presolving/maxrounds", -1, 0),
    "glpk":    ("presol", "on", "off"),
    "xpress":  ("PRESOLVE", 1, 0),
    "mosek":   ("MSK_IPAR_PRESOLVE_USE", "MSK_PRESOLVE_MODE_ON", "MSK_PRESOLVE_MODE_OFF"),
}


_MIP_KEYS_BY_SOLVER = {
    # (gap_key, time_limit_key). User values via the dispatcher are decimal
    # gap (0.01) and seconds (e.g. 3600). The dispatcher converts where the
    # solver expects different units.
    "highs":   ("mip_rel_gap",                "time_limit"),
    "gurobi":  ("MIPGap",                     "TimeLimit"),
    "cplex":   ("mip_tolerances_mipgap",      "timelimit"),
    "copt":    ("RelGap",                     "TimeLimit"),
    "scip":    ("limits/gap",                 "limits/time"),
    "glpk":    ("mipgap",                     "tmlim"),
    "xpress":  ("MIPRELSTOP",                 "MAXTIME"),
    "mosek":   ("MSK_DPAR_MIO_TOL_REL_GAP",   "MSK_DPAR_MIO_MAX_TIME"),
}


def _resolve_mip_kwargs(
    solver_name: str,
    mip_gap: float,
    mip_time_limit_s: float,
    user_options: dict | None,
) -> dict:
    """
    Map mip_gap / mip_time_limit_s to solver-specific option keys.

    Only injects keys that aren't already user-set, so power-user overrides
    in `solver_options` still win. `mip_time_limit_s <= 0` is treated as
    "no limit" — the key is not injected at all (don't set 0 directly, some
    solvers interpret that as instant timeout).
    """
    base = dict(user_options or {})
    entry = _MIP_KEYS_BY_SOLVER.get(solver_name.lower())
    if entry is None:
        return base
    gap_key, time_key = entry
    if gap_key not in base:
        base[gap_key] = float(mip_gap)
    if time_key not in base and mip_time_limit_s and mip_time_limit_s > 0:
        base[time_key] = float(mip_time_limit_s)
    return base


def _resolve_presolve_kwargs(solver_name: str, enabled: bool, user_options: dict | None) -> dict:
    """
    Map the user's presolve toggle to a solver-specific option key.

    Returns a new dict (does not mutate `user_options`). When the user already
    specified the key explicitly, that takes precedence — the toggle is just a
    convenience for the common case.
    """
    base = dict(user_options or {})
    entry = _PRESOLVE_KEYS_BY_SOLVER.get(solver_name.lower())
    if entry is None:
        return base
    key, on_val, off_val = entry
    if key not in base:
        base[key] = on_val if enabled else off_val
    return base


_DISPATCH_FIX_ACCESSORS = ("generators_t", "storage_units_t", "stores_t")


def _normalise_dynamic_indexes(n, phase=None) -> int:
    """
    Ensure every `_t` DataFrame's row index matches `n.snapshots`.

    Background: PyPSA's ``assign_duals`` (run after ``n.optimize``) writes
    flat-shaped dual DataFrames onto ``c.dynamic[attr]`` via
    ``.loc[df.index, df.columns] = df``. If a stale MultiIndex remains on
    any target frame (e.g. an empty ``mu_upper`` left over from a previous
    multi-period solve that wasn't fully reset when the user demoted back
    to flat snapshots), the assignment raises
    ``KeyError: DatetimeIndex(...) not in index`` and the whole solve is
    reported as failed even though HiGHS converged.

    This helper sweeps every component's ``_t`` store and any frame whose
    index doesn't match ``n.snapshots`` is reset:
      • empty frame → drop in a fresh empty DataFrame indexed by snapshots
      • stale MultiIndex on a flat network → trim to first-period slice
      • everything else → ``reindex(n.snapshots)`` (PyPSA handles
        DatetimeIndex → MultiIndex via level alignment for non-empty data)

    Belt-and-suspenders: it covers the case where the demotion path in
    ``set_investment_periods`` misses an attribute that was created later
    by PyPSA itself.
    """
    # Ensure n.snapshots has .name = "snapshot". When this is None, xarray
    # converts _t DataArrays with the unnamed dim labelled "dim_0", and
    # PyPSA's optimizer fails on .sel(snapshot=sns) with
    # `'snapshot' is not a valid dimension or coordinate ... ('dim_0': N, ...)`.
    # set_snapshots(MultiIndex) only sets .name when the new index has none
    # AND the previous one did — multi→multi rebuilds (POST /snapshots/multi_period)
    # therefore drop the name. Force it here so every solve starts clean.
    try:
        if getattr(n.snapshots, "name", None) != "snapshot":
            n.snapshots.name = "snapshot"
    except Exception:
        pass
    snap = n.snapshots
    fixed = 0
    try:
        all_components = list(n.all_components)
    except Exception:
        return 0
    for comp in all_components:
        try:
            dyn = n.c[comp].dynamic
        except Exception:
            continue
        for attr in list(dyn.keys()):
            df = dyn.get(attr)
            if df is None or not hasattr(df, "index"):
                continue
            # Also propagate the snapshot name onto every _t frame's row index —
            # set_snapshots only updates frames that were non-empty at the time
            # of the reshape; later-created empties otherwise carry .name=None
            # and xarray would still see dim_0 for them.
            try:
                if getattr(df.index, "name", None) != "snapshot" and not isinstance(df.index, pd.MultiIndex):
                    df.index.name = "snapshot"
                elif isinstance(df.index, pd.MultiIndex) and df.index.name != "snapshot":
                    df.index.name = "snapshot"
            except Exception:
                pass
            if df.index.equals(snap):
                continue
            # Mismatch — three cases.
            if df.empty:
                # Drop in a fresh empty frame on the right index.
                dyn[attr] = pd.DataFrame(index=snap, columns=df.columns)
                fixed += 1
                continue
            if isinstance(df.index, pd.MultiIndex) and not isinstance(snap, pd.MultiIndex):
                # Stale MultiIndex on a flat network — keep period-0's slice.
                first_p = df.index.get_level_values(0).unique()[0]
                mask = df.index.get_level_values(0) == first_p
                sub = df[mask].copy()
                sub.index = pd.DatetimeIndex(df.index[mask].get_level_values(1))
                if len(sub) == len(snap):
                    sub.index = snap
                    dyn[attr] = sub
                else:
                    try:
                        dyn[attr] = sub.reindex(snap)
                    except Exception:
                        dyn[attr] = pd.DataFrame(index=snap, columns=df.columns)
                fixed += 1
                continue
            # Otherwise rely on pandas reindex (handles flat→MultiIndex and
            # different-length flat→flat). Swallow any failure into a
            # fresh empty frame so the LP can still proceed.
            try:
                dyn[attr] = df.reindex(snap)
            except Exception:
                dyn[attr] = pd.DataFrame(index=snap, columns=df.columns)
            fixed += 1
    if fixed and phase is not None:
        phase(f"Normalised {fixed} stale dynamic index(es) before solve.")
    return fixed


def _clear_dispatch_fix(n, phase=None) -> None:
    """
    Drop any *_t.p_set columns persisted by a prior `_fix_dispatch_for_ac_pf`.

    Called at the top of every LOPF/SCLOPF/PF solve so a saved-then-reloaded
    network doesn't carry equality constraints that pin generator dispatch.
    Storage units have two side attributes (`p_dispatch_set` / `p_store_set`)
    that PyPSA also reads, so wipe them too.

    Implementation: replace the DataFrame with a 0-column DataFrame indexed by
    the current snapshots. Dropping rows alone (`iloc[0:0]`) leaves the columns
    in place and PyPSA still emits Generator-p_set constraints — confirmed
    against PyPSA 1.1.x where misaligned `p_set` rows pin dispatch even when
    the row index doesn't match `n.snapshots`.
    """
    cleared = []
    for accessor_name in _DISPATCH_FIX_ACCESSORS:
        accessor = getattr(n, accessor_name, None)
        if accessor is None:
            continue
        for attr in ("p_set", "p_dispatch_set", "p_store_set"):
            df = getattr(accessor, attr, None)
            if df is None or df.empty or len(df.columns) == 0:
                continue
            try:
                setattr(accessor, attr, pd.DataFrame(index=n.snapshots))
                cleared.append(f"{accessor_name}.{attr}")
            except Exception:
                pass
    if cleared and phase is not None:
        phase(f"Cleared stale dispatch-fix on {len(cleared)} accessor(s): {', '.join(cleared)}")


def _sanitise_transformer_types(n, phase) -> None:
    """
    Strip any `transformer.type` value that isn't a row in
    `n.transformer_types`. PyPSA crashes at solve time otherwise with
    `The type(s) X do(es) not exist in n.transformer_types`.

    The GUI's transformer presets ("380/220 kV" etc.) are UI labels — they
    don't register as real PyPSA transformer types. When the user picks one,
    the GUI also fills in explicit s_nom/x; PyPSA can use those directly when
    `type=""`. So clearing the type at solve time is safe — the explicit
    parameters take over.

    Sanitises in place. No undo: the bad value was a UI artefact and the
    sanitised state is what the user wanted PyPSA to see anyway.
    """
    if n.transformers.empty or "type" not in n.transformers.columns:
        return
    try:
        valid = set(n.transformer_types.index)
    except Exception:
        valid = set()
    types = n.transformers["type"].fillna("")
    bad_mask = (types != "") & ~types.isin(valid)
    if not bad_mask.any():
        return
    bad = sorted(set(types[bad_mask].tolist()))
    n.transformers.loc[bad_mask, "type"] = ""
    phase(
        f"Stripped unrecognised transformer type(s) {bad!r} from "
        f"{int(bad_mask.sum())} transformer(s). Falling back to explicit s_nom/x."
    )


def _apply_modelling_assumptions(n, cfg: "SolverConfig", phase):
    """
    Apply the five transient modelling knobs and return a `(restore, captured)`
    pair. Caller MUST invoke restore() in a finally block, regardless of
    solve outcome — otherwise the network keeps the LP transforms.

    `captured` is a dict the restore callbacks populate before reverting
    transforms — used to lift solve-time-only data (like VOLL slack
    dispatch) out of the soon-to-be-removed slack generators and into the
    caller's results store. Empty when no VOLL is configured or no slack
    actually got dispatched.

    Logs a one-line summary per knob via the `phase` callable so the user can
    see what was modified in the log stream.

    Implementation note: undo actions store the component-attribute *name*
    (e.g. "generators"), not the DataFrame reference. PyPSA's n.add() can
    rebuild the underlying DataFrame, invalidating any stored reference; we
    must re-resolve via getattr(n, attr) at restore time.
    """
    import numpy as np

    # Each undo entry is ("col", attr_name, col_name, idx, original_series)
    # or ("call", callable_to_run). Listed in apply-order; restore walks the
    # list in reverse.
    undo_actions: list = []
    # Populated by VOLL slack-removal callback (and any future capture step)
    # before reverting LP transforms, so the caller can salvage solve-only
    # data that wouldn't survive restoration.
    captured: dict = {}

    # Wipe the cross-iteration frozen-capacity side-store. Populated by
    # `_freeze_period_capacities` during the myopic loop and read by
    # `vintage_service._capture_and_drop_vintages` at restore. Stale
    # entries from a previous solve on THIS thread would otherwise
    # mis-attribute capacity to this solve's vintages. (Thread-local on
    # solver_service — see `_frozen_vintage_store` for the per-thread
    # isolation rationale.)
    _frozen_vintage_store().clear()

    # 1) Discount-rate / lifetime fill — delegated to the shared helper so
    #    /results/cost_breakdown can re-apply the same fill at report time
    #    (PyPSA's n.statistics() computes capital_cost via periodized_cost
    #    too, and the revert below would otherwise leave NaN behind).
    revert_periodized = fill_periodized_cost_defaults(n, cfg)
    undo_actions.append(("call", revert_periodized))

    # 2) CO2 price. Adds emissions × price / efficiency to fossil generators.
    #    We index by carrier so any generator on a non-zero-CO2 carrier gets
    #    the bump; efficiency=0 is a model error elsewhere — skip silently.
    #
    #    Two flavours:
    #      • Uniform `co2_price` (single float) — bump every emitter's scalar
    #        marginal_cost by emissions × price / efficiency.
    #      • Per-period `co2_price_per_period` (dict period→€/tCO2) — applies
    #        ONLY on multi-period networks. Sets generators_t.marginal_cost
    #        (time-varying) so each snapshot uses the period's price.
    #        Periods missing from the dict fall back to the scalar co2_price.
    has_per_period_price = (
        bool(getattr(cfg, "co2_price_per_period", None))
        and isinstance(n.snapshots, pd.MultiIndex)
    )
    if (cfg.co2_price > 0 or has_per_period_price) and not n.generators.empty and not n.carriers.empty:
        co2 = n.carriers["co2_emissions"] if "co2_emissions" in n.carriers.columns else pd.Series(dtype=float)
        co2 = co2[co2 > 0]
        if not co2.empty:
            gens = n.generators
            mask = gens["carrier"].isin(co2.index) & (gens["efficiency"] > 0)
            if mask.any():
                idx = gens.index[mask]
                if has_per_period_price:
                    # Build per-snapshot price series keyed by period.
                    raw_dict = cfg.co2_price_per_period or {}
                    period_price: dict[int, float] = {}
                    for k, v in raw_dict.items():
                        try:
                            period_price[int(k)] = float(v)
                        except (TypeError, ValueError):
                            continue
                    period_lvl = n.snapshots.get_level_values(0)
                    price_series = pd.Series(
                        [period_price.get(int(p), cfg.co2_price) for p in period_lvl],
                        index=n.snapshots, dtype=float,
                    )
                    # Build the time-varying surcharge per emitting generator:
                    # surcharge[g, t] = price_series[t] × co2[carrier(g)] / efficiency[g]
                    intensity_per_gen = gens.loc[idx, "carrier"].map(co2)
                    eff_per_gen = gens.loc[idx, "efficiency"]
                    base_mc = gens.loc[idx, "marginal_cost"].copy()
                    # outer = snapshot × generator; broadcast price (T,) × intensity (G,)
                    surcharge = pd.DataFrame(
                        {g: price_series * float(intensity_per_gen.loc[g]) / float(eff_per_gen.loc[g])
                         for g in idx},
                        index=n.snapshots,
                    )
                    # Time-varying marginal_cost = scalar base + per-snapshot surcharge.
                    # PyPSA picks up generators_t.marginal_cost when set.
                    mc_t = n.generators_t.marginal_cost
                    # Capture the pre-existing time-varying columns we're
                    # about to overwrite so restore() can put them back.
                    existing_cols = [g for g in idx if g in mc_t.columns]
                    saved_t_mc = mc_t[existing_cols].copy() if existing_cols else None
                    new_cols = [g for g in idx if g not in mc_t.columns]
                    for g in idx:
                        n.generators_t.marginal_cost[g] = float(base_mc.loc[g]) + surcharge[g]
                    undo_actions.append(("t_marginal_cost", saved_t_mc, list(new_cols)))
                    by_period_msg = ", ".join(
                        f"{p}: {period_price.get(p, cfg.co2_price):.1f}"
                        for p in sorted(set(int(x) for x in period_lvl))
                    )
                    phase(
                        f"Applied per-period CO2 price ({by_period_msg} EUR/tCO2) to "
                        f"{len(idx)} fossil generator(s) via time-varying marginal_cost."
                    )
                else:
                    # Single-period network OR no per-period dict: uniform price path.
                    add_per_mwh = gens.loc[idx, "carrier"].map(co2) * cfg.co2_price / gens.loc[idx, "efficiency"]
                    original_mc = gens.loc[idx, "marginal_cost"].copy()
                    gens.loc[idx, "marginal_cost"] = original_mc + add_per_mwh
                    undo_actions.append(("col", "generators", "marginal_cost", idx, original_mc))
                    phase(
                        f"Applied CO2 price {cfg.co2_price:.1f} EUR/tCO2 to {len(idx)} "
                        f"fossil generator(s) (sum surcharge {add_per_mwh.sum():.2f} EUR/MWh)."
                    )
    elif getattr(cfg, "co2_price_per_period", None) and not isinstance(n.snapshots, pd.MultiIndex):
        # User set per-period prices but the network is flat — log so the
        # silent no-op doesn't go unnoticed.
        phase(
            "co2_price_per_period is set but the network is single-period; "
            "ignoring the per-period dict. Enable multi-period planning to apply per-year prices."
        )

    # 3) VOLL — RELOCATED to after step 5 (load scaling) so the slack
    #    p_nom sizing sees the SCALED p_set max, not the pre-scaling
    #    value. Previously sized here, a load-growth scaler of 2× could
    #    leave slack p_nom undersized for the high-growth period, and the
    #    LP would silently shed less load than VOLL was supposed to allow.
    #    See block "5b) VOLL (post-scaling sizing)" below.

    # 4) Investment periods. Only meaningful when multi_investment_periods is
    #    also true — otherwise the flag is ignored by PyPSA. Snapshot the
    #    original periods (could be empty) and restore on exit.
    if cfg.multi_investment_periods and cfg.investment_periods:
        try:
            original_periods = list(n.investment_periods)
            # Capture the pre-promotion snapshot shape so the restore can
            # actually demote multi→flat when the user started with flat
            # snapshots + cfg-only periods. Without this, every cfg-only
            # run silently leaves the network MultiIndexed on disk.
            was_flat = not isinstance(n.snapshots, pd.MultiIndex)
            periods = sorted(int(p) for p in cfg.investment_periods)
            n.set_investment_periods(periods=periods)
            phase(f"Configured {len(periods)} investment period(s): {periods}.")
            def _restore_periods(orig=original_periods, started_flat=was_flat):
                if orig:
                    n.set_investment_periods(periods=orig)
                elif started_flat:
                    # User had flat snapshots; cfg-only set the periods.
                    # Demote back to flat so the on-disk save doesn't
                    # carry transient cfg-only periods the user never
                    # set on the network surface. Lazy import avoids
                    # a circular dependency between solver_service and
                    # routers.network.
                    try:
                        from routers.network import _flatten_snapshot_state
                        _flatten_snapshot_state(n)
                        phase(
                            "Reverted snapshots to flat — cfg.investment_periods "
                            "promoted them transiently for the solve only."
                        )
                    except Exception as exc:
                        phase(
                            f"Couldn't auto-revert snapshots to flat ({exc}). "
                            "Reload the project to fully reset, or accept the "
                            "MultiIndex shape on disk."
                        )
            undo_actions.append(("call", _restore_periods))
        except Exception as exc:
            phase(f"Investment periods setup failed: {exc}. Continuing with single-period.")

    # 4b) Auto period discount via investment_period_weightings.objective.
    #     Computes PV factors (1+r)^-(P-ref_year) per period and writes them
    #     into ipw.objective so PyPSA's LP discounts BOTH capex and opex by
    #     a uniform social rate. Without this, a 3-period model treats every
    #     period as equally weighted — so the LP front-loads all CAPEX into
    #     the first period (cheaper amortisation, identical OPEX savings).
    #     Reference year = first period in the active list. Multi-period only.
    if (
        cfg.auto_discount_periods
        and cfg.multi_investment_periods
        and isinstance(n.snapshots, pd.MultiIndex)
        and len(n.investment_periods) > 0
    ):
        try:
            periods_active = sorted(int(p) for p in n.investment_periods)
            ref_year = periods_active[0]
            r_nom = float(cfg.discount_rate or 0.0)
            infl = float(getattr(cfg, "inflation_rate", 0.0) or 0.0)
            # Real discount rate used in the cross-period PV factor. When the
            # user enters a NOMINAL discount (e.g. WACC = 7 %) and a separate
            # inflation rate (e.g. 2 %), the real discount that matters for
            # discounting REAL-€ LP costs is roughly nominal − inflation.
            # Use the exact Fisher relation so the formula is right even at
            # higher inflations: real_r = (1 + nominal) / (1 + inflation) − 1.
            # Guard against pathological inflation > nominal (real_r would
            # go negative — mathematically valid but rarely intended): clamp
            # at -0.999 so (1 + r) stays positive in the PV exponentiation.
            if 1.0 + infl > 0:
                r = (1.0 + r_nom) / (1.0 + infl) - 1.0
            else:
                r = r_nom
            if r <= -0.999:
                r = -0.999
            ipw = n.investment_period_weightings
            original_obj = ipw["objective"].copy()
            new_factors: dict[int, float] = {}
            for p in periods_active:
                # Per-period span (years_in_period). PyPSA defaults to gap-to-next
                # so multi-year periods get correctly weighted. PV applied at
                # period start; the within-period sum is approximated by
                # PV(start) × years (sufficient for r ≪ 1 and short periods —
                # exact would compute the geometric series).
                yrs = float(ipw.at[p, "years"]) if "years" in ipw.columns else 1.0
                pv = (1.0 + r) ** -(p - ref_year)
                new_factors[p] = pv * yrs
                ipw.at[p, "objective"] = new_factors[p]
            infl_note = f", inflation_rate={infl:.3f}, real={r:.3f}" if infl != 0.0 else ""
            phase(
                f"Auto-discount: ipw.objective set to PV × years for {len(periods_active)} "
                f"period(s) at discount_rate={r_nom:.3f}{infl_note}: " +
                ", ".join(f"{p}:{new_factors[p]:.3f}" for p in periods_active)
            )
            def _restore_ipw_objective(orig=original_obj):
                for p in orig.index:
                    n.investment_period_weightings.at[p, "objective"] = float(orig.at[p])
            undo_actions.append(("call", _restore_ipw_objective))
        except Exception as exc:
            phase(f"Auto-discount setup failed: {exc}. Periods stay at original weights.")

    # 5) Per-period × per-carrier load scaling. For each load column, look up
    #    its (canonical carrier, period) and multiply by the resolved factor.
    #    Resolution priority:
    #      1. cfg.load_scalers_by_carrier[carrier][period_str] — per-carrier
    #         (e.g. electrical 2027 × 1.10, hydrogen 2027 × 1.50)
    #      2. cfg.load_scalers[period_str] — legacy global fallback
    #      3. 1.0 — identity
    #    Multi-period only; ignored for flat networks. The whole p_set frame
    #    is snapshotted and restored wholesale.
    by_carrier_cfg = getattr(cfg, "load_scalers_by_carrier", {}) or {}
    if (
        cfg.multi_investment_periods
        and (cfg.load_scalers or by_carrier_cfg)
        and isinstance(n.snapshots, pd.MultiIndex)
        and not n.loads_t.p_set.empty
    ):
        p_set = n.loads_t.p_set
        period_level = p_set.index.get_level_values(0)
        # Build a per-column carrier-key map up front. We canonicalise to
        # the same alias set the frontend uses (see loadCarrierKey in
        # Dispatch.tsx) so 'AC' / 'electricity' / '' all map to 'electrical'.
        carrier_by_col: dict[str, str] = {}
        if "carrier" in n.loads.columns:
            for col in p_set.columns:
                if col in n.loads.index:
                    carrier_by_col[col] = _canonical_load_carrier_key(n.loads.at[col, "carrier"])
                else:
                    carrier_by_col[col] = "unspecified"
        else:
            for col in p_set.columns:
                carrier_by_col[col] = "unspecified"

        applied: list[str] = []
        original_p_set = None
        for period in sorted(set(period_level)):
            mask = period_level == period
            for col in p_set.columns:
                carrier_key = carrier_by_col.get(col, "unspecified")
                factor: float | None = None
                # 1) per-carrier per-period
                car_block = by_carrier_cfg.get(carrier_key)
                if isinstance(car_block, dict):
                    raw = car_block.get(str(period))
                    if raw is not None:
                        try:
                            f = float(raw)
                            if math.isfinite(f):
                                factor = f
                        except (TypeError, ValueError):
                            pass
                # 2) legacy global (applied to ALL carriers if no per-carrier override)
                if factor is None and cfg.load_scalers:
                    raw = cfg.load_scalers.get(str(period))
                    if raw is not None:
                        try:
                            f = float(raw)
                            if math.isfinite(f):
                                factor = f
                        except (TypeError, ValueError):
                            pass
                # 3) identity
                if factor is None or factor == 1.0:
                    continue
                if original_p_set is None:
                    original_p_set = p_set.copy(deep=True)
                p_set.loc[mask, col] = p_set.loc[mask, col] * factor
                applied.append(f"{period}/{carrier_key}/{col}×{factor:g}")
        if original_p_set is not None:
            def _restore_p_set(orig=original_p_set):
                n.loads_t["p_set"] = orig
            undo_actions.append(("call", _restore_p_set))
            # Summarise — listing every (period, carrier, col) explosion in the
            # log would be noisy on networks with many loads; collapse to
            # unique (period, carrier) factor combos.
            unique_combos = sorted(set(
                f"{a.split('/')[0]}/{a.split('/')[1]}×{a.split('×')[-1]}"
                for a in applied
            ))
            phase(f"Applied per-carrier load scaling: {', '.join(unique_combos)}.")

    # 5b) VOLL (post-scaling sizing). Relocated from step 3 so the slack
    #    `p_nom` sees the loads AFTER step 5's per-period × per-carrier
    #    scaling has been applied. Previously sized at step 3 against
    #    the pre-scaling p_set, a 2× load-growth scaler in period N could
    #    leave the slack undersized and the LP would silently shed less
    #    load than the configured VOLL was supposed to permit (slack hits
    #    its p_nom cap before the demand is matched, producing a
    #    primal-infeasible window that PyPSA simply reports as zero VOLL
    #    dispatch). Sized at 10× the observed max so the slack always has
    #    headroom — bumping further would slow the solver without benefit.
    if cfg.voll > 0 and not n.buses.empty:
        added = []
        # Choose a slack p_nom that comfortably exceeds the worst-case load.
        try:
            max_load = float(n.loads_t.p_set.max().max()) if not n.loads_t.p_set.empty else 0.0
        except Exception:
            max_load = 0.0
        if max_load <= 0 and not n.loads.empty:
            max_load = float(n.loads["p_set"].max()) if "p_set" in n.loads.columns else 0.0
        slack_pnom = max(max_load, 1.0) * 10.0
        # ONLY add VOLL slack at buses that actually carry a load. Transit /
        # source buses (waste-heat collection, gas trunk, hydrogen network
        # backbone) have no demand to "fail to meet" — adding a slack there
        # lets the LP create energy from nothing at VOLL cost, which on a
        # sector-coupled network the optimiser exploits: e.g. a heat-pump
        # bus2 drawing from a low-T heat collector with insufficient waste
        # heat is filled by the slack instead of curtailing high-T demand,
        # producing physically impossible balances ("creating" 35 MW of
        # waste heat at €100k/MWh to enable €3M of avoided high-T VOLL).
        # If the user genuinely wants slack on a transit bus they can add
        # a Generator manually.
        load_bus_set = set(n.loads["bus"].astype(str)) if "bus" in n.loads.columns else set()
        skipped_transit = 0
        for bus in n.buses.index:
            if str(bus) not in load_bus_set:
                skipped_transit += 1
                continue
            name = f"__voll_{bus}"
            if name in n.generators.index:
                continue  # don't double-add if a previous run leaked
            # Mark BEFORE n.add so a GET landing during the add window
            # still hides the row (filtering an absent name is a no-op).
            # On n.add failure we unmark to keep the registry consistent.
            PyPSAService.mark_transient("Generator", name)
            try:
                n.add(
                    "Generator", name,
                    bus=bus,
                    p_nom=slack_pnom,
                    marginal_cost=cfg.voll,
                    carrier="load_shedding",
                )
            except Exception:
                PyPSAService.unmark_transient("Generator", name)
                raise
            added.append(name)
        if added:
            phase(
                f"Added {len(added)} VOLL slack generator(s) at {cfg.voll:.0f} EUR/MWh "
                f"(sized to {slack_pnom:.0f} MW = 10× scaled-max load)."
                + (f" Skipped {skipped_transit} transit bus(es) without loads." if skipped_transit else "")
            )
            # Restore = remove the slacks.
            def _capture_and_remove_slacks(names=added, voll=cfg.voll):
                # Capture lost-load dispatch BEFORE removing the slack
                # generators — once they're removed, n.generators_t.p
                # forgets them and the user can never see their dispatch.
                try:
                    df = n.generators_t.p
                    if df is not None and not df.empty:
                        live = [nm for nm in names if nm in df.columns]
                        if live:
                            sub = df[live].copy()
                            # Strip the "__voll_" prefix so the bus name stands
                            # alone in the result payload — friendlier to plot.
                            sub.columns = [c.replace("__voll_", "") for c in sub.columns]
                            # Aggregate stats for the KPI tiles in the UI.
                            # Assumes hourly snapshots (MW=MWh/h) — matches the
                            # convention used by the curtailment KPI.
                            total_mwh = float(sub.values.clip(min=0).sum())
                            captured["lost_load_t"] = sub
                            captured["lost_load_total_mwh"] = total_mwh
                            captured["lost_load_cost_eur"] = total_mwh * float(voll)
                except Exception:
                    pass
                # Now remove the slack generators so they don't pollute
                # the post-solve network state. Pair every remove with an
                # unmark_transient so the registry doesn't keep filtering
                # the name once the row is gone — important if the user
                # later creates a real generator that happens to reuse
                # the bus name.
                for nm in names:
                    if nm in n.generators.index:
                        n.remove("Generator", nm)
                    PyPSAService.unmark_transient("Generator", nm)
            undo_actions.append(("call", _capture_and_remove_slacks))

    # 6) Multi-period activity guard. In a multi-period run PyPSA only lets an
    #    asset dispatch in period p when build_year <= p < build_year + lifetime.
    #    The GUI leaves build_year at its 0 default, and step 1 above may have
    #    filled lifetime to cfg.default_lifetime (e.g. 25) for overnight_cost
    #    assets — so an asset's active window can be [0, 25), which excludes
    #    investment periods like 2026 / 2027 entirely. PyPSA then can't dispatch
    #    it: the LP sheds load and "curtails" renewables that simply can't run.
    #    Rebase build_year to the first investment period for any asset that
    #    would otherwise be inactive in EVERY period, and stretch a too-short
    #    lifetime to cover the horizon. Transient — reverted in restore().
    if cfg.multi_investment_periods and isinstance(n.snapshots, pd.MultiIndex):
        try:
            mp_periods = sorted(int(p) for p in n.investment_periods)
        except (TypeError, ValueError):
            mp_periods = []
        if mp_periods:
            first_p, last_p = mp_periods[0], mp_periods[-1]
            rebased: list[str] = []
            for comp_attr in ("generators", "storage_units", "stores",
                              "links", "lines", "transformers"):
                df = getattr(n, comp_attr, None)
                if df is None or df.empty or "build_year" not in df.columns:
                    continue
                lifetimes = df["lifetime"] if "lifetime" in df.columns else None
                # COLLECT-THEN-APPLY: walk df.index once into a local snapshot
                # list, then mutate the frame in a second pass. Pandas tolerates
                # `df.at[name, col] = …` during iteration on `df.index`, but a
                # collected snapshot is more defensive (e.g. against any future
                # refactor that ends up calling n.add() / n.remove() inside the
                # loop, which would invalidate the index iterator).
                #
                # `_safe_isfinite` defends against object-dtype columns that
                # hold non-numeric tokens. Bare `np.isfinite(None)` raises
                # TypeError; with mixed-dtype columns (rare but possible after
                # CSV import of e.g. "auto") `np.isfinite("auto")` also raises.
                # Wrapping in try/except + coercing to float first reduces the
                # surface to the failure modes we care about (None / strings →
                # treated as PyPSA defaults).
                def _safe_isfinite(v) -> tuple[bool, float]:
                    """
                    Return (is_finite, float_value). Falls back to (False, 0)
                    for None / non-numeric / NaN / Inf.
                    """
                    if v is None:
                        return False, 0.0
                    try:
                        f = float(v)
                    except (TypeError, ValueError):
                        return False, 0.0
                    if not np.isfinite(f):
                        return False, 0.0
                    return True, f

                pending: list[tuple[str, float, object, float | None, object]] = []
                # tuple shape: (name, by, raw_by_for_undo, lt_for_resize_or_None, raw_lt_for_undo)
                for name in df.index:
                    raw_by = df.at[name, "build_year"]
                    finite_by, by = _safe_isfinite(raw_by)
                    if not finite_by:
                        by = 0.0
                    if lifetimes is not None:
                        raw_lt = lifetimes.at[name]
                        finite_lt, lt = _safe_isfinite(raw_lt)
                        if not finite_lt:
                            lt = float("inf")
                    else:
                        raw_lt = None
                        lt = float("inf")
                    if any(by <= p < by + lt for p in mp_periods):
                        continue  # already active in at least one period
                    # Stretch lifetime only when finite AND too short to cover
                    # the horizon from the new build_year.
                    resize_lt: float | None = None
                    if lifetimes is not None and np.isfinite(lt) and first_p + lt <= last_p:
                        resize_lt = float(last_p - first_p + 1)
                    pending.append((name, by, raw_by, resize_lt, raw_lt))

                # APPLY pass — now safe even if the loop body needed to read
                # un-rebased neighbours (none do today, but future-proof).
                for name, _by, raw_by, resize_lt, raw_lt in pending:
                    df.at[name, "build_year"] = first_p
                    undo_actions.append((
                        "col", comp_attr, "build_year",
                        pd.Index([name]), pd.Series({name: raw_by}),
                    ))
                    if resize_lt is not None:
                        df.at[name, "lifetime"] = resize_lt
                        undo_actions.append((
                            "col", comp_attr, "lifetime",
                            pd.Index([name]), pd.Series({name: raw_lt}),
                        ))
                    rebased.append(f"{comp_attr[:-1]} '{name}'")
            if rebased:
                preview = ", ".join(rebased[:4]) + ("…" if len(rebased) > 4 else "")
                phase(
                    f"Multi-period activity guard: rebased build_year -> {first_p} "
                    f"for {len(rebased)} asset(s) inactive in every investment "
                    f"period ({preview}). Set a build_year on these assets to "
                    f"control which period they're built in."
                )

    # 7) Per-period capacity bounds (vintage expansion). For each asset that
    #    the user assigned `p_nom_min` / `p_nom_max` per investment period via
    #    `n.meta["vintage_bounds"]`, replace the single extendable parent row
    #    with one vintage row per period — each carrying its own build_year
    #    and the bounds for that period. The parent's `*_extendable` flag is
    #    flipped off so the optimiser sizes ONLY the vintages. All transforms
    #    revert in restore(). No-op when not multi-period.
    if cfg.multi_investment_periods and isinstance(n.snapshots, pd.MultiIndex):
        try:
            apply_vintage_bounds(n, undo_actions, phase)
        except Exception as exc:
            phase(f"Vintage expansion failed: {exc}. Continuing without per-period bounds.")

    def restore():
        # Walk in reverse order. "col" actions write back original column
        # values; "call" actions run arbitrary cleanup (slack-gen removal,
        # period restoration, vintage drop). DataFrame is re-resolved at
        # restore time because PyPSA's n.add() may have rebuilt it during
        # apply.
        #
        # Each action is wrapped in try/except so one bad row doesn't strand
        # subsequent ones — without this, a failure in (say) the vintage
        # capture-and-drop callback would leave the parent's extendable
        # flip un-reverted and orphan rows in the network. We log to phase
        # but swallow per-entry so the rest of the restore proceeds.
        for action in reversed(undo_actions):
            kind = action[0]
            try:
                if kind == "col":
                    _, attr_name, col, idx, original = action
                    df = getattr(n, attr_name, None)
                    if df is None:
                        continue
                    # Filter index to rows that still exist — a previous undo
                    # action (e.g. slack removal) might have shrunk the frame.
                    valid = [i for i in idx if i in df.index]
                    if valid:
                        df.loc[valid, col] = original.loc[valid]
                elif kind == "call":
                    action[1]()
                elif kind == "t_marginal_cost":
                    # Restore generators_t.marginal_cost — drop columns we
                    # newly added, restore originals for columns we overwrote.
                    _, saved_t_mc, new_cols = action
                    mc_t = n.generators_t.marginal_cost
                    if new_cols:
                        keep_cols = [c for c in mc_t.columns if c not in new_cols]
                        n.generators_t.marginal_cost = mc_t[keep_cols]
                        mc_t = n.generators_t.marginal_cost
                    if saved_t_mc is not None and not saved_t_mc.empty:
                        for g in saved_t_mc.columns:
                            n.generators_t.marginal_cost[g] = saved_t_mc[g]
            except Exception as exc:
                # Surface via the phase callback so the log captures it, then
                # keep restoring. Don't re-raise.
                try:
                    phase(f"Restore: skipped one entry ({type(exc).__name__}: {exc})")
                except Exception:
                    pass
        # Safety net: drop any remaining transient-row marks. The per-row
        # unmark calls inside _capture_and_remove_slacks and
        # _capture_and_drop_vintages cover the happy path, but if a
        # restore step errored out before reaching them the registry
        # would keep filtering rows that no longer exist (or worse, real
        # rows that reuse a name later). Clearing here guarantees the
        # GET filter is a no-op on the post-restore network.
        try:
            PyPSAService.clear_transient()
        except Exception:
            pass
        # Drop the freeze-time vintage-capacity side-store after all
        # capture closures have read from it. Thread-local, so the explicit
        # clear() at end-of-restore prevents any chance of leakage into the
        # next solve cycle on this same worker thread.
        try:
            _frozen_vintage_store().clear()
        except Exception:
            pass

    return restore, captured

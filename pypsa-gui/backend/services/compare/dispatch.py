"""
Dispatch comparison: energy by carrier, served load, peak.

Moved from `routers/compare.py`. `routers.compare` re-exports every name here
under the same name — or wraps it, where the function now takes the solver
config / result lookup as keyword-only arguments instead of reading router
state — so no call site changed. See the decomposition spec, Phase 3 addendum.

math / pandas are imported locally inside functions where the router did the
same; module-level imports below are only what the bodies reference at module
scope.
"""
from __future__ import annotations

from models.schemas import (
    DispatchComparison,
)
from services.compare.support import (
    _build_snapshot_weights,
    _per_period_groupby,
    _to_pv,
    _to_pv_dict,
)
from services.results.load_frames import lp_scaled_load_frame


def _compute_dispatch_summary(n, periods, is_multi, has_solve, *, cfg=None) -> DispatchComparison:
    """
    Dispatch side: GWh per carrier + OPEX (M€) + total load (GWh).
    All time-aggregated quantities are weighted by
    ``snapshot_weightings.objective × investment_period_weightings.years``
    so multi-period networks (where one operational year is replicated under
    each period) accumulate correctly.
    """
    import math as _math

    import pandas as pd

    if not has_solve:
        # Without a solve we still return a populated container — the
        # frontend distinguishes "no solve" via the top-level has_solve
        # flag rather than missing fields.
        return DispatchComparison()

    # ENERGY quantities (dispatch GWh, served load) use the `generators`
    # weighting column — the basis PyPSA's n.statistics() and the Results-tab
    # energy KPIs use. COST quantities (OPEX) use `objective`. Previously both
    # used `objective`, so Compare dispatch GWh diverged from the Results tab
    # whenever the two columns differed (representative-week runs). Identical
    # when the columns are equal (the common single-weight case).
    weights = _build_snapshot_weights(n, "generators")
    cost_weights = _build_snapshot_weights(n, "objective")
    sns = n.snapshots

    dispatch_by_carrier: dict = {}
    opex_bucket = {"total": 0.0, "by_period": {}}
    load_bucket = {"total": 0.0, "by_period": {}}

    def _accum_carrier(carrier: str, weighted_mwh: float, weighted_pp: dict[str, float]) -> None:
        b = dispatch_by_carrier.setdefault(carrier, {"total": 0.0, "by_period": {}})
        b["total"] += weighted_mwh / 1000.0  # → GWh
        for p, v in weighted_pp.items():
            b["by_period"][p] = b["by_period"].get(p, 0.0) + v / 1000.0

    gens = n.generators
    p_t = getattr(n.generators_t, "p", None) if hasattr(n, "generators_t") else None
    if p_t is not None and not p_t.empty:
        for g in p_t.columns:
            if g not in gens.index:
                continue
            try:
                series = p_t[g].reindex(sns).fillna(0.0).astype(float)
            except Exception:
                continue
            weighted = series * weights
            total_mwh = float(weighted.sum())
            if not _math.isfinite(total_mwh):
                continue
            pp = _per_period_groupby(weighted, sns, is_multi)
            carrier = str(gens.at[g, "carrier"]) if "carrier" in gens.columns else "unknown"
            _accum_carrier(carrier.lower(), total_mwh, pp)
            # OPEX from marginal_cost × dispatch. Carrier-level scalar; PyPSA
            # also supports generators_t.marginal_cost for time-varying costs
            # but solver_service restores the user-typed value after solve,
            # so the static column is what the LP saw on average.
            try:
                mc = float(gens.at[g, "marginal_cost"])
            except (TypeError, ValueError):
                mc = 0.0
            if _math.isfinite(mc) and mc > 0:
                opex_t = series * mc * cost_weights
                opex_bucket["total"] += float(opex_t.sum()) / 1e6
                for p, v in _per_period_groupby(opex_t, sns, is_multi).items():
                    opex_bucket["by_period"][p] = opex_bucket["by_period"].get(p, 0.0) + v / 1e6

    # Storage-unit dispatch — only the discharge half goes into the
    # energy-mix; charging is internal cycling, double-counting it would
    # inflate the energy-mix chart. We use `storage_units_t.p` (signed,
    # grid-side, post-efficiency) clipped to non-negative — same convention
    # as the Results panel's `/results/storage_dispatch` endpoint, so the
    # Compare-tab number matches what the user already sees there. (PyPSA's
    # `p_dispatch` is gross internal discharge before the efficiency factor
    # — summing it overstates dispatch by 1/eta_d, ~25 % on a typical
    # 0.78-eta round-trip battery.)
    sus = n.storage_units
    p_storage = getattr(n.storage_units_t, "p", None) if hasattr(n, "storage_units_t") else None
    if p_storage is not None and not p_storage.empty:
        for s in p_storage.columns:
            if s not in sus.index:
                continue
            try:
                series = p_storage[s].reindex(sns).fillna(0.0).clip(lower=0).astype(float)
            except Exception:
                continue
            weighted = series * weights
            total_mwh = float(weighted.sum())
            if not _math.isfinite(total_mwh) or total_mwh < 1e-9:
                continue
            pp = _per_period_groupby(weighted, sns, is_multi)
            carrier = str(sus.at[s, "carrier"]) if "carrier" in sus.columns else "storage"
            _accum_carrier(carrier.lower(), total_mwh, pp)

    # Storage equivalent-cycles per carrier — fleet-level.
    # Cycle = (gross throughput) / (2 × total energy capacity).
    # Throughput sums |p| over all snapshots × snapshot_weight (NOT ×
    # ipw.years — we want cycles-per-year, not cycles-over-horizon).
    storage_cycles: dict = {}
    if not sus.empty and p_storage is not None and not p_storage.empty:
        # Per-snapshot weight WITHOUT the investment_period_weightings.years
        # multiplier — `_build_snapshot_weights` includes years for
        # energy/cost totals, but cycles is intrinsically a per-year metric
        # and we don't want it scaled by horizon length.
        try:
            sw_only = n.snapshot_weightings.loc[sns, "objective"].astype(float)
        except Exception:
            sw_only = pd.Series(1.0, index=sns, dtype=float)
        # Carrier-level accumulators: throughput (MWh) and energy_cap (MWh).
        # Both numerator and denominator get period-split so per-period
        # cycles = period_throughput / (2 × period_energy_cap).
        throughput_by_carrier: dict = {}
        energy_cap_by_carrier: dict = {}
        for s in sus.index:
            try:
                p_nom_opt = float(sus.at[s, "p_nom_opt"]) if "p_nom_opt" in sus.columns else float(sus.at[s, "p_nom"])
                max_hours = float(sus.at[s, "max_hours"]) if "max_hours" in sus.columns else 0.0
            except (TypeError, ValueError):
                continue
            if not _math.isfinite(p_nom_opt) or p_nom_opt <= 1e-9:
                continue
            if not _math.isfinite(max_hours) or max_hours <= 0:
                continue
            energy_cap = p_nom_opt * max_hours  # MWh
            carrier = str(sus.at[s, "carrier"]) if "carrier" in sus.columns else "storage"
            ec_b = energy_cap_by_carrier.setdefault(carrier.lower(), {"total": 0.0, "by_period": {}})
            ec_b["total"] += energy_cap
            if is_multi:
                # Energy cap is constant per snapshot; the per-period split
                # uses the same value (the unit is active in every period
                # it appears in). Multiplying-by-time isn't appropriate —
                # the denominator in cycles is MWh of capacity, not MWh-yr.
                for p in periods:
                    ec_b["by_period"][str(p)] = ec_b["by_period"].get(str(p), 0.0) + energy_cap
            # Throughput: Σ |p| × snapshot_weight.
            if s not in p_storage.columns:
                continue
            try:
                series = p_storage[s].reindex(sns).fillna(0.0).abs().astype(float)
            except Exception:
                continue
            throughput_t = series * sw_only  # MWh per snapshot
            tp_b = throughput_by_carrier.setdefault(carrier.lower(), {"total": 0.0, "by_period": {}})
            tp_b["total"] += float(throughput_t.sum())
            for p, v in _per_period_groupby(throughput_t, sns, is_multi).items():
                tp_b["by_period"][p] = tp_b["by_period"].get(p, 0.0) + v
        # Cycles = throughput / (2 × energy_cap). Guard zero-cap.
        # Per-period: t_period / (2 × ec_period) for each period.
        # Horizon "total": MEAN across periods (not SUM) so a 3-year horizon
        # reports the per-year cycling rate, not 3× it. Previously this
        # computed `tp["total"] / (2 × ec["total"])` where tp["total"] was
        # summed across the horizon but ec["total"] was a single period's
        # energy cap → result was 3× the per-year value and DISAGREED with
        # the parallel `storage_cycling.cycles_by_carrier` view (which uses
        # mean) by exactly factor=n_periods. Compare View surfaces both →
        # contradictory numbers. Align here with mean-per-year so the two
        # views are consistent.
        for carrier, tp in throughput_by_carrier.items():
            ec = energy_cap_by_carrier.get(carrier) or {"total": 0.0, "by_period": {}}
            pp: dict[str, float] = {}
            for period_str, t_period in tp["by_period"].items():
                ec_period = ec["by_period"].get(period_str, 0.0)
                pp[period_str] = t_period / (2 * ec_period) if ec_period > 1e-9 else 0.0
            if pp:
                # Multi-period: mean cycles per year across the horizon.
                total_cycles = sum(pp.values()) / len(pp)
            elif ec["total"] > 1e-9:
                # Flat (single-period) fallback: tp/2*ec gives cycles directly.
                total_cycles = tp["total"] / (2 * ec["total"])
            else:
                total_cycles = 0.0
            storage_cycles[carrier] = {"total": total_cycles, "by_period": pp}

    # Load aggregation — magnitude only (load convention is positive
    # consumption on `p_set`; serving means dispatch matches load).
    loads = n.loads
    # Scaled demand (loads_t.p when present, else load_scalers×p_set) via the
    # shared helper, so this matches /results/loads and the compare-state
    # total_energy. `from_state=False` reads n's OWN frame (n is the loaded
    # bundle here), never the live network's cached snapshot.
    p_set_t = None
    try:
        p_set_t = lp_scaled_load_frame(n, cfg, from_state=False)
    except Exception:
        p_set_t = None
    if p_set_t is None:
        p_set_t = getattr(n.loads_t, "p_set", None) if hasattr(n, "loads_t") else None
    if p_set_t is not None and not p_set_t.empty:
        try:
            row_total = p_set_t.abs().sum(axis=1).reindex(sns).fillna(0.0).astype(float)
        except Exception:
            row_total = None
        if row_total is not None:
            weighted = row_total * weights
            load_bucket["total"] = float(weighted.sum()) / 1000.0
            for p, v in _per_period_groupby(weighted, sns, is_multi).items():
                load_bucket["by_period"][p] = v / 1000.0
    elif not loads.empty and "p_set" in loads.columns:
        # Static-only loads — apply a flat profile to keep the KPI honest.
        try:
            static_total = float(loads["p_set"].abs().sum())
        except Exception:
            static_total = 0.0
        if static_total > 0:
            # Treat as constant over the whole horizon; weights collapse
            # to nsnapshots × period_years.
            load_bucket["total"] = static_total * float(weights.sum()) / 1000.0

    return DispatchComparison(
        dispatch_gwh_by_carrier=_to_pv_dict(dispatch_by_carrier),
        opex_meur=_to_pv(opex_bucket),
        total_load_gwh=_to_pv(load_bucket),
        storage_cycles_by_carrier=_to_pv_dict(storage_cycles),
    )

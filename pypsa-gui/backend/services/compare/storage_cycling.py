"""
Storage cycling comparison: equivalent full cycles per unit.

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
    StorageCyclingComparison,
)
from services.compare.support import (
    _per_period_groupby,
    _to_pv,
    _to_pv_dict,
)


def _compute_storage_cycling_summary(n, periods, is_multi, has_solve) -> StorageCyclingComparison:
    """
    Per-storage-unit cycling with carrier rollup, vintage-aware per period.

    Cycle definition: ``throughput / (2 × energy_capacity)`` where throughput
    is ``Σ |p| × snapshot_weight`` (sign-agnostic — charge and discharge
    count equally). For multi-period vintage-expanded networks the
    ``energy_capacity`` denominator MUST be the cumulative-by-period
    capacity (``initial + Σ vintages with build_year ≤ P``), NOT the parent's
    final ``p_nom_opt × max_hours``. Using the final cumulative undercounts
    early-period cycles (denominator too big when the unit hadn't yet
    expanded) — e.g. a 50→500 MWh battery doing 200 MWh of throughput in
    its first year shows 0.2 cycles instead of the true 2.

    "All" (horizon-wide) is the AVERAGE of per-period cycles for multi-
    period networks — not throughput/total-cap — so a 3-year run with 100
    cycles/year reads as 100, not 300. For flat networks the per-period
    dict is empty and the total is computed straight from the horizon
    throughput and energy capacity.

    Snapshot weight is the OBJECTIVE weight only — NOT multiplied by
    ``investment_period_weightings.years`` — because cycles is intrinsically
    a per-year metric.
    """
    import math as _math

    import pandas as pd
    from models.schemas import StorageCyclingComparison, StorageUnitCycles

    if not has_solve:
        return StorageCyclingComparison()
    sus = n.storage_units
    if sus.empty:
        return StorageCyclingComparison()
    p_storage = getattr(n.storage_units_t, "p", None) if hasattr(n, "storage_units_t") else None
    if p_storage is None or p_storage.empty:
        return StorageCyclingComparison()

    sns = n.snapshots
    try:
        sw_only = n.snapshot_weightings.loc[sns, "objective"].astype(float)
    except Exception:
        sw_only = pd.Series(1.0, index=sns, dtype=float)

    vintage_root = (n.meta or {}).get("vintage_results", {}) if hasattr(n, "meta") else {}
    su_vr = vintage_root.get("StorageUnit", {}) if isinstance(vintage_root, dict) else {}

    def _per_period_pnom(s_name: str, parent_opt: float) -> dict[int, float]:
        """
        Cumulative ``p_nom`` active in each period for storage unit
        ``s_name``: ``initial + Σ vintages with build_year ≤ P``. Non-vintage
        / single-period assets return a flat ``{P: parent_opt}`` for each
        period.
        """
        if not is_multi or not periods:
            return {}
        meta_entry = su_vr.get(s_name) if isinstance(su_vr, dict) else None
        if not isinstance(meta_entry, dict):
            return {int(p): parent_opt for p in periods}
        try:
            initial = float(meta_entry.get("initial_capacity", 0.0) or 0.0)
        except (TypeError, ValueError):
            initial = 0.0
        out: dict[int, float] = {}
        for p in periods:
            cum = initial
            for entry in (meta_entry.get("periods") or []):
                if not isinstance(entry, dict):
                    continue
                try:
                    by = int(entry.get("build_year"))
                    pn = float(entry.get("p_nom_opt", 0.0) or 0.0)
                except (TypeError, ValueError):
                    continue
                if pn <= 1e-9:
                    continue
                if by <= int(p):
                    cum += pn
            out[int(p)] = cum
        return out

    by_unit: list[StorageUnitCycles] = []
    # Carrier rollup buckets: per-period throughput and per-period energy_cap
    # accumulated across units in the same carrier. cycles_carrier[P] is
    # derived from these as Σ_units throughput[P] / (2 × Σ_units energy_cap[P]).
    tp_by_carrier_pp: dict[str, dict[str, float]] = {}
    ec_by_carrier_pp: dict[str, dict[str, float]] = {}
    tp_by_carrier_total: dict[str, float] = {}     # horizon throughput
    ec_by_carrier_final: dict[str, float] = {}     # final energy_cap (for flat-net fallback)

    for s in sus.index:
        try:
            parent_pnom_opt = float(sus.at[s, "p_nom_opt"]) if "p_nom_opt" in sus.columns else float(sus.at[s, "p_nom"])
            max_hours = float(sus.at[s, "max_hours"]) if "max_hours" in sus.columns else 0.0
            carrier = (str(sus.at[s, "carrier"]) if "carrier" in sus.columns else "storage").lower()
        except (TypeError, ValueError):
            continue
        if not _math.isfinite(parent_pnom_opt) or parent_pnom_opt <= 1e-9:
            continue
        if not _math.isfinite(max_hours) or max_hours <= 0:
            continue
        if s not in p_storage.columns:
            continue
        try:
            series = p_storage[s].reindex(sns).fillna(0.0).abs().astype(float)
        except Exception:
            continue

        throughput_t = series * sw_only
        tp_total = float(throughput_t.sum())
        if not _math.isfinite(tp_total):
            continue
        tp_pp = _per_period_groupby(throughput_t, sns, is_multi)

        # Per-period effective energy_cap for THIS unit.
        per_period_pnom = _per_period_pnom(s, parent_pnom_opt)
        per_period_ec: dict[int, float] = {p: pn * max_hours for p, pn in per_period_pnom.items()}
        final_ec = parent_pnom_opt * max_hours  # for flat-net or all-horizon fallback

        # Per-period cycles using period-specific energy capacity.
        cycles_pp: dict[str, float] = {}
        for p_str, tp in tp_pp.items():
            try:
                ec_p = per_period_ec.get(int(p_str), final_ec)
            except (TypeError, ValueError):
                ec_p = final_ec
            cycles_pp[p_str] = (tp / (2 * ec_p)) if ec_p > 1e-9 else 0.0

        # Horizon-wide "total" = AVERAGE of per-period cycles for multi-period
        # (so a 3-year horizon doesn't read 3× the per-period count); for flat
        # networks fall back to throughput / (2 × final_ec).
        if is_multi and cycles_pp:
            cycles_total = sum(cycles_pp.values()) / len(cycles_pp)
        else:
            cycles_total = (tp_total / (2 * final_ec)) if final_ec > 1e-9 else 0.0

        by_unit.append(StorageUnitCycles(
            name=str(s),
            carrier=carrier,
            p_nom_mw=parent_pnom_opt,
            energy_mwh=final_ec,
            throughput_mwh=_to_pv({"total": tp_total, "by_period": tp_pp}),
            cycles=_to_pv({"total": cycles_total, "by_period": cycles_pp}),
        ))

        # Carrier rollup accumulators (per-period values, not unit-final).
        tp_b = tp_by_carrier_pp.setdefault(carrier, {})
        for p_str, tp in tp_pp.items():
            tp_b[p_str] = tp_b.get(p_str, 0.0) + tp
        ec_b = ec_by_carrier_pp.setdefault(carrier, {})
        for p_int, ec in per_period_ec.items():
            key = str(p_int)
            ec_b[key] = ec_b.get(key, 0.0) + ec
        tp_by_carrier_total[carrier] = tp_by_carrier_total.get(carrier, 0.0) + tp_total
        ec_by_carrier_final[carrier] = ec_by_carrier_final.get(carrier, 0.0) + final_ec

    # Carrier-level cycles: per-period using per-period denominator; total
    # is the AVERAGE of per-period values (multi-period) or throughput /
    # (2 × final_ec) for flat networks.
    cycles_by_carrier: dict = {}
    for c, tp_pp in tp_by_carrier_pp.items():
        ec_pp = ec_by_carrier_pp.get(c, {})
        pp: dict[str, float] = {}
        for p_str, tp in tp_pp.items():
            ec_p = ec_pp.get(p_str, 0.0)
            pp[p_str] = (tp / (2 * ec_p)) if ec_p > 1e-9 else 0.0
        if is_multi and pp:
            total = sum(pp.values()) / len(pp)
        else:
            final_ec_c = ec_by_carrier_final.get(c, 0.0)
            tp_total_c = tp_by_carrier_total.get(c, 0.0)
            total = (tp_total_c / (2 * final_ec_c)) if final_ec_c > 1e-9 else 0.0
        cycles_by_carrier[c] = {"total": total, "by_period": pp}

    # Sort detail rows by horizon-wide cycles desc so the most-active units
    # surface first.
    by_unit.sort(key=lambda u: -(u.cycles.total or 0))

    return StorageCyclingComparison(
        cycles_by_carrier=_to_pv_dict(cycles_by_carrier),
        by_unit=by_unit,
    )

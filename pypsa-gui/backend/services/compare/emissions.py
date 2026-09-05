"""
Emissions comparison: tCO2 by carrier and period.

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
    CarrierPeriodValue,
    EmissionsComparison,
)
from services.compare.support import (
    _build_snapshot_weights,
    _co2_intensity_map,
    _per_period_groupby,
    _to_pv,
    _to_pv_dict,
)


def _compute_emissions_summary(n, periods, is_multi, has_solve) -> EmissionsComparison:
    """
    Sum CO2 emissions per (carrier, period) using the same kg/MWh model
    the LP optimised against: ``dispatch × carrier.co2_emissions /
    efficiency × snapshot_weight × period_year``. Output in kilotons.

    Intensity = total kt × 1000 / total dispatch GWh × 1000 = kg/MWh.
    """
    import math as _math
    if not has_solve:
        return EmissionsComparison()
    # ENERGY basis (generators): emissions = dispatch × factor, and the dispatch
    # denominator must match _compute_dispatch_summary (also generators) so the
    # two Compare views — and the Results /emissions endpoint — reconcile. The
    # whole function is energy; there is no cost term here.
    weights = _build_snapshot_weights(n, "generators")
    sns = n.snapshots
    co2_map = _co2_intensity_map(n)
    if not co2_map:
        return EmissionsComparison()

    total_bucket = {"total": 0.0, "by_period": {}}
    by_carrier_kt: dict = {}
    # Total dispatch is needed for the intensity denominator — match what
    # _compute_dispatch_summary reports so the two views stay reconciled.
    total_dispatch_mwh = {"total": 0.0, "by_period": {}}

    gens = n.generators
    p_t = getattr(n.generators_t, "p", None) if hasattr(n, "generators_t") else None
    if p_t is None or p_t.empty:
        return EmissionsComparison()

    def _accum(bucket, mwh_total, mwh_pp):
        bucket["total"] += mwh_total
        for p, v in mwh_pp.items():
            bucket["by_period"][p] = bucket["by_period"].get(p, 0.0) + v

    for g in p_t.columns:
        if g not in gens.index:
            continue
        try:
            series = p_t[g].reindex(sns).fillna(0.0).astype(float)
        except Exception:
            continue
        weighted = series * weights
        mwh_total = float(weighted.sum())
        if not _math.isfinite(mwh_total):
            continue
        mwh_pp = _per_period_groupby(weighted, sns, is_multi)
        _accum(total_dispatch_mwh, mwh_total, mwh_pp)

        carrier = str(gens.at[g, "carrier"]).lower() if "carrier" in gens.columns else ""
        intensity = co2_map.get(carrier, 0.0)
        if intensity <= 0:
            continue
        eff = float(gens.at[g, "efficiency"]) if "efficiency" in gens.columns else 1.0
        if not _math.isfinite(eff) or eff <= 0:
            eff = 1.0
        out_intensity = intensity / eff  # tCO2 per MWh_elec
        # CO2 mass in tons → /1000 for kt.
        co2_total_kt = mwh_total * out_intensity / 1000.0
        if co2_total_kt < 1e-9:
            continue
        co2_pp_kt = {p: v * out_intensity / 1000.0 for p, v in mwh_pp.items()}

        total_bucket["total"] += co2_total_kt
        for p, v in co2_pp_kt.items():
            total_bucket["by_period"][p] = total_bucket["by_period"].get(p, 0.0) + v
        b = by_carrier_kt.setdefault(carrier, {"total": 0.0, "by_period": {}})
        b["total"] += co2_total_kt
        for p, v in co2_pp_kt.items():
            b["by_period"][p] = b["by_period"].get(p, 0.0) + v

    # Intensity = total kt × 1e6 (kt→kg) ÷ total MWh
    def _intensity(mwh, kt):
        if mwh <= 1e-9 or kt < 0:
            return 0.0
        return kt * 1e6 / mwh

    intensity_total = _intensity(total_dispatch_mwh["total"], total_bucket["total"])
    intensity_pp: dict[str, float] = {}
    for p, mwh in total_dispatch_mwh["by_period"].items():
        intensity_pp[p] = _intensity(mwh, total_bucket["by_period"].get(p, 0.0))

    return EmissionsComparison(
        total_kt=_to_pv(total_bucket),
        by_carrier_kt=_to_pv_dict(by_carrier_kt),
        intensity_kg_per_mwh=CarrierPeriodValue(total=intensity_total, by_period=intensity_pp),
    )

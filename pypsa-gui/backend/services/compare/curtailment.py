"""
Curtailment comparison: curtailed renewable energy per carrier and period.

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
    CurtailmentComparison,
)
from services.compare.support import (
    _build_snapshot_weights,
    _per_period_groupby,
    _to_pv,
    _to_pv_dict,
)


def _compute_curtailment_summary(n, periods, is_multi, has_solve) -> CurtailmentComparison:
    """
    Per-carrier renewable curtailment in GWh + percent rate.

    Mirrors the logic in ``/results/curtailment``: per-snapshot effective
    capacity = ``initial + Σ vintages with build_year ≤ snapshot's period``,
    NOT the parent's final ``p_nom_opt``. Without this vintage mask, a
    2028-vintage build that's rolled into the parent's post-solve
    ``p_nom_opt`` inflates the 2026 max-available envelope and yields
    phantom curtailment — typically 5–10× too high. PyPSA's own
    ``n.statistics.curtailment()`` has the same overcounting issue on
    multi-period vintage-expanded networks; that's why the live Dispatch
    tab uses ``/results/curtailment`` (vintage-aware) instead.

    Only generators with a time-varying ``p_max_pu`` (renewables with a
    profile) contribute. Aggregation is weighted by
    ``_build_snapshot_weights`` so multi-period representative-week
    networks accumulate to the right horizon energy figures.
    """
    import math as _math

    import pandas as pd
    from models.schemas import CurtailmentComparison

    if not has_solve:
        return CurtailmentComparison()

    # ENERGY basis (generators): curtailment and available energy are MWh, so
    # they must use the same basis as the Compare Dispatch tab
    # (_compute_dispatch_summary, also generators) — otherwise curtailment GWh
    # disagrees with dispatch GWh under representative-week weighting. Whole
    # function is energy; no cost term.
    weights = _build_snapshot_weights(n, "generators")
    sns = n.snapshots

    curt_by_carrier: dict = {}
    avail_by_carrier: dict = {}

    gens = n.generators
    if gens.empty:
        return CurtailmentComparison()
    p_t = getattr(n.generators_t, "p", None) if hasattr(n, "generators_t") else None
    p_max_pu_t = getattr(n.generators_t, "p_max_pu", None) if hasattr(n, "generators_t") else None
    if p_t is None or p_t.empty or p_max_pu_t is None or p_max_pu_t.empty:
        return CurtailmentComparison()

    # ── Vintage-aware effective capacity per (snapshot, generator) ───────
    # For multi-period networks with vintage_results in n.meta, override the
    # parent's flat p_nom_opt with a time-varying effective capacity that
    # respects build_year. For non-vintage assets (no entry in meta) we fall
    # back to parent's static p_nom_opt — same as /results/curtailment.
    vintage_root = (n.meta or {}).get("vintage_results", {}) if hasattr(n, "meta") else {}
    gen_vr = vintage_root.get("Generator", {}) if isinstance(vintage_root, dict) else {}

    period_lvl = None
    if is_multi:
        try:
            period_lvl = sns.get_level_values(0).astype(int)
        except Exception:
            period_lvl = None

    def _effective_capacity_series(g_name: str) -> pd.Series:
        """
        Per-snapshot effective capacity for generator ``g_name``. Walks
        vintage_results entries and accumulates ``initial + Σ vintages
        with build_year ≤ snapshot's period``. Falls back to a flat series
        at parent's p_nom_opt when no vintage data exists (single-period
        networks or non-vintage-expanded assets).
        """
        try:
            parent_opt = float(gens.at[g_name, "p_nom_opt"]) if "p_nom_opt" in gens.columns else float(gens.at[g_name, "p_nom"])
        except (TypeError, ValueError, KeyError):
            parent_opt = 0.0
        if not _math.isfinite(parent_opt):
            parent_opt = 0.0
        meta_entry = gen_vr.get(g_name) if isinstance(gen_vr, dict) else None
        if not meta_entry or period_lvl is None:
            return pd.Series(parent_opt, index=sns, dtype=float)
        try:
            initial = float(meta_entry.get("initial_capacity", 0.0) or 0.0)
        except (TypeError, ValueError):
            initial = 0.0
        eff = pd.Series(initial, index=sns, dtype=float)
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
            mask = period_lvl >= by
            eff.values[mask] += pn
        return eff

    for g in p_t.columns:
        if g not in gens.index:
            continue
        # Only consider generators with a per-snapshot p_max_pu profile.
        # Thermal/conventional plants leave this column empty (static 1.0
        # applies); summing their (p_nom − p) would inflate the result with
        # economic dispatch headroom, not curtailment.
        if g not in p_max_pu_t.columns:
            continue
        eff_cap = _effective_capacity_series(g)
        if not _math.isfinite(float(eff_cap.max())) or float(eff_cap.max()) <= 1e-9:
            continue
        try:
            disp = p_t[g].reindex(sns).fillna(0.0).astype(float)
            pmu = p_max_pu_t[g].reindex(sns).fillna(0.0).astype(float)
        except Exception:
            continue
        available = pmu * eff_cap  # Hadamard product per snapshot
        # Clip negatives — solver tolerance can leave |p| > p_max_pu ×
        # effective_cap by ε; we don't want to count that as negative
        # curtailment.
        curtail = (available - disp).clip(lower=0)
        weighted_curt = curtail * weights
        weighted_avail = available * weights
        total_c = float(weighted_curt.sum())
        total_a = float(weighted_avail.sum())
        if not _math.isfinite(total_c) or not _math.isfinite(total_a):
            continue
        if total_a <= 1e-9:
            continue
        carrier = (str(gens.at[g, "carrier"]) if "carrier" in gens.columns else "unknown").lower()
        cb = curt_by_carrier.setdefault(carrier, {"total": 0.0, "by_period": {}})
        cb["total"] += total_c / 1000.0  # GWh
        for p, v in _per_period_groupby(weighted_curt, sns, is_multi).items():
            cb["by_period"][p] = cb["by_period"].get(p, 0.0) + v / 1000.0
        ab = avail_by_carrier.setdefault(carrier, {"total": 0.0, "by_period": {}})
        ab["total"] += total_a / 1000.0
        for p, v in _per_period_groupby(weighted_avail, sns, is_multi).items():
            ab["by_period"][p] = ab["by_period"].get(p, 0.0) + v / 1000.0

    if not curt_by_carrier:
        return CurtailmentComparison()

    # System totals.
    total_bucket = {"total": 0.0, "by_period": {}}
    total_avail = {"total": 0.0, "by_period": {}}
    for c, b in curt_by_carrier.items():
        total_bucket["total"] += b["total"]
        for p, v in b["by_period"].items():
            total_bucket["by_period"][p] = total_bucket["by_period"].get(p, 0.0) + v
    for c, b in avail_by_carrier.items():
        total_avail["total"] += b["total"]
        for p, v in b["by_period"].items():
            total_avail["by_period"][p] = total_avail["by_period"].get(p, 0.0) + v

    # Per-carrier rate.
    rate_by_carrier: dict = {}
    for c, b in curt_by_carrier.items():
        ab = avail_by_carrier.get(c) or {"total": 0.0, "by_period": {}}
        rate_t = 100.0 * b["total"] / ab["total"] if ab["total"] > 1e-9 else 0.0
        rate_pp: dict = {}
        for p, v in b["by_period"].items():
            ap = ab["by_period"].get(p, 0.0)
            rate_pp[p] = 100.0 * v / ap if ap > 1e-9 else 0.0
        rate_by_carrier[c] = {"total": rate_t, "by_period": rate_pp}

    # System rate.
    sys_rate_t = 100.0 * total_bucket["total"] / total_avail["total"] if total_avail["total"] > 1e-9 else 0.0
    sys_rate_pp: dict = {}
    for p, v in total_bucket["by_period"].items():
        ap = total_avail["by_period"].get(p, 0.0)
        sys_rate_pp[p] = 100.0 * v / ap if ap > 1e-9 else 0.0

    return CurtailmentComparison(
        total_gwh=_to_pv(total_bucket),
        by_carrier_gwh=_to_pv_dict(curt_by_carrier),
        rate_pct_by_carrier=_to_pv_dict(rate_by_carrier),
        system_rate_pct=_to_pv({"total": sys_rate_t, "by_period": sys_rate_pp}),
    )

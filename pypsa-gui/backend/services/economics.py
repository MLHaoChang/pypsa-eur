"""
Shared economics / emissions primitives used by BOTH the single-network
Results endpoints (``routers/results.py``) and the cross-scenario Compare
view (``routers/compare.py``).

Why this module exists: those two files answer different questions — Results
details one network, Compare aggregates several — but they must agree on the
*inputs* to those answers. Where they each carried their own copy of a lookup,
the two views could silently disagree on the same network, and a fix applied
to one would not reach the other.

LEAF module: imports only stdlib + pandas. Must never import ``routers.*`` or
other ``services.*`` (beyond equally-leaf siblings) so anything can depend on
it without a cycle. Same contract as ``services/serialization.py`` and
``services/period_utils.py``.

Deliberately NOT here:
  * The annuity / capital-recovery factor. That already has a single home in
    ``services.solver_service._annuity`` and is imported from there — the LP
    and the reporting layer must not diverge on it.
  * The per-snapshot weighting basis (``snapshot_weightings × investment_
    period_weightings.years``) — that lives in ``services.period_utils.
    snapshot_weights``.
  * ``lp_scaled_load_frame`` / ``corrected_marginal_prices``. Both are genuinely
    shared (compare.py imports them from results.py) but they depend on
    ``routers.results._result_df``, which reads the ``routers.simulation._state``
    module global. Moving them here means untangling that global first; until
    then compare.py imports them from results.py directly, which is safe —
    verified that neither import order cycles.
"""
from __future__ import annotations

import math

from services import period_utils


def co2_intensity_map(n) -> dict[str, float]:
    """
    ``carrier_name_lower -> co2_emissions`` (tCO2 per MWh of PRIMARY energy)
    for every carrier whose value is finite.

    This is the intensity PyPSA's primary-energy global constraint reads, so
    emissions must be computed as
    ``dispatch × weight × co2_intensity / efficiency`` to match what the LP
    actually enforced.

    Keys are lower-cased because component ``carrier`` values are looked up
    case-insensitively downstream. Returns an EMPTY dict when the network has
    no carriers frame or no ``co2_emissions`` column — callers treat a missing
    carrier as zero-emitting.

    Values are coerced with ``float(v)`` rather than gated on
    ``isinstance(v, (int, float))``. routers/results.py previously used the
    isinstance form, which silently DROPPED any carrier whose co2_emissions
    arrived as a numeric string (e.g. from a CSV-imported carriers table)
    while routers/compare.py's coercing form kept it — so the Results tab and
    the Compare rail could report different emissions for the same network.
    Coercion is the superset and makes the two agree.
    """
    out: dict[str, float] = {}
    carriers = getattr(n, "carriers", None)
    if carriers is None or carriers.empty or "co2_emissions" not in carriers.columns:
        return out
    try:
        for k, v in carriers["co2_emissions"].items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(fv):
                continue
            out[str(k).lower()] = fv
    except Exception:
        return out
    return out


def annuitised_capex_by_carrier(
    generators, storage_units, stores, links,
    *,
    periods, is_multi, years_map, capital_cost_of,
) -> dict:
    """
    Walk every cost-bearing component, accumulate ``p_nom_opt × cc_per_MW``
    per carrier as M€/yr, then expand into per-period values via
    ``ipw.years[P]``. Returns ``carrier -> {"total": float, "by_period":
    {str: float}}``.

    Mirrors the per-period aggregation in ``cost_breakdown`` so the Capacity
    and Economics views show the same gas / solar / battery CAPEX numbers.

    Lives here rather than in ``routers/compare.py``, where it was written,
    because the Results side could not reach it there without importing a
    router — and so a second aggregation grew there instead. The two capex
    parity suites exist to keep those two answers agreeing; this module is
    the address that makes a single answer possible.

    ``capital_cost_of(row, comp_attr) -> float`` is the seam. The caller owns
    cost resolution (``routers.compare._safe_capital_cost`` delegates to
    ``solver_service.periodized_capital_costs``), so this module needs
    neither that plumbing nor any ``routers.*`` import, and a test can drive
    the walk with a two-line lambda.
    """
    out: dict = {}

    def _walk(df, nom_col: str, comp_attr: str, *, extendable_only: bool = False) -> None:
        if df is None or df.empty:
            return
        if extendable_only:
            ext_col = f"{nom_col}_extendable"
            if ext_col not in df.columns:
                return
            df = df[df[ext_col].astype(bool)]
            if df.empty:
                return
        opt_col = f"{nom_col}_opt"
        capacity_col = opt_col if opt_col in df.columns else (nom_col if nom_col in df.columns else None)
        if capacity_col is None:
            return
        for asset in df.index:
            row = df.loc[asset]
            try:
                opt = float(row[capacity_col])
            except (TypeError, ValueError, KeyError):
                continue
            if not math.isfinite(opt) or opt <= 1e-9:
                continue
            cc_per_mw = capital_cost_of(row, comp_attr)
            if cc_per_mw <= 0:
                continue
            per_year_meur = (opt * cc_per_mw) / 1e6  # M€/yr annuitised
            carrier = str(row.get("carrier", "unknown") or "unknown").lower()
            b = out.setdefault(carrier, {"total": 0.0, "by_period": {}})
            if is_multi and periods:
                # Each period contributes annuitised_per_yr × ipw.years[P].
                # We assume the asset is active in every period — which is
                # what PyPSA's `n.statistics()` does for capex by default
                # (period-aware costing happens at LP time, not at stats
                # time). The horizon total is the sum across periods.
                for p in periods:
                    years = period_utils.years_for_period(years_map, p)
                    contrib = per_year_meur * years
                    b["by_period"][str(p)] = b["by_period"].get(str(p), 0.0) + contrib
                b["total"] = sum(b["by_period"].values())
            else:
                b["total"] += per_year_meur

    # Generation / storage / stores unconditionally, plus links.
    #
    # Lines and transformers stay omitted: a passive branch that the LP
    # cannot resize contributes nothing to the objective, and reporting its
    # notional CAPEX here produced a "line CAPEX" carrier value nobody could
    # reconcile with the Results panel. Branch expansion is visible in the
    # Line loading tab; that is where it belongs.
    #
    # An EXTENDABLE link is a different animal, and excluding it was a defect:
    # the LP does size it, its capital_cost does enter the objective, and
    # `_compute_economics_summary` has always counted it. So the two tabs of
    # one comparison reported different CAPEX for one network — MEASURED at
    # 25.154535 vs 25.320785 M€ on the golden fixture (exactly the
    # electrolyzer's CAPEX) and at a 56.192453 M€ gap on a real three-period
    # project. See docs/superpowers/findings/
    # 2026-08-03-compare-tab-correctness.md §S1.
    #
    # Every link that carries a capital_cost, not only the extendable ones.
    # `cost_breakdown` is built from `n.statistics()`, which charges
    # capital_cost x p_nom_opt for EVERY asset — so restricting this walk to
    # extendables made a fixed link with a capital_cost show up in Economics
    # and vanish from the Capacity tab, contradicting this function's
    # documented contract of mirroring cost_breakdown.
    _walk(generators,    "p_nom", "generators")
    _walk(storage_units, "p_nom", "storage_units")
    _walk(stores,        "e_nom", "stores")
    _walk(links,         "p_nom", "links")
    return out

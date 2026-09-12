"""
Lifted from `routers.results` (get_asset_economics).

The handler keeps the network lookup, the `_dispatch_ready` gate and every
`_state` read; this module gets the arithmetic and returns the payload, or
`None` where the endpoint answers 204. Result frames arrive through the
injected `result_df` callable where one is needed, so this runs on any
network with no router state — see `tests/test_results_seam.py`.

pandas / numpy / math are imported locally inside each function, the pattern
the router already used, so they are intentionally absent from this header.
"""
from __future__ import annotations

import logging

from services.period_utils import (
    period_years_map,
    years_for_period,
)
from services.results.load_frames import corrected_marginal_prices
from services.solver_service import periodized_capital_costs




# The SAME logger the router used, not a child of it: the lifted bodies must
# produce byte-identical log records — `tests/test_asset_economics_capital_costs.py`
# asserts on the channel name.
logger = logging.getLogger("pypsa_gui.results")

def compute_asset_economics(n, cfg, *, result_df):
    """
    Per-asset economics for Generator / StorageUnit / Store / Link.

    Lifted from `routers.results.get_asset_economics`, which keeps the network
    lookup, the `_dispatch_ready` gate and the `_state` reads. Returns the
    payload dict, or `None` where the handler returns 204.

    For each asset, computes:
      • revenue       = Σ_t p_t × price_t × weight_t      (€)
      • vom_cost      = Σ_t |p_t| × marginal_cost × weight_t  (€)
      • fixed_cost    = capital_cost × p_nom_opt          (€/yr; already
                        annualised by PyPSA's annuity machinery)
      • fom_cost      = fom_cost × p_nom_opt              (informational
                        breakdown of fixed_cost when the user typed FOM)
      • net_profit    = revenue − (fixed_cost + vom_cost)
      • LCOE / LCOS   = (fixed_cost + vom_cost [+ charge_cost]) / energy

    Storage adds:
      • discharge_mwh / charge_mwh — positive and negative halves of p_t
      • discharge_revenue / charge_cost — same split but multiplied by price
      • spread_eur_per_mwh = (discharge_revenue / discharge_mwh) −
                             (charge_cost / charge_mwh)

    Weightings: snapshot_weightings.objective × investment_period_weightings.years —
    same convention used everywhere else (cost_breakdown, carrier_kpis).

    Multi-period response also emits `by_period[period] = {...}` per asset so
    the frontend can show both the horizon-wide total AND a per-period view
    without re-running the same arithmetic on the client.

    What these numbers reconcile with (measured on a live network, not
    inferred from the code):

      • Σ `fixed_cost_eur` == Σ `economics_by_carrier.capex_meur` × 1e6
        EXACTLY — both 352,864,456.77, Δ = 0.00. That is the Dispatch tab's
        "CAPEX (annuitised)" KPI, and it is the reconciliation to quote.
      • Σ `vom_cost_eur` == `cost_breakdown.opex` EXACTLY — both
        691,055,137.75, Δ = 0.00.
      • It does NOT reconcile with `cost_breakdown.capex`. That figure was
        8,420,504,580.76 against Σ `fixed_cost_eur` of 352,864,456.77 — a
        23.9× difference — because `cost_breakdown` includes Line capex
        (8,067,640,123.99) and transformers, while this endpoint covers only
        Generator / StorageUnit / Store / Link. An earlier version of this
        docstring claimed `cost_breakdown.capex = Σ fixed_cost`; it was
        false, and comparing against it will look like a bug that isn't one.

    `capital_costs_available` (top level) is False when the capital-cost
    resolver raised. In that case every capital-cost-derived field —
    `fixed_cost_eur`, `fom_cost_eur`, `net_profit_eur`, `lcoe_eur_per_mwh`,
    `lcos_eur_per_mwh` — is emitted as `null` rather than 0.0, at the top
    level AND inside every `by_period` entry. Fields that owe nothing to
    capital cost (revenue, VOM, energy, capacity factor, prices, spread) keep
    their real values. See `_capital_derived` below for why.
    """
    import math

    import numpy as _np
    import pandas as _pd



    # ── Pre-compute the effective annualised capital_cost for every asset.
    # Same resolver the cost_breakdown endpoint feeds from, so Σ fixed_cost
    # here matches `economics_by_carrier`'s Σ capex — see the docstring for
    # what does and does not reconcile.
    #
    # When this raises, EVERY downstream lookup below falls through to its
    # `.get("capital_cost", 0.0)` default, and the whole tab renders €0.00
    # fixed cost, a net profit inflated by the missing CAPEX, and an
    # understated LCOE — all with the same confidence as real figures. The
    # flag and the nulls exist so that cannot happen silently again.
    capital_costs_available = True
    try:
        asset_costs = periodized_capital_costs(n, cfg)
    except Exception:
        logger.exception(
            "periodized_capital_costs failed in /results/asset_economics; "
            "capital-cost-derived fields will be reported as unavailable",
        )
        asset_costs = {}
        capital_costs_available = False

    # ── Snapshot + period weighting helpers ──────────────────────────────
    # Multi-period: `snapshot_weightings.objective` carries the per-row weight
    # (representative-week scaling lives here), and `investment_period_weightings.years`
    # carries the per-period multiplier. Flat snapshots: only the snapshot
    # weight applies.
    try:
        sw_obj = n.snapshot_weightings["objective"]
    except (KeyError, AttributeError):
        sw_obj = None
    # Energy basis: the `generators` column — PyPSA's n.statistics() energy
    # weight, the same basis the Dispatch tab uses. Cost terms (revenue, VOM,
    # charge cost) keep `objective`; ENERGY denominators (energy_mwh,
    # discharge/charge_mwh, capacity-factor hours) use this. Falls back to the
    # objective column when absent (older netcdf) — identical to prior behaviour.
    try:
        sw_gen = n.snapshot_weightings["generators"]
    except (KeyError, AttributeError):
        sw_gen = sw_obj
    period_years_lookup = period_years_map(n)
    # Horizon scaling factor for time-basis alignment between annual
    # CAPEX and horizon-summed OPEX/dispatch. Without this, LCOS for
    # storage and LCOE for generators are computed as (annual CAPEX +
    # horizon OPEX + horizon charge_cost) / horizon discharge — mixing
    # units. Documented in CLAUDE.md "LCOH/LCOE fleet aggregation mixing
    # single-year CAPEX with horizon-total OPEX". Surfaced by user:
    # Battery 1 LCOS = 4 €/MWh in this view vs 19.6 €/MWh in Compare
    # View, a ~5× ratio that exactly tracks the n_periods × annual mismatch.
    total_years_factor = (
        float(sum(period_years_lookup.values()))
        if period_years_lookup else 1.0
    )

    is_multi = isinstance(n.snapshots, _pd.MultiIndex)
    if is_multi:
        try:
            period_lvl = n.snapshots.get_level_values(0)
        except Exception:
            period_lvl = None
    else:
        period_lvl = None

    def _years_for_period(p) -> float:
        return years_for_period(period_years_lookup, p)

    def _weight_series_for(snapshots, sw) -> _pd.Series:
        """Per-row effective weight = sw × period.years (sw column: objective=cost, generators=energy)."""
        w = _pd.Series(1.0, index=snapshots, dtype=float)
        if sw is not None:
            try:
                w = w.multiply(sw.reindex(snapshots).fillna(1.0), axis=0)
            except Exception:
                pass
        if period_lvl is not None and period_years_lookup:
            try:
                years = _pd.Series(
                    [_years_for_period(p) for p in period_lvl],
                    index=snapshots, dtype=float,
                )
                w = w.multiply(years, axis=0)
            except Exception:
                pass
        return w

    # ── Per-row weights for the network's current snapshots ──────────────
    # Two bases: w_vals (objective) for COST quantities (revenue, VOM, charge
    # cost); w_vals_energy (generators) for ENERGY quantities (energy_mwh,
    # discharge/charge_mwh, capacity-factor hours). Equal when the columns
    # coincide; LCOE/LCOS = cost[objective] / energy[generators].
    snapshots = n.snapshots
    w_series = _weight_series_for(snapshots, sw_obj)
    w_vals = w_series.values
    w_series_energy = _weight_series_for(snapshots, sw_gen)
    w_vals_energy = w_series_energy.values
    # Period vector (same length as snapshots) for grouping later.
    if is_multi and period_lvl is not None:
        period_keys = [
            (int(p) if hasattr(p, "__int__") else p) for p in period_lvl
        ]
    else:
        period_keys = [None] * len(snapshots)

    def _safe_finite(x: float) -> float:
        return 0.0 if x is None or not math.isfinite(x) else float(x)

    def _capital_derived(x: float | None) -> float | None:
        """
        Emit a capital-cost-derived field, or `null` if the resolver failed.

        Use this — NOT `_safe_finite` — for anything computed from
        `asset_costs`. `_safe_finite(0.0)` is indistinguishable on the wire
        from a genuine zero, and the Economics tab formats it as "€0.00"
        beside real revenue figures. `null` is the only value the frontend
        cannot accidentally render as a number.
        """
        if not capital_costs_available:
            return None
        return None if x is None else _safe_finite(x)
    def _accumulate_per_period(
        series: _pd.Series,
        weights: _np.ndarray,
    ) -> tuple[float, dict]:
        """
        Sum (value × weight) across rows; also bucket by period.

        Returns (total, by_period_dict). For flat snapshots `by_period_dict`
        is empty (single-period collapses to the total).
        """
        vals = series.values
        # Vectorised weighted total. NaN/Inf → 0 so JSON serialises.
        finite_mask = _np.isfinite(vals) & _np.isfinite(weights)
        weighted = _np.where(finite_mask, vals * weights, 0.0)
        total = float(weighted.sum())
        if not is_multi:
            return total, {}
        by_p: dict = {}
        for i, p in enumerate(period_keys):
            if p is None:
                continue
            by_p[p] = by_p.get(p, 0.0) + float(weighted[i])
        return total, by_p

    # ── Marginal prices per bus, merit-order corrected ───────────────────
    # Was a third verbatim copy of the curtailment-subsidy correction. The
    # shared helper performs the IDENTICAL fetch this block used to do by
    # hand — `result_df(..., "lopf")`, zero-fallback on `n.snapshots`
    # (`snapshots` here is bound to exactly that), then `fillna(0.0)` — so
    # the collapse is behaviour-preserving, pinned by
    # tests/test_asset_economics_merit_order_parity.py.
    #
    # Why it matters that this is one function now: the copy that lived in
    # `get_prices` implemented only the first of the two branches and had
    # silently drifted (02b5e806). Three copies of a rule this subtle is how
    # that happened.
    # `result_df` is injected, not fetched: the decomposition made the
    # frame lookup the caller's to supply, and master's shared helper takes
    # it keyword-only. Omitting it made this return an empty frame, which
    # silently zeroed every asset's revenue rather than failing.
    prices = corrected_marginal_prices(n, result_df=result_df)

    # ── Generator block ──────────────────────────────────────────────────
    gen_rows: list[dict] = []
    try:
        gens_p = result_df(n, "generators_t", "p", "lopf")
    except Exception:
        gens_p = None
    if gens_p is not None and not gens_p.empty and not n.generators.empty:
        gens_df = n.generators
        # Use post-solve p_nom_opt when available (capacity expansion);
        # else fall back to the input p_nom.
        if "p_nom_opt" in gens_df.columns:
            p_nom = gens_df["p_nom_opt"].fillna(gens_df.get("p_nom", 0.0))
        else:
            p_nom = gens_df["p_nom"]
        mc_static = gens_df["marginal_cost"].fillna(0.0) if "marginal_cost" in gens_df.columns else _pd.Series(0.0, index=gens_df.index)
        fom_static = gens_df["fom_cost"].fillna(0.0) if "fom_cost" in gens_df.columns else _pd.Series(0.0, index=gens_df.index)
        # PyPSA also allows a time-varying marginal_cost — capture it when present.
        try:
            mc_t_df = n.get_switchable_as_dense("Generator", "marginal_cost")
        except Exception:
            mc_t_df = None

        for g in gens_p.columns:
            if g not in gens_df.index:
                continue
            bus = str(gens_df.at[g, "bus"])
            if bus not in prices.columns:
                continue
            try:
                p_series = gens_p[g].fillna(0.0)
            except Exception:
                continue
            price_series = prices[bus].reindex(p_series.index).fillna(0.0)
            if mc_t_df is not None and g in mc_t_df.columns:
                mc_series = mc_t_df[g].reindex(p_series.index).fillna(float(mc_static.get(g, 0.0)))
            else:
                mc_series = _pd.Series(float(mc_static.get(g, 0.0)), index=p_series.index)

            # revenue = Σ p × price × weight (€)
            revenue_total, revenue_per_p = _accumulate_per_period(p_series * price_series, w_vals)
            # vom = Σ p × marginal_cost × weight (€). For generators p is
            # non-negative, but use |p| anyway so a backed-out p_min_pu<0
            # asset (e.g. a Link-like generator) doesn't book negative VOM.
            vom_series = p_series.abs() * mc_series
            vom_total, vom_per_p = _accumulate_per_period(vom_series, w_vals)
            # ENERGY (LCOE denominator) on the generators basis; revenue/VOM above on objective.
            energy_total, energy_per_p = _accumulate_per_period(p_series, w_vals_energy)

            # Fixed cost: capital_cost (annualised) × p_nom_opt, scaled to
            # horizon by total_years_factor so the LCOE denominator (horizon-
            # summed energy via per-period weights × ipw.years) and the
            # numerator are on the same time basis. Without this, multi-period
            # generators show LCOE = (annual_capex + horizon_vom) / horizon_energy,
            # under-reporting CAPEX by `n_periods` (same bug as the storage
            # block fixed above).
            try:
                cc_eff = float(asset_costs.get("generators", {}).get(g, {}).get("capital_cost", 0.0))
            except (TypeError, ValueError):
                cc_eff = 0.0
            p_nom_g = float(p_nom.get(g, 0.0) or 0.0)
            fixed_cost_annual = cc_eff * p_nom_g
            fixed_cost = fixed_cost_annual * total_years_factor
            fom_per_mw = float(fom_static.get(g, 0.0) or 0.0)
            fom_cost = fom_per_mw * p_nom_g

            # LCOE — divide total horizon-scaled cost by total energy
            # dispatched. When energy is ~0 (e.g. a built-but-curtailed
            # renewable) the ratio is undefined; report None.
            denom = energy_total
            if denom > 1e-6:
                lcoe = (fixed_cost + vom_total) / denom
                avg_price = revenue_total / denom
            else:
                lcoe = None
                avg_price = None

            # Capacity factor: energy / (8760 × p_nom_opt × Σ years). Useful
            # for thermal vs renewable comparisons. Skip if p_nom_opt = 0.
            if p_nom_g > 1e-6:
                # Hours on the same (generators) basis as energy_total so the
                # capacity factor = energy / (p_nom × represented_hours) is consistent.
                total_hours_modelled = float(w_vals_energy.sum())
                cap_factor = energy_total / (p_nom_g * total_hours_modelled) if total_hours_modelled > 0 else None
            else:
                cap_factor = None

            # Per-period roll-up. Each period gets its own LCOE / avg-price.
            by_period_rows: list[dict] = []
            if is_multi and energy_per_p:
                # Per-period fixed cost is fixed_cost × years[p] / Σ years —
                # i.e. distribute the annualised CAPEX across periods in
                # proportion to their years weight. This matches what PyPSA's
                # statistics does: cost_per_period = capital_cost × p_nom × years.
                total_years = sum(period_years_lookup.values()) or 1.0
                for p_key in sorted(set(list(energy_per_p.keys()) + list(revenue_per_p.keys()))):
                    y = _years_for_period(p_key)
                    fixed_p = fixed_cost * (y / total_years) if total_years > 0 else 0.0
                    fom_p = fom_cost * (y / total_years) if total_years > 0 else 0.0
                    vom_p = vom_per_p.get(p_key, 0.0)
                    rev_p = revenue_per_p.get(p_key, 0.0)
                    e_p = energy_per_p.get(p_key, 0.0)
                    lcoe_p = ((fixed_p + vom_p) / e_p) if e_p > 1e-6 else None
                    avg_price_p = (rev_p / e_p) if e_p > 1e-6 else None
                    net_p = rev_p - fixed_p - vom_p
                    by_period_rows.append({
                        "period": p_key,
                        "energy_mwh": _safe_finite(e_p),
                        "revenue_eur": _safe_finite(rev_p),
                        "fixed_cost_eur": _capital_derived(fixed_p),
                        "fom_cost_eur": _capital_derived(fom_p),
                        "vom_cost_eur": _safe_finite(vom_p),
                        "net_profit_eur": _capital_derived(net_p),
                        "lcoe_eur_per_mwh": _capital_derived(lcoe_p),
                        "avg_price_eur_per_mwh": _safe_finite(avg_price_p) if avg_price_p is not None else None,
                    })

            gen_rows.append({
                "name": str(g),
                "bus": bus,
                "carrier": str(gens_df.at[g, "carrier"]) if "carrier" in gens_df.columns else "",
                "p_nom_opt_mw": _safe_finite(p_nom_g),
                "energy_mwh": _safe_finite(energy_total),
                "capacity_factor": _safe_finite(cap_factor) if cap_factor is not None else None,
                "revenue_eur": _safe_finite(revenue_total),
                "vom_cost_eur": _safe_finite(vom_total),
                "fixed_cost_eur": _capital_derived(fixed_cost),
                "fom_cost_eur": _capital_derived(fom_cost),
                "net_profit_eur": _capital_derived(revenue_total - fixed_cost - vom_total),
                "lcoe_eur_per_mwh": _capital_derived(lcoe) if lcoe is not None else None,
                "avg_price_eur_per_mwh": _safe_finite(avg_price) if avg_price is not None else None,
                "by_period": by_period_rows,
            })

    # ── StorageUnit block (arbitrage) ────────────────────────────────────
    # Sign convention: positive p = discharge (acts like generation), negative
    # p = charge (acts like load). Revenue comes from discharging into the
    # market; cost comes from charging from it.
    su_rows: list[dict] = []
    try:
        su_p = result_df(n, "storage_units_t", "p", "lopf")
    except Exception:
        su_p = None
    if su_p is not None and not su_p.empty and not n.storage_units.empty:
        su_df = n.storage_units
        if "p_nom_opt" in su_df.columns:
            p_nom_su = su_df["p_nom_opt"].fillna(su_df.get("p_nom", 0.0))
        else:
            p_nom_su = su_df["p_nom"]
        mc_static_su = su_df["marginal_cost"].fillna(0.0) if "marginal_cost" in su_df.columns else _pd.Series(0.0, index=su_df.index)
        fom_static_su = su_df["fom_cost"].fillna(0.0) if "fom_cost" in su_df.columns else _pd.Series(0.0, index=su_df.index)
        max_hours_su = su_df["max_hours"].fillna(0.0) if "max_hours" in su_df.columns else _pd.Series(0.0, index=su_df.index)

        for s in su_p.columns:
            if s not in su_df.index:
                continue
            bus = str(su_df.at[s, "bus"])
            if bus not in prices.columns:
                continue
            try:
                p_series = su_p[s].fillna(0.0)
            except Exception:
                continue
            price_series = prices[bus].reindex(p_series.index).fillna(0.0)

            discharge_series = p_series.clip(lower=0.0)
            charge_series = (-p_series).clip(lower=0.0)
            discharge_revenue_total, discharge_revenue_pp = _accumulate_per_period(
                discharge_series * price_series, w_vals,
            )
            charge_cost_total, charge_cost_pp = _accumulate_per_period(
                charge_series * price_series, w_vals,
            )
            discharge_mwh, discharge_mwh_pp = _accumulate_per_period(discharge_series, w_vals_energy)
            charge_mwh, charge_mwh_pp = _accumulate_per_period(charge_series, w_vals_energy)
            # PyPSA convention: marginal_cost applies to discharge dispatch.
            # Charge has no explicit cost in standard formulation. Keep VOM
            # restricted to discharge to match the LP objective contribution.
            vom_total_su, vom_pp_su = _accumulate_per_period(
                discharge_series * float(mc_static_su.get(s, 0.0)), w_vals,
            )

            try:
                cc_eff = float(asset_costs.get("storage_units", {}).get(s, {}).get("capital_cost", 0.0))
            except (TypeError, ValueError):
                cc_eff = 0.0
            p_nom_s = float(p_nom_su.get(s, 0.0) or 0.0)
            # `cc_eff` is annual annuitised €/MW/yr. To put it on the same
            # time basis as `vom_total_su` / `charge_cost_total` / `discharge_mwh`
            # (which are all horizon-summed via the per-period weights below),
            # multiply by `total_years_factor` = Σ ipw.years across periods.
            # On flat networks total_years_factor=1 and this is a no-op.
            # Documented bug: previous `fixed_cost = cc_eff × p_nom_s` mixed
            # annual capex with horizon opex → LCOS was understated by ~3×
            # for a 3-year multi-period horizon (Battery 1: reported 4 €/MWh,
            # true 16.4 €/MWh).
            fixed_cost_annual = cc_eff * p_nom_s
            fixed_cost = fixed_cost_annual * total_years_factor
            fom_per_mw = float(fom_static_su.get(s, 0.0) or 0.0)
            fom_cost = fom_per_mw * p_nom_s

            net_profit = discharge_revenue_total - charge_cost_total - vom_total_su - fixed_cost

            # LCOS — total cost to deliver one MWh of discharge energy.
            # Numerator includes the cost to charge (electricity bought
            # at market price), the variable cost of discharging, and
            # the horizon-scaled fixed cost. Denominator = discharge MWh
            # (what the storage actually delivered, horizon-summed).
            if discharge_mwh > 1e-6:
                lcos = (fixed_cost + vom_total_su + charge_cost_total) / discharge_mwh
            else:
                lcos = None
            # Spread = average discharge price − average charge price.
            avg_discharge_price = (discharge_revenue_total / discharge_mwh) if discharge_mwh > 1e-6 else None
            avg_charge_price = (charge_cost_total / charge_mwh) if charge_mwh > 1e-6 else None
            spread = None
            if avg_discharge_price is not None and avg_charge_price is not None:
                spread = avg_discharge_price - avg_charge_price
            # Round-trip efficiency for display — PyPSA stores eta_charge
            # and eta_dispatch separately.
            eta_c = float(su_df.at[s, "efficiency_store"]) if "efficiency_store" in su_df.columns else 1.0
            eta_d = float(su_df.at[s, "efficiency_dispatch"]) if "efficiency_dispatch" in su_df.columns else 1.0
            try:
                rte = eta_c * eta_d
            except Exception:
                rte = None

            by_period_rows: list[dict] = []
            if is_multi and discharge_mwh_pp:
                total_years = sum(period_years_lookup.values()) or 1.0
                periods_set = sorted(set(
                    list(discharge_mwh_pp.keys())
                    + list(charge_mwh_pp.keys())
                    + list(discharge_revenue_pp.keys())
                    + list(charge_cost_pp.keys())
                ))
                for p_key in periods_set:
                    y = _years_for_period(p_key)
                    fixed_p = fixed_cost * (y / total_years) if total_years > 0 else 0.0
                    fom_p = fom_cost * (y / total_years) if total_years > 0 else 0.0
                    dm = discharge_mwh_pp.get(p_key, 0.0)
                    cm = charge_mwh_pp.get(p_key, 0.0)
                    dr = discharge_revenue_pp.get(p_key, 0.0)
                    cc = charge_cost_pp.get(p_key, 0.0)
                    vp = vom_pp_su.get(p_key, 0.0)
                    np_period = dr - cc - vp - fixed_p
                    lcos_p = ((fixed_p + vp + cc) / dm) if dm > 1e-6 else None
                    spread_p = None
                    ap_d = (dr / dm) if dm > 1e-6 else None
                    ap_c = (cc / cm) if cm > 1e-6 else None
                    if ap_d is not None and ap_c is not None:
                        spread_p = ap_d - ap_c
                    by_period_rows.append({
                        "period": p_key,
                        "discharge_mwh": _safe_finite(dm),
                        "charge_mwh": _safe_finite(cm),
                        "discharge_revenue_eur": _safe_finite(dr),
                        "charge_cost_eur": _safe_finite(cc),
                        "fixed_cost_eur": _capital_derived(fixed_p),
                        "fom_cost_eur": _capital_derived(fom_p),
                        "vom_cost_eur": _safe_finite(vp),
                        "net_profit_eur": _capital_derived(np_period),
                        "lcos_eur_per_mwh": _capital_derived(lcos_p) if lcos_p is not None else None,
                        "spread_eur_per_mwh": _safe_finite(spread_p) if spread_p is not None else None,
                    })

            su_rows.append({
                "name": str(s),
                "bus": bus,
                "carrier": str(su_df.at[s, "carrier"]) if "carrier" in su_df.columns else "",
                "p_nom_opt_mw": _safe_finite(p_nom_s),
                "max_hours": _safe_finite(float(max_hours_su.get(s, 0.0) or 0.0)),
                "energy_capacity_mwh": _safe_finite(p_nom_s * float(max_hours_su.get(s, 0.0) or 0.0)),
                "round_trip_efficiency": _safe_finite(rte) if rte is not None else None,
                "discharge_mwh": _safe_finite(discharge_mwh),
                "charge_mwh": _safe_finite(charge_mwh),
                "discharge_revenue_eur": _safe_finite(discharge_revenue_total),
                "charge_cost_eur": _safe_finite(charge_cost_total),
                "vom_cost_eur": _safe_finite(vom_total_su),
                "fixed_cost_eur": _capital_derived(fixed_cost),
                "fom_cost_eur": _capital_derived(fom_cost),
                "net_profit_eur": _capital_derived(net_profit),
                "lcos_eur_per_mwh": _capital_derived(lcos) if lcos is not None else None,
                "spread_eur_per_mwh": _safe_finite(spread) if spread is not None else None,
                "avg_discharge_price_eur_per_mwh": _safe_finite(avg_discharge_price) if avg_discharge_price is not None else None,
                "avg_charge_price_eur_per_mwh": _safe_finite(avg_charge_price) if avg_charge_price is not None else None,
                "by_period": by_period_rows,
            })

    # ── Store block (energy-as-state arbitrage) ──────────────────────────
    # Same arbitrage logic as StorageUnit, but the capacity unit is MWh
    # (e_nom) instead of MW (p_nom). Hydrogen / heat storage commonly uses
    # this. Sign convention identical to StorageUnit.
    store_rows: list[dict] = []
    try:
        store_p = result_df(n, "stores_t", "p", "lopf")
    except Exception:
        store_p = None
    if store_p is not None and not store_p.empty and not n.stores.empty:
        stores_df = n.stores
        if "e_nom_opt" in stores_df.columns:
            e_nom = stores_df["e_nom_opt"].fillna(stores_df.get("e_nom", 0.0))
        else:
            e_nom = stores_df["e_nom"]
        mc_static_st = stores_df["marginal_cost"].fillna(0.0) if "marginal_cost" in stores_df.columns else _pd.Series(0.0, index=stores_df.index)
        fom_static_st = stores_df["fom_cost"].fillna(0.0) if "fom_cost" in stores_df.columns else _pd.Series(0.0, index=stores_df.index)

        for s in store_p.columns:
            if s not in stores_df.index:
                continue
            bus = str(stores_df.at[s, "bus"])
            if bus not in prices.columns:
                continue
            try:
                p_series = store_p[s].fillna(0.0)
            except Exception:
                continue
            price_series = prices[bus].reindex(p_series.index).fillna(0.0)

            discharge_series = p_series.clip(lower=0.0)
            charge_series = (-p_series).clip(lower=0.0)
            discharge_revenue_total, discharge_revenue_pp = _accumulate_per_period(
                discharge_series * price_series, w_vals,
            )
            charge_cost_total, charge_cost_pp = _accumulate_per_period(
                charge_series * price_series, w_vals,
            )
            discharge_mwh, discharge_mwh_pp = _accumulate_per_period(discharge_series, w_vals_energy)
            charge_mwh, charge_mwh_pp = _accumulate_per_period(charge_series, w_vals_energy)
            vom_total_st, vom_pp_st = _accumulate_per_period(
                discharge_series * float(mc_static_st.get(s, 0.0)), w_vals,
            )

            try:
                cc_eff = float(asset_costs.get("stores", {}).get(s, {}).get("capital_cost", 0.0))
            except (TypeError, ValueError):
                cc_eff = 0.0
            e_nom_s = float(e_nom.get(s, 0.0) or 0.0)
            # Horizon-scale annual capex (see storage_units block above for
            # the same fix). cc_eff is €/MWh/yr × e_nom_opt gives annual €;
            # multiplying by total_years_factor matches the horizon-summed
            # opex / discharge / charge_cost magnitudes used below.
            fixed_cost_annual = cc_eff * e_nom_s
            fixed_cost = fixed_cost_annual * total_years_factor
            fom_per_unit = float(fom_static_st.get(s, 0.0) or 0.0)
            fom_cost = fom_per_unit * e_nom_s

            net_profit = discharge_revenue_total - charge_cost_total - vom_total_st - fixed_cost
            if discharge_mwh > 1e-6:
                lcos = (fixed_cost + vom_total_st + charge_cost_total) / discharge_mwh
            else:
                lcos = None
            avg_discharge_price = (discharge_revenue_total / discharge_mwh) if discharge_mwh > 1e-6 else None
            avg_charge_price = (charge_cost_total / charge_mwh) if charge_mwh > 1e-6 else None
            spread = None
            if avg_discharge_price is not None and avg_charge_price is not None:
                spread = avg_discharge_price - avg_charge_price

            by_period_rows: list[dict] = []
            if is_multi and discharge_mwh_pp:
                total_years = sum(period_years_lookup.values()) or 1.0
                periods_set = sorted(set(
                    list(discharge_mwh_pp.keys())
                    + list(charge_mwh_pp.keys())
                    + list(discharge_revenue_pp.keys())
                    + list(charge_cost_pp.keys())
                ))
                for p_key in periods_set:
                    y = _years_for_period(p_key)
                    fixed_p = fixed_cost * (y / total_years) if total_years > 0 else 0.0
                    fom_p = fom_cost * (y / total_years) if total_years > 0 else 0.0
                    dm = discharge_mwh_pp.get(p_key, 0.0)
                    cm = charge_mwh_pp.get(p_key, 0.0)
                    dr = discharge_revenue_pp.get(p_key, 0.0)
                    cc = charge_cost_pp.get(p_key, 0.0)
                    vp = vom_pp_st.get(p_key, 0.0)
                    np_period = dr - cc - vp - fixed_p
                    lcos_p = ((fixed_p + vp + cc) / dm) if dm > 1e-6 else None
                    spread_p = None
                    ap_d = (dr / dm) if dm > 1e-6 else None
                    ap_c = (cc / cm) if cm > 1e-6 else None
                    if ap_d is not None and ap_c is not None:
                        spread_p = ap_d - ap_c
                    by_period_rows.append({
                        "period": p_key,
                        "discharge_mwh": _safe_finite(dm),
                        "charge_mwh": _safe_finite(cm),
                        "discharge_revenue_eur": _safe_finite(dr),
                        "charge_cost_eur": _safe_finite(cc),
                        "fixed_cost_eur": _capital_derived(fixed_p),
                        "fom_cost_eur": _capital_derived(fom_p),
                        "vom_cost_eur": _safe_finite(vp),
                        "net_profit_eur": _capital_derived(np_period),
                        "lcos_eur_per_mwh": _capital_derived(lcos_p) if lcos_p is not None else None,
                        "spread_eur_per_mwh": _safe_finite(spread_p) if spread_p is not None else None,
                    })

            store_rows.append({
                "name": str(s),
                "bus": bus,
                "carrier": str(stores_df.at[s, "carrier"]) if "carrier" in stores_df.columns else "",
                "e_nom_opt_mwh": _safe_finite(e_nom_s),
                "discharge_mwh": _safe_finite(discharge_mwh),
                "charge_mwh": _safe_finite(charge_mwh),
                "discharge_revenue_eur": _safe_finite(discharge_revenue_total),
                "charge_cost_eur": _safe_finite(charge_cost_total),
                "vom_cost_eur": _safe_finite(vom_total_st),
                "fixed_cost_eur": _capital_derived(fixed_cost),
                "fom_cost_eur": _capital_derived(fom_cost),
                "net_profit_eur": _capital_derived(net_profit),
                "lcos_eur_per_mwh": _capital_derived(lcos) if lcos is not None else None,
                "spread_eur_per_mwh": _safe_finite(spread) if spread is not None else None,
                "avg_discharge_price_eur_per_mwh": _safe_finite(avg_discharge_price) if avg_discharge_price is not None else None,
                "avg_charge_price_eur_per_mwh": _safe_finite(avg_charge_price) if avg_charge_price is not None else None,
                "by_period": by_period_rows,
            })

    # ── Link block (converters: electrolysers, heat pumps, P2X) ──────────
    # Missing entirely until 2026-07-31. The user asked why their electrolyser
    # showed no economics; the endpoint returned generators / storage_units /
    # stores and no `links` key at all, so a Link could not appear in the
    # Economics table however its costs were configured.
    #
    # A Link is two-sided in a way the other three are not: it BUYS at bus0 and
    # SELLS at bus1. `revenue_eur` is therefore the NET of the two — value
    # delivered at bus1 minus energy bought at bus0 — so `net_profit_eur`
    # (revenue − fixed − vom) means the same thing it does for a generator and
    # the columns stay comparable down the table. The gross halves ride along
    # as their own fields so the netting is auditable rather than implied, and
    # so the Compare view can reconstruct either convention.
    link_rows: list[dict] = []
    try:
        links_p0 = result_df(n, "links_t", "p0", "lopf")
        links_p1 = result_df(n, "links_t", "p1", "lopf")
    except Exception:
        links_p0 = links_p1 = None
    if links_p0 is not None and not links_p0.empty and not n.links.empty:
        links_df = n.links
        if "p_nom_opt" in links_df.columns:
            l_p_nom = links_df["p_nom_opt"].fillna(links_df.get("p_nom", 0.0))
        else:
            l_p_nom = links_df["p_nom"]
        l_mc_static = (
            links_df["marginal_cost"].fillna(0.0) if "marginal_cost" in links_df.columns
            else _pd.Series(0.0, index=links_df.index)
        )
        l_fom_static = (
            links_df["fom_cost"].fillna(0.0) if "fom_cost" in links_df.columns
            else _pd.Series(0.0, index=links_df.index)
        )
        try:
            l_mc_t_df = n.get_switchable_as_dense("Link", "marginal_cost")
        except Exception:
            l_mc_t_df = None

        for ln in links_p0.columns:
            if ln not in links_df.index:
                continue
            bus0 = str(links_df.at[ln, "bus0"]) if "bus0" in links_df.columns else ""
            bus1 = str(links_df.at[ln, "bus1"]) if "bus1" in links_df.columns else ""
            try:
                p0_series = links_p0[ln].fillna(0.0)
            except Exception:
                continue

            # `p1` is NEGATIVE when the Link delivers into bus1, so flip it to
            # get a positive quantity of energy sold. Fall back to
            # p0 × efficiency only when the dispatch table lacks p1.
            if links_p1 is not None and ln in links_p1.columns:
                out_series = -links_p1[ln].reindex(p0_series.index).fillna(0.0)
            else:
                try:
                    eff = float(links_df.at[ln, "efficiency"])
                except (KeyError, TypeError, ValueError):
                    eff = 1.0
                out_series = p0_series * eff
            gross_revenue_series = out_series * (
                prices[bus1].reindex(p0_series.index).fillna(0.0)
                if bus1 in prices.columns else 0.0
            )

            # Multi-output Links (CHP: bus0 gas → bus1 electricity + bus2 heat;
            # heat pumps with a second sink) deliver at bus2/bus3/bus4 as well.
            # Counting only bus1 would silently drop half a CHP's product and
            # inflate its unit cost accordingly. Each extra port is valued at
            # ITS OWN bus price, which is unambiguous; the energy total is the
            # combined output across ports, so for a multi-output Link the
            # unit cost is per MWh of everything it delivers.
            for port in ("2", "3", "4"):
                bus_col = f"bus{port}"
                if bus_col not in links_df.columns:
                    continue
                bus_n = str(links_df.at[ln, bus_col] or "").strip()
                if not bus_n:
                    continue
                try:
                    p_n_df = result_df(n, "links_t", f"p{port}", "lopf")
                except Exception:
                    p_n_df = None
                if p_n_df is None or ln not in getattr(p_n_df, "columns", []):
                    continue
                out_n = -p_n_df[ln].reindex(p0_series.index).fillna(0.0)
                out_series = out_series + out_n
                if bus_n in prices.columns:
                    gross_revenue_series = gross_revenue_series + (
                        out_n * prices[bus_n].reindex(p0_series.index).fillna(0.0)
                    )

            # Unlike the generator block, a missing bus price is NOT a reason
            # to drop the row. An H₂ or heat bus often carries no meaningful
            # dual, and skipping would reproduce the very bug this block
            # fixes — the asset silently vanishing from the table. Treat an
            # absent price as zero and still report capacity, energy and cost.
            if bus0 in prices.columns:
                price0 = prices[bus0].reindex(p0_series.index).fillna(0.0)
            else:
                price0 = _pd.Series(0.0, index=p0_series.index)
            if l_mc_t_df is not None and ln in l_mc_t_df.columns:
                l_mc_series = l_mc_t_df[ln].reindex(p0_series.index).fillna(float(l_mc_static.get(ln, 0.0)))
            else:
                l_mc_series = _pd.Series(float(l_mc_static.get(ln, 0.0)), index=p0_series.index)

            gross_revenue_total, gross_rev_per_p = _accumulate_per_period(gross_revenue_series, w_vals)
            input_cost_total, input_cost_per_p = _accumulate_per_period(p0_series * price0, w_vals)
            # PyPSA charges a Link's marginal_cost against p0 (the input), not
            # the output — matching how the LP builds the objective.
            vom_total, vom_per_p = _accumulate_per_period(p0_series.abs() * l_mc_series, w_vals)
            # ENERGY = what leaves bus1. Using p0 here would overstate a
            # 70%-efficient electrolyser's product by 1/0.7 and understate its
            # unit cost by the same factor.
            energy_total, energy_per_p = _accumulate_per_period(out_series, w_vals_energy)
            input_energy_total, _ = _accumulate_per_period(p0_series, w_vals_energy)

            try:
                cc_eff = float(asset_costs.get("links", {}).get(ln, {}).get("capital_cost", 0.0))
            except (TypeError, ValueError):
                cc_eff = 0.0
            p_nom_l = float(l_p_nom.get(ln, 0.0) or 0.0)
            fixed_cost = cc_eff * p_nom_l * total_years_factor
            fom_cost = float(l_fom_static.get(ln, 0.0) or 0.0) * p_nom_l

            revenue_total = gross_revenue_total - input_cost_total

            # All-in levelised cost of the Link's OUTPUT: capital + VOM + the
            # energy it had to buy. The bought energy belongs in the numerator
            # — for a 70%-efficient electrolyser it is the dominant term, and
            # omitting it produced €43.74/MWh against the LCOH panel's €246.02
            # for the identical asset. Two views of one converter disagreeing
            # by 5.6x is worse than either number alone, so this matches
            # `/results/lcoh` exactly, term for term.
            denom = energy_total
            if denom > 1e-6:
                lcoe = (fixed_cost + vom_total + input_cost_total) / denom
                avg_price = gross_revenue_total / denom
            else:
                lcoe = None
                avg_price = None

            # p_nom bounds the INPUT (p0), so utilisation is measured there.
            if p_nom_l > 1e-6:
                total_hours_modelled = float(w_vals_energy.sum())
                cap_factor = (
                    input_energy_total / (p_nom_l * total_hours_modelled)
                    if total_hours_modelled > 0 else None
                )
            else:
                cap_factor = None

            by_period_rows = []
            if is_multi and energy_per_p:
                total_years = sum(period_years_lookup.values()) or 1.0
                keys = set(energy_per_p) | set(gross_rev_per_p) | set(input_cost_per_p)
                for p_key in sorted(keys):
                    y = _years_for_period(p_key)
                    fixed_p = fixed_cost * (y / total_years) if total_years > 0 else 0.0
                    fom_p = fom_cost * (y / total_years) if total_years > 0 else 0.0
                    vom_p = vom_per_p.get(p_key, 0.0)
                    gross_p = gross_rev_per_p.get(p_key, 0.0)
                    in_p = input_cost_per_p.get(p_key, 0.0)
                    rev_p = gross_p - in_p
                    e_p = energy_per_p.get(p_key, 0.0)
                    # Same all-in basis as the horizon figure above.
                    lcoe_p = ((fixed_p + vom_p + in_p) / e_p) if e_p > 1e-6 else None
                    by_period_rows.append({
                        "period": p_key,
                        "energy_mwh": _safe_finite(e_p),
                        "revenue_eur": _safe_finite(rev_p),
                        "gross_revenue_eur": _safe_finite(gross_p),
                        "input_cost_eur": _safe_finite(in_p),
                        "fixed_cost_eur": _capital_derived(fixed_p),
                        "fom_cost_eur": _capital_derived(fom_p),
                        "vom_cost_eur": _safe_finite(vom_p),
                        "net_profit_eur": _capital_derived(rev_p - fixed_p - vom_p),
                        "lcoe_eur_per_mwh": _capital_derived(lcoe_p),
                        "avg_price_eur_per_mwh": _safe_finite((gross_p / e_p) if e_p > 1e-6 else 0.0) if e_p > 1e-6 else None,
                    })

            link_rows.append({
                "name": str(ln),
                "bus": bus0,
                "bus1": bus1,
                "carrier": str(links_df.at[ln, "carrier"]) if "carrier" in links_df.columns else "",
                "efficiency": _safe_finite(float(links_df.at[ln, "efficiency"])) if "efficiency" in links_df.columns else None,
                "p_nom_opt_mw": _safe_finite(p_nom_l),
                "energy_mwh": _safe_finite(energy_total),
                "input_energy_mwh": _safe_finite(input_energy_total),
                "capacity_factor": _safe_finite(cap_factor) if cap_factor is not None else None,
                "revenue_eur": _safe_finite(revenue_total),
                "gross_revenue_eur": _safe_finite(gross_revenue_total),
                "input_cost_eur": _safe_finite(input_cost_total),
                "vom_cost_eur": _safe_finite(vom_total),
                "fixed_cost_eur": _capital_derived(fixed_cost),
                "fom_cost_eur": _capital_derived(fom_cost),
                "net_profit_eur": _capital_derived(revenue_total - fixed_cost - vom_total),
                "lcoe_eur_per_mwh": _capital_derived(lcoe) if lcoe is not None else None,
                "avg_price_eur_per_mwh": _safe_finite(avg_price) if avg_price is not None else None,
                "by_period": by_period_rows,
            })

    # Periods list (sorted) for the frontend's period selector.
    periods_list: list = []
    if is_multi:
        seen = set()
        for p_key in period_keys:
            if p_key is not None and p_key not in seen:
                periods_list.append(p_key)
                seen.add(p_key)
        try:
            periods_list = sorted(periods_list, key=lambda x: (0, int(x)) if hasattr(x, "__int__") else (1, str(x)))
        except Exception:
            pass

    return {
        "currency": "EUR",
        "is_multi_period": is_multi,
        # False when `periodized_capital_costs` raised. Every capital-cost-
        # derived field in every row (and every `by_period` entry) is `null`
        # in that case — see `_capital_derived`. The flag is the summary; the
        # nulls are the wire signal. Consumers need both: the flag so one
        # banner can explain forty blank cells, the nulls so a consumer that
        # ignores the flag still cannot format a zero.
        "capital_costs_available": capital_costs_available,
        "periods": periods_list,
        "generators": gen_rows,
        "storage_units": su_rows,
        "stores": store_rows,
        "links": link_rows,
    }

"""
Economics comparison: revenue, OPEX, CAPEX, LCOE per carrier, per-asset LCOH.

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
    AssetLCOHEntry,
    CarrierEconomics,
    CarrierPeriodValue,
    EconomicsComparison,
)
from services.compare.support import (
    _CLS_TO_ATTR,
    _build_snapshot_weights,
    _per_period_groupby,
    _periodized_lookup,
    _safe_capital_cost,
)
from services import period_utils
from services.results.load_frames import corrected_marginal_prices


def _compute_economics_summary(n, periods, is_multi, has_solve, prices_from_state: bool = True,
                               lost_load_cap: dict | None = None, *, cfg=None, result_df=None) -> EconomicsComparison:
    """
    Per-carrier economic summary for the Economics comparison tab.

    For each generator/storage_unit in the network, accumulate four
    quantities into a per-carrier bucket:

      * dispatch (MWh)    — Σ p × generators_weight × period_year (ENERGY basis)
      * revenue (€)       — Σ p × bus_marginal_price × objective_weight
      * opex   (€)        — Σ p × marginal_cost × objective_weight
      * capex  (€/yr)     — p_nom_opt × annuitised_capital_cost

    Energy (dispatch_mwh) is weighted by ``snapshot_weightings.generators``;
    cost quantities by ``snapshot_weightings.objective``. See the weight setup
    below — this keeps the per-carrier GWh equal to the Compare Dispatch tab and
    n.statistics(), while LCOE's numerator stays on the cost basis.

    LCOE per carrier is then computed POST-roll-up:
      ``LCOE = (Σ capex + Σ opex) / Σ dispatch_MWh``

    A few subtle choices:

      * Storage discharge counts as dispatch for the revenue line so the
        battery's arbitrage profit shows up (and matches what dispatch's
        energy-mix chart already reports). Charging is excluded — it would
        artificially deflate net revenue.
      * Capex is attributed to its build_year period (matching CAPACITY's
        rule). Pre-existing assets (build_year outside the horizon) still
        contribute to the aggregate but not to a specific period.
      * Per-period LCOE uses the period's capex + opex over the period's
        dispatch — i.e., each year stands on its own, no cross-period
        amortization.
    """
    import math as _math

    import pandas as pd
    if not has_solve:
        return EconomicsComparison()

    # COST quantities (opex, revenue, curtailment, lost-load, storage charge
    # cost) use the `objective` weighting column; ENERGY quantities
    # (dispatch_mwh — the LCOE denominator) use `generators`, matching PyPSA's
    # n.statistics() and the Compare Dispatch tab (_compute_dispatch_summary).
    # When the two columns are equal (the common single-weight case) these are
    # identical; they diverge only under representative-week weighting, where
    # energy must follow `generators` or the Economics-tab GWh disagree with the
    # Dispatch tab for the same carrier.
    weights = _build_snapshot_weights(n)                       # objective → COST
    energy_weights = _build_snapshot_weights(n, "generators")  # generators → ENERGY
    sns = n.snapshots
    # Per-snapshot bus marginal price — needed for revenue. Average across
    # buses is wrong for locational pricing; use each generator's home bus.
    # Curtailment-cost-corrected bus marginal prices — the SAME merit-order
    # correction the per-asset Results tab (asset_economics) applies, via a
    # shared helper. Without it, storage charge-cost / revenue here priced
    # against the raw (subsidy-distorted) dual, so the per-carrier LCOE diverged
    # from the per-asset LCOS by the curtailment-subsidy term (battery 148 vs
    # 153 EUR/MWh). Lazy import avoids the simulation<->projects import cycle.
    # `prices_from_state`: True for the LIVE Results endpoint (read the LP-stage
    # `_state` snapshot, matching asset_economics); False for a loaded Compare
    # bundle (read temp_n's own duals, never the live network's cached snapshot).
    try:
        bus_prices = corrected_marginal_prices(n, from_state=prices_from_state, result_df=result_df)
    except Exception:
        bus_prices = getattr(n.buses_t, "marginal_price", None) if hasattr(n, "buses_t") else None

    # Per-asset annuitised capital_cost, resolved once via the SAME
    # `periodized_capital_costs` path `asset_economics` / `cost_breakdown` /
    # `asset_costs` use — see `_periodized_lookup`'s docstring.
    pcc = _periodized_lookup(n, cfg=cfg)

    # Investment-period weightings — years × period. Default 1.0 each. Used
    # to scale annuitised CAPEX commitment from a single-year cost to the
    # period's total commitment (annual_cost × years_in_period).
    ipw_years: dict = period_utils.period_years_map(n)

    # Same vintage_results dict that the capacity summary consumes — gives
    # us per-period p_nom_opt attribution that the collapsed dataframe row
    # loses after the solver restores vintages to their parent. Without
    # this, CAPEX bookkeeping would lump every asset's annual cost under
    # the parent's (often pre-horizon) build_year — making per-period
    # CAPEX columns zero and per-period LCOE degenerate to marginal_cost.
    vintage_root = (n.meta or {}).get("vintage_results", {})
    if not isinstance(vintage_root, dict):
        vintage_root = {}

    def _vintage_handled(cls_name: str, parent_name: str) -> bool:
        block = vintage_root.get(cls_name)
        return isinstance(block, dict) and parent_name in block

    by_carrier: dict[str, dict] = {}

    def _bucket(carrier: str) -> dict:
        if carrier not in by_carrier:
            by_carrier[carrier] = {
                "dispatch_mwh":  {"total": 0.0, "by_period": {}},
                "revenue_eur":   {"total": 0.0, "by_period": {}},
                # `opex_eur` is the headline TOTAL (gen + charge + curtailment
                # + lost_load). The split buckets below let the user see WHAT
                # the OPEX consists of per carrier.
                "opex_eur":      {"total": 0.0, "by_period": {}},
                "gen_cost_eur":            {"total": 0.0, "by_period": {}},
                "storage_charge_cost_eur": {"total": 0.0, "by_period": {}},
                "curtailment_cost_eur":    {"total": 0.0, "by_period": {}},
                "lost_load_cost_eur":      {"total": 0.0, "by_period": {}},
                "capex_eur":     {"total": 0.0, "by_period": {}},
            }
        return by_carrier[carrier]

    def _accum(bucket: dict, total_val: float, pp_vals: dict[str, float]) -> None:
        bucket["total"] += total_val
        for p, v in pp_vals.items():
            bucket["by_period"][p] = bucket["by_period"].get(p, 0.0) + v

    def _capex_commitment(cc_per_mw_yr: float, p_nom: float,
                          build_year, lifetime) -> tuple[float, dict[str, float]]:
        """
        Annuitised CAPEX commitment across the horizon — FULL-HORIZON basis.

        Returns ``(total_horizon_eur, by_period_eur)`` where:

          * ``total``       = annual_cost × Σ ipw.years over ALL horizon periods
          * ``by_period[P]`` = annual_cost × ipw.years[P]  (every period)

        This matches PyPSA's ``n.statistics()`` / ``/results/cost_breakdown``,
        the per-asset ``/results/asset_economics`` tab, and the Compare
        Capacity tab (``_compute_total_annuitised_capex``) — so the Economics
        comparison reconciles with all of them.

        Previously this gated each period on build-year "active" years
        (``build_year ≤ P < build_year + lifetime``), which UNDER-counted capex
        for capacity built in later vintages: a battery whose 477 MW is mostly
        built in 2027-28 accrued only ~1.2 years of annuity (€49.5 M) instead
        of the authoritative full-horizon €122.9 M reported everywhere else.
        ``build_year`` / ``lifetime`` are kept as parameters (the vintage walk
        passes them) but no longer reduce the commitment.

        Flat (single-period) networks short-circuit to one annual cost on
        the total bucket; ``by_period`` is empty (matches every other
        per-period field's flat-network behaviour).
        """
        if cc_per_mw_yr <= 0 or p_nom <= 1e-9 or not _math.isfinite(p_nom):
            return 0.0, {}
        annual = cc_per_mw_yr * p_nom  # €/yr
        if not is_multi or not periods:
            return annual, {}
        total = 0.0
        pp: dict[str, float] = {}
        for P in periods:
            years_in_P = period_utils.years_for_period(ipw_years, P)
            commitment = annual * years_in_P
            pp[str(P)] = commitment
            total += commitment
        return total, pp

    def _walk_capex_vintage(cls_name: str, df) -> None:
        """
        Per-vintage CAPEX attribution. Pulls each vintage row's
        ``p_nom_opt`` and ``build_year`` from ``n.meta["vintage_results"]``
        — the only place the per-period breakdown survives the post-solve
        collapse onto the parent.
        """
        block = vintage_root.get(cls_name)
        if not isinstance(block, dict) or df is None or df.empty:
            return
        for parent_name, payload in block.items():
            if parent_name not in df.index:
                continue
            row = df.loc[parent_name]
            carrier = str(row.get("carrier", "unknown") or "unknown").lower()
            cc = _safe_capital_cost(row, pcc, _CLS_TO_ATTR[cls_name])
            if cc <= 0:
                continue
            lt = row.get("lifetime") if "lifetime" in df.columns else None
            # Pre-existing capacity that came pre-build — annuitise under
            # the parent's build_year, treated as always-active when that
            # year is outside the horizon.
            try:
                ini = float(payload.get("initial_capacity") or 0)
            except (TypeError, ValueError):
                ini = 0.0
            if _math.isfinite(ini) and ini > 0:
                total, pp = _capex_commitment(cc, ini, row.get("build_year"), lt)
                _accum(_bucket(carrier)["capex_eur"], total, pp)
            for entry in payload.get("periods") or []:
                try:
                    opt = float(entry.get("p_nom_opt") or 0)
                except (TypeError, ValueError):
                    continue
                if not _math.isfinite(opt) or opt <= 1e-9:
                    continue
                total, pp = _capex_commitment(cc, opt, entry.get("build_year"), lt)
                _accum(_bucket(carrier)["capex_eur"], total, pp)

    def _walk_capex_plain(cls_name: str, df, nom: str) -> None:
        """
        Per-asset CAPEX for assets WITHOUT a vintage_results entry —
        uses the dataframe row's full ``p_nom_opt`` (which equals
        ``p_nom`` for non-extendable assets, so commitment = annual cost
        of the existing fleet).
        """
        if df is None or df.empty:
            return
        opt_col = f"{nom}_opt"
        if opt_col not in df.columns:
            return
        for asset_name in df.index:
            if _vintage_handled(cls_name, asset_name):
                continue
            row = df.loc[asset_name]
            carrier = str(row.get("carrier", "unknown") or "unknown").lower()
            cc = _safe_capital_cost(row, pcc, _CLS_TO_ATTR[cls_name])
            if cc <= 0:
                continue
            try:
                opt = float(row[opt_col])
            except (TypeError, ValueError):
                continue
            if not _math.isfinite(opt) or opt <= 1e-9:
                continue
            lt = row.get("lifetime") if "lifetime" in df.columns else None
            total, pp = _capex_commitment(cc, opt, row.get("build_year"), lt)
            _accum(_bucket(carrier)["capex_eur"], total, pp)

    def _walk_dispatch_side(df, t_p_df) -> None:
        """
        Per-asset DISPATCH + OPEX + REVENUE. CAPEX is handled separately
        above so the active-period attribution can be vintage-aware.
        """
        if df is None or df.empty or t_p_df is None or t_p_df.empty:
            return
        for asset_name in df.index:
            if asset_name not in t_p_df.columns:
                continue
            row = df.loc[asset_name]
            carrier = str(row.get("carrier", "unknown") or "unknown").lower()
            b = _bucket(carrier)
            try:
                series = t_p_df[asset_name].reindex(sns).fillna(0.0).astype(float)
            except Exception:
                continue
            # Storage: split the signed series into discharge (positive) and
            # charge (negative magnitude). The dispatch + revenue series use
            # discharge only — matching the dispatch summary's choice and
            # `/results/storage_dispatch`. The CHARGE COST (= bus_price ×
            # |charge| × weight) is added to opex_eur so the per-carrier LCOE
            # reflects what the storage operator actually pays for energy.
            # Without this, LCOE for storage under-counts the round-trip cost
            # → on a network with negative bus prices during charging hours
            # (solar curtailment) the gap with per-asset LCOS reaches several
            # €/MWh (user-flagged: Battery 1 showed 19.6 €/MWh here vs 16.4
            # in asset_economics; the 3.2 difference was exactly the missing
            # charge_cost term).
            is_storage_carrier = df is n.storage_units
            if is_storage_carrier:
                discharge_series = series.clip(lower=0)
                charge_series = (-series).clip(lower=0)
                weighted = discharge_series * energy_weights
            else:
                discharge_series = series
                charge_series = None
                weighted = series * energy_weights
            mwh_total = float(weighted.sum())
            if not _math.isfinite(mwh_total):
                continue
            mwh_pp = _per_period_groupby(weighted, sns, is_multi)
            _accum(b["dispatch_mwh"], mwh_total, mwh_pp)

            try:
                mc = float(row["marginal_cost"]) if "marginal_cost" in df.columns else 0.0
            except (TypeError, ValueError):
                mc = 0.0
            if _math.isfinite(mc) and mc > 0:
                opex_t = discharge_series * mc * weights
                opex_total = float(opex_t.sum())
                opex_pp = _per_period_groupby(opex_t, sns, is_multi)
                # Both the headline opex AND the gen_cost split bucket get
                # the VOM contribution. For storage_units this is the
                # discharge VOM only; the charge cost has its own bucket below.
                _accum(b["opex_eur"], opex_total, opex_pp)
                _accum(b["gen_cost_eur"], opex_total, opex_pp)

            bus = row.get("bus") if "bus" in df.columns else None
            if bus_prices is not None and not bus_prices.empty and bus in bus_prices.columns:
                try:
                    bp_series = bus_prices[bus].reindex(sns).fillna(0.0).astype(float)
                    rev_t = discharge_series * bp_series * weights
                    rev_total = float(rev_t.sum())
                    rev_pp = _per_period_groupby(rev_t, sns, is_multi)
                    _accum(b["revenue_eur"], rev_total, rev_pp)
                    # Storage charge cost — split into its own bucket AND
                    # added to the total opex_eur so the LCOE accounting
                    # matches /api/results/asset_economics's per-asset LCOS.
                    if is_storage_carrier and charge_series is not None:
                        cc_t = charge_series * bp_series * weights
                        cc_total = float(cc_t.sum())
                        cc_pp = _per_period_groupby(cc_t, sns, is_multi)
                        _accum(b["opex_eur"], cc_total, cc_pp)
                        _accum(b["storage_charge_cost_eur"], cc_total, cc_pp)
                except Exception:
                    pass

    # CAPEX: vintage path first, then plain path for assets without vintages.
    _walk_capex_vintage("Generator",   n.generators)
    _walk_capex_vintage("StorageUnit", n.storage_units)
    _walk_capex_vintage("Store",       n.stores)
    _walk_capex_vintage("Link",        n.links)
    _walk_capex_plain("Generator",   n.generators,    "p_nom")
    _walk_capex_plain("StorageUnit", n.storage_units, "p_nom")
    _walk_capex_plain("Store",       n.stores,        "e_nom")
    _walk_capex_plain("Link",        n.links,         "p_nom")

    # DISPATCH/OPEX/REVENUE: storage uses `_t.p` (signed, grid-side) so the
    # GWh match the dispatch summary + `/results/storage_dispatch`. Stores
    # have no dispatch — capex_only above. Links use `_t.p0` (signed flow
    # bus0→bus1); their revenue calculation safely no-ops because Links
    # don't have a single "bus" column (the revenue lookup falls through).
    # Sector-coupling Links contribute CAPEX + OPEX + dispatch to their
    # carrier bucket (heat-pump-waste, H2, P2X, etc.) so the economics tab
    # surfaces them in the per-carrier rollup.
    _walk_dispatch_side(n.generators,    getattr(n.generators_t, "p", None) if hasattr(n, "generators_t") else None)
    _walk_dispatch_side(n.storage_units, getattr(n.storage_units_t, "p", None) if hasattr(n, "storage_units_t") else None)
    _walk_dispatch_side(n.links,         getattr(n.links_t, "p0", None) if hasattr(n, "links_t") else None)

    # ── Curtailment cost per carrier ────────────────────────────────────────
    # For each renewable generator with `curtailment_cost > 0`, the LP pays
    # `cost × Σ_t (p_max_pu × p_nom_opt − p) × weights` for spilled energy.
    # Accumulated into the GENERATOR'S carrier bucket (so a solar generator's
    # curtailment penalty appears under "solar" carrier OPEX). Mirrors the
    # curtailment wrapper logic in solver_service so the displayed cost
    # matches what the LP actually paid.
    if "curtailment_cost" in n.generators.columns and hasattr(n, "generators_t"):
        p_t_curt = getattr(n.generators_t, "p", None)
        p_max_pu_t = getattr(n.generators_t, "p_max_pu", None)
        if p_t_curt is not None and not p_t_curt.empty:
            for g_name in n.generators.index:
                try:
                    cc = float(n.generators.at[g_name, "curtailment_cost"] or 0.0)
                except (TypeError, ValueError):
                    cc = 0.0
                if not _math.isfinite(cc) or cc <= 0:
                    continue
                if g_name not in p_t_curt.columns:
                    continue
                try:
                    p_series = p_t_curt[g_name].reindex(sns).fillna(0.0).astype(float)
                    p_nom_opt = float(n.generators.at[g_name, "p_nom_opt"]) if "p_nom_opt" in n.generators.columns else 0.0
                    if not _math.isfinite(p_nom_opt) or p_nom_opt <= 1e-9:
                        continue
                    # Effective max per snapshot — p_max_pu × p_nom_opt.
                    if p_max_pu_t is not None and g_name in p_max_pu_t.columns:
                        pmp_series = p_max_pu_t[g_name].reindex(sns).fillna(1.0).astype(float) * p_nom_opt
                    else:
                        static_pmp = float(n.generators.at[g_name, "p_max_pu"]) if "p_max_pu" in n.generators.columns else 1.0
                        pmp_series = pd.Series(static_pmp * p_nom_opt, index=sns, dtype=float)
                    curt_amt = (pmp_series - p_series).clip(lower=0)
                    curt_cost_t = curt_amt * cc * weights
                    curt_cost_total = float(curt_cost_t.sum())
                    if not _math.isfinite(curt_cost_total) or abs(curt_cost_total) < 1e-6:
                        continue
                    curt_cost_pp = _per_period_groupby(curt_cost_t, sns, is_multi)
                    carrier_g = str(n.generators.at[g_name, "carrier"] or "unknown").lower()
                    b = _bucket(carrier_g)
                    _accum(b["opex_eur"], curt_cost_total, curt_cost_pp)
                    _accum(b["curtailment_cost_eur"], curt_cost_total, curt_cost_pp)
                except Exception:
                    continue

    # ── Lost-load cost per carrier (load-bearing bus → its carrier) ─────────
    # VOLL slack dispatch is passed in by the caller (`lost_load_cap`) — it is
    # NOT on the network. The capture is persisted to results_state.pkl by the
    # solver service, which strips the slack generators right after capturing
    # them; nothing ever writes it to `n.meta`, so the previous read from there
    # returned None on every code path and this whole block was dead (the
    # lost_load_cost_meur field was always 0.0). Callers supply it from the
    # project pickle (compare) or the live solver state (foreground results).
    # For each bus with non-zero slack, look up its carrier and accumulate
    # (slack × VOLL) into the carrier's opex bucket + the dedicated
    # lost_load_cost_eur split. Skipped when the capture is absent (network
    # solved without VOLL, or no shedding occurred).
    cap = lost_load_cap
    if isinstance(cap, dict):
        ll_df = cap.get("lost_load_t")
        ll_total_mwh = float(cap.get("lost_load_total_mwh", 0.0) or 0.0)
        ll_total_cost = float(cap.get("lost_load_cost_eur", 0.0) or 0.0)
        voll = (ll_total_cost / ll_total_mwh) if ll_total_mwh > 1e-9 else 0.0
        if voll > 0 and ll_df is not None and not getattr(ll_df, "empty", True):
            try:
                ll_aligned = ll_df.reindex(sns).fillna(0.0).astype(float)
                ll_weighted = ll_aligned.mul(weights, axis=0)
                # Bus → carrier lookup.
                bus_carrier_map: dict = {}
                if "carrier" in n.buses.columns:
                    for b_name in n.buses.index:
                        bus_carrier_map[b_name] = str(n.buses.at[b_name, "carrier"] or "unknown").lower()
                for bus_name in ll_weighted.columns:
                    bus_mwh = float(ll_weighted[bus_name].sum())
                    if not _math.isfinite(bus_mwh) or abs(bus_mwh) < 1e-6:
                        continue
                    bus_cost = bus_mwh * voll
                    bus_cost_pp = _per_period_groupby(ll_weighted[bus_name] * voll, sns, is_multi)
                    carrier_b = bus_carrier_map.get(bus_name, "unknown")
                    b = _bucket(carrier_b)
                    _accum(b["opex_eur"], bus_cost, bus_cost_pp)
                    _accum(b["lost_load_cost_eur"], bus_cost, bus_cost_pp)
            except Exception:
                pass

    # Convert €→M€ and MWh→GWh, then derive LCOE.
    def _to_meur(d):  return {"total": d["total"] / 1e6, "by_period": {k: v / 1e6 for k, v in d["by_period"].items()}}
    def _to_gwh(d):   return {"total": d["total"] / 1000.0, "by_period": {k: v / 1000.0 for k, v in d["by_period"].items()}}

    out: dict[str, CarrierEconomics] = {}
    for c, agg in by_carrier.items():
        rev_m = _to_meur(agg["revenue_eur"])
        opex_m = _to_meur(agg["opex_eur"])
        capex_m = _to_meur(agg["capex_eur"])
        disp_g = _to_gwh(agg["dispatch_mwh"])
        # New per-cost-type buckets — split of opex_eur so the frontend can
        # show users WHAT they paid for. Missing buckets fall through to a
        # zero CarrierPeriodValue.
        gen_cost_m = _to_meur(agg.get("gen_cost_eur") or {"total": 0.0, "by_period": {}})
        storage_charge_m = _to_meur(agg.get("storage_charge_cost_eur") or {"total": 0.0, "by_period": {}})
        curtailment_m = _to_meur(agg.get("curtailment_cost_eur") or {"total": 0.0, "by_period": {}})
        lost_load_m = _to_meur(agg.get("lost_load_cost_eur") or {"total": 0.0, "by_period": {}})
        # LCOE €/MWh = (capex_eur + CASH opex_eur) / dispatch_MWh, where CASH
        # opex EXCLUDES the curtailment + lost-load PENALTIES. Those are
        # non-cash modelling soft-constraints (curtailment_cost discourages
        # spilling renewables; VOLL prices unserved demand) — not real operating
        # expenses — so they don't belong in a levelised generation cost. This
        # matches the per-asset Results LCOE/LCOS (asset_economics uses
        # fixed + vom [+ charge] only), so e.g. "solar LCOE" reads identically
        # on the Results and Compare tabs. The penalties stay visible in
        # opex_meur (total) and their own split buckets — just not in the ratio.
        def _lcoe(capex_eur: float, opex_eur: float, dispatch_mwh: float) -> float:
            return (capex_eur + opex_eur) / dispatch_mwh if dispatch_mwh > 1e-9 else 0.0
        _curt_b = agg.get("curtailment_cost_eur") or {"total": 0.0, "by_period": {}}
        _ll_b = agg.get("lost_load_cost_eur") or {"total": 0.0, "by_period": {}}
        cash_opex_total = agg["opex_eur"]["total"] - _curt_b["total"] - _ll_b["total"]
        lcoe_total = _lcoe(agg["capex_eur"]["total"], cash_opex_total, agg["dispatch_mwh"]["total"])
        lcoe_pp: dict[str, float] = {}
        for p, mwh in agg["dispatch_mwh"]["by_period"].items():
            capex_pp = agg["capex_eur"]["by_period"].get(p, 0.0)
            cash_opex_pp = (agg["opex_eur"]["by_period"].get(p, 0.0)
                            - _curt_b["by_period"].get(p, 0.0)
                            - _ll_b["by_period"].get(p, 0.0))
            lcoe_pp[p] = _lcoe(capex_pp, cash_opex_pp, mwh)

        out[c] = CarrierEconomics(
            revenue_meur=CarrierPeriodValue(**rev_m),
            opex_meur=CarrierPeriodValue(**opex_m),
            gen_cost_meur=CarrierPeriodValue(**gen_cost_m),
            storage_charge_cost_meur=CarrierPeriodValue(**storage_charge_m),
            curtailment_cost_meur=CarrierPeriodValue(**curtailment_m),
            lost_load_cost_meur=CarrierPeriodValue(**lost_load_m),
            capex_meur=CarrierPeriodValue(**capex_m),
            dispatch_gwh=CarrierPeriodValue(**disp_g),
            lcoe_eur_per_mwh=CarrierPeriodValue(total=lcoe_total, by_period=lcoe_pp),
        )

    # Per-Link levelised cost. Closes BLOCKER from Compare View audit
    # 2026-05-25: users couldn't compare H2 plant / heat pump economics
    # across scenarios because only fleet rollup was exposed. For each
    # extendable Link with non-trivial dispatch, compute LCOH-style metric
    # = (CAPEX horizon-total + OPEX + input-electricity cost at bus0) /
    # (|p0| × efficiency, in MWh of bus1's carrier). Multi-port Links (bus2
    # set) only counts bus1 output; bus2's reverse flow contributes via
    # efficiency2 to the LP balance but isn't separately priced.
    per_asset_lcoh: list[AssetLCOHEntry] = []
    if n.links is not None and not n.links.empty:
        links_p0 = getattr(n.links_t, "p0", None) if hasattr(n, "links_t") else None
        if links_p0 is not None and not links_p0.empty:
            for link_name in n.links.index:
                if link_name not in links_p0.columns:
                    continue
                row = n.links.loc[link_name]
                try:
                    p_nom_opt = float(row.get("p_nom_opt", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if not _math.isfinite(p_nom_opt) or p_nom_opt <= 1e-9:
                    continue
                try:
                    p0_series = links_p0[link_name].reindex(sns).fillna(0.0).astype(float)
                except Exception:
                    continue
                try:
                    eff = float(row.get("efficiency", 1.0) or 1.0)
                except (TypeError, ValueError):
                    eff = 1.0
                if not _math.isfinite(eff):
                    eff = 1.0
                output_t = p0_series.abs() * abs(eff) * weights
                output_mwh_total = float(output_t.sum())
                if output_mwh_total <= 1e-6:
                    continue
                output_mwh_pp = _per_period_groupby(output_t, sns, is_multi)

                try:
                    mc = float(row.get("marginal_cost", 0) or 0)
                except (TypeError, ValueError):
                    mc = 0.0
                if not _math.isfinite(mc) or mc <= 0:
                    opex_eur_total = 0.0
                    opex_eur_pp: dict[str, float] = {}
                else:
                    opex_t = p0_series.abs() * mc * weights
                    opex_eur_total = float(opex_t.sum())
                    opex_eur_pp = _per_period_groupby(opex_t, sns, is_multi)

                # Input-electricity cost at bus0 (only positive-direction flow
                # counts as input). For energy-flow Links bus0 is the source.
                input_eur_total = 0.0
                input_eur_pp: dict[str, float] = {}
                try:
                    bus0 = row.get("bus0")
                except Exception:
                    bus0 = None
                if (bus_prices is not None and not bus_prices.empty
                        and bus0 is not None and bus0 in bus_prices.columns):
                    try:
                        bp = bus_prices[bus0].reindex(sns).fillna(0.0).astype(float)
                        input_t = p0_series.clip(lower=0) * bp * weights
                        input_eur_total = float(input_t.sum())
                        input_eur_pp = _per_period_groupby(input_t, sns, is_multi)
                    except Exception:
                        pass

                # CAPEX: prefer vintage breakdown when present, else plain.
                cc = _safe_capital_cost(row, pcc, "links")
                lt_val = row.get("lifetime") if "lifetime" in n.links.columns else None
                capex_eur_total = 0.0
                capex_eur_pp: dict[str, float] = {}
                link_vintages = vintage_root.get("Link", {}).get(link_name) if isinstance(vintage_root.get("Link"), dict) else None
                if link_vintages and cc > 0:
                    try:
                        ini = float(link_vintages.get("initial_capacity") or 0)
                    except (TypeError, ValueError):
                        ini = 0.0
                    if _math.isfinite(ini) and ini > 0:
                        t, pp = _capex_commitment(cc, ini, row.get("build_year"), lt_val)
                        capex_eur_total += t
                        for k, v in pp.items():
                            capex_eur_pp[k] = capex_eur_pp.get(k, 0.0) + v
                    for entry in link_vintages.get("periods") or []:
                        try:
                            opt = float(entry.get("p_nom_opt") or 0)
                        except (TypeError, ValueError):
                            continue
                        if not _math.isfinite(opt) or opt <= 1e-9:
                            continue
                        t, pp = _capex_commitment(cc, opt, entry.get("build_year"), lt_val)
                        capex_eur_total += t
                        for k, v in pp.items():
                            capex_eur_pp[k] = capex_eur_pp.get(k, 0.0) + v
                elif cc > 0:
                    t, pp = _capex_commitment(cc, p_nom_opt, row.get("build_year"), lt_val)
                    capex_eur_total = t
                    capex_eur_pp = pp

                # LCOH = (CAPEX + OPEX + input_cost) / output_MWh
                total_cost_eur = capex_eur_total + opex_eur_total + input_eur_total
                lcoh_total = total_cost_eur / output_mwh_total if output_mwh_total > 1e-9 else 0.0
                lcoh_pp: dict[str, float] = {}
                for p_key, out_mwh in output_mwh_pp.items():
                    cx = capex_eur_pp.get(p_key, 0.0)
                    ox = opex_eur_pp.get(p_key, 0.0)
                    ix = input_eur_pp.get(p_key, 0.0)
                    lcoh_pp[p_key] = (cx + ox + ix) / out_mwh if out_mwh > 1e-9 else 0.0

                carrier_str = None
                try:
                    raw_c = row.get("carrier")
                    if raw_c not in (None, ""):
                        carrier_str = str(raw_c)
                except Exception:
                    pass

                per_asset_lcoh.append(AssetLCOHEntry(
                    name=str(link_name),
                    carrier=carrier_str,
                    p_nom_opt=p_nom_opt,
                    capex_meur=CarrierPeriodValue(
                        total=capex_eur_total / 1e6,
                        by_period={k: v / 1e6 for k, v in capex_eur_pp.items()},
                    ),
                    opex_meur=CarrierPeriodValue(
                        total=opex_eur_total / 1e6,
                        by_period={k: v / 1e6 for k, v in opex_eur_pp.items()},
                    ),
                    input_cost_meur=CarrierPeriodValue(
                        total=input_eur_total / 1e6,
                        by_period={k: v / 1e6 for k, v in input_eur_pp.items()},
                    ),
                    output_gwh=CarrierPeriodValue(
                        total=output_mwh_total / 1000.0,
                        by_period={k: v / 1000.0 for k, v in output_mwh_pp.items()},
                    ),
                    lcoh_eur_per_mwh=CarrierPeriodValue(total=lcoh_total, by_period=lcoh_pp),
                ))

        # Cheapest LCOH first — surface the most competitive Link on top.
        per_asset_lcoh.sort(key=lambda e: e.lcoh_eur_per_mwh.total)

    return EconomicsComparison(by_carrier=out, per_asset_lcoh=per_asset_lcoh)

"""
Lifted from `routers.results` (get_lcoh).

The handler keeps the network lookup, the `_dispatch_ready` gate and every
`_state` read; this module gets the arithmetic and returns the payload, or
`None` where the endpoint answers 204. Result frames arrive through the
injected `result_df` callable where one is needed, so this runs on any
network with no router state — see `tests/test_results_seam.py`.

pandas / numpy / math are imported locally inside each function, the pattern
the router already used, so they are intentionally absent from this header.
"""
from __future__ import annotations

from services.period_utils import (
    is_multi_period,
    period_years_map,
    snapshot_weights,
    years_for_period,
)
from services.results.load_frames import corrected_marginal_prices



def compute_lcoh(n, cfg, *, result_df):
    """
    Levelised cost of hydrogen per electrolyser Link.

    Lifted from `routers.results.get_lcoh`, which keeps the network
    lookup, the `_dispatch_ready` gate and the `_state` reads. Returns the
    payload dict, or `None` where the handler returns 204.
    """
    import math

    import pandas as _pd
    from services.solver_service import with_periodized_cost_defaults


    links_df = n.links
    if links_df.empty:
        return {"rows": [], "total": None, "currency": "EUR"}

    # Filter to electrolyser-like links by carrier substring. Matches the
    # frontend's `isElectrolyzerCarrier` token set so the two views agree.
    tokens = [
        "electrol", "p2g", "p2h2", "power-to-h2", "power-to-gas",
        "power2gas", "hydrogen", "h2",
    ]
    def _is_electrolyzer(c) -> bool:
        s = str(c or "").strip().lower()
        return any(t in s for t in tokens)

    if "carrier" not in links_df.columns:
        return {"rows": [], "total": None, "currency": "EUR"}
    candidate_names = [name for name in links_df.index if _is_electrolyzer(links_df.at[name, "carrier"])]
    if not candidate_names:
        return {"rows": [], "total": None, "currency": "EUR"}

    # Snapshot weight × period years — same weighting basis as cost_breakdown.
    sns = n.snapshots
    is_multi = is_multi_period(n)
    weights = snapshot_weights(n, "objective")

    # ENERGY weighting for the H₂-produced denominator follows the `generators`
    # column (matching PyPSA n.statistics() and the Dispatch tab); the cost
    # terms (VOM, electricity input) keep the `objective` weighting above.
    # Identical when the two columns coincide (the common case); they diverge
    # only under representative-week weighting. `snapshot_weights` applies the
    # same generators→objective→1.0 fallback + investment-period years scaling.
    # (The router called `routers.compare._build_snapshot_weights` here through
    # a lazy import to dodge an import cycle; that helper is a documented thin
    # wrapper over exactly this call, and a service must not import a router.)
    energy_weights = snapshot_weights(n, "generators")

    try:
        with with_periodized_cost_defaults(n, cfg):
            cap_costs = n.c["Link"].capital_cost
            if not isinstance(cap_costs, _pd.Series):
                cap_costs = _pd.Series(cap_costs, index=links_df.index)
    except Exception:
        cap_costs = links_df.get("capital_cost", _pd.Series(0.0, index=links_df.index))

    # bus0 marginal prices for the electricity-cost term. Use the merit-order
    # SUBSIDY-REMOVED duals (the same correction asset_economics and the Compare
    # per-carrier economics apply via the shared helper) — NOT raw
    # n.buses_t.marginal_price. Under a curtailment_cost subsidy the raw bus
    # dual goes negative wherever a subsidised renewable sets the price, which
    # makes the electrolyser's electricity input cost negative (unphysical — no
    # money flows; it's an LP-accounting artefact) and understates LCOH.
    # corrected_marginal_prices restores the real price at those buses/snapshots.
    try:
        bus_prices = corrected_marginal_prices(n, result_df=result_df)
    except Exception:
        try:
            bus_prices = n.buses_t.marginal_price
        except Exception:
            bus_prices = None

    p0 = getattr(n.links_t, "p0", None)
    if p0 is None or p0.empty:
        return {"rows": [], "total": None, "currency": "EUR"}

    rows: list[dict] = []
    # Fleet accumulators for the aggregated LCOH at the bottom.
    # `fleet_capex_per_year` is the sum of annualised €/yr CAPEX across
    # links (the value surfaced in the response for backwards display
    # compatibility). `fleet_capex_total` is the horizon-total CAPEX used
    # in the LCOH numerator — see total_years_factor comment below.
    fleet_capex_per_year = 0.0
    fleet_capex_total = 0.0
    fleet_vom = 0.0
    fleet_elec = 0.0
    fleet_h2 = 0.0
    # Fleet per-period accumulators. Only populated when the network is
    # multi-period; lets the frontend Economics tab switch between the
    # horizon-aggregate LCOH (when "Aggregated" is selected) and the
    # period-scoped LCOH for any specific year.
    fleet_by_period: dict[int, dict[str, float]] = {}

    # Helpers for per-period slicing — Multi-period MultiIndex → list of
    # period years; flat → empty (no per-period view).
    if is_multi:
        try:
            unique_periods = sorted({int(p) for p in sns.get_level_values(0)})
            period_level = sns.get_level_values(0)
            _years_map = period_years_map(n)
            period_year_factor = {
                int(p): years_for_period(_years_map, p) for p in unique_periods
            }
        except Exception:
            unique_periods = []
            period_level = None
            period_year_factor = {}
    else:
        unique_periods = []
        period_level = None
        period_year_factor = {}

    # Horizon-total years multiplier. Per-row OPEX, electricity, and H₂
    # totals are already weighted by `weights = sw * years_s` (line ~1567),
    # so they accumulate ACROSS investment periods (e.g. multi-period run
    # over [2030(years=5), 2040(years=10)] yields totals that span 15
    # operational years). CAPEX is computed as `capital_cost × p_nom_opt`
    # which is PyPSA's ANNUALISED value (€/yr). To keep the LCOH numerator
    # consistent — apples-to-apples integration across the same horizon —
    # scale CAPEX by the same total-years factor before mixing it with
    # OPEX/elec. Without this, a 15-year-horizon LCOH would have ~1× CAPEX
    # divided by ~15× H₂ and silently understate by an order of magnitude.
    # Flat networks (no investment periods) get factor=1.0, preserving
    # the legacy per-row formula `1-yr CAPEX + 1-yr OPEX`.
    total_years_factor = float(sum(period_year_factor.values())) if is_multi and period_year_factor else 1.0

    for name in candidate_names:
        try:
            p_nom_opt = float(links_df.at[name, "p_nom_opt"]) if "p_nom_opt" in links_df.columns else float(links_df.at[name, "p_nom"])
        except (TypeError, ValueError, KeyError):
            continue
        if not math.isfinite(p_nom_opt) or p_nom_opt <= 0:
            continue
        try:
            cc = float(cap_costs.at[name]) if name in cap_costs.index else 0.0
        except Exception:
            cc = 0.0
        if not math.isfinite(cc) or cc < 0:
            cc = 0.0
        capex_eur_per_year = cc * p_nom_opt

        try:
            eff = float(links_df.at[name, "efficiency"])
        except (TypeError, ValueError, KeyError):
            eff = 1.0
        if not math.isfinite(eff) or eff <= 0:
            eff = 1.0

        try:
            mc = float(links_df.at[name, "marginal_cost"])
        except (TypeError, ValueError, KeyError):
            mc = 0.0

        if name not in p0.columns:
            continue
        try:
            disp = p0[name].reindex(sns).fillna(0.0).astype(float)
        except Exception:
            continue
        # Only count the CONSUMING direction (positive p0). A reverse-flow
        # snapshot represents the link running in fuel-cell mode and isn't
        # part of H2 production cost.
        consume = disp.clip(lower=0)
        weighted_consume = consume * energy_weights
        consume_mwh = float(weighted_consume.sum())
        if consume_mwh <= 0:
            # Link wasn't dispatched as a consumer during this run.
            rows.append({
                "name": name,
                "carrier": str(links_df.at[name, "carrier"] or ""),
                "p_nom_opt_mw": p_nom_opt,
                "efficiency": eff,
                "capex_eur_per_year": capex_eur_per_year,
                "vom_cost_eur": 0.0,
                "electricity_cost_eur": 0.0,
                "h2_produced_mwh": 0.0,
                "lcoh_eur_per_mwh_h2": None,
                "lcoh_eur_per_kg_h2": None,
            })
            continue

        # Variable OPEX (€): Σ |p0| × marginal_cost × weight. Use |p0| so
        # reverse-flow snapshots still attract their marginal cost (PyPSA's
        # convention).
        vom_cost = float((disp.abs() * mc * weights).sum()) if mc > 0 else 0.0

        # Electricity input cost: bus0 marginal price × consume_t × weight.
        bus0 = links_df.at[name, "bus0"] if "bus0" in links_df.columns else None
        elec_cost = 0.0
        if bus_prices is not None and bus0 is not None and bus0 in bus_prices.columns:
            try:
                bp = bus_prices[bus0].reindex(sns).fillna(0.0).astype(float)
                elec_cost = float((consume * bp * weights).sum())
            except Exception:
                elec_cost = 0.0

        # H2 produced (MWh_H2): consume × efficiency × weight.
        h2_produced_mwh = consume_mwh * eff

        # Horizon-total CAPEX for the LCOH numerator — annualised €/yr × total
        # operational years across all investment periods. Keeps the
        # ratio's units consistent (€ / MWh_H2) regardless of horizon length.
        capex_total_eur = capex_eur_per_year * total_years_factor

        total_cost = capex_total_eur + vom_cost + elec_cost
        lcoh_eur_per_mwh = total_cost / h2_produced_mwh if h2_produced_mwh > 0 else None
        # Convert €/MWh_H2 → €/kg using LHV: 33.33 MWh / kg
        # (1 kg H2 = 33.33 kWh = 0.03333 MWh, so €/MWh × 0.03333 → €/kg)
        lcoh_eur_per_kg = lcoh_eur_per_mwh * 0.03333 if lcoh_eur_per_mwh is not None else None

        # Per-period breakdown for the Economics tab's period selector. For
        # each investment period:
        #   - h2[P]  = Σ consume × weight over snapshots in P
        #   - elec[P] = Σ consume × bus0_price × weight over snapshots in P
        #   - vom[P] = Σ |p0| × mc × weight over snapshots in P
        #   - capex[P] = annuitised cc × p_nom_opt × ipw.years[P]
        #     (the asset's annual cost allocated to this period's year-span)
        # All cost contributions in € for the period; LCOH[P] = sum / h2[P].
        per_period_rows: list[dict] = []
        if is_multi and unique_periods and period_level is not None:
            for p in unique_periods:
                mask = period_level == p
                try:
                    consume_p = consume[mask]
                    weights_p = weights[mask]
                    energy_weights_p = energy_weights[mask]
                except Exception:
                    continue
                # H₂ (energy) on the generators basis; VOM/elec (cost) on objective.
                weighted_consume_p = consume_p * energy_weights_p
                h2_p_mwh = float(weighted_consume_p.sum()) * eff
                vom_p = float((disp[mask].abs() * mc * weights_p).sum()) if mc > 0 else 0.0
                elec_p = 0.0
                if bus_prices is not None and bus0 is not None and bus0 in bus_prices.columns:
                    try:
                        bp_p = bus_prices[bus0][mask].reindex(consume_p.index).fillna(0.0).astype(float)
                        elec_p = float((consume_p * bp_p * weights_p).sum())
                    except Exception:
                        elec_p = 0.0
                capex_p = capex_eur_per_year * period_year_factor.get(int(p), 1.0)
                tot_p = capex_p + vom_p + elec_p
                lcoh_p = tot_p / h2_p_mwh if h2_p_mwh > 0 else None
                lcoh_p_kg = lcoh_p * 0.03333 if lcoh_p is not None else None
                per_period_rows.append({
                    "period": int(p),
                    "h2_produced_mwh": h2_p_mwh,
                    "capex_eur": capex_p,
                    "vom_cost_eur": vom_p,
                    "electricity_cost_eur": elec_p,
                    "lcoh_eur_per_mwh_h2": lcoh_p,
                    "lcoh_eur_per_kg_h2": lcoh_p_kg,
                })
                # Roll into fleet per-period totals.
                fb = fleet_by_period.setdefault(int(p), {
                    "h2_produced_mwh": 0.0, "capex_eur": 0.0,
                    "vom_cost_eur": 0.0, "electricity_cost_eur": 0.0,
                })
                fb["h2_produced_mwh"] += h2_p_mwh
                fb["capex_eur"] += capex_p
                fb["vom_cost_eur"] += vom_p
                fb["electricity_cost_eur"] += elec_p

        rows.append({
            "name": name,
            "carrier": str(links_df.at[name, "carrier"] or ""),
            "p_nom_opt_mw": p_nom_opt,
            "efficiency": eff,
            "capex_eur_per_year": capex_eur_per_year,
            "vom_cost_eur": vom_cost,
            "electricity_cost_eur": elec_cost,
            "h2_produced_mwh": h2_produced_mwh,
            "lcoh_eur_per_mwh_h2": lcoh_eur_per_mwh,
            "lcoh_eur_per_kg_h2": lcoh_eur_per_kg,
            "by_period": per_period_rows,
        })
        # Two CAPEX accumulators: annualised €/yr for display, horizon-total
        # for the LCOH numerator (apples-to-apples with OPEX/elec/H₂ totals).
        fleet_capex_per_year += capex_eur_per_year
        fleet_capex_total += capex_total_eur
        fleet_vom += vom_cost
        fleet_elec += elec_cost
        fleet_h2 += h2_produced_mwh

    rows.sort(key=lambda r: -(r["p_nom_opt_mw"] or 0))

    total = None
    if fleet_h2 > 0:
        fleet_total_cost = fleet_capex_total + fleet_vom + fleet_elec
        fleet_lcoh = fleet_total_cost / fleet_h2
        # Per-period fleet LCOH — same shape as the per-row by_period array.
        fleet_by_period_list = []
        for p in sorted(fleet_by_period.keys()):
            fb = fleet_by_period[p]
            tot_p = fb["capex_eur"] + fb["vom_cost_eur"] + fb["electricity_cost_eur"]
            lcoh_p = tot_p / fb["h2_produced_mwh"] if fb["h2_produced_mwh"] > 0 else None
            fleet_by_period_list.append({
                "period": p,
                "h2_produced_mwh": fb["h2_produced_mwh"],
                "capex_eur": fb["capex_eur"],
                "vom_cost_eur": fb["vom_cost_eur"],
                "electricity_cost_eur": fb["electricity_cost_eur"],
                "lcoh_eur_per_mwh_h2": lcoh_p,
                "lcoh_eur_per_kg_h2": lcoh_p * 0.03333 if lcoh_p is not None else None,
            })
        total = {
            "h2_produced_mwh": fleet_h2,
            "capex_eur_per_year": fleet_capex_per_year,
            "vom_cost_eur": fleet_vom,
            "electricity_cost_eur": fleet_elec,
            "lcoh_eur_per_mwh_h2": fleet_lcoh,
            "lcoh_eur_per_kg_h2": fleet_lcoh * 0.03333,
            "by_period": fleet_by_period_list,
        }
    return {"rows": rows, "total": total, "currency": "EUR"}

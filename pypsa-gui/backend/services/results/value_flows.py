"""
`/results/value_flows`: the participants' ledger of the last solve (Edge
Investment Case P3; spec §7).

Plan: docs/superpowers/plans/2026-09-29-edge-investment-case-p3.md WP3.1 (this
part: `ledger_inputs`, the network-level builder the commercial services leave
to the results layer) and WP3.4 (the payload).

`ledger_inputs(n, cfg, *, result_df, lost_load=None)` reads, per modelled
period and unweighted (the `cost_rows` basis):

  * the bill — `bill_site`'s `per_item_sampled` (the months the dispatch
    covers, the convention the LP rows share), its tariff items and the
    RetailContract's retailer;
  * the settlement — `results.billing.settlement_lines` (the P2 lines);
  * the connection fees — the committed `ic_connection_fee` (× `p_nom_opt`, the
    `network_capacity` cost row) and `ic_connection_fixed_fee` (not an LP term);
  * the export-price revenue — Σ w·p0(export_link)·`ic_export_price` — and, for
    `export_revenue_to="asset_owner"`, its per-interval split (and the tariff's
    export-revenue items') by site-side generation;
  * the assets — `n.statistics.capex / fom / opex(groupby=False)` inside the
    same `with_periodized_cost_defaults(for_back_calculation=True)` fill
    `cost_breakdown` uses, so the ledger re-adds to it exactly; each asset
    classed site / grid / unclassified by `participants.classify_buses`;
  * the reconciliation terms — `cost_breakdown`'s total per period (÷ years on
    a multi-period network) and Σ `commercial_cost_terms` items;
  * disclosures, not money lines — the DSR slack cost (`buses_t["ic_dsr_p"]` ×
    `dsr_price_eur_per_mwh`) and VoLL shedding (the lost-load capture).

Imports no router.
"""
from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any, Callable

import numpy as np
import pandas as pd

from services.commercial import lp_bindings as _lp
from services.commercial import participants as P


def _key(period) -> str:
    return "_" if period is None else str(int(period))


def _periods(n) -> list:
    if isinstance(n.snapshots, pd.MultiIndex):
        return [int(p) for p in n.snapshots.get_level_values(0).unique()]
    return [None]


def _period_mask(n, p) -> np.ndarray:
    if p is None:
        return np.ones(len(n.snapshots), dtype=bool)
    return np.asarray(n.snapshots.get_level_values(0)) == p


def _fin(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _per_asset(frame_or_series, periods) -> dict[tuple[str, str], dict[str, float]]:
    """`n.statistics.*(groupby=False)` → {(component, name): {period key: value}}.

    NaN reads as 0 on purpose: `n.statistics` returns NaN for an asset with no
    cost of that kind (no capital cost, no marginal cost, inactive in the
    period), and `cost_breakdown` reads it the same way (`_safe_float`), so the
    ledger re-adds to it. It is not an unknown amount."""
    out: dict[tuple[str, str], dict[str, float]] = {}
    if frame_or_series is None or len(frame_or_series) == 0:
        return out
    if isinstance(frame_or_series, pd.DataFrame):
        for idx, row in frame_or_series.iterrows():
            comp, name = str(idx[0]), str(idx[-1])
            for col, v in row.items():
                f = _fin(v) or 0.0
                out.setdefault((comp, name), {})[_key(col)] = f
        return out
    for idx, v in frame_or_series.items():
        comp, name = str(idx[0]), str(idx[-1])
        out.setdefault((comp, name), {})[_key(None)] = _fin(v) or 0.0
    return out


def _asset_costs(n, cfg, parsed, sides) -> list[P.AssetCost]:
    from services.solver_service import with_periodized_cost_defaults

    periods = [_key(p) for p in _periods(n)]
    with with_periodized_cost_defaults(n, cfg, for_back_calculation=True):
        capex = _per_asset(n.statistics.capex(groupby=False), periods)
        fom = _per_asset(n.statistics.fom(groupby=False), periods)
        opex = _per_asset(n.statistics.opex(groupby=False), periods)
    out = []
    # The meter's site-bus carrier: a site-side Generator on a bus of another
    # carrier (gas behind a CHP Link) is a fuel supply, its opex a purchase.
    poc_bus = str(n.links.at[parsed.poc_link, "bus1"]) if parsed.poc_link in n.links.index \
        else None
    carrier = (lambda b: str(n.buses.at[b, "carrier"]) if b in n.buses.index and
               "carrier" in n.buses.columns else "")
    site_carrier = carrier(poc_bus) if poc_bus else ""
    for key in sorted(set(capex) | set(fom) | set(opex)):
        comp, name = key
        side, flags = P.asset_side(n, comp, name, sides)
        if comp == "Generator" and side == "site" and name in n.generators.index:
            bus_carrier = carrier(str(n.generators.at[name, "bus"]))
            if bus_carrier and site_carrier and bus_carrier != site_carrier:
                flags = [*flags, "fuel_supply_generator"]
        out.append(P.AssetCost(component=comp, name=name, side=side, flags=flags,
                               capex=capex.get(key, {}), fom=fom.get(key, {}),
                               opex=opex.get(key, {})))
    return out


def _years_of(n) -> Callable[[Any], float]:
    from services.period_utils import period_years_map, years_for_period

    ymap = period_years_map(n)
    return lambda p: years_for_period(ymap, p)


def _cost_breakdown_total(n, cb) -> dict[str, float | None]:
    """`cost_breakdown`'s total per period on the unweighted basis: the flat
    total, or each period's `by_period` entry ÷ its years."""
    if cb is None:
        return {_key(p): None for p in _periods(n)}
    if not isinstance(n.snapshots, pd.MultiIndex):
        return {"_": _fin(cb.get("total"))}
    years = _years_of(n)
    out = {}
    for entry in cb.get("by_period") or []:
        y, total = years(entry["period"]), _fin(entry.get("total"))
        out[_key(entry["period"])] = (total / y) if (y and total is not None) else None
    return out


def _connection(n, commercial) -> tuple[dict, dict]:
    from services.commercial import connection as _conn
    from services.commercial.cost_rows import commercial_cost_terms

    fee: dict[str, float | None] = {}
    if n.meta.get(_conn.META_FEE):
        items = commercial_cost_terms(n, commercial)["items"]
        rows = [(p, cx + ox) for label, p, cx, ox in items if label == "network_capacity"]
        if rows:
            for p, v in rows:
                fee[_key(p)] = fee.get(_key(p), 0.0) + v
        else:
            fee = {_key(p): None for p in _periods(n)}   # committed, not established
    fixed: dict[str, float | None] = {}
    rec = n.meta.get(_conn.META_FIXED_FEE)
    if rec:
        by_p = rec.get("eur_by_period") or {"_": rec.get("eur")}
        fixed = {("_" if k == "_" else str(int(k))): _fin(v) for k, v in by_p.items()}
    return fee, fixed


def _lp_commercial(n, commercial) -> dict[str, float | None]:
    from services.commercial.cost_rows import commercial_cost_terms

    out = {_key(p): 0.0 for p in _periods(n)}
    terms = commercial_cost_terms(n, commercial)
    for _label, p, cx, ox in terms["items"]:
        out[_key(p)] = out.get(_key(p), 0.0) + cx + ox
    return out


def _export_revenue(n, parsed) -> tuple[dict[str, float | None], dict | None]:
    """(revenue per period, the per-interval revenue per period) — only when the
    config prices export (`export_price_ref` on an export Link)."""
    if not (parsed.export_link and parsed.export_price_ref is not None):
        return {}, None
    store = n.links_t.get(_lp.EXPORT_PRICE_ATTR) if hasattr(n.links_t, "get") else None
    p0 = getattr(n.links_t, "p0", None)
    link = parsed.export_link
    if store is None or link not in getattr(store, "columns", []) or p0 is None or \
            link not in p0.columns:
        return {_key(p): None for p in _periods(n)}, None
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)
    price = store[link].reindex(n.snapshots).to_numpy(dtype=float)
    flow = p0[link].reindex(n.snapshots).to_numpy(dtype=float)
    per_interval = w * flow * price
    out, intervals = {}, {}
    for p in _periods(n):
        m = _period_mask(n, p)
        vals = per_interval[m]
        out[_key(p)] = None if np.isnan(vals).any() else float(vals.sum())
        intervals[_key(p)] = vals
    return out, intervals


def _export_split(n, parsed, sides, bill, export_intervals, flags: list[str]) -> dict | None:
    """Per period and source, the export revenue split pro rata to each site-side
    generator's output per interval (key (component, name)); intervals with no
    site generation go to key None."""
    gens = [g for g in n.generators.index if str(n.generators.at[g, "bus"]) in sides.site]
    gp = n.generators_t.p.reindex(columns=gens).to_numpy(dtype=float) if gens else None
    rev_items = [it.id for it in (parsed.import_tariff.items if parsed.import_tariff else [])
                 if it.kind == "energy" and (it.direction == "revenue"
                                             or it.measured_on == "export")]
    out: dict[str, dict] = {}
    for p in _periods(n):
        m = _period_mask(n, p)
        k = _key(p)
        sources: dict[str, np.ndarray] = {}
        if export_intervals and export_intervals.get(k) is not None:
            sources["export_price"] = export_intervals[k]
        res = (bill.per_period.get(p) if bill is not None else None)
        if res is not None:
            for item in rev_items:
                amounts = res.lines.loc[res.lines["tariff_item"] == item, "amount"] \
                    .to_numpy(dtype=float)
                if len(amounts) == int(m.sum()):
                    sources[item] = amounts
                else:
                    # The bill's lines are in dispatch-row order per item; a
                    # different length cannot be aligned (never guessed).
                    flags.append(f"export_split_not_established:{item}:{k}")
        split = {}
        for sid, vals in sources.items():
            if np.isnan(vals).any():
                flags.append(f"export_split_not_established:{sid}:{k}")
                continue
            parts: dict = {}
            if gp is not None:
                g = np.clip(gp[m], 0.0, None)
                tot = g.sum(axis=1)
                has = tot > 0
                share = np.where(has[:, None], g / np.where(has, tot, 1.0)[:, None], 0.0)
                for j, name in enumerate(gens):
                    v = float((vals * share[:, j]).sum())
                    if v:
                        parts[("Generator", str(name))] = v
                rest = float(vals[~has].sum())
            else:
                rest = float(vals.sum())
            if rest:
                parts[None] = rest
            split[sid] = parts
        out[k] = split
    return out


def _disclosures(n, cfg, lost_load) -> dict[str, dict[str, float | None]]:
    from services.commercial import settlement_inputs as _SI

    w = n.snapshot_weightings.objective.to_numpy(dtype=float)
    dsr_price = float(getattr(cfg, "dsr_price_eur_per_mwh", 0.0) or 0.0)
    frame = n.buses_t.get(_SI.DSR_ATTR) if hasattr(n.buses_t, "get") else None
    shed = (lost_load or {}).get("lost_load_t") if isinstance(lost_load, dict) else None
    voll = _fin((lost_load or {}).get("voll_eur_per_mwh")) if isinstance(lost_load, dict) \
        else None
    out = {}
    for p in _periods(n):
        m = _period_mask(n, p)
        d: dict[str, float | None] = {"dsr_slack": 0.0, "voll": 0.0}
        if frame is None and dsr_price and getattr(cfg, "dsr_buses", None):
            d["dsr_slack"] = None               # DSR configured, no committed record
        if frame is not None and not frame.empty:
            vals = frame.reindex(n.snapshots).to_numpy(dtype=float)[m]
            d["dsr_slack"] = None if np.isnan(vals).any() else \
                float((w[m][:, None] * vals).sum() * dsr_price)
        if shed is not None and not getattr(shed, "empty", True):
            vals = shed.reindex(n.snapshots).fillna(0.0).to_numpy(dtype=float)[m]
            d["voll"] = None if voll is None else float((w[m][:, None] * vals).sum() * voll)
        out[_key(p)] = d
    return out


def ledger_inputs(n, cfg, *, result_df, lost_load=None) -> P.LedgerInputs | None:
    """Everything the ledger reads, from the solved network (None without a
    commercial config or before a solve)."""
    from services.commercial import billing as _billing
    from services.results.billing import _retail, _solved, settlement_lines
    from services.results.physical_quantities import physical_quantities

    commercial = getattr(cfg, "commercial", None)
    if not commercial or not _solved(n):
        return None
    parsed = _lp._parse(commercial)
    sides = P.classify_buses(n, parsed)
    periods = [_key(p) for p in _periods(n)]

    bill = _billing.bill_site(n, commercial) if parsed.import_tariff is not None else None
    items = {it.id: P.BillItem(it.id, it.kind, it.measured_on, it.direction)
             for it in (parsed.import_tariff.items if parsed.import_tariff else [])}
    input_flags: list[str] = list(bill.flags) if bill is not None else []
    per_item: dict[str, dict] = {}
    bill_flags: dict[str, list[str]] = {}
    for p in _periods(n):
        res = bill.per_period.get(p) if bill is not None else None
        k = _key(p)
        if res is None:
            per_item[k] = {i: None for i in items}
            bill_flags[k] = ["bill_not_established"] if bill is not None else []
            if bill is not None:
                input_flags.append(f"period_not_billed:{k}")
            continue
        per_item[k] = {i: _fin(res.per_item_sampled.get(i)) for i in items}
        bill_flags[k] = [f"{i}:{f}" for i, fl in res.flags.items() for f in fl]
        # A partial URDB import is a bill missing charges (ADR-0001).
        input_flags += [f for f in res.flags.get("_tariff", [])]
    retail, retail_flags = _retail(parsed)
    retailers = {r[1] for r in retail.values() if r}
    retailer = next(iter(retailers)) if len(retailers) == 1 else None

    pq = physical_quantities(n, cfg, result_df=result_df)
    pq["result_df"] = result_df
    lines, contract_flags = settlement_lines(n, parsed, pq)
    settlement = [{**asdict(ln), "period": _key(ln.period)} for ln in lines]
    unsettled = []
    for f in [*contract_flags, *retail_flags]:
        if f.startswith("contract_not_settled:"):
            cid, _, reason = f[len("contract_not_settled:"):].partition(":")
            unsettled.append((cid, reason))
    from services.commercial.cost_rows import commercial_cost_terms

    input_flags += [*contract_flags, *retail_flags,
                    *commercial_cost_terms(n, commercial)["flags"]]

    fee, fixed = _connection(n, commercial)
    export_revenue, export_intervals = _export_revenue(n, parsed)
    vf = P.parse_value_flows(parsed.value_flows)
    split = (_export_split(n, parsed, sides, bill, export_intervals, input_flags)
             if vf is not None and vf.export_revenue_to == "asset_owner" else None)
    agreement = parsed.connection
    external_ppa = [c.id for c in parsed.contracts
                    if c.type == "ppa" and vf is not None
                    and any(P.same_party(c.seller, e) for e in vf.externals)
                    and any(a in n.generators.index for a in c.asset_ids)]
    from services.results.cost_breakdown import compute_cost_breakdown

    cb = compute_cost_breakdown(n, cfg)
    return P.LedgerInputs(
        periods=periods, site_party=parsed.site_party, bill_items=items, bill=per_item,
        bill_flags=bill_flags, retailer=retailer, settlement=settlement,
        connection_fee=fee, connection_fixed_fee=fixed,
        curtailment_compensation=bool(
            agreement is not None
            and agreement.curtailment_compensation_eur_per_mwh is not None),
        export_revenue=export_revenue, export_split=split,
        assets=_asset_costs(n, cfg, parsed, sides),
        cost_breakdown_total=_cost_breakdown_total(n, cb),
        lp_commercial=_lp_commercial(n, commercial),
        disclosures=_disclosures(n, cfg, lost_load),
        input_flags=sorted(set(input_flags)), unsettled_contracts=unsettled,
        curtailment_penalty=_fin((cb or {}).get("curtailment_cost")),
        external_ppa_assets=external_ppa)


def value_flow_ledger(n, cfg, *, result_df, lost_load=None):
    """(inputs, value-flow config, ledger, conservation), or None when there is
    nothing to build: no commercial config, no solve, or no `value_flows`.
    Raises `participants.ValueFlowsInvalid` for a stored value that does not
    validate (the payload layer answers `value_flows_invalid`)."""
    commercial = getattr(cfg, "commercial", None)
    if not commercial:
        return None
    vf = P.parse_value_flows(_lp._parse(commercial).value_flows)
    if vf is None:
        return None
    inputs = ledger_inputs(n, cfg, result_df=result_df, lost_load=lost_load)
    if inputs is None:
        return None
    ledger = P.build_ledger(inputs, vf)
    return inputs, vf, ledger, P.check_conservation(ledger, inputs, vf)

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

import logging
import math
from dataclasses import asdict
from typing import Any, Callable

import numpy as np
import pandas as pd

from services.commercial import lp_bindings as _lp
from services.commercial import participants as P

_log = logging.getLogger(__name__)


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


# Kept here for callers; defined beside the classifier (WP3.2 moved it).
is_fuel_supply = P.is_fuel_supply


def _asset_costs(n, cfg, parsed, sides) -> list[P.AssetCost]:
    from services.solver_service import with_periodized_cost_defaults

    periods = [_key(p) for p in _periods(n)]
    with with_periodized_cost_defaults(n, cfg, for_back_calculation=True):
        capex = _per_asset(n.statistics.capex(groupby=False), periods)
        fom = _per_asset(n.statistics.fom(groupby=False), periods)
        opex = _per_asset(n.statistics.opex(groupby=False), periods)
    out = []
    for key in sorted(set(capex) | set(fom) | set(opex)):
        comp, name = key
        side, flags = P.asset_side(n, comp, name, sides)
        if comp == "Generator" and side == "site" and is_fuel_supply(n, parsed, name):
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
    generator's ELECTRIC output per interval (key (component, name)); intervals
    with no site generation go to key None. Generation is one definition with
    the `as_consumed_btm` PPA's share (IC P3 gate, condition 2): the site's
    Generators on electric buses (`lp_bindings.site_generators`) and what its
    converting Links deliver to electric buses from every port
    (`site_link_generation`, a CHP's power, not its heat). Storage discharge
    is not generation: its export stays the site's. A generation value that is
    not known (NaN) in a period makes that period's split not established —
    never a silent share for the site (ADR-0001)."""
    from services.commercial import lp_bindings as _lp

    gens = _lp.site_generators(n, parsed)
    links = _lp.site_link_generation(n, parsed, lambda k: getattr(n.links_t, f"p{k}", None))
    keys = [("Generator", g) for g in gens] + [("Link", str(k)) for k in links.columns]
    cols = []
    if gens:
        cols.append(n.generators_t.p.reindex(index=n.snapshots, columns=gens)
                    .to_numpy(dtype=float))
    if not links.empty:
        cols.append(links.to_numpy(dtype=float))
    gp = np.hstack(cols) if cols else None
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
        gen_unknown = gp is not None and bool(np.isnan(gp[m]).any())
        for sid, vals in sources.items():
            if np.isnan(vals).any() or gen_unknown:
                flags.append(f"export_split_not_established:{sid}:{k}")
                continue
            parts: dict = {}
            if gp is not None:
                g = np.clip(gp[m], 0.0, None)
                tot = g.sum(axis=1)
                has = tot > 0
                share = np.where(has[:, None], g / np.where(has, tot, 1.0)[:, None], 0.0)
                for j, key in enumerate(keys):
                    v = float((vals * share[:, j]).sum())
                    if v:
                        parts[key] = v
                rest = float(vals[~has].sum())
            else:
                rest = float(vals.sum())
            if rest:
                parts[None] = rest
            split[sid] = parts
        out[k] = split
    return out


def _hub_inputs(n, parsed, vf, bill) -> P.HubInputs | None:
    """An energy hub's allocation inputs (WP3.3a): per period, each group
    member's import on the group bill's own rating arguments (the ones
    `billing.bill_site` passes — step, represented hours, billing period, the
    site clock), rated by `hub_allocation.period_hub`."""
    from services.commercial import billing as _billing
    from services.commercial import hub_allocation as _HA

    if vf is None or vf.allocation is None or not vf.hub_members:
        return None
    # No group left (the contract cleared after the hub was saved): stale, so
    # the ledger refuses the split — never a silent "no allocation" (WP3.3a
    # review round 2 R2-1).
    by_link = {m.link: m.participant for m in vf.hub_members}
    members = [(link, by_link[link]) for link in parsed.group_members if link in by_link]
    items = parsed.import_tariff.items if parsed.import_tariff is not None else []
    hub = P.HubInputs(members=members, periods={}, group_links=list(parsed.group_members),
                      metered_items=[i.id for i in items if _HA.is_metered(i)],
                      peak_items=[i.id for i in items if _HA.is_peak_item(i)])
    if sorted(parsed.group_members) != sorted(by_link):
        return hub                    # stale: the ledger refuses the split (review #1)
    p0 = getattr(n.links_t, "p0", None)
    links = [link for link, _p in members] + ([parsed.export_link] if parsed.export_link else [])
    if p0 is None or any(link not in p0.columns for link in links):
        return hub
    scratch: list[str] = []
    flows = {part: _billing._flow(p0, link, scratch) for link, part in members}
    exp_all = (_billing._flow(p0, parsed.export_link, scratch) if parsed.export_link
               else np.zeros(len(n.snapshots)))
    w_all = n.snapshot_weightings.objective.to_numpy(dtype=float)
    multi = isinstance(n.snapshots, pd.MultiIndex)
    periods: dict[str, P.HubPeriod] = {}
    for p in _periods(n):
        sel = _period_mask(n, p)
        ts = pd.DatetimeIndex(n.snapshots[sel].get_level_values(-1) if multi
                              else n.snapshots[sel])
        idx = ts.tz_localize("UTC") if parsed.timezone and ts.tz is None else ts
        step = _billing._step_hours(ts)
        represents, billing_period = _billing._represented(ts, w_all[sel], step)
        group = bill.per_period.get(p) if bill is not None else None
        try:
            periods[_key(p)] = _HA.period_hub(
                idx, {part: v[sel] for part, v in flows.items()}, exp_all[sel], w_all[sel],
                parsed.import_tariff, step_hours=step, timezone=parsed.timezone,
                billing_period=billing_period, represents_hours=represents, group=group,
                peak=vf.allocation.basis == "peak_contribution")
        except ValueError as exc:                   # the results path never raises
            periods[_key(p)] = P.HubPeriod(
                energy_mwh={part: None for _l, part in members}, metered={}, peak={},
                reason="period_not_rated", flags=[f"period_not_rated:{str(exc)[:120]}"])
    hub.periods = periods
    return hub


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
    unsettled_parties = {}
    by_id = {c.id: c for c in parsed.contracts}
    for f in [*contract_flags, *retail_flags]:
        if f.startswith("contract_not_settled:"):
            cid, _, reason = f[len("contract_not_settled:"):].partition(":")
            unsettled.append((cid, reason))
            if cid in by_id:
                unsettled_parties[cid] = P.contract_payer_payee(by_id[cid], parsed.site_party)
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
        unsettled_parties=unsettled_parties,
        curtailment_penalty=_fin((cb or {}).get("curtailment_cost")),
        external_ppa_assets=external_ppa, hub=_hub_inputs(n, parsed, vf, bill))


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


# ── the payload (WP3.4) ─────────────────────────────────────────────────────


def _sankey(lines: list[P.ValueFlowLine], vf) -> tuple[dict, dict[str, int]]:
    """A bipartite Sankey — payer nodes `p:<id>` on the left, payee nodes
    `r:<id>` on the right — so it is a DAG by construction (recharts' Sankey
    recurses without a visited set and crashes on a cycle). Links aggregate
    lines by (payer, payee, stream); zero and unknown amounts are dropped and
    counted."""
    names = {x.id: x.name for x in vf.participants}
    ids = list(names)

    def canon(party: str) -> str:
        # One node per party however a line spells it (as `by_participant`).
        return P.canonical_party(party, vf)

    agg: dict[tuple[str, str, str], float] = {}
    dropped = {"unknown": 0, "zero": 0}
    for ln in lines:
        if ln.amount is None or ln.payer is None or ln.payee is None:
            dropped["unknown"] += 1
            continue
        if ln.amount == 0:
            dropped["zero"] += 1
            continue
        key = (canon(ln.payer), canon(ln.payee), ln.value_stream)
        agg[key] = agg.get(key, 0.0) + ln.amount
    nodes: dict[str, dict] = {}

    def node(party: str, side: str) -> str:
        nid = f"{'p' if side == 'payer' else 'r'}:{party}"
        internal = any(P.same_party(party, i) for i in ids)
        nodes.setdefault(nid, {"id": nid, "label": names.get(party, party), "side": side,
                               "internal": internal})
        return nid

    links = [{"source": node(payer, "payer"), "target": node(payee, "payee"),
              "value": value, "stream": stream}
             for (payer, payee, stream), value in sorted(agg.items()) if value]
    return {"nodes": list(nodes.values()), "links": links}, dropped


def compute_value_flows(n, cfg, *, result_df, lost_load=None) -> dict | None:
    """`GET /results/value_flows` (plan WP3.4): the participants' ledger of the
    last solve per period — its lines, per-participant totals, a bipartite
    Sankey and the conservation checks. None (the route's 204) without a
    commercial config or before a solve; `status: "not_established"` without a
    value-flows config (a 204 would read as "not solved" in chat);
    `status: "value_flows_invalid"` for a stored value that does not
    validate. Money is unweighted per period (the `cost_rows` basis)."""
    from services.commercial import value_flow_templates as T
    from services.results.billing import _solved

    commercial = getattr(cfg, "commercial", None)
    if not commercial or not _solved(n):
        return None
    parsed = _lp._parse(commercial)
    try:
        vf = P.parse_value_flows(parsed.value_flows)
    except P.ValueFlowsInvalid as exc:
        # The payload is a route's answer: a fixed reason, never the
        # validator's text; the detail goes to the server log.
        _log.warning("stored value_flows does not validate: %s", exc)
        return {"status": "value_flows_invalid",
                "reason": "the stored value_flows does not validate; re-save it "
                          "(PUT /simulation/commercial/value_flows names the field)"}
    if vf is None:
        return {"status": "not_established", "reason": "no_value_flows_config"}
    inputs = ledger_inputs(n, cfg, result_df=result_df, lost_load=lost_load)
    if inputs is None:
        return None
    ledger = P.build_ledger(inputs, vf)
    res = P.check_conservation(ledger, inputs, vf)
    totals = P.by_participant(ledger, vf)
    template = T.template_status(vf, n, parsed)
    flags = sorted({*ledger.flags, *res.flags, *template})
    # The allocation's reasons ride its lines; the payload names them once.
    flags += sorted({f for lines in ledger.periods.values() for ln in lines
                     for f in ln.flags if f.startswith("allocation_not_established:")})
    periods = {}
    for p in inputs.periods:
        lines = ledger.periods.get(p, [])
        sankey, dropped = _sankey(lines, vf)
        for kind, count in dropped.items():
            if count:
                flags.append(f"sankey_dropped_{kind}:{p}:{count}")
        pc = res.periods[p]
        periods[p] = {"lines": [asdict(ln) for ln in lines], "by_participant": totals.get(p, {}),
                      "sankey": sankey, "conservation": {"ok": pc.ok, "checks": pc.checks},
                      "disclosures": ledger.disclosures.get(p, {})}
    return {"status": "ok", "participants": [x.model_dump(mode="json") for x in vf.participants],
            "externals": list(vf.externals), "template": vf.template,
            "template_version": vf.template_version, "periods": periods,
            "conservation_ok": res.ok, "flags": sorted(set(flags)), "notes": list(ledger.notes),
            "provenance": {"tariff_payees": ledger.tariff_payees,
                           "billing": {"input_flags": list(inputs.input_flags)},
                           "template": {"name": vf.template, "version": vf.template_version,
                                        "status": template},
                           "basis": "unweighted_per_period"}}

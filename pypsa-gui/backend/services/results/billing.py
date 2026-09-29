"""
`/results/billing`: the site bill, the contract settlement and the
billing-vs-LP gap of the last solve (Edge Investment Case P2 WP2.5).

`compute_billing(n, cfg, *, state=None, result_df)` is the network-level
driver the commercial services leave to the results layer:

  * the site bill — `services.commercial.billing.bill_site` (per period, the
    engine's per-item amounts, `per_item_sampled`, total or None, flags);
  * the settlement — every contract settled per investment period by
    `services.commercial.contracts.settle`, on inputs built from the physical
    seam (`physical_quantities` intervals: generators, loads, storage
    discharge, `links_p1_output`), the commercial meter's export, the
    settlement readers (reference prices, the DSR activation) and
    `lp_bindings.site_generators`. A `changes_dispatch` PPA settles at the
    price the LP COMMITTED (`generators_t["ic_ppa_price"]`), so its line equals
    its cost row by construction (WP2.2d review #6);
  * the gap — `services.commercial.gap.billing_vs_lp_gap` on that bill and
    those lines;
  * provenance — the bill's, plus `contracts`: the settled contracts' record
    (`settlement_inputs.contracts_record`), which a stored settlement is
    compared with (`contracts_state`).

The compact billing frames (WP2.1b) go to `state` (`store_billing_frames`).
The payload is JSON: periods keyed "_" (a flat axis) or the period as a
string; NaN is None. Interval lines are in the stored frames, not the payload.

Returns None (the route's 204) without a commercial config or before a solve.
Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any, Callable

import numpy as np
import pandas as pd

from services.commercial import billing as _billing
from services.commercial import contracts as _K
from services.commercial import gap as _gap
from services.commercial import lp_bindings as _lp
from services.commercial import settlement_inputs as _SI
from services.results.physical_quantities import physical_quantities


def _key(period) -> str:
    return "_" if period is None else str(int(period))


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return None if not math.isfinite(f) else f


def _json(x: Any) -> Any:
    """Plain JSON: dict keys as strings, NaN / inf as None, numpy as Python."""
    if isinstance(x, dict):
        return {("_" if k is None else str(k)): _json(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [_json(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return _num(x)
    if isinstance(x, pd.Timestamp):
        return x.isoformat()
    return x


def _solved(n) -> bool:
    p = getattr(n.generators_t, "p", None)
    return p is not None and not p.empty


def _site_index(n, cfg, sel) -> pd.DatetimeIndex:
    ts = n.snapshots[sel]
    ts = ts.get_level_values(-1) if isinstance(ts, pd.MultiIndex) else ts
    return pd.DatetimeIndex(_lp._local_clock(pd.DatetimeIndex(ts), cfg.timezone))


def _rows(frame, sel, index) -> pd.DataFrame | None:
    if frame is None:
        return None
    out = frame.loc[sel] if not isinstance(sel, slice) else frame
    out = pd.DataFrame(np.asarray(out, dtype=float), columns=frame.columns, index=index)
    return out


def settlement_lines(n, cfg, pq: dict) -> tuple[list[_K.Line], list[str]]:
    """Every contract's lines, per investment period, and the flags of the
    contracts that could not settle (`contract_not_settled:<id>:<reason>`)."""
    if not cfg.contracts:
        return [], []
    iv = pq["intervals"]
    multi = isinstance(n.snapshots, pd.MultiIndex)
    periods = list(n.snapshots.get_level_values(0).unique()) if multi else [None]
    w_all = n.snapshot_weightings.objective.to_numpy(dtype=float)
    meter = pq.get("commercial_meter") or {}
    exp_all = meter.get("export_mw")
    storage = [f for f in (iv.get("storage_units_discharge"), iv.get("stores_discharge"))
               if f is not None]
    dsr = _SI.dsr_activation(n)
    refs = {c.id: _SI.reference_price(n, c) for c in cfg.contracts}
    site = _lp.site_generators(n, cfg)
    load_bus = n.loads["bus"].astype(str).to_dict() if not n.loads.empty else {}
    ppa_rec = n.meta.get(_lp.META_PPA) or {}
    ppa_frame = n.generators_t.get(_lp.PPA_PRICE_ATTR) if hasattr(n.generators_t, "get") \
        else None
    lines: list[_K.Line] = []
    flags: list[str] = []
    for p in periods:
        sel = (np.asarray(n.snapshots.get_level_values(0)) == p) if multi \
            else np.ones(len(n.snapshots), bool)
        index = _site_index(n, cfg, sel)

        def cut(series):
            """A seam or reader series on this period's site-clock index."""
            if series is None:
                return None
            s = pd.Series(np.asarray(series, dtype=float)[sel], index=index)
            return s

        def cut_frame(frame):
            return None if frame is None else _rows(frame, sel, index)

        dsr_frame, dsr_flags = dsr
        inputs = _K.SettlementInputs(
            period=None if p is None else int(p), index=index, weights=w_all[sel],
            generators=cut_frame(iv.get("generators")),
            export_mw=cut(exp_all), references={
                cid: ((None if s is None else pd.Series(np.asarray(s, dtype=float)[sel],
                                                        index=index)), f)
                for cid, (s, f) in refs.items()},
            modelled_year=None if p is None else int(p),
            loads=cut_frame(iv.get("loads")), load_bus=load_bus,
            dsr=(cut_frame(dsr_frame), dsr_flags),
            storage_discharge=(cut_frame(pd.concat(storage, axis=1)) if storage else None),
            link_output=cut_frame(iv.get("links_p1_output")),
            site_party=cfg.site_party, site_generators=site)
        for c in cfg.contracts:
            try:
                got = _K.settle(c, inputs)
            except _K.ContractError as exc:
                flags.append(f"contract_not_settled:{c.id}:{str(exc)[:160]}")
                continue
            if c.id in (ppa_rec.get("contracts") or []) and c.type == "ppa":
                got = _committed_ppa(n, c, got, sel, w_all, ppa_frame)
            lines.extend(got)
    return lines, flags


def _committed_ppa(n, c, got, sel, w_all, frame) -> list[_K.Line]:
    """A dispatch PPA's energy line at the price the LP committed: Σ w × p_gen
    × `ic_ppa_price` over the period (the cost row's formula)."""
    gens = list(c.asset_ids)
    p = n.generators_t.p
    if frame is None or any(g not in frame.columns or g not in p.columns for g in gens):
        return [_K.Line(**{**asdict(ln), "amount": None,
                           "flags": sorted(set(ln.flags) | {"ppa_price_not_committed"})})
                for ln in got]
    mw = p[gens].to_numpy(dtype=float)[sel]
    price = frame[gens].to_numpy(dtype=float)[sel]
    if np.isnan(mw).any() or np.isnan(price).any():
        amount = None
    else:
        amount = float((w_all[sel][:, None] * mw * price).sum())
    out = []
    for ln in got:
        if ln.value_stream == "ppa_energy":
            out.append(_K.Line(**{**asdict(ln), "amount": amount,
                                  "flags": sorted(set(ln.flags) | {"ppa_price_committed"})}))
        else:
            out.append(ln)
    return out


def _period_payload(res) -> dict | None:
    if res is None:
        return None
    monthly = {str(m): {str(k): _num(v) for k, v in row.items()}
               for m, row in res.monthly.iterrows()} if not res.monthly.empty else {}
    demand = res.demand_lines.copy()
    for c in demand.columns:
        demand[c] = demand[c].astype(object)
    return {
        "per_item": {k: _num(v) for k, v in res.per_item.items()},
        "per_item_sampled": {k: _num(v) for k, v in res.per_item_sampled.items()},
        "total": _num(res.total), "total_supported": _num(res.total_supported),
        "complete": bool(res.complete),
        "flags": {k: list(v) for k, v in res.flags.items() if v},
        "notes": {k: list(v) for k, v in res.notes.items() if v},
        "unsupported_items": list(res.unsupported_items),
        "monthly": monthly,
        "demand_lines": _json(demand.to_dict(orient="records")),
        "fixed_lines": _json(res.fixed_lines.astype(object).to_dict(orient="records")),
    }


def compute_billing(n, cfg, *, state: dict | None = None,
                    result_df: Callable[..., Any]) -> dict | None:
    commercial = getattr(cfg, "commercial", None)
    if not commercial or not _solved(n):
        return None
    parsed = _lp._parse(commercial)
    bill = _billing.bill_site(n, commercial)
    if "not_solved" in bill.flags:
        return None
    pq = physical_quantities(n, cfg, result_df=result_df)
    lines, contract_flags = settlement_lines(n, parsed, pq)
    gap = _gap.billing_vs_lp_gap(n, commercial, bill,
                                 settlement_lines=lines if parsed.contracts else None)
    if state is not None:
        from services.finance.report import store_billing_frames

        store_billing_frames(state, _billing.compact_frames(bill))
    record = _SI.contracts_record(parsed)
    return _json({
        "per_period": {_key(p): _period_payload(r) for p, r in bill.per_period.items()},
        "flags": sorted(set(bill.flags)),
        "contracts": {"lines": [asdict(ln) for ln in lines],
                      "flags": sorted(set(contract_flags)),
                      "retail": {c.id: _K.retail_parties(c) for c in parsed.contracts
                                 if c.type == "retail"}},
        "gap": gap,
        "provenance": {**bill.provenance, "contracts": record},
    })

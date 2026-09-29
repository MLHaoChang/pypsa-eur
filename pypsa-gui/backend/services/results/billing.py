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


def _raw_frames(n, result_df) -> dict:
    """Generator output, StorageUnit discharge and Link output at bus1 as
    SOLVED — NaN kept (the seam's intervals zero-fill, which would settle an
    unknown hour confidently; review F5). A Store is not an EaaS asset, so
    only StorageUnits discharge here (F4: no name collisions)."""
    sns = n.snapshots

    def frame(attr, col, names):
        df = result_df(n, attr, col, "lopf")
        cols = set(getattr(df, "columns", []))
        return pd.DataFrame({str(c): (df[c].reindex(sns).astype(float) if c in cols
                                      else pd.Series(np.nan, index=sns)) for c in names},
                            index=sns)

    gens = frame("generators_t", "p", list(n.generators.index))
    su = frame("storage_units_t", "p", list(n.storage_units.index))
    p1 = result_df(n, "links_t", "p1", "lopf")
    p0 = result_df(n, "links_t", "p0", "lopf")
    eff = n.links["efficiency"].fillna(1.0) if "efficiency" in n.links.columns else None
    link_out = {}
    for c in n.links.index:
        if p1 is not None and c in p1.columns:
            link_out[str(c)] = -p1[c].reindex(sns).astype(float)
        elif p0 is not None and c in p0.columns:
            link_out[str(c)] = p0[c].reindex(sns).astype(float) * float(
                1.0 if eff is None else eff.get(c, 1.0))
        else:
            link_out[str(c)] = pd.Series(np.nan, index=sns)
    return {"generators": gens, "storage_discharge": su.clip(lower=0.0),
            "link_output": pd.DataFrame(link_out, index=sns)}


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
    # No export Link: the site cannot export, so export is KNOWN to be 0
    # (review F6); a Link without its flow stays unknown.
    exp_all = meter.get("export_mw") if cfg.export_link else \
        pd.Series(0.0, index=n.snapshots)
    raw = _raw_frames(n, pq["result_df"])
    dsr = _SI.dsr_activation(n)
    refs = {c.id: _SI.reference_price(n, c) for c in cfg.contracts}
    site = _lp.site_generators(n, cfg)
    load_bus = n.loads["bus"].astype(str).to_dict() if not n.loads.empty else {}
    ppa_rec = n.meta.get(_lp.META_PPA) or {}
    ppa_frame = n.generators_t.get(_lp.PPA_PRICE_ATTR) if hasattr(n.generators_t, "get") \
        else None
    # The committed price stands for a PPA only while it is still a dispatch
    # PPA AND the record's hash is the config's (review F3).
    from services.commercial import hashing as _H

    current = {c.id for c in _lp.dispatch_ppas(cfg)}
    committed_ids = current if ppa_rec and ppa_rec.get("hash") == _lp.ppa_dispatch_hash(
        cfg, _H.version_of(ppa_rec)) else set()
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
            generators=cut_frame(raw["generators"]),
            export_mw=cut(exp_all), references={
                cid: ((None if s is None else pd.Series(np.asarray(s, dtype=float)[sel],
                                                        index=index)), f)
                for cid, (s, f) in refs.items()},
            modelled_year=None if p is None else int(p),
            loads=cut_frame(iv.get("loads")), load_bus=load_bus,
            dsr=(cut_frame(dsr_frame), dsr_flags),
            storage_discharge=cut_frame(raw["storage_discharge"]),
            link_output=cut_frame(raw["link_output"]),
            site_party=cfg.site_party, site_generators=site)
        for c in cfg.contracts:
            try:
                got = _K.settle(c, inputs)
            except _K.ContractError as exc:
                flags.append(f"contract_not_settled:{c.id}:{str(exc)[:160]}")
                continue
            if c.id in committed_ids:
                got = _committed_ppa(n, c, got, sel, w_all, ppa_frame)
            elif c.id in (ppa_rec.get("contracts") or []):
                # Bound at the solve, changed since (review F3): settled on the
                # current terms, and said so.
                got = [_K.Line(**{**asdict(ln), "flags": sorted(set(ln.flags)
                                                              | {"ppa_changed_since_solve"})})
                       for ln in got]
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
        if ln.value_stream == "ppa_energy" and ln.amount is not None:
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


def _retail(parsed) -> tuple[dict, list[str]]:
    out, flags = {}, []
    for c in parsed.contracts:
        if c.type != "retail":
            continue
        try:
            out[c.id] = _K.retail_parties(c, parsed.import_tariff)
        except (_K.ContractError, TypeError, ValueError) as exc:     # review F1
            out[c.id] = None
            flags.append(f"contract_not_settled:{c.id}:{str(exc)[:160]}")
    return out, flags


def _gap_summary(gap: dict) -> dict:
    """What the chat must see first (review F7): the gates, and per period and
    kind the LP, billed and unattributed amounts."""
    return {"gates": gap.get("gates") or [],
            "periods": {p: {k: {"lp": v.get("lp"), "billed": v.get("billed"),
                                "unattributed_pct": v.get("unattributed_pct")}
                            for k, v in per.items()}
                        for p, per in (gap.get("periods") or {}).items()}}


def compute_billing(n, cfg, *, state: dict | None = None,
                    result_df: Callable[..., Any]) -> dict | None:
    commercial = getattr(cfg, "commercial", None)
    if not commercial or not _solved(n):
        return None
    parsed = _lp._parse(commercial)
    if parsed.import_tariff is None and not parsed.contracts:
        return None                                   # nothing to bill (review F13)
    bill = _billing.bill_site(n, commercial)
    if "not_solved" in bill.flags:
        return None
    flags = list(bill.flags)
    if parsed.import_tariff is None:
        # No bill, so `bill_site` compared no records: the contracts' drift
        # (a dispatch PPA edited since the solve) is still stated (F3).
        drift, _ = _billing._drift_flags(n, parsed)
        flags += [f for f in drift if f != "solve_provenance_unknown"]
    pq = physical_quantities(n, cfg, result_df=result_df)
    pq["result_df"] = result_df
    lines, contract_flags = settlement_lines(n, parsed, pq)
    retail, retail_flags = _retail(parsed)
    gap = _gap.billing_vs_lp_gap(n, commercial, bill,
                                 settlement_lines=lines if parsed.contracts else None)
    if state is not None:
        from services.finance.report import store_billing_frames

        store_billing_frames(state, _billing.compact_frames(bill))
    record = _SI.contracts_record(parsed)
    per_period = {_key(p): _period_payload(r) for p, r in bill.per_period.items()}
    # Summary first, detail last: a chat reads the head of the payload (F7).
    return _json({
        "summary": {k: (None if v is None else {"total": v["total"],
                                                "total_supported": v["total_supported"],
                                                "per_item": v["per_item"]})
                    for k, v in per_period.items()},
        "flags": sorted(set(flags)),
        "contracts": {"lines": [asdict(ln) for ln in lines],
                      "flags": sorted(set(contract_flags + retail_flags)),
                      "retail": retail},
        "gap_summary": _gap_summary(gap),
        "per_period": per_period,
        "gap": gap,
        "provenance": {**bill.provenance, "contracts": record},
    })

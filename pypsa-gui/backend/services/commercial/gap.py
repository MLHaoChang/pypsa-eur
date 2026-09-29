"""
Billing vs LP gap per item kind, with computed causes (Edge Investment Case P2
WP2.3; spec §5.5).

`billing_vs_lp_gap(n, commercial, site_bill, *, settlement_lines=None,
threshold_pct=5.0)` compares, per investment period, what the LP charged with
what the bill rates, per item kind:

  * kinds: `energy` (per-interval €/kWh items), `demand`, `tiers` (tiered
    energy), `capacity` (tariff capacity items), `fixed`, `contracts`;
  * basis: the UNWEIGHTED period-year amounts — the bill's `per_item_sampled`
    (the WP2.1b convention: sampled to sampled), the settlement lines as
    produced, and the LP amounts per period before any `years` weighting;
  * the LP side is recomputed from what the solve COMMITTED, on the same
    dispatch: energy from `links_t["ic_energy_price"]` (less the export
    price, which is not a tariff item, and less the non-convex tiers' adders,
    which are the `tiers` kind), demand from `ic_demand_peaks`, convex tiers
    from `ic_tier_volumes`, capacity from `ic_tariff_capacity`, dispatch PPAs
    from `generators_t["ic_ppa_price"]`. A per-item breakdown (`items`)
    recomputes energy items from their rates.

Every difference the model explains is a cause with a COMPUTED amount:

  * `fixed` — a fixed item (not in the LP): its billed amount;
  * `not_in_lp` — an item the LP leaves out, with its reason: its billed
    amount (a fixed-PoC capacity charge is `fixed_poc_not_in_lp`);
  * `nonconvex_tier` — an item the LP prices at one predicted tier: billed − LP;
  * `tier_allocation` — convex windowed tiers: the engine's proportional
    allocation − the LP's optimal one, billed − LP;
  * `net_split_by_direction` — a net item the LP charges on one side, which
    the meter nets per interval (import and export in one interval):
    billed − LP;
  * `settlement_only` — a contract with no LP term: its settled amount;
  * `months_not_established` / `partial_months` — disclosures: both sides
    compare the same sampled months (WP2.1b), so their amount is 0;
  * `ratchet_seed` — a ratchet whose lookback is partly unknown: amount None
    plus `ratchet_seed_missing` (both sides bill the lower bound);
  * `config_changed_since_solve` — the bill rates an edited config on the old
    dispatch: the WHOLE difference (likewise `lp_recipe_changed`, a solve
    under an older LP recipe).

`resolution` is a disclosed risk, not a computed cause: the LP and the bill
read the same dispatch at the same resolution, so a finer real load cannot
show as a gap. When the axis is coarser than a demand item's settlement the
payload carries `resolution_risk` with the preflight's warning.

`unattributed = gap − Σ known cause amounts`; `unattributed_pct` is its size
relative to the larger of |LP| and |billed|. Above `threshold_pct` (and above
a cent) it raises the `billing_gap_unexplained` warn gate. An amount that
cannot be computed is None with a flag (ADR-0001), never 0; a kind with an
unknown side has no gap and raises no gate.

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from services.commercial import lp_bindings as _lp
from services.commercial.preflight import demand_resolution_warnings
from services.commercial.tariff_engine import _is_demand, _rates, is_windowed_tiered

KINDS = ("energy", "demand", "tiers", "capacity", "fixed", "contracts")
DEFAULT_THRESHOLD_PCT = 5.0
GATE = "billing_gap_unexplained"
_RECIPE_FLAGS = ("energy_recipe_changed", "demand_recipe_changed", "capacity_recipe_changed",
                 "ppa_recipe_changed")
_ABS_TOL_EUR = 0.01   # below a cent, a relative gap is float noise, not a gate
_KWH_PER_MWH = 1000.0


def item_kind(item) -> str:
    if item.kind == "fixed":
        return "fixed"
    if item.kind == "capacity":
        return "capacity"
    if _is_demand(item):
        return "demand"
    if item.tiers:
        return "tiers"
    return "energy"


def _gap_pct(lp: float, billed: float) -> float | None:
    """(billed − LP) / |LP| in %; undefined (None) for an LP of 0 with a bill."""
    if lp == 0:
        return 0.0 if billed == 0 else None
    return 100.0 * (billed - lp) / abs(lp)


def _unattributed_pct(unattributed: float, lp: float, billed: float) -> float:
    basis = max(abs(lp), abs(billed))
    return 0.0 if basis == 0 else 100.0 * abs(unattributed) / basis


# ── the LP side, from the committed records ────────────────────────────────


class _Axis:
    def __init__(self, n):
        self.multi = isinstance(n.snapshots, pd.MultiIndex)
        self.index = n.snapshots
        self.w = n.snapshot_weightings.objective.to_numpy(dtype=float)
        self.periods = ([int(p) for p in n.snapshots.get_level_values(0).unique()]
                        if self.multi else [None])

    def by_period(self, values: np.ndarray) -> dict:
        if not self.multi:
            return {None: float(np.sum(values))}
        s = pd.Series(values, index=self.index).groupby(level=0).sum()
        return {int(p): float(v) for p, v in s.items()}


def _p0(n, link: str | None) -> np.ndarray | None:
    if link is None or link not in n.links_t.p0.columns:
        return None
    return n.links_t.p0[link].to_numpy(dtype=float)


def _energy_committed(n, cfg, ax: _Axis) -> np.ndarray | None:
    """w × p0 × the committed €/MWh on every priced Link, per snapshot, less
    the export price (a market price the tariff bill does not contain)."""
    rec = n.meta.get(_lp.META_LINKS)
    if not rec:
        return None
    prices = _lp._frame(n, _lp.ENERGY_PRICE_ATTR)
    total = np.zeros(len(ax.w))
    for link in rec.get("priced") or []:
        p0 = _p0(n, link)
        if p0 is None or link not in prices.columns:
            return None
        add = prices[link].to_numpy(dtype=float)
        if np.isnan(add).any():
            return None
        total += ax.w * p0 * add
    exp = rec.get("export")
    if cfg.export_price_ref is not None and exp in (rec.get("priced") or []):
        store = n.links_t.get(_lp.EXPORT_PRICE_ATTR) if hasattr(n.links_t, "get") else None
        if store is None or exp not in store.columns:
            return None
        price = store[exp].reindex(n.snapshots).to_numpy(dtype=float)
        if np.isnan(price).any():
            return None
        total += ax.w * _p0(n, exp) * price   # the adder was tariff − price
    return total


def _import_flow(n, cfg) -> np.ndarray | None:
    flows = [_p0(n, link) for link in _lp.import_links(cfg)]
    return None if any(f is None for f in flows) else np.sum(flows, axis=0)


def _energy_items_lp(n, cfg, ax: _Axis, local) -> dict:
    """{item: per-snapshot LP € or None} for the energy items the LP prices,
    from their rates on the side the LP charges them."""
    imp = _import_flow(n, cfg)
    exp = _p0(n, cfg.export_link)
    out = {}
    for item in cfg.import_tariff.items:
        if item_kind(item) != "energy" or _lp._lp_reason(item) is not None:
            continue
        flow = imp if _lp._side(item) == "import" else exp
        r = _rates(item, local)
        if flow is None or np.isnan(r).any():
            out[item.id] = None
            continue
        sign = 1.0 if item.direction == "cost" else -1.0
        out[item.id] = sign * ax.w * flow * r * _KWH_PER_MWH
    return out


def _nonconvex_lp(n, cfg, ax: _Axis, local) -> dict:
    """{item: per-snapshot LP € or None} for the non-convex tiered energy
    items: the predicted tier's €/MWh the LP charged on import — None when the
    committed energy record is gone (WP2.3 review M1)."""
    rec = n.meta.get(_lp.META_LINKS)
    imp = _import_flow(n, cfg)
    committed = _energy_committed(n, cfg, ax) is not None
    out = {}
    for item in _lp._energy_tiered(cfg, _lp.energy_recipe_of(rec)):
        if _lp.item_tiers_convex(item):
            continue
        if imp is None or not committed:
            # The adder rode `ic_energy_price`: without that record the LP's
            # charge is not established, whatever the config predicts (M1).
            out[item.id] = None
            continue
        try:
            price, _ = _lp._predicted_tier_price(n, cfg, item, local)
        except _lp.CommercialBindingError:
            out[item.id] = None
            continue
        out[item.id] = ax.w * imp * price
    return out


def _add(acc: dict, key, period, amount: float) -> None:
    acc.setdefault(key, {}).setdefault(period, 0.0)
    acc[key][period] += float(amount)


def _demand_lp(n) -> dict | None:
    peaks = n.meta.get(_lp.META_DEMAND)
    if peaks is None:
        return None
    out: dict = {}
    for v in peaks.values():
        _add(out, v.get("item"), v.get("inv_period"), _lp.demand_amount(v))
    return out


def _tiers_lp(n) -> dict | None:
    tiers = n.meta.get(_lp.META_TIERS)
    if tiers is None:
        return None
    out: dict = {}
    for v in tiers.values():
        _add(out, v.get("item"), v.get("inv_period"),
             float(v["rate_eur_per_mwh"]) * float(v["q_mwh"]))
    return out


def _capacity_lp(n) -> tuple[dict | None, set[str]]:
    """({item: {period: €}} or None, the fixed-PoC items the LP left out)."""
    cap = n.meta.get(_lp.META_CAPACITY)
    if cap is None:
        return None, set()
    out: dict = {}
    for item_id, c in (cap.get("contracted") or {}).items():
        link = c.get("link")
        size = (float(n.links.at[link, "p_nom_opt"])
                if link in n.links.index and "p_nom_opt" in n.links.columns else np.nan)
        if not np.isfinite(size):
            return None, set()
        for key, eur_per_mw in (c.get("eur_per_mw_by_period") or {}).items():
            _add(out, item_id, None if key == "_" else int(key), float(eur_per_mw) * size)
    for v in (cap.get("peaks") or {}).values():
        _add(out, v.get("item"), v.get("inv_period"),
             float(v["eur_per_mw"]) * float(v["peak_mw"]))
    return out, set(cap.get("fixed") or {})


def _ppa_lp(n, cfg, ax: _Axis) -> dict | None:
    """{contract: {period: €}} for the dispatch PPAs, from the committed €/MWh."""
    rec = n.meta.get(_lp.META_PPA)
    if rec is None:
        return None
    frame = n.generators_t.get(_lp.PPA_PRICE_ATTR) if hasattr(n.generators_t, "get") else None
    p = n.generators_t.p
    out: dict = {}
    for c in _lp.dispatch_ppas(cfg):
        gens = list(c.asset_ids)
        if frame is None or any(g not in frame.columns or g not in p.columns for g in gens):
            return None
        amount = (p[gens].to_numpy(dtype=float) * frame[gens].to_numpy(dtype=float)).sum(axis=1)
        if np.isnan(amount).any():
            return None
        for period, v in ax.by_period(ax.w * amount).items():
            _add(out, c.id, period, v)
    return out


# ── the billed side ────────────────────────────────────────────────────────


def _site_view(line, site_party: str) -> float | None:
    """A line's amount from the site's view: + paid by the site, − paid to it,
    0 for a line between two other parties."""
    if line.amount is None:
        return None
    if _lp.same_party(line.payer, site_party):
        return float(line.amount)
    if _lp.same_party(line.payee, site_party):
        return -float(line.amount)
    if line.payer is None or line.payee is None:
        return None   # a party not established: whose money it is is unknown
    return 0.0


# ── independent checks of the by-construction causes (review M3) ───────────


_TOL_REL = 1e-6


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= _TOL_REL * max(1.0, abs(a), abs(b))


def _tier_allocation_ok(n, cfg, item, period, ax: _Axis, local) -> str | None:
    """None when the LP's windowed tier record is consistent with the
    dispatch: per (month, window) its segment volumes sum to the window's
    metered energy. Else the reason."""
    recs = [v for v in (n.meta.get(_lp.META_TIERS) or {}).values()
            if v.get("item") == item.id and v.get("inv_period") == period]
    imp = _import_flow(n, cfg)
    if imp is None:
        return "import_flow_not_established"
    from services.commercial.tariff_engine import _period_index

    frag = _period_index(item, local)
    months = np.asarray(local.strftime("%Y-%m"))
    inv = (np.asarray(n.snapshots.get_level_values(0)) if ax.multi
           else np.full(len(ax.w), None, dtype=object))
    sel = (inv == period) if ax.multi else np.ones(len(ax.w), bool)
    names = np.asarray([item.periods[f].name if f >= 0 else "" for f in frag])
    metered = pd.Series(ax.w * imp)[sel].groupby([months[sel], names[sel]]).sum()
    q = pd.Series([float(v["q_mwh"]) for v in recs],
                  index=pd.MultiIndex.from_tuples([(v["month"], v["period"]) for v in recs])
                  ).groupby(level=[0, 1]).sum() if recs else pd.Series(dtype=float)
    for key, energy in metered.items():
        if not _close(float(q.get(key, 0.0)), float(energy)):
            return "tier_volumes_do_not_match_the_dispatch"
    return None


def _net_split(n, cfg, item, ax: _Axis, local) -> dict | None:
    """{period: €} computed from the dispatch: what the meter's per-interval
    netting bills minus what the LP charged on one side."""
    imp = _import_flow(n, cfg)
    exp = _p0(n, cfg.export_link)
    r = _rates(item, local)
    if imp is None or np.isnan(r).any():
        return None
    exp = np.zeros(len(ax.w)) if exp is None else exp
    i_c, e_c = np.clip(imp, 0.0, None), np.clip(exp, 0.0, None)
    if item.direction == "cost":
        diff = np.clip(i_c - e_c, 0.0, None) - imp
        sign = 1.0
    else:
        diff = np.clip(e_c - i_c, 0.0, None) - exp
        sign = -1.0
    return ax.by_period(sign * ax.w * r * _KWH_PER_MWH * diff)


# ── assembly ───────────────────────────────────────────────────────────────


# Causes computed as billed − LP: under a config change or a recipe change the
# LP and the bill no longer describe the same config, so they are dropped and
# the remainder is the change's (review M2).
_BY_CONSTRUCTION = {"nonconvex_tier", "tier_allocation"}


def _kind(lp, billed, causes, flags, items, *, drift=False, recipe=()) -> dict:
    out = {"lp": lp, "billed": billed, "gap": None, "gap_pct": None, "causes": causes,
           "unattributed": None, "unattributed_pct": None, "flags": sorted(set(flags)),
           "items": items}
    if lp is None:
        out["flags"] = sorted(set(out["flags"]) | {"lp_not_established"})
    if billed is None:
        out["flags"] = sorted(set(out["flags"]) | {"billed_not_established"})
    if lp is None or billed is None:
        return out
    gap = billed - lp
    if drift or recipe:
        # The bill rates an edited config (or one a newer recipe binds more
        # of) on the old dispatch: what the independent causes do not explain
        # is that change.
        causes = [c for c in causes if c["cause"] not in _BY_CONSTRUCTION]
        known = sum(c["amount"] for c in causes if c.get("amount") is not None)
        change = ({"cause": "config_changed_since_solve", "amount": gap - known} if drift
                  else {"cause": "lp_recipe_changed", "amount": gap - known,
                        "flags": sorted(recipe)})
        causes = causes + [change]
    known = sum(c["amount"] for c in causes if c.get("amount") is not None)
    unattributed = gap - known
    out.update(gap=gap, gap_pct=_gap_pct(lp, billed), causes=causes,
               unattributed=unattributed,
               unattributed_pct=_unattributed_pct(unattributed, lp, billed))
    return out


def _sum_known(values) -> float | None:
    values = list(values)
    return None if any(v is None for v in values) else float(sum(values))


def _of_period(entries, period, multi: bool) -> list[str]:
    """A demand record's months of one period ("<p>:YYYY-MM" on a multi-
    period axis; review L5)."""
    if not multi:
        return list(entries)
    prefix = f"{period}:"
    return [e for e in entries if str(e).startswith(prefix)]


def billing_vs_lp_gap(n, commercial, site_bill, *, settlement_lines: list | None = None,
                      threshold_pct: float = DEFAULT_THRESHOLD_PCT) -> dict:
    cfg = _lp._parse(commercial)
    risk = [{"code": "commercial.demand_resolution", "item": item_id, "message": msg}
            for item_id, msg in demand_resolution_warnings(n, cfg)]
    out = {"periods": {}, "flags": sorted(set(site_bill.flags)), "gates": [],
           "threshold_pct": float(threshold_pct), "resolution_risk": risk}
    ax = _Axis(n)
    solved = hasattr(n, "generators_t") and not n.generators_t.p.empty
    if site_bill.per_period:
        periods = list(site_bill.per_period)
    elif cfg.contracts and solved:
        periods = list(ax.periods)   # contracts compare without a tariff (review L6)
    else:
        return out
    prov = site_bill.provenance or {}
    if "drift" in prov:
        dr = prov["drift"]
        rc = prov.get("recipe_changed") or {}
    else:   # a bill without per-record states: the global flags, tariff kinds only
        glob = "config_changed_since_solve" in site_bill.flags
        dr = {k: glob for k in ("energy", "demand", "tiers", "capacity", "ppa")}
        rc = {k: sorted(f for f in site_bill.flags if f in _RECIPE_FLAGS)
              for k in ("energy", "demand", "tiers", "capacity", "contracts")}
    drift_of = {"energy": dr.get("energy", False), "demand": dr.get("demand", False),
                "tiers": dr.get("tiers", False) or dr.get("energy", False),
                "capacity": dr.get("capacity", False)}
    local = _lp._local_clock(n.snapshots, cfg.timezone)
    items = cfg.import_tariff.items if cfg.import_tariff is not None else []
    by_kind: dict[str, list] = {k: [i for i in items if item_kind(i) == k] for k in KINDS}

    # The LP side, once for all periods.
    committed = _energy_committed(n, cfg, ax) if items else None
    energy_items = _energy_items_lp(n, cfg, ax, local) if items else {}
    nonconvex = _nonconvex_lp(n, cfg, ax, local) if items else {}
    if committed is not None and all(v is not None for v in nonconvex.values()):
        energy_total = ax.by_period(committed - sum(nonconvex.values(), np.zeros(len(ax.w))))
    else:
        energy_total = None
    energy_items_p = {k: (None if v is None else ax.by_period(v)) for k, v in energy_items.items()}
    nonconvex_p = {k: (None if v is None else ax.by_period(v)) for k, v in nonconvex.items()}
    net_p = {i.id: _net_split(n, cfg, i, ax, local) for i in by_kind["energy"]
             if i.measured_on == "net" and _lp._lp_reason(i) is None}
    demand = _demand_lp(n)
    demand_info = n.meta.get(_lp.META_DEMAND_INFO) or {}
    demand_notes = demand_info.get("notes") or []
    tiers = _tiers_lp(n)
    capacity, fixed_poc = _capacity_lp(n)
    ppa = _ppa_lp(n, cfg, ax)
    wanted_demand = bool(_lp.demand_lp_items(cfg)) if items else False
    wanted_convex = _lp.tier_items_hash(cfg) is not None if items else False
    wanted_capacity = bool(_lp.capacity_lp_items(cfg)) if items else False
    wanted_ppa = bool(_lp.dispatch_ppas(cfg))

    for period in periods:
        res = site_bill.per_period.get(period)
        billed_of = (lambda i: None) if res is None else \
            (lambda i, r=res: r.per_item_sampled.get(i))
        per: dict = {}

        # energy
        if by_kind["energy"] or (energy_total is not None
                                 and abs(energy_total.get(period, 0.0)) > _ABS_TOL_EUR):
            causes, flags, rows = [], [], {}
            for item in by_kind["energy"]:
                b = billed_of(item.id)
                reason = _lp._lp_reason(item)
                lp_i = 0.0 if reason is not None else (
                    None if energy_items_p.get(item.id) is None
                    else energy_items_p[item.id].get(period, 0.0))
                rows[item.id] = {"lp": lp_i, "billed": b}
                if reason is not None:
                    causes.append({"cause": "not_in_lp", "item": item.id, "reason": reason,
                                   "amount": b})
                elif item.measured_on == "net":
                    split = net_p.get(item.id)
                    causes.append({"cause": "net_split_by_direction", "item": item.id,
                                   "amount": None if split is None else split.get(period, 0.0)})
            lp = None if energy_total is None else energy_total.get(period, 0.0)
            billed = _sum_known(r["billed"] for r in rows.values())
            per["energy"] = _kind(lp, billed, causes, flags, rows, drift=drift_of["energy"],
                                  recipe=rc.get("energy") or ())

        # demand
        if by_kind["demand"]:
            causes, flags, rows = [], [], {}
            for item in by_kind["demand"]:
                b = billed_of(item.id)
                reason = _lp._lp_reason(item)
                lp_i = 0.0 if reason is not None or demand is None and not wanted_demand else (
                    None if demand is None else (demand.get(item.id) or {}).get(period, 0.0))
                rows[item.id] = {"lp": lp_i, "billed": b}
                if reason is not None:
                    causes.append({"cause": "not_in_lp", "item": item.id, "reason": reason,
                                   "amount": b})
                elif f"nonconvex_tier:{item.id}" in demand_notes:
                    causes.append({"cause": "nonconvex_tier", "item": item.id,
                                   "amount": None if b is None or lp_i is None else b - lp_i})
            gone = _of_period(demand_info.get("not_established") or [], period, ax.multi)
            if gone:
                causes.append({"cause": "months_not_established", "amount": 0.0,
                               "months": gone})
            partial = _of_period(demand_info.get("partial_months") or [], period, ax.multi)
            if partial:
                causes.append({"cause": "partial_months", "amount": 0.0, "months": partial})
            tagged = any(str(x).startswith("ratchet_seed_missing:") for x in demand_notes)
            seed = (f"ratchet_seed_missing:{period}" in demand_notes if ax.multi and tagged
                    else "ratchet_seed_missing" in demand_notes)
            if seed:
                causes.append({"cause": "ratchet_seed", "amount": None,
                               "flag": "ratchet_seed_missing"})
                flags.append("ratchet_seed_missing")
            lp = _sum_known(r["lp"] for r in rows.values())
            billed = _sum_known(r["billed"] for r in rows.values())
            per["demand"] = _kind(lp, billed, causes, flags, rows, drift=drift_of["demand"],
                                  recipe=rc.get("demand") or ())

        # tiers
        if by_kind["tiers"]:
            causes, flags, rows = [], [], {}
            for item in by_kind["tiers"]:
                b = billed_of(item.id)
                reason = _lp._lp_reason(item)
                if reason is not None:
                    lp_i = 0.0
                    causes.append({"cause": "not_in_lp", "item": item.id, "reason": reason,
                                   "amount": b})
                elif item.id in nonconvex_p:
                    lp_i = None if nonconvex_p[item.id] is None \
                        else nonconvex_p[item.id].get(period, 0.0)
                    causes.append({"cause": "nonconvex_tier", "item": item.id,
                                   "amount": None if b is None or lp_i is None else b - lp_i})
                else:
                    lp_i = (None if tiers is None and wanted_convex
                            else (tiers or {}).get(item.id, {}).get(period, 0.0))
                    if is_windowed_tiered(item):
                        amount = None if b is None or lp_i is None else b - lp_i
                        why = None if tiers is None else \
                            _tier_allocation_ok(n, cfg, item, period, ax, local)
                        if why is None and amount is not None and \
                                amount < -_TOL_REL * max(1.0, abs(b)):
                            why = "billed_below_the_lp_optimum"   # proportional ≥ optimal
                        if why is not None:
                            flags.append(f"tier_allocation_not_established:{item.id}:{why}")
                            amount = None
                        causes.append({"cause": "tier_allocation", "item": item.id,
                                       "amount": amount})
                rows[item.id] = {"lp": lp_i, "billed": b}
            lp = _sum_known(r["lp"] for r in rows.values())
            billed = _sum_known(r["billed"] for r in rows.values())
            per["tiers"] = _kind(lp, billed, causes, flags, rows, drift=drift_of["tiers"],
                                 recipe=rc.get("tiers") or ())

        # capacity
        if by_kind["capacity"]:
            causes, flags, rows = [], [], {}
            for item in by_kind["capacity"]:
                b = billed_of(item.id)
                reason = _lp._lp_reason(item)
                if reason is None and item.id in fixed_poc:
                    reason = "fixed_poc_not_in_lp"
                if reason is not None:
                    lp_i = 0.0
                    causes.append({"cause": "not_in_lp", "item": item.id, "reason": reason,
                                   "amount": b})
                else:
                    lp_i = (None if capacity is None and wanted_capacity
                            else (capacity or {}).get(item.id, {}).get(period, 0.0))
                rows[item.id] = {"lp": lp_i, "billed": b}
            lp = _sum_known(r["lp"] for r in rows.values())
            billed = _sum_known(r["billed"] for r in rows.values())
            per["capacity"] = _kind(lp, billed, causes, flags, rows,
                                    drift=drift_of["capacity"], recipe=rc.get("capacity") or ())

        # fixed — never in the LP, so never drift
        if by_kind["fixed"]:
            causes, rows = [], {}
            for item in by_kind["fixed"]:
                b = billed_of(item.id)
                rows[item.id] = {"lp": 0.0, "billed": b}
                causes.append({"cause": "fixed", "item": item.id, "amount": b})
            billed = _sum_known(r["billed"] for r in rows.values())
            per["fixed"] = _kind(0.0, billed, causes, [], rows)

        # contracts — drift only on the dispatch PPAs, per contract
        if cfg.contracts:
            causes, flags, rows = [], [], {}
            dispatch_ids = {c.id for c in _lp.dispatch_ppas(cfg)}
            for c in cfg.contracts:
                if c.id in dispatch_ids:
                    lp_c = (None if ppa is None and wanted_ppa
                            else (ppa or {}).get(c.id, {}).get(period, 0.0))
                else:
                    lp_c = 0.0
                if settlement_lines is None:
                    b = None
                else:
                    mine = [ln for ln in settlement_lines
                            if ln.contract_id == c.id and ln.period == period]
                    views = [_site_view(ln, cfg.site_party) for ln in mine]
                    if not mine and c.type != "retail":
                        # A contract that settles has lines; none is unknown,
                        # never a confident 0 (review M4). Retail has none.
                        b = None
                        flags.append(f"settlement_lines_missing:{c.id}")
                    else:
                        b = _sum_known(views)
                    if any(v == 0.0 and ln.amount for v, ln in zip(views, mine)):
                        flags.append(f"third_party_lines_excluded:{c.id}")
                rows[c.id] = {"lp": lp_c, "billed": b}
                if c.id not in dispatch_ids:
                    causes.append({"cause": "settlement_only", "contract": c.id, "amount": b})
                elif dr.get("ppa") and b is not None and lp_c is not None:
                    causes.append({"cause": "config_changed_since_solve", "contract": c.id,
                                   "amount": b - lp_c})
                elif rc.get("contracts") and b is not None and lp_c is not None:
                    causes.append({"cause": "lp_recipe_changed", "contract": c.id,
                                   "amount": b - lp_c, "flags": sorted(rc["contracts"])})
            if settlement_lines is None:
                flags.append("settlement_not_provided")
            lp = _sum_known(r["lp"] for r in rows.values())
            billed = _sum_known(r["billed"] for r in rows.values())
            per["contracts"] = _kind(lp, billed, causes, flags, rows)

        out["periods"][period] = per
        for kind, v in per.items():
            u, pct = v["unattributed"], v["unattributed_pct"]
            if pct is not None and pct > threshold_pct and abs(u) > _ABS_TOL_EUR:
                out["gates"].append({"gate": GATE, "severity": "warn", "period": period,
                                     "kind": kind, "unattributed": u,
                                     "unattributed_pct": pct,
                                     "threshold_pct": float(threshold_pct)})
    return out

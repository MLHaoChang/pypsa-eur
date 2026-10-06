"""
The commercial terms a solve charged, as cost rows (Edge Investment Case spec
§5.1; P1 WP1.3 / WP1.4a).

The commercial layer is transient: the PoC prices and the connection agreement
are applied for the solve and undone, so their money is NOT in
`n.statistics()`. `commercial_cost_terms` recomputes it from what the solve
committed (`links_t["ic_energy_price"]`, `n.meta["ic_poc_links"]`,
`n.meta["ic_connection_fee"]`, `n.meta["ic_connection_fixed_fee"]`, the group
net-import record `n.meta["ic_group_net"]` with `links_t["ic_group_net_price"]`)
and the dispatch. The rows are therefore identical after a reload, and every total
built on `n.statistics()` adds the same items:

  * `cost_breakdown` feeds each item through its `_accumulate` as the
    component "Commercial", so `Σ by_component == capex/opex`, and the same
    holds per period;
  * `cost_totals.horizon_system_cost` adds the same sum, so the solve-queue
    and status-bar total keep agreeing with `/results/cost_breakdown`.

Items carry the period (None on a flat axis) and are NOT years-weighted; each
caller applies its own `years(period)`, exactly as for the statistics rows.
A fixed connection fee (on fixed capacity) is not an LP term: it is reported
under `network_capacity_fixed` with `included_in_total: False`. A term the
config names but the solve did not commit is None plus a flag (ADR-0001).

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from services.commercial import connection as _conn
from services.commercial import hashing as _H
from services.commercial import lp_bindings as _lp


def _energy_by_period(n, link: str, prices: pd.DataFrame) -> dict | None:
    if link not in prices.columns or link not in n.links_t.p0.columns:
        return None
    add = prices[link]
    if add.isna().any():
        return None
    amount = n.snapshot_weightings.objective * n.links_t.p0[link] * add
    if isinstance(n.snapshots, pd.MultiIndex):
        return {p: float(v) for p, v in amount.groupby(level=0).sum().items()}
    return {None: float(amount.sum())}


def commercial_cost_terms(n, commercial: dict | None, *, years=None) -> dict:
    """{"items": [(label, period, capex, opex)], "block": {...}, "flags": [...]}.

    Items are unweighted per period; `block` (the `cost_breakdown["commercial"]`
    payload) sums them with the SAME `years(period)` weighting the caller
    applies to the items, so a label in the block equals what the totals carry
    (WP1.3 review round 3 #2). `years` defaults to 1 per period.
    """
    yrs = years or (lambda p: 1.0)

    def weighted(label: str) -> float:
        return float(sum((cx + ox) * (yrs(p) if p is not None else 1.0)
                         for lab, p, cx, ox in items if lab == label))

    flags: list[str] = []
    items: list[tuple] = []
    block: dict = {"included_in_total": True}

    # Energy import / export (WP1.3).
    rows = _lp.energy_cost_rows(n, commercial)
    if rows is not None:
        flags += [f for f in rows.get("flags", []) if f not in flags]
        solved = n.meta.get(_lp.META_LINKS) or {}
        prices = _lp._frame(n, _lp.ENERGY_PRICE_ATTR)
        exp = solved.get("export")
        for label, links in (("energy_import", _lp.solved_import_links(solved)),
                             ("energy_export", [exp] if exp else [])):
            block[label] = rows.get(label)
            if not links or rows.get(label) is None:
                continue
            for link in links:  # a group's members each carry the tariff
                per = _energy_by_period(n, link, prices)
                for p, v in (per or {}).items():
                    if v:
                        items.append((label, p, 0.0, v))
            block[label] = weighted(label)
        if "simultaneous_snapshots" in rows:
            block["simultaneous_snapshots"] = rows["simultaneous_snapshots"]

    # Peak-demand charges (WP1.5a): €/MW × solved (billed) monthly peak.
    peaks = n.meta.get(_lp.META_DEMAND)
    info = n.meta.get(_lp.META_DEMAND_INFO) or {}
    wanted: list[str] = []
    wanted_items: list = []
    cfg_parsed = None
    wanted_hash = None
    if commercial:
        try:
            cfg_parsed = _lp._parse(commercial)
            wanted_items = _lp.demand_lp_items(cfg_parsed)
            wanted = [i.id for i in wanted_items]
            wanted_hash = (_lp.demand_hash(n, cfg_parsed, wanted_items, _H.version_of(info))
                           if wanted_items else None)
        except Exception:  # noqa: BLE001
            wanted = []
    if peaks:
        for v in peaks.values():
            amount = _lp.demand_amount(v)
            items.append(("demand_charge", v.get("inv_period"), 0.0, amount))
        block["demand_charge"] = weighted("demand_charge")
        drift = sorted(info.get("items", [])) != sorted(wanted) or (
            info.get("items_hash") is not None and info.get("items_hash") != wanted_hash)
        if drift and commercial and _lp.demand_only_newly_bound(n, info, wanted_items, cfg_parsed):
            # Solved before WP2.1c-i put demand tiers and designated-month /
            # cyclic ratchets in the LP: the config is unchanged, the recipe
            # binds more of it now — re-solve (not a config drift).
            flags.append("demand_recipe_changed")
        elif drift:
            flags.append("config_changed_since_solve" if commercial else
                         "config_cleared_since_solve")
        if info.get("hash_version") is None:
            # A P1 record keyed peaks by period POSITION: two peaks for one
            # (item, window name, month) mean a split window billed twice,
            # which the P2 recipe bills once (WP2.1a-0 review #4). Unique-name
            # P1 records bill the same under both and are not flagged.
            idents = [(v.get("item"), v.get("period"), v.get("month"), v.get("inv_period"))
                      for v in peaks.values()]
            if len(idents) != len(set(idents)):
                flags.append("demand_recipe_changed")
        if "ratchet_seed_missing" in (info.get("notes") or []):
            # The billed demand is a LOWER BOUND: part of a ratchet's lookback
            # is unknown (round 2 #4).
            flags.append("ratchet_seed_missing")
        if info.get("not_established"):
            flags.append("demand_months_not_established")
            block["demand_months_not_established"] = list(info["not_established"])
        if info.get("partial_months"):
            flags.append("demand_partial_months")
            block["demand_partial_months"] = list(info["partial_months"])
    elif wanted:
        block["demand_charge"] = None
        flags.append("demand_charge_not_established")
        if _lp.demand_all_newly_bound(n.meta.get(_lp.META_LINKS), wanted_items):
            flags.append("demand_recipe_changed")  # …because that recipe bound none

    cfg_now = None
    if commercial:
        try:
            cfg_now = _lp._parse(commercial)
        except Exception:  # noqa: BLE001
            cfg_now = None

    def drifted() -> None:
        flags.append("config_changed_since_solve" if commercial else
                     "config_cleared_since_solve")

    # Convex tiered energy (WP1.5c): Σ rate_k × q_k per month and period.
    tiers = n.meta.get(_lp.META_TIERS)
    if tiers:
        for v in tiers.values():
            amount = float(v["rate_eur_per_mwh"]) * float(v["q_mwh"])
            if amount:
                items.append(("energy_tiers", v.get("inv_period"), 0.0, amount))
        block["energy_tiers"] = weighted("energy_tiers")
        first_tier = next(iter(tiers.values()))
        solved_hash = first_tier.get("items_hash")
        recipe = _lp.energy_recipe_of(n.meta.get(_lp.META_LINKS))
        wanted_tiers = (_lp.tier_items_hash(cfg_now, _H.version_of(first_tier), recipe=recipe)
                        if cfg_now is not None else None)
        if solved_hash is not None and solved_hash != wanted_tiers:
            drifted()
        elif cfg_now is not None and _lp.energy_record_state(
                n, cfg_now, n.meta.get(_lp.META_LINKS)) == "recipe":
            flags.append("energy_recipe_changed")
    elif cfg_now is not None and _lp.tier_items_hash(cfg_now) is not None:
        # Tiers the config names but the solve did not bind (ADR-0001).
        block["energy_tiers"] = None
        flags.append("energy_tiers_not_established")
        if _lp.energy_record_state(n, cfg_now, n.meta.get(_lp.META_LINKS)) == "recipe":
            # …because the solve's recipe left windowed tiers out (WP2.1c-ii).
            flags.append("energy_recipe_changed")

    # Tariff capacity items (P2 WP2.1c-iii): contracted €/MW × p_nom_opt per
    # active period (a capex-like term, as the connection fee), and each
    # annual measured peak × its €/MW; a fixed PoC's charge is reported
    # OUTSIDE the reconciled total (the LP cannot change it).
    cap = n.meta.get(_lp.META_CAPACITY)
    wanted_cap = _lp.capacity_items_hash(cfg_now) if cfg_now is not None else None
    if cap:
        cap_items: list[tuple] = []
        known = True
        for item_id, c in (cap.get("contracted") or {}).items():
            link = c.get("link")
            if link in n.links.index and "p_nom_opt" in n.links.columns and \
                    np.isfinite(float(n.links.at[link, "p_nom_opt"])):
                size = float(n.links.at[link, "p_nom_opt"])
                for key, eur_per_mw in c.get("eur_per_mw_by_period", {}).items():
                    cap_items.append(("tariff_capacity", None if key == "_" else int(key),
                                      float(eur_per_mw) * size, 0.0))
            else:
                known = False
        for v in (cap.get("peaks") or {}).values():
            cap_items.append(("tariff_capacity", v.get("inv_period"), 0.0,
                              float(v["eur_per_mw"]) * float(v["peak_mw"])))
        if known:
            items.extend(cap_items)
        else:
            # A partly unknown term is unknown: nothing of it enters the
            # totals (ADR-0001; review #4).
            flags.append("tariff_capacity_not_established")
        if cap.get("contracted") or cap.get("peaks"):
            block["tariff_capacity"] = (None if "tariff_capacity_not_established" in flags
                                        else weighted("tariff_capacity"))
        if cap.get("fixed"):
            eur = sum(float(v) * (yrs(int(k)) if k != "_" else 1.0)
                      for c in cap["fixed"].values()
                      for k, v in (c.get("eur_by_period") or {}).items())
            block["tariff_capacity_fixed"] = {
                "eur": float(eur), "included_in_total": False,
                "flags": ["network_capacity_fixed", "fixed_charge_not_in_lp"]}
        if cap.get("items_hash") != (_lp.capacity_items_hash(cfg_now, _H.version_of(cap))
                                     if cfg_now is not None else None):
            drifted()
    elif wanted_cap is not None:
        block["tariff_capacity"] = None
        flags.append("tariff_capacity_not_established")
        if _lp.capacity_all_newly_bound(n.meta.get(_lp.META_LINKS)):
            flags.append("capacity_recipe_changed")  # the solve's recipe bound none

    # changes_dispatch PPAs (P2 WP2.2d): Σ w × p_gen × the committed €/MWh,
    # per period — in the objective, out of the statistics (the adder was
    # undone), so ADDED like the energy rows.
    ppa = n.meta.get(_lp.META_PPA)
    wanted_ppa = _lp.ppa_dispatch_hash(cfg_now) if cfg_now is not None else None
    if ppa:
        frame = n.generators_t.get(_lp.PPA_PRICE_ATTR)
        gens = ppa.get("generators") or []
        p = n.generators_t.p
        if frame is None or any(g not in frame.columns or g not in p.columns for g in gens) \
                or frame[gens].isna().any().any() or p[gens].isna().any().any():
            block["ppa_settlement"] = None
            flags.append("ppa_settlement_not_established")
        else:
            amount = (n.snapshot_weightings.objective * (p[gens] * frame[gens]).sum(axis=1))
            per = (amount.groupby(level=0).sum().items()
                   if isinstance(n.snapshots, pd.MultiIndex) else [(None, amount.sum())])
            for period, v in per:
                items.append(("ppa_settlement", None if period is None else int(period), 0.0,
                              float(v)))
            block["ppa_settlement"] = weighted("ppa_settlement")
        if ppa.get("hash") != (_lp.ppa_dispatch_hash(cfg_now, _H.version_of(ppa))
                               if cfg_now is not None else None):
            drifted()
    elif wanted_ppa is not None:
        block["ppa_settlement"] = None
        flags.append("ppa_settlement_not_established")
        rec = n.meta.get(_lp.META_LINKS) or {}
        if rec and int(rec.get("lp_recipe") or 1) < _lp.PPA_DISPATCH_RECIPE:
            flags.append("ppa_recipe_changed")  # the solve's recipe bound no dispatch PPA
        elif rec:
            drifted()  # this recipe binds it, so the PPA came after the solve (review #7)

    # A net cost energy item on a multi-member group (P3 WP3.3b): priced once
    # on the group's net import — Σ w × price × max(0, Σ members − export)
    # from the dispatch, per period (never the LP's degenerate split).
    net = n.meta.get(_lp.META_GROUP_NET)
    wanted_net = _lp.group_net_hash(cfg_now) if cfg_now is not None else None
    if net:
        amounts = _lp.group_net_amounts(n)
        if amounts is None:
            block["energy_net_group"] = None
            flags.append("energy_net_group_not_established")
        else:
            for period, v in amounts.items():
                if v:
                    items.append(("energy_net_group", period, 0.0, v))
            block["energy_net_group"] = weighted("energy_net_group")
        if net.get("items_hash") != (_lp.group_net_hash(cfg_now, _H.version_of(net))
                                     if cfg_now is not None else None):
            drifted()
    elif wanted_net is not None:
        block["energy_net_group"] = None
        flags.append("energy_net_group_not_established")
        if n.meta.get(_lp.META_LINKS):
            drifted()   # no solve before recipe 6 could carry it: it came after the solve

    # Energy-hub group contract (WP1.6): no money of its own; the members'
    # shares of the group's import energy are reported (allocation is P3).
    def comparable(spec: dict | None) -> dict | None:
        return None if not spec else {"name": spec.get("name"),
                                      "members": sorted(spec.get("members") or []),
                                      "cap_mw": spec.get("cap_mw")}

    group = n.meta.get(_lp.META_GROUP)
    wanted_group = _lp.group_spec(cfg_now) if cfg_now is not None else None
    if group:
        block["group"] = {k: group.get(k) for k in ("name", "members", "cap_mw", "energy_share")}
        if comparable(wanted_group) != comparable(group):
            drifted()
        shares = group.get("energy_share")
        if shares is None or any(v is None for v in shares.values()):
            flags.append("group_energy_share_not_established")
    elif wanted_group is not None and n.meta.get(_lp.META_LINKS):
        drifted()  # a group the solve did not bind

    # Connection capacity fee in the LP (WP1.4a).
    fee = n.meta.get(_conn.META_FEE)
    agreement = None
    if commercial:
        try:
            agreement = _lp._parse(commercial).connection
        except Exception:  # noqa: BLE001
            agreement = None
    wants_fee = (agreement is not None and agreement.kind == "firm"
                 and agreement.capacity_fee is not None)
    if fee:
        link = fee.get("link")
        if link in n.links.index and "p_nom_opt" in n.links.columns:
            p_nom_opt = float(n.links.at[link, "p_nom_opt"])
            total = 0.0
            for key, eur_per_mw in fee.get("eur_per_mw_by_period", {}).items():
                period = None if key == "_" else int(key)
                amount = float(eur_per_mw) * p_nom_opt
                items.append(("network_capacity", period, amount, 0.0))
                total += amount
            block["network_capacity"] = weighted("network_capacity")
        else:
            block["network_capacity"] = None
            flags.append("network_capacity_not_established")
        stale = (fee.get("agreement_hash") is not None
                 and fee["agreement_hash"] != _conn.agreement_hash(agreement,
                                                                   _H.version_of(fee)))
        if not wants_fee or stale:
            flags.append("config_changed_since_solve" if commercial else
                         "config_cleared_since_solve")
    elif wants_fee:
        block["network_capacity"] = None
        flags.append("network_capacity_not_established")

    # The agreement itself (fee or not) — its cap and availability shaped the
    # dispatch, so an edit without a re-solve is drift (P2 WP2.0).
    solved_agreement = n.meta.get(_conn.META_AGREEMENT)
    if solved_agreement and solved_agreement.get("agreement_hash") != \
            _conn.agreement_hash(agreement, _H.version_of(solved_agreement)):
        flags.append("config_changed_since_solve" if commercial else
                     "config_cleared_since_solve")
    elif not solved_agreement and agreement is not None and \
            (n.meta.get(_lp.META_LINKS) or {}).get("agreement_recorded"):
        # An agreement the solve did not bind. Only for solves that record
        # agreements: a P1 solve never wrote `ic_connection` (upgrade path).
        flags.append("config_changed_since_solve")

    # Fixed connection fee — reported, NOT in the reconciled total (§5.5).
    fixed = n.meta.get(_conn.META_FIXED_FEE)
    if fixed:
        by_p = fixed.get("eur_by_period") or {"_": fixed.get("eur", 0.0)}
        eur = sum(float(v) * (yrs(int(k)) if k != "_" else 1.0) for k, v in by_p.items())
        if fixed.get("agreement_hash") is not None and \
                fixed["agreement_hash"] != _conn.agreement_hash(agreement,
                                                                _H.version_of(fixed)):
            flags.append("config_changed_since_solve" if commercial else
                         "config_cleared_since_solve")
        block["network_capacity_fixed"] = {
            "eur": float(eur), "kind": fixed.get("kind"),
            "included_in_total": False, "flags": ["fixed_charge_not_in_lp"]}

    # Documented order: sorted and unique, so consumers can compare lists (P2 WP2.0).
    block["flags"] = sorted(set(flags))
    if len(block) == 2 and not items:  # only included_in_total + empty flags
        return {"items": [], "block": None, "flags": []}
    return {"items": items, "block": block, "flags": block["flags"]}

"""
Preflight findings for the commercial layer (Edge Investment Case P1 WP1.8).

`commercial_findings(n, commercial)` returns `(severity, code, component_class,
name, message)` tuples; `validation_service._check_commercial` turns them into
`Issue`s. Nothing here mutates the network. The bindings are evaluated the way
the solve would evaluate them, so a preflight error is the solve's own refusal,
stated before any solver time is spent.

  * `commercial.binding_invalid` (error): the config cannot bind: a PoC, export
    or group Link that is missing or two-way, a bare Library tariff id, an
    export item with no export Link, an unrated snapshot, a missing or stale
    export price or envelope, a tariff on a non-datetime axis, and the
    refusals that depend on the strategy that will run (demand, tiers or a
    capacity fee with a rolling or multi-period myopic solve).
  * `commercial.arbitrage_loop` (warning): in some snapshots exporting pays
    more than importing costs, net of the import Link's efficiency, so the LP
    can lower its cost by circulating power through the grid (both PoC Links
    at once). Read on the MATERIALISED prices: the Links' base cost, the
    tariff adders, the first convex tier and the export price.
  * `commercial.arbitrage_loop_via_storage` (warning, IC U1 f): no single
    snapshot pays, but storage behind the meter can carry the cheapest import
    to the dearest export: max(export credit) × η_import × η_round_trip −
    min(import cost) > 0 (GS gate S3 [S2], ported).
  * `commercial.demand_resolution` (warning): a demand item's interval is finer
    than the snapshot step, so the LP and the bill measure a peak the axis
    cannot resolve (spec §5.5 gap cause `resolution`).
  * `commercial.demand_partial_months` (warning): months charged a full
    demand charge against part of a month's operation (spec §5.2).
  * `commercial.tariff_out_of_validity` (warning): modelled dates (or a later
    investment period's year) outside the tariff's `valid_from` / `valid_to`.
  * `commercial.group_fee_bypass` (warning): a capacity fee on `poc_link`
    while another group member is extendable (it takes capacity fee-free).
  * `commercial.meter_bypass` (warning, P2 WP2.2c round 2): a grid-side bus
    of the meter is reachable from the site without the import or export Link.
  * `commercial.preflight_incomplete` (warning): a check after binding raised;
    validation still returns, the solve applies its own checks.

The PPA and DR-contract double-count checks need P2's contracts (plan WP1.8
deviation).

WITHOUT a commercial config (owner decision 10, IC U1 f): `network_findings(n)`
is the guided study's tariff sanity check (`validation_service.
_check_export_cycling` on the GS branch, ported; GS removes its copy in U2),
read on the Links' own `marginal_cost` for every pair of Links that join the
same two buses in opposite directions, with GS's codes:

  * `tariff_export_exceeds_import` (warning): in some snapshot
    −mc_back × efficiency_forward − mc_forward > 0, for either orientation of
    the pair (a raw pair does not say which Link imports, so the check is
    conservative: it may flag a cycle the LP would not exploit, never miss
    one; the commercial path knows the roles and nets η_import alone);
  * `tariff_export_exceeds_import_via_storage` (warning): no snapshot pays,
    but storage on the pair's site bus carries a cheap hour to a dear one.

`CYCLING_CODES` lists the four cycling codes; `cycling_flags(issues)` picks
them out (GS's `export_cycling_flags`, for the decision study's filter).

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from services.commercial import lp_bindings as _lp

_SETTLE_H = {"15min": 0.25, "30min": 0.5, "h": 1.0}
# Steps longer than this are gaps between sampled stretches (representative
# weeks), not the axis resolution.
_GAP_H = 24.0


def _max_step_h(snapshots: pd.Index) -> float | None:
    """The coarsest in-period step, gaps excluded (review #5: a median hides a
    coarse stretch of a mixed axis)."""
    if isinstance(snapshots, pd.MultiIndex):
        groups = [pd.DatetimeIndex(snapshots[snapshots.get_level_values(0) == p]
                                   .get_level_values(-1))
                  for p in snapshots.get_level_values(0).unique()]
    else:
        groups = [pd.DatetimeIndex(snapshots)]
    steps: list[float] = []
    for ts in groups:
        if len(ts) > 1:
            steps.extend(np.diff(ts.asi8) / 3.6e12)  # ns → h: a unit, not a step
    positive = [d for d in steps if d > 0]
    within = [d for d in positive if d <= _GAP_H]
    if within:
        return max(within)
    # Every step is longer than the gap bound: that IS the axis (round 2 C).
    return min(positive) if positive else None


def demand_resolution_warnings(n, cfg) -> list[tuple[str, str]]:
    """(item id, message) for each demand item whose settlement interval is
    finer than the axis step — shared with the billing gap's `resolution_risk`
    (P2 WP2.3)."""
    step_h = _max_step_h(n.snapshots)
    items = cfg.import_tariff.items if cfg.import_tariff is not None else []
    return [(item.id,
             f"Demand item {item.id!r} is measured on {item.settlement} intervals but "
             f"part of the axis is {step_h:g} h apart: the peak within an interval is "
             "not resolved, so the modelled demand charge can understate the bill.")
            for item in items
            if _lp._is_demand(item) and step_h is not None
            and step_h > _SETTLE_H[item.settlement] + 1e-9]


def commercial_findings(n, commercial, *, solve_strategy: str = "full",
                        multi_period: bool = False, dsr: dict | None = None
                        ) -> list[tuple[str, str, str, str, str]]:
    """`solve_strategy` is the strategy that will RUN
    (`lp_bindings.effective_strategy`), so a refusal that depends on it is
    stated here too (review #1)."""
    if not commercial:
        return []
    try:
        cfg = _lp._parse(commercial)
        _lp.validate_for_network(n, cfg)
        adders, _items, _nil, _notes = _lp._adders(n, cfg)
        price = _lp._export_price(n, cfg) if cfg.export_price_ref is not None else None
        demand, _ids, _missing, _dnotes = _lp._demand_spec(n, cfg)
        tier_spec, _tiered, _nonconvex = _lp._tier_spec(n, cfg)
        _lp.refuse_windowed_terms(demand, tier_spec, solve_strategy, multi_period,
                                  _lp._capacity_spec(n, cfg))
        if cfg.connection is not None:
            from services.commercial import connection as conn

            conn._validate(n, cfg.connection, cfg.poc_link, cfg.export_link, solve_strategy,
                           multi_period)
    except Exception as exc:  # noqa: BLE001 — every refusal the solve would make
        # A refusal that names its own code (`commercial.<code>: …`) keeps it.
        for code in ("commercial.capacity_double_count", "commercial.contract_asset_missing",
                     "commercial.contract_tariff_mismatch"):
            if str(exc).startswith(code):
                return [("error", code, "", "", str(exc))]
        return [("error", "commercial.binding_invalid", "", "",
                 f"The commercial config cannot bind to this network: {exc}")]
    try:
        return _warnings(n, cfg, adders, price, demand) + _contract_warnings(n, cfg, dsr or {})
    except Exception as exc:  # noqa: BLE001 — preflight never takes validation down
        return [("warning", "commercial.preflight_incomplete", "", "",
                 f"Some commercial preflight checks could not run ({type(exc).__name__}: "
                 f"{exc}); the solve applies its own checks.")]


def _warnings(n, cfg, adders, price, demand) -> list[tuple[str, str, str, str, str]]:
    out: list[tuple[str, str, str, str, str]] = []
    # Netted of η_import, never gated on the efficiency-blind count: with an
    # export FEE, η < 1 raises the gain (review round 1, item 1).
    loops = _materialised_loops(n, cfg, adders, price) if cfg.export_link is not None else 0
    if loops:
        members = _lp.import_links(cfg)
        out.append(("warning", "commercial.arbitrage_loop", "Link", cfg.export_link,
                    f"In {loops} snapshot(s) exporting through {cfg.export_link!r} pays more "
                    f"than importing through {members[0] if len(members) == 1 else members} "
                    "costs, net of the import Link's efficiency: the LP can lower its cost by "
                    "importing and exporting at once. Check the export price and the import "
                    "tariff, or add a cost on the export Link."))
    elif cfg.export_link is not None:
        cross = _materialised_cross_interval(n, cfg, adders, price)
        if cross is not None:
            out.append(cross)

    fee = cfg.connection.capacity_fee if cfg.connection is not None else None
    if fee is not None and cfg.group_members:
        free = [m for m in cfg.group_members
                if m != cfg.poc_link and bool(n.links.at[m, "p_nom_extendable"])]
        if free:
            out.append(("warning", "commercial.group_fee_bypass", "Link", free[0],
                        f"The connection capacity fee is charged on {cfg.poc_link!r} only; "
                        f"extendable group member(s) {free} can take capacity without it. "
                        "Fix their capacity or model their fee on the Link's capital_cost."))

    for item_id, msg in demand_resolution_warnings(n, cfg):
        out.append(("warning", "commercial.demand_resolution", "", item_id, msg))
    partial = (demand or {}).get("info", {}).get("partial_months", [])
    if partial:
        out.append(("warning", "commercial.demand_partial_months", "", "",
                    f"Months {', '.join(partial)} are charged a full monthly demand charge "
                    "against only part of the month's operation (spec §5.2), which over-weights "
                    "peak shaving against energy. Model whole months to avoid it."))

    tariff = cfg.import_tariff
    if tariff is not None and len(n.snapshots):
        local = _lp._local_clock(n.snapshots, cfg.timezone)
        first, last = local.min().date(), local.max().date()
        if isinstance(n.snapshots, pd.MultiIndex):
            # The periods decide which tariff years are modelled; their
            # timestamps are often a weather year (review #7, round 2 B).
            off = [int(p) for p in n.snapshots.get_level_values(0).unique()
                   if int(p) < tariff.valid_from.year
                   or (tariff.valid_to is not None and int(p) > tariff.valid_to.year)]
            where = f"Investment period(s) {', '.join(map(str, off))}" if off else None
        else:
            outside = first < tariff.valid_from or (tariff.valid_to is not None
                                                    and last > tariff.valid_to)
            where = f"The modelled dates {first}..{last}" if outside else None
        if where:
            out.append(("warning", "commercial.tariff_out_of_validity", "", tariff.id,
                        f"{where} fall outside tariff {tariff.id!r}'s validity "
                        f"{tariff.valid_from}..{tariff.valid_to or 'open'}."))
    return out


def _contract_warnings(n, cfg, dsr: dict) -> list[tuple[str, str, str, str, str]]:
    """Contract findings that do not stop the solve (P2 WP2.2c; closes P1
    WP1.8's deviation): settlement-only contracts naming what the network
    cannot settle (warnings here, refused at binding), and double counts."""
    out: list[tuple[str, str, str, str, str]] = []
    bypass = _lp.meter_bypass_buses(n, cfg)
    if bypass:
        # Power through such a connection is neither billed nor settled, and
        # the grid side's generators are not on-site (review 2.2c round 2 #2).
        out.append(("warning", "commercial.meter_bypass", "Bus", bypass[0],
                    f"Bus(es) {bypass} on the grid side of the commercial meter are reachable "
                    "from the site without passing the import or export Link: power through "
                    "that connection is not billed, and generators beyond it are not treated "
                    "as on-site."))
    for code, msg, dispatch in _lp.contract_problems(n, cfg):
        if not dispatch:
            out.append(("warning", code, "", "", f"{msg}: its settlement is not established"))
    items = cfg.import_tariff.items if cfg.import_tariff is not None else []
    earns_export = cfg.export_link is not None and (
        cfg.export_price_ref is not None
        or any(i.measured_on in ("export", "net") for i in items))
    onsite = set(_lp.site_generators(n, cfg))
    commodity = [i.id for i in items
                 if i.kind in ("energy", "certificate") and i.measured_on != "export"]
    load_bus = n.loads["bus"].astype(str).to_dict() if not n.loads.empty else {}
    active_dsr = ({str(b) for b in dsr.get("buses") or []}
                  if (dsr.get("price") or 0) > 0 and (dsr.get("share") or 0) > 0 else set())
    meter = set(_lp.import_links(cfg)) | ({cfg.export_link} if cfg.export_link else set())
    for c in cfg.contracts:
        if c.type == "ppa" and earns_export and not c.changes_dispatch \
                and _lp.same_party(c.seller, cfg.site_party):
            sold = [a for a in c.asset_ids if a in onsite]
            if sold:
                out.append(("warning", "commercial.ppa_export_double_count", "Generator",
                            sold[0],
                            f"The site ({cfg.site_party!r}) sells PPA {c.id!r}'s output of "
                            f"{sold} behind the meter, and its exported share also earns the "
                            f"export price through {cfg.export_link!r}: the same MWh is paid "
                            "twice. Settle one of them, or let the PPA change dispatch."))
        if c.type == "dr":
            # DR activation is settled on the DSR dispatch of its loads' buses
            # (WP2.2b): a DR load on a bus without active DSR can never settle
            # (review 2.2c #1). The LP dispatches DSR at the DSR price without
            # the contract's revenue (settled afterwards; P5 co-optimises).
            off = sorted({load_bus.get(l) for l in c.load_ids if l in load_bus} - active_dsr)
            if off:
                out.append(("warning", "commercial.dr_without_dsr", "Bus", off[0],
                            f"DR contract {c.id!r} settles its activation on the DSR dispatch "
                            f"of its loads' buses, but {off} have no active demand response "
                            "(opt the bus in, with a DSR price and share above 0): the "
                            "activation is not established."))
        if c.type == "ppa" and c.kind == "sleeved" and commodity:
            out.append(("warning", "commercial.sleeved_commodity_double_count", "", c.id,
                        f"Sleeved PPA {c.id!r} buys the commodity, and the import tariff's "
                        f"per-kWh item(s) {commodity} may charge it again on PoC import. "
                        "A tariff item does not say whether it is a commodity or a network / "
                        "levy charge: remove the commodity (and certificate) share and keep "
                        "network and levy items."))
        if c.type == "eaas":
            on_meter = [a for a in c.asset_ids if a in meter]
            if on_meter:
                out.append(("warning", "commercial.eaas_on_poc", "Link", on_meter[0],
                            f"EaaS contract {c.id!r} bills delivery on {on_meter}, a PoC / "
                            "export Link: every MWh the site imports would count as "
                            "delivered. Name the asset the provider operates."))
    return out


# ── Import-to-export cycling (IC U1 f; owner decision 10) ──────────────────

#: Every cycling code, commercial path then raw path. A decision study keeps
#: these warnings (and discloses them); `cycling_flags` picks them out.
CYCLING_CODES = ("tariff_export_exceeds_import", "tariff_export_exceeds_import_via_storage",
                 "commercial.arbitrage_loop", "commercial.arbitrage_loop_via_storage")
_GAIN_EPS = 1e-9


def cycling_flags(issues) -> list[str]:
    """The cycling codes among `issues` (anything with a `.code`), once each,
    in `CYCLING_CODES` order."""
    found = {getattr(i, "code", None) for i in issues}
    return [c for c in CYCLING_CODES if c in found]


def _num(row, col: str, default: float) -> float:
    try:
        v = float(row.get(col, default))
    except (TypeError, ValueError):
        return default
    return default if v != v else v


def _site_storage(n, buses) -> tuple[str, float] | None:
    """The storage on `buses` that can hold energy from one snapshot to
    another, as `(name, round-trip efficiency)`, the most efficient first. A
    StorageUnit counts when it has (or may build) power and has hours; a Store
    when it has (or may build) energy, at round trip 1.0 (its losses sit on
    Links this check does not walk). Inactive rows are skipped; standing
    losses are ignored, which can only make the check flag more."""
    buses = {str(b) for b in buses}
    best: tuple[str, float] | None = None

    def on_buses(df):
        if df is None or not len(df) or "bus" not in df.columns:
            return None
        rows = df[df["bus"].astype(str).isin(buses)]
        if "active" in rows.columns:
            rows = rows[rows["active"].astype(bool)]
        return rows

    su = on_buses(getattr(n, "storage_units", None))
    if su is not None:
        for name, row in su.iterrows():
            can = bool(row.get("p_nom_extendable", False)) or _num(row, "p_nom", 0.0) > 0
            if not can or _num(row, "max_hours", 0.0) <= 0:
                continue
            eta = _num(row, "efficiency_store", 1.0) * _num(row, "efficiency_dispatch", 1.0)
            if best is None or eta > best[1]:
                best = (str(name), eta)
    st = on_buses(getattr(n, "stores", None))
    if st is not None:
        for name, row in st.iterrows():
            if bool(row.get("e_nom_extendable", False)) or _num(row, "e_nom", 0.0) > 0:
                if best is None or 1.0 > best[1]:
                    best = (str(name), 1.0)
    return best


# ── with a commercial config: the materialised prices ─────────────────────


def _materialised(n, cfg, adders, price) -> tuple[dict[str, np.ndarray], np.ndarray,
                                                  dict[str, np.ndarray]]:
    """({import link: €/MWh at bus0}, export €/MWh at bus0 (negative = a
    credit), {import link: efficiency}) as the solve will price them: the
    base cost + the adders (+ the first convex tier on import) − the export
    price, i.e. what `lp_bindings.apply` writes on `marginal_cost`."""
    members = _lp.import_links(cfg)

    def dense(attr, links):
        return n.get_switchable_as_dense("Link", attr, inds=pd.Index(links))

    mc = dense("marginal_cost", members + [cfg.export_link])
    eff = dense("efficiency", members)
    floor = _lp.tier_floor_eur_per_mwh(cfg)
    imp = {m: mc[m].to_numpy(dtype=float) + adders["import"] + floor for m in members}
    exp = mc[cfg.export_link].to_numpy(dtype=float) + adders["export"]
    if price is not None:
        exp = exp - price
    return imp, exp, {m: eff[m].to_numpy(dtype=float) for m in members}


def _materialised_loops(n, cfg, adders, price) -> int:
    """Snapshots where η_import × export credit − import cost > 0 through any
    import Link."""
    imp, exp, eff = _materialised(n, cfg, adders, price)
    gain = np.max([-exp * eff[m] - imp[m] for m in imp], axis=0)
    return int((gain > _GAIN_EPS).sum())


def _returning_buses(n, cfg, site: set[str]) -> set[str]:
    """The site buses (behind the meter) that electricity can reach AND come
    back from: reachable from an electric site bus (`lp_bindings.
    _electric_bus_test`) along the flow directions, and able to reach one.
    Lines and transformers run both ways; a Link runs bus0 → bus_k (bus_k →
    bus0 for a port with a negative static efficiency), both ways when its
    `p_min_pu` < 0; the meter Links are not walked. A heat tank behind a heat
    pump, or an H2 Store behind an electrolyser with no fuel cell, is not on
    such a bus; a battery Store behind a charger and a discharger is (review
    round 1, B2). Static attributes only."""
    electric = _lp._electric_bus_test(n, cfg)
    meter = set(_lp.import_links(cfg)) | ({cfg.export_link} if cfg.export_link else set())
    fwd: dict[str, set[str]] = {}
    back: dict[str, set[str]] = {}

    def edge(a, b):
        if a in site and b in site and a != b:
            fwd.setdefault(a, set()).add(b)
            back.setdefault(b, set()).add(a)

    for comp in ("lines", "transformers"):
        df = getattr(n, comp)
        for b0, b1 in zip(df["bus0"].astype(str), df["bus1"].astype(str)):
            edge(b0, b1)
            edge(b1, b0)
    links = n.links
    for name in links.index:
        if name in meter:
            continue
        b0 = str(links.at[name, "bus0"])
        two_way = float(links.at[name, "p_min_pu"]) < 0
        for k in (1, 2, 3, 4):
            col = "bus1" if k == 1 else f"bus{k}"
            if col not in links.columns:
                continue
            bk = str(links.at[name, col]).strip()
            if not bk or bk == "nan":
                continue
            ecol = "efficiency" if k == 1 else f"efficiency{k}"
            eff = float(links.at[name, ecol]) if ecol in links.columns else 1.0
            src, dst = (bk, b0) if eff < 0 else (b0, bk)
            edge(src, dst)
            if two_way:
                edge(dst, src)

    def reach(starts, graph):
        seen, todo = set(), list(starts)
        while todo:
            b = todo.pop()
            if b in seen:
                continue
            seen.add(b)
            todo.extend(graph.get(b, ()))
        return seen

    elec = {b for b in site if electric(b)}
    return reach(elec, fwd) & reach(elec, back)


def _materialised_cross_interval(n, cfg, adders, price):
    """`commercial.arbitrage_loop_via_storage`, or None: storage BEHIND the
    meter on a bus electricity can come back from (`_returning_buses`; the
    losses of the Links between are ignored, so this flags more, never less).
    For each import Link m the gain is

        max_t(η_rt × max(export credit) × η_m[t] − import cost_m[t]),

    the import Link's efficiency read at the IMPORT snapshot (review round 1,
    item 2). Interval order is not checked (a cyclic state of charge makes it
    irrelevant; an acyclic one only narrows the gain)."""
    site, _bypass = _lp._meter_sides(n, cfg)
    store = _site_storage(n, _returning_buses(n, cfg, site))
    if store is None:
        return None
    name, eta = store
    imp, exp, eff = _materialised(n, cfg, adders, price)
    credit = -exp
    best = int(np.argmax(credit))
    per = {m: eta * credit[best] * eff[m] - imp[m] for m in imp}
    member = max(per, key=lambda m: float(per[m].max()))
    cheap = int(np.argmax(per[member]))
    gain = float(per[member][cheap])
    if not gain > _GAIN_EPS:
        return None
    return ("warning", "commercial.arbitrage_loop_via_storage", "Link", member,
            f"No single snapshot pays, but with storage {name!r} behind the meter, energy "
            f"imported through {member!r} ({imp[member][cheap]:,.2f} per MWh at "
            f"{n.snapshots[cheap]}) and exported through {cfg.export_link!r} at the highest "
            f"credit ({credit[best]:,.2f} per MWh, first at {n.snapshots[best]}), after the "
            f"import Link's efficiency and the storage's round trip "
            f"({float(eff[member][cheap]) * eta:.2f}), earns up to {gain:,.2f} per MWh, and "
            "the LP will do it. Check the export price against the tariff's energy rates; "
            "if the tariff really pays this, the result is real.")


# ── without a commercial config: the Links' own marginal cost ─────────────


def _reverse_pairs(links) -> list[tuple[str, str, str, str]]:
    """(a, b, bus0, bus1) for each pair of Links joining the same two buses in
    opposite directions, from the static bus columns only, so a network with
    no reverse pair densifies nothing (GS gate S3 BC-S3-1)."""
    by_buses: dict[tuple[str, str], list[str]] = {}
    for name, b0, b1 in zip(links.index, links["bus0"].astype(str),
                            links["bus1"].astype(str)):
        by_buses.setdefault((b0, b1), []).append(name)
    pairs: list[tuple[str, str, str, str]] = []
    seen: set[frozenset] = set()
    for (b0, b1), forward in by_buses.items():
        for a in forward:
            for b in by_buses.get((b1, b0), []):
                key = frozenset((a, b))
                if a == b or key in seen:
                    continue
                seen.add(key)
                pairs.append((a, b, b0, b1))
    return pairs


def _storage_by_bus(n) -> dict[str, tuple[str, float]]:
    """{bus: (name, round trip)} of the best storage on each bus, built ONCE
    per call (review round 1, B4) with `_site_storage`'s rules and tie order:
    StorageUnits in row order (a strictly higher round trip wins), then a
    Store (round trip 1.0) only where nothing reaches 1.0."""
    best: dict[str, tuple[str, float]] = {}

    def rows(df):
        if df is None or not len(df) or "bus" not in df.columns:
            return None
        if "active" in df.columns:
            df = df[df["active"].astype(bool)]
        return df

    def col(df, name, default):
        if name not in df.columns:
            return np.full(len(df), default, dtype=float)
        return pd.to_numeric(df[name], errors="coerce").fillna(default).to_numpy(dtype=float)

    su = rows(getattr(n, "storage_units", None))
    if su is not None and len(su):
        ext = (su["p_nom_extendable"].astype(bool).to_numpy() if "p_nom_extendable" in su.columns
               else np.zeros(len(su), dtype=bool))
        ok = (ext | (col(su, "p_nom", 0.0) > 0)) & (col(su, "max_hours", 0.0) > 0)
        eta = col(su, "efficiency_store", 1.0) * col(su, "efficiency_dispatch", 1.0)
        for name, bus, e in zip(su.index[ok], su["bus"].astype(str).to_numpy()[ok], eta[ok]):
            if bus not in best or e > best[bus][1]:
                best[bus] = (str(name), float(e))
    st = rows(getattr(n, "stores", None))
    if st is not None and len(st):
        ext = (st["e_nom_extendable"].astype(bool).to_numpy() if "e_nom_extendable" in st.columns
               else np.zeros(len(st), dtype=bool))
        ok = ext | (col(st, "e_nom", 0.0) > 0)
        for name, bus in zip(st.index[ok], st["bus"].astype(str).to_numpy()[ok]):
            if bus not in best or 1.0 > best[bus][1]:
                best[bus] = (str(name), 1.0)
    return best


def _raw_cross_hour(snapshots, stats, eff, storage, imp: str, exp: str, site: str):
    """`tariff_export_exceeds_import_via_storage` for one direction of a pair,
    or None: max_t(−mc_exp) × η_imp × η_round_trip − min_t(mc_imp) > 0 with
    storage on `site` (GS `_cross_hour_cycling`, ported). `stats[link]` is
    (max credit, its first index, min cost, its first index), NaN-skipping
    like pandas."""
    store = storage.get(str(site))
    if store is None:
        return None
    name, eta = store
    best_credit, i_credit, _, _ = stats[exp]
    _, _, cheapest, i_cheap = stats[imp]
    gain = best_credit * float(eff[imp]) * eta - cheapest
    if not gain > _GAIN_EPS:
        return None
    return ("warning", "tariff_export_exceeds_import_via_storage", "Link", str(imp),
            f"Links '{imp}' (import) and '{exp}' (export) with storage '{name}' "
            f"at '{site}': no single hour pays, but the highest export credit "
            f"({best_credit:,.2f} per MWh, first at {snapshots[i_credit]}) after the "
            f"import link's efficiency and the storage's round trip "
            f"({float(eff[imp]) * eta:.2f}) exceeds the cheapest import price "
            f"({cheapest:,.2f}, first at {snapshots[i_cheap]}). Charging from the "
            f"grid and exporting later would earn up to {gain:,.2f} per MWh, and "
            "the LP will do it. Check the tariff's export price against its "
            "energy bands; if the tariff really pays this, the result is real.")


def network_findings(n) -> list[tuple[str, str, str, str, str]]:
    """The cycling check for a network WITHOUT a commercial config (owner
    decision 10): for every reverse pair of Links (a site's grid import and
    export), warn when moving a MWh out through one and back through the
    other EARNS money in some snapshot, after the forward Link's efficiency:

        −mc_back[t] × efficiency_forward − mc_forward[t] > 0

    (`tariff_export_exceeds_import`). A pair no single snapshot flags is
    checked again ACROSS snapshots when storage sits at its site end
    (`tariff_export_exceeds_import_via_storage`). Warnings, not errors: some
    contracts do pay export above import, and the user decides. Ported from
    the GS branch's `validation_service._check_export_cycling`; the same
    `(severity, code, component_class, name, message)` tuples as
    `commercial_findings`. Never raises (`commercial.preflight_incomplete`)."""
    try:
        return _network_findings(n)
    except Exception as exc:  # noqa: BLE001 — preflight never takes validation down
        return [("warning", "commercial.preflight_incomplete", "", "",
                 f"The import-to-export cycling check could not run ({type(exc).__name__}: "
                 f"{exc}); the solve is not affected.")]


def _network_findings(n) -> list[tuple[str, str, str, str, str]]:
    links = getattr(n, "links", None)
    if links is None or len(links) < 2 or not {"bus0", "bus1"} <= set(links.columns):
        return []
    pairs = _reverse_pairs(links)
    if not pairs:
        return []
    paired = pd.Index(sorted({x for a, b, _, _ in pairs for x in (a, b)}))
    try:
        mc = n.get_switchable_as_dense("Link", "marginal_cost", inds=paired)
    except Exception:  # noqa: BLE001 — GS: a frame that cannot be built finds nothing
        return []
    eff = (links["efficiency"] if "efficiency" in links.columns
           else pd.Series(1.0, index=links.index)).astype(float).fillna(1.0)
    # Every pair at once (review round 1, B4): a (snapshots × pairs) gain.
    m = mc.to_numpy(dtype=float)
    at = {name: i for i, name in enumerate(mc.columns)}
    ia = np.array([at[a] for a, _, _, _ in pairs])
    ib = np.array([at[b] for _, b, _, _ in pairs])
    ea = eff.reindex([a for a, _, _, _ in pairs]).to_numpy(dtype=float)
    eb = eff.reindex([b for _, b, _, _ in pairs]).to_numpy(dtype=float)
    with np.errstate(invalid="ignore"):
        gain = np.maximum(-m[:, ib] * ea - m[:, ia], -m[:, ia] * eb - m[:, ib])
        hit = gain > _GAIN_EPS
    counts = hit.sum(axis=0)
    first = hit.argmax(axis=0)
    storage: dict | None = None
    stats: dict[str, tuple[float, int, float, int]] = {}

    def link_stats(name):
        if name not in stats:
            col = m[:, at[name]]
            credit = np.where(np.isnan(col), -np.inf, -col)
            cost = np.where(np.isnan(col), np.inf, col)
            ic, ip = int(np.argmax(credit)), int(np.argmin(cost))
            stats[name] = (float(credit[ic]), ic, float(cost[ip]), ip)
        return stats[name]

    out: list[tuple[str, str, str, str, str]] = []
    for k, (a, b, b0, b1) in enumerate(pairs):
        if not counts[k]:
            # No snapshot pays on its own; storage at either end may still
            # carry a cheap import hour to a dear export hour.
            if storage is None:
                storage = _storage_by_bus(n)
            if not storage:
                continue
            link_stats(a), link_stats(b)
            for imp, exp, site in ((a, b, b1), (b, a, b0)):
                issue = _raw_cross_hour(mc.index, stats, eff, storage, imp, exp, site)
                if issue is not None:
                    out.append(issue)
                    break
            continue
        top = float(gain[:, k].max())   # numpy's max, as GS reports it
        out.append(("warning", "tariff_export_exceeds_import", "Link", str(a),
                    f"Links '{a}' ({b0}→{b1}) and '{b}' ({b1}→{b0}): the "
                    f"export price exceeds the import price in "
                    f"{int(counts[k])} snapshot(s) (first {mc.index[first[k]]}, up to "
                    f"{top:,.2f} per MWh), so cycling energy "
                    "out and back in would pay and the LP will do it. Check "
                    "the tariff's export price against its energy bands."))
    return out

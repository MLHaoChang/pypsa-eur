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
    more than importing costs, so the LP can lower its cost by circulating
    power through the grid (both PoC Links at once).
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
    loops = _lp.circulation_risk_snapshots(n, cfg, adders, price)
    if loops:
        members = _lp.import_links(cfg)
        out.append(("warning", "commercial.arbitrage_loop", "Link", cfg.export_link,
                    f"In {loops} snapshot(s) exporting through {cfg.export_link!r} pays more "
                    f"than importing through {members[0] if len(members) == 1 else members} "
                    "costs: the LP can lower its cost by importing and exporting at once. "
                    "Check the export price and the import tariff, or add a cost on the export "
                    "Link. (Link efficiencies are not considered in this check.)"))

    fee = cfg.connection.capacity_fee if cfg.connection is not None else None
    if fee is not None and cfg.group_members:
        free = [m for m in cfg.group_members
                if m != cfg.poc_link and bool(n.links.at[m, "p_nom_extendable"])]
        if free:
            out.append(("warning", "commercial.group_fee_bypass", "Link", free[0],
                        f"The connection capacity fee is charged on {cfg.poc_link!r} only; "
                        f"extendable group member(s) {free} can take capacity without it. "
                        "Fix their capacity or model their fee on the Link's capital_cost."))

    step_h = _max_step_h(n.snapshots)
    items = cfg.import_tariff.items if cfg.import_tariff is not None else []
    for item in items:
        if _lp._is_demand(item) and step_h is not None and \
                step_h > _SETTLE_H[item.settlement] + 1e-9:
            out.append(("warning", "commercial.demand_resolution", "", item.id,
                        f"Demand item {item.id!r} is measured on {item.settlement} intervals but "
                        f"part of the axis is {step_h:g} h apart: the peak within an interval is "
                        "not resolved, so the modelled demand charge can understate the bill."))
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

"""Minimum renewable fraction (homer-parity 01a, owner decision HOMER-GAPS-1).

HOMER Pro's renewable fraction, on the electrical side, as a share of all
the electricity used on site (owner decision RENFRAC-ALL-1):

    f_ren = 1 − E_nonren / E_served

* ``E_served`` — the active Loads on electrical buses (the p_set the LP
  solves, so after the solve-time load scalers), plus the electricity that
  conversion Links draw (below), less every slack's supply on electrical
  buses (VOLL shed, demand response, data-centre curtailment): shed or
  curtailed energy is not served.
* Conversion (R14): an active Link that draws electricity — bus0 on an
  electrical bus, or a second input busN (efficiencyN < 0) on one — and whose
  outputs are all non-electrical is *use* when no output has a path back to
  an electrical bus (forward through Links, backward through a Link that can
  run negative, but not through a counted source Link). Its draw is
  p0·(1 for bus0, −efficiencyN(t) for each electrical second input), p0 ≥ 0.
  Electrolyser into an H2 offtake, heat pump, e-boiler, DAC and process
  Links are use.
  - Storage is not use: a charger whose energy comes back to electricity
    (a battery charger, a battery's own bidirectional Link, an electrolyser
    whose H2 bus feeds a fuel cell) is not counted, or it would count twice.
    A Link that can run negative is a path back by itself, so a counted
    conversion Link never runs negative and its draw is p0 directly.
  - Net cluster (homer-parity 06): a path back AND a sink on a non-electrical
    bus it reaches (an H2 bus with both an offtake and a fuel cell). A sink is
    an active Load, or an active non-slack Generator that withdraws (R16:
    sign < 0, or p_max_pu ≤ 0 throughout with p_min_pu < 0 somewhere). The
    cluster is counted by its net draw: Σ p0·`_net_coef` over every Link
    between it and electricity (what the electrolysers draw minus what the
    fuel cell returns), signed and summed per period with the period's
    weights, in the KPI and the row alike. Only a closed cluster is netted
    (GK B N1): one with a Store or StorageUnit that can start full (not
    cyclic, e_initial > 0; per period on a multi-period network) or a Link
    delivering into it from a non-electrical bus outside it is open, and
    fails closed like an unattributed path: the KPI is None with a reason
    when its Links run and the row warns and leaves it out. So is a cluster
    whose efficiency is NaN (N4). A solved period whose net is negative
    reads None (N2, defence in depth).
  - Export is not use: elec→elec Links are not conversion, nor is a Link
    into a supply bus or a bus whose carrier names the grid or export.
  - Not guarded (R14j): renewable electricity dumped into a converter whose
    output nothing uses (an electrolyser into an unbounded Store) still
    counts as use, so it raises the denominator and with it the
    non-renewable allowance. Likewise grid or diesel electricity drawn into
    a converter counts as served (accepted by the owner). The KPI payload
    carries ``note`` (`CONVERSION_NOTE`) disclosing both.
* ``E_nonren`` — the electricity that non-renewable sources put into the
  served network:

  - a Generator on an electrical bus whose carrier is not renewable
    (`carrier_kpis._is_renewable_carrier`; grid import, diesel, gas, …),
    counted gross (its positive output only), unless it withdraws (R16,
    below): a withdrawing Generator on an electrical bus (an export or an
    offtake, ``sign`` < 0 or p_max_pu ≤ 0 throughout) is neither supply nor
    use, so it is in neither ``E_nonren`` nor ``E_served`` (export is not
    use, R14c). One with ``sign`` < 0 that can also inject (p_min_pu < 0)
    supplies the bus at p < 0: that injection, −p (positive part), is
    counted gross as non-renewable (GK B N3, GK A R1; an auxiliary
    ``y ≥ −p, y ≥ 0`` in the row);
  - a Link out of a *supply bus* into an electrical bus that is not one,
    counted as η(t)·p0 (positive part). A supply bus has a non-renewable,
    non-slack Generator, no Load, no Line or Transformer, and a Link into an
    electrical bus: a fuel bus feeding a genset Link, or a grid bus feeding an
    import Link (electrical or not). Its own Generator is then not counted,
    so nothing is counted twice. A bus fed by both a renewable and a
    non-renewable Generator counts whole (conservative).

  Storage (StorageUnit, Store and the Links around them), electrolyser and
  fuel-cell loops, and elec→elec converters are not sources: what they put
  out was generated upstream. Heat and hydrogen loads are out of scope.

  Fail closed: a dispatched non-renewable Generator off the served network
  whose energy can reach an electrical bus by a path not counted above (a
  chain through another non-electrical bus, a multi-port Link output, a
  second Link out of a supply bus, or a Link that can run negative run
  backward into its bus0, e.g. a reversible electrolyser beside a grey-H2
  Generator, an SMR or a grey-H2 Store) makes the KPI None with a reason,
  and the LP row logs a warning naming it. Fuel that only reaches heat or
  hydrogen is not electricity and passes.
  So is a Link from a fuel stock (a Store or StorageUnit whose own carrier is
  neither renewable nor electricity storage, on a bus with no Generator and
  no electrical inflow, whatever the bus's carrier) into an electrical bus,
  and a fuel stock on an electrical bus that discharges into it (R13);
  counting them is a follow-up. Batteries and the like stay storage.

* Bounded (GK B N2): all non-renewable output counts as used on site
  (conservative). When it exceeds the electricity used, e.g. diesel exported
  through a withdrawing Generator, the KPI is None with a reason, never a
  negative number; the fraction is always in [0, 1]. The LP row keeps its
  gross numerator.

Energy is MW × ``snapshot_weightings.generators`` per year of each period;
the horizon totals weight each period by its ``years``.

`renewable_fraction(n)` is the KPI: a pure function of the solved network
(plus the lost-load capture, which a solve removes from the network), shown
whether or not the constraint is on. homer-parity 03a ranks architectures
with it; there is one implementation, here.

`_wrap_with_min_renewable_fraction` adds, per investment period P, the row
``min_renewable_fraction_<P>`` (``min_renewable_fraction`` on a single-period
network):

    Σ_t∈P w_t·nonren_t + (1−f)·Σ_t∈P w_t·slack_t − (1−f)·Σ_t∈P w_t·draw_t
        ≤ (1−f)·Σ_t∈P w_t·load_t

A period with no load and no conversion Link gets no row (0 ≤ 0 is
harmless but says nothing).
Rolling and myopic solves are refused at preflight
(`validation_service._check_min_renewable_fraction`): they would build one
row per LP window, not one per period.
With a slack present the LP may meet the target by shedding or curtailing at
the slack's price: a target above what renewables can reach is then met by
shedding, not reported infeasible. Without a slack it is infeasible, and
`infeasibility_lines` names the row, the renewable energy it needs (from the
load the row was built on, so after the load scalers) and the most the plan
could produce. Inactive Loads are not served and not in the row.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from services.adequacy.archetypes import min_p_min_pu
from services.adequacy.slack import LOST_SLACK_PREFIX, slack_generator_mask
from services.profile_shapes import _ELEC_CARRIERS
from services.results.carrier_kpis import _is_renewable_carrier
from services.results.load_shape import BATTERY

# Electricity-storage carriers (`load_shape.BATTERY` and pumped hydro): a
# Store or StorageUnit carrying one is storage, never a fuel stock.
_ELEC_STORAGE = BATTERY | {"phs", "pumped hydro"}

ROW = "min_renewable_fraction"
# The facts the rows were built from (the target and each period's load, so
# after the solve-time load scalers), kept on the network for the
# infeasibility hint: the scalers are reverted before the diagnosis runs.
_BUILT = "_min_renewable_fraction_rows"
LABEL = "Minimum renewable fraction"
_AUX = "RenewableFraction-gross"


CONVERSION_NOTE = (
    "Electricity drawn by conversion units (electrolysers, heat pumps, …) "
    "counts as used on site, whatever its source and whether or not their "
    "output is used. So grid or diesel electricity drawn into a converter "
    "counts as served electricity, and renewable electricity run into a "
    "converter whose output nothing uses raises the renewable fraction."
)

NO_DISPATCH_REASON = "no dispatch: the network has not been solved"
NO_SERVED_REASON = "no electricity served: the fraction is undefined"
NONREN_EXCEEDS_REASON = (
    "non-renewable supply ({nonren:,.1f} MWh) exceeds the electricity used on "
    "site ({used:,.1f} MWh), for example because it was exported: the fraction "
    "counts all non-renewable output as used on site, so it is undefined here"
)


def row_name(period: Any) -> str:
    """The LP row of one investment period (None: the single-period row)."""
    return ROW if period is None else f"{ROW}_{period}"


def _pct(f: float) -> str:
    """The target as a percent with the user's precision (up to 4 decimals,
    trailing zeros trimmed): 0.825 → "82.5%", 0.9 → "90%"."""
    return f"{f * 100:.4f}".rstrip("0").rstrip(".") + "%"


def target_of(cfg: Any) -> float | None:
    """The configured target as a fraction in (0, 1], or None when off."""
    try:
        f = getattr(cfg, "min_renewable_fraction", None)
        f = None if f is None else float(f)
    except (TypeError, ValueError):
        return None
    if f is None or not math.isfinite(f) or f <= 0.0:
        return None
    # Not clamped: the schema refuses > 1 at entry; a path around it gets the
    # LP it asked for (a negative right-hand side, reported infeasible).
    return f


# ── classification ────────────────────────────────────────────────────────


def _is_electrical(carrier: object) -> bool:
    c = str(carrier or "").lower().strip()
    return c == "" or c in _ELEC_CARRIERS


def _electrical_buses(n) -> set[str]:
    return (
        {str(b) for b, c in n.buses["carrier"].items() if _is_electrical(c)}
        if "carrier" in n.buses.columns
        else {str(b) for b in n.buses.index}
    )


def classify(n) -> dict[str, list[str]]:
    """Which assets the fraction reads: non-renewable Generators counted at
    the generator, source Links (from a supply bus), slack Generators and
    Loads, all on electrical buses. Pure; see the module docstring."""
    elec = _electrical_buses(n)
    gens = n.generators
    slack = slack_generator_mask(gens) if len(gens) else pd.Series(dtype=bool)
    slack = slack.reindex(gens.index, fill_value=False)
    gbus = gens["bus"].astype(str) if len(gens) else pd.Series(dtype=str)
    nonren = [
        g
        for g in gens.index
        if not slack[g]
        and not _is_renewable_carrier(str(gens.at[g, "carrier"]))
        and not str(g).startswith(LOST_SLACK_PREFIX)
    ]
    load_buses = set(n.loads["bus"].astype(str)) if len(n.loads) else set()
    branch_buses: set[str] = set()
    for comp in (n.lines, n.transformers):
        if len(comp):
            branch_buses |= set(comp["bus0"].astype(str)) | set(
                comp["bus1"].astype(str)
            )
    links = n.links
    # R16: a Generator that withdraws is a sink, not a supply.
    withdraw = set(_withdrawing_generators(n))
    candidates = {gbus[g] for g in nonren if g not in withdraw}
    candidates -= load_buses | branch_buses
    supply = {
        b
        for b in candidates
        if len(links)
        and (
            (links["bus0"].astype(str) == b)
            & links["bus1"].astype(str).isin(elec - {b})
        ).any()
    }
    src_links = (
        [
            l
            for l in links.index
            if str(links.at[l, "bus0"]) in supply
            and str(links.at[l, "bus1"]) in elec
            and str(links.at[l, "bus1"]) not in supply
        ]
        if len(links)
        else []
    )
    return {
        "nonren_generators": [
            g
            for g in nonren
            if gbus[g] in elec and gbus[g] not in supply and g not in withdraw
        ],
        "source_links": src_links,
        "slack_generators": [g for g in gens.index if slack[g] and gbus[g] in elec],
        "loads": [
            l
            for l in n.loads.index
            if str(n.loads.at[l, "bus"]) in elec
            and ("active" not in n.loads.columns or bool(n.loads.at[l, "active"]))
        ],
        "renewable_generators": [
            g
            for g in gens.index
            if not slack[g] and gbus[g] in elec and g not in nonren
        ],
        "unattributed_generators": _unattributed(
            n,
            [
                g
                for g in nonren
                if g not in withdraw and (gbus[g] not in elec or gbus[g] in supply)
            ],
            gbus,
            elec,
            set(src_links),
        ),
        "injecting_generators": [
            g
            for g in _two_way_withdrawers(n, withdraw)
            if g in nonren and gbus[g] in elec
        ],
        "unattributed_links": _fuel_store_links(n, elec, gbus),
        "unattributed_stores": _fuel_stocks_on_elec(n, elec)
        + _fuel_stocks_run_backward(n, elec, set(src_links)),
        **_conversion(n, elec, supply, set(src_links)),
    }


def _is_elec_storage(carrier: str) -> bool:
    """An electricity-storage carrier (battery and the like): its output was
    generated upstream, so it is storage, never a fuel."""
    c = carrier.lower().strip()
    return c in _ELEC_STORAGE or "battery" in c


def _fuel_stocks(n, elec) -> list[tuple[str, str, str]]:
    """(component, name, bus) of every Store and StorageUnit that holds a
    fuel. Its own carrier decides when set: a fuel is neither renewable nor
    an electricity-storage carrier, whatever its bus's carrier (a bus with no
    carrier reads as AC, F1). Without its own carrier the bus's carrier
    decides, on a non-electrical bus only."""
    out = []
    for comp, df in (("Store", n.stores), ("StorageUnit", n.storage_units)):
        if not len(df):
            continue
        carriers = (
            df["carrier"] if "carrier" in df.columns else pd.Series("", index=df.index)
        )
        for s in df.index:
            bus = str(df.at[s, "bus"])
            own = str(carriers.get(s) or "").strip()
            if own:
                fuel = not _is_renewable_carrier(own) and not _is_elec_storage(own)
            else:
                fuel = bus not in elec and not _is_renewable_carrier(
                    str(n.buses.at[bus, "carrier"] or "")
                )
            if fuel:
                out.append((comp, str(s), bus))
    return out


def _fuel_store_links(n, elec, gbus) -> list[str]:
    """R13: Links delivering into an electrical bus from a bus that holds a
    fuel stock (`_fuel_stocks`), no Generator and no inflow from an
    electrical bus (a fuel tank, not a battery). A Link that can run
    backwards (p_min_pu < 0, static or in time) with an electrical output
    feeds its bus0 from electricity too: a battery behind one bidirectional
    inverter. Not counted yet (follow-up: count η·p0); the KPI fails closed
    on them."""
    links = n.links
    stocks = _fuel_stocks(n, elec)
    if not len(links) or not stocks:
        return []
    fed_from_elec = {
        b
        for l in links.index
        if str(links.at[l, "bus0"]) in elec
        for b in _link_outputs(links, l)
    } | {
        str(links.at[l, "bus0"])
        for l in links.index
        if any(b in elec for b in _link_outputs(links, l)) and min_p_min_pu(n, l) < 0
    }
    fuel = {bus for _c, _s, bus in stocks} - set(gbus) - fed_from_elec
    return [
        l
        for l in links.index
        if str(links.at[l, "bus0"]) in fuel
        and any(b in elec for b in _link_outputs(links, l))
    ]


def _fuel_stocks_on_elec(n, elec) -> list[str]:
    """F1: a fuel Store or StorageUnit on an electrical bus discharges
    non-renewable electricity straight into it, which the fraction does not
    count; the KPI fails closed when it discharges."""
    return [s for _c, s, bus in _fuel_stocks(n, elec) if bus in elec]


def _active(df: pd.DataFrame, name) -> bool:
    return "active" not in df.columns or bool(df.at[name, "active"])


def _eff_attr(k: int) -> str:
    return "efficiency" if k == 1 else f"efficiency{k}"


def _ports(n, link) -> tuple[list[tuple[int, str]], list[str]]:
    """A Link's input ports ``[(k, bus)]`` (bus0, and any bus2, bus3, … whose
    efficiency is negative, static or in time: a second input) and its
    output buses (bus1 and the other set ports)."""
    links = n.links
    ins: list[tuple[int, str]] = [(0, str(links.at[link, "bus0"]))]
    outs: list[str] = []
    for c in links.columns:
        if not c.startswith("bus") or c == "bus0" or not c[3:].isdigit():
            continue
        bus = str(links.at[link, c] or "").strip()
        if not bus:
            continue
        k = int(c[3:])
        attr = _eff_attr(k)
        neg = False
        if k >= 2 and attr in links.columns:
            try:
                neg = float(links.at[link, attr]) < 0
            except (TypeError, ValueError):
                neg = False
            series = getattr(n.links_t, attr, None)
            if series is not None and link in getattr(series, "columns", []):
                neg = neg or bool((series[link].astype(float) < 0).any())
        if neg:
            ins.append((k, bus))
        else:
            outs.append(bus)
    return ins, outs


def _is_export_bus(n, bus: str, supply: set[str]) -> bool:
    """The grid side of the meter: a supply bus (it feeds a counted import),
    or a bus whose carrier names the grid or export."""
    c = str(n.buses.at[bus, "carrier"] or "").lower() if bus in n.buses.index else ""
    return bus in supply or "grid" in c or "export" in c


def _withdrawing_generators(n) -> list[str]:
    """R16: the active, non-slack Generators that withdraw (an offtake
    modelled as a negative Generator): ``sign`` < 0, or p_max_pu ≤ 0 at every
    snapshot with p_min_pu < 0 at some (static or in time). A sink, like an
    active Load, and never a supply."""
    gens = n.generators
    if not len(gens):
        return []
    slack = slack_generator_mask(gens).reindex(gens.index, fill_value=False)
    names = [
        g
        for g in gens.index
        if _active(gens, g)
        and not slack[g]
        and not str(g).startswith(LOST_SLACK_PREFIX)
    ]
    if not names:
        return []
    pmax = n.get_switchable_as_dense("Generator", "p_max_pu")[names]
    pmin = n.get_switchable_as_dense("Generator", "p_min_pu")[names]
    sign = (
        gens["sign"].astype(float)
        if "sign" in gens.columns
        else pd.Series(1.0, index=gens.index)
    )
    return [
        g
        for g in names
        if sign[g] < 0 or ((pmax[g] <= 0).all() and (pmin[g] < 0).any())
    ]


def _two_way_withdrawers(n, withdraw) -> list[str]:
    """GK B N3: the withdrawing Generators with ``sign`` < 0 that can also
    inject (p_min_pu < 0 somewhere, static or in time): their bus injection
    is ``sign``·p > 0 when p < 0. A non-renewable one on an electrical bus
    counts that injection, −p (positive part), gross as non-renewable supply,
    in the KPI and the row (GK A ruling R1 on N3)."""
    gens = n.generators
    names = [
        g for g in withdraw if "sign" in gens.columns and float(gens.at[g, "sign"]) < 0
    ]
    if not names:
        return []
    pmin = n.get_switchable_as_dense("Generator", "p_min_pu")[names]
    return [g for g in names if (pmin[g] < 0).any()]


def _conversion(n, elec, supply, src_links) -> dict[str, Any]:
    """R14: which active Links use electricity on site (see the module
    docstring). Returns ``conversion_links`` (counted as use),
    ``conversion_inputs`` ({link: [k of each electrical input port]}),
    ``storage_links`` (their energy comes back to electricity) and
    ``net_clusters`` (homer-parity 06: a path back AND a non-electrical sink,
    a Load or a withdrawing Generator). A net cluster is ``{"buses", "links"}``:
    the non-electrical buses its conversion Links reach (clusters sharing a bus
    merge) and every active non-source Link between them and electricity, each
    once; it is counted by its net draw (`_net_coef`)."""
    out: dict[str, Any] = {
        "conversion_links": [],
        "conversion_inputs": {},
        "storage_links": [],
        "net_clusters": [],
        "open_clusters": [],
    }
    links = n.links
    if not len(links):
        return out
    active = [l for l in links.index if _active(links, l)]
    ports = {l: _ports(n, l) for l in active}
    reversible = {l for l in active if min_p_min_pu(n, l) < 0}
    loads = n.loads
    clusters: list[set[str]] = []
    sink_buses = (
        {str(loads.at[l, "bus"]) for l in loads.index if _active(loads, l)}
        | {str(n.generators.at[g, "bus"]) for g in _withdrawing_generators(n)}
    ) - elec
    for l in active:
        ins, outs = ports[l]
        e_in = [k for k, b in ins if b in elec]
        if not e_in or not outs or any(b in elec for b in outs):
            continue  # draws no electricity, or an elec→elec converter
        if any(_is_export_bus(n, b, supply) for b in outs):
            continue  # export (R14c)
        seen, stack, back = set(outs), list(outs), False
        while stack:
            cur = stack.pop()
            for m in active:
                m_ins, m_outs = ports[m]
                nxt = list(m_outs) if any(b == cur for _k, b in m_ins) else []
                if cur in m_outs and m in reversible:
                    nxt += [b for _k, b in m_ins]  # runs backwards
                for b in nxt:
                    if b in elec:
                        back = back or m not in src_links
                    elif b not in seen:
                        seen.add(b)
                        stack.append(b)
        if not back:
            out["conversion_links"].append(l)
            out["conversion_inputs"][l] = e_in
        elif seen & sink_buses:
            merged = set(seen)
            for c in [c for c in clusters if c & merged]:
                clusters.remove(c)
                merged |= c
            clusters.append(merged)
        else:
            out["storage_links"].append(l)
    taken: set[str] = set()
    for buses in clusters:
        bridge = [
            m
            for m in active
            if m not in src_links
            and m not in taken  # GK B N6: a Link is counted in one cluster only
            and any(b in elec for _k, b in _bus_ports(n, m))
            and any(b in buses for _k, b in _bus_ports(n, m))
        ]
        taken |= set(bridge)
        cluster = {"buses": sorted(buses), "links": sorted(bridge)}
        why = _cluster_open(n, buses, set(bridge), elec, active, ports)
        if why:
            out["open_clusters"].append({**cluster, "why": why})
        else:
            out["net_clusters"].append(cluster)
    out["net_clusters"].sort(key=lambda c: c["buses"])
    out["open_clusters"].sort(key=lambda c: c["buses"])
    return out


def _starts_full(n, comp: str, name, multi: bool) -> bool:
    """GK B N1: can this Store/StorageUnit hold energy at the start that the cluster never drew? Not when it
    starts empty (each period, on a multi-period network) or wraps cyclically (per period when multi-period)."""
    if comp == "Store":
        df, init, cyc = n.stores, "e_initial", "e_cyclic"
    else:
        df, init, cyc = n.storage_units, "state_of_charge_initial", "cyclic_state_of_charge"

    def _get(attr, default):
        return df.at[name, attr] if attr in df.columns else default

    if bool(_get(f"{cyc}_per_period", False)):
        return False
    if bool(_get(cyc, False)) and not multi:
        return False
    empty = float(_get(init, 0.0) or 0.0) == 0.0
    return not (empty and (not multi or bool(_get(f"{init}_per_period", False))))


def _cluster_open(n, buses, bridge, elec, active, ports) -> str | None:
    """GK B N1 (and its premise for N5): a net cluster is counted by its net draw only when it is closed, so
    all it returns is electricity it drew. It is open when a Store or StorageUnit on its buses can start full,
    or when a Link outside the cluster delivers into one of its buses from a bus that is neither electrical nor
    the cluster's (a source Link's port, a reformer from a fuel bus). Returns why, or None."""
    multi = isinstance(n.snapshots, pd.MultiIndex)
    for comp, df in (("Store", n.stores), ("StorageUnit", n.storage_units)):
        for name in df.index:
            if _active(df, name) and str(df.at[name, "bus"]) in buses and _starts_full(n, comp, name, multi):
                return f"{comp} {name} can start with energy the cluster never drew"
    for m in active:
        if m in bridge:
            continue
        ins, outs = ports[m]
        if min_p_min_pu(n, m) < 0:  # runs backwards too: every port can deliver
            ins = outs = [b for _k, b in _bus_ports(n, m)]
            ins = [(0, b) for b in ins]
        if any(b in buses for b in outs) and any(b not in buses and b not in elec for _k, b in ins):
            return f"Link {m} delivers into it from outside"
    return None


def _bus_ports(n, link) -> list[tuple[int, str]]:
    """Every set port of a Link, ``[(k, bus)]``."""
    ins, _outs = _ports(n, link)
    links = n.links
    out = list(ins)
    for c in links.columns:
        if c.startswith("bus") and c[3:].isdigit() and int(c[3:]) not in {k for k, _b in ins}:
            bus = str(links.at[link, c] or "").strip()
            if bus:
                out.append((int(c[3:]), bus))
    return sorted(out)


def _net_coef(n, link: str, elec: set[str], snapshots) -> pd.Series:
    """MW a net-cluster Link withdraws from electricity per MW of p0 (homer-parity 06): Σ over its electrical
    ports k of 1 (k = 0) or −efficiencyk(t), PyPSA's own p_k ÷ p0. An electrolyser gives +1, a fuel cell into
    electricity −η1 (it returns), a reversible Link is signed by its p0."""
    out = pd.Series(0.0, index=snapshots)
    for k, b in _bus_ports(n, link):
        if b in elec:
            out += 1.0 if k == 0 else -_efficiency(n, link, snapshots, _eff_attr(k))
    return out


def _link_outputs(links: pd.DataFrame, link: str) -> list[str]:
    """Every output bus of a Link: bus1 and any bus2, bus3, … that is set."""
    cols = [c for c in links.columns if c.startswith("bus") and c not in ("bus0",)]
    return [
        str(links.at[link, c]) for c in cols if str(links.at[link, c] or "").strip()
    ]


def _leaks(n, bus: str, elec, counted, *, backward_only: bool = False) -> bool:
    """Whether energy put into ``bus`` can reach an electrical bus by a path
    the fraction does not count: forward through a Link out of a bus (but not
    a counted source Link's bus1), or backward through a Link that can run
    negative (p_min_pu < 0, static or in time), which delivers from its
    output side into its bus0 (A3). ``backward_only``: only a path that runs
    some Link backward counts (a forward path out of a fuel stock is R13's)."""
    links = n.links
    out_of: dict[str, list[str]] = {}
    into: dict[str, list[str]] = {}
    for l in links.index:
        out_of.setdefault(str(links.at[l, "bus0"]), []).append(l)
        if min_p_min_pu(n, l) < 0:
            for b in _link_outputs(links, l):
                into.setdefault(b, []).append(l)
    start = (bus, False)
    seen, stack = {start}, [start]
    while stack:
        cur, back = stack.pop()
        steps = [
            (l, b, back)
            for l in out_of.get(cur, [])
            for b in _link_outputs(links, l)
            if not (l in counted and b == str(links.at[l, "bus1"])) or b not in elec
        ] + [(l, str(links.at[l, "bus0"]), True) for l in into.get(cur, [])]
        for _l, b, b_back in steps:
            if b in elec:
                if b_back or not backward_only:
                    return True
            elif (b, b_back) not in seen:
                seen.add((b, b_back))
                stack.append((b, b_back))
    return False


def _unattributed(n, gens, gbus, elec, counted) -> list[str]:
    """The non-renewable Generators off the served network whose energy can
    reach an electrical bus by a path the fraction does not count (`_leaks`):
    through a Link that is not a counted source Link (a chain through another
    non-electrical bus, a multi-port output, a second Link out of a supply
    bus), or backward through a Link that can run negative (a reversible
    electrolyser run as a fuel cell off a grey-H2 bus). Fail closed (GK B):
    the KPI then reads None, never a value. A fuel that only reaches heat or
    hydrogen loads is not electricity and passes."""
    if not len(n.links) or not gens:
        return []
    return [g for g in gens if _leaks(n, gbus[g], elec, counted)]


def _fuel_stocks_run_backward(n, elec, counted) -> list[str]:
    """A3: fuel stocks off the electrical buses whose energy reaches one only
    by running a Link backward (a grey-H2 Store behind a reversible
    electrolyser). Forward paths out of a fuel stock are R13's
    (`_fuel_store_links`). Fail closed when they discharge."""
    if not len(n.links):
        return []
    return [
        s
        for _c, s, bus in _fuel_stocks(n, elec)
        if bus not in elec and _leaks(n, bus, elec, counted, backward_only=True)
    ]


# ── weights and periods ───────────────────────────────────────────────────


def _weights(n, snapshots) -> pd.Series:
    sw = n.snapshot_weightings
    col = "generators" if "generators" in sw.columns else sw.columns[0]
    return sw.loc[snapshots, col].astype(float)


def _periods(snapshots) -> tuple[pd.Series, list]:
    """Each snapshot's period, and the periods; (None,) on a single period."""
    if isinstance(snapshots, pd.MultiIndex):
        per = pd.Series(snapshots.get_level_values(0), index=snapshots)
        return per, sorted(set(per))
    return pd.Series([None] * len(snapshots), index=snapshots, dtype=object), [None]


def _period_mask(per_of: pd.Series, period) -> pd.Series:
    """The snapshots of one period (all of them on a single-period network)."""
    return (
        (per_of == period)
        if period is not None
        else pd.Series(True, index=per_of.index)
    )


def _efficiency(n, link: str, snapshots, attr: str = "efficiency") -> pd.Series:
    eff_t = getattr(n.links_t, attr, None)
    if eff_t is not None and link in getattr(eff_t, "columns", []):
        return eff_t[link].reindex(snapshots).astype(float)
    return pd.Series(float(n.links.at[link, attr]), index=snapshots)


def _has_nan(n, cls, gp, lp, snapshots) -> bool:
    """A NaN in a dispatch the fraction reads: unknown, never 0 (03a F3)."""
    reads = [
        (
            gp,
            cls["nonren_generators"]
            + cls["injecting_generators"]
            + cls["slack_generators"],
        ),
        (n.links_t.p1, cls["source_links"]),
        (lp, cls["loads"]),
        (
            n.links_t.p0,
            cls["source_links"]
            + cls["conversion_links"]
            + [l for c in cls["net_clusters"] for l in c["links"]],
        ),
    ]
    for frame, names in reads:
        cols = [c for c in names if c in getattr(frame, "columns", [])]
        if cols and frame[cols].reindex(snapshots).isna().to_numpy().any():
            return True
    return False


def _source_delivery(n, link: str, snapshots) -> pd.Series | None:
    """A source Link's delivery into the site, η(t)·p0 (positive part), or
    ``-p1`` (positive part) when the dispatch carries p1 and not p0. None,
    so the KPI fails closed, when it carries neither: never inferred from
    the supply bus (GK B A2)."""
    p0 = n.links_t.p0
    if p0 is not None and link in getattr(p0, "columns", []):
        sent = p0[link].reindex(snapshots).astype(float).fillna(0.0)
        return sent.clip(lower=0.0) * _efficiency(n, link, snapshots)
    p1 = n.links_t.p1
    if p1 is not None and link in getattr(p1, "columns", []):
        got = -p1[link].reindex(snapshots).astype(float).fillna(0.0)
        return got.clip(lower=0.0)
    return None


def _draw_coef(n, cls, link: str, snapshots) -> pd.Series:
    """MW of electricity a conversion Link draws per MW of p0: 1 for bus0,
    −efficiencyN(t) for each electrical second input busN."""
    out = pd.Series(0.0, index=snapshots)
    for k in cls["conversion_inputs"][link]:
        out += 1.0 if k == 0 else -_efficiency(n, link, snapshots, _eff_attr(k))
    return out


def _load_mw(n, loads, snapshots) -> pd.Series:
    if not loads:
        return pd.Series(0.0, index=snapshots)
    p = n.get_switchable_as_dense("Load", "p_set", snapshots)[loads]
    return p.sum(axis=1).astype(float)


# ── the KPI ───────────────────────────────────────────────────────────────


def _electrical_slack_columns(n, cls, columns) -> list:
    """The columns of a slack capture that are electrical: a Load the fraction
    serves, or an electrical bus (legacy and DSR captures are bus-keyed). A
    non-electrical Load or bus is dropped; a column that is neither is kept,
    so shed energy that cannot be classified is never dropped silently."""
    elec, loads = _electrical_buses(n), set(cls["loads"])
    keep = []
    for c in columns:
        if c in n.loads.index:
            if c in loads:
                keep.append(c)
        elif c in n.buses.index:
            if str(c) in elec:
                keep.append(c)
        else:
            keep.append(c)
    return keep


def _unserved_mw(n, cls, unserved_t, snapshots) -> pd.Series:
    """The electrical slack MW per snapshot: ``unserved_t`` is a frame (its
    columns Loads or buses, filtered to the electrical ones), a series
    (already electrical), or a list of them."""
    out = pd.Series(0.0, index=snapshots)
    frames = unserved_t if isinstance(unserved_t, list) else [unserved_t]
    for f in frames:
        if f is None:
            continue
        if isinstance(f, pd.DataFrame):
            f = f[_electrical_slack_columns(n, cls, f.columns)].sum(axis=1)
        out += pd.Series(f, dtype=float).reindex(snapshots).fillna(0.0)
    return out


def renewable_fraction(
    n,
    *,
    unserved_t: pd.DataFrame | pd.Series | list | None = None,
    snapshots=None,
) -> dict[str, Any]:
    """The renewable fraction of a solved network (HOMER's definition, see
    the module docstring). ``unserved_t`` is the slack MW per snapshot of the
    solve's capture (the VOLL, DSR and curtailment slacks are removed after
    a solve): a frame keyed by Load or bus, of which only the electrical
    columns count, a series, or a list of them. Slack Generators still on
    the network count as well.

    Returns ``fraction`` (None, with ``reason``, when nothing was served or
    no dispatch is present (GK B A1/A2/A5: a counted Load or non-renewable
    Generator missing from the dispatch, a source Link with neither p0 nor
    p1, or a NaN in what the KPI reads; never inferred), or when non-renewable output exceeds the
    electricity used on site, e.g. diesel exported or charging a battery that
    is not discharged: GK B N2, the fraction is always in [0, 1]),
    ``renewable_mwh``, ``nonrenewable_mwh``,
    ``served_mwh`` (horizon totals, period years applied) and ``per_period``.
    """
    sns = n.snapshots if snapshots is None else snapshots
    out: dict[str, Any] = {
        "fraction": None,
        "renewable_mwh": None,
        "nonrenewable_mwh": None,
        "served_mwh": None,
        "conversion_mwh": None,
        "per_period": [],
        "reason": None,
    }
    gp = n.generators_t.p
    if len(n.generators) and (gp is None or gp.empty):
        out["reason"] = NO_DISPATCH_REASON
        return out
    lp = n.loads_t.p
    cls = classify(n)
    if cls["loads"] and (
        lp is None or lp.empty or not set(cls["loads"]) <= set(lp.columns)
    ):
        out["reason"] = NO_DISPATCH_REASON
        return out
    # GK B A5: a counted non-renewable or slack Generator missing from the
    # dispatch (added after the solve) is unknown, never 0.
    counted_gens = (
        cls["nonren_generators"] + cls["injecting_generators"] + cls["slack_generators"]
    )
    if counted_gens and not set(counted_gens) <= set(gp.columns):
        out["reason"] = NO_DISPATCH_REASON
        return out

    def _col(frame, name):
        if frame is None or name not in getattr(frame, "columns", []):
            return pd.Series(0.0, index=sns)
        return frame[name].reindex(sns).astype(float).fillna(0.0)

    leaked = [g for g in cls["unattributed_generators"] if (_col(gp, g) > 0).any()]
    leaked += [
        l for l in cls["unattributed_links"] if (_col(n.links_t.p0, l) > 0).any()
    ]
    leaked += [
        s
        for s in cls["unattributed_stores"]
        if (_col(n.stores_t.p, s) > 0).any() or (_col(n.storage_units_t.p, s) > 0).any()
    ]
    if leaked:
        out["reason"] = (
            "cannot attribute the electricity of source(s) "
            + ", ".join(map(str, leaked))
            + ": it reaches the site through a path the fraction does not count"
        )
        return out
    opened = [
        c
        for c in cls["open_clusters"]
        if any((_col(n.links_t.p0, l) != 0).any() for l in c["links"])
    ]
    if opened:
        out["reason"] = "; ".join(
            "the net draw of "
            + ", ".join(map(str, c["links"]))
            + " cannot be attributed: "
            + c["why"]
            + ", so what it returns to electricity may not be electricity it drew"
            for c in opened
        )
        return out
    if _has_nan(n, cls, gp, lp, sns):
        out["reason"] = "the dispatch has missing values (NaN)"
        return out
    elec = _electrical_buses(n)
    net_coef = {
        l: _net_coef(n, l, elec, sns) for c in cls["net_clusters"] for l in c["links"]
    }
    bad = [l for l, k in net_coef.items() if not np.isfinite(k.to_numpy()).all()]
    if bad:
        # GK B N4: an unknown efficiency is unknown, never 0.
        out["reason"] = (
            "the efficiency of " + ", ".join(map(str, bad)) + " is missing or not finite"
        )
        return out
    nonren = pd.Series(0.0, index=sns)
    for g in cls["nonren_generators"]:
        nonren += _col(gp, g).clip(lower=0.0)
    for g in cls["injecting_generators"]:
        # GK A G4: the bus receives sign·p = |sign|·(−p) at p < 0.
        nonren += (-_col(gp, g)).clip(lower=0.0) * abs(
            float(n.generators.at[g, "sign"])
        )
    blind = []
    for l in cls["source_links"]:
        delivered = _source_delivery(n, l, sns)
        if delivered is None:
            blind.append(l)
        else:
            nonren += delivered
    if blind:
        out["reason"] = (
            "cannot attribute the electricity of source(s) "
            + ", ".join(map(str, blind))
            + ": the dispatch carries neither its p0 nor its p1"
        )
        return out
    slack = pd.Series(0.0, index=sns)
    for g in cls["slack_generators"]:
        slack += _col(gp, g).clip(lower=0.0)
    load = sum((_col(lp, l) for l in cls["loads"]), pd.Series(0.0, index=sns))
    draw = pd.Series(0.0, index=sns)
    for l in cls["conversion_links"]:
        draw += _col(n.links_t.p0, l).clip(lower=0.0) * _draw_coef(n, cls, l, sns)
    nets = {
        i: sum(
            (_col(n.links_t.p0, l) * net_coef[l] for l in c["links"]),
            pd.Series(0.0, index=sns),
        )
        for i, c in enumerate(cls["net_clusters"])
    }
    for net in nets.values():
        draw += net
    served_t = load + draw - slack - _unserved_mw(n, cls, unserved_t, sns)

    w = _weights(n, sns)
    per_of, periods = _periods(sns)
    for i, net in nets.items():
        # GK B N2 (defence in depth; N1 rules it out): a closed cluster cannot return more than it drew.
        for P in periods:
            mask = _period_mask(per_of, P)
            wn = (w * net)[mask]
            if float(wn.sum()) < -1e-6 * float(wn.clip(lower=0.0).sum()) - 1e-9:
                out["reason"] = (
                    "the net draw of "
                    + ", ".join(map(str, cls["net_clusters"][i]["links"]))
                    + f" is negative in period {P if P is not None else 'all'}: "
                    "it returned more electricity than it drew"
                )
                return out
    years = n.investment_period_weightings["years"] if periods != [None] else None
    tot_served = tot_nonren = tot_draw = 0.0
    for P in periods:
        mask = _period_mask(per_of, P)
        served = float((w * served_t)[mask].sum())
        nr = float((w * nonren)[mask].sum())
        drawn = float((w * draw)[mask].sum())
        out["per_period"].append(
            {
                "period": P,
                "served_mwh": served,
                "conversion_mwh": drawn,
                "nonrenewable_mwh": nr,
                "renewable_mwh": served - nr,
                "fraction": (served - nr) / served
                if served >= nr and served > 0
                else None,
            }
        )
        y = float(years.get(P, 1.0)) if years is not None else 1.0
        tot_served += y * served
        tot_nonren += y * nr
        tot_draw += y * drawn
    out.update(
        served_mwh=tot_served,
        conversion_mwh=tot_draw,
        nonrenewable_mwh=tot_nonren,
        renewable_mwh=tot_served - tot_nonren,
    )
    if cls["conversion_links"] or cls["net_clusters"]:
        out["note"] = CONVERSION_NOTE
    if tot_served <= 0:
        out["reason"] = NO_SERVED_REASON
    elif tot_nonren > tot_served:
        # GK B N2: non-renewable output counts as used on site (conservative),
        # so the fraction stays in [0, 1]; when it exceeds what was used (it
        # was exported, or charged storage that was not used back), read None.
        out["reason"] = NONREN_EXCEEDS_REASON.format(nonren=tot_nonren, used=tot_served)
    else:
        out["fraction"] = (tot_served - tot_nonren) / tot_served
    return out


def renewable_fraction_result(
    n, capture: dict | None, target: float | None
) -> dict[str, Any]:
    """The /results/renewable_fraction payload: the KPI with every slack the
    solve removed as not served (R5, as the LP row counts them): the
    capture's VOLL shed (``lost_load_t``), demand response (``dsr_t``) and
    data-centre curtailment (``dc_curtailment``). Demand response and
    curtailment fall back to the network's committed records when the
    capture lacks them (the capture is kept only when a VOLL slack ran).
    A capture keyed to other snapshots is another solve's and is refused,
    with a reason, rather than dropped; so is a curtailment whose capture
    failed. ``target`` is the target the solve enforced (None when off)."""
    from services.commercial.settlement_inputs import dsr_activation
    from services.solver import dc_curtailment as dcc

    cap = capture or {}
    dsr = cap.get("dsr_t")
    if dsr is None:
        dsr = dsr_activation(n)[0]
    curt = None
    if dcc.META_KEY in cap:
        p_t = (cap[dcc.META_KEY] or {}).get("p_t")
        curt = "failed" if p_t is None else pd.DataFrame({dcc.COOLING_BUS: p_t})
    elif (getattr(n, "meta", None) or {}).get(dcc.META_KEY):
        series = dcc.curtailment_series(n)[0]
        curt = "failed" if series is None else pd.DataFrame({dcc.COOLING_BUS: series})
    slacks = [cap.get("lost_load_t"), dsr, curt]
    if isinstance(curt, str):
        reason = "the data-centre curtailment of the solve could not be read"
    elif any(
        s is not None
        and (
            not isinstance(s, (pd.DataFrame, pd.Series))
            or not s.index.equals(n.snapshots)
        )
        for s in slacks
    ):
        reason = "the lost-load capture belongs to another solve"
    else:
        return {**renewable_fraction(n, unserved_t=slacks), "target": target}
    kpi = renewable_fraction(n)
    kpi.update(fraction=None, reason=reason)
    return {**kpi, "target": target}


# ── the LP row ────────────────────────────────────────────────────────────


def _safe_log(log_queue, msg: str) -> None:
    if log_queue is None:
        return
    try:
        log_queue.put(f"[RENEWABLE] {msg}")
    except Exception:  # noqa: BLE001
        pass


def _gross(m, var, names: list[str], lower) -> Any:
    """``var`` for ``names``, or a gross (≥ 0, ≥ var) auxiliary for those
    that may run negative (an export or a bidirectional import Link)."""
    v = var.sel(name=names)
    neg = [nm for nm, lo in zip(names, lower) if lo < 0]
    if not neg:
        return v
    aux_name = f"{_AUX}-{var.name}"
    z = m.add_variables(
        lower=0.0,
        coords=[v.indexes["snapshot"], pd.Index(neg, name="name")],
        name=aux_name,
    )
    m.add_constraints(z - var.sel(name=neg) >= 0, name=f"{aux_name}-def")
    pos = [nm for nm in names if nm not in neg]
    return (z, var.sel(name=pos) if pos else None)


def _weighted_sum(var_or_pair, coef: pd.DataFrame, snap_coord):
    """Σ_t,name coef[t,name]·var[t,name]; ``var_or_pair`` may be (aux, rest)."""
    import xarray as xr

    def one(v):
        if v is None:
            return None
        names = list(v.indexes["name"])
        da = xr.DataArray(
            coef[names].values,
            dims=["snapshot", "name"],
            coords={"snapshot": snap_coord, "name": names},
        )
        return (v * da).sum()

    if isinstance(var_or_pair, tuple):
        a, b = (one(x) for x in var_or_pair)
        return a if b is None else a + b
    return one(var_or_pair)


def _wrap_with_min_renewable_fraction(network, user_fn, cfg, log_queue=None):
    """Compose extra_functionality with the minimum-renewable-fraction rows
    (one per investment period; see the module docstring). Off (None, 0,
    non-finite) returns ``user_fn`` unchanged, so the LP is untouched."""
    f = target_of(cfg)
    try:
        setattr(network, _BUILT, None)  # never another solve's facts
    except Exception:  # noqa: BLE001
        pass
    if f is None:
        return user_fn

    def rows(n, snapshots):
        m = n.model
        cls = classify(n)
        built: dict[Any, float] = {}
        setattr(n, _BUILT, {"target": f, "demand": built})
        elec = _electrical_buses(n)
        net_coef = {
            l: _net_coef(n, l, elec, snapshots)
            for c in cls["net_clusters"]
            for l in c["links"]
        }
        # GK B N4: a cluster with an unknown efficiency is not netted; it is refused like an open one (N1).
        unknown = [
            c
            for c in cls["net_clusters"]
            if any(not np.isfinite(net_coef[l].to_numpy()).all() for l in c["links"])
        ]
        unseen = (
            cls["unattributed_generators"]
            + cls["unattributed_links"]
            + cls["unattributed_stores"]
            + [l for c in cls["open_clusters"] + unknown for l in c["links"]]
        )
        if unseen:
            _safe_log(
                log_queue,
                f"{LABEL}: WARNING the row cannot see the electricity of "
                + ", ".join(map(str, unseen))
                + " (it reaches the site through a path the fraction does not count), so "
                "the row is looser than the target; the result's fraction reads unavailable "
                "if they run.",
            )
        w = _weights(n, snapshots)
        per_of, periods = _periods(snapshots)
        load = _load_mw(n, cls["loads"], snapshots)
        gvar = m.variables["Generator-p"] if "Generator-p" in m.variables else None
        lvar = m.variables["Link-p"] if "Link-p" in m.variables else None
        coord = next(
            (v.coords["snapshot"] for v in (gvar, lvar) if v is not None), None
        )
        gens = [
            g
            for g in cls["nonren_generators"]
            if gvar is not None and g in gvar.indexes["name"]
        ]
        links = [
            l
            for l in cls["source_links"]
            if lvar is not None and l in lvar.indexes["name"]
        ]
        slacks = [
            g
            for g in cls["slack_generators"]
            if gvar is not None and g in gvar.indexes["name"]
        ]
        inj = [
            g
            for g in cls["injecting_generators"]
            if gvar is not None and g in gvar.indexes["name"]
        ]
        convs = [
            l
            for l in cls["conversion_links"]
            if lvar is not None and l in lvar.indexes["name"]
        ]
        draw = pd.DataFrame(
            {l: _draw_coef(n, cls, l, snapshots) for l in convs}, index=snapshots
        )
        # homer-parity 06: a net cluster's Links, signed by `_net_coef` (no clip: the net draw is linear).
        nets = [
            l
            for c in cls["net_clusters"]
            if c not in unknown
            for l in c["links"]
            if lvar is not None and l in lvar.indexes["name"]
        ]
        for l in nets:
            draw[l] = net_coef[l]
        convs = convs + nets
        pmin_g = n.get_switchable_as_dense("Generator", "p_min_pu", snapshots)
        pmin_l = n.get_switchable_as_dense("Link", "p_min_pu", snapshots)
        g_src = (
            _gross(m, gvar, gens, [float(pmin_g[g].min()) for g in gens])
            if gens
            else None
        )
        l_src = (
            _gross(m, lvar, links, [float(pmin_l[l].min()) for l in links])
            if links
            else None
        )
        eff = pd.DataFrame(
            {l: _efficiency(n, l, snapshots) for l in links}, index=snapshots
        )
        inj_src = None
        if inj:
            # GK A R1: a sign < 0 Generator injects −p at p < 0; y ≥ −p, y ≥ 0.
            # GK A G4: the row weighs y by |sign|, the bus injection sign·p.
            inj_name = f"{_AUX}-inject-Generator-p"
            inj_src = m.add_variables(
                lower=0.0,
                coords=[gvar.indexes["snapshot"], pd.Index(inj, name="name")],
                name=inj_name,
            )
            m.add_constraints(inj_src + gvar.sel(name=inj) >= 0, name=f"{inj_name}-def")
        for P in periods:
            in_p = _period_mask(per_of, P)
            w_p = w.where(in_p, 0.0)
            demand = float((w_p * load).sum())
            built[P] = demand
            name = row_name(P)
            if demand <= 0.0 and not convs:
                _safe_log(
                    log_queue,
                    f"{LABEL}: period {P if P is not None else 'all'} has no "
                    f"electrical load; no row ({name}) added.",
                )
                continue
            terms = []
            if gens:
                terms.append(
                    _weighted_sum(g_src, pd.DataFrame({g: w_p for g in gens}), coord)
                )
            if links:
                terms.append(_weighted_sum(l_src, eff.mul(w_p, axis=0), coord))
            if inj:
                terms.append(
                    _weighted_sum(
                        inj_src,
                        pd.DataFrame(
                            {
                                g: w_p * abs(float(n.generators.at[g, "sign"]))
                                for g in inj
                            }
                        ),
                        coord,
                    )
                )
            if slacks:
                terms.append(
                    _weighted_sum(
                        gvar.sel(name=slacks),
                        pd.DataFrame({g: w_p * (1.0 - f) for g in slacks}),
                        coord,
                    )
                )
            if not terms:
                _safe_log(
                    log_queue,
                    f"{LABEL}: no non-renewable source or slack on the electrical "
                    f"buses; {name} is met trivially and not added.",
                )
                continue
            if convs:
                # R14: what conversion Links draw is used on site, so it
                # raises the allowance: −(1−f)·Σ w·draw on the left.
                terms.append(
                    _weighted_sum(
                        lvar.sel(name=convs),
                        draw.mul(-(1.0 - f) * w_p, axis=0),
                        coord,
                    )
                )
            expr = terms[0]
            for t in terms[1:]:
                expr = expr + t
            rhs = (1.0 - f) * demand
            m.add_constraints(expr <= rhs, name=name)
            _safe_log(
                log_queue,
                f"{LABEL} {_pct(f)} (row {name}): non-renewable supply ≤ "
                f"{rhs:,.1f} MWh of {demand:,.1f} MWh electrical load"
                + (
                    f" plus {_pct(1.0 - f)} of what {len(convs)} conversion "
                    "unit(s) draw"
                    if convs
                    else ""
                )
                + (
                    f"; {len(slacks)} slack(s) may shed at their price instead"
                    if slacks
                    else ""
                )
                + ".",
            )

    def wrapper(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        rows(n, snapshots)

    return wrapper


# ── infeasibility ─────────────────────────────────────────────────────────


def max_renewable_mwh(n, snapshots=None) -> dict[Any, float]:
    """Per period, the most renewable electricity the plan could produce:
    Σ w·p_max_pu·(p_nom_max if extendable else p_nom) over the renewable
    Generators on electrical buses (∞ when one is extendable without a cap)."""
    sns = n.snapshots if snapshots is None else snapshots
    cls = classify(n)
    w = _weights(n, sns)
    per_of, periods = _periods(sns)
    gens = cls["renewable_generators"]
    pmax = n.get_switchable_as_dense("Generator", "p_max_pu", sns)
    out: dict[Any, float] = {}
    for P in periods:
        in_p = _period_mask(per_of, P)
        tot = 0.0
        for g in gens:
            ext = bool(n.generators.at[g, "p_nom_extendable"])
            cap = float(
                n.generators.at[g, "p_nom_max"] if ext else n.generators.at[g, "p_nom"]
            )
            tot += float((w.where(in_p, 0.0) * pmax[g].clip(lower=0.0)).sum()) * cap
        out[P] = tot if np.isfinite(tot) else math.inf
    return out


def infeasibility_lines(n, cfg) -> list[str]:
    """The hints of an infeasible solve with the target on: the row per
    period, the renewable energy the target needs and the most the plan can
    produce. Empty when the target is off."""
    f = target_of(cfg)
    if f is None:
        return []
    cls = classify(n)
    per_of, periods = _periods(n.snapshots)
    w = _weights(n, n.snapshots)
    load = _load_mw(n, cls["loads"], n.snapshots)
    reach = max_renewable_mwh(n)
    # The load the rows were built from (scaled), when they were built for
    # this target; else the network's load.
    built = getattr(n, _BUILT, None) or {}
    built = built.get("demand", {}) if built.get("target") == f else {}
    lines = []
    for P in periods:
        in_p = _period_mask(per_of, P)
        demand = built.get(P, float((w.where(in_p, 0.0) * load).sum()))
        if demand <= 0:
            continue
        need = f * demand
        can = reach.get(P, 0.0)
        where = "" if P is None else f" in {P}"
        conv = (
            f", plus {_pct(f)} of what the conversion units draw"
            if cls["conversion_links"] or cls["net_clusters"]
            else ""
        )
        lines.append(
            f"{LABEL}: the target of {_pct(f)}{where} (row {row_name(P)}) needs "
            f"{need:,.1f} MWh of renewable electricity served{conv}; the renewable "
            f"generators can produce at most "
            + ("an unbounded amount" if math.isinf(can) else f"{can:,.1f} MWh")
            + ". Lower the target, raise a renewable p_nom_max or add a VOLL slack to "
            "allow shedding instead."
        )
    return lines

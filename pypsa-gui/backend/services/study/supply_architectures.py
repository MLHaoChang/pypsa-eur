"""
The best system per supply architecture (homer-parity 03a, owner decision
HOMER-GAPS-1): HOMER Pro's "categorized results", one row per architecture.

"Supply architecture" (gate G11), not "architecture": that word already names
the data-centre power chain (`services/dc_architecture.py`).

* **Groups.** A network's assets fall into the groups of :data:`GROUP_ORDER`
  by component, carrier and ``eh_role`` (:func:`classify`): PV, wind,
  battery, genset and the grid supply (its generator and its import/export
  links). An asset in no group (the load, a bus, a transformer) is in every
  architecture. A shortfall slack (VoLL, demand response, ``__lost_*``, a
  ``process_role == "shortfall"`` unit) is never a member: it stays in every
  fork and never counts as a supply (gate G2).
* **Architectures** are the combinations of the groups present that hold a
  source group (PV, wind, genset or grid; a battery alone holds none) and
  leave every load bus a supply path to a non-slack generator
  (:func:`architectures_for`, gates G2/G3).
* **Omitted, not zeroed.** An architecture's network is a copy with every
  asset of the other groups REMOVED, and with them the links and stores left
  hanging on the buses they vacated (a store battery's charger and
  discharger, a grid's import and export links) (:func:`omit`, gate G3), as
  the study's ``none`` option omits its battery (review v2 BC-1): a size
  fixed at zero fails preflight and is not a "without" case.
* **Best system.** The LP sizes the extendable assets left, so each solve IS
  that architecture's best system. :func:`compare` solves each one through an
  injected ``solve`` and keeps every architecture: one that did not solve is
  listed as ``infeasible``, ``failed`` or ``not_reached`` with null figures
  flagged in ``unavailable``, never dropped (ADR-0001, gate G6).
* **Renewable fraction** is homer-parity 01a's KPI
  (``services.solver.renewable_fraction``, the one definition; GK B G13):
  :func:`renewable_fraction` calls it. Since RENFRAC-ALL-1 it is the share of
  all electricity used on site, so on a sector-coupled architecture what
  converters draw counts as used (R14f).
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

from services.adequacy.slack import LOST_SLACK_PREFIX, slack_generator_mask
from services.solver import renewable_fraction as _RF
from services.study import runner as _runner
from services.study.runner import _opt_value

__all__ = [
    "GROUP_ORDER",
    "MAX_ARCHITECTURES",
    "STATUSES",
    "SupplyArchitecture",
    "ArchitectureUnsolved",
    "architectures_for",
    "classify",
    "compare",
    "omit",
    "renewable_fraction",
    "row_of",
]

# Canonical order: an architecture's id joins its groups in this order.
GROUP_ORDER = ("pv", "wind", "battery", "genset", "grid")
SOURCES = frozenset({"pv", "wind", "genset", "grid"})
LABELS = {
    "pv": "PV",
    "wind": "Wind",
    "battery": "Battery",
    "genset": "Genset",
    "grid": "Grid",
}
# Row statuses, in rank order after the solved rows (gate G6).
STATUSES = ("ok", "infeasible", "failed", "not_reached")
# One solve each; more than this needs an explicit selection.
MAX_ARCHITECTURES = 16

_PV = ("solar",)
_WIND = ("onwind", "offwind", "wind")
_GENSET = frozenset(
    {"diesel", "genset", "gas", "oil", "ocgt", "ccgt", "hfo", "lpg", "biodiesel", "chp"}
)
# A carrier naming a fuel-to-heat or transport asset is no genset, whatever
# fuel it names ("gas boiler", "gas pipeline").
_NOT_GENSET = frozenset({"boiler", "pipeline"})
_GRID = frozenset({"grid", "grid_import", "grid_export", "grid_supply"})
_GRID_ROLES = frozenset({"grid_supply", "grid_import", "grid_export"})

# Components a group can hold, in the order `classify` lists them.
_COMPONENTS = ("Generator", "StorageUnit", "Store", "Link")
# One-port components that keep a bus alive (a bus holding only links that
# lead nowhere else is dead once its members are gone).
_ONE_PORTS = ("Generator", "Load", "StorageUnit", "Store", "ShuntImpedance")
_BRANCHES = ("Line", "Transformer")


class ArchitectureUnsolved(RuntimeError):
    """An architecture's solve gave no best system (``code``: infeasible,
    failed), or the network has none to compare (``no_architectures``,
    ``too_many_architectures``, ``unknown_architecture``)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        #: The text a row or a refusal may show (built by this module or the
        #: runner, never an underlying exception's text).
        self.detail = message or code


@dataclass(frozen=True)
class SupplyArchitecture:
    architecture_id: str
    groups: tuple[str, ...]

    @property
    def label(self) -> str:
        return " + ".join(LABELS[g] for g in self.groups)


def _static(n, component: str) -> pd.DataFrame:
    return n.components[component].static


def _col(df: pd.DataFrame, name: str, col: str) -> str:
    if col not in df.columns:
        return ""
    v = df.at[name, col]
    return "" if v is None or v != v else str(v).strip().lower()


def slack_names(n) -> set[str]:
    """Every shortfall slack generator (gate G2): never a member, never a supply."""
    g = _static(n, "Generator")
    if g.empty:
        return set()
    mask = slack_generator_mask(g)
    mask |= pd.Series(
        [str(x).startswith(LOST_SLACK_PREFIX) for x in g.index], index=g.index
    )
    if "process_role" in g.columns:
        mask |= g["process_role"].astype(str).str.lower().eq("shortfall")
    return {str(x) for x in g.index[mask]}


def _is_genset(carrier: str) -> bool:
    """A genset carrier: one of :data:`_GENSET`'s fuel or technology tokens
    anywhere in it, case-insensitive ("gas turbine", "Diesel genset", "urban
    central gas CHP"), unless a :data:`_NOT_GENSET` token names it."""
    tokens = set(re.split(r"[^a-z0-9]+", carrier.lower()))
    return bool(tokens & _GENSET) and not tokens & _NOT_GENSET


def _group_of(component: str, carrier: str, role: str) -> str | None:
    if role in _GRID_ROLES or carrier in _GRID:
        return "grid" if component in ("Generator", "Link") else None
    if "battery" in carrier:
        return "battery"
    if component == "Link" and _is_genset(carrier):
        return "genset"  # a genset modelled as a fuel-to-power Link (OCGT, CHP)
    if component != "Generator":
        return None
    if carrier.startswith(_PV):
        return "pv"
    if carrier.startswith(_WIND):
        return "wind"
    if _is_genset(carrier):
        return "genset"
    return None


def _stranded(n, out: dict[str, list[tuple[str, str]]], slacks: set[str]) -> None:
    """Add to its group a Generator whose only way to the site is that
    group's member Links (eh_setup's grid Generator, carrier AC, behind its
    ``eh_role = grid_import`` Link): no Load and no Line or Transformer on
    its bus, and every Link touching the bus a member of one group (gate G3:
    removed with them, never left stranded in a fork)."""
    member_links = {
        name: g for g, ms in out.items() for comp, name in ms if comp == "Link"
    }
    if not member_links:
        return
    held = {str(b) for b in _static(n, "Load")["bus"]}
    for comp in _BRANCHES:
        df = _static(n, comp)
        held.update(str(b) for c in ("bus0", "bus1") for b in df[c])
    link_buses = _link_buses(n)
    touching: dict[str, set[str]] = {}
    for name, buses in link_buses.items():
        for b in buses:
            touching.setdefault(b, set()).add(name)
    grouped = {m for ms in out.values() for m in ms}
    g = _static(n, "Generator")
    for name in g.index:
        name = str(name)
        bus = str(g.at[name, "bus"])
        if name in slacks or ("Generator", name) in grouped or bus in held:
            continue
        groups = {member_links.get(k) for k in touching.get(bus, ())}
        if len(groups) == 1 and None not in groups:
            out[groups.pop()].append(("Generator", name))


def classify(n) -> dict[str, list[tuple[str, str]]]:
    """``group -> [(component, name), ...]`` for the groups present."""
    slacks = slack_names(n)
    electric = _electrical_buses(n)
    out: dict[str, list[tuple[str, str]]] = {}
    for component in _COMPONENTS:
        df = _static(n, component)
        for name in df.index:
            if component == "Generator" and str(name) in slacks:
                continue
            group = _group_of(
                component, _col(df, name, "carrier"), _col(df, name, "eh_role")
            )
            # A genset makes electricity: a Generator on a fuel bus, or a Link
            # into a heat bus, is not one (a fuel Generator behind a genset
            # Link joins it through `_stranded`).
            port = {"Generator": "bus", "Link": "bus1"}.get(component)
            if group == "genset" and port and str(df.at[name, port]) not in electric:
                group = None
            if group is not None:
                out.setdefault(group, []).append((component, str(name)))
    _stranded(n, out, slacks)
    return {g: out[g] for g in GROUP_ORDER if g in out}


# ── what an architecture removes ──────────────────────────────────────────


def _bus_of(n, component: str, name: str) -> list[str]:
    df = _static(n, component)
    if component == "Link":
        return _link_buses(n)[name]
    if component in ("Line", "Transformer"):
        return [str(df.at[name, c]) for c in ("bus0", "bus1") if c in df.columns]
    return [str(df.at[name, "bus"])]


def _link_buses(n) -> dict[str, list[str]]:
    df = _static(n, "Link")
    ports = [c for c in df.columns if c.startswith("bus") and c[3:].isdigit()]
    return {
        str(name): [
            str(df.at[name, c]) for c in ports if str(df.at[name, c]) not in ("", "nan")
        ]
        for name in df.index
    }


def removed_components(
    n,
    arch: SupplyArchitecture,
    groups: Mapping[str, list[tuple[str, str]]] | None = None,
) -> list[tuple[str, str]]:
    """
    The members of every group ``arch`` leaves out, then their dependants
    (gate G3): a link or store left on a bus that a removed member vacated and
    that holds nothing else, repeated until nothing more hangs.
    """
    groups = classify(n) if groups is None else groups
    gone = [m for g, ms in groups.items() if g not in arch.groups for m in ms]
    removed = set(gone)
    vacated = {b for comp, name in gone for b in _bus_of(n, comp, name)}
    links = _link_buses(n)
    stores = _static(n, "Store")
    while vacated:
        alive = set()
        for comp in _ONE_PORTS + _BRANCHES:
            df = _static(n, comp)
            for name in df.index:
                if (comp, str(name)) in removed or comp == "Store":
                    continue
                alive.update(_bus_of(n, comp, str(name)))
        live_links = {k: v for k, v in links.items() if ("Link", k) not in removed}
        next_vacated: set[str] = set()
        for bus in vacated:
            if bus in alive:
                continue
            hanging = [k for k, v in live_links.items() if bus in v]
            # A bus whose links reach two or more other buses is a hub that
            # still joins them, not a dead end.
            if len({b for k in hanging for b in links[k]} - {bus}) > 1:
                continue
            for k in hanging:
                removed.add(("Link", k))
                gone.append(("Link", k))
                next_vacated.update(b for b in links[k] if b != bus)
            for s in stores.index:
                if str(stores.at[s, "bus"]) == bus and ("Store", str(s)) not in removed:
                    removed.add(("Store", str(s)))
                    gone.append(("Store", str(s)))
        vacated = next_vacated
    return gone


def omit(
    n,
    arch: SupplyArchitecture,
    groups: Mapping[str, list[tuple[str, str]]] | None = None,
):
    """A copy of ``n`` without the components ``arch`` leaves out (removed,
    never sized to zero)."""
    m = n.copy()
    for component, name in removed_components(n, arch, groups):
        m.remove(component, name)
    return m


def _link_edges(n, removed: Iterable[tuple[str, str]] = ()) -> dict[str, set[str]]:
    """``bus -> buses`` the Links not in ``removed`` carry energy to: a Link
    runs from ``bus0`` to its other ports, and back only when its
    ``p_min_pu`` is negative. A ``links_t.p_min_pu`` column, when present, is
    that ``p_min_pu`` (negative in any snapshot); the static value is read
    only for a Link without one."""
    removed = set(removed)
    edges: dict[str, set[str]] = {}
    links = _static(n, "Link")
    varying = n.links_t.p_min_pu
    back = {str(x) for x in varying.columns[(varying < 0).any()]}
    timed = {str(x) for x in varying.columns}
    for name, buses in _link_buses(n).items():
        if ("Link", name) in removed:
            continue
        if name in timed:
            both = name in back
        else:
            both = float(links.at[name, "p_min_pu"]) < 0
        for b in buses[1:]:
            edges.setdefault(buses[0], set()).add(b)
            if both:
                edges.setdefault(b, set()).add(buses[0])
    return edges


def _supplied(n, removed: set[tuple[str, str]]) -> bool:
    """Every load bus is reached from a non-slack generator through the
    branches and links left (gate G3). A Line or Transformer runs both ways;
    a Link runs as :func:`_link_edges` says."""
    edges = _link_edges(n, removed)

    def edge(a: str, b: str) -> None:
        edges.setdefault(a, set()).add(b)

    for comp in _BRANCHES:
        df = _static(n, comp)
        for name in df.index:
            a, b = str(df.at[name, "bus0"]), str(df.at[name, "bus1"])
            edge(a, b)
            edge(b, a)
    slacks = slack_names(n)
    g = _static(n, "Generator")
    reached = {
        str(g.at[x, "bus"])
        for x in g.index
        if str(x) not in slacks and ("Generator", str(x)) not in removed
    }
    todo = list(reached)
    while todo:
        for b in edges.get(todo.pop(), ()):
            if b not in reached:
                reached.add(b)
                todo.append(b)
    loads = _static(n, "Load")
    return all(str(loads.at[x, "bus"]) in reached for x in loads.index)


def architectures_for(n) -> list[SupplyArchitecture]:
    """Every combination of the present groups that holds a source group and
    supplies every load bus, fewest groups first, each in :data:`GROUP_ORDER`."""
    groups = classify(n)
    present = [g for g in GROUP_ORDER if g in groups]
    out = []
    for size in range(1, len(present) + 1):
        for combo in itertools.combinations(present, size):
            if not SOURCES.intersection(combo):
                continue
            arch = SupplyArchitecture("+".join(combo), combo)
            if _supplied(n, set(removed_components(n, arch, groups))):
                out.append(arch)
    return out


# ── reading a solved architecture ─────────────────────────────────────────


def _weights(n) -> pd.Series:
    """Hours each snapshot stands for (gate G5): the generators' snapshot
    weighting, times its period's ``years`` weighting on a multi-period net."""
    w = n.snapshot_weightings["generators"].astype(float)
    if isinstance(n.snapshots, pd.MultiIndex) and len(n.investment_periods):
        years = n.investment_period_weightings["years"].astype(float)
        w = w * years.reindex(n.snapshots.get_level_values(0)).to_numpy()
    return w


#: Bus carriers that count as electric: AC, DC, a voltage level or none. A copy
#: of the commercial engine's ``participants._ELECTRIC``: the study reaches the
#: engine only through its two seams (``tests/test_study_one_engine.py``), and
#: ``tests/test_homer_supply_architectures.py`` pins the two sets equal.
_ELECTRIC = frozenset({"ac", "dc", "low voltage", "lv", "mv", "hv", "electricity", ""})


def _electrical_buses(n) -> set[str]:
    """Buses whose carrier is electric (AC, DC, a voltage level or none; see
    ``_ELECTRIC``)."""
    buses = n.buses
    if "carrier" not in buses.columns:
        return {str(b) for b in buses.index}
    return {
        str(b)
        for b, c in buses["carrier"].items()
        if str(c if c == c else "").strip().casefold() in _ELECTRIC
    }


def renewable_fraction(n) -> float | None:
    """
    The architecture's renewable fraction: homer-parity 01a's
    :func:`services.solver.renewable_fraction.renewable_fraction`, the one
    definition (GK B G13, owner decision RENFRAC-ALL-1): the share of all
    electricity used on site (served Load plus what conversion Links draw,
    less every slack's shortfall) not supplied by a non-renewable source.
    Always in [0, 1]. None, never 1.0 or NaN, when nothing is served or
    dispatched, or when the fraction cannot attribute a source or a
    conversion, or non-renewable output exceeds what was used (fail closed;
    the shared result carries the reason).
    """
    return _RF.renewable_fraction(n)["fraction"]


def _sizes(
    n, members: list[tuple[str, str]]
) -> tuple[list[dict], float | None, str | None]:
    """The solved sizes of an architecture's assets and their capex (overnight
    cost x optimal size, summed over the extendable assets), or why capex is
    unknown: any extendable asset without an overnight cost (gate G4: never a
    partial sum, never 0)."""
    sizes: list[dict] = []
    capex = 0.0
    missing: str | None = None
    for component, name in members:
        df = _static(n, component)
        attr = "e_nom" if component == "Store" else "p_nom"
        extendable = bool(df.at[name, f"{attr}_extendable"])
        size = _opt_value(df.at[name, f"{attr}_opt" if extendable else attr])
        sizes.append(
            {
                "asset": name,
                "component": component,
                "size": size,
                "unit": "MWh" if component == "Store" else "MW",
                "extendable": extendable,
            }
        )
        if not extendable:
            continue
        cost = (
            _opt_value(df.at[name, "overnight_cost"])
            if "overnight_cost" in df.columns
            else None
        )
        if cost is None or cost <= 0 or size is None:
            missing = missing or (
                f"no_overnight_cost:{name}" if size is not None else f"not_sized:{name}"
            )
        else:
            capex += cost * size
    return sizes, (None if missing else capex), missing


def row_of(arch: SupplyArchitecture, n) -> dict:
    """The solved best system of ``arch`` (``n`` is its solved network)."""
    unavailable: dict[str, str] = {}
    # The runner's system-cost reading, shared with `runner._read_option`.
    cost = _runner.system_cost(n)
    if cost is None:
        unavailable["total_cost_eur_per_year"] = "no_objective"
    groups = classify(n)
    members = [m for g in arch.groups for m in groups.get(g, ())]
    sizes, capex, why = _sizes(n, members)
    if why:
        unavailable["capex_eur"] = why
    kpi = _RF.renewable_fraction(n)
    rf = kpi["fraction"]
    if rf is None:
        # Otherwise the fraction could not attribute a source or a conversion,
        # or non-renewable output exceeded what was used on site.
        unavailable["renewable_fraction"] = (
            "no_served_load"
            if kpi["reason"] in (_RF.NO_DISPATCH_REASON, _RF.NO_SERVED_REASON)
            else "uncounted_non_renewable"
        )
    return {
        "architecture_id": arch.architecture_id,
        "label": arch.label,
        "groups": list(arch.groups),
        "status": "ok",
        "total_cost_eur_per_year": cost,
        "capex_eur": capex,
        "renewable_fraction": rf,
        "sizes": sizes,
        "unavailable": unavailable,
    }


_FIGURES = ("total_cost_eur_per_year", "capex_eur", "renewable_fraction")


def unsolved_row(
    arch: SupplyArchitecture, status: str, error: str | None = None
) -> dict:
    """The row of an architecture with no best system (listed, never dropped)."""
    if status not in STATUSES[1:]:
        raise ValueError(f"not an unsolved status: {status}")
    row = {
        "architecture_id": arch.architecture_id,
        "label": arch.label,
        "groups": list(arch.groups),
        "status": status,
        "sizes": [],
        "unavailable": {k: status for k in _FIGURES},
    }
    row.update({k: None for k in _FIGURES})
    if error:
        row["error"] = error
    return row


def ranked(rows: list[dict]) -> list[dict]:
    """Solved rows by total cost (cheapest first, ties in list order), then
    infeasible, failed and not_reached rows in list order (gate G6). A solved
    row with no cost ranks after every costed one."""

    def key(item):
        i, r = item
        cost = r.get("total_cost_eur_per_year")
        if r["status"] == "ok":
            return (0, 0, cost, i) if cost is not None else (0, 1, 0.0, i)
        return (1 + STATUSES.index(r["status"]), 0, 0.0, i)

    return [r for _, r in sorted(enumerate(rows), key=key)]


def best_of(rows: list[dict]) -> str | None:
    """The cheapest solved architecture, or None when none solved (ruling 1:
    no winner when nothing was compared)."""
    solved = [
        r
        for r in rows
        if r["status"] == "ok" and r["total_cost_eur_per_year"] is not None
    ]
    if not solved:
        return None
    return ranked(solved)[0]["architecture_id"]


def select(n, architecture_ids: list[str] | None = None) -> list[SupplyArchitecture]:
    """The architectures to solve: all of them, or the named ones."""
    archs = architectures_for(n)
    if not archs:
        raise ArchitectureUnsolved(
            "no_architectures",
            "the network has no PV, wind, genset or grid supply that reaches its load",
        )
    if architecture_ids:
        known = {a.architecture_id: a for a in archs}
        unknown = [a for a in architecture_ids if a not in known]
        if unknown:
            raise ArchitectureUnsolved(
                "unknown_architecture",
                f"not a supply architecture of this network: {', '.join(unknown)}; "
                f"choose from {', '.join(known)}",
            )
        archs = [known[a] for a in dict.fromkeys(architecture_ids)]
    if len(archs) > MAX_ARCHITECTURES:
        raise ArchitectureUnsolved(
            "too_many_architectures",
            f"{len(archs)} supply architectures exceed {MAX_ARCHITECTURES}; "
            "name the ones to compare",
        )
    return archs


SolveFn = Callable[[Any, SupplyArchitecture], Any]


def compare(
    n,
    solve: SolveFn,
    architecture_ids: list[str] | None = None,
    *,
    archs: list[SupplyArchitecture] | None = None,
    progress: Callable[..., None] | None = None,
) -> dict:
    """
    One row per architecture: ``solve(network, arch)`` returns the solved
    network, None when it was not reached (stopped, no budget, deadline), or
    raises :class:`ArchitectureUnsolved` (``infeasible`` / ``failed``).
    """
    archs = select(n, architecture_ids) if archs is None else archs
    groups = classify(n)
    rows = []
    for arch in archs:
        if progress is not None:
            progress(current=arch.architecture_id)
        try:
            solved = solve(omit(n, arch, groups), arch)
        except ArchitectureUnsolved as exc:
            rows.append(unsolved_row(arch, exc.code, exc.detail))
            continue
        rows.append(
            unsolved_row(arch, "not_reached")
            if solved is None
            else row_of(arch, solved)
        )
    rows = ranked(rows)
    return {
        "rows": rows,
        "best_architecture_id": best_of(rows),
        "renewable_fraction_basis": "served_energy_genset_and_grid_import_non_renewable",
    }

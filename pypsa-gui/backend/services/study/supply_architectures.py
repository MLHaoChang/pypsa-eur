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
* **Renewable fraction** is computed here from served energy
  (:func:`renewable_fraction`). homer-parity 01a builds a
  ``renewable_fraction`` KPI; that KPI and this column must share one
  definition, so 01a reuses or replaces this function rather than adding a
  second one.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

from services.adequacy.slack import LOST_SLACK_PREFIX, slack_generator_mask
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
NON_RENEWABLE = ("genset", "grid")
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


def _group_of(component: str, carrier: str, role: str) -> str | None:
    if role in _GRID_ROLES or carrier in _GRID:
        return "grid" if component in ("Generator", "Link") else None
    if "battery" in carrier:
        return "battery"
    if component != "Generator":
        return None
    if carrier.startswith(_PV):
        return "pv"
    if carrier.startswith(_WIND):
        return "wind"
    if carrier in _GENSET:
        return "genset"
    return None


def classify(n) -> dict[str, list[tuple[str, str]]]:
    """``group -> [(component, name), ...]`` for the groups present."""
    slacks = slack_names(n)
    out: dict[str, list[tuple[str, str]]] = {}
    for component in _COMPONENTS:
        df = _static(n, component)
        for name in df.index:
            if component == "Generator" and str(name) in slacks:
                continue
            group = _group_of(
                component, _col(df, name, "carrier"), _col(df, name, "eh_role")
            )
            if group is not None:
                out.setdefault(group, []).append((component, str(name)))
    return {g: out[g] for g in GROUP_ORDER if g in out}


# ── what an architecture removes ──────────────────────────────────────────


def _bus_of(n, component: str, name: str) -> list[str]:
    df = _static(n, component)
    if component in ("Link", "Line", "Transformer"):
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


def _supplied(n, removed: set[tuple[str, str]]) -> bool:
    """Every load bus reaches a non-slack generator through the branches and
    links left (gate G3)."""
    parent: dict[str, str] = {}

    def find(b: str) -> str:
        parent.setdefault(b, b)
        while parent[b] != b:
            parent[b] = parent[parent[b]]
            b = parent[b]
        return b

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for comp in _BRANCHES:
        df = _static(n, comp)
        for name in df.index:
            union(str(df.at[name, "bus0"]), str(df.at[name, "bus1"]))
    for name, buses in _link_buses(n).items():
        if ("Link", name) not in removed:
            for b in buses[1:]:
                union(buses[0], b)
    slacks = slack_names(n)
    g = _static(n, "Generator")
    fed = {
        find(str(g.at[x, "bus"]))
        for x in g.index
        if str(x) not in slacks and ("Generator", str(x)) not in removed
    }
    loads = _static(n, "Load")
    return all(find(str(loads.at[x, "bus"])) in fed for x in loads.index)


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


def _energy(df: pd.DataFrame, names: Iterable[str], w: pd.Series) -> float:
    cols = [c for c in names if c in df.columns]
    if not cols:
        return 0.0
    return float(df[cols].clip(lower=0.0).mul(w, axis=0).to_numpy().sum())


def _demand(n, w: pd.Series) -> float:
    if n.loads.empty:
        return 0.0
    p = n.loads_t.p
    if p.empty or not set(n.loads.index).issubset(p.columns):
        p = n.get_switchable_as_dense("Load", "p_set")
    return float(p[list(n.loads.index)].mul(w, axis=0).to_numpy().sum())


def _non_renewable_energy(n, members, w: pd.Series) -> float:
    """A non-renewable group's energy into the site. A dispatched Link member
    (eh_setup's grid import, ``eh_role = grid_import``) counts by its delivery
    at ``bus1`` and stands for the group, so the generator upstream of it is
    not counted twice; otherwise the group's Generators count by output."""
    links = [name for comp, name in members if comp == "Link"]
    delivered = n.links_t.p1
    if any(name in delivered.columns for name in links):
        return _energy(-delivered, links, w)
    gens = [name for comp, name in members if comp == "Generator"]
    return _energy(n.generators_t.p, gens, w)


def renewable_fraction(n) -> float | None:
    """
    The share of served load not met by a genset or the grid, HOMER's
    definition: ``1 - (genset energy + grid import) / served load``, clipped
    to [0, 1] (exported surplus does not lift it above 1). Grid import counts
    gross (export does not net against it) and as non-renewable; an import
    Link counts once, by its delivery (:func:`_non_renewable_energy`). Served load
    is the load less the slacks' shortfall. Energy is weighted by
    :func:`_weights`. None when the network has no dispatch or serves no load
    (never 1.0 or NaN).

    The ONE definition: homer-parity 01a's ``renewable_fraction`` KPI reuses
    or replaces this function.
    """
    gp = n.generators_t.p
    if gp.empty:
        return None
    w = _weights(n)
    served = _demand(n, w) - _energy(gp, slack_names(n), w)
    if not served > 0:
        return None
    groups = classify(n)
    non_ren = sum(_non_renewable_energy(n, groups.get(g, ()), w) for g in NON_RENEWABLE)
    return min(1.0, max(0.0, 1.0 - non_ren / served))


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
    # The runner's system-cost reading (`runner._read_option`): the LP
    # objective plus its constant.
    objective = _opt_value(getattr(n, "objective", None))
    const = _opt_value(getattr(n, "objective_constant", 0.0)) or 0.0
    cost = None if objective is None else objective + const
    if cost is None:
        unavailable["total_cost_eur_per_year"] = "no_objective"
    groups = classify(n)
    members = [m for g in arch.groups for m in groups.get(g, ())]
    sizes, capex, why = _sizes(n, members)
    if why:
        unavailable["capex_eur"] = why
    rf = renewable_fraction(n)
    if rf is None:
        unavailable["renewable_fraction"] = "no_served_load"
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

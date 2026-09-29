"""
Graph-level diagnosis of a network: islands, and what each one can serve.

`validation_service` checks values — bounds, finiteness, references. Nothing
checked the SHAPE. A network can pass every one of those checks and still be
two disconnected halves, one holding demand and the other holding the plant
that was meant to serve it. The LP answers that with `infeasible` and a linopy
traceback that names neither the island nor the demand, which is why "my model
won't solve" is the single most expensive question a modeller asks.

Two design rules, both about not over-claiming:

* **Connectivity is judged over lines, transformers AND links.** PyPSA's own
  `sub_networks` are the passive AC/DC subnetworks — links are deliberately
  not edges there, because a link is a controllable converter, not an
  impedance. For the ENERGY BALANCE, which is what feasibility turns on, a
  link absolutely connects its buses: an electrolyser bus fed only through a
  link is not islanded, and reporting it as such would send the user chasing
  a line that should not exist.

* **A shortfall is only reported when it is CERTAIN.** Nameplate is an upper
  bound on dispatch (`p_max_pu <= 1` for the shapes that matter), so
  "peak demand exceeds total nameplate" is a one-directional test: when it
  fires the island genuinely cannot be served, and when it does not fire
  nothing is claimed either way. Anything extendable, and no claim is made at
  all — the LP may simply build what is missing.

Findings are WARNINGS wherever they reach preflight, never errors, even for
an island whose demand nothing can serve: a positive VOLL turns that into
lost load at a price rather than an infeasibility, and blocking the run would
refuse a network that solves.
"""
from __future__ import annotations

import math
import re

import pandas as pd

# Branch frames. Every `busN` column is a port — `bus0`/`bus1` on lines and
# transformers, and on a multi-port link however many further outputs it has
# (a CHP's heat port, an electrolyser's heat and oxygen ports, and PyPSA sets
# no upper limit on N). Matched exactly, so a column that merely starts with
# "bus" is never mistaken for a port.
_BRANCH_FRAMES = ("lines", "transformers", "links")
_PORT = re.compile(r"bus\d+")

# Assets that can inject into a bus, and the column holding their nameplate.
_SUPPLY: tuple[tuple[str, str], ...] = (
    ("generators", "p_nom"),
    ("storage_units", "p_nom"),
)


class _Union:
    """
    Union-find over bus names.

    Deliberately dependency-free: this is a dozen lines, and it keeps a graph
    library out of the preflight path.
    """

    def __init__(self, items) -> None:
        self._parent = {item: item for item in items}

    def find(self, item: str) -> str:
        parent = self._parent
        root = item
        while parent[root] != root:
            root = parent[root]
        while parent[item] != root:          # path compression
            parent[item], item = root, parent[item]
        return root

    def union(self, a: str, b: str) -> None:
        if a not in self._parent or b not in self._parent:
            return                            # a dangling ref; not our finding
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def _as_float(value, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _ports(df) -> list[str]:
    """The frame's bus-port columns, `bus0` first."""
    return sorted((c for c in df.columns
                   if isinstance(c, str) and _PORT.fullmatch(c)),
                  key=lambda c: int(c[3:]))


def _port_bus(value) -> str | None:
    """A port's bus name, or None for an unused port (empty or NaN)."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    name = str(value)
    return name or None


def _peak_loads(n, island_of: dict[str, int], count: int) -> list[float]:
    """
    Each island's highest simultaneous demand over the horizon.

    Time-varying `p_set` wins over the static column where it exists — that is
    what PyPSA reads — and the sum is taken PER SNAPSHOT before the max, so
    two loads peaking in different hours do not add into a peak that never
    happens. One pass over the loads for every island at once: preflight and
    the chat tool both call this on networks with hundreds of islands.
    """
    peaks = [0.0] * count
    loads = getattr(n, "loads", None)
    if loads is None or loads.empty or "bus" not in loads.columns:
        return peaks

    frame = getattr(n.loads_t, "p_set", None)
    profiled = set(getattr(frame, "columns", []))
    static = loads["p_set"] if "p_set" in loads.columns else None
    dynamic: dict[int, list] = {}
    for name, bus in loads["bus"].items():
        island = island_of.get(str(bus))
        if island is None:
            continue                          # a dangling ref; not our finding
        if name in profiled:
            dynamic.setdefault(island, []).append(name)
        elif static is not None:
            peaks[island] += _as_float(static.at[name])

    for island, names in dynamic.items():
        try:
            series = frame[names].sum(axis=1)
            finite = series[series.notna()]
            if not finite.empty:
                peaks[island] += float(finite.max())
        except (KeyError, TypeError, ValueError):
            pass
    return peaks


def _supplies(n, island_of: dict[str, int], count: int) -> list[dict]:
    """
    Per island: nameplate injection capacity, whether it can grow, and whether
    any supply asset sits there at all.

    `has_supply_asset` is the plain presence test — any Generator,
    StorageUnit or Store, whatever its size — and is kept separate from the
    nameplate on purpose: "nothing here can inject" and "what is here is too
    small" are different findings, and the chat tool reports the first.
    """
    out = [{"nameplate_mw": 0.0, "extendable": False, "has_supply_asset": False}
           for _ in range(count)]
    for frame_name, nom_col in _SUPPLY:
        df = getattr(n, frame_name, None)
        if df is None or df.empty or "bus" not in df.columns:
            continue
        noms = df[nom_col] if nom_col in df.columns else None
        ext_col = f"{nom_col}_extendable"
        exts = df[ext_col] if ext_col in df.columns else None
        for name, bus in df["bus"].items():
            island = island_of.get(str(bus))
            if island is None:
                continue
            row = out[island]
            row["has_supply_asset"] = True
            if noms is not None:
                row["nameplate_mw"] += _as_float(noms.at[name])
            if exts is not None and bool(exts.at[name]):
                row["extendable"] = True
    # A Store discharges into its bus too. It cannot be a standing source over
    # a horizon — it must be charged first — so it never counts toward the
    # nameplate, but its presence is enough to withdraw the shortfall claim.
    stores = getattr(n, "stores", None)
    if stores is not None and not stores.empty and "bus" in stores.columns:
        for bus in stores["bus"]:
            island = island_of.get(str(bus))
            if island is not None:
                out[island]["extendable"] = True
                out[island]["has_supply_asset"] = True
    return out


def _verdict(peak_load: float, supply: dict) -> tuple[str, str]:
    """One island's structural reading, and the sentence that explains it."""
    if peak_load <= 0:
        return "no_demand", (
            "no load in this island — whatever is built here serves nothing, "
            "and nothing outside it can be reached from here")
    if supply["extendable"]:
        return "ok", (
            "demand here can be met by building: something in this island is "
            "extendable, so no shortfall can be claimed before the solve")
    if supply["nameplate_mw"] <= 0:
        return "no_supply", (
            f"{peak_load:g} MW of demand and NO injection capacity at all. "
            "The LP is infeasible unless VOLL > 0, in which case every MWh "
            "here is lost load")
    if peak_load > supply["nameplate_mw"]:
        return "under_capacity", (
            f"peak demand {peak_load:g} MW exceeds total nameplate "
            f"{supply['nameplate_mw']:g} MW and nothing here is extendable. "
            "Nameplate is an upper bound on dispatch, so this shortfall is "
            "certain, not indicative")
    return "ok", "demand here is within the installed nameplate"


def analyse_topology(n) -> dict:
    """
    Islands, their supply/demand balance, and the buses connected to nothing.

    Pure and cheap: one pass over the branch frames, one over the asset
    frames. Safe to call from preflight, which runs before every solve, and
    the one bus-graph walk in the backend — the chat tool `diagnose_network`
    reads its islands rather than walking the graph a second time.

    `isolated_buses` means degree zero: no branch port names the bus at all.
    That is narrower than "an island of one", which also covers a bus whose
    only branch is a self-loop or points at a bus that does not exist;
    callers wanting the island reading take the one-bus islands.
    """
    buses = [str(name) for name in getattr(n, "buses").index]
    if not buses:
        return {"islands": [], "n_islands": 0, "isolated_buses": [],
                "counts": {"islands": 0, "isolated_buses": 0,
                           "islands_without_supply": 0}}

    union = _Union(buses)
    degree = dict.fromkeys(buses, 0)
    for frame_name in _BRANCH_FRAMES:
        df = getattr(n, frame_name, None)
        if df is None or df.empty:
            continue
        ports = _ports(df)
        if not ports:
            continue
        for row in df[ports].itertuples(index=False):
            attached = [bus for bus in map(_port_bus, row) if bus in degree]
            for bus in attached:
                degree[bus] += 1
            for other in attached[1:]:
                union.union(attached[0], other)

    grouped: dict[str, list[str]] = {}
    for bus in buses:
        grouped.setdefault(union.find(bus), []).append(bus)

    # Sorted by size then by first bus name: a stable id across calls matters,
    # because these ids appear in messages a user reads twice.
    ordered = sorted(grouped.values(), key=lambda m: (-len(m), m[0]))
    island_of = {bus: index for index, members in enumerate(ordered)
                 for bus in members}
    peaks = _peak_loads(n, island_of, len(ordered))
    supplies = _supplies(n, island_of, len(ordered))

    islands = []
    for index, members in enumerate(ordered):
        peak_load, supply = peaks[index], supplies[index]
        verdict, reason = _verdict(peak_load, supply)
        islands.append({
            "id": index,
            "n_buses": len(members),
            "buses": sorted(members),
            "peak_load_mw": peak_load,
            "nameplate_mw": supply["nameplate_mw"],
            "extendable": supply["extendable"],
            "has_supply_asset": supply["has_supply_asset"],
            "verdict": verdict,
            "reason": reason,
        })

    isolated = sorted(bus for bus in buses if degree[bus] == 0)
    unserved = [i for i in islands
                if i["verdict"] in ("no_supply", "under_capacity")]
    return {
        "islands": islands,
        "n_islands": len(islands),
        "isolated_buses": isolated,
        "counts": {
            "islands": len(islands),
            "isolated_buses": len(isolated),
            "islands_without_supply": len(unserved),
        },
    }


def topology_issues(n) -> list:
    """
    The subset of `analyse_topology` worth interrupting a user for, as
    validation Issues.

    Warnings only. An island whose demand nothing can serve is an
    infeasibility at VOLL = 0 and priced lost load above it, and this function
    does not know the solver config — so it says which it is and blocks
    neither.
    """
    from services.validation_service import Issue

    report = analyse_topology(n)
    out: list[Issue] = []
    if report["n_islands"] > 1:
        out.append(Issue(
            "warning", "network_islanded", "", "",
            f"the network is {report['n_islands']} disconnected islands "
            f"(counting lines, transformers and links as connections). Power "
            f"cannot flow between them, so each has to balance on its own."))
    for island in report["islands"]:
        if island["verdict"] in ("no_supply", "under_capacity"):
            head = ", ".join(island["buses"][:4])
            if island["n_buses"] > 4:
                head += f", … ({island['n_buses']} buses)"
            out.append(Issue(
                "warning", f"island_{island['verdict']}", "", "",
                f"island {island['id']} ({head}): {island['reason']}."))
    return out

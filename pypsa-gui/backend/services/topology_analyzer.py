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

# Branch frames and their bus columns. `bus2`..`bus4` on multi-port links are
# added dynamically — an electrolyser's heat port connects buses too.
_BRANCHES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("lines", ("bus0", "bus1")),
    ("transformers", ("bus0", "bus1")),
    ("links", ("bus0", "bus1")),
)
_EXTRA_LINK_BUSES = ("bus2", "bus3", "bus4")

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


def _peak_load(n, buses: set[str]) -> float:
    """
    The island's highest simultaneous demand over the horizon.

    Time-varying `p_set` wins over the static column where it exists — that is
    what PyPSA reads — and the sum is taken PER SNAPSHOT before the max, so
    two loads peaking in different hours do not add into a peak that never
    happens.
    """
    if n.loads.empty:
        return 0.0
    names = [str(name) for name in n.loads.index
             if str(n.loads.at[name, "bus"]) in buses]
    if not names:
        return 0.0

    frame = getattr(n.loads_t, "p_set", None)
    dynamic = [name for name in names
               if frame is not None and name in getattr(frame, "columns", [])]
    static = [name for name in names if name not in dynamic]

    peak = sum(_as_float(n.loads.at[name, "p_set"]) for name in static)
    if dynamic:
        try:
            series = frame[dynamic].sum(axis=1)
            finite = series[series.notna()]
            if not finite.empty:
                peak += float(finite.max())
        except (KeyError, TypeError, ValueError):
            pass
    return peak


def _supply(n, buses: set[str]) -> dict:
    """Nameplate injection capacity in an island, and whether it can grow."""
    nameplate, extendable = 0.0, False
    for frame_name, nom_col in _SUPPLY:
        df = getattr(n, frame_name, None)
        if df is None or df.empty or "bus" not in df.columns:
            continue
        for name in df.index:
            if str(df.at[name, "bus"]) not in buses:
                continue
            nameplate += _as_float(df.at[name, nom_col])
            ext_col = f"{nom_col}_extendable"
            if ext_col in df.columns and bool(df.at[name, ext_col]):
                extendable = True
    # A Store discharges into its bus too. It cannot be a standing source over
    # a horizon — it must be charged first — so it never counts toward the
    # nameplate, but its presence is enough to withdraw the shortfall claim.
    stores = getattr(n, "stores", None)
    if stores is not None and not stores.empty and "bus" in stores.columns:
        if any(str(stores.at[name, "bus"]) in buses for name in stores.index):
            extendable = True
    return {"nameplate_mw": nameplate, "extendable": extendable}


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
    frames. Safe to call from preflight, which runs before every solve.
    """
    buses = [str(name) for name in getattr(n, "buses").index]
    if not buses:
        return {"islands": [], "n_islands": 0, "isolated_buses": [],
                "counts": {"islands": 0, "isolated_buses": 0,
                           "islands_without_supply": 0}}

    union = _Union(buses)
    degree = dict.fromkeys(buses, 0)
    for frame_name, cols in _BRANCHES:
        df = getattr(n, frame_name, None)
        if df is None or df.empty:
            continue
        columns = cols + (_EXTRA_LINK_BUSES if frame_name == "links" else ())
        present = [c for c in columns if c in df.columns]
        for name in df.index:
            attached = [str(df.at[name, c]) for c in present]
            attached = [b for b in attached if b in degree]
            for bus in attached:
                degree[bus] += 1
            for other in attached[1:]:
                union.union(attached[0], other)

    grouped: dict[str, list[str]] = {}
    for bus in buses:
        grouped.setdefault(union.find(bus), []).append(bus)

    islands = []
    # Sorted by size then by first bus name: a stable id across calls matters,
    # because these ids appear in messages a user reads twice.
    for index, members in enumerate(
            sorted(grouped.values(), key=lambda m: (-len(m), m[0]))):
        member_set = set(members)
        peak_load = _peak_load(n, member_set)
        supply = _supply(n, member_set)
        verdict, reason = _verdict(peak_load, supply)
        islands.append({
            "id": index,
            "n_buses": len(members),
            "buses": sorted(members),
            "peak_load_mw": peak_load,
            "nameplate_mw": supply["nameplate_mw"],
            "extendable": supply["extendable"],
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

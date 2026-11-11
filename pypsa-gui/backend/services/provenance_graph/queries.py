"""
Building the graph and its two queries, `staleness` and `why` (spec "The two queries").

A comparator per edge kind answers "does the recorded value still match the current one?"; the producer module
owns it (`money.compare_priced_through`). An edge kind with no comparator, or an edge that recorded nothing,
compared nothing: the walk reads `uncompared` there, never `fresh` (P1). Rebuilt per query, never cached (P5).
"""

from __future__ import annotations

from collections.abc import Callable

from services.provenance_graph import commercial, money
from services.provenance_graph.types import (
    WALKED,
    Broken,
    CellStamp,
    Edge,
    Graph,
    Node,
    State,
    Step,
    Verdict,
    Walk,
)

#: The one comparison per edge kind. `computed_in` joins with the runs slice (04).
COMPARATORS: dict[str, Callable[[Graph, Edge], Broken | None]] = {
    "priced_through": money.compare_priced_through,
    "stamped_with": commercial.compare_stamp,
}


class Builder:
    """Collects the nodes and edges producers declare; `build` freezes them. A node added twice must agree."""

    def __init__(self, n) -> None:
        self.n = n
        self._nodes: dict[str, Node] = {}
        self._edges: dict[tuple, Edge] = {}

    def node(self, node: Node) -> None:
        had = self._nodes.setdefault(node.id, node)
        if had.kind != node.kind:
            raise ValueError(f"node {node.id} added as {had.kind} and as {node.kind}")

    def has(self, node_id: str) -> bool:
        return node_id in self._nodes

    def edge(self, edge: Edge) -> None:
        self._edges.setdefault((edge.kind, edge.src, edge.dst), edge)

    def build(self) -> Graph:
        return Graph(dict(self._nodes), tuple(self._edges.values()), self.n)


def money_graph(n, assets) -> Graph:
    """The money path of `assets` (`(component_class, name)` pairs) on network `n`."""
    b = Builder(n)
    for cls, name in assets:
        money.add_asset(b, cls, name)
    return b.build()


def commercial_graph(config: dict | None) -> Graph:
    """The `commercial.provenance` cells of the commercial config `config` (`Graph.ctx`), read once by the caller."""
    b = Builder(config)
    commercial.add_cells(b, config)
    return b.build()


def cell_verdict(config: dict | None, key: str) -> Verdict:
    """Whether the cell at provenance key `key` still holds what its stamp approved (issue 08). A key with no
    stored entry has no stamp to compare: uncompared, never fresh."""
    return staleness(commercial_graph(config), commercial.cell_id(key))


def cell_wire(config: dict | None, key: str) -> dict | None:
    """One cell for a surface: its stored entry, `state`, the entry fields that were `compared` and the ones
    `uncompared`, and `stale` (TCU-05's shape) when a compared field broke. None for a key with no entry."""
    entry = ((config or {}).get("provenance") or {}).get(key)
    if not isinstance(entry, dict):
        return None
    verdict = cell_verdict(config, key)
    compared, uncompared = commercial.fields(config, key, entry)
    out = {
        "entry": entry,
        "state": verdict.state,
        "compared": compared,
        "uncompared": uncompared,
    }
    if verdict.broken:
        out["stale"] = verdict.broken[0].wire()
    return out


def walked(g: Graph, node_id: str) -> list[Edge]:
    """The edges both queries follow from a node: the `WALKED` kinds, and a `stamped_with` edge only when it
    records a commercial cell (`CellStamp`). An asset's stamp describes its input; it is not a comparison."""
    return [
        e
        for e in g.edges
        if e.src == node_id
        and (
            e.kind in WALKED
            or (e.kind == "stamped_with" and isinstance(e.recorded, CellStamp))
        )
    ]


def _check(g: Graph, edge: Edge) -> tuple[State, Broken | None]:
    compare = COMPARATORS.get(edge.kind)
    if compare is None or edge.recorded is None:
        return "uncompared", None
    broke = compare(g, edge)
    return ("fresh", None) if broke is None else ("stale", broke)


def staleness(g: Graph, node_id: str) -> Verdict:
    """Walk `derived_from`, `priced_through` and `computed_in` from `node_id`. Stale when an edge broke (its
    recorded value no longer matches, or the current one cannot be read); uncompared when something on the walk
    compared nothing (named in `uncompared`); fresh only when every edge on the walk compared and matched, and at
    least one did (a walk that compared nothing, such as a `derived_from` cycle, names its start: P1)."""
    broken: list[Broken] = []
    compared = 0
    uncompared: list[str] = []
    seen: set[str] = set()

    def visit(nid: str) -> None:
        nonlocal compared
        if nid in seen:
            return
        seen.add(nid)
        edges = walked(g, nid)
        if not edges:
            uncompared.append(nid)
        for e in edges:
            state, broke = _check(g, e)
            compared += state != "uncompared"
            if broke is not None:
                broken.append(broke)
            elif state == "uncompared" and e.kind != "derived_from":
                uncompared.append(e.dst)
            # A compared edge's input is the comparison itself; only derived_from walks on.
            if e.kind == "derived_from":
                visit(e.dst)

    visit(node_id)
    if not compared and not uncompared:
        uncompared.append(node_id)
    state: State = "stale" if broken else "uncompared" if uncompared else "fresh"
    return Verdict(node_id, state, tuple(broken), tuple(uncompared))


def surface_verdicts(g: Graph, surface_id: str) -> list[Verdict]:
    """A surface asks through its `shown_in` edges: the verdict of every value it shows."""
    return [staleness(g, nid) for nid in g.shown_at(surface_id)]


def _stamp(g: Graph, node_id: str) -> dict | None:
    """The `stamped_with` record of a node, or of the input a derived node replaces (same field)."""
    for nid in (node_id, node_id.replace("derived:", "input:", 1)):
        for e in g.edges:
            if e.kind == "stamped_with" and e.src == nid:
                return (
                    e.recorded.entry
                    if isinstance(e.recorded, CellStamp)
                    else e.recorded
                )
    return None


def why(g: Graph, node_id: str) -> Walk:
    """The same walk as `staleness`, as ordered steps: each node, the edge that reached it, its stamp and its
    own state (a value an edge reached through a broken edge reads that edge's state)."""
    steps: list[Step] = []
    seen: set[str] = set()

    def visit(nid: str, via: Edge | None, state: State) -> None:
        if nid in seen:
            return
        seen.add(nid)
        node = g.nodes.get(nid) or Node("input", nid)
        steps.append(Step(node, via, _stamp(g, nid), state))
        for e in walked(g, nid):
            visit(
                e.dst,
                e,
                staleness(g, e.dst).state
                if e.kind == "derived_from"
                else _check(g, e)[0],
            )

    visit(node_id, None, staleness(g, node_id).state)
    return Walk(node_id, tuple(steps))


def stale_entries(n, component_class: str, name: str) -> dict[str, dict]:
    """The wire every money surface reads (`routers/network.py`, the Guided ledger): one asset's recorded
    entries (`provenance.get`), with `stale` (TCU-05's shape: field, recorded, current_field, current, reason)
    added to each money field whose verdict is stale. A field with nothing to say is returned as it was."""
    b = Builder(n)
    entries = money.add_asset(b, component_class, name)
    g = b.build()
    out = dict(entries)
    for field in (
        money.SCALED_FIELDS
    ):  # a field never recorded has no node: it reads uncompared, never stale
        verdict = staleness(g, money.derived_id(component_class, name, field))
        if verdict.state == "stale":
            out[field] = {**entries[field], "stale": verdict.broken[0].wire()}
    return out

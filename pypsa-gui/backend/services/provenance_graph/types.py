"""
The provenance graph's types (spec `.scratch/provenance-graph/spec.md`, plan GO in issue 01).

Frozen records only: a `Node`, an `Edge`, the `Graph` that holds them and the network they were read from, and
the two answers, `Verdict` (is this value still current?) and `Walk` (why does it have this value?). An unknown
node or edge kind is refused, so a producer cannot invent one silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

#: `memory` is reserved (MEMORY-1): the kind exists so adding it is not a schema change; nothing builds one yet.
NODE_KINDS = ("input", "derived", "run", "surface", "memory")
#: `computed_in` is built by the runs slice (04); `recalled_from` is reserved (MEMORY-1).
EDGE_KINDS = (
    "derived_from",
    "priced_through",
    "stamped_with",
    "computed_in",
    "shown_in",
    "recalled_from",
)
RESERVED_NODE_KINDS = ("run", "memory")
RESERVED_EDGE_KINDS = ("computed_in", "recalled_from")
#: The edges `staleness` and `why` walk, from a value towards what it was made from.
WALKED = ("derived_from", "priced_through", "computed_in")

State = Literal["fresh", "stale", "uncompared"]


@dataclass(frozen=True)
class Node:
    kind: str
    id: str
    value: Any = None
    unit: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in NODE_KINDS:
            raise ValueError(
                f"unknown node kind {self.kind}; expected one of {NODE_KINDS}"
            )


@dataclass(frozen=True)
class Unrecorded:
    """What an edge recorded when its producer recorded nothing it can be compared with: the reason why."""

    reason: str


@dataclass(frozen=True)
class Edge:
    """`src` → `dst`; `recorded` is what the producer recorded when it made the link (a scale input, a stamp),
    `Unrecorded` when it recorded nothing comparable, None for an edge that carries no value (`derived_from`,
    `shown_in`)."""

    kind: str
    src: str
    dst: str
    recorded: Any = None

    def __post_init__(self) -> None:
        if self.kind not in EDGE_KINDS:
            raise ValueError(
                f"unknown edge kind {self.kind}; expected one of {EDGE_KINDS}"
            )


@dataclass(frozen=True)
class CellStamp:
    """What a `stamped_with` edge records for a `commercial.provenance` cell: its key and the stored entry. The
    walk follows a stamp edge only when it carries one (`queries.walked`); an asset's stamp (a dict) is not
    compared."""

    key: str
    entry: Any


@dataclass(frozen=True)
class Graph:
    """Built per query from records the app already stores (`ctx`: the network read); it writes nothing."""

    nodes: dict[str, Node]
    edges: tuple[Edge, ...]
    ctx: Any = field(default=None, compare=False, repr=False)

    def out(self, node_id: str, kinds: tuple[str, ...] = WALKED) -> list[Edge]:
        return [e for e in self.edges if e.src == node_id and e.kind in kinds]

    def shown_at(self, surface_id: str) -> list[str]:
        return [
            e.src for e in self.edges if e.kind == "shown_in" and e.dst == surface_id
        ]


@dataclass(frozen=True)
class Broken:
    """One edge whose recorded value no longer matches the current one (or cannot be read). The four value
    fields are the wire's `stale` keys (TCU-05), so every surface words it the same (`staleNote`)."""

    edge: str
    src: str
    dst: str
    field: str | None
    recorded: Any
    current_field: str | None
    current: Any
    reason: str

    def wire(self) -> dict:
        return {
            "field": self.field,
            "recorded": self.recorded,
            "current_field": self.current_field,
            "current": self.current,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Verdict:
    """`stale` when any walked edge broke; else `uncompared` when anything on the walk compared nothing (an edge
    with no recorded value, a node with no edge): never `fresh` by default (P1, #1170); else `fresh`."""

    node: str
    state: State
    broken: tuple[Broken, ...] = ()
    uncompared: tuple[str, ...] = ()


@dataclass(frozen=True)
class Step:
    node: Node
    via: Edge | None  # the edge from the previous step; None on the first
    stamp: dict | None  # the node's provenance stamp (`stamped_with`), if any
    state: State


@dataclass(frozen=True)
class Walk:
    node: str
    steps: tuple[Step, ...]

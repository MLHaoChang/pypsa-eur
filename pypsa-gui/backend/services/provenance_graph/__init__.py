"""
The provenance graph: one answer to "is this number still current?" and "why does it have this value?"
(`.scratch/provenance-graph/spec.md`; plan GO with conditions P1–P8 in issue 01).

A small typed graph held in process and rebuilt per query from records the app already stores. It writes
nothing; `asset_schema.provenance` stays the only writer of `asset_params`. Slice 02 fills the money path
(`money.py`); later slices add their producers next to the code that records them.
"""
from services.provenance_graph.queries import (
    COMPARATORS,
    Builder,
    money_graph,
    stale_entries,
    staleness,
    surface_verdicts,
    why,
)
from services.provenance_graph.types import (
    EDGE_KINDS,
    NODE_KINDS,
    Broken,
    Edge,
    Graph,
    Node,
    Step,
    Unrecorded,
    Verdict,
    Walk,
)

__all__ = ["COMPARATORS", "EDGE_KINDS", "NODE_KINDS", "Broken", "Builder", "Edge", "Graph", "Node", "Step",
           "Unrecorded", "Verdict", "Walk", "money_graph", "stale_entries", "staleness", "surface_verdicts", "why"]

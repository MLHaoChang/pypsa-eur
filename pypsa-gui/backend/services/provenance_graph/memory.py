"""
The memory producer (slice 07, MEMORY-1): a value the user accepted from a memory item links to that item.

`memory_parameters.apply` stamps the accepted field `source="memory:<id>@v<n>"` with `approved_by`/`approved_at`
(COPILOT-1: a memory item is only ever offered, so the stamp exists only after the user accepts). This module reads
that stamp: an `input` node with it gets a `memory` node and a `recalled_from` edge carrying a `MemoryRecall`. A
source that merely looks like a memory id, with no approval recorded, builds no edge: nothing was accepted.

Versions are immutable and confirmed items are never deleted, so the edge has nothing to compare: retiring or
editing the item leaves the value's own stamp as it was. The verdict reads `from_memory`, never `fresh` (P1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from services.provenance_graph.types import Edge, Node

PREFIX = "memory:"
#: `memory_parameters.SOURCE` = `memory:{id}@v{version}`; the id is the item's public id (`mem_…`).
_SOURCE = re.compile(r"^memory:(?P<id>[A-Za-z0-9_\-]+)@v(?P<version>[1-9][0-9]*)$")
STATE_TEXT = "from memory v{version}, user-approved"


@dataclass(frozen=True)
class MemoryRecall:
    """What a `recalled_from` edge recorded: the item and version, who approved it and when, and the source it
    replaced (all from the accepted value's stamp)."""

    item: str
    version: int
    approved_by: str
    approved_at: str
    replaces: str | None = None

    @property
    def state(self) -> str:
        return STATE_TEXT.format(version=self.version)


def memory_id(item: str, version: int) -> str:
    return f"{PREFIX}{item}@v{version}"


def recall_of(entry: dict) -> MemoryRecall | None:
    """The recall an entry's stamp records, or None when it is not a memory source or was not approved."""
    match = _SOURCE.match(str(entry.get("source") or ""))
    approved_by, approved_at = entry.get("approved_by"), entry.get("approved_at")
    if match is None or not approved_by or not approved_at:
        return None
    return MemoryRecall(
        match["id"],
        int(match["version"]),
        str(approved_by),
        str(approved_at),
        entry.get("replaces"),
    )


def add_recall(b, input_id: str, entry: dict) -> None:
    """Add the `memory` node and the `recalled_from` edge for the input `input_id`, when its entry was accepted
    from a memory item."""
    recall = recall_of(entry)
    if recall is None:
        return
    mid = memory_id(recall.item, recall.version)
    b.node(Node("memory", mid))
    b.edge(Edge("recalled_from", input_id, mid, recorded=recall))

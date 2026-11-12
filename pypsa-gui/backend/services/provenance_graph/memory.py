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
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from services.provenance_graph.types import Edge, Node

PREFIX = "memory:"
#: `memory_parameters.SOURCE` = `memory:{id}@v{version}`; the id is the item's public id (`mem_…`).
_SOURCE = re.compile(r"^memory:(?P<id>[A-Za-z0-9_\-]+)@v(?P<version>[1-9][0-9]*)$")
STATE_TEXT = "from memory v{version}, user-approved"
UNCONFIRMED = "memory v{version} recorded on the value; not confirmed"
NOT_VISIBLE = "not visible to you"
NOT_READ = "memory item could not be read"


class ItemNotVisible(LookupError):
    """A resolver raises this when the viewer may not read the item at its scope (or it is not there: the memory
    side answers both the same, so a reader cannot probe for items it may not see)."""


@dataclass(frozen=True)
class MemoryRecall:
    """What a `recalled_from` edge recorded: the item and version, who approved it and when, and the source it
    replaced (all from the accepted value's stamp)."""

    item: str
    version: int
    approved_by: str
    approved_at: str
    replaces: str | None = None
    #: What the viewer's own read of the item returned, when it could be read; `None` otherwise (the stamp alone
    #: is text any imported file can carry, so it is never enough: GK B P2).
    item_read: dict | None = field(default=None, compare=False)
    #: Why the item was not confirmed (`NOT_VISIBLE`, `NOT_READ`); `None` when it was.
    note: str | None = field(default=NOT_READ, compare=False)

    @property
    def confirmed(self) -> bool:
        return self.item_read is not None

    @property
    def state(self) -> str:
        if not self.confirmed:
            return UNCONFIRMED.format(version=self.version)
        return STATE_TEXT.format(version=self.version)


def memory_id(item: str, version: int) -> str:
    return f"{PREFIX}{item}@v{version}"


def recall_of(entry: dict) -> MemoryRecall | None:
    """The recall an entry's stamp records, or None when it is not a memory source or was not approved. Fail
    closed (GK B P1): anything malformed (a non-string source, no version, a version below 1, a missing or
    non-string approver or time) builds no edge."""
    source = entry.get("source")
    approved_by, approved_at = entry.get("approved_by"), entry.get("approved_at")
    if not isinstance(source, str) or not _SOURCE.fullmatch(source):
        return None
    if not (isinstance(approved_by, str) and approved_by):
        return None
    if not (isinstance(approved_at, str) and approved_at):
        return None
    match = _SOURCE.fullmatch(source)
    return MemoryRecall(
        match["id"],
        int(match["version"]),
        str(approved_by),
        str(approved_at),
        entry.get("replaces"),
    )


Reader = Callable[[str, int], "dict | None"]


def reader_for(db, user) -> Reader:
    """The viewer's own read of one item version (`memory_store.node`): the summary, kind, owner scope and
    evidence they may see. Raises `ItemNotVisible` for a signed-out viewer and for a 404 (the store answers an
    unknown item and one the viewer may not read the same, so a reader cannot probe for either)."""

    def read(item: str, version: int) -> dict | None:
        from services import memory_store

        if user is None:
            raise ItemNotVisible(item)
        try:
            return memory_store.node(db, user, item, version)
        except memory_store.MemoryError as exc:
            if exc.status == 404:
                raise ItemNotVisible(item) from exc
            raise

    return read


def _confirm(read: Reader | None, recall: MemoryRecall) -> MemoryRecall:
    """`recall` with the item as the viewer reads it. A stamp the store does not confirm for this viewer stays
    unconfirmed with the reason: it is not shown as "user-approved" (GK B P2), and says nothing about the item."""
    if read is None:
        return recall
    try:
        item = read(recall.item, recall.version)
    except ItemNotVisible:
        return replace(recall, note=NOT_VISIBLE)
    except Exception:  # noqa: BLE001 - an unreadable item must not fail the walk
        return replace(recall, note=NOT_READ)
    if not isinstance(item, dict) or not item:
        return replace(recall, note=NOT_READ)
    return replace(recall, item_read=item, note=None)


def add_recall(b, input_id: str, entry: dict) -> None:
    """Add the `memory` node and the `recalled_from` edge for the input `input_id`, when its entry carries a
    memory approval stamp. `b.memory` is the viewer's reader; the edge reads `from_memory` only when it confirms
    the item at that version."""
    recall = recall_of(entry)
    if recall is None:
        return
    recall = _confirm(getattr(b, "memory", None), recall)
    mid = memory_id(recall.item, recall.version)
    b.node(Node("memory", mid))
    b.edge(Edge("recalled_from", input_id, mid, recorded=recall))

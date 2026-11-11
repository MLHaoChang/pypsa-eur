"""
The `commercial.provenance` cells on the graph (provenance-graph issue 08).

Each stored entry (`document_values_commercial.provenance`: key `<target>:<entity>/<path>`) is an `input` node with
a `stamped_with` edge to its source, recording the entry as a `CellStamp`. `compare_stamp` asks whether the cell
still holds what the stamp approved: of the entry's fields it compares the ones the commercial config can supply
now (`COMPARED`, read by `document_values_commercial.cell_now`); `fields` names the rest. A cell
whose item, period, tier or contract is gone cannot be read, so it is stale, never fresh (P1).

`Graph.ctx` is the commercial config the caller read once; nothing here reads the store or writes (P5).
"""

from __future__ import annotations

import re

from services.provenance_graph.types import Broken, CellStamp, Edge, Node

#: The entry fields the commercial config can supply now, in the order they are compared.
# A tariff cell compares the tariff's own currency at approval (`tariff_currency`) with the one it states or
# implies now: the entry's `currency` is what the document stated, which the plan checked, not a property of the
# cell. An entry written before `tariff_currency` was recorded leaves it uncompared.
COMPARED = ("value", "unit", "tariff_currency", "currency_year")
MALFORMED = "stamp record malformed"
GONE = "cell no longer exists"
UNREADABLE = "cell cannot be read now"
_KEY = re.compile(r"^(tariff|ppa):[^/]+/.+$", re.S)


def cell_id(key: str) -> str:
    """The node of a provenance key: the key itself after a fixed prefix, so two keys never share a node."""
    return f"input:commercial/{key}"


def _source_id(key: str, entry) -> str:
    source = entry.get("source") if isinstance(entry, dict) else None
    # An entry that names no source keeps a node of its own: it never merges into another cell's source.
    return (
        f"source:{source}"
        if isinstance(source, str) and source
        else f"source-unrecorded:{key}"
    )


def add_cells(b, commercial: dict | None) -> None:
    """Add every stored `commercial.provenance` entry of `commercial` to the builder `b`."""
    entries = (commercial or {}).get("provenance")
    for key, entry in (entries if isinstance(entries, dict) else {}).items():
        b.node(Node("input", cell_id(key)))
        b.edge(
            Edge(
                "stamped_with",
                cell_id(key),
                _source_id(key, entry),
                recorded=CellStamp(key, entry),
            )
        )


def fields(config, key: str, entry: dict) -> tuple[list[str], list[str]]:
    """`(compared, uncompared)`: the entry's fields the comparison covers, and the ones it does not because the
    config supplies no current value for them (a source, an approver, the currency the document stated)."""
    from services import document_values_commercial as DC

    try:
        now = DC.cell_now(config, key)
    except DC.CELL_GONE:
        now = {"value": None}
    compared = _compared(now, entry)
    return compared, [f for f in entry if f not in compared]


def _compared(now: dict, entry: dict) -> list[str]:
    """The stamp fields compared: each one the entry records and the config supplies now. A field that moved
    to or from None differs, so a currency or money year that appeared or vanished is never fresh."""
    return [f for f in COMPARED if f in entry and f in now]


def compare_stamp(g, edge: Edge) -> Broken | None:
    """None when the cell still holds the stamp's value, unit, currency and money year (each one the config
    supplies now); else what broke. A malformed stamp or a cell that is gone is stale, never fresh. Only
    `CELL_GONE` is read as gone: any other error is a bug and propagates."""
    from services import document_values_commercial as DC

    def broken(field, recorded, current, reason) -> Broken:
        return Broken(
            "stamped_with",
            edge.src,
            edge.dst,
            field,
            recorded,
            stamp.key,
            current,
            reason,
        )

    stamp = edge.recorded
    entry = stamp.entry
    if (
        not isinstance(entry, dict)
        or "value" not in entry
        or not _KEY.match(str(stamp.key))
        or not isinstance(g.ctx, dict)
    ):
        return broken(None, None, None, MALFORMED)
    try:
        DC.cell_value(g.ctx, stamp.key)
    except DC.CELL_GONE:
        return broken("value", entry["value"], None, GONE)
    try:
        now = DC.cell_now(g.ctx, stamp.key)
    except DC.CELL_GONE:  # the value is there, but not its unit or currency (an item with an unknown unit)
        return broken("value", entry["value"], None, UNREADABLE)
    for field in _compared(now, entry):
        if not DC._same(now[field], entry[field]):
            return broken(
                field,
                entry[field],
                now[field],
                "cell edited since approval"
                if field == "value"
                else f"{field} differs from the approved one",
            )
    return None

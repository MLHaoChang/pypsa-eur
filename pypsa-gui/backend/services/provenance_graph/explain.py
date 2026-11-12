"""
"Why this number" (provenance-graph issue 03): the wire of `why(node)` for the assistant's read tool.

`explain(n, component, name, field)` builds one asset's money path (`money.add_asset`), walks it with `why` and
returns, outcome first, each step as plain JSON: the node, the value the network holds for it now and its unit,
its provenance stamp (and the stamp keys it lacks), the edge that reached it with what that edge recorded, the
step's state and, on a broken edge, what was compared (recorded vs current) and what was not. A price scaled at
pricing time also carries the pack price it was scaled from, worked back as arithmetic (value ÷ recorded scale),
or `not_established` when that division cannot be made (GK B (42) X3).

`explain_ledger_row(n, key)` does the same for each asset field a Guided ledger row compiles to
(`asset_view.LEDGER_FIELDS`).

It reads only (COPILOT-1): nothing here writes to `n`, and the graph writes nothing of its own. A target the graph
cannot answer for is refused (`TargetUnknown`), never guessed.
"""

from __future__ import annotations

import math
from typing import Any

from services.asset_schema import fields as asset_fields
from services.asset_schema.schema import CLASS_ATTR
from services.provenance_graph import money
from services.provenance_graph.memory import (
    MemoryRecall,
)
from services.provenance_graph.queries import _check, money_graph, staleness, why
from services.provenance_graph.types import Edge, Graph, Step, Unrecorded

#: The most steps one answer carries; a longer walk is cut and marked `truncated`, never read as complete (X5).
MAX_STEPS = 64
#: The stamp keys a step carries (`provenance.record`, plus the annotations pricing adds).
STAMP_KEYS = (
    "status",
    "source",
    "source_year",
    "currency_year",
    "currency",
    "fallback",
    "approved_by",
    "approved_at",
    "replaces",
    "illustrative",
    "range",
    "capacity_basis",
)
#: The stamp keys a step names as missing when its stamp lacks them (X3: named, never filled).
STAMP_REQUIRED = ("source", "source_year", "currency_year")
#: Money units are written in EUR by the schema; a stamp naming another currency is shown in that one.
_SCHEMA_CURRENCY = "EUR"
NOT_ESTABLISHED = "not_established"
ARITHMETIC = (
    "arithmetic: the value ÷ the scale recorded at pricing, not a re-read of the pack"
)


class TargetUnknown(LookupError):
    """A target the graph cannot answer for. `status` is the HTTP code the route refuses with (404 or 422)."""

    error_kind = "why_target_unknown"

    def __init__(self, message: str, status: int = 404) -> None:
        super().__init__(message)
        self.status = status


def _parse(node_id: str) -> tuple[str, str, str] | None:
    """`input:<class>/<name>/<field>` or `derived:…` → (class, name, field); None for any other id."""
    kind, _, rest = node_id.partition(":")
    if kind not in ("input", "derived"):
        return None
    cls, _, tail = rest.partition("/")
    name, _, field = tail.rpartition("/")
    return (cls, name, field) if cls and name and field else None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _schema_unit(cls: str, field: str) -> str | None:
    try:
        spec = next((f for f in asset_fields.fields_for(cls) if f.key == field), None)
    except KeyError:
        return None
    return (spec.unit_storage or None) if spec is not None else None


def _unit(cls: str, field: str, stamp: dict | None) -> str | None:
    """The column's storage unit, in the currency the stamp names when it names one."""
    unit = _schema_unit(cls, field)
    currency = (stamp or {}).get("currency")
    if unit and currency and unit.startswith(_SCHEMA_CURRENCY):
        return currency + unit[len(_SCHEMA_CURRENCY) :]
    return unit


def _value(n, cls: str, name: str, field: str) -> float | None:
    """The value the network holds now; None when the class, asset or column is absent or not a number."""
    table = getattr(n, CLASS_ATTR.get(cls, ""), None)
    if table is None or name not in table.index or field not in table.columns:
        return None
    return _finite(table.at[name, field])


def _recorded(edge: Edge) -> Any:
    """What an edge recorded, as JSON: a scale record's `scaled_by`, an unrecorded reason, or nothing."""
    rec = edge.recorded
    if rec is None:
        return None
    if isinstance(rec, Unrecorded):
        return {"unrecorded": rec.reason}
    if isinstance(rec, MemoryRecall):
        out = {"item": rec.item, "version": rec.version, "replaces": rec.replaces}
        if not rec.confirmed:
            # The stamp's approver is text a file can carry: not echoed as an approval until the store confirms it.
            return {**out, "approval": UNCONFIRMED_APPROVAL}
        return {
            **out,
            "approved_by": rec.approved_by,
            "approved_at": rec.approved_at,
        }
    if isinstance(rec, money.ScaleRecord):
        return (
            rec.scaled
            if isinstance(rec.scaled, dict)
            else {"malformed": repr(rec.scaled)}
        )
    return rec


UNCONFIRMED_APPROVAL = (
    "unconfirmed: the memory store did not confirm this approval for you"
)
_APPROVAL_KEYS = ("approved_by", "approved_at")


def _stamp(stamp: dict | None, unconfirmed: bool = False) -> dict | None:
    """The stamp keys a step carries. A stamp that claims a memory approval the store did not confirm for this
    viewer drops the approver and time and says so (GK B A3)."""
    if not stamp:
        return None
    out = {k: stamp[k] for k in STAMP_KEYS if k in stamp}
    if unconfirmed:
        out = {k: v for k, v in out.items() if k not in _APPROVAL_KEYS}
        out["approval"] = UNCONFIRMED_APPROVAL
    return out


def _unconfirmed(g: Graph, node_id: str) -> bool:
    return any(
        e.kind == "recalled_from"
        and e.src == node_id
        and isinstance(e.recorded, MemoryRecall)
        and not e.recorded.confirmed
        for e in g.edges
    )


def _stamp_missing(stamp: dict | None, unit: str | None) -> list[str]:
    """The stamp keys this step's stamp lacks; a money unit with no recorded currency names `currency`. A step
    with no stamp at all names `stamp`: its unit is then the schema's, not a recorded one."""
    if not stamp:
        return ["stamp"]
    missing = [k for k in STAMP_REQUIRED if stamp.get(k) is None]
    if unit and unit.startswith(_SCHEMA_CURRENCY) and not stamp.get("currency"):
        missing.append("currency")
    return missing


def _broken(edge: Edge, g: Graph) -> dict | None:
    """What a broken edge compared (recorded vs current) and, by name, what it could not compare (X2)."""
    broke = _check(g, edge)[1]
    if broke is None:
        return None
    wire = broke.wire()
    reason = wire.pop("reason")
    return {
        "compared": {k: v for k, v in wire.items() if v is not None},
        "uncompared": [k for k, v in wire.items() if v is None],
        "reason": reason,
    }


def _not_established(reason: str) -> dict:
    return {"status": NOT_ESTABLISHED, "reason": reason, "basis": ARITHMETIC}


def _pack_price(
    g: Graph, step: Step, value: float | None, unit: str | None
) -> dict | None:
    """A scaled price's pack price: the value ÷ the scale its `priced_through` edge recorded, with that price's
    source. None when no scale priced it; `not_established` when the division cannot be made (X3)."""
    edge = next(iter(g.out(step.node.id, ("priced_through",))), None)
    if edge is None:
        return None
    if not isinstance(edge.recorded, money.ScaleRecord):
        return _not_established("the scale input was not recorded at pricing")
    scaled = edge.recorded.scaled
    raw = scaled.get("value") if isinstance(scaled, dict) else None
    # A number as pricing writes it: never a string, a bool or anything float() would coerce.
    scale = _finite(raw) if isinstance(raw, (int, float)) else None
    if scale is None:
        return _not_established("the recorded scale is missing or not a finite number")
    if scale == 0:
        return _not_established("the recorded scale is zero")
    if value is None:
        return _not_established("the priced value is missing or not a finite number")
    column = scaled.get("column")
    basis = scaled.get("capacity_basis")
    return {
        "status": "established",
        "value": value / scale,
        # Per unit of the pack row's capacity basis (DC for a DC-priced PV), as pricing recorded it.
        "unit": unit,
        "capacity_basis": basis if isinstance(basis, str) else None,
        "source": (step.stamp or {}).get("source"),
        "basis": ARITHMETIC,
        "worked_back": f"{value} ÷ {column if isinstance(column, str) else 'the recorded scale'} {scale}",
    }


def _memory(step: Step) -> dict | None:
    """The memory step: its state wording and, when the viewer's own read of the item confirmed it, its summary,
    kind, owner scope and source evidence. Never a silent omission (P4, P5): an item the viewer may not read says
    "not visible to you"; any other failure, or no reader, says "memory item could not be read". The id and
    version come from the stamp, which alone never reads "user-approved" (P2)."""
    rec = step.via.recorded if step.via is not None else None
    if step.node.kind != "memory" or not isinstance(rec, MemoryRecall):
        return None
    return {
        "id": step.node.id,
        "state": rec.state,
        "item": rec.item_read,
        "note": rec.note,
    }


def _step(g: Graph, step: Step) -> dict:
    parsed = _parse(step.node.id)
    cls, name, field = parsed if parsed else (None, None, None)
    value = _value(g.ctx, cls, name, field) if parsed else None
    unit = _unit(cls, field, step.stamp) if parsed else None
    out = {
        "node": step.node.id,
        "kind": step.node.kind,
        "component": cls,
        "name": name,
        "field": field,
        "value": value,
        "unit": unit,
        "stamp": _stamp(step.stamp, _unconfirmed(g, step.node.id)),
        "stamp_missing": _stamp_missing(step.stamp, unit),
        "via": None
        if step.via is None
        else {
            "edge": step.via.kind,
            "from": step.via.src,
            "recorded": _recorded(step.via),
        },
        "state": step.state,
        "broken": None,
    }
    if step.via is not None and step.via.kind != "derived_from":
        out["broken"] = _broken(step.via, g)
    memory = _memory(step)
    if memory is not None:
        out["memory"] = memory
    pack = _pack_price(g, step, value, unit) if step.node.kind == "derived" else None
    if pack is not None:
        out["pack_price"] = pack
    return out


def _start(g: Graph, component: str, name: str, field: str) -> str:
    """The node a field is shown as: its derived node when pricing scaled it (or it is the annualised cost), else
    its input node. Refused when the graph holds neither: the field has no provenance to walk (X4)."""
    for nid in (
        money.derived_id(component, name, field),
        money.input_id(component, name, field),
    ):
        if nid in g.nodes:
            return nid
    raise TargetUnknown(f"{component} '{name}' records no provenance for '{field}'")


def check_target(n, component: str, name: str) -> None:
    """Refuse an unknown class (422) or an asset the network does not hold (404)."""
    attr = CLASS_ATTR.get(component)
    if attr is None:
        raise TargetUnknown(f"no asset schema for '{component}'", status=422)
    if name not in getattr(n, attr).index:
        raise TargetUnknown(f"{component} '{name}' not found")


def explain(n, component: str, name: str, field: str, memory=None) -> dict:
    """Why one asset field has its value: the walk from the field to what priced it, outcome first.
    `memory` is the viewer's reader of memory items (`memory.reader_for`, slice 07): a value accepted from one
    reads `from_memory` only when the store confirms the item for this viewer."""
    check_target(n, component, name)
    g = money_graph(n, [(component, name)], memory)
    start = _start(g, component, name, field)
    steps = why(g, start).steps
    verdict = staleness(g, start)
    truncated = len(steps) > MAX_STEPS
    return {
        "outcome": {
            "state": verdict.state,
            "uncompared": list(verdict.uncompared),
            "complete": not truncated,
            "truncated": truncated,
            "steps_total": len(steps),
        },
        "target": {"component": component, "name": name, "field": field},
        "node": start,
        "steps": [_step(g, s) for s in steps[:MAX_STEPS]],
    }


def ledger_fields(key: str, pv_kind: str | None = None) -> list:
    """The asset fields a ledger row compiles to (`LEDGER_FIELDS`); the PV kind the pack did not read is left
    out, as the ledger leaves it out (`asset_view.ledger_stale`). Refused for a key the ledger does not hold."""
    from services.study.asset_view import LEDGER_FIELDS

    rows = [m for m in LEDGER_FIELDS if m.key == key]
    if not rows:
        raise TargetUnknown(f"no ledger row '{key}'")
    if pv_kind is not None:
        rows = [
            m
            for m in rows
            if not m.key.startswith("pv_") or m.key.startswith(f"pv_{pv_kind}_")
        ]
    return rows


def explain_ledger_row(n, key: str, memory=None) -> dict:
    """Why a Guided ledger row's value is what the network holds: `explain` for each asset field the row compiles
    to (the round-trip efficiency compiles to two). Outcome first: stale when any field is stale, else uncompared
    when any is, else fresh; complete only when every field's walk is."""
    meta = (getattr(n, "meta", None) or {}).get("decision_study_pack") or {}
    fields = []
    for m in ledger_fields(key, meta.get("pv_kind")):
        try:
            fields.append(explain(n, m.component, m.name, m.field, memory))
        except TargetUnknown:
            continue  # the asset is not on this network, or the field was never recorded on it
    if not fields:
        raise TargetUnknown(
            f"ledger row '{key}' compiles to no recorded field on this network"
        )
    states = [f["outcome"]["state"] for f in fields]
    state = (
        "stale"
        if "stale" in states
        else "uncompared"
        if "uncompared" in states
        else "from_memory"
        if "from_memory" in states
        else "fresh"
    )
    return {
        "outcome": {
            "state": state,
            "uncompared": [u for f in fields for u in f["outcome"]["uncompared"]],
            "complete": all(f["outcome"]["complete"] for f in fields),
            "truncated": any(f["outcome"]["truncated"] for f in fields),
            "steps_total": sum(f["outcome"]["steps_total"] for f in fields),
        },
        "target": {"ledger_key": key},
        "fields": fields,
    }

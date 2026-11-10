"""
"Why this number" (provenance-graph issue 03): the wire of `why(node)` for the assistant's read tool.

`explain(n, component, name, field)` builds one asset's money path (`money.add_asset`), walks it with `why` and
returns each step as plain JSON: the node, the value and unit the network holds for it now, its provenance stamp,
the edge that reached it (with what that edge recorded) and the step's state, plus, on a broken edge, the
`Broken` wire every surface words the same (`staleNote`). A price scaled at pricing time also carries the pack
price it was scaled from, worked back as the value ÷ the recorded scale.

It reads only (COPILOT-1): nothing here writes to `n`, and the graph writes nothing of its own.
"""

from __future__ import annotations

import math
from typing import Any

from services.asset_schema import fields as asset_fields
from services.asset_schema.schema import CLASS_ATTR
from services.provenance_graph import money
from services.provenance_graph.queries import Builder, _check, staleness, why
from services.provenance_graph.types import Edge, Graph, Step, Unrecorded

#: The stamp keys a step carries (`provenance.record`, plus the scale annotation pricing adds).
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


def _parse(node_id: str) -> tuple[str, str, str] | None:
    """`input:<class>/<name>/<field>` or `derived:…` → (class, name, field); None for any other id."""
    kind, _, rest = node_id.partition(":")
    if kind not in ("input", "derived"):
        return None
    cls, _, tail = rest.partition("/")
    name, _, field = tail.rpartition("/")
    return (cls, name, field) if cls and name and field else None


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _unit(cls: str, field: str) -> str | None:
    try:
        spec = next((f for f in asset_fields.fields_for(cls) if f.key == field), None)
    except KeyError:
        return None
    return (spec.unit_storage or None) if spec is not None else None


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
    if isinstance(rec, money.ScaleRecord):
        return rec.scaled if isinstance(rec.scaled, dict) else {"malformed": repr(rec.scaled)}
    return rec


def _stamp(stamp: dict | None) -> dict | None:
    if not stamp:
        return None
    return {k: stamp[k] for k in STAMP_KEYS if k in stamp}


def _pack_price(g: Graph, step: Step, value: float | None, unit: str | None) -> dict | None:
    """A scaled price's pack price: the value ÷ the scale its `priced_through` edge recorded, with that price's
    source. None when nothing scaled it, or the record cannot be divided by (missing, malformed, zero)."""
    edge = next(iter(g.out(step.node.id, ("priced_through",))), None)
    if edge is None or not isinstance(edge.recorded, money.ScaleRecord):
        return None
    scaled = edge.recorded.scaled
    if not money._well_formed(scaled) or not scaled["value"] or value is None:
        return None
    return {
        "value": value / float(scaled["value"]),
        "unit": unit,
        "source": (step.stamp or {}).get("source"),
        "worked_back": f"{value} ÷ {scaled['column']} {scaled['value']} (recorded at pricing)",
    }


def _step(g: Graph, step: Step) -> dict:
    parsed = _parse(step.node.id)
    cls, name, field = parsed if parsed else (None, None, None)
    value = _value(g.ctx, cls, name, field) if parsed else None
    unit = _unit(cls, field) if parsed else None
    out = {
        "node": step.node.id,
        "kind": step.node.kind,
        "component": cls,
        "name": name,
        "field": field,
        "value": value,
        "unit": unit,
        "stamp": _stamp(step.stamp),
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
        broke = _check(g, step.via)[1]
        out["broken"] = None if broke is None else broke.wire()
    pack = _pack_price(g, step, value, unit) if step.node.kind == "derived" else None
    if pack is not None:
        out["pack_price"] = pack
    return out


def _start(component: str, name: str, field: str, g: Graph) -> str:
    """The node a field is shown as: its derived node when pricing scaled it (or it is the annualised cost),
    else its input node."""
    derived = money.derived_id(component, name, field)
    return derived if derived in g.nodes else money.input_id(component, name, field)


def explain(n, component: str, name: str, field: str) -> dict:
    """Why one asset field has its value: the walk from the field to what priced it, step by step."""
    b = Builder(n)
    money.add_asset(b, component, name)
    g = b.build()
    start = _start(component, name, field, g)
    walk = why(g, start)
    verdict = staleness(g, start)
    return {
        "target": {"component": component, "name": name, "field": field},
        "node": start,
        "state": verdict.state,
        "uncompared": list(verdict.uncompared),
        "steps": [_step(g, s) for s in walk.steps],
    }

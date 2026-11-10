"""
The money path's producer (slice 02): the nodes and edges of one asset's priced costs, and the comparison of a
`priced_through` edge (TCU-05, moved here from `asset_schema.provenance`, not copied).

Per asset, from `n.meta["asset_params"]` (`provenance.get`, the only writer stays `provenance.py`):

- an `input` node per recorded field, with a `stamped_with` edge to its source carrying the stamp;
- a `derived` node per money field pricing scaled (it replaces that field's `input` node on the walk) (`overnight_cost`, `fom_cost`), with one `priced_through` edge to
  the scale input it was priced with (`dc_ac_ratio`, a Link's output efficiency), carrying the recorded scale, or
  `Unrecorded` when a price that pricing scales recorded none;
- a `derived` node for the annualised `capital_cost` on the Economics row, `derived_from` each money field;
- `shown_in` edges from each money field to the asset card, its read-out and the Economics costs table.

"What is the scale input now?" is answered by `pricing.current_scale_input` only, so the check cannot drift from
what pricing would do.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

from services.asset_schema import provenance
from services.provenance_graph.types import Broken, Edge, Node, Unrecorded

#: The money fields a scaled price writes (pricing: overnight × scale, FOM = overnight × share).
SCALED_FIELDS = ("overnight_cost", "fom_cost")
ANNUALISED = "capital_cost"
#: The surfaces that show an asset's money fields (`surface_id`).
ASSET_SURFACES = ("asset_card", "asset_readout", "economics_costs")
NOT_RECORDED = "scale input not recorded"


@dataclass(frozen=True)
class ScaleRecord:
    """What a `priced_through` edge recorded: the asset it priced, and the scale input pricing wrote (`scaled_by`,
    as stored, so a malformed record is compared, not dropped)."""

    component_class: str
    name: str
    scaled: object


def input_id(cls: str, name: str, field: str) -> str:
    return f"input:{cls}/{name}/{field}"


def derived_id(cls: str, name: str, field: str) -> str:
    return f"derived:{cls}/{name}/{field}"


def surface_id(surface: str, cls: str, name: str, field: str) -> str:
    return f"{surface}:{cls}/{name}/{field}"


def _recorded_input(entry: dict) -> dict | None:
    """The scale input an entry was priced with: `scaled_by`, or TC-02's `dc_ac_ratio` key."""
    if entry.get("scaled_by") is not None:
        return entry["scaled_by"]
    if entry.get("dc_ac_ratio") is not None:
        # TC-02 wrote the key on a DC-priced (`output_electric_dc`) overnight cost only.
        return {
            "column": "dc_ac_ratio",
            "value": entry["dc_ac_ratio"],
            "technology": None,
            "capacity_basis": "output_electric_dc",
        }
    return None


def _well_formed(scaled) -> bool:
    """The shape pricing writes: a str column and capacity basis, a finite number value."""
    return (
        isinstance(scaled, dict)
        and isinstance(scaled.get("column"), str)
        and isinstance(scaled.get("capacity_basis"), str)
        and isinstance(scaled.get("value"), (int, float))
        and not isinstance(scaled["value"], bool)
        and math.isfinite(scaled["value"])
    )


#: An applied Library reference's source (`reference_apply.source_id`).
_REFERENCE_SOURCE = re.compile(
    r"^(?P<id>[^#/]+)#(?P<hash>[0-9a-f]{16})/(?P<technology>.+)@(?P<year>\d{4})$"
)


def _dc_reference_unrecorded(component_class: str, entry: dict) -> str | None:
    """A Library reference applied before TCU-05 recorded no scale input. Only a Generator takes
    a DC-priced reference (every other class refuses `output_electric_dc`), so its source is
    looked up: a DC reference, or one this build cannot read back, is not recorded (a reason);
    an AC or other basis reads fresh (None)."""
    if (
        component_class != "Generator"
        or entry.get("status") != "customised"
        or not entry.get("approved_by")
    ):
        return None
    m = _REFERENCE_SOURCE.match(str(entry.get("source")))  # None: "None", no match
    if m is None:
        return None
    from services.library import europe_reference as R

    try:
        ref = R.load_europe_technology_reference(m["technology"], int(m["year"]))
    except Exception:  # noqa: BLE001 - any failure to read it back: the basis is unknown
        return f"{NOT_RECORDED} (the applied reference cannot be read back)"
    if ref.reference_id != m["id"] or ref.reference_hash[:16] != m["hash"]:
        return f"{NOT_RECORDED} (the applied reference is from another release)"
    return NOT_RECORDED if ref.capacity_basis_code == "output_electric_dc" else None


def _priced_by_scaling_rule(component_class: str, entry: dict) -> bool:
    """A pack price (status default, annotated with its row's capacity basis) whose rule scales."""
    from services.library.defaults_pack.pricing import _CLASS, SCALED_RULES, scale_rule

    component = next((c for c, cls in _CLASS.items() if cls == component_class), None)
    return (
        entry.get("status") == "default"
        and scale_rule(component, entry.get("capacity_basis")) in SCALED_RULES
    )  # unknown: "refuse"


def _priced_with(component_class: str, field: str, entries: dict[str, dict]):
    """What a money field was priced through: the recorded scale (a dict, maybe malformed), `Unrecorded`
    for a price pricing scales that recorded none, or None when no scale input priced it."""
    entry = entries[field]
    over = entries.get("overnight_cost") or {}
    scaled = _recorded_input(entry)
    basis = entry
    # fom_cost carries no annotation of its own before TCU-05: it shares the overnight
    # cost's when both are the same pack price.
    if (
        scaled is None
        and field == "fom_cost"
        and entry.get("status") == over.get("status")
        and entry.get("source") == over.get("source")
    ):
        scaled, basis = _recorded_input(over), over
    if scaled is not None:
        return scaled
    if _priced_by_scaling_rule(component_class, basis):
        return Unrecorded(NOT_RECORDED)
    why = _dc_reference_unrecorded(component_class, basis)
    return None if why is None else Unrecorded(why)


def add_asset(
    b, component_class: str, name: str, entries: dict[str, dict] | None = None
) -> dict[str, dict]:
    """Add one asset's money path to the builder `b`; returns the recorded entries it read (`provenance.get`)."""
    entries = provenance.get(b.n, component_class, name) if entries is None else entries
    for field, entry in entries.items():
        nid = input_id(component_class, name, field)
        b.node(Node("input", nid))
        b.edge(
            Edge(
                "stamped_with",
                nid,
                f"source:{entry.get('source')}",
                recorded=dict(entry),
            )
        )
    money = []
    for field in SCALED_FIELDS:
        if field not in entries:
            continue
        priced = _priced_with(component_class, field, entries)
        if priced is None:
            node = input_id(component_class, name, field)
        else:
            node = derived_id(component_class, name, field)
            column = (
                priced.get("column")
                if isinstance(priced, dict) and _well_formed(priced)
                else "scale_input"
            )
            b.node(Node("derived", node))
            b.node(Node("input", input_id(component_class, name, column)))
            recorded = (
                priced
                if isinstance(priced, Unrecorded)
                else ScaleRecord(component_class, name, priced)
            )
            b.edge(
                Edge(
                    "priced_through",
                    node,
                    input_id(component_class, name, column),
                    recorded=recorded,
                )
            )
        money.append(node)
        for surface in ASSET_SURFACES:
            sid = surface_id(surface, component_class, name, field)
            b.node(Node("surface", sid))
            b.edge(Edge("shown_in", node, sid))
    if money:
        annual = derived_id(component_class, name, ANNUALISED)
        b.node(Node("derived", annual))
        for node in money:
            b.edge(Edge("derived_from", annual, node))
        sid = surface_id("economics_costs", component_class, name, ANNUALISED)
        b.node(Node("surface", sid))
        b.edge(Edge("shown_in", annual, sid))
    return entries


def compare_priced_through(g, edge: Edge) -> Broken | None:
    """None when the scale input pricing would read now is the recorded one (column, and value within
    math.isclose rel 1e-9); else what broke. Unrecorded, malformed or unreadable is stale, never fresh."""
    from services.library.defaults_pack.pricing import current_scale_input

    def broken(field, recorded, current_field, current, reason) -> Broken:
        return Broken(
            "priced_through",
            edge.src,
            edge.dst,
            field,
            recorded,
            current_field,
            current,
            reason,
        )

    if isinstance(edge.recorded, Unrecorded):
        return broken(None, None, None, None, edge.recorded.reason)
    scaled = edge.recorded.scaled
    if not _well_formed(scaled):  # read as stale, never a KeyError 500 (#1255)
        return broken(None, None, None, None, "scale input record malformed")
    try:
        column, value = current_scale_input(
            g.ctx,
            edge.recorded.component_class,
            edge.recorded.name,
            scaled.get("technology"),
            scaled["capacity_basis"],
        )
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        return broken(
            scaled["column"],
            scaled["value"],
            None,
            None,
            f"cannot be priced now: {exc}",
        )
    if column == scaled["column"] and math.isclose(
        float(value), float(scaled["value"]), rel_tol=1e-9
    ):
        return None
    return broken(
        scaled["column"], scaled["value"], column, value, "scale input changed"
    )


def ledger_row_id(key: str) -> str:
    return f"ledger_row:{key}"


def add_ledger_rows(b, rows: Iterable[tuple[str, str, str, str]]) -> None:
    """The Guided ledger's rows (`asset_view.LEDGER_FIELDS`: key, component class, name, field): an `input` node
    per row shown in its `ledger_row` surface, and the asset's money field shown there too, so the row reads the
    verdict the asset card reads. Call after `add_asset` for each asset the rows compile to."""
    for key, cls, name, field in rows:
        row = ledger_row_id(key)
        b.node(Node("input", f"input:ledger/{key}"))
        b.node(Node("surface", row))
        b.edge(Edge("shown_in", f"input:ledger/{key}", row))
        if field in SCALED_FIELDS and b.has(derived_id(cls, name, field)):
            b.edge(Edge("shown_in", derived_id(cls, name, field), row))

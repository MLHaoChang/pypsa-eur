"""
P3 oracle (provenance-graph plan GO): `asset_schema.provenance.staleness` as it stood on master `714077b58`, frozen
with every helper it calls (lines 86-195 of that file), verbatim but for `ruff format`, so the graph-backed wire can
be compared with it row for row (GK B G3). It imports nothing from `provenance_graph` or `asset_schema.provenance`;
the only outside reads are the ones master made (pricing, the Europe reference). Test-only: no production module may
import this.
"""

from __future__ import annotations

import math
import re

_DROPPED_ON_EDIT = ("approved_by", "approved_at", "scaled_by", "dc_ac_ratio")

#: The money fields a scaled price writes (pricing: overnight × scale, FOM = overnight × share).
_SCALED_FIELDS = ("overnight_cost", "fom_cost")
_NOT_RECORDED = {
    "field": None,
    "recorded": None,
    "current_field": None,
    "current": None,
    "reason": "scale input not recorded",
}


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


def _verdict(n, component_class: str, name: str, scaled) -> dict | None:
    from services.library.defaults_pack.pricing import current_scale_input

    if not _well_formed(scaled):  # read as stale, never a KeyError 500 (#1255)
        return {**_NOT_RECORDED, "reason": "scale input record malformed"}

    try:
        column, value = current_scale_input(
            n, component_class, name, scaled.get("technology"), scaled["capacity_basis"]
        )
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        return {
            "field": scaled["column"],
            "recorded": scaled["value"],
            "current_field": None,
            "current": None,
            "reason": f"cannot be priced now: {exc}",
        }
    if column == scaled["column"] and math.isclose(
        float(value), float(scaled["value"]), rel_tol=1e-9
    ):
        return None
    return {
        "field": scaled["column"],
        "recorded": scaled["value"],
        "current_field": column,
        "current": value,
        "reason": "scale input changed",
    }


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
        return "scale input not recorded (the applied reference cannot be read back)"
    if ref.reference_id != m["id"] or ref.reference_hash[:16] != m["hash"]:
        return (
            "scale input not recorded (the applied reference is from another release)"
        )
    return (
        "scale input not recorded"
        if ref.capacity_basis_code == "output_electric_dc"
        else None
    )


def _priced_by_scaling_rule(component_class: str, entry: dict) -> bool:
    """A pack price (status default, annotated with its row's capacity basis) whose rule scales."""
    from services.library.defaults_pack.pricing import _CLASS, SCALED_RULES, scale_rule

    component = next((c for c, cls in _CLASS.items() if cls == component_class), None)
    return (
        entry.get("status") == "default"
        and scale_rule(component, entry.get("capacity_basis")) in SCALED_RULES
    )  # unknown: "refuse"


def staleness(
    n, component_class: str, name: str, entries: dict[str, dict]
) -> dict[str, dict]:
    """`entries` (from `get`) with `stale` added to each money field whose price was scaled by a
    column of the asset (TCU-05: a PV Generator's `dc_ac_ratio`, a Link's output efficiency) that
    now differs, in column or value (math.isclose rel 1e-9), from what pricing would read; or that
    pricing would now refuse (`current` None). A scaled pack price that recorded no input reads
    `stale` with reason "scale input not recorded", never fresh. Pure: the store is not changed;
    a field with nothing to say is returned as it was."""
    out = dict(entries)
    over = entries.get("overnight_cost") or {}
    for field in _SCALED_FIELDS:
        entry = entries.get(field)
        if entry is None:
            continue
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
            verdict = _verdict(n, component_class, name, scaled)
        elif _priced_by_scaling_rule(component_class, basis):
            verdict = dict(_NOT_RECORDED)
        elif (why := _dc_reference_unrecorded(component_class, basis)) is not None:
            verdict = {**_NOT_RECORDED, "reason": why}
        else:
            verdict = None
        if verdict is not None:
            out[field] = {**entry, "stale": verdict}
    return out

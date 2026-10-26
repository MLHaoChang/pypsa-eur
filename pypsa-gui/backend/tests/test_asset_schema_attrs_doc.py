"""
S1d-3 (plan 2026-10-13-s1-expert-asset-cards.md): the chat tool `create_component` documents its `attrs` from the
asset schema's descriptors, the same ones the Expert card and the creation form render.

WHAT THIS PINS. Before S1d-3 the tool's `attrs` was a bare object: the assistant was told nothing about the
investment parts (`overnight_cost`, `lifetime`, a battery's `inv_power_*` / `inv_energy_*`, a line's per-km
cost), so it could only type the annuity `capital_cost`, the one field the plan says no face types. The docs
name every typed column with its stored unit (the chat writes stored values, per MW and as fractions), list the
derived columns as not to be sent, and name only attributes the create route accepts.
"""
from __future__ import annotations

import re

import pytest

from services.asset_schema.attrs_doc import ATTRS_DOC, attrs_doc, class_line
from services.asset_schema.fields import fields_for, known_components

CLASSES = known_components()


def _tool(name: str) -> dict:
    from harness.catalogue import TOOLS

    return next(t for t in TOOLS if t["name"] == name)


def _typed_and_derived(line: str) -> tuple[str, str]:
    body = line.split(": ", 1)[1].split(" Investment parts:", 1)[0]
    typed, _, derived = body.partition("; derived: ")
    return typed, derived


def test_create_component_documents_its_attrs_from_the_descriptors():
    attrs = _tool("create_component")["input_schema"]["properties"]["attrs"]
    assert attrs["type"] == "object"
    assert attrs["description"] == attrs_doc()


def test_the_generated_literal_is_current():
    """The catalogue imports the literal (the assistant inventory reads it without importing application code).
    After a descriptor change, regenerate from pypsa-gui/backend: python -m services.asset_schema.attrs_doc --write"""
    assert ATTRS_DOC == attrs_doc()


def test_batch_create_points_at_the_same_docs():
    components = _tool("batch_create_components")["input_schema"]["properties"]["components"]
    assert "create_component's attrs" in components["description"]


@pytest.mark.parametrize("component", CLASSES)
def test_every_typed_column_is_listed_with_its_stored_unit(component):
    typed, derived = _typed_and_derived(class_line(component))
    for f in fields_for(component):
        if f.key == "name":
            continue
        listed = re.search(rf"(?<![\w]){re.escape(f.key)}\b", derived if f.derived else typed)
        assert listed, f"{component}.{f.key} missing from the {'derived' if f.derived else 'typed'} list"
        if not f.derived and f.unit_storage:
            assert f"{f.key}{'*' if f.required else ''} ({f.unit_storage})" in typed


@pytest.mark.parametrize("component", CLASSES)
def test_no_derived_column_is_offered_as_typed(component):
    typed, _ = _typed_and_derived(class_line(component))
    for f in fields_for(component):
        if f.derived:
            assert not re.search(rf"(?<![\w]){re.escape(f.key)}\b", typed), f"{component}.{f.key}"


def test_the_units_are_the_stored_ones():
    gen = class_line("Generator")
    assert "overnight_cost (EUR/MW)" in gen and "EUR/kW" not in gen
    assert "efficiency (fraction)" in gen and "%" not in gen
    assert "capital_cost" in _typed_and_derived(gen)[1]


def test_the_composite_parts_say_how_they_scale():
    su = class_line("StorageUnit")
    assert "inv_power_overnight (EUR/MW)" in su
    assert "inv_energy_overnight (EUR/MWh)" in su
    assert "inv_energy_* is the energy part (EUR/MWh, × max_hours)" in su
    line = class_line("Line")
    assert "inv_per_km_overnight (EUR/MVA/km)" in line
    assert "× length; uses the Line's lifetime, which must then be finite" in line


@pytest.mark.parametrize("component", CLASSES)
def test_every_listed_attribute_is_accepted_by_the_create_route(component):
    """The docs never name an attribute `create_component` would drop or refuse."""
    from services import chat_tools

    schema = chat_tools._get_schema(chat_tools._COMPONENT_CREATE_SCHEMAS[component])
    for f in fields_for(component):
        assert f.key in schema.model_fields, f"{component}.{f.key}"


def test_the_outage_flag_is_a_generator_column_only():
    """Ported from the retired `includesOutages.test.tsx`: `p_max_pu_includes_outages` is described (so typed and
    documented) on the Generator only; the other classes carry no availability the adequacy engines read so."""
    holders = [c for c in CLASSES if any(f.key == "p_max_pu_includes_outages" for f in fields_for(c))]
    assert holders == ["Generator"]


def test_the_docs_stay_compact():
    """They ride in the tool catalogue on every turn."""
    # 4300 (owner G2P-17A-DOCCAP): room for the Link's harmonic-screen fields, harmonic_source's list in full.
    assert len(attrs_doc()) < 4300

"""
Asset sizing loop AS-01: design-intent presets A-E as scenario metadata.

Data model and validation only; no LP changes.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from models.design_intent import DESIGN_INTENTS, DesignIntentSpec, preset
from models.flex_archetypes import DataCentreLoadSpec
from services import design_intent as D


@pytest.mark.parametrize("intent", list("ABCDE"))
def test_each_intent_loads_and_round_trips(intent):
    spec = DesignIntentSpec(intent=intent)
    again = DesignIntentSpec.model_validate_json(spec.model_dump_json())
    assert again == spec
    p = preset(intent)
    assert p["technologies"] and p["extendable"] <= set(p["technologies"])
    assert p["services"]


def test_closed_set_rejects_unknown_intent():
    assert set(DESIGN_INTENTS) == set("ABCDE")
    with pytest.raises(ValidationError):
        DesignIntentSpec(intent="F")
    with pytest.raises(KeyError):
        preset("F")


def test_intent_a_backup_energy_is_critical_load_times_4h():
    assert DesignIntentSpec(intent="A").backup_autonomy_h == 4.0
    assert D.backup_energy_mwh(DesignIntentSpec(intent="A"), 80.0) == 320.0
    assert D.backup_energy_mwh(DesignIntentSpec(intent="A", backup_autonomy_h=2), 80.0) == 160.0


@pytest.mark.parametrize("hours", [0, -1])
def test_autonomy_hours_must_be_positive(hours):
    with pytest.raises(ValidationError):
        DesignIntentSpec(intent="A", backup_autonomy_h=hours)


def test_backup_energy_only_for_intent_a():
    assert D.backup_energy_mwh(DesignIntentSpec(intent="B"), 80.0) is None


def test_intent_d_bridge_reserve_default():
    assert DesignIntentSpec(intent="D").bridge_reserve_h == 0.25
    assert D.bridge_reserve_mwh(DesignIntentSpec(intent="D"), 80.0) == 20.0
    assert D.bridge_reserve_mwh(DesignIntentSpec(intent="A"), 80.0) is None


@pytest.mark.parametrize("intent", ["B", "C"])
def test_genset_floor_and_run_hour_cap(intent):
    spec = DesignIntentSpec(intent=intent, genset_run_hours_cap=500)
    assert spec.genset_run_hours_cap == 500
    assert D.genset_p_nom_min_mw(spec, 80.0) == 80.0
    with pytest.raises(ValidationError):
        DesignIntentSpec(intent=intent, genset_run_hours_cap=-1)


def test_no_genset_in_intent_a_bound_to_zero():
    b = D.technology_bounds(DesignIntentSpec(intent="A"), 80.0)
    assert b["genset"] == {"p_nom_max": 0.0}
    assert "genset" not in D.technology_bounds(DesignIntentSpec(intent="B"), 80.0) or \
        D.technology_bounds(DesignIntentSpec(intent="B"), 80.0)["genset"]["p_nom_min"] == 80.0


def test_intent_d_warns_without_transfer_ride_through_but_does_not_block():
    spec = DesignIntentSpec(intent="D")
    w = D.grid_code_warnings(spec, {"bess_transfer_ride_through": False})
    assert len(w) == 1 and "ride-through" in w[0]
    assert D.grid_code_warnings(spec, {}) == w
    assert D.grid_code_warnings(spec, {"bess_transfer_ride_through": True}) == []
    assert D.grid_code_warnings(DesignIntentSpec(intent="A"), {}) == []


def test_existing_specs_load_unchanged_without_design_intent():
    assert "design_intent" in DataCentreLoadSpec.model_fields
    assert DataCentreLoadSpec.model_fields["design_intent"].default is None

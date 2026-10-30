"""
Asset sizing loop AS-01: design-intent presets A-E as scenario metadata.

Data model and validation only; no LP changes.
"""
from __future__ import annotations

import pathlib

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


def test_technology_bounds_pinned_per_intent():
    tb = lambda i, **kw: D.technology_bounds(DesignIntentSpec(intent=i, **kw), 80.0)
    assert tb("A") == {"genset": {"p_nom_max": 0.0}, "energy_bess_gfm": {"e_nom_min": 320.0}}
    assert tb("B") == {"genset": {"p_nom_min": 80.0}}
    assert tb("C", genset_run_hours_cap=500) == {"genset": {"p_nom_min": 80.0, "run_hours_cap": 500}}
    assert tb("D") == {"bess_gfm": {"e_nom_min": 20.0}}
    assert tb("E") == {}


@pytest.mark.parametrize("bad", [0, -80.0, float("nan"), float("inf")])
def test_critical_load_must_be_positive(bad):
    spec = DesignIntentSpec(intent="A")
    for fn in (D.backup_energy_mwh, D.bridge_reserve_mwh, D.genset_p_nom_min_mw, D.technology_bounds):
        with pytest.raises(ValueError):
            fn(spec, bad)


def test_run_hours_cap_only_for_b_c_and_at_most_a_year():
    for i in "ADE":
        with pytest.raises(ValidationError):
            DesignIntentSpec(intent=i, genset_run_hours_cap=100)
    with pytest.raises(ValidationError):
        DesignIntentSpec(intent="B", genset_run_hours_cap=8761)
    assert DesignIntentSpec(intent="B", genset_run_hours_cap=8760).genset_run_hours_cap == 8760


def test_intent_d_warns_without_transfer_ride_through_but_does_not_block():
    spec = DesignIntentSpec(intent="D")
    w = D.grid_code_warnings(spec, {"bess_transfer_ride_through": False})
    assert len(w) == 1 and "ride-through" in w[0]
    assert D.grid_code_warnings(spec, {}) == w
    assert D.grid_code_warnings(spec, {"bess_transfer_ride_through": True}) == []
    assert D.grid_code_warnings(DesignIntentSpec(intent="A"), {}) == []


@pytest.mark.parametrize("tid", ["eh_datacenter", "eh_datacenter_staged"])
def test_existing_stored_spec_loads_unchanged(tid, monkeypatch):
    monkeypatch.syspath_prepend(str(pathlib.Path(__file__).resolve().parents[1] / "project_templates"))
    import eh_templates as T
    stored = T.dc_load_preset(tid)  # a spec as persisted today, no design_intent key
    assert "design_intent" not in stored
    spec = DataCentreLoadSpec.model_validate(stored)
    assert spec.design_intent is None
    # the full dump equals the one from the same spec built without the field
    plain = DataCentreLoadSpec.model_validate({**stored, "design_intent": None})
    assert spec.model_dump(mode="json", exclude={"design_intent"}) == \
        plain.model_dump(mode="json", exclude={"design_intent"})
    assert T.dc_load_spec(tid, {}).model_dump(mode="json")["design_intent"] is None


def test_spec_carrying_a_design_intent_round_trips(monkeypatch):
    monkeypatch.syspath_prepend(str(pathlib.Path(__file__).resolve().parents[1] / "project_templates"))
    import eh_templates as T
    stored = {**T.dc_load_preset("eh_datacenter"),
              "design_intent": {"intent": "B", "genset_run_hours_cap": 500}}
    spec = DataCentreLoadSpec.model_validate(stored)
    assert spec.design_intent == DesignIntentSpec(intent="B", genset_run_hours_cap=500)
    again = DataCentreLoadSpec.model_validate_json(spec.model_dump_json())
    assert again == spec and again.model_dump() == spec.model_dump()

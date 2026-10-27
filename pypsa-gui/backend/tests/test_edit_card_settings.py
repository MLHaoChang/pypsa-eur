"""Old → new edit cards for the project settings and scope tools (Q3-CARDS-1, slice 1)."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from harness import edit_card
from services import chat_tools
from tests.study_s4_support import enable_studies
from tests.test_lv08_b1_study_project_tools import _start
from tests.test_study_tornado_routes import NO_PV_TOU


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


def _cells(card):
    return [(r["field"], r["old"], r["new"]) for r in card["rows"]]


def _toggle(name, scope):
    """The first single-option switch-off the scope's own validation accepts."""
    for card, chosen in scope["options"].items():
        for option in chosen:
            trial = {**scope, "options": {**scope["options"], card: [o for o in chosen if o != option]}}
            try:
                return trial, edit_card.preview("update_project_flow_scope", {"name": name, "scope": trial})
            except HTTPException:
                continue
    pytest.fail("no option can be switched off")


def test_flow_scope_shows_the_changed_option_old_to_new(client, api_project):
    name = api_project("card-scope")
    scope = chat_tools.get_project_flow_scope(name)["scope"]
    chat_tools.update_project_flow_scope(name, scope)
    trial, card = _toggle(name, chat_tools.get_project_flow_scope(name)["scope"])
    assert card["complete"] and card["kind"] == "settings_edit"
    assert [(r["old"], r["new"]) for r in card["rows"]] == [(True, False)]
    assert "\u2192 False" in card["says"]


def test_flow_scope_adding_an_option_was_off(client, api_project):
    name = api_project("card-scope-new")
    scope = chat_tools.get_project_flow_scope(name)["scope"]
    trial, _ = _toggle(name, scope)
    chat_tools.update_project_flow_scope(name, trial)
    card = edit_card.preview("update_project_flow_scope", {"name": name, "scope": scope})
    assert [(r["old"], r["new"]) for r in card["rows"]] == [(False, True)]


def test_flow_scope_unchanged_refuses_and_a_bad_shape_refuses(client, api_project):
    name = api_project("card-scope-same")
    scope = chat_tools.get_project_flow_scope(name)["scope"]
    with pytest.raises(HTTPException) as same:
        edit_card.preview("update_project_flow_scope", {"name": name, "scope": scope})
    assert same.value.detail["error_kind"] == "empty_edit"
    with pytest.raises(HTTPException) as bad:
        edit_card.preview("update_project_flow_scope", {"name": name, "scope": {"version": "nope"}})
    assert bad.value.detail["error_kind"] == "flow_scope_invalid"


def test_project_settings_not_set_then_old_to_new_then_unchanged(client, api_project):
    name = api_project("card-settings")
    args = {"name": name, "derive_lengths_from_geometry": True}
    first = edit_card.preview("update_project_settings", args)
    assert _cells(first) == [("derive_lengths_from_geometry", None, True)]
    assert first["says"] == "Project settings derive_lengths_from_geometry not set → True"
    chat_tools.update_project_settings(name, True)
    again = edit_card.preview("update_project_settings", args)
    assert _cells(again) == [("derive_lengths_from_geometry", True, True)]
    flipped = edit_card.preview("update_project_settings", {**args, "derive_lengths_from_geometry": False})
    assert _cells(flipped) == [("derive_lengths_from_geometry", True, False)]
    with pytest.raises(HTTPException):
        edit_card.preview("update_project_settings", {"name": name, "derive_lengths_from_geometry": "yes"})


def test_scenario_metadata_old_new_unchanged_and_cleared(client, api_project):
    name = api_project("card-scenario")
    first = edit_card.preview("update_project_scenario_metadata", {"name": name, "description": "cold winter"})
    assert _cells(first) == [("description", None, "cold winter")]
    chat_tools.update_project_scenario_metadata(name, description="cold winter", scenario_type="stress")
    same = edit_card.preview("update_project_scenario_metadata", {"name": name, "description": " cold winter "})
    assert _cells(same) == [("description", "cold winter", "cold winter")]
    cleared = edit_card.preview("update_project_scenario_metadata",
                                {"name": name, "description": None, "scenario_type": "baseline"})
    assert _cells(cleared) == [("description", "cold winter", None), ("scenario_type", "stress", "baseline")]
    assert "cold winter → not set" in cleared["says"]
    reread = edit_card.ADAPTERS["update_project_scenario_metadata"].readback({"name": name}, cleared["rows"])
    assert reread == ["cold winter", "stress"]


def test_decision_study_shows_name_intake_and_settings_old_to_new(client, api_project, studies_on):
    source = api_project("card-study")
    study_id, project = _start(source, "card-study-base")
    stored = chat_tools.get_decision_study(project, study_id)
    args = {"project": project, "study_id": study_id, "name": "Renamed", "settings": {"currency_year": 2030}}
    card = edit_card.preview("update_decision_study", args)
    by_field = {r["field"]: r for r in card["rows"]}
    assert (by_field["name"]["old"], by_field["name"]["new"]) == (stored["name"], "Renamed")
    assert by_field["settings.currency_year"]["old"] == stored.get("currency_year")
    unchanged = edit_card.preview("update_decision_study", {**args, "name": stored["name"], "settings": {}})
    assert _cells(unchanged) == [("name", stored["name"], stored["name"])]
    with pytest.raises(HTTPException) as empty:
        edit_card.preview("update_decision_study", {"project": project, "study_id": study_id})
    assert empty.value.detail["error_kind"] == "empty_edit"

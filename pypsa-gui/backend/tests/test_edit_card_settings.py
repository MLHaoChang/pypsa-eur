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
    assert "\u203a" not in card["rows"][0]["label"] and " \u00b7 " in card["rows"][0]["label"]  # data, not a link
    assert "Included \u2192 Not included" in card["says"]
    args = {"name": name, "scope": trial}
    chat_tools.update_project_flow_scope(name, trial)
    assert edit_card.ADAPTERS["update_project_flow_scope"].readback(args, card["rows"]) == [False]


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
    assert first["says"] == "Derive lengths from geometry not set \u2192 On"
    chat_tools.update_project_settings(name, True)
    with pytest.raises(HTTPException) as same:
        edit_card.preview("update_project_settings", args)
    assert same.value.detail["error_kind"] == "empty_edit"
    flipped = edit_card.preview("update_project_settings", {**args, "derive_lengths_from_geometry": False})
    assert _cells(flipped) == [("derive_lengths_from_geometry", True, False)]
    with pytest.raises(HTTPException):
        edit_card.preview("update_project_settings", {"name": name, "derive_lengths_from_geometry": "yes"})


def test_scenario_metadata_old_new_unchanged_and_cleared(client, api_project):
    name = api_project("card-scenario")
    first = edit_card.preview("update_project_scenario_metadata", {"name": name, "description": "cold winter"})
    assert _cells(first) == [("description", None, "cold winter")]
    assert first["says"] == "Scenario description not set \u2192 cold winter"
    chat_tools.update_project_scenario_metadata(name, description="cold winter", scenario_type="stress")
    with pytest.raises(HTTPException) as same:
        edit_card.preview("update_project_scenario_metadata", {"name": name, "description": " cold winter "})
    assert same.value.detail["error_kind"] == "empty_edit"
    cleared = edit_card.preview("update_project_scenario_metadata",
                                {"name": name, "description": None, "scenario_type": "baseline"})
    assert _cells(cleared) == [("description", "cold winter", None), ("scenario_type", "stress", "baseline")]
    assert "cold winter \u2192 not set" in cleared["says"]
    reread = edit_card.ADAPTERS["update_project_scenario_metadata"].readback({"name": name}, cleared["rows"])
    assert reread == ["cold winter", "stress"]


def test_scenario_metadata_refuses_what_the_handler_refuses(client, api_project):
    name = api_project("card-scenario-bad")
    with pytest.raises(HTTPException) as bad:
        edit_card.preview("update_project_scenario_metadata", {"name": name, "scenario_type": "nonsense"})
    assert bad.value.status_code == 400
    with pytest.raises(HTTPException) as legacy:
        edit_card.preview("update_project_scenario_metadata", {"name": name, "description": "[stress] cold"})
    assert legacy.value.status_code == 400
    with pytest.raises(HTTPException) as long:
        edit_card.preview("update_project_scenario_metadata", {"name": name, "description": "x" * 501})
    assert long.value.status_code == 422


def test_decision_study_shows_name_intake_and_settings_old_to_new(client, api_project, studies_on):
    source = api_project("card-study")
    study_id, project = _start(source, "card-study-base")
    stored = chat_tools.get_decision_study(project, study_id)
    args = {"project": project, "study_id": study_id, "name": "Renamed", "settings": {"currency_year": 2030}}
    card = edit_card.preview("update_decision_study", args)
    by_field = {r["field"]: r for r in card["rows"]}
    assert (by_field["name"]["old"], by_field["name"]["new"]) == (stored["name"], "Renamed")
    assert by_field["settings.currency_year"]["new"] == "EUR of 2030"
    assert card["says"].startswith("Study name ")
    with pytest.raises(HTTPException) as same:  # the stored name again: nothing to change
        edit_card.preview("update_decision_study", {"project": project, "study_id": study_id, "name": stored["name"]})
    assert same.value.detail["error_kind"] == "empty_edit"
    unset = edit_card.preview("update_decision_study", {"project": project, "study_id": study_id,
                                                        "intake": {"brand_new_answer": "yes"}})
    assert _cells(unset) == [("intake.brand_new_answer", None, "yes")]
    assert "not set \u2192 yes" in unset["says"]


def test_decision_study_refuses_what_the_handler_refuses(client, api_project, studies_on):
    source = api_project("card-study-bad")
    study_id, project = _start(source, "card-study-bad-base")
    base = {"project": project, "study_id": study_id}
    for extra in ({"name": "x" * 121}, {"intake": {"load": {"csv_text": "a,b"}}}, {"step": "tariff"},
                  {"settings": {"perspective": "not_a_perspective"}}):
        with pytest.raises(HTTPException) as exc:
            edit_card.preview("update_decision_study", {**base, **extra})
        assert exc.value.status_code in (422, 400), extra
        assert exc.value.detail["error_kind"] == "invalid_edit_value", extra


# ── GK B conditions: previews write nothing, ACL first, verified readback, caps ──

def test_previews_write_nothing(client, api_project, project_storage_dir, studies_on):
    from tests.study_s4_support import dir_hash

    name = api_project("card-nowrite")
    source = api_project("card-nowrite-src")
    study_id, study_project = _start(source, "card-nowrite-base")
    scope = chat_tools.get_project_flow_scope(name)["scope"]
    chat_tools.update_project_settings(name, True)
    before = {n: dir_hash(project_storage_dir(n)) for n in (name, study_project)}
    edit_card.preview("update_project_settings", {"name": name, "derive_lengths_from_geometry": False})
    edit_card.preview("update_project_scenario_metadata", {"name": name, "description": "x"})
    _toggle(name, scope)
    edit_card.preview("update_decision_study", {"project": study_project, "study_id": study_id, "name": "Other"})
    assert {n: dir_hash(project_storage_dir(n)) for n in before} == before


def test_an_outsider_gets_the_handlers_refusal_not_old_values(client, api_project, second_identity, studies_on):
    name = api_project("card-acl")
    study_id, study_project = _start(api_project("card-acl-src"), "card-acl-base")
    scope = chat_tools.get_project_flow_scope(name)["scope"]
    chat_tools.set_acting_user(second_identity["user_id"])  # after the owner's setup
    for tool, args in (("update_project_settings", {"name": name, "derive_lengths_from_geometry": True}),
                       ("update_project_scenario_metadata", {"name": name, "description": "x"}),
                       ("update_project_flow_scope", {"name": name, "scope": scope}),
                       ("update_decision_study", {"project": study_project, "study_id": study_id, "name": "x"})):
        with pytest.raises(HTTPException) as exc:
            edit_card.preview(tool, args)
        assert exc.value.status_code in (403, 404), tool


def test_readback_after_the_real_call_equals_new(client, api_project, studies_on):
    name = api_project("card-readback")
    for tool, args, call in (
        ("update_project_settings", {"name": name, "derive_lengths_from_geometry": True},
         lambda: chat_tools.update_project_settings(name, True)),
        ("update_project_scenario_metadata", {"name": name, "description": "warm", "scenario_type": "stress"},
         lambda: chat_tools.update_project_scenario_metadata(name, description="warm", scenario_type="stress")),
    ):
        card = edit_card.preview(tool, args)
        call()
        assert edit_card.ADAPTERS[tool].readback(args, card["rows"]) == [r["new"] for r in card["rows"]], tool
    source = api_project("card-readback-src")
    study_id, project = _start(source, "card-readback-base")
    args = {"project": project, "study_id": study_id, "name": "Renamed", "settings": {"currency_year": 2030}}
    card = edit_card.preview("update_decision_study", args)
    chat_tools.update_decision_study(project, study_id, name="Renamed", settings={"currency_year": 2030})
    assert edit_card.ADAPTERS["update_decision_study"].readback(args, card["rows"]) == [r["new"] for r in card["rows"]]


def test_study_intake_is_capped_summarised_and_redacted(client, api_project, studies_on):
    source = api_project("card-cap")
    study_id, project = _start(source, "card-cap-base")
    base = {"project": project, "study_id": study_id}
    intake = {"api_token": "hunter2", **{f"k{i}": [1, 2, 3] for i in range(edit_card.STUDY_LEAF_CAP + 3)}}
    card = edit_card.preview("update_decision_study", {**base, "intake": intake})
    shown = [r for r in card["rows"] if r["role"] == "typed"]
    assert len(shown) == edit_card.STUDY_LEAF_CAP
    secret = next(r for r in shown if r["field"] == "intake.api_token")
    assert (secret["old"], secret["new"]) == (None, "set")
    assert all(r["new"] == "3 items" for r in shown if r is not secret)
    assert "hunter2" not in repr(card)
    assert any(r["role"] == "note" for r in card["rows"])
    # A rotated secret is not "unchanged", and still never appears.
    chat_tools.update_decision_study(project, study_id, intake={"api_token": "hunter2"})
    rotated = edit_card.preview("update_decision_study", {**base, "intake": {"api_token": "hunter3"}})
    assert _cells(rotated) == [("intake.api_token", "set", "set (changed)")]
    assert "hunter" not in repr(rotated)


def test_a_real_intake_step_shows_one_row_per_changed_field_in_the_panels_words(client, api_project, studies_on):
    source = api_project("card-step")
    study_id, project = _start(source, "card-step-base")
    base = {"project": project, "study_id": study_id}
    card = edit_card.preview("update_decision_study", {**base, "intake": {"site": {"zone": "DE", "connection_mw": 5.0}},
                                                      "settings": {"currency_year": 2030}})
    rows = {r["field"]: r for r in card["rows"]}
    site = rows["intake.site.connection_mw"]
    assert (site["label"], site["unit"], site["old"], site["new"]) == ("Your site \u00b7 Grid connection limit", "MW", 2.0, 5.0)
    assert "intake.site.zone" not in rows  # unchanged inside the step: no row
    year = rows["settings.currency_year"]
    assert (year["label"], year["new"]) == ("Currency year", "EUR of 2030")
    assert "fields" not in repr([r["old"] for r in card["rows"]])  # never "N fields \u2192 N fields"
    chat_tools.update_decision_study(project, study_id, intake={"site": {"zone": "DE", "connection_mw": 5.0}},
                                     settings={"currency_year": 2030})
    args = {**base, "intake": {"site": {"zone": "DE", "connection_mw": 5.0}}, "settings": {"currency_year": 2030}}
    assert edit_card.ADAPTERS["update_decision_study"].readback(args, card["rows"]) == [r["new"] for r in card["rows"]]


def test_the_study_perspective_shows_the_panels_words(client, api_project, studies_on):
    study_id, project = _start(api_project("card-persp"), "card-persp-base")
    for value in ("developer", "investor"):  # not implemented in this version: the route's refusal
        with pytest.raises(HTTPException) as exc:
            edit_card.preview("update_decision_study", {"project": project, "study_id": study_id,
                                                        "settings": {"perspective": value}})
        assert exc.value.status_code == 422
    from services.study.intake_words import INTAKE_WORDS

    assert INTAKE_WORDS["settings"]["perspective"]["words"]["site_owner"] == "The site owner\u2019s"


def test_studies_disabled_refuses_with_the_handlers_refusal(client, api_project, studies_on, monkeypatch):
    from routers import studies

    study_id, project = _start(api_project("card-off"), "card-off-base")

    def off():
        raise HTTPException(404, {"error_kind": "decision_studies_disabled", "message": "Decision studies are off."})

    monkeypatch.setattr(studies, "require_decision_studies_enabled", off)
    with pytest.raises(HTTPException) as exc:
        edit_card.preview("update_decision_study", {"project": project, "study_id": study_id, "name": "x"})
    assert (exc.value.status_code, exc.value.detail["error_kind"]) == (404, "decision_studies_disabled")
    with pytest.raises(HTTPException) as unknown:  # the gate comes before the study lookup
        edit_card.preview("update_decision_study", {"project": project, "study_id": "no-such-study", "name": "x"})
    assert (unknown.value.status_code, unknown.value.detail["error_kind"]) == (404, "decision_studies_disabled")

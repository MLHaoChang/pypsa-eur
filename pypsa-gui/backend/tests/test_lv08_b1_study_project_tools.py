"""
Live-voice issue 08, slice B1 (PR 1): the typed tools over the remaining
decision-study routes and the project scope/settings routes.

Each tool calls its router handler in process through `_route`, so these tests
pin (a) the tier against the HTTP method of every route the tool declares,
(b) that every `view` / `stage` reaches the route it names, (c) the routes'
own refusals passing through, including for a caller outside the project's
organization, and (d) that the intake preview writes nothing (GK B P2).
"""
from __future__ import annotations

import functools

import pytest
from fastapi import HTTPException

from harness import catalogue
from services import chat_tools
from tests.study_s4_support import dir_hash, enable_studies
from tests.test_study_tornado_routes import NO_PV_TOU

TOOLS = {
    "list_decision_studies": "read",
    "get_decision_study": "read",
    "preview_decision_study_intake": "read",
    "update_decision_study": "write",
    "abort_decision_study": "write",
    "delete_decision_study": "destructive",
    "get_site_template_form": "read",
    "get_project_flow_scope": "read",
    "update_project_flow_scope": "write",
    "update_project_scenario_metadata": "write",
    "update_project_settings": "write",
}
# The one POST a read tool covers: declared non-mutating in the parity file.
NON_MUTATING = {("POST", "/api/projects/{name}/studies/preview")}


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


@pytest.fixture
def outsider(second_identity):
    """Act as a user of ANOTHER organization for the rest of the test."""
    chat_tools.set_acting_user(second_identity["user_id"])
    yield


def _spy(monkeypatch, module, name: str, calls: list, result=None):
    """Replace a router handler with a recorder keeping its signature for `_route`."""
    original = getattr(module, name)

    @functools.wraps(original)
    def recorder(*args, **kwargs):
        calls.append((name, args))
        return {} if result is None else result

    monkeypatch.setattr(module, name, recorder)


def _start(project: str, base: str) -> tuple[str, str]:
    out = chat_tools.start_decision_study(project, "bess_at_site", "Site battery", NO_PV_TOU,
                                          base_project_name=base)
    return out["study_id"], out["project"]


# ── registry ────────────────────────────────────────────────────────────────


def test_the_tools_are_registered_with_tiers_matching_their_routes():
    names = {t["name"] for t in catalogue.TOOLS}
    for name, tier in TOOLS.items():
        assert name in names and name in chat_tools.DISPATCHERS, name
        assert catalogue.safety_tier_for(name) == tier, name
        routes = catalogue.TOOL_ROUTES[name]
        assert routes and all(isinstance(r, tuple) for r in routes), name
        if tier == "read":
            assert all(m == "GET" or (m, p) in NON_MUTATING for m, p in routes), name
        else:
            assert any(m != "GET" for m, _ in routes), name


def test_the_view_and_stage_enums_list_exactly_the_routed_views():
    schema = {t["name"]: t["input_schema"]["properties"] for t in catalogue.TOOLS}
    assert schema["get_decision_study"]["view"]["enum"] == list(chat_tools._STUDY_VIEWS)
    assert len(catalogue.TOOL_ROUTES["get_decision_study"]) == len(chat_tools._STUDY_VIEWS)
    assert schema["abort_decision_study"]["stage"]["enum"] == list(chat_tools._STUDY_STAGES)
    assert len(catalogue.TOOL_ROUTES["abort_decision_study"]) == len(chat_tools._STUDY_STAGES)


def test_the_delete_description_names_the_cascade():
    desc = next(t["description"] for t in catalogue.TOOLS if t["name"] == "delete_decision_study")
    assert "CASCADES to the option forks the study owns" in desc


# ── each view and stage reaches its route ───────────────────────────────────


@pytest.mark.parametrize("view, handler, extra", [
    ("summary", "get_study", ()),
    ("ledger", "get_ledger", ()),
    ("option_case", "get_option_case", ("bess_1h",)),
    ("report", "get_report", ()),
])
def test_each_view_reaches_its_route(client, api_project, monkeypatch, view, handler, extra):
    from routers import studies as R

    name = api_project("b1-view")
    calls: list = []
    _spy(monkeypatch, R, handler, calls, {"view": view})
    out = chat_tools.get_decision_study(name, "s1", view=view,
                                        option_id=extra[0] if extra else None)
    assert out == {"view": view}
    assert calls == [(handler, ("s1", *extra))]


@pytest.mark.parametrize("stage, handler", [
    ("options", "abort_study_run"),
    ("tornado", "abort_findings_tornado"),
])
def test_each_abort_stage_reaches_its_route(client, api_project, monkeypatch, stage, handler):
    from routers import studies as R

    name = api_project("b1-abort")
    calls: list = []
    _spy(monkeypatch, R, handler, calls, {"status": "aborted"})
    out = chat_tools.abort_decision_study(name, "s1", stage=stage)
    assert out == {"stage": stage, "status": "aborted"}
    assert calls == [(handler, ("s1",))]


def test_the_list_reads_the_library_only_on_request(client, api_project, monkeypatch):
    from routers import studies as R

    name = api_project("b1-list")
    calls: list = []
    _spy(monkeypatch, R, "list_studies", calls, [])
    _spy(monkeypatch, R, "get_study_library", calls, {"tariffs": []})
    assert chat_tools.list_decision_studies(name) == {"studies": []}
    assert chat_tools.list_decision_studies(name, include_library=True) == {
        "studies": [], "library": {"tariffs": []}}
    assert [c[0] for c in calls] == ["list_studies", "list_studies", "get_study_library"]


def test_bad_view_stage_and_missing_option_are_typed_refusals():
    for call in (lambda: chat_tools.get_decision_study("p", "s", view="everything"),
                 lambda: chat_tools.get_decision_study("p", "s", view="option_case")):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.detail["error_kind"] == "study_view_invalid"
    with pytest.raises(HTTPException) as exc:
        chat_tools.abort_decision_study("p", "s", stage="everything")
    assert exc.value.detail["error_kind"] == "study_stage_invalid"


# ── the real routes ─────────────────────────────────────────────────────────


def test_a_study_is_listed_read_renamed_and_deleted_by_chat(client, api_project, studies_on):
    sid, base = _start(api_project("b1-src"), "b1-base")

    listed = chat_tools.list_decision_studies(base, include_library=True)
    assert sid in {s["study_id"] for s in listed["studies"]}
    assert listed["library"]["tariffs"]

    assert chat_tools.get_decision_study(base, sid)["study_id"] == sid
    ledger = chat_tools.get_decision_study(base, sid, view="ledger")
    assert ledger["study_id"] == sid and ledger["ledger"] and ledger["maturity"]

    renamed = chat_tools.update_decision_study(base, sid, name="Renamed battery")
    assert renamed["name"] == "Renamed battery"
    with pytest.raises(HTTPException) as exc:
        chat_tools.update_decision_study(base, sid, settings={"perspective": "investor"})
    assert exc.value.status_code == 422

    with pytest.raises(HTTPException) as exc:  # never ran: the route's 404 passes through
        chat_tools.abort_decision_study(base, sid)
    assert exc.value.status_code == 404

    assert chat_tools.delete_decision_study(base, sid) == {"deleted": sid}
    assert sid not in {s["study_id"] for s in chat_tools.list_decision_studies(base)["studies"]}


def test_the_intake_preview_writes_nothing(client, api_project, studies_on):
    from services import dirty_state

    sid, base = _start(api_project("b1-prev-src"), "b1-prev")
    directory = chat_tools._authorized_project(base).directory
    before = (dir_hash(directory), chat_tools.list_decision_studies(base),
              dirty_state.is_dirty())

    out = chat_tools.preview_decision_study_intake(base, NO_PV_TOU)
    assert out

    assert (dir_hash(directory), chat_tools.list_decision_studies(base),
            dirty_state.is_dirty()) == before
    assert chat_tools.get_decision_study(base, sid)["study_id"] == sid


def test_the_flow_scope_round_trips_and_refuses_a_bad_shape(client, api_project):
    name = api_project("b1-scope")
    read = chat_tools.get_project_flow_scope(name)
    assert {"scope", "cards"} <= read.keys()
    chat_tools.update_project_flow_scope(name, read["scope"])
    stored = chat_tools.get_project_flow_scope(name)["scope"]
    assert stored.pop("source") == "project"  # stored now, no longer the preset
    assert stored == {k: v for k, v in read["scope"].items() if k != "source"}
    with pytest.raises(HTTPException) as exc:
        chat_tools.update_project_flow_scope(name, {"version": "nope"})
    assert exc.value.status_code == 422


def test_scenario_metadata_and_settings_write_only_what_is_sent(client, api_project):
    name = api_project("b1-meta")
    out = chat_tools.update_project_scenario_metadata(name, scenario_type="stress")
    assert out.scenario_type == "stress"
    out = chat_tools.update_project_scenario_metadata(name, description="cold winter")
    assert (out.scenario_type, out.scenario_description) == ("stress", "cold winter")
    with pytest.raises(HTTPException) as exc:
        chat_tools.update_project_scenario_metadata(name, scenario_type="nonsense")
    assert exc.value.status_code == 400

    import json

    from services import project_settings

    chat_tools.update_project_settings(name, derive_lengths_from_geometry=True)
    meta = json.loads((chat_tools._authorized_project(name).directory / "metadata.json").read_text())
    assert meta[project_settings.SETTINGS_KEY]["derive_lengths_from_geometry"] is True


def test_the_site_template_form_needs_a_user():
    assert chat_tools.get_site_template_form()
    chat_tools.set_acting_user(None)
    with pytest.raises(HTTPException) as exc:
        chat_tools.get_site_template_form()
    assert exc.value.status_code == 401


# ── a caller outside the project's organization (GK B P6) ───────────────────


@pytest.mark.parametrize("call", [
    lambda p: chat_tools.list_decision_studies(p),
    lambda p: chat_tools.get_decision_study(p, "s1"),
    lambda p: chat_tools.preview_decision_study_intake(p, NO_PV_TOU),
    lambda p: chat_tools.update_decision_study(p, "s1", name="x"),
    lambda p: chat_tools.abort_decision_study(p, "s1"),
    lambda p: chat_tools.delete_decision_study(p, "s1"),
    lambda p: chat_tools.get_project_flow_scope(p),
    lambda p: chat_tools.update_project_flow_scope(p, {}),
    lambda p: chat_tools.update_project_scenario_metadata(p, scenario_type="stress"),
    lambda p: chat_tools.update_project_settings(p, derive_lengths_from_geometry=True),
], ids=[n for n in TOOLS if n != "get_site_template_form"])
def test_a_non_member_gets_the_routes_refusal(client, api_project, studies_on, call, request):
    name = api_project("b1-acl")
    request.getfixturevalue("outsider")
    with pytest.raises(HTTPException) as exc:
        call(name)
    assert exc.value.status_code in (403, 404)

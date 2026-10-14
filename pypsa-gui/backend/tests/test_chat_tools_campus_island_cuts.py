"""The copilot's frequency-cut tools (campus island plan, I7 follow-up).

``campus_island_cuts`` (read) and ``campus_apply_island_cuts`` (the loop)
follow the campus tools' rules: each is registered, routed as a service
call, declares its safety tier, and calls ONE function in
``services/campus_island_cuts.py``, the one the panel's routes call.

The apply tool re-solves and saves the hub up to three times and rewrites
the campus file's sizes, as a background job: it is ``execution_long_running``
like the other multi-solve jobs, so it goes through the confirmation card in
Expert and in Guided, and it checks the edit lock of the project it NAMES
before it starts, as the HTTP handler does.
"""
import pytest
from fastapi import HTTPException

from db.models import Project, User
from harness import confirm
from services import campus_island_cuts as cic
from services import chat_tools, project_registry
from services.chat_tools_schema import TOOL_ROUTES, TOOLS, safety_tier_for

CUT_TOOLS = ("campus_island_cuts", "campus_apply_island_cuts")


@pytest.fixture
def hub(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        yield project_registry.create_root(db, user, "Cut Chat Hub")


def test_the_cut_tools_are_registered_routed_and_tiered():
    names = {t["name"] for t in TOOLS}
    schema = {t["name"]: t["input_schema"] for t in TOOLS}
    for name in CUT_TOOLS:
        assert name in names, name
        assert TOOL_ROUTES[name] == ["_service_call_"], name
        assert callable(chat_tools.DISPATCHERS[name]), name
        assert schema[name]["required"] == ["project_id"], name
        assert set(schema[name]["properties"]) == {"project_id"}, name
    assert safety_tier_for("campus_island_cuts") == "read"
    assert safety_tier_for("campus_apply_island_cuts") == "execution_long_running"


def test_the_apply_tool_goes_through_the_card_in_both_modes_and_the_read_in_neither():
    for guided in (False, True):
        tiers = confirm._confirm_tiers(guided)
        assert safety_tier_for("campus_apply_island_cuts") in tiers, guided
        assert safety_tier_for("campus_island_cuts") not in tiers, guided


def test_the_descriptions_say_what_the_model_must_say_and_what_apply_does():
    desc = {t["name"]: t["description"] for t in TOOLS if t["name"] in CUT_TOOLS}
    read, apply = desc["campus_island_cuts"], desc["campus_apply_island_cuts"]
    assert "heuristic" in read and "estimate" in read and "bridge" in read.lower()
    assert "assumed" in read
    assert "re-solve" in apply and "campus file" in apply and "campus_island_cuts" in apply
    assert "3" in apply or "three" in apply
    assert read.rstrip().endswith("Safety: read.")
    assert apply.rstrip().endswith("Safety: execution_long_running.")


def test_the_read_tool_answers_the_cuts_and_the_job(hub, monkeypatch):
    from routers import projects

    monkeypatch.setattr(projects, "_check_project_lock", lambda *a: pytest.fail("a read checks no lock"))
    seen = []
    monkeypatch.setattr(cic, "get_cuts", lambda p: seen.append(p) or {"proposed": [], "applied": []})
    monkeypatch.setattr(cic, "apply_status", lambda p: {"state": "running", "trials": 1})
    out = chat_tools.DISPATCHERS["campus_island_cuts"](project_id="Cut Chat Hub")
    assert out == {"proposed": [], "applied": [], "job": {"state": "running", "trials": 1}}
    assert isinstance(seen[0], Project) and seen[0].name == "Cut Chat Hub"


def test_the_apply_tool_starts_the_job_for_the_named_project_as_its_user(hub, seeded_identity, monkeypatch):
    calls = []
    monkeypatch.setattr(cic, "start_apply_job", lambda p, user_id: calls.append((p, user_id)) or {"state": "running"})
    assert chat_tools.DISPATCHERS["campus_apply_island_cuts"](project_id="Cut Chat Hub") == {"state": "running"}
    ((project, user_id),) = calls
    assert isinstance(project, Project) and project.name == "Cut Chat Hub"
    assert user_id == seeded_identity["user_id"]


def test_the_apply_tool_checks_the_named_projects_lock_before_starting(hub, monkeypatch):
    from routers import projects

    checked = []

    def refuse(db, target, user):
        checked.append(target.name)
        raise HTTPException(status_code=409, detail="locked by someone else")

    monkeypatch.setattr(projects, "_check_project_lock", refuse)
    monkeypatch.setattr(cic, "start_apply_job", lambda *a, **k: pytest.fail("the job must not start"))
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["campus_apply_island_cuts"](project_id="Cut Chat Hub")
    assert exc.value.status_code == 409 and checked == ["Cut Chat Hub"]


def test_a_refusal_of_the_job_reaches_the_model_with_its_reason(hub, monkeypatch):
    def nothing(*a, **k):
        raise HTTPException(status_code=422, detail="nothing to apply: the last campus run proposes no cut")

    monkeypatch.setattr(cic, "start_apply_job", nothing)
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["campus_apply_island_cuts"](project_id="Cut Chat Hub")
    assert exc.value.status_code == 422 and "nothing to apply" in exc.value.detail

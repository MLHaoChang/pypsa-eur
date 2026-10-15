"""The copilot's GFM-settings tools (E2E demo plan 2026-10-14 §7.2, GFM-b).

``campus_island_gfm_settings`` (read), ``campus_recommend_gfm_settings``
(queues the recommender as a campus job, ``execution_long_running`` like
``campus_run_study``) and ``campus_apply_gfm_settings`` (writes the island
sidecar and the campus file, ``write``) follow the campus tools' rules: each
is registered, routed as a service call, declares its safety tier, and calls
ONE function in ``services/campus_island_tuning.py``, the one the panel's
routes call. The two writes check the edit lock of the project they NAME.
"""
import pytest
from fastapi import HTTPException

from db.models import Project, User
from harness import confirm
from harness.catalogue import _SERVICE_CALL_NON_NETWORK_WRITES
from services import campus_electrical_service as ce
from services import campus_island_tuning as cit
from services import chat_tools, project_registry
from services.chat_tools_schema import TOOL_ROUTES, TOOLS, safety_tier_for

READ, RECOMMEND, APPLY = ("campus_island_gfm_settings", "campus_recommend_gfm_settings",
                          "campus_apply_gfm_settings")


@pytest.fixture
def hub(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        yield project_registry.create_root(db, user, "GFM Chat Hub")


def test_the_tools_are_registered_routed_and_tiered():
    schema = {t["name"]: t["input_schema"] for t in TOOLS}
    for name in (READ, RECOMMEND, APPLY):
        assert name in schema, name
        assert TOOL_ROUTES[name] == ["_service_call_"], name
        assert callable(chat_tools.DISPATCHERS[name]), name
        assert schema[name]["required"] == ["project_id"], name
    assert set(schema[READ]["properties"]) == {"project_id"}
    assert set(schema[APPLY]["properties"]) == {"project_id"}
    assert set(schema[RECOMMEND]["properties"]) == {"project_id", "allow_i_max"}
    assert safety_tier_for(READ) == "read"
    assert safety_tier_for(RECOMMEND) == "execution_long_running"
    assert safety_tier_for(APPLY) == "write"
    assert APPLY in _SERVICE_CALL_NON_NETWORK_WRITES          # files beside the network, never the live one


def test_the_writes_go_through_the_card_in_guided_and_the_read_never():
    tiers = confirm._confirm_tiers(True)
    assert safety_tier_for(RECOMMEND) in tiers and safety_tier_for(APPLY) in tiers
    for guided in (False, True):
        assert safety_tier_for(READ) not in confirm._confirm_tiers(guided)


def test_the_descriptions_say_what_the_model_must_say():
    desc = {t["name"]: t["description"] for t in TOOLS}
    read, rec, apply = desc[READ], desc[RECOMMEND], desc[APPLY]
    assert "heuristic screening" in read and "assumed" in read
    assert "not_achievable" in read and "capacity" in read
    assert "stale" in read and "re-solve" in read
    assert "wait_for_job" in rec and "campus_island_gfm_settings" in rec and "E4" in rec
    assert "campus file" in apply and "sidecar" in apply and "re-solve" in apply
    assert read.rstrip().endswith("Safety: read.")
    assert rec.rstrip().endswith("Safety: execution_long_running.")
    assert apply.rstrip().endswith("Safety: write.")


def test_the_read_tool_answers_the_settings_and_the_job(hub, monkeypatch):
    from routers import projects

    monkeypatch.setattr(projects, "_check_project_lock", lambda *a: pytest.fail("a read checks no lock"))
    seen = []
    monkeypatch.setattr(cit, "get_settings", lambda p: seen.append(p) or {"state": "not_run"})
    monkeypatch.setattr(ce, "run_job_state", lambda p: {"id": "j", "stage": cit.STAGE, "status": "running"})
    out = chat_tools.DISPATCHERS[READ](project_id="GFM Chat Hub")
    assert out == {"state": "not_run", "job": {"id": "j", "stage": cit.STAGE, "status": "running"}}
    assert isinstance(seen[0], Project) and seen[0].name == "GFM Chat Hub"


def test_the_read_tool_names_no_job_of_another_stage(hub, monkeypatch):
    monkeypatch.setattr(cit, "get_settings", lambda p: {"state": "ready"})
    monkeypatch.setattr(ce, "run_job_state", lambda p: {"id": "j", "stage": "rank", "status": "running"})
    assert chat_tools.DISPATCHERS[READ](project_id="GFM Chat Hub")["job"] is None


@pytest.mark.parametrize("tool, function, kw", [
    (RECOMMEND, "enqueue_settings", {"allow_i_max": True}),
    (APPLY, "apply_settings", {}),
])
def test_each_write_calls_its_service_for_the_named_project_after_its_lock(
        hub, seeded_identity, monkeypatch, tool, function, kw):
    from routers import projects

    order = []
    monkeypatch.setattr(projects, "_check_project_lock",
                        lambda db, target, user: order.append(("lock", target.name)))
    monkeypatch.setattr(cit, function, lambda *a, **k: order.append(("call", a, k)) or {"ok": True})
    assert chat_tools.DISPATCHERS[tool](project_id="GFM Chat Hub", **kw) == {"ok": True}
    assert order[0] == ("lock", "GFM Chat Hub")
    _call, args, k = order[1]
    assert isinstance(args[0], Project) and args[0].name == "GFM Chat Hub"
    if function == "enqueue_settings":
        assert args[1].id == seeded_identity["user_id"] and k == {"allow_i_max": True}


def test_a_refusal_reaches_the_model_with_its_reason(hub, monkeypatch):
    def refuse(*a, **k):
        raise HTTPException(status_code=422, detail="no setting within the bounds passes: capacity "
                                                    "is needed (I7), so there is nothing to apply")
    monkeypatch.setattr(cit, "apply_settings", refuse)
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS[APPLY](project_id="GFM Chat Hub")
    assert exc.value.status_code == 422 and "capacity" in exc.value.detail

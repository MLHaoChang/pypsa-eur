"""The copilot's campus island tools (campus island plan, I9a).

``campus_island_config`` (read), ``campus_set_island_config`` (write) and
``campus_run_island_study`` (write) follow the campus tools' rules: each is
registered, routed as a service call, declares its safety tier, and calls
ONE function in ``services/campus_electrical_service.py``, the same one the
panel's routes call.

Unlike the campus write tools of OPEN-ITEMS item 18, the two write tools
check the edit lock of the project they NAME before they write, as the HTTP
handlers do; ``test_write_surface_lock_policy`` follows that call.
"""
import pytest
from fastapi import HTTPException

from db.models import Project, User
from services import campus_electrical_service as ce
from services import chat_tools, project_registry
from services.chat_tools_schema import TOOL_ROUTES, TOOLS, safety_tier_for

ISLAND_TOOLS = ("campus_island_config", "campus_set_island_config", "campus_run_island_study")


@pytest.fixture
def hub(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        yield project_registry.create_root(db, user, "Chat Hub")


def test_the_island_tools_are_registered_routed_and_tiered():
    names = {t["name"] for t in TOOLS}
    for name in ISLAND_TOOLS:
        assert name in names, name
        assert TOOL_ROUTES[name] == ["_service_call_"], name
        assert callable(chat_tools.DISPATCHERS[name]), name
    assert safety_tier_for("campus_island_config") == "read"
    assert safety_tier_for("campus_set_island_config") == "write"
    assert safety_tier_for("campus_run_island_study") == "write"
    schema = {t["name"]: t["input_schema"] for t in TOOLS}
    assert schema["campus_island_config"]["required"] == ["project_id"]
    assert schema["campus_set_island_config"]["required"] == ["project_id", "config"]
    assert schema["campus_run_island_study"]["required"] == ["project_id"]
    assert schema["campus_run_island_study"]["properties"]["scenarios"]["items"]["enum"] == [
        "ups_bridged", "seamless", "planned"]


def test_the_descriptions_say_what_the_model_must_not_do():
    desc = {t["name"]: t["description"] for t in TOOLS if t["name"] in ISLAND_TOOLS}
    assert "not a certificate" in desc["campus_island_config"]
    assert "assumed" in desc["campus_set_island_config"]
    assert "ask before" in desc["campus_set_island_config"]
    assert "minutes" in desc["campus_run_island_study"]
    for name in ISLAND_TOOLS:
        tier = "read" if name == "campus_island_config" else "write"
        assert desc[name].rstrip().endswith(f"Safety: {tier}."), name


@pytest.mark.parametrize("tool, args, function, expected", [
    ("campus_island_config", {"project_id": "Chat Hub"}, "get_island", ()),
    ("campus_set_island_config", {"project_id": "Chat Hub", "config": {"units": {}}}, "save_island",
     ({"units": {}},)),
    ("campus_run_island_study", {"project_id": "Chat Hub"}, "run_island_study", (None,)),
    ("campus_run_island_study", {"project_id": "Chat Hub", "scenarios": ["seamless"]},
     "run_island_study", (["seamless"],)),
])
def test_each_island_dispatcher_calls_its_service_function(hub, monkeypatch, tool, args, function,
                                                           expected):
    calls = []
    monkeypatch.setattr(ce, function, lambda *a: calls.append(a) or {"ok": True})
    assert chat_tools.DISPATCHERS[tool](**args) == {"ok": True}
    (call,) = calls
    project, *rest = call
    assert isinstance(project, Project) and project.name == "Chat Hub"
    assert tuple(rest) == expected


@pytest.mark.parametrize("tool, args", [
    ("campus_set_island_config", {"project_id": "Chat Hub", "config": {}}),
    ("campus_run_island_study", {"project_id": "Chat Hub"}),
])
def test_the_write_tools_check_the_named_projects_lock_before_writing(hub, monkeypatch, tool, args):
    """The lock check runs for the project the tool names, and a refusal
    stops the tool before the service is reached."""
    from routers import projects

    checked = []

    def refuse(db, target, user):
        checked.append(target.name)
        raise HTTPException(status_code=409, detail="locked by someone else")

    monkeypatch.setattr(projects, "_check_project_lock", refuse)
    monkeypatch.setattr(ce, "save_island", lambda *a: pytest.fail("the service must not be reached"))
    monkeypatch.setattr(ce, "run_island_study", lambda *a: pytest.fail("the service must not be reached"))
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS[tool](**args)
    assert exc.value.status_code == 409
    assert checked == ["Chat Hub"]


def test_the_read_tool_checks_no_lock(hub, monkeypatch):
    from routers import projects

    monkeypatch.setattr(projects, "_check_project_lock",
                        lambda *a: pytest.fail("a read checks no lock"))
    monkeypatch.setattr(ce, "get_island", lambda *a: {"config": None, "study": None})
    assert chat_tools.DISPATCHERS["campus_island_config"](project_id="Chat Hub") == {
        "config": None, "study": None}

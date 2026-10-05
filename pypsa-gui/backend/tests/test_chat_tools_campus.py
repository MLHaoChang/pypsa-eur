"""The copilot's campus electrical tools (plan C6).

They follow the gridspine tools' rules:
* each is registered, routed as a service call, and declares its safety
  tier;
* each dispatcher resolves the project ROW under the acting user and calls
  ONE function in ``services/campus_electrical_service.py``, the same one
  the panel's routes call.
"""
import pytest

from db.models import Project, User
from services import campus_electrical_service as ce
from services import chat_tools, project_registry
from services.chat_tools_schema import TOOL_ROUTES, TOOLS, safety_tier_for

CAMPUS_TOOLS = ("campus_get_study", "campus_draft_campus", "campus_run_study")
LIBRARY_TOOLS = ("campus_get_library", "campus_set_library", "campus_get_investment")


@pytest.fixture
def hub(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        yield project_registry.create_root(db, user, "Chat Hub")


def test_every_campus_tool_is_registered_routed_and_tiered():
    names = {t["name"] for t in TOOLS}
    for name in CAMPUS_TOOLS:
        assert name in names, name
        assert TOOL_ROUTES[name] == ["_service_call_"], name
        assert callable(chat_tools.DISPATCHERS[name]), name
    assert safety_tier_for("campus_get_study") == "read"
    assert safety_tier_for("campus_draft_campus") == "write"
    assert safety_tier_for("campus_run_study") == "write"
    for tool in TOOLS:
        if tool["name"] in CAMPUS_TOOLS:
            assert tool["input_schema"]["required"] == ["project_id"], tool["name"]


def test_the_descriptions_say_what_the_model_must_not_do():
    desc = {t["name"]: t["description"] for t in TOOLS if t["name"] in CAMPUS_TOOLS}
    assert "stale" in desc["campus_get_study"] and "not a compliance certificate" in desc["campus_get_study"]
    assert "ask before" in desc["campus_draft_campus"]


@pytest.mark.parametrize("tool, args, function, expected", [
    ("campus_get_study", {"project_id": "Chat Hub"}, "get_state", ()),
    ("campus_draft_campus", {"project_id": "Chat Hub"}, "draft", (False,)),
    ("campus_draft_campus", {"project_id": "Chat Hub", "overwrite": True}, "draft", (True,)),
    ("campus_run_study", {"project_id": "Chat Hub", "k": 2, "pf": 0.95}, "run", ({"k": 2, "pf": 0.95},)),
    ("campus_run_study", {"project_id": "Chat Hub"}, "run", ({"pf": None},)),
    ("campus_run_study", {"project_id": "Chat Hub", "invest": False}, "run", ({"invest": False, "pf": None},)),
    ("campus_run_study", {"project_id": "Chat Hub", "invest": True}, "run", ({"invest": True, "pf": None},)),
])
def test_each_dispatcher_resolves_the_project_and_calls_its_service_function(hub, monkeypatch, tool, args,
                                                                           function, expected):
    calls = []
    monkeypatch.setattr(ce, function, lambda *a: calls.append(a) or {"ok": True})
    assert chat_tools.DISPATCHERS[tool](**args) == {"ok": True}
    assert len(calls) == 1
    project, *rest = calls[0]
    assert isinstance(project, Project) and project.name == "Chat Hub"
    assert tuple(rest) == expected


# --------------------------------------------------------------------------
# grid codes (plan C10)
# --------------------------------------------------------------------------

GRID_CODE_TOOLS = ("campus_list_grid_codes", "campus_extract_grid_code")


def test_the_grid_code_tools_are_registered_routed_and_tiered():
    names = {t["name"] for t in TOOLS}
    for name in GRID_CODE_TOOLS:
        assert name in names, name
        assert TOOL_ROUTES[name] == ["_service_call_"], name
        assert callable(chat_tools.DISPATCHERS[name]), name
    assert safety_tier_for("campus_list_grid_codes") == "read"
    assert safety_tier_for("campus_extract_grid_code") == "write"
    schema = {t["name"]: t["input_schema"] for t in TOOLS}
    assert schema["campus_list_grid_codes"]["required"] == ["project_id"]
    assert schema["campus_extract_grid_code"]["required"] == ["project_id", "document_id"]


def test_there_is_no_tool_that_publishes_or_confirms():
    names = {t["name"] for t in TOOLS}
    assert not {n for n in names if n.startswith("campus_") and ("publish" in n or "confirm" in n)}


def test_the_extract_description_says_the_document_is_data_and_a_person_confirms():
    desc = {t["name"]: t["description"] for t in TOOLS}["campus_extract_grid_code"]
    assert "untrusted data" in desc
    assert "draft" in desc and "a person must confirm" in desc
    assert "Never publish" in desc


@pytest.mark.parametrize("tool, args, function, expected", [
    ("campus_list_grid_codes", {"project_id": "Chat Hub"}, "list_grid_codes", ()),
    ("campus_extract_grid_code", {"project_id": "Chat Hub", "document_id": "a" * 64}, "extract", ("a" * 64,)),
])
def test_each_grid_code_dispatcher_calls_its_service_function(hub, monkeypatch, tool, args, function, expected):
    from services import campus_grid_code_service as gc
    calls = []
    monkeypatch.setattr(gc, function, lambda *a: calls.append(a) or {"ok": True})
    assert chat_tools.DISPATCHERS[tool](**args) == {"ok": True}
    (call,) = calls
    project, *rest = call
    assert isinstance(project, Project) and project.name == "Chat Hub"
    assert tuple(rest) == expected


# --------------------------------------------------------------------------
# the asset library and the investment (plan C9)
# --------------------------------------------------------------------------

def test_the_library_tools_are_registered_routed_and_tiered():
    names = {t["name"] for t in TOOLS}
    for name in LIBRARY_TOOLS:
        assert name in names, name
        assert TOOL_ROUTES[name] == ["_service_call_"], name
        assert callable(chat_tools.DISPATCHERS[name]), name
    assert safety_tier_for("campus_get_library") == "read"
    assert safety_tier_for("campus_get_investment") == "read"
    assert safety_tier_for("campus_set_library") == "write"
    schema = {t["name"]: t["input_schema"] for t in TOOLS}
    assert schema["campus_get_library"]["required"] == ["project_id"]
    assert schema["campus_get_investment"]["required"] == ["project_id"]
    assert schema["campus_set_library"]["required"] == ["project_id", "yaml"]
    assert schema["campus_run_study"]["properties"]["invest"] == {"type": "boolean"}
    desc = {t["name"]: t["description"] for t in TOOLS}
    for name in ("campus_get_library", "campus_get_investment"):
        assert desc[name].rstrip().endswith("Safety: read."), name
    assert desc["campus_set_library"].rstrip().endswith("Safety: write.")


def test_the_library_descriptions_say_what_the_model_must_not_do():
    desc = {t["name"]: t["description"] for t in TOOLS}
    assert "ask before" in desc["campus_set_library"] and "replac" in desc["campus_set_library"]
    assert "validated" in desc["campus_set_library"] and "422" in desc["campus_set_library"]
    inv = desc["campus_get_investment"]
    assert "placeholder" in inv and "assumed" in inv
    assert "tap" in inv and "not optimised" in inv
    assert "unresolved" in inv and "stale" in inv
    assert "invest" in desc["campus_run_study"]


@pytest.mark.parametrize("tool, args, function, expected", [
    ("campus_get_library", {"project_id": "Chat Hub"}, "get_library", ()),
    ("campus_set_library", {"project_id": "Chat Hub", "yaml": "discount_rate: {}"}, "save_library",
     ("discount_rate: {}",)),
    ("campus_get_investment", {"project_id": "Chat Hub"}, "get_investment", ()),
])
def test_each_library_dispatcher_resolves_the_project_and_calls_its_service_function(hub, monkeypatch, tool, args,
                                                                                   function, expected):
    calls = []
    monkeypatch.setattr(ce, function, lambda *a: calls.append(a) or {"ok": True})
    assert chat_tools.DISPATCHERS[tool](**args) == {"ok": True}
    (call,) = calls
    project, *rest = call
    assert isinstance(project, Project) and project.name == "Chat Hub"
    assert tuple(rest) == expected

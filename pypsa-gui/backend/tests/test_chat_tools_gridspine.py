"""The copilot's gridspine tools are wrappers over the SAME service functions
the router calls (increment 4, task 6).

Parity is structural, so the tests hold the structure: every tool is in the
registry with a safety tier, a route-table row and a dispatcher; every
dispatcher resolves the project under the acting identity and hands the
service the ROW; the chat edit carries `edited_by="chat"` and nothing else
ever does; and the tool LIST a turn sees depends on the kind of project the
session is bound to — the spec's "the agent gets the toolset matching the
open study".

NOT COVERED HERE, ON PURPOSE AND ON RECORD: ADR 0002 requires a live API probe
for chat changes — a real model choosing and calling these tools. This session
had no Anthropic key, so that probe has not run. These tools should be treated
as unverified against a model until it has (increment-4 plan, task 6).
"""
import uuid

import pytest
from fastapi import HTTPException

from db.models import Project, User
from services import chat_service, chat_tools
from services import gridspine_service as gs
from services.chat_tools_schema import TOOL_ROUTES, TOOLS, safety_tier_for

GRIDSPINE_TOOLS = (
    "gridspine_create_study",
    "gridspine_set_dispatch_source",
    "gridspine_get_config",
    "gridspine_update_config",
    "gridspine_run_pipeline",
    "gridspine_get_stage_status",
    "gridspine_list_ranked_snapshots",
    "gridspine_get_assumption_ledger",
    "gridspine_edit_template_param",
    "gridspine_export_handoff_bundle",
)
PROJECT_SCOPED = tuple(t for t in GRIDSPINE_TOOLS if t != "gridspine_create_study")
CONFIG = {"hours": 24, "k": 1, "window": 24, "overlap": 0, "screen": False}


@pytest.fixture
def study(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        created = gs.create_study(db, user, "Chat Study", config=CONFIG)
        yield db.get(Project, uuid.UUID(created["id"]))


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

def test_every_gridspine_tool_is_registered_routed_and_tiered():
    names = {t["name"] for t in TOOLS}
    for name in GRIDSPINE_TOOLS:
        assert name in names, name
        assert TOOL_ROUTES[name] == ["_service_call_"], name
        assert callable(chat_tools.DISPATCHERS[name]), name
        assert safety_tier_for(name) in ("read", "write", "execution_long_running"), name


def test_the_run_is_long_running_and_the_reads_are_reads():
    assert safety_tier_for("gridspine_run_pipeline") == "execution_long_running"
    for name in ("gridspine_get_stage_status", "gridspine_list_ranked_snapshots",
                 "gridspine_get_assumption_ledger", "gridspine_get_config"):
        assert safety_tier_for(name) == "read", name
    for name in ("gridspine_create_study", "gridspine_set_dispatch_source",
                 "gridspine_edit_template_param", "gridspine_export_handoff_bundle",
                 "gridspine_update_config"):
        assert safety_tier_for(name) == "write", name


def test_no_gridspine_tool_is_lock_gated():
    """They write the project's gridspine/ subdirectory, never the resident
    network — the same reason the /api/projects/* write tools are not gated."""
    assert not set(GRIDSPINE_TOOLS) & set(chat_tools._lock_gated_tool_names())


def test_project_scoped_tools_take_the_project_by_name():
    for tool in TOOLS:
        if tool["name"] in PROJECT_SCOPED:
            assert "project_id" in tool["input_schema"]["required"], tool["name"]


# --------------------------------------------------------------------------
# dispatchers are wrappers
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool, args, function", [
    ("gridspine_get_stage_status", {"project_id": "Chat Study"}, "get_stage_status"),
    ("gridspine_list_ranked_snapshots", {"project_id": "Chat Study"}, "list_ranked_snapshots"),
    ("gridspine_get_assumption_ledger", {"project_id": "Chat Study"}, "get_assumption_ledger"),
    ("gridspine_run_pipeline", {"project_id": "Chat Study"}, "run_pipeline"),
    ("gridspine_set_dispatch_source", {"project_id": "Chat Study"}, "set_dispatch_source"),
    ("gridspine_get_config", {"project_id": "Chat Study"}, "get_config"),
])
def test_each_dispatcher_resolves_the_project_and_calls_its_service_function(
    study, monkeypatch, tool, args, function
):
    calls = []

    def fake(*a, **kw):
        calls.append((a, kw))
        return {"ok": True}

    monkeypatch.setattr(gs, function, fake)
    assert chat_tools.DISPATCHERS[tool](**args) == {"ok": True}
    assert len(calls) == 1
    rows = [x for x in calls[0][0] if isinstance(x, Project)]
    assert rows and rows[0].id == study.id          # the ROW, resolved under the acting user


def test_the_chat_edit_carries_chat_provenance_and_cannot_be_told_otherwise(study, monkeypatch):
    seen = {}
    monkeypatch.setattr(gs, "edit_template_param",
                        lambda project, unit_id, param, value, source, edited_by:
                        seen.update(edited_by=edited_by, unit_id=unit_id, value=value) or {"ok": True})
    chat_tools.DISPATCHERS["gridspine_edit_template_param"](
        project_id="Chat Study", unit_id="G_BUS_32", param="h_s", value=4.25, source="datasheet",
    )
    assert seen["edited_by"] == "chat"
    assert seen["unit_id"] == "G_BUS_32" and seen["value"] == 4.25
    # the schema offers no way to pass edited_by: it is not a parameter
    tool = next(t for t in TOOLS if t["name"] == "gridspine_edit_template_param")
    assert "edited_by" not in tool["input_schema"]["properties"]


def test_create_goes_through_the_acting_user(monkeypatch, seeded_identity):
    seen = {}

    def fake(db, user, name, config=None):
        seen.update(user_id=user.id, name=name, config=config)
        return {"id": "x"}

    monkeypatch.setattr(gs, "create_study", fake)
    chat_tools.DISPATCHERS["gridspine_create_study"](name="Via Chat", config=CONFIG)
    assert seen["user_id"] == seeded_identity["user_id"]
    assert seen["name"] == "Via Chat" and seen["config"] == CONFIG


def test_export_returns_a_file_description_not_a_path_only(study, monkeypatch, tmp_path):
    target = gs.gridspine_dir(study) / "bundle_h7.zip"
    target.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    monkeypatch.setattr(gs, "export_handoff_bundle", lambda project, hour: target)
    out = chat_tools.DISPATCHERS["gridspine_export_handoff_bundle"](project_id="Chat Study", hour=7)
    assert out == {"path": str(target), "filename": "bundle_h7.zip", "bytes": 22}


def test_an_unknown_or_foreign_project_is_404_like_the_router(study):
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["gridspine_get_stage_status"](project_id="No Such Study")
    assert exc.value.status_code == 404


def test_a_dispatch_source_omitting_both_arguments_means_generate(study, monkeypatch, seeded_identity):
    seen = {}
    monkeypatch.setattr(gs, "set_dispatch_source",
                        lambda db, project, source, user=None: seen.update(source=source, user=user) or {})
    chat_tools.DISPATCHERS["gridspine_set_dispatch_source"](project_id="Chat Study")
    assert seen["source"] == "generate"
    chat_tools.DISPATCHERS["gridspine_set_dispatch_source"](project_id="Chat Study", from_dispatch="/x")
    assert seen["source"] == {"from_dispatch": "/x"}
    chat_tools.DISPATCHERS["gridspine_set_dispatch_source"](project_id="Chat Study", from_project="Solved 39")
    assert seen["source"] == {"from_project": "Solved 39"}
    assert seen["user"].id == seeded_identity["user_id"]      # resolved under the acting user


def test_the_config_read_names_the_source_project_through_the_db(study, monkeypatch):
    seen = {}
    monkeypatch.setattr(gs, "get_config", lambda project, db=None: seen.update(db=db) or {})
    chat_tools.DISPATCHERS["gridspine_get_config"](project_id="Chat Study")
    assert seen["db"] is not None


# --------------------------------------------------------------------------
# the toolset matches the open project
# --------------------------------------------------------------------------

class _Ctx:
    def __init__(self, project_uuid=None):
        self.project_uuid = project_uuid


def _names(tools):
    return {t["name"] for t in tools}


def test_an_unbound_session_sees_only_the_create_tool():
    names = _names(chat_service._tools_payload(None))
    assert "gridspine_create_study" in names
    assert not (names & set(PROJECT_SCOPED))
    # and every ordinary tool is still there
    assert len(names) == len(TOOLS) - len(PROJECT_SCOPED)


def test_a_capacity_expansion_project_sees_only_the_create_tool(_auth_db, seeded_identity):
    from services import project_registry
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        plain = project_registry.create_root(db, user, "Plain For Chat")
    names = _names(chat_service._tools_payload(_Ctx(str(plain.id))))
    assert "gridspine_create_study" in names
    assert not (names & set(PROJECT_SCOPED))


def test_a_planning_project_sees_every_gridspine_tool(study):
    names = _names(chat_service._tools_payload(_Ctx(str(study.id))))
    assert set(GRIDSPINE_TOOLS) <= names
    assert len(names) == len(TOOLS)


def test_a_bad_binding_degrades_to_the_unbound_toolset_rather_than_failing():
    names = _names(chat_service._tools_payload(_Ctx("not-a-uuid")))
    assert "gridspine_create_study" in names
    assert not (names & set(PROJECT_SCOPED))


def test_update_config_forwards_only_the_fields_the_model_set(study, monkeypatch):
    seen = {}
    monkeypatch.setattr(gs, "update_config", lambda project, patch: seen.update(patch=patch) or patch)
    chat_tools.DISPATCHERS["gridspine_update_config"](project_id="Chat Study", k=3, screen=None)
    assert seen["patch"] == {"k": 3}


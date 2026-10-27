"""
Live-voice issue 08, slice B-campus: the typed tools over the campus
electrical and gridspine routes the GUI reaches.

Each tool calls its route's own handler in-process, so the route's lock check,
body validation and refusals are the tool's. Pins: registry and tiers by route
method, the handler reaching the service with the tool's arguments, the
named project's lock refusing a write before the service, a non-member getting
the route's 404, reads trimmed under the result cap, and the upload tools
reading only the active project's uploads.
"""
from __future__ import annotations

import io

import pytest
from fastapi import HTTPException, UploadFile

from db.models import Project, User
from harness import catalogue
from services import campus_electrical_service as ce
from services import chat_tools, gridspine_service as gs, project_registry

TOOLS = {
    "campus_save_campus": "write",
    "campus_milp_status": "read",
    "campus_start_milp": "execution_long_running",
    "campus_cancel_milp": "write",
    "campus_set_grid_limit": "write",
    "campus_reset_grid_limit": "write",
    "campus_island_report": "read",
    "campus_cancel_island_cuts": "write",
    "campus_remove_island_cut": "write",
    "campus_clear_island_cuts": "destructive",
    "campus_gfm_settings_card": "read",
    "campus_reset_library": "destructive",
    "gridspine_upload_external_dispatch": "write",
    "gridspine_upload_readback": "write",
}
P = {"project_id": "Chat Hub"}


@pytest.fixture
def hub(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        yield project_registry.create_root(db, user, "Chat Hub")


def _spy(monkeypatch, module, name: str, calls: list, result=None):
    def recorder(*args, **kwargs):
        calls.append((name, args, kwargs))
        return {"ok": True} if result is None else result

    monkeypatch.setattr(module, name, recorder)


# ── registry ────────────────────────────────────────────────────────────────


def test_the_tools_are_registered_with_tiers_matching_their_routes():
    names = {t["name"] for t in catalogue.TOOLS}
    for name, tier in TOOLS.items():
        assert name in names and name in chat_tools.DISPATCHERS, name
        assert catalogue.safety_tier_for(name) == tier, name
        routes = catalogue.TOOL_ROUTES[name]
        assert routes and all(isinstance(r, tuple) for r in routes), name
        assert all((m == "GET") == (tier == "read") for m, _ in routes), name
        assert name.startswith(("campus_", "gridspine_")), name  # the gridspine toolset


def test_none_of_them_edits_the_live_network():
    for name in TOOLS:
        assert not catalogue.tool_edits_network(name), name
        assert name not in chat_tools._lock_gated_tool_names(), name


# ── each tool reaches its service through the route ─────────────────────────


def _services(monkeypatch):
    from harness import edit_card
    from services import campus_island_cuts as cic
    from services import campus_island_report
    from services.adequacy import grid_limits

    calls: list = []
    _spy(monkeypatch, ce, "save_campus", calls)
    _spy(monkeypatch, ce, "milp_job_status", calls)
    _spy(monkeypatch, ce, "start_milp_job", calls)
    _spy(monkeypatch, ce, "cancel_milp_job", calls)
    _spy(monkeypatch, ce, "reset_library", calls)
    _spy(monkeypatch, grid_limits, "set_limit", calls)
    _spy(monkeypatch, grid_limits, "clear_limit", calls, result=True)
    _spy(monkeypatch, campus_island_report, "island_report", calls)
    _spy(monkeypatch, cic, "cancel_apply_job", calls)
    _spy(monkeypatch, cic, "remove_cut", calls)
    _spy(monkeypatch, cic, "clear_cuts", calls)
    _spy(monkeypatch, edit_card, "preview", calls)
    return calls


@pytest.mark.parametrize("tool, args, service, check", [
    ("campus_save_campus", {"yaml": "buses: []"}, "save_campus", lambda a, k: a[1:] == ("buses: []",)),
    ("campus_milp_status", {}, "milp_job_status", lambda a, k: len(a) == 1),
    ("campus_start_milp", {"k": 3, "invest": False}, "start_milp_job",
     lambda a, k: a[1]["k"] == 3 and a[1]["invest"] is False and a[1]["n_minus_1"] is True),
    ("campus_cancel_milp", {}, "cancel_milp_job", lambda a, k: len(a) == 1),
    ("campus_set_grid_limit", {"item": "oscillation", "source": "datasheet", "value": 0.5, "note": "OEM"},
     "set_limit", lambda a, k: a[1] == "oscillation" and k["value"] == 0.5 and k["source"] == "datasheet"
     and k["note"] == "OEM"),
    ("campus_reset_grid_limit", {"item": "fault_level"}, "clear_limit", lambda a, k: a[1] == "fault_level"),
    ("campus_island_report", {}, "island_report", lambda a, k: len(a) == 1),
    ("campus_cancel_island_cuts", {}, "cancel_apply_job", lambda a, k: len(a) == 1),
    ("campus_remove_island_cut", {"period": 2030, "hour": 7, "kind": "inertia"}, "remove_cut",
     lambda a, k: k == {"period": 2030, "hour": 7, "kind": "inertia"}),
    ("campus_clear_island_cuts", {}, "clear_cuts", lambda a, k: len(a) == 1),
    ("campus_reset_library", {}, "reset_library", lambda a, k: len(a) == 1),
])
def test_each_tool_reaches_its_service_through_the_route(hub, monkeypatch, tool, args, service, check):
    calls = _services(monkeypatch)
    out = chat_tools.DISPATCHERS[tool](**P, **args)
    assert isinstance(out, dict)
    (name, a, k), = calls
    assert name == service
    if a and isinstance(a[0], Project):
        assert a[0].name == "Chat Hub"
    assert check(a, k), (a, k)


def test_the_gfm_card_is_the_apply_tools_card_and_writes_nothing(hub, monkeypatch):
    calls = _services(monkeypatch)
    chat_tools.DISPATCHERS["campus_gfm_settings_card"](**P)
    ((name, a, k),) = calls
    assert name == "preview" and a[0] == "campus_apply_gfm_settings"


def test_a_bad_body_is_the_routes_validation_error(hub, monkeypatch):
    from pydantic import ValidationError

    calls = _services(monkeypatch)
    with pytest.raises(ValidationError):
        chat_tools.DISPATCHERS["campus_start_milp"](**P, k=0)
    assert calls == []


# ── lock, membership ────────────────────────────────────────────────────────

WRITES = [(n, a) for n, a in [
    ("campus_save_campus", {"yaml": "x"}), ("campus_start_milp", {}), ("campus_cancel_milp", {}),
    ("campus_set_grid_limit", {"item": "oscillation", "source": "assumed"}),
    ("campus_reset_grid_limit", {"item": "oscillation"}), ("campus_cancel_island_cuts", {}),
    ("campus_remove_island_cut", {"period": 0, "hour": 0, "kind": "inertia"}),
    ("campus_clear_island_cuts", {}), ("campus_reset_library", {}),
]]


@pytest.mark.parametrize("tool, args", WRITES)
def test_the_write_tools_refuse_under_the_named_projects_lock(hub, monkeypatch, tool, args):
    from routers import projects

    checked = []

    def refuse(db, target, user):
        checked.append(target.name)
        raise HTTPException(status_code=409, detail="locked by someone else")

    monkeypatch.setattr(projects, "_check_project_lock", refuse)
    calls = _services(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS[tool](**P, **args)
    assert exc.value.status_code == 409 and checked == ["Chat Hub"] and calls == []


@pytest.mark.parametrize("tool", ["campus_milp_status", "campus_island_report", "campus_gfm_settings_card"])
def test_the_reads_check_no_lock(hub, monkeypatch, tool):
    from routers import projects

    monkeypatch.setattr(projects, "_check_project_lock", lambda *a: pytest.fail("a read checks no lock"))
    _services(monkeypatch)
    chat_tools.DISPATCHERS[tool](**P)


@pytest.mark.parametrize("tool, args", [("campus_milp_status", {}), *WRITES])
def test_a_non_member_gets_the_routes_404(hub, second_identity, monkeypatch, tool, args):
    calls = _services(monkeypatch)
    chat_tools.set_acting_user(second_identity["user_id"])
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS[tool](**P, **args)
    assert exc.value.status_code == 404 and calls == []


def test_a_large_island_report_is_trimmed_under_the_result_cap(hub, monkeypatch):
    from services import campus_island_report

    rows = [{"unit": f"U{i}", "value": i, "note": "assumed inertia constant"} for i in range(500)]
    monkeypatch.setattr(campus_island_report, "island_report",
                        lambda project: {"caveat": "screening only", "tables": {"units": rows}})
    out = chat_tools.campus_island_report("Chat Hub")
    assert chat_tools._json_len(out) <= chat_tools._B2B_BUDGET + 200
    assert out["caveat"] == "screening only"
    assert out["truncated"]["tables.units"]["total"] == 500


# ── gridspine uploads ───────────────────────────────────────────────────────


@pytest.fixture
def uploads(hub, monkeypatch):
    files = {"d1": ("dispatch.csv", b"t,g\n0,1\n"), "l1": ("loads.csv", b"t,l\n0,1\n"),
             "b1": ("bus.csv", b"bus,v\nB1,1.0\n")}
    monkeypatch.setattr(chat_tools, "_require_active_project", lambda: "Chat Hub")
    monkeypatch.setattr(chat_tools, "_active_upload",
                        lambda file_id: UploadFile(filename=files[file_id][0],
                                                   file=io.BytesIO(files[file_id][1])))
    return files


def test_external_dispatch_hands_the_uploaded_bytes_to_the_service(uploads, monkeypatch):
    calls = []
    _spy(monkeypatch, gs, "upload_external_dispatch", calls)
    chat_tools.gridspine_upload_external_dispatch("Chat Hub", "d1", "l1")
    ((_, a, _k),) = calls
    assert a[0].name == "Chat Hub"
    assert a[1:] == (b"t,g\n0,1\n", "dispatch.csv", b"t,l\n0,1\n", "loads.csv")


def test_readback_hands_the_uploaded_bus_csv_and_hour_to_the_service(uploads, monkeypatch):
    calls = []
    _spy(monkeypatch, gs, "upload_readback", calls)
    chat_tools.gridspine_upload_readback("Chat Hub", 12, "b1")
    ((_, a, _k),) = calls
    assert a[1:] == (12, b"bus,v\nB1,1.0\n", "bus.csv", None, None)


def test_the_upload_tools_refuse_a_project_other_than_the_active_one(uploads, monkeypatch):
    monkeypatch.setattr(chat_tools, "_require_active_project", lambda: "Another")
    monkeypatch.setattr(gs, "upload_readback", lambda *a: pytest.fail("never reached"))
    with pytest.raises(HTTPException) as exc:
        chat_tools.gridspine_upload_readback("Chat Hub", 0, "b1")
    assert exc.value.status_code == 409
    assert exc.value.detail["error_kind"] == "upload_project_mismatch"


def test_the_upload_tools_check_the_named_projects_lock(uploads, monkeypatch):
    from routers import projects

    def refuse(db, target, user):
        raise HTTPException(status_code=409, detail="locked by someone else")

    monkeypatch.setattr(projects, "_check_project_lock", refuse)
    monkeypatch.setattr(gs, "upload_external_dispatch", lambda *a: pytest.fail("never reached"))
    with pytest.raises(HTTPException) as exc:
        chat_tools.gridspine_upload_external_dispatch("Chat Hub", "d1")
    assert exc.value.status_code == 409


# ── the existing tools now matched to their routes ──────────────────────────


def test_extract_passes_the_routes_profile_id_and_overwrite(hub, monkeypatch):
    from services import campus_grid_code_service as gc

    calls = []
    _spy(monkeypatch, gc, "extract", calls)
    chat_tools.campus_extract_grid_code("Chat Hub", "doc1", profile_id="tso", overwrite=True)
    ((_, a, _k),) = calls
    assert a[1:] == ("doc1", "tso", True)

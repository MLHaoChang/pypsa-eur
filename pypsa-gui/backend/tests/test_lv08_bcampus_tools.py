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
    "campus_remove_island_cut": "destructive",
    "campus_clear_island_cuts": "destructive",
    "campus_gfm_settings_card": "read",
    "campus_reset_library": "destructive",
    "gridspine_upload_external_dispatch": "write",
    "gridspine_upload_readback": "write",
    # GRIDCODE-CHAT-1
    "campus_upload_grid_code_document": "write",
    "campus_delete_grid_code_document": "destructive",
    "campus_new_grid_code_draft": "write",
    "campus_get_grid_code_draft": "read",
    "campus_save_grid_code_draft": "write",
    "campus_delete_grid_code_draft": "destructive",
    "campus_confirm_grid_code_limit": "destructive",
    "campus_publish_grid_code": "destructive",
    "campus_get_published_grid_code": "read",
    "campus_delete_published_grid_code": "destructive",
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
             "b1": ("bus.csv", b"bus,v\nB1,1.0\n"), "p1": ("code.pdf", b"%PDF-1.7 grid code")}
    from types import SimpleNamespace

    from services import upload_service

    monkeypatch.setattr(upload_service, "get_upload_meta",
                        lambda project, file_id: SimpleNamespace(mime="application/pdf" if file_id == "p1"
                                                                 else "text/csv"))
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


def test_the_gridspine_upload_tools_keep_the_routes_declared_lock_exemption(uploads, monkeypatch):
    """GK B condition 2: the routes are STUDY_NOT_LOCK_COORDINATED; the tools add no lock."""
    from routers import projects
    from tests import test_write_surface_lock_policy as policy

    for tool in ("gridspine_upload_external_dispatch", "gridspine_upload_readback"):
        ((method, path),) = catalogue.TOOL_ROUTES[tool]
        assert policy.ROUTE_POLICY[(method, path)][0] == policy.STUDY_NOT_LOCK_COORDINATED, tool
    monkeypatch.setattr(projects, "_check_project_lock", lambda *a: pytest.fail("no lock check"))
    calls = []
    _spy(monkeypatch, gs, "upload_external_dispatch", calls)
    chat_tools.gridspine_upload_external_dispatch("Chat Hub", "d1")
    assert len(calls) == 1


def test_an_oversized_stored_upload_gets_the_routes_refusal(uploads, monkeypatch):
    """GK B condition 5: the bytes go through the route's own UploadBudget."""
    from services import campus_grid_code_service as gc

    monkeypatch.setattr(gc, "MAX_PDF_BYTES", 4)
    monkeypatch.setattr(gc, "upload_document", lambda *a: pytest.fail("never reached"))
    with pytest.raises(HTTPException) as exc:
        chat_tools.campus_upload_grid_code_document("Chat Hub", "p1")
    assert exc.value.status_code == 413


# ── the existing tools now matched to their routes ──────────────────────────


def test_extract_passes_the_routes_profile_id_and_overwrite(hub, monkeypatch):
    from services import campus_grid_code_service as gc

    calls = []
    _spy(monkeypatch, gc, "extract", calls)
    chat_tools.campus_extract_grid_code("Chat Hub", "doc1", profile_id="tso", overwrite=True)
    ((_, a, _k),) = calls
    assert a[1:] == ("doc1", "tso", True)


# ── async handlers, the joint optimisation's card (GK B conditions 1, 6') ───


def test_an_async_route_tool_returns_the_payload_not_a_coroutine(hub, monkeypatch):
    import inspect

    from routers import campus_electrical

    assert inspect.iscoroutinefunction(campus_electrical.start_milp)
    _services(monkeypatch)
    out = chat_tools.campus_start_milp("Chat Hub")
    assert out == {"ok": True} and not inspect.iscoroutine(out)


def test_the_milp_start_is_carded_and_its_card_starts_nothing(hub, monkeypatch):
    from harness import confirm, edit_card

    calls = []
    _spy(monkeypatch, ce, "start_milp_job", calls)
    assert edit_card.is_edit("campus_start_milp")
    for guided in (False, True):
        assert confirm.confirmation_policy("execution_long_running", guided=guided) == "user"
    card = edit_card.preview("campus_start_milp", {"project_id": "Chat Hub", "k": 4})
    assert calls == []  # the card is built; no job started
    shown = {r["field"]: r["new"] for r in card["rows"] if r["role"] == "typed"}
    assert shown["k"] == 4 and shown["invest"] is True and shown["n_minus_1"] is True


@pytest.mark.parametrize("decision, started", [("deny", 0), ("approve", 1)])
def test_the_turn_loop_starts_no_milp_job_before_the_card_is_approved(hub, monkeypatch, decision, started):
    from services import chat_service

    calls = []
    _spy(monkeypatch, ce, "start_milp_job", calls)
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    try:
        collector, cards = [], []
        tu = {"id": "tu-milp", "name": "campus_start_milp", "input": {**P, "k": 4}}
        for event, data in chat_service._dispatch_real_tool_call(session, tu, collector):
            if event == "tool_pending_confirmation":
                assert calls == []  # the card is up; no job yet
                cards.append(data["edit_card"])
                session.record_decision(data["confirmation_token"], decision)
    finally:
        chat_tools.set_chat_session(None)
    assert len(cards) == 1 and len(calls) == started
    assert len(collector) == 1 and bool(collector[0].get("is_error")) is (decision == "deny")


def test_a_second_milp_start_gets_the_handlers_own_409(hub, monkeypatch):
    monkeypatch.setitem(ce._MILP_SLOT, "active", {"state": "running", "project": "Other", "thread": None})
    monkeypatch.setattr(ce, "profiles", lambda project: {"eu_rfg_dcc_ce": "EU"}, raising=False)
    with pytest.raises(HTTPException) as exc:
        chat_tools.campus_start_milp("Chat Hub")
    assert exc.value.status_code == 409 and "already running" in str(exc.value.detail)


# ── whole-file writes refuse a stale read (GK B condition 3) ────────────────


def test_save_campus_refuses_a_stale_base_and_its_card_shows_the_change(hub, monkeypatch):
    from harness import campus_cards, edit_card

    f = ce.campus_dir(hub) / ce.CAMPUS_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("a: 1\nb: 2\n")
    monkeypatch.setattr(ce, "save_campus", lambda *a: pytest.fail("a stale write never reaches the service"))
    with pytest.raises(HTTPException) as exc:
        chat_tools.campus_save_campus("Chat Hub", "a: 1\nb: 3\n", base_sha256="0" * 64)
    assert exc.value.status_code == 409 and exc.value.detail["error_kind"] == "file_changed_since_read"
    base = campus_cards.sha256_text("a: 1\nb: 2\n")
    card = edit_card.preview("campus_save_campus", {"project_id": "Chat Hub", "yaml": "a: 1\nb: 3\n",
                                                    "base_sha256": base})
    ((row,),) = [[r for r in card["rows"] if r["role"] == "typed"]]
    assert (row["field"], row["old"], row["new"]) == ("b", 2, 3)
    assert card["binding"] == {"sha256": base}
    with pytest.raises(HTTPException):
        edit_card.preview("campus_save_campus", {"project_id": "Chat Hub", "yaml": "a: 2\n"})  # no base


def test_get_study_carries_the_digest_save_campus_names(hub, monkeypatch):
    monkeypatch.setattr(ce, "get_state", lambda project: {"campus_yaml": "a: 1\n"})
    from harness.campus_cards import sha256_text

    assert chat_tools.campus_get_study("Chat Hub")["campus_sha256"] == sha256_text("a: 1\n")


# ── grid codes (GRIDCODE-CHAT-1) ────────────────────────────────────────────

EXTRACTED = {"q_range_demand": {"q_min": -0.48, "q_max": 0.48, "source": "extracted", "page": 12,
                                "clause": "4.2", "quote": "between 0.48 leading and lagging"}}


def _shaped(pid="tso", profile=None, review=None, unconfirmed=("q_range_demand",)):
    profile = {"title": "TSO code", **(EXTRACTED if profile is None else profile)}
    return {"id": pid, "yaml": f"title: TSO code # {pid}\n", "profile": profile, "review": review,
            "unconfirmed": list(unconfirmed), "document": None}


def test_the_confirm_card_shows_the_limit_and_its_quote(hub, monkeypatch):
    from harness import edit_card
    from services import campus_grid_code_service as gc

    review = {"limits": {"q_range_demand": {"quote_found": False, "found_on_page": 13}}}
    monkeypatch.setattr(gc, "get_draft", lambda project, pid: _shaped(pid, review=review))
    monkeypatch.setattr(gc, "confirm", lambda *a: pytest.fail("the card confirms nothing"))
    card = edit_card.preview("campus_confirm_grid_code_limit",
                             {"project_id": "Chat Hub", "profile_id": "tso", "limit": "q_range_demand"})
    typed = {r["field"]: (r["old"], r["new"]) for r in card["rows"] if r["role"] == "typed"}
    assert typed["source"] == ("extracted", "code") and typed["q_min"] == (-0.48, -0.48)
    notes = " ".join(r["note"] for r in card["rows"] if r["role"] == "note")
    assert "between 0.48 leading and lagging" in notes and "page 12" in notes and "clause 4.2" in notes
    assert "Quote not found on its page (it is on page 13)" in notes


def test_the_confirm_card_refuses_a_limit_that_is_not_extracted(hub, monkeypatch):
    from harness import edit_card
    from services import campus_grid_code_service as gc

    monkeypatch.setattr(gc, "get_draft", lambda project, pid: _shaped(
        pid, profile={"q_range_demand": {**EXTRACTED["q_range_demand"], "source": "code"}}))
    with pytest.raises(HTTPException) as exc:
        edit_card.preview("campus_confirm_grid_code_limit",
                          {"project_id": "Chat Hub", "profile_id": "tso", "limit": "q_range_demand"})
    assert exc.value.status_code == 422


def test_the_publish_card_names_what_it_replaces_and_each_unconfirmed_limit(hub, monkeypatch):
    from harness import edit_card
    from services import campus_grid_code_service as gc

    monkeypatch.setattr(gc, "get_draft", lambda project, pid: _shaped(pid))
    monkeypatch.setattr(gc, "get_published", lambda project, pid: {**_shaped(pid), "yaml": "old: 1\n",
                                                                   "profile": {"title": "Old code"}})
    card = edit_card.preview("campus_publish_grid_code", {"project_id": "Chat Hub", "profile_id": "tso",
                                                           "allow_unconfirmed": True})
    (row,) = [r for r in card["rows"] if r["role"] == "typed"]
    assert (row["old"], row["new"]) == ("Old code", "TSO code")
    notes = " ".join(r["note"] for r in card["rows"] if r["role"] == "note")
    assert "Replaces the published profile" in notes and "q_range_demand is unconfirmed: it stays extracted" in notes
    assert set(card["binding"]) == {"draft_sha256", "published_sha256"}


def test_the_grid_code_actions_are_carded_destructive_and_reach_their_service(hub, monkeypatch):
    from harness import edit_card
    from services import campus_grid_code_service as gc

    calls = []
    for fn in ("delete_document", "new_draft", "get_draft", "delete_draft", "confirm", "publish",
               "get_published", "delete_published"):
        _spy(monkeypatch, gc, fn, calls, result=_shaped())
    for tool in ("campus_confirm_grid_code_limit", "campus_publish_grid_code", "campus_delete_grid_code_draft",
                 "campus_delete_published_grid_code", "campus_delete_grid_code_document"):
        assert tool in edit_card.CARDED_ACTIONS and catalogue.safety_tier_for(tool) == "destructive", tool
    chat_tools.campus_new_grid_code_draft("Chat Hub", "tso", title="T", overwrite=True)
    chat_tools.campus_confirm_grid_code_limit("Chat Hub", "tso", "q_range_demand")
    chat_tools.campus_publish_grid_code("Chat Hub", "tso", allow_unconfirmed=True)
    chat_tools.campus_delete_grid_code_draft("Chat Hub", "tso")
    chat_tools.campus_delete_published_grid_code("Chat Hub", "tso")
    chat_tools.campus_delete_grid_code_document("Chat Hub", "d" * 64)
    out = chat_tools.campus_get_grid_code_draft("Chat Hub", "tso")
    from harness.campus_cards import sha256_text

    assert out["sha256"] == sha256_text(_shaped()["yaml"])
    got = [(n, a[1:]) for n, a, _k in calls]
    assert got == [("new_draft", ("tso", "T", True)), ("confirm", ("tso", "q_range_demand")),
                   ("publish", ("tso", True)), ("delete_draft", ("tso",)), ("delete_published", ("tso",)),
                   ("delete_document", ("d" * 64,)), ("get_draft", ("tso",))]


def test_save_draft_refuses_a_stale_base(hub, monkeypatch):
    from services import campus_grid_code_service as gc

    monkeypatch.setattr(gc, "get_draft", lambda project, pid: _shaped(pid))
    monkeypatch.setattr(gc, "save_draft", lambda *a: pytest.fail("a stale write never reaches the service"))
    with pytest.raises(HTTPException) as exc:
        chat_tools.campus_save_grid_code_draft("Chat Hub", "tso", "title: x\n", base_sha256="0" * 64)
    assert exc.value.detail["error_kind"] == "file_changed_since_read"


def test_the_pdf_upload_reaches_the_service_with_its_media_type(uploads, monkeypatch):
    from services import campus_grid_code_service as gc

    calls = []
    _spy(monkeypatch, gc, "upload_document", calls)
    chat_tools.campus_upload_grid_code_document("Chat Hub", "p1", page_ranges="1-3")
    ((_, a, _k),) = calls
    assert a[1:] == (b"%PDF-1.7 grid code", "code.pdf", "application/pdf", "1-3")

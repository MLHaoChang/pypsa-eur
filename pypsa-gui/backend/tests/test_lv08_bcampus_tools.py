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


def test_oversized_gridspine_uploads_get_the_routes_shared_budget_refusal(uploads, monkeypatch):
    """GK B condition f: both gridspine uploads read through the route's one UploadBudget."""
    from routers import gridspine as gs_router
    from services import upload_guard

    monkeypatch.setattr(gs, "upload_external_dispatch", lambda *a: pytest.fail("never reached"))
    monkeypatch.setattr(gs, "upload_readback", lambda *a: pytest.fail("never reached"))
    monkeypatch.setattr(gs_router, "TABLE_MAX_BYTES", 10)  # each table fits alone, the pair does not
    with pytest.raises(HTTPException) as exc:
        chat_tools.gridspine_upload_external_dispatch("Chat Hub", "d1", "l1")
    assert exc.value.status_code == 413
    monkeypatch.setattr(upload_guard, "_DEFAULT_MAX_UPLOAD_BYTES", 4)
    with pytest.raises(HTTPException) as exc:
        chat_tools.gridspine_upload_readback("Chat Hub", 12, "b1")
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
    monkeypatch.setattr(ce, "check_campus_text", lambda text: __import__("yaml").safe_load(text))
    with pytest.raises(HTTPException) as exc:
        chat_tools.campus_save_campus("Chat Hub", "a: 1\nb: 3\n", base_sha256="0" * 64)
    assert exc.value.status_code == 409 and exc.value.detail["error_kind"] == "file_changed_since_read"
    base = campus_cards.sha256_text("a: 1\nb: 2\n")
    card = edit_card.preview("campus_save_campus", {"project_id": "Chat Hub", "yaml": "a: 1\nb: 3\n",
                                                    "base_sha256": base})
    row, sha = [r for r in card["rows"] if r["role"] == "typed"]
    assert (row["field"], row["old"], row["new"]) == ("b", 2, 3)
    assert (sha["old"], sha["new"]) == (base, campus_cards.sha256_text("a: 1\nb: 3\n"))
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
    row, sha = [r for r in card["rows"] if r["role"] == "typed"]
    assert (row["old"], row["new"]) == ("Old code", "TSO code")
    from harness.campus_cards import sha256_text

    assert (sha["old"], sha["new"]) == (sha256_text("old: 1\n"), sha256_text(_shaped()["yaml"]))
    assert card["says"] == "Publishes draft tso as grid code tso, replacing the published “Old code”."
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


# ── receipts, stale cards and refusals at preview (GK B plan-delta conditions a–e) ──


def _through_the_loop(tool, args, *, before_approval=None):
    """One chat call through the turn loop, approved: (error kind or None, the
    receipt the loop built, read where it is built: a long result spills)."""
    from harness import edit_card
    from services import chat_service

    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    errors, receipts, built = [], [], edit_card.receipt
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(edit_card, "receipt", lambda *a, **k: receipts.append(built(*a, **k)) or receipts[-1])
            for event, data in chat_service._dispatch_real_tool_call(
                    session, {"id": "tu", "name": tool, "input": {**P, **args}}, []):
                if event == "tool_pending_confirmation":
                    if before_approval is not None:
                        before_approval()
                    session.record_decision(data["confirmation_token"], "approve")
                elif event == "tool_error":
                    errors.append(data)
    finally:
        chat_tools.set_chat_session(None)
    if errors:
        return errors[0]["error_kind"], None
    (receipt,) = receipts
    return None, receipt


def _pdf() -> bytes:
    import pypdf

    w, buf = pypdf.PdfWriter(), io.BytesIO()
    w.add_blank_page(width=200, height=200)
    w.write(buf)
    return buf.getvalue()


@pytest.fixture
def codes(hub):
    """A draft `tso` whose first voltage band is extracted (confirmable), a
    published `tso`, and one stored document."""
    from services import campus_grid_code_service as gc

    doc = gc.upload_document(hub, _pdf(), "code.pdf", "application/pdf")
    gc.new_draft(hub, "tso", title="TSO code")
    gc.publish(hub, "tso")  # the published profile differs from the draft below
    profile = gc.get_draft(hub, "tso")["profile"]
    profile["document"] = {"sha256": doc["id"], "filename": "code.pdf", "title": "TSO code"}
    profile["voltage_bands"][0].update(source="extracted", page=1, quote="plus or minus ten percent")
    gc.save_draft(hub, "tso", __import__("yaml").safe_dump(profile))
    return {"project": hub, "document_id": doc["id"]}


def _draft_file(project, pid="tso"):
    from services import campus_grid_code_service as gc

    return gc._drafts(project) / f"{pid}.yaml"


def _published_file(project, pid="tso"):
    from services import campus_grid_code_service as gc

    return gc.codes_dir(project) / f"{pid}.yaml"


def _grid_code_calls(codes):
    from services import campus_grid_code_service as gc

    text = gc.get_draft(codes["project"], "tso")["yaml"]
    edited = text.replace("TSO code", "TSO code, edited")
    return {
        "campus_save_grid_code_draft": {"profile_id": "tso", "yaml": edited,
                                        "base_sha256": __import__("harness.campus_cards").campus_cards.sha256_text(text)},
        "campus_new_grid_code_draft": {"profile_id": "fresh", "title": "Fresh code"},
        "campus_confirm_grid_code_limit": {"profile_id": "tso", "limit": "voltage_bands[0]"},
        "campus_publish_grid_code": {"profile_id": "tso", "allow_unconfirmed": True},
        "campus_delete_grid_code_draft": {"profile_id": "tso"},
        "campus_delete_published_grid_code": {"profile_id": "tso"},
        "campus_delete_grid_code_document": {"document_id": codes["document_id"]},
    }


GRID_CODE_TOOLS = ["campus_save_grid_code_draft", "campus_new_grid_code_draft", "campus_confirm_grid_code_limit",
                   "campus_publish_grid_code", "campus_delete_grid_code_draft",
                   "campus_delete_published_grid_code", "campus_delete_grid_code_document"]


@pytest.mark.parametrize("tool", GRID_CODE_TOOLS)
def test_each_grid_code_receipt_is_verified_by_reading_the_effect_back(codes, tool):
    kind, receipt = _through_the_loop(tool, _grid_code_calls(codes)[tool])
    assert kind is None and receipt["outcome"] == "verified", [r for r in receipt["rows"] if r["role"] != "note"]
    assert [r for r in receipt["rows"] if r["role"] == "typed"]  # at least one row was read back
    assert receipt["undo"]["available"] is False  # no undo for these; the card says so


GRID_CODE_SERVICE = {"campus_save_grid_code_draft": "save_draft", "campus_new_grid_code_draft": "new_draft",
                     "campus_confirm_grid_code_limit": "confirm", "campus_publish_grid_code": "publish",
                     "campus_delete_grid_code_draft": "delete_draft",
                     "campus_delete_published_grid_code": "delete_published",
                     "campus_delete_grid_code_document": "delete_document"}


@pytest.mark.parametrize("tool", GRID_CODE_TOOLS)
def test_a_grid_code_call_whose_effect_is_missing_gets_an_uncertain_receipt(codes, monkeypatch, tool):
    from services import campus_grid_code_service as gc

    calls = _grid_code_calls(codes)
    monkeypatch.setattr(gc, GRID_CODE_SERVICE[tool], lambda *a, **k: {})  # answers, writes nothing
    kind, receipt = _through_the_loop(tool, calls[tool])
    assert kind is None and receipt["outcome"] == "uncertain"


def test_the_receipts_say_the_effect_in_words(codes):
    from harness import edit_card

    calls = _grid_code_calls(codes)
    says = {tool: edit_card.preview(tool, {**P, **calls[tool]}, with_state=False)["says"] for tool in GRID_CODE_TOOLS}
    assert says["campus_delete_grid_code_draft"] == ("Deletes the draft grid-code profile tso (“TSO code”); "
                                                     "this cannot be undone.")
    assert says["campus_delete_published_grid_code"].startswith("Deletes the published grid-code profile tso")
    assert says["campus_delete_grid_code_document"] == "Deletes the grid-code document code.pdf; this cannot be undone."
    assert says["campus_publish_grid_code"] == "Publishes draft tso as grid code tso, replacing the published “TSO code”."
    assert says["campus_confirm_grid_code_limit"] == ("Marks limit voltage_bands[0] of draft tso as confirmed against "
                                                      "its quoted page (source extracted → code).")
    assert "None" not in " ".join(says.values())


def _touch(path):
    return lambda: path.write_text(path.read_text() + "# touched\n")  # same values, other bytes


@pytest.mark.parametrize("tool, touched, service", [
    ("campus_save_grid_code_draft", _draft_file, "save_draft"),
    ("campus_confirm_grid_code_limit", _draft_file, "confirm"),
    ("campus_publish_grid_code", _draft_file, "publish"),
    ("campus_publish_grid_code", _published_file, "publish"),
    ("campus_delete_grid_code_draft", _draft_file, "delete_draft"),
    ("campus_delete_published_grid_code", _published_file, "delete_published"),
])
def test_a_file_moved_after_the_card_is_edit_preview_stale_and_nothing_is_written(codes, monkeypatch, tool,
                                                                                  touched, service):
    from services import campus_grid_code_service as gc

    calls = _grid_code_calls(codes)
    monkeypatch.setattr(gc, service, lambda *a, **k: pytest.fail("a stale card never reaches the service"))
    kind, _ = _through_the_loop(tool, calls[tool], before_approval=_touch(touched(codes["project"])))
    assert kind == "edit_preview_stale"


def test_a_campus_file_moved_after_the_card_is_edit_preview_stale(hub, monkeypatch):
    from harness.campus_cards import sha256_text

    f = ce.campus_dir(hub) / ce.CAMPUS_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("a: 1\n")
    monkeypatch.setattr(ce, "check_campus_text", lambda text: __import__("yaml").safe_load(text))
    monkeypatch.setattr(ce, "save_campus", lambda *a: pytest.fail("a stale card never reaches the service"))
    kind, _ = _through_the_loop("campus_save_campus", {"yaml": "a: 2\n", "base_sha256": sha256_text("a: 1\n")},
                                before_approval=_touch(f))
    assert kind == "edit_preview_stale"


def test_save_campus_receipt_reads_the_stored_file_back(hub, monkeypatch):
    from harness.campus_cards import sha256_text

    f = ce.campus_dir(hub) / ce.CAMPUS_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("a: 1\n")
    monkeypatch.setattr(ce, "check_campus_text", lambda text: __import__("yaml").safe_load(text))
    monkeypatch.setattr(ce, "save_campus", lambda project, text: f.write_text(text) and {"campus_yaml": text})
    kind, receipt = _through_the_loop("campus_save_campus", {"yaml": "a: 2\n", "base_sha256": sha256_text("a: 1\n")})
    assert kind is None and receipt["outcome"] == "verified"
    monkeypatch.setattr(ce, "save_campus", lambda project, text: {"campus_yaml": text})  # writes nothing
    kind, receipt = _through_the_loop("campus_save_campus", {"yaml": "a: 3\n", "base_sha256": sha256_text("a: 2\n")})
    assert kind is None and receipt["outcome"] == "uncertain"


@pytest.mark.parametrize("tool, args", [
    ("campus_save_grid_code_draft", {"profile_id": "tso", "yaml": "title: [unclosed\n"}),
    ("campus_save_campus", {"yaml": "campus: [unclosed\n"}),
])
def test_malformed_yaml_gets_the_handlers_refusal_and_no_card(codes, tool, args):
    from harness import campus_cards, edit_card

    current = (_draft_file(codes["project"]) if tool == "campus_save_grid_code_draft"
               else ce.campus_dir(codes["project"]) / ce.CAMPUS_FILE)
    text = current.read_text() if current.is_file() else None
    with pytest.raises(HTTPException) as exc:
        edit_card.preview(tool, {**P, **args, "base_sha256": campus_cards.sha256_text(text)})
    assert exc.value.status_code == 422 and "not valid YAML" in str(exc.value.detail)


def test_the_extract_card_says_the_document_goes_to_the_extraction_provider(codes):
    from harness import edit_card

    card = edit_card.preview("campus_extract_grid_code", {**P, "document_id": codes["document_id"]})
    notes = " ".join(r["note"] for r in card["rows"] if r["role"] == "note")
    assert "Sends the document to the configured extraction provider" in notes and "one API call" in notes
    (row,) = [r for r in card["rows"] if r["role"] == "typed"]
    assert (row["field"], row["new"]) == ("document", codes["document_id"])


def _fake_milp_start(record_settings=None):
    def start(project, settings):
        s = {**ce.DEFAULTS, **{k: v for k, v in settings.items() if v is not None or k == "pf"}}
        basis = ce.campus_dir(project) / ce.MILP_SUBDIR / ce.MILP_BASIS_FILE
        basis.parent.mkdir(parents=True, exist_ok=True)
        basis.write_text(__import__("json").dumps({"settings": {**s, **(record_settings or {})}, "inputs": {}}))
        ce._MILP_JOBS[ce._milp_key(project)] = {"state": "running", "started_at": 1.0, "project": project.name}
        return {"state": "running"}
    return start


def test_the_milp_receipt_reads_the_new_job_and_its_recorded_settings(hub, monkeypatch):
    monkeypatch.setattr(ce, "_MILP_JOBS", {})
    monkeypatch.setattr(ce, "start_milp_job", _fake_milp_start())
    kind, receipt = _through_the_loop("campus_start_milp", {"k": 4})
    assert kind is None and receipt["outcome"] == "verified", [r for r in receipt["rows"] if r["role"] != "note"]
    job = next(r for r in receipt["rows"] if r["field"] == "job")
    assert job["readback"] == "running"
    assert receipt["says"].startswith("Starts the campus joint optimisation in the background (k 4")


@pytest.mark.parametrize("fake", ["other_settings", "no_new_job"])
def test_a_milp_start_whose_job_or_settings_differ_is_uncertain(hub, monkeypatch, fake):
    monkeypatch.setattr(ce, "_MILP_JOBS", {})
    if fake == "other_settings":
        monkeypatch.setattr(ce, "start_milp_job", _fake_milp_start({"k": 9}))
    else:
        monkeypatch.setattr(ce, "start_milp_job", lambda project, settings: {"state": "running"})
    kind, receipt = _through_the_loop("campus_start_milp", {"k": 4})
    assert kind is None and receipt["outcome"] == "uncertain"


# ── CARDED_ACTIONS widens is_edit (GK B condition b) ────────────────────────


def test_no_carded_action_is_a_read_and_a_checked_turn_refuses_each():
    from types import SimpleNamespace

    from harness import assurance, edit_card

    turn = assurance.Turn(SimpleNamespace(owner_user_id="owner", usage_acc={}),
                          SimpleNamespace(project_uuid="00000000-0000-0000-0000-000000000000"))
    for tool in edit_card.CARDED_ACTIONS:
        assert catalogue.safety_tier_for(tool) != "read", tool
        assert edit_card.is_edit(tool) and tool in edit_card.ADAPTERS, tool
        assert tool not in assurance.CHECKED_TOOLS and turn.tool_guard(tool, {**P}) is False, tool

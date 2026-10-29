"""
Live-voice issue 08, R2: the last needs-tool GUI routes.

`get_process_view` reads the process view of the network the route reads (the
caller's foreground project) and refuses a foreground project the caller may
not read. `get_site_anchor` and the carded `set_site_anchor` serve
`GET/PUT /api/projects/{name}/site-anchor`; the write goes through
`anchor.write_anchor` only, after the route's ACL and lock checks, and its card
binds the stored revision, so an anchor moved after the card writes nothing.
The five Site 3D scene routes are held for SC-07 (`held-by-spec`), and route
parity fails on any needs-tool row (tests/test_assistant_route_parity.py).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from db.models import User
from harness import catalogue, edit_card
from routers import results as results_router
from services import chat_tools, project_registry
from services.pypsa_service import PyPSAService
from services.site_scene import anchor

TOOLS = {"get_process_view": "read", "get_site_anchor": "read", "set_site_anchor": "write"}
NEW = {"name": "Chat Site", "lat": 52.52, "lng": 13.405, "source": "manual", "confirmed": True,
       "expected_revision": 0}


@pytest.fixture
def site(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        project = project_registry.create_root(db, user, "Chat Site")
        directory = chat_tools._authorized_project("Chat Site").directory
        directory.mkdir(parents=True, exist_ok=True)  # a saved project's folder
        yield SimpleNamespace(uuid=str(project.id), directory=directory)


def test_the_tools_are_registered_with_tiers_matching_their_routes():
    names = {t["name"] for t in catalogue.TOOLS}
    for name, tier in TOOLS.items():
        assert name in names and name in chat_tools.DISPATCHERS, name
        assert catalogue.safety_tier_for(name) == tier, name
        assert all((m == "GET") == (tier == "read") for m, _ in catalogue.TOOL_ROUTES[name]), name
    assert edit_card.is_edit("set_site_anchor") and "set_site_anchor" in edit_card.ADAPTERS


# ── get_process_view ────────────────────────────────────────────────────────


def test_the_process_view_matches_a_hand_count(monkeypatch):
    from tests.test_process_results import MWH_PER_T, _hb_network

    n = _hb_network()
    monkeypatch.setattr(PyPSAService, "get_network", classmethod(lambda cls: n))
    monkeypatch.setattr(results_router, "_dispatch_ready", lambda net: True)
    out = chat_tools.get_process_view()
    assert out["available"] is True
    nh3 = next(p for p in out["products"] if p["bus"] == "nh3")
    # The load takes 30 MWh/h for six hours: 180 MWh, 180 / 5.166 t.
    assert nh3["delivery"]["mwh"] == pytest.approx(180.0)
    assert nh3["delivery"]["t"] == pytest.approx(180.0 / MWH_PER_T)
    (hb,) = [u for u in out["process_units"] if u["name"] == "hb"]
    assert hb is not None


def test_the_process_view_is_unavailable_before_a_solve(monkeypatch):
    monkeypatch.setattr(results_router, "_dispatch_ready", lambda net: False)
    assert chat_tools.get_process_view() == {"available": False, "products": [], "process_units": []}


def test_a_foreground_project_the_caller_may_not_read_is_refused(site, second_identity, monkeypatch):
    monkeypatch.setattr(PyPSAService, "get_active_context", classmethod(lambda cls: SimpleNamespace(project_uuid=site.uuid)))
    monkeypatch.setattr(results_router, "get_process_view", lambda: pytest.fail("read before the refusal"))
    chat_tools.set_acting_user(second_identity["user_id"])
    with pytest.raises(HTTPException) as exc:
        chat_tools.get_process_view()
    assert exc.value.status_code == 404 and exc.value.detail["error_kind"] == "not_authorized"


def test_the_owner_reads_the_process_view_of_their_foreground_project(site, monkeypatch):
    monkeypatch.setattr(PyPSAService, "get_active_context", classmethod(lambda cls: SimpleNamespace(project_uuid=site.uuid)))
    monkeypatch.setattr(results_router, "_dispatch_ready", lambda net: False)
    assert chat_tools.get_process_view()["available"] is False


# ── the site anchor ─────────────────────────────────────────────────────────


def test_get_site_anchor_reads_the_sidecar(site):
    assert chat_tools.get_site_anchor("Chat Site") == {"version": 1, "revision": 0, "anchor": None}
    anchor.write_anchor(site.directory, {"lat": 1.0, "lng": 2.0, "source": "geocode", "confirmed": False}, 0)
    out = chat_tools.get_site_anchor("Chat Site")
    assert out["revision"] == 1 and out["anchor"] == {"lat": 1.0, "lng": 2.0, "source": "geocode", "confirmed": False}


@pytest.mark.parametrize("tool, args", [("get_site_anchor", {}),
                                        ("set_site_anchor", {k: v for k, v in NEW.items() if k != "name"})])
def test_a_non_member_gets_the_routes_404(site, second_identity, tool, args):
    chat_tools.set_acting_user(second_identity["user_id"])
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS[tool](name="Chat Site", **args)
    assert exc.value.status_code == 404
    assert not (site.directory / anchor.ANCHOR_FILE).exists()


def test_the_card_shows_old_and_new_and_writes_nothing(site):
    card = edit_card.preview("set_site_anchor", NEW)
    rows = {r["field"]: (r["old"], r["new"]) for r in card["rows"] if r["role"] == "typed"}
    assert rows == {"lat": (None, 52.52), "lng": (None, 13.405), "source": (None, "manual"),
                    "confirmed": (None, True)}
    assert card["binding"]["revision"] == 0 and card["binding"]["expected_revision"] == 0
    assert "52.52, 13.405" in card["says"] and "confirmed" in card["says"]
    assert not (site.directory / anchor.ANCHOR_FILE).exists()


def test_confirm_writes_through_write_anchor_and_the_receipt_reads_the_stored_row(site, monkeypatch):
    card = edit_card.preview("set_site_anchor", NEW)
    edit_card.require_current("set_site_anchor", NEW, card)
    calls = []
    real = anchor.write_anchor
    monkeypatch.setattr(anchor, "write_anchor", lambda *a: calls.append(a) or real(*a))
    out = chat_tools.set_site_anchor(**NEW)
    assert len(calls) == 1 and out["revision"] == 1
    receipt = edit_card.receipt(card, NEW)
    assert [r["readback"] for r in receipt["rows"] if r["role"] == "typed"] == [52.52, 13.405, "manual", True]


def test_an_anchor_moved_after_the_card_is_stale_and_writes_nothing(site, monkeypatch):
    card = edit_card.preview("set_site_anchor", NEW)
    # Someone else writes the same values, then clears them again: only the revision moved.
    anchor.write_anchor(site.directory, {k: NEW[k] for k in ("lat", "lng", "source", "confirmed")}, 0)
    anchor.write_anchor(site.directory, None, 1)
    with pytest.raises(HTTPException) as exc:
        edit_card.require_current("set_site_anchor", NEW, card)
    assert exc.value.status_code == 409 and exc.value.detail["error_kind"] == "edit_preview_stale"
    assert anchor.read_anchor(site.directory) == {"version": 1, "revision": 2, "anchor": None}


def test_the_card_is_bound_to_the_revision_it_was_built_on(site):
    first = edit_card.preview("set_site_anchor", NEW)
    anchor.write_anchor(site.directory, {"lat": 1.0, "lng": 2.0, "source": "manual", "confirmed": True}, 0)
    anchor.write_anchor(site.directory, None, 1)
    # The same rows (no anchor → the same new one); only the stored revision differs.
    again = edit_card.preview("set_site_anchor", {**NEW, "expected_revision": 2})
    assert again["rows"] == first["rows"]
    assert again["digest"] != first["digest"]


def test_tampered_args_at_confirm_are_validated_by_the_write(site):
    with pytest.raises(HTTPException) as exc:
        chat_tools.set_site_anchor(**{**NEW, "lat": 95.0})
    assert exc.value.status_code == 422 and exc.value.detail["error_kind"] == "invalid_input"
    assert not (site.directory / anchor.ANCHOR_FILE).exists()


def test_a_stale_expected_revision_is_refused_before_any_card(site):
    anchor.write_anchor(site.directory, {"lat": 1.0, "lng": 2.0, "source": "manual", "confirmed": True}, 0)
    with pytest.raises(HTTPException) as exc:
        edit_card.preview("set_site_anchor", NEW)
    assert exc.value.status_code == 409 and exc.value.detail["error_kind"] == "file_changed_since_read"
    with pytest.raises(HTTPException) as exc:  # and the write's own compare-and-swap, past any card
        chat_tools.set_site_anchor(**NEW)
    assert exc.value.status_code == 409 and exc.value.detail["error_kind"] == "revision_conflict"


def test_swapped_coordinates_are_refused_at_preview_with_the_hint(site):
    with pytest.raises(HTTPException) as exc:
        edit_card.preview("set_site_anchor", {**NEW, "lat": 120.0, "lng": 45.0})
    assert exc.value.status_code == 422 and exc.value.detail["hint"] == "swapped"


def test_the_write_checks_the_projects_edit_lock_first(site, monkeypatch):
    from routers import projects

    def refuse(db, project, user):
        raise HTTPException(409, {"error_kind": "project_locked", "message": "locked"})

    monkeypatch.setattr(projects, "_check_project_lock", refuse)
    monkeypatch.setattr(anchor, "write_anchor", lambda *a: pytest.fail("written under another user's lock"))
    for call in (lambda: chat_tools.set_site_anchor(**NEW), lambda: edit_card.preview("set_site_anchor", NEW)):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 409

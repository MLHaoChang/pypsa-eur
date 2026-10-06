"""
P33b step 0 (owner decision D-5): an HTTP undo and a Saved-snapshot restore must
land in the context the NEXT request reads.

Inside a session-bound request `reset_network()` publishes the new, unbound
context into the session's scratch slot (`_publish_active` routes by the new
context's own identity). Undo and restore then put the binding back with
`set_binding` / `bind_project`, which stamp identity but never re-key — so the
project's `org:uuid` slot kept the PRE-undo context, and that is what
`resolve_for_session` handed the next request: the undo answered 200 and the
next `GET` still read the edited value.

Every assertion here reads back on a fresh request (or through `session_ctx`,
which is the same resolution the app performs), because the defect is
invisible to anything that inspects the request's own context.
"""
from __future__ import annotations

import contextlib

from services.pypsa_service import PyPSAService


def _bus(client):
    buses = client.get("/api/network/buses").json()
    assert buses, "the fixture network has no bus"
    return buses[0]


def _x(client, name):
    for b in client.get("/api/network/buses").json():
        if b["name"] == name:
            return float(b.get("x") or 0.0)
    raise AssertionError(f"bus {name!r} not found")


def _move_bus(client, bus, dx=2.5):
    body = {**bus, "x": float(bus.get("x") or 0.0) + dx}
    r = client.put(f"/api/network/buses/{bus['name']}", json=body)
    assert r.status_code == 200, r.text
    return body["x"]


def _ctxs_bound_to(name):
    with PyPSAService._registry_lock:
        return {
            k: c for k, c in PyPSAService._contexts.items()
            if c.loaded_project == name
        }


@contextlib.contextmanager
def _as_session(client, _auth_db):
    """Bind the client's session context the way the request middleware does
    (ctx, slot AND scratch), so an in-process chat dispatch routes exactly as a
    chat turn on that session does."""
    from services import active_project
    from services.auth_service import resolve_session_row
    from settings import get_settings

    _engine, session_local = _auth_db
    raw = client.cookies.get(get_settings().session_cookie_name)
    with session_local() as db:
        row = resolve_session_row(db, raw)
        ctx, slot = active_project.resolve_for_session(db, row)
        scratch = active_project.scratch_key(row)
    t1 = PyPSAService.bind_request_context(ctx)
    t2 = PyPSAService.bind_request_slot(slot)
    t3 = PyPSAService.bind_request_scratch(scratch)
    try:
        yield ctx
    finally:
        PyPSAService._request_scratch.reset(t3)
        PyPSAService._request_slot.reset(t2)
        PyPSAService.reset_request_context(t1)


def test_an_http_undo_is_read_by_the_next_request(
    client, api_project, registry_key_for, session_ctx
):
    name = api_project("undo-probe")
    key = registry_key_for(name)
    bus = _bus(client)
    x0 = float(bus.get("x") or 0.0)
    assert _move_bus(client, bus) == _x(client, bus["name"])

    r = client.post("/api/network/undo")
    assert r.status_code == 200, r.text

    assert _x(client, bus["name"]) == x0, (
        "the undo answered 200 but the next request still reads the edited "
        "value — the re-imported context was left in the session's scratch slot"
    )
    resolved = session_ctx(client)
    assert PyPSAService._contexts[key] is resolved
    assert list(_ctxs_bound_to(name)) == [key], (
        "more than one resident context is bound to the project after an undo"
    )


def test_a_chat_undo_is_read_by_the_next_request(
    client, api_project, registry_key_for, session_ctx, _auth_db, monkeypatch
):
    from services import chat_service
    from tests.test_chat_edits_are_captured import _dispatch

    # `undo_last` is destructive: approve it without a human round-trip.
    monkeypatch.setattr(chat_service, "AUTO_APPROVE_TIERS", frozenset({"destructive"}))

    name = api_project("chat-undo-probe")
    key = registry_key_for(name)
    bus = _bus(client)
    x0 = float(bus.get("x") or 0.0)
    _move_bus(client, bus)

    with _as_session(client, _auth_db):
        frames, _ = _dispatch("undo_last", {})
    assert any(ev == "tool_result" for ev, _p in frames), frames

    assert _x(client, bus["name"]) == x0
    assert PyPSAService._contexts[key] is session_ctx(client)
    assert list(_ctxs_bound_to(name)) == [key]


def test_a_saved_snapshot_restore_is_read_by_the_next_request(
    client, api_project, registry_key_for, session_ctx
):
    name = api_project("restore-probe")
    key = registry_key_for(name)
    bus = _bus(client)
    x0 = float(bus.get("x") or 0.0)
    r = client.post(f"/api/projects/{name}/snapshots", json={"label": "before"})
    assert r.status_code == 200, r.text
    snap_id = r.json()["id"]
    _move_bus(client, bus)

    r = client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore")
    assert r.status_code == 200, r.text

    assert _x(client, bus["name"]) == x0
    assert PyPSAService._contexts[key] is session_ctx(client)
    assert list(_ctxs_bound_to(name)) == [key]


def test_a_restore_of_another_resident_project_rekeys_under_that_project(
    client, api_project, registry_key_for, session_ctx, project_row, _auth_db
):
    a = api_project("rk-alpha")
    b = api_project("rk-beta")
    key_a, key_b = registry_key_for(a), registry_key_for(b)
    r = client.post(f"/api/projects/{b}/snapshots", json={"label": "b0"})
    assert r.status_code == 200, r.text
    snap_id = r.json()["id"]
    assert client.post(f"/api/projects/{a}/activate").status_code == 200
    ctx_a = PyPSAService._contexts[key_a]

    r = client.post(f"/api/projects/{b}/snapshots/{snap_id}/restore")
    assert r.status_code == 200, r.text

    resolved = session_ctx(client)
    assert resolved.loaded_project == b
    assert PyPSAService._contexts[key_b] is resolved
    assert PyPSAService._contexts[key_a] is ctx_a, "A's resident context moved"
    assert list(_ctxs_bound_to(b)) == [key_b]


def test_an_undo_on_an_unbound_draft_stays_in_scratch(client, session_ctx):
    assert client.post("/api/network/reset").status_code == 200
    r = client.post("/api/network/buses", json={"name": "draft-bus", "x": 1.0, "y": 2.0})
    assert r.status_code in (200, 201), r.text
    before_keys = set(PyPSAService._contexts)

    r = client.post("/api/network/undo")
    assert r.status_code == 200, r.text

    ctx = session_ctx(client)
    assert ctx.registry_key is None
    with PyPSAService._registry_lock:
        slots = [k for k, c in PyPSAService._contexts.items() if c is ctx]
    assert len(slots) == 1 and slots[0].startswith("scratch:"), slots
    assert set(PyPSAService._contexts) - before_keys == set(), (
        "an undo on an unbound draft registered a new non-scratch slot"
    )
    names = [b["name"] for b in client.get("/api/network/buses").json()]
    assert "draft-bus" not in names

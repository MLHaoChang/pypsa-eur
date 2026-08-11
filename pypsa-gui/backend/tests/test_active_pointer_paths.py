"""Every path that rebinds the caller's own active context also moves the
session's DB pointer.

`resolve_for_session` reads `sessions.active_project_id` before falling back to
the process context, so a path that moves only the context is reverted on the
next request. Background paths (the solve queue, which has no session) and
path-scoped reads (`resolve_project_context`) deliberately do NOT move the
pointer — the last test here pins that.

Route paths verified against the decorators rather than taken from the plan,
which named three of them wrongly: the import route is `/import_bundle` (not
`/import`), the template route is `/from_template/{id}` with an UNDERSCORE,
and its ids are `3bus|ieee14|belgium` — there is no `blank`.
"""
from __future__ import annotations


def _pointer(session_local, test_client) -> str | None:
    """The session's active_project_id, read the way session_ctx reads it."""
    from services.auth_service import resolve_session_row
    from settings import get_settings

    raw = test_client.cookies.get(get_settings().session_cookie_name)
    assert raw, "client has no session cookie"
    with session_local() as db:
        row = resolve_session_row(db, raw)
        return str(row.active_project_id) if row.active_project_id else None


def test_load_project_moves_the_pointer(client, api_project, project_row, _auth_db):
    _engine, session_local = _auth_db
    a = api_project("alpha")
    b = api_project("beta")
    client.post(f"/api/projects/{a}/activate")

    client.get(f"/api/projects/{b}")

    assert _pointer(session_local, client) == str(project_row(b).id), (
        "load_project rebinds the active context; the pointer must follow or "
        "the next request reverts the switch"
    )


def test_create_from_template_moves_the_pointer(client, api_project, _auth_db):
    _engine, session_local = _auth_db
    a = api_project("alpha")
    client.post(f"/api/projects/{a}/activate")
    before = _pointer(session_local, client)

    resp = client.post("/api/projects/from_template/3bus", params={"name": "fromtpl"})
    assert resp.status_code < 400, f"template create failed: {resp.status_code} {resp.text[:200]}"

    after = _pointer(session_local, client)
    assert after is not None and after != before, (
        "create_from_template binds the new Project as the active context; "
        "the pointer must follow"
    )


def test_import_bundle_moves_the_pointer(client, api_project, _auth_db):
    _engine, session_local = _auth_db
    a = api_project("alpha")
    b = api_project("beta")
    client.post(f"/api/projects/{a}/activate")
    before = _pointer(session_local, client)

    bundle = client.get(f"/api/projects/{b}/bundle").content
    resp = client.post(
        "/api/projects/import_bundle",
        files={"file": ("beta.zip", bundle, "application/zip")},
        params={"name": "imported"},
    )
    assert resp.status_code < 400, f"import failed: {resp.status_code} {resp.text[:200]}"

    after = _pointer(session_local, client)
    assert after is not None and after != before, (
        "import_bundle binds the imported Project as the active context; "
        "the pointer must follow"
    )


def test_path_scoped_read_does_not_move_the_pointer(client, api_project, _auth_db):
    _engine, session_local = _auth_db
    a = api_project("alpha")
    b = api_project("beta")
    client.post(f"/api/projects/{a}/activate")
    before = _pointer(session_local, client)

    client.get(f"/api/projects/{b}/snapshots")

    assert _pointer(session_local, client) == before, (
        "reading another Project's data must not switch the session to it"
    )

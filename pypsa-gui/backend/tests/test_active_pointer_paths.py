"""Every path that rebinds the caller's own active context also moves the
session's DB pointer.

`resolve_for_session` reads `sessions.active_project_id` before falling back to
the process context, so a path that moves only the context is reverted on the
next request. Background paths (the solve queue, which has no session) and
path-scoped reads (`resolve_project_context`) deliberately do NOT move the
pointer — the last test here pins that.
"""
from __future__ import annotations

import pytest


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


def test_create_from_template_moves_the_pointer(client, api_project, _auth_db, tmp_path, monkeypatch):
    # NOTE: the brief's route used `/from-template/blank`. The real decorator is
    # `@router.post("/from_template/{template_id}")` (underscore) and `"blank"`
    # is not a registered template id (`_TEMPLATE_DEFAULT_NAMES` only has
    # "3bus"/"ieee14"/"belgium"). Corrected the URL to the real one below.
    #
    # The real "3bus" template.nc is a gitignored build artifact
    # (`project_templates/3bus/network.nc`) produced only by manually running
    # `project_templates/_build.py`, which does a genuine LOPF solve. Nothing in
    # `gui-tests`, conftest, or CI builds it — a fresh checkout does not have
    # it, and the endpoint would 404 ("network.nc is missing") before ever
    # reaching the code this test exists to cover. So this test fakes the
    # template instead of depending on that artifact: `create_from_template`
    # only copies `_PROJECT_TEMPLATES_DIR/<id>/network.nc` and netcdf-imports
    # it — it never solves it — so a minimal unsolved network, written to a
    # tmp dir that `_PROJECT_TEMPLATES_DIR` is monkeypatched to, is a faithful
    # stand-in for everything the endpoint actually touches. This keeps the
    # pointer assertion executing on every checkout instead of skipping.
    import pandas as pd
    import pypsa
    from routers import projects as projects_router

    template_dir = tmp_path / "3bus"
    template_dir.mkdir()
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=2, freq="h"))
    n.add("Bus", "B1")
    n.export_to_netcdf(str(template_dir / "network.nc"))
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)

    _engine, session_local = _auth_db
    a = api_project("alpha")
    client.post(f"/api/projects/{a}/activate")
    before = _pointer(session_local, client)

    resp = client.post("/api/projects/from_template/3bus", params={"name": "fromtpl"})
    assert resp.status_code == 200, resp.text

    after = _pointer(session_local, client)
    assert after is not None and after != before, (
        "create_from_template binds the new Project as the active context; "
        "the pointer must follow"
    )


def test_import_bundle_moves_the_pointer(client, api_project, _auth_db, tmp_path):
    # NOTE: the brief's route was `/api/projects/import`. The real decorator is
    # `@router.post("/import_bundle")`.
    _engine, session_local = _auth_db
    a = api_project("alpha")
    b = api_project("beta")
    client.post(f"/api/projects/{a}/activate")
    before = _pointer(session_local, client)

    bundle = client.get(f"/api/projects/{b}/bundle").content
    client.post(
        "/api/projects/import_bundle",
        files={"file": ("beta.zip", bundle, "application/zip")},
        params={"name": "imported"},
    )

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

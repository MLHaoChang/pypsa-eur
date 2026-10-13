"""
OPEN-ITEMS 13: re-loading the open project must leave memory and disk agreeing.

`GET /api/projects/{name}` reads the project off disk. Its UI callers on the
SAME project (`Sidebar.handleOpenPick` for `name === currentProject`, the
mismatch banner's "Reload") first ask before discarding unsaved edits: a
reload is "discard and re-read". But the route ended by registering the fresh
context under the project's key, which displaced the resident one — the copy
holding the unsaved edit — and `register` writes a displaced context back to
disk. So memory held the disk copy (x = 0) while disk now held the discarded
edit (x = 1): the next save overwrote the edit, a restart brought it back.

Both halves are checked: memory right after the reload, and disk after the
resident copy is dropped and the project is re-activated cold.
"""
from __future__ import annotations

from services.pypsa_service import PyPSAService
from tests.test_local_mode_api import local_client  # noqa: F401 — fixture
from tests.test_project_switch_isolation import _drop_resident, _move, _xs


def _reload_discards_the_edit(c, name):
    saved = _xs(c)
    _move(c, 1.0)
    assert _xs(c) != saved
    assert c.get("/api/network/undo/info").json()["unsaved"] is True

    r = c.get(f"/api/projects/{name}")
    assert r.status_code == 200, r.text

    assert _xs(c) == saved, "the reload did not re-read the saved copy"
    info = c.get("/api/network/undo/info").json()
    assert info["depth"] == 0 and info["unsaved"] is False

    _drop_resident(name)
    assert c.post(f"/api/projects/{name}/activate").status_code == 200
    assert _xs(c) == saved, (
        "disk holds the edit the reload discarded: the displaced resident "
        "context was written back over the project's files"
    )


def test_reload_of_the_open_project_does_not_write_the_discarded_edit(
        client, api_project):
    name = api_project("reload-same")
    assert client.post(f"/api/projects/{name}/activate").status_code == 200
    _reload_discards_the_edit(client, name)


def test_local_mode_reload_of_the_open_project_does_not_write_the_discarded_edit(
        local_client):
    c = local_client
    r = c.post("/api/projects/from_template/eh_h2_hub", params={"name": "RL"})
    assert r.status_code == 200, r.text
    _reload_discards_the_edit(c, "RL")


def test_a_displacing_register_still_writes_back(client, api_project, monkeypatch):
    """The write-back itself stays: any OTHER registration that displaces a
    resident context still persists it, so unsaved work on a context that
    merely stops being reachable survives. Only the reload opts out."""
    from services.project_context import ProjectContext

    name = api_project("reload-wb")
    assert client.post(f"/api/projects/{name}/activate").status_code == 200
    key = next(k for k, cc in PyPSAService._contexts.items() if cc.loaded_project == name)
    prior = PyPSAService._contexts[key]
    saved = []
    monkeypatch.setattr(PyPSAService, "_save_evicted_ctx",
                        staticmethod(lambda k, cc: saved.append(cc)))
    import pypsa
    PyPSAService.register(key, ProjectContext(network=pypsa.Network()))
    assert saved == [prior]


def test_loading_another_resident_project_keeps_its_unsaved_edit(client, api_project):
    """The clone wizard's `load(src)` from another project: src is resident
    with an unsaved edit nobody chose to discard. The load persists it first,
    so memory and disk agree on the EDITED copy rather than on neither."""
    a = api_project("reload-other-a")
    b = api_project("reload-other-b")
    assert client.post(f"/api/projects/{a}/activate").status_code == 200
    _move(client, 1.0)
    edited = _xs(client)
    assert client.post(f"/api/projects/{b}/activate").status_code == 200

    assert client.get(f"/api/projects/{a}").status_code == 200
    assert _xs(client) == edited, "the load dropped A's unsaved edit"

    _drop_resident(a)
    assert client.post(f"/api/projects/{a}/activate").status_code == 200
    assert _xs(client) == edited, "memory and disk disagree after the load"

"""
Restoring a Saved snapshot must not lose the work it replaces (SR-1, F01).

The restore route takes a "before-restore" Saved snapshot first, and the UI and
the chat tool promise that the current state is kept there. The snapshot copies
files on disk, so an edit that was never saved used to be dropped: the restore
then reloaded the old files and cleared the undo stack. These tests pin that the
safety snapshot holds the live state, edits included.
"""
import pypsa


def _snapshot(client, name, label="original"):
    r = client.post(f"/api/projects/{name}/snapshots", json={"label": label})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _restore(client, name, sid):
    r = client.post(f"/api/projects/{name}/snapshots/{sid}/restore")
    assert r.status_code == 200, r.text
    return r.json()


def _safety_network(client, name, project_storage_dir):
    rows = client.get(f"/api/projects/{name}/snapshots").json()
    safety = next(s for s in rows if s["label"] == "before-restore")
    path = project_storage_dir(name) / "snapshots" / safety["id"] / "network.nc"
    return pypsa.Network(str(path))


def _edit_first_bus_x(client):
    bus = client.get("/api/network/buses").json()[0]
    changed_x = float(bus.get("x") or 0) + 25.0
    r = client.put(f"/api/network/buses/{bus['name']}", json={**bus, "x": changed_x})
    assert r.status_code == 200, r.text
    live = client.get("/api/network/buses").json()
    assert next(b for b in live if b["name"] == bus["name"])["x"] == changed_x
    return bus["name"], changed_x


def test_the_safety_snapshot_keeps_an_unsaved_edit(client, api_project, project_storage_dir):
    name = api_project("restore-unsaved")
    sid = _snapshot(client, name)
    bus, changed_x = _edit_first_bus_x(client)

    _restore(client, name, sid)

    captured = _safety_network(client, name, project_storage_dir)
    assert float(captured.buses.loc[bus, "x"]) == changed_x, (
        "the before-restore Saved snapshot did not keep the unsaved edit")


def test_restore_still_brings_back_the_snapshot_state(client, api_project):
    """Saving first must not leak the edit into the restored state."""
    name = api_project("restore-unsaved-result")
    original = {b["name"]: b["x"] for b in client.get("/api/network/buses").json()}
    sid = _snapshot(client, name)
    bus, _ = _edit_first_bus_x(client)

    _restore(client, name, sid)

    live = {b["name"]: b["x"] for b in client.get("/api/network/buses").json()}
    assert live[bus] == original[bus]


def _project_bytes(d):
    return {p.relative_to(d): p.read_bytes() for p in sorted(d.rglob("*"))
            if p.is_file() and "snapshots" not in p.relative_to(d).parts}


def _labels(client, name):
    return [s["label"] for s in client.get(f"/api/projects/{name}/snapshots").json()]


def test_a_refused_save_refuses_the_restore_and_changes_nothing(
        client, api_project, project_storage_dir, monkeypatch):
    """A save refusal (solver in flight, study running, identity) must refuse
    the restore before any file moves or any safety snapshot is taken."""
    from fastapi import HTTPException
    from routers import projects

    name = api_project("restore-save-refused")
    sid = _snapshot(client, name)
    bus, changed_x = _edit_first_bus_x(client)
    d = project_storage_dir(name)
    before = _project_bytes(d)

    def _refuse(*_a, **_k):
        raise HTTPException(409, detail={"error_kind": "solver_in_flight", "message": "busy"})
    monkeypatch.setattr(projects, "_save_context", _refuse)

    r = client.post(f"/api/projects/{name}/snapshots/{sid}/restore")
    assert r.status_code == 409, r.text
    assert _project_bytes(d) == before
    assert "before-restore" not in _labels(client, name)
    live = client.get("/api/network/buses").json()
    assert next(b for b in live if b["name"] == bus)["x"] == changed_x


def test_an_edit_during_the_restore_refuses_it(
        client, api_project, project_storage_dir, monkeypatch):
    """An edit landing after the save but before the restore's lock is in
    neither the safety snapshot nor the restored state: refuse instead."""
    from routers import snapshots
    from services import dirty_state

    name = api_project("restore-edit-during")
    sid = _snapshot(client, name)
    bus, changed_x = _edit_first_bus_x(client)
    original_create = snapshots._create_snapshot_internal

    def _create_then_edit(*a, **k):
        info = original_create(*a, **k)
        dirty_state.bump_revision()
        return info
    monkeypatch.setattr(snapshots, "_create_snapshot_internal", _create_then_edit)

    r = client.post(f"/api/projects/{name}/snapshots/{sid}/restore")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error_kind"] == "changed_during_restore"
    live = client.get("/api/network/buses").json()
    assert next(b for b in live if b["name"] == bus)["x"] == changed_x


def test_restoring_a_project_that_is_not_open_saves_nothing(
        client, api_project, project_storage_dir):
    """Only the project being restored is saved: another open project's
    unsaved edits are not written to its disk."""
    other = api_project("restore-other")
    target = api_project("restore-target")
    sid = _snapshot(client, target)
    r = client.get(f"/api/projects/{other}")
    assert r.status_code == 200, r.text
    _edit_first_bus_x(client)
    other_dir = project_storage_dir(other)
    before = _project_bytes(other_dir)

    _restore(client, target, sid)

    assert _project_bytes(other_dir) == before

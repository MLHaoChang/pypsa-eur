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

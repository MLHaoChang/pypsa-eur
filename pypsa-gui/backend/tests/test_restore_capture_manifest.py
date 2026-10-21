"""
Restoring a Saved snapshot brings back what was there, and only that (SR-1).

F02: a sidecar written after the snapshot (the stress-scenario registry here)
survived restoring it, because restore skipped every file the snapshot lacked.
F03: the same for a bundle folder (`uploads/` here) created after the snapshot.

A snapshot now records which bundle files and folders were present when it was
taken (`capture_manifest.json`). Restore removes a tracked one that was absent;
the before-restore Saved snapshot keeps it. A snapshot without the manifest
(taken before it existed) keeps the old leave-in-place behaviour.
"""
import json


def _snapshot(client, name, label="original"):
    r = client.post(f"/api/projects/{name}/snapshots", json={"label": label})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _restore(client, name, sid):
    r = client.post(f"/api/projects/{name}/snapshots/{sid}/restore")
    assert r.status_code == 200, r.text


def _add_stress_scenario(client, name):
    r = client.put(f"/api/projects/{name}/stress_scenarios", json={"scenarios": [{
        "id": "later", "name": "Added after the snapshot", "kind": "parametric",
        "frequency_per_year": 2.0, "electrical_load_multiplier": 1.2}]})
    assert r.status_code == 200, r.text


def _safety_dir(client, name, project_storage_dir):
    rows = client.get(f"/api/projects/{name}/snapshots").json()
    safety = next(s for s in rows if s["label"] == "before-restore")
    return project_storage_dir(name) / "snapshots" / safety["id"]


def test_a_sidecar_absent_at_capture_is_removed_on_restore(
        client, api_project, project_storage_dir):
    name = api_project("restore-new-sidecar")
    d = project_storage_dir(name)
    before = client.get(f"/api/projects/{name}/stress_scenarios").json()["scenarios"]
    assert not (d / "adequacy_stress_scenarios.json").exists()
    sid = _snapshot(client, name)
    _add_stress_scenario(client, name)

    _restore(client, name, sid)

    assert not (d / "adequacy_stress_scenarios.json").exists()
    after = client.get(f"/api/projects/{name}/stress_scenarios").json()["scenarios"]
    assert after == before, "a setting added after the snapshot survived restoring it"
    # The before-restore Saved snapshot keeps what the restore removed.
    assert (_safety_dir(client, name, project_storage_dir)
            / "adequacy_stress_scenarios.json").exists()


def test_a_folder_absent_at_capture_is_removed_on_restore(
        client, api_project, project_storage_dir):
    name = api_project("restore-new-folder")
    d = project_storage_dir(name)
    assert not (d / "uploads").exists()
    sid = _snapshot(client, name)
    later = d / "uploads" / "later.txt"
    later.parent.mkdir()
    later.write_text("created after the snapshot")

    _restore(client, name, sid)

    assert not (d / "uploads").exists(), "an upload made after the snapshot survived restoring it"
    assert (_safety_dir(client, name, project_storage_dir) / "uploads" / "later.txt").exists()


def test_the_snapshot_lists_report_the_manifest(client, api_project):
    name = api_project("restore-manifest-flag")
    sid = _snapshot(client, name)
    rows = client.get(f"/api/projects/{name}/snapshots").json()
    assert next(s for s in rows if s["id"] == sid)["has_capture_manifest"] is True


def test_a_snapshot_without_a_manifest_leaves_later_files_in_place(
        client, api_project, project_storage_dir):
    """A snapshot taken before the manifest existed cannot tell absent from
    untracked, so restore keeps the old behaviour and the list says so."""
    name = api_project("restore-legacy")
    d = project_storage_dir(name)
    sid = _snapshot(client, name)
    (d / "snapshots" / sid / "capture_manifest.json").unlink()
    _add_stress_scenario(client, name)
    later = d / "uploads" / "later.txt"
    later.parent.mkdir()
    later.write_text("created after the snapshot")

    rows = client.get(f"/api/projects/{name}/snapshots").json()
    assert next(s for s in rows if s["id"] == sid)["has_capture_manifest"] is False

    _restore(client, name, sid)

    assert (d / "adequacy_stress_scenarios.json").exists()
    assert later.exists()


def test_a_name_tracked_after_capture_is_left_alone(
        client, api_project, project_storage_dir):
    """A bundle name the manifest never tracked (added to the bundle lists by a
    later release) is not 'absent at capture'; restore leaves it in place."""
    name = api_project("restore-untracked")
    d = project_storage_dir(name)
    sid = _snapshot(client, name)
    manifest_path = d / "snapshots" / sid / "capture_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["tracked_files"].remove("adequacy_stress_scenarios.json")
    manifest["tracked_dirs"].remove("uploads")
    manifest_path.write_text(json.dumps(manifest))
    _add_stress_scenario(client, name)
    later = d / "uploads" / "later.txt"
    later.parent.mkdir()
    later.write_text("created after the snapshot")

    _restore(client, name, sid)

    assert (d / "adequacy_stress_scenarios.json").exists()
    assert later.exists()

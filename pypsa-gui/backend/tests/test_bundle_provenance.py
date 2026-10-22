"""
A project's provenance travels with it, and history is never rolled back (SR-1, F10).

`sensitivity_case.json` (a sensitivity case's record of its baseline and
changes) was in no bundle list, so an export, a copy or a snapshot dropped it.
It is now a bundle file, but a scenario fork does not inherit it: a fork of a
case is not that case.

`run_evidence/` (append-only run records) is HISTORY, not working state: bundle
export/import and Save-As carry it, but snapshots do not, so restoring an older
snapshot never deletes runs recorded after it. `experiments/` stays out of every
list: its plans name other case projects' ids.
"""
import io
import json
import zipfile

from routers import projects
from services import run_evidence, workflow_tools
from services.study import forks

CASE = workflow_tools.SENSITIVITY_CASE_FILE
CASE_BODY = json.dumps({"baseline_id": "b", "case": {"name": "c"}}, sort_keys=True)


def _write_case_and_evidence(d):
    (d / CASE).write_text(CASE_BODY)
    run = d / "run_evidence" / "run-1"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text('{"run_id": "run-1"}')


def test_the_lists_name_the_writers_files():
    assert CASE in projects._BUNDLE_FILES
    assert CASE in projects._FORK_SKIPPED_FILES
    assert CASE in forks.SKIPPED
    assert projects._HISTORY_DIRS == ("run_evidence",)
    assert run_evidence._evidence_path(
        projects.PROJECTS_DIR / "x", "r").parent.name == projects._HISTORY_DIRS[0]
    assert not set(projects._HISTORY_DIRS) & set(projects._BUNDLE_DIRS)
    assert "experiments" not in projects._BUNDLE_DIRS + projects._HISTORY_DIRS


def test_a_bundle_round_trip_carries_the_case_record_and_run_evidence(
        client, api_project, project_storage_dir):
    name = api_project("prov-export")
    _write_case_and_evidence(project_storage_dir(name))
    r = client.get(f"/api/projects/{name}/bundle")
    assert r.status_code == 200
    members = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert {CASE, "run_evidence/run-1/manifest.json"} <= members, members

    r = client.post("/api/projects/import_bundle?name=prov_imported",
                    files={"file": ("p.pypsaproj.zip", r.content, "application/zip")})
    assert r.status_code in (200, 201), r.text
    imported = r.json().get("imported") or r.json().get("name") or "prov_imported"
    d = project_storage_dir(imported)
    assert (d / CASE).read_text() == CASE_BODY
    assert (d / "run_evidence" / "run-1" / "manifest.json").exists()


def test_a_snapshot_restores_the_case_record_but_never_rolls_back_evidence(
        client, api_project, project_storage_dir):
    name = api_project("prov-snapshot")
    d = project_storage_dir(name)
    _write_case_and_evidence(d)
    r = client.post(f"/api/projects/{name}/snapshots", json={"label": "v1"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert (d / "snapshots" / sid / CASE).exists()
    assert not (d / "snapshots" / sid / "run_evidence").exists()

    (d / CASE).unlink()
    later = d / "run_evidence" / "run-2"
    later.mkdir()
    (later / "manifest.json").write_text('{"run_id": "run-2"}')
    r = client.post(f"/api/projects/{name}/snapshots/{sid}/restore")
    assert r.status_code == 200, r.text

    assert (d / CASE).read_text() == CASE_BODY
    assert (later / "manifest.json").exists(), "a restore erased run evidence"
    assert (d / "run_evidence" / "run-1" / "manifest.json").exists()


def test_a_scenario_fork_inherits_neither(client, api_project, project_storage_dir):
    name = api_project("prov-parent")
    _write_case_and_evidence(project_storage_dir(name))
    r = client.post(f"/api/projects/{name}/scenarios",
                    json={"name": "prov-parent_child", "description": "x"})
    assert r.status_code in (200, 201), r.text
    child = project_storage_dir(r.json().get("name") or "prov-parent_child")
    assert (child / "network.nc").exists()
    assert not (child / CASE).exists()
    assert not (child / "run_evidence").exists()

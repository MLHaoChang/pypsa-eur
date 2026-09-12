"""
The FMEA worksheet and the stress-scenario registry travel with the project.

Whole-branch review (2026-09-08), finding S7. `adequacy_worksheet.json`
(user-authored expert rows + overlays) and `adequacy_stress_scenarios.json`
are per-project sidecars the adequacy routes write beside `network.nc`. They
were not in `_BUNDLE_FILES`, so a bundle export/import, a project snapshot
and a scenario fork all silently dropped them: the shared bundle arrived
with an empty worksheet and no stress scenarios, and a snapshot restore
could not bring them back — while `layout.json`, pure presentation state,
had been added to that tuple for exactly this reason.

Every loop over the tuple tolerates an absent file, so an older bundle or
snapshot without the sidecars still imports; that is pinned here too.

Bite (verified): drop the two names from `_BUNDLE_FILES` — the bundle zip
lacks both, the snapshot directory lacks both, the fork's worksheet is
empty, and the restore test cannot bring the rows back.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from routers.projects import _BUNDLE_FILES
from services.adequacy import stress as _stress
from services.adequacy import worksheet as _worksheet

WORKSHEET = {
    "manual_rows": [{
        "mode_id": "manual:1", "component_class": "Generator", "name": "expert row",
        "failure_class": "D", "occurrence_per_year": 1.0, "occurrence_basis": "expert",
        "severity_eur": 5.0, "criticality_eur_per_year": 5.0, "in_metric_scope": False,
        "engine": "expert", "fidelity": "expert_judgement",
    }],
    "overlays": {"generator:g1:forced_outage": {"mitigability": "hi"}},
}
STRESS = {"scenarios": [{
    "id": "s1", "name": "cold snap", "kind": "parametric",
    "frequency_per_year": 2.0, "electrical_load_multiplier": 1.2,
}]}


def _author(client, name):
    r = client.put(f"/api/projects/{name}/worksheet", json=WORKSHEET)
    assert r.status_code == 200, r.text
    r = client.put(f"/api/projects/{name}/stress_scenarios", json=STRESS)
    assert r.status_code == 200, r.text


def _rows(client, name):
    ws = client.get(f"/api/projects/{name}/worksheet").json()
    st = client.get(f"/api/projects/{name}/stress_scenarios").json()
    return ([r["mode_id"] for r in ws.get("manual_rows", [])],
            [s["id"] for s in (st.get("scenarios") or [])])


def test_the_tuple_names_the_services_sidecars():
    """The literals in `_BUNDLE_FILES` are the names the services write."""
    assert _worksheet.SIDECAR_NAME in _BUNDLE_FILES
    assert _stress.SIDECAR_NAME in _BUNDLE_FILES
    assert _worksheet.SIDECAR_NAME == "adequacy_worksheet.json"
    assert _stress.SIDECAR_NAME == "adequacy_stress_scenarios.json"


def test_every_adequacy_sidecar_is_in_the_bundle():
    """
    Finding S7, generalised after its THIRD recurrence.

    Naming the sidecars by hand is what let each new one ship outside the
    bundle: the test passed because it only knew about the ones that already
    worked. This walks `services/adequacy/` instead, so a module that declares
    a SIDECAR_NAME is in the bundle or this fails — including the next one
    nobody has written yet.
    """
    import importlib
    import pkgutil

    import services.adequacy as adequacy_pkg

    declared = {}
    for module in pkgutil.iter_modules(adequacy_pkg.__path__):
        mod = importlib.import_module(f"services.adequacy.{module.name}")
        name = getattr(mod, "SIDECAR_NAME", None)
        if name:
            declared[module.name] = name

    assert declared, "no sidecars found — has the package moved?"
    missing = {m: n for m, n in declared.items() if n not in _BUNDLE_FILES}
    assert not missing, (
        f"sidecar(s) not carried by _BUNDLE_FILES: {missing}. A bundle "
        f"export/import, a snapshot restore and a scenario fork all drop "
        f"them silently."
    )


def test_bundle_export_carries_both_and_import_restores_them(
        client, api_project, project_storage_dir):
    """★ The zip lists both sidecars; importing it under a new name gives a
    project whose worksheet and stress registry read back byte-equal."""
    name = api_project("sidecars")
    _author(client, name)
    r = client.get(f"/api/projects/{name}/bundle")
    assert r.status_code == 200
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert {"adequacy_worksheet.json", "adequacy_stress_scenarios.json"} <= names, names

    r = client.post("/api/projects/import_bundle?name=sidecars_imported",
                    files={"file": ("sidecars.pypsaproj.zip", r.content,
                                    "application/zip")})
    assert r.status_code in (200, 201), r.text
    imported = r.json().get("imported") or r.json().get("name") or "sidecars_imported"
    assert _rows(client, imported) == (["manual:1"], ["s1"])


def test_an_older_bundle_without_the_sidecars_still_imports(
        client, api_project, project_storage_dir):
    """The import loop tolerates absent members: a bundle exported before
    this change imports, with an empty worksheet rather than an error."""
    name = api_project("plain")
    r = client.get(f"/api/projects/{name}/bundle")
    assert r.status_code == 200
    src = zipfile.ZipFile(io.BytesIO(r.content))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for m in src.namelist():
            if m not in ("adequacy_worksheet.json", "adequacy_stress_scenarios.json"):
                zf.writestr(m, src.read(m))
    r = client.post("/api/projects/import_bundle?name=plain_imported",
                    files={"file": ("old.pypsaproj.zip", buf.getvalue(),
                                    "application/zip")})
    assert r.status_code in (200, 201), r.text
    imported = r.json().get("imported") or "plain_imported"
    assert _rows(client, imported) == ([], [])


def test_snapshot_carries_both_and_restore_brings_them_back(
        client, api_project, project_storage_dir):
    """★ A snapshot copies both; clearing the live sidecars and restoring
    the snapshot puts the rows back."""
    name = api_project("snapme")
    _author(client, name)
    r = client.post(f"/api/projects/{name}/snapshots", json={"label": "v1"})
    assert r.status_code in (200, 201), r.text
    snap_id = r.json()["id"]
    d = project_storage_dir(name)
    snap_dirs = [p for p in (d / "snapshots").iterdir() if p.is_dir()] \
        if (d / "snapshots").exists() else []
    assert snap_dirs, sorted(p.name for p in d.iterdir())
    snap_files = {p.name for p in snap_dirs[-1].iterdir()}
    assert {"adequacy_worksheet.json", "adequacy_stress_scenarios.json"} <= snap_files, snap_files

    # Clear the live sidecars, then restore.
    assert client.put(f"/api/projects/{name}/worksheet",
                      json={"manual_rows": [], "overlays": {}}).status_code == 200
    assert client.put(f"/api/projects/{name}/stress_scenarios",
                      json={"scenarios": []}).status_code == 200
    assert _rows(client, name) == ([], [])
    r = client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore")
    assert r.status_code == 200, r.text
    assert _rows(client, name) == (["manual:1"], ["s1"])


def test_a_scenario_fork_inherits_both(client, api_project, project_storage_dir):
    """★ The child scenario starts from the parent's worksheet and stress
    registry, as it starts from the parent's network and layout."""
    name = api_project("parent")
    _author(client, name)
    r = client.post(f"/api/projects/{name}/scenarios",
                    json={"name": "parent_child", "description": "x"})
    assert r.status_code in (200, 201), r.text
    child = r.json().get("name") or "parent_child"
    assert _rows(client, child) == (["manual:1"], ["s1"])
    # …and they are copies, not links: editing the child leaves the parent.
    assert client.put(f"/api/projects/{child}/worksheet",
                      json={"manual_rows": [], "overlays": {}}).status_code == 200
    assert _rows(client, name)[0] == ["manual:1"]

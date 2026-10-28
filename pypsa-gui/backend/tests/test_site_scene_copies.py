"""
SC-01a, D03 copy closure (V59/V60): a scene-enabled Site travels as its
pointer plus the one pointed generation, through every real copy path:
snapshot and restore, scenario, Save-As, bundle export and import. Orphan and
superseded generations stay behind; a missing pointed generation fails before
the destination changes; a pointer that is not a plain generation id is never
followed. Also project delete.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from services import site_service as ss

SITE = {"id": "campus", "name": "Campus", "buses": [], "origin": {"lng": 11.58, "lat": 48.14},
        "boundary": [[11.579, 48.139], [11.581, 48.139], [11.581, 48.141]], "placements": {}}
BOX = {"id": "hall", "center": [0, 0], "width": 10, "depth": 20, "height": 8, "heading": 0, "unit": "m"}
ORPHAN = "g000009-0123456789ab"


def _scene_files(project_dir) -> set[str]:
    root = project_dir / ss.SITES_DIR / "campus" / "scene"
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def enabled(client, api_project, project_storage_dir):
    """A project whose Site is adopted (revision 1) and then generated (revision 2), plus an orphan."""
    from services.site_scene import store

    name = api_project("base")
    project = project_storage_dir(name)
    assert client.put(f"/api/projects/{name}/sites", json={"version": 1, "sites": [SITE]}).status_code == 200
    digest = store.legacy_site_digest(ss.read_sites(project), "campus")
    assert client.post(f"/api/projects/{name}/sites/campus/scene/adopt",
                       json={"expected_digest": digest}).status_code == 200
    pid = client.post(f"/api/projects/{name}/sites/campus/scene/proposals",
                      json={"action": "generate_native", "base_revision": 1,
                            "inputs": {"greenfield": [BOX]}}).json()["id"]
    assert client.post(f"/api/projects/{name}/sites/campus/scene/proposals/{pid}/apply",
                       json={"expected_revision": 1}).status_code == 200
    orphan = project / ss.SITES_DIR / "campus" / "scene" / "generations" / ORPHAN
    orphan.mkdir(parents=True)
    (orphan / "generation.json").write_text("{}")
    (project / ss.SITES_DIR / "campus" / "context.json").write_text(json.dumps({"version": 1, "buildings": []}))
    pointer = json.loads((project / ss.SITES_DIR / "campus" / "scene" / "current.json").read_text())
    return name, project, pointer


def _closure(pointer) -> set[str]:
    return {"current.json", f"generations/{pointer['generation']}/generation.json"}


def _same_scene(client, a, b):
    sa, sb = (client.get(f"/api/projects/{n}/sites/campus/scene").json() for n in (a, b))
    assert sb["state"] == "enabled"
    assert {k: sa[k] for k in ("revision", "generation", "buildings", "site")} == \
        {k: sb[k] for k in ("revision", "generation", "buildings", "site")}


def test_the_source_has_history_and_an_orphan(enabled):
    _name, project, pointer = enabled
    files = _scene_files(project)
    assert len(files) == 4 and f"generations/{ORPHAN}/generation.json" in files and _closure(pointer) < files


def test_scenario_copies_the_closure_only(client, enabled, project_storage_dir):
    name, _project, pointer = enabled
    r = client.post(f"/api/projects/{name}/scenarios", json={"name": "child", "description": "x"})
    assert r.status_code in (200, 201), r.text
    child = project_storage_dir("child")
    assert _scene_files(child) == _closure(pointer)
    assert (child / ss.SITES_DIR / "campus" / "context.json").exists()
    _same_scene(client, name, "child")
    # The copy is editable on its own: its receipt chain ends at the copied generation.
    pid = client.post("/api/projects/child/sites/campus/scene/proposals",
                      json={"action": "generate_native", "base_revision": 2,
                            "inputs": {"greenfield": [{**BOX, "id": "shed"}]}}).json()["id"]
    r = client.post(f"/api/projects/child/sites/campus/scene/proposals/{pid}/apply", json={"expected_revision": 2})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/projects/{name}/sites/campus/scene").json()["revision"] == 2


def test_save_as_copies_the_closure_only(client, enabled, project_storage_dir):
    name, _project, pointer = enabled
    r = client.post("/api/projects/copy", params={"rebind": "true"})
    assert r.status_code == 200, r.text
    assert _scene_files(project_storage_dir("copy")) == _closure(pointer)
    _same_scene(client, name, "copy")


def test_snapshot_and_restore_carry_the_closure_only(client, enabled):
    name, project, pointer = enabled
    r = client.post(f"/api/projects/{name}/snapshots", json={"label": "v1"})
    assert r.status_code in (200, 201), r.text
    snap_id = r.json()["id"]
    pid = client.post(f"/api/projects/{name}/sites/campus/scene/proposals",
                      json={"action": "generate_native", "base_revision": 2,
                            "inputs": {"greenfield": [{**BOX, "id": "shed"}]}}).json()["id"]
    assert client.post(f"/api/projects/{name}/sites/campus/scene/proposals/{pid}/apply",
                       json={"expected_revision": 2}).status_code == 200
    assert client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore").status_code == 200
    assert _scene_files(project) == _closure(pointer)
    scene = client.get(f"/api/projects/{name}/sites/campus/scene").json()
    assert (scene["revision"], [b["id"] for b in scene["buildings"]]) == (2, ["greenfield:hall"])


def test_export_carries_the_closure_and_import_round_trips(client, enabled, project_storage_dir):
    name, _project, pointer = enabled
    r = client.get(f"/api/projects/{name}/bundle")
    assert r.status_code == 200
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    scene = {n.removeprefix("sites/campus/scene/") for n in names if n.startswith("sites/campus/scene/")}
    assert scene == _closure(pointer)
    assert "sites/campus/context.json" in names
    r = client.post("/api/projects/import_bundle?name=imported",
                    files={"file": ("x.pypsaproj.zip", r.content, "application/zip")})
    assert r.status_code in (200, 201), r.text
    imported = r.json().get("imported") or r.json().get("name") or "imported"
    _same_scene(client, name, imported)


def test_a_missing_pointed_generation_fails_before_the_copy(client, enabled, project_storage_dir):
    name, project, pointer = enabled
    (project / ss.SITES_DIR / "campus" / "scene" / "generations" / pointer["generation"] / "generation.json").unlink()
    r = client.get(f"/api/projects/{name}/sites/campus/scene")
    assert (r.status_code, r.json()["detail"]["error_kind"]) == (500, "integrity_error")
    from routers.projects import _copy_bundle_dirs
    from services.site_scene.contract import SceneError
    dst = project.parent / "copy_target"
    (dst / "sites").mkdir(parents=True)
    (dst / "sites" / "marker").write_text("kept")
    with pytest.raises(SceneError):
        _copy_bundle_dirs(project, dst)
    assert (dst / "sites" / "marker").read_text() == "kept"


@pytest.mark.parametrize("generation", ["../x", "/etc", "g000001-0123456789ab/../../x", ORPHAN + "/"])
def test_an_imported_pointer_that_is_not_a_plain_id_is_never_followed(client, enabled, project_storage_dir,
                                                                      generation):
    name, _project, pointer = enabled
    bundle = client.get(f"/api/projects/{name}/bundle").content
    src, out = zipfile.ZipFile(io.BytesIO(bundle)), io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for item in src.namelist():
            data = src.read(item)
            if item == "sites/campus/scene/current.json":
                data = json.dumps({**pointer, "generation": generation}).encode()
            zf.writestr(item, data)
    r = client.post("/api/projects/import_bundle?name=hostile",
                    files={"file": ("x.pypsaproj.zip", out.getvalue(), "application/zip")})
    assert r.status_code in (200, 201), r.text
    hostile = r.json().get("imported") or r.json().get("name") or "hostile"
    r = client.get(f"/api/projects/{hostile}/sites/campus/scene")
    assert (r.status_code, r.json()["detail"]["error_kind"]) == (500, "integrity_error")
    r = client.get(f"/api/projects/{hostile}/bundle")
    assert r.status_code == 500


def test_deleting_the_project_removes_its_scene(client, enabled, project_storage_dir):
    name, project, _pointer = enabled
    r = client.delete(f"/api/projects/{name}")
    assert r.status_code in (200, 204), r.text
    assert not (project / ss.SITES_DIR / "campus" / "scene").exists()

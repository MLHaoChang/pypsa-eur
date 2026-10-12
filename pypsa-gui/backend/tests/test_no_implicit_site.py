"""
No site is ever created implicitly (visual-layers plan 3, S4; owner decision
O3 of 2026-10-06: the 3D site is generated on demand from the assets, never
at project or template creation).

Every project template and every route that brings a project into being —
save a new project, create from a template, branch a scenario, import a
bundle — must leave `sites.json` absent (or empty) and `sites/` absent (or
empty). Expected green today; this is the guard that keeps it so.
"""
from __future__ import annotations

import io
import zipfile

import pytest

import project_templates._build as classic
import project_templates.eh_templates as eh
from routers import projects as projects_router
from routers.projects import _PROJECT_TEMPLATES_DIR, _TEMPLATE_DEFAULT_NAMES
from services import site_service as ss

_CLASSIC_BUILDERS = {
    "3bus": classic.build_3bus,
    "ieee14": classic.build_ieee14,
    "belgium": classic.build_belgium,
    "ieee39": classic.build_ieee39,
}


def _assert_no_site(pdir) -> None:
    assert pdir.exists(), pdir
    assert ss.read_sites(pdir)["sites"] == []
    sites_dir = pdir / ss.SITES_DIR
    assert not sites_dir.exists() or not any(sites_dir.iterdir()), list(sites_dir.iterdir())


def test_every_template_id_has_a_builder_under_test():
    """A template added to the registry must be covered here too."""
    assert set(_TEMPLATE_DEFAULT_NAMES) == set(_CLASSIC_BUILDERS) | set(eh.BUILDERS)


def test_shipped_template_dirs_carry_no_sites_sidecar():
    for tid in _TEMPLATE_DEFAULT_NAMES:
        tdir = _PROJECT_TEMPLATES_DIR / tid
        if not tdir.is_dir():
            continue
        assert not (tdir / ss.SITES_FILE).exists(), tid
        assert not (tdir / ss.SITES_DIR).exists(), tid


def test_eh_sidecar_writer_writes_no_sites(tmp_path):
    for tid in eh.BUILDERS:
        out = tmp_path / tid
        out.mkdir()
        eh.write_sidecars(out, tid)
        assert not (out / ss.SITES_FILE).exists(), tid


@pytest.mark.parametrize("tid", sorted(_TEMPLATE_DEFAULT_NAMES))
def test_create_from_template_creates_no_site(client, project_storage_dir, tmp_path, monkeypatch, tid):
    builder = _CLASSIC_BUILDERS.get(tid) or eh.BUILDERS[tid]
    n = builder()
    if n is None:
        pytest.skip(f"template {tid} needs source data that is not checked in")
    src = tmp_path / tid
    src.mkdir()
    n.export_to_netcdf(str(src / "network.nc"))
    if tid in eh.BUILDERS:
        eh.write_sidecars(src, tid)
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)

    r = client.post(f"/api/projects/from_template/{tid}", params={"name": f"t_{tid}"})
    assert r.status_code == 200, r.text
    name = r.json().get("name", f"t_{tid}")
    assert client.get(f"/api/projects/{name}/sites").json() == {"version": 1, "sites": []}
    _assert_no_site(project_storage_dir(name))


def test_saving_a_new_project_creates_no_site(client, api_project, project_storage_dir):
    name = api_project("fresh")
    _assert_no_site(project_storage_dir(name))


def test_save_as_creates_no_site(client, api_project, project_storage_dir):
    api_project("orig")
    r = client.post("/api/projects/orig_copy", params={"force": True})
    assert r.status_code == 200, r.text
    _assert_no_site(project_storage_dir("orig_copy"))


def test_scenario_creates_no_site(client, api_project, project_storage_dir):
    name = api_project("base_nosite")
    r = client.post(f"/api/projects/{name}/scenarios", json={"name": "child_nosite", "description": "x"})
    assert r.status_code in (200, 201), r.text
    child = r.json().get("name") or "child_nosite"
    _assert_no_site(project_storage_dir(child))


def test_bundle_import_creates_no_site(client, api_project, project_storage_dir):
    name = api_project("exp_nosite")
    r = client.get(f"/api/projects/{name}/bundle")
    assert r.status_code == 200
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert ss.SITES_FILE not in names
    assert not any(n.startswith(f"{ss.SITES_DIR}/") for n in names)
    r = client.post("/api/projects/import_bundle?name=imp_nosite",
                    files={"file": ("exp.pypsaproj.zip", r.content, "application/zip")})
    assert r.status_code in (200, 201), r.text
    imported = r.json().get("imported") or r.json().get("name") or "imp_nosite"
    _assert_no_site(project_storage_dir(imported))

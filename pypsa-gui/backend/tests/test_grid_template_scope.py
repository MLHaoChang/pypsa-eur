"""A project created from a grid template reads the grid scope preset
(study harness 51) through the real creation route.

The grid templates ship no ``eh_template.json``, so the preset reads the
template id the creation records in ``metadata.json``; the marker, and with it
``has_template``, stays absent.
"""

from __future__ import annotations

import json

import pytest

import project_templates._build as classic
import project_templates.eh_templates as eh
from routers import projects as projects_router
from services import flow_scope, run_profiles, template_marker


def _create(client, tmp_path, monkeypatch, tid: str) -> str:
    src = tmp_path / tid
    src.mkdir()
    builder = {"3bus": classic.build_3bus, "ieee14": classic.build_ieee14}.get(
        tid
    ) or eh.BUILDERS[tid]
    builder().export_to_netcdf(str(src / "network.nc"))
    if tid in eh.BUILDERS:
        eh.write_sidecars(src, tid)
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": f"t_{tid}"})
    assert r.status_code == 200, r.text
    return r.json().get("name", f"t_{tid}")


@pytest.mark.parametrize("tid", ["3bus", "ieee14"])
def test_a_grid_template_project_reads_the_grid_preset(
    client, project_storage_dir, tmp_path, monkeypatch, tid
):
    name = _create(client, tmp_path, monkeypatch, tid)
    pdir = project_storage_dir(name)
    assert not template_marker.present(pdir)  # no EH behaviour switched on
    assert (
        client.get("/api/simulation/solver_config").json()["run_ac_pf_after_lopf"]
        is True
    )
    assert (
        json.loads((pdir / "solver_config.json").read_text())["run_ac_pf_after_lopf"]
        is True
    )

    body = client.get(f"/api/projects/{name}/flow/scope").json()
    assert body["preset_id"] == tid
    assert (
        body["preset_label"] == {"3bus": "3-Bus Tutorial", "ieee14": "IEEE 14-Bus"}[tid]
    )
    assert body["scope"]["preset_id"] == tid
    assert "grid" in body["scope"]["groups"]
    assert "load_flow" in body["scope"]["options"]["grid"]
    reliability = next(c for c in body["cards"] if c["id"] == "reliability")
    assert all(
        o["status"] != flow_scope.AVAILABLE for o in reliability["options"]
    )  # has_template False

    scope = run_profiles.scope_for(flow_scope.preset_template(pdir))
    assert scope["preset"] == tid
    assert scope["cards"]["grid"] == ["load_flow"]
    assert "reliability" in scope["skipped"]


def test_an_eh_template_project_is_unchanged(
    client, project_storage_dir, tmp_path, monkeypatch
):
    name = _create(client, tmp_path, monkeypatch, "eh_microgrid")
    pdir = project_storage_dir(name)
    assert template_marker.present(pdir)
    assert (
        client.get(f"/api/projects/{name}/flow/scope").json()["preset_id"]
        == "eh_microgrid"
    )
    assert flow_scope.preset_template(pdir)["id"] == "eh_microgrid"
    assert (
        client.get("/api/simulation/solver_config").json()["run_ac_pf_after_lopf"]
        is False
    )


def test_a_project_with_no_recorded_template_reads_blank(
    client, project_storage_dir, tmp_path, monkeypatch
):
    name = _create(client, tmp_path, monkeypatch, "ieee14")
    pdir = project_storage_dir(name)
    meta = json.loads((pdir / "metadata.json").read_text())
    meta.pop("template_id")
    (pdir / "metadata.json").write_text(json.dumps(meta))
    assert flow_scope.preset_template(pdir) is None
    assert client.get(f"/api/projects/{name}/flow/scope").json()["preset_id"] == "blank"
    assert run_profiles.scope_for(flow_scope.preset_template(pdir))["preset"] == "blank"


def test_a_save_keeps_the_recorded_template(
    client, project_storage_dir, tmp_path, monkeypatch
):
    name = _create(client, tmp_path, monkeypatch, "3bus")
    assert client.post(f"/api/projects/{name}").status_code == 200
    meta = json.loads((project_storage_dir(name) / "metadata.json").read_text())
    assert meta["template_id"] == "3bus"
    assert client.get(f"/api/projects/{name}/flow/scope").json()["preset_id"] == "3bus"

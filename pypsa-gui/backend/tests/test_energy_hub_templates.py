"""
P19 — Energy Hub project templates (plan 2026-09-26).

Each template must be usable straight away: feasible, tagged, carrying the
outage data its archetype needs, and arriving in a new project with its
stress registry, solver settings (VOLL) and study metadata.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = BACKEND / "project_templates"
sys.path.insert(0, str(TPL_DIR))
import eh_templates as T  # noqa: E402

IDS = sorted(T.BUILDERS)


@pytest.mark.parametrize("tid", IDS)
def test_template_is_feasible_tagged_and_mttr_safe(tid):
    n = T.BUILDERS[tid]()
    probe = n.copy()
    status, cond = probe.optimize(solver_name="highs")
    assert (status, cond) == ("ok", "optimal")
    assert abs(float(n.snapshot_weightings["generators"].sum()) - 8760.0) < 1e-6
    assert n.buses["eh_poc"].dtype == bool and n.buses["eh_poc"].any()
    assert n.buses["eh_critical"].any()
    assert (n.links["eh_role"] == "grid_import").sum() >= 1
    for df in (n.generators, n.links):
        if "mttr_hours" in df.columns:
            assert float(df["mttr_hours"].max(skipna=True)) <= T.HOURS


@pytest.mark.parametrize("tid", IDS)
def test_readiness_predicts_every_default_stage_runs(tid):
    from services.adequacy.eh_readiness import eh_readiness
    from services.adequacy.eh_study_runner import (
        _PACK_FACTORY,
        apply_pack_overrides,
    )
    meta = T.TEMPLATE_META[tid]
    pack = apply_pack_overrides(_PACK_FACTORY[meta["recommended_archetype"]](),
                                meta["pack_overrides"])
    ready = eh_readiness(T.BUILDERS[tid](), pack, budget_solves=30,
                         voll=T.SOLVER_CONFIG["voll"],
                         dtc_attribution=meta["dtc_attribution"])
    requested = [r for r in ready["stages"] if r["prediction"] != "not_requested"]
    assert requested and all(r["prediction"] == "run" for r in requested), requested
    assert ready["mc_boundary"]["ok"] is True
    assert ready["warnings"] == []


@pytest.mark.parametrize("tid", IDS)
def test_committed_sidecars_match_the_builder(tid, tmp_path):
    """The committed JSON is what _build.py writes (no drift)."""
    T.write_sidecars(tmp_path, tid)
    for f in T.SIDECAR_FILES:
        assert (TPL_DIR / tid / f).read_text() == (tmp_path / f).read_text(), f


def test_sidecar_schema_and_registry_are_valid():
    from services.adequacy import stress
    assert T.SCHEMA == stress.SCHEMA
    for tid in IDS:
        stress._validate(T.STRESS_SCENARIOS[tid])
        meta = T.TEMPLATE_META[tid]
        assert meta["id"] == tid and meta["provenance"]


def test_every_template_is_registered_and_listed():
    from routers.projects import _TEMPLATE_DEFAULT_NAMES
    wizard = (BACKEND.parent / "frontend" / "src" / "layout"
              / "NewProjectWizard.tsx").read_text()
    for tid in IDS:
        assert _TEMPLATE_DEFAULT_NAMES[tid] == T.TEMPLATE_META[tid]["name"]
        assert f"id: '{tid}'" in wizard


def test_from_template_copies_sidecars_and_solver_settings(
        client, tmp_path, monkeypatch, tmp_projects_dir):
    from routers import projects as projects_router
    tid = "eh_datacenter"
    src = tmp_path / tid
    src.mkdir()
    T.BUILDERS[tid]().export_to_netcdf(str(src / "network.nc"))
    T.write_sidecars(src, tid)
    (src / "stray.json").write_text("{}")          # not allow-listed
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)

    r = client.post(f"/api/projects/from_template/{tid}", params={"name": "dc1"})
    assert r.status_code == 200, r.text
    name = r.json().get("name", "dc1")
    meta = client.get(f"/api/projects/{name}/eh_template")
    assert meta.status_code == 200 and meta.json()["recommended_archetype"] == \
        "weak_flexible"
    reg = client.get(f"/api/projects/{name}/stress_scenarios").json()
    assert [s["id"] for s in reg["scenarios"]] == ["heatwave", "dunkelflaute"]
    cfg = client.get("/api/simulation/solver_config").json()
    assert cfg["voll"] == 5000.0
    # untagged / non-EH projects answer 204
    r2 = client.post("/api/projects/from_template/eh_h2_hub",
                     params={"name": "h2x"})
    assert r2.status_code == 404                     # no network.nc staged


def test_chat_names_every_template():
    from routers.projects import _TEMPLATE_DEFAULT_NAMES
    from services.chat_tools_schema import TOOLS
    tool = next(t for t in TOOLS if t["name"] == "create_project_from_template")
    enum = tool["input_schema"]["properties"]["template_id"]["enum"]
    assert set(enum) == set(_TEMPLATE_DEFAULT_NAMES)


def test_eh_templates_build_on_demand_when_network_nc_is_missing(
        client, tmp_path, monkeypatch, tmp_projects_dir):
    """network.nc is a gitignored build artifact (only build-macos.sh makes
    it). The EH templates are pure builders, so a fresh checkout / dev start
    must still create them instead of 404-ing."""
    import shutil

    from routers import projects as projects_router
    shutil.copy(TPL_DIR / "eh_templates.py", tmp_path / "eh_templates.py")
    src = tmp_path / "eh_microgrid"
    src.mkdir()
    T.write_sidecars(src, "eh_microgrid")               # sidecars, no .nc
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)
    r = client.post("/api/projects/from_template/eh_microgrid",
                    params={"name": "mg_fresh"})
    assert r.status_code == 200, r.text
    links = {r["name"]: r for r in client.get("/api/network/links").json()}
    assert links["subsea_tie"]["eh_role"] == "grid_import"
    buses = {r["name"]: r for r in client.get("/api/network/buses").json()}
    assert buses["hospital"]["eh_critical"] is True
    name = r.json().get("name", "mg_fresh")
    assert client.get(f"/api/projects/{name}/eh_template").status_code == 200
    # a non-EH template still needs its prebuilt network.nc
    assert client.post("/api/projects/from_template/3bus",
                       params={"name": "x"}).status_code == 404

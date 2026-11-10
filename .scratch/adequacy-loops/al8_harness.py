# AL-8 harness (adequacy loops 01). Run: cp .scratch/adequacy-loops/al8_harness.py pypsa-gui/backend/tests/test_zz_al8_scratch.py && cd pypsa-gui/backend && AL8_OUT=<file.jsonl> python3 -m pytest -q tests/test_zz_al8_scratch.py  (one JSON row per EH template)
import dataclasses, json, os, pathlib, time
import pytest

TIDS = ["eh_h2_hub", "eh_h2_hub_staged", "eh_datacenter", "eh_datacenter_staged",
        "eh_microgrid", "eh_microgrid_staged", "eh_ammonia_staged", "eh_ipp_hybrid",
        "eh_concentrator_staged"]
OUT = os.environ["AL8_OUT"]


@pytest.mark.parametrize("tid", TIDS)
def test_al8(client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx, tid):
    from project_templates import eh_templates as T
    from routers import projects as projects_router
    src = tmp_path / tid
    src.mkdir()
    T.BUILDERS[tid]().export_to_netcdf(str(src / "network.nc"))
    T.write_sidecars(src, tid)
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)
    (tmp_path / "eh_templates.py").write_text(pathlib.Path(T.__file__).read_text())
    for extra in ("eh_staged_hubs.py", "eh_ammonia.py", "eh_concentrator.py", "eh_ipp.py", "eh_site.py"):
        p = pathlib.Path(T.__file__).parent / extra
        if p.exists():
            (tmp_path / extra).write_text(p.read_text())
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": f"al8_{tid}"})
    assert r.status_code == 200, r.text
    ctx = session_ctx(client)
    cfg = ctx.solver_state.get("solver_config")
    ctx.solver_state["solver_config"] = dataclasses.replace(cfg, target_lole_h=2.0)
    t0 = time.time()
    r = client.post("/api/results/coupling_loop", json={"max_solves": 3, "restore": "base"})
    row = {"t": tid}
    if r.status_code != 200:
        row.update(start=r.status_code, detail=str(r.json().get("detail"))[:160])
    else:
        body = None
        while time.time() - t0 < 900:
            g = client.get("/api/results/coupling_loop")
            body = g.json()
            if body.get("status") != "running":
                break
            time.sleep(0.5)
        fin = body.get("final") or {}
        its = body.get("iterations") or []
        last = (its[-1] if its else {}) or {}
        mc = fin.get("mc") or last.get("mc") or {}
        row.update(status=body.get("status"), solves_used=body.get("solves_used"),
                   target=body.get("target_lole_h"), nyears=body.get("horizon_years"),
                   lole_h=mc.get("lole_hours"), fleet_notes=body.get("fleet_notes"),
                   error=body.get("error"), secs=round(time.time() - t0))
    with open(OUT, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")

# 03 harness: the MC study (POST /results/mc, with the ELCC portfolio) per EH template. Run: cp .scratch/adequacy-loops/mc03_harness.py pypsa-gui/backend/tests/test_zz_mc03_scratch.py && cd pypsa-gui/backend && AL8_OUT=<file.jsonl> python3 -m pytest -q tests/test_zz_al8_scratch.py  (one JSON row per EH template; AL8_WAIT=<seconds> to wait longer than 900 s for a loop)
# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
import dataclasses
import json
import os
import pathlib
import time

import pytest

TIDS = [
    "eh_h2_hub",
    "eh_h2_hub_staged",
    "eh_datacenter",
    "eh_datacenter_staged",
    "eh_microgrid",
    "eh_microgrid_staged",
    "eh_ammonia_staged",
    "eh_ipp_hybrid",
    "eh_concentrator_staged",
]
OUT = os.environ["AL8_OUT"]


@pytest.mark.parametrize("tid", TIDS)
def test_mc03(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx, tid
):
    from project_templates import eh_templates as T
    from routers import projects as projects_router

    src = tmp_path / tid
    src.mkdir()
    T.BUILDERS[tid]().export_to_netcdf(str(src / "network.nc"))
    T.write_sidecars(src, tid)
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)
    (tmp_path / "eh_templates.py").write_text(pathlib.Path(T.__file__).read_text())
    for extra in (
        "eh_staged_hubs.py",
        "eh_ammonia.py",
        "eh_concentrator.py",
        "eh_ipp.py",
        "eh_site.py",
    ):
        p = pathlib.Path(T.__file__).parent / extra
        if p.exists():
            (tmp_path / extra).write_text(p.read_text())
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": f"al8_{tid}"})
    assert r.status_code == 200, r.text
    ctx = session_ctx(client)
    cfg = ctx.solver_state.get("solver_config")
    ctx.solver_state["solver_config"] = dataclasses.replace(cfg, target_lole_h=2.0)
    t0 = time.time()
    r = client.post(
        "/api/results/mc", json={"draws": 500, "seed": 0, "elcc_portfolio": True}
    )
    row = {"t": tid}
    if r.status_code not in (200, 202):
        row.update(start=r.status_code, detail=str(r.json().get("detail"))[:200])
    else:
        body = None
        while time.time() - t0 < float(os.environ.get("AL8_WAIT", 900)):
            body = client.get("/api/results/mc").json()
            if body.get("status") != "running":
                break
            time.sleep(0.5)
        res = body.get("result") or {}
        m = res.get("metrics") or {}
        pf = res.get("elcc_portfolio") or {}
        row.update(
            status=body.get("status"),
            lole_h=m.get("lole_hours"),
            eue=m.get("eue_mwh"),
            draws=m.get("n_samples"),
            basis=m.get("time_basis"),
            nyears=m.get("horizon_years"),
            units=[u.get("name") for u in (res.get("units_provenance") or [])],
            fleet_notes=m.get("fleet_notes"),
            portfolio_status=pf.get("status"),
            portfolio=[
                (x.get("kind"), x.get("name"))
                for x in ((pf.get("population") or {}).get("members") or [])
            ],
            portfolio_reason=str(pf.get("reason"))[:160],
            error=body.get("error"),
            secs=round(time.time() - t0),
        )
    with open(OUT, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")

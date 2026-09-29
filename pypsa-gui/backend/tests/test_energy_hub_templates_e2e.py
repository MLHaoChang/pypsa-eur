"""
P20 — every Energy Hub feature on every EH template, through the real HTTP
API (no stubbed driver): project from template → readiness → full-pipeline
study with the template's recommended settings → report + JSON export →
Class-C FMEA sweep with the template's stress registry.
"""
from __future__ import annotations

import pathlib
import sys
import time

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

ALL_STAGES = ["apply_pack", "ens_solve", "frontier", "mc_certify", "fmea_top",
              "redundancy", "levers", "dtc_stress", "dtc_planning", "assemble"]
BUDGET = 60


def _poll(client, url, timeout=900.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(url)
        if r.status_code == 200 and r.json().get("status") != "running":
            return r.json()
        time.sleep(0.2)
    raise AssertionError(f"{url} never finished")


def _project_from_template(client, tmp_path, monkeypatch, tid):
    from routers import projects as P
    src = tmp_path / tid
    src.mkdir()
    T.BUILDERS[tid]().export_to_netcdf(str(src / "network.nc"))
    T.write_sidecars(src, tid)
    monkeypatch.setattr(P, "_PROJECT_TEMPLATES_DIR", tmp_path)
    r = client.post(f"/api/projects/from_template/{tid}",
                    params={"name": f"e2e_{tid}"})
    assert r.status_code == 200, r.text
    return r.json().get("name", f"e2e_{tid}")


def _network_fingerprint():
    from services.pypsa_service import PyPSAService
    n = PyPSAService.get_network()
    return (tuple(n.generators.index), tuple(n.links.index),
            tuple(n.links["p_nom"].round(6)),
            tuple(n.links.get("p_max_pu", n.links["p_nom"] * 0).round(6)),
            tuple(n.loads.index))


@pytest.mark.live_solve
@pytest.mark.parametrize("tid", sorted(T.BUILDERS))
def test_every_feature_on_the_template(tid, client, tmp_path, monkeypatch,
                                       tmp_projects_dir):
    name = _project_from_template(client, tmp_path, monkeypatch, tid)
    meta = client.get(f"/api/projects/{name}/eh_template").json()
    body = {"archetype": meta["recommended_archetype"], "stages": ALL_STAGES,
            "budget_solves": BUDGET}
    if meta["pack_overrides"]:
        body["pack_overrides"] = meta["pack_overrides"]
    if meta.get("dtc_attribution"):
        body["dtc_attribution"] = meta["dtc_attribution"]

    # 1. readiness predicts that every requested stage runs
    params = {"archetype": body["archetype"], "budget_solves": BUDGET,
              "stages": ",".join(ALL_STAGES)}
    if "dtc_attribution" in body:
        params["dtc_attribution"] = body["dtc_attribution"]
    ready = client.get("/api/results/eh_readiness", params=params).json()
    predicted = {r["stage"]: r["prediction"] for r in ready["stages"]}
    assert set(predicted.values()) <= {"run"}, predicted

    # 2. the study, on a private copy of the session network
    before = _network_fingerprint()
    r = client.post("/api/results/eh_study", json=body)
    assert r.status_code == 200, r.text
    study = _poll(client, "/api/results/eh_study")
    assert study["status"] == "done", study.get("error")
    assert _network_fingerprint() == before          # P10 isolation

    # 3. the report — every stage ran, budget honoured, sections honest
    report = client.get("/api/results/eh_reference_design")
    assert report.status_code == 200
    rep = report.json()
    stages = {s["stage"]: s for s in rep["pipeline"]["stages"]}
    assert all(stages[s]["status"] == "run" for s in ALL_STAGES), stages
    charged = sum(int(s.get("solves_charged") or 0) for s in stages.values())
    assert charged == rep["pipeline"]["solves_consumed"] <= BUDGET
    for section, status in rep["completeness"].items():
        assert status in ("ok", "skipped", "not_established"), section
        if status == "not_established":
            assert rep["sections"][section]["note"], section
    for must in ("target", "cost", "sizing", "frontier", "fmea_top",
                 "redundancy", "levers", "dtc", "certification"):
        assert rep["completeness"][must] == "ok", (
            must, rep["sections"][must].get("note"))
    cert = rep["sections"]["certification"]["payload"] or {}
    assert rep["certified"] is (True if cert.get("verdict") == "pass"
                                else (None if cert.get("verdict") is None
                                      else False))
    if meta.get("dtc_attribution") == "per_load":
        assert rep["sections"]["dtc"]["payload"]["attribution"] == "per_load"
    if body["archetype"] == "weak_flexible":
        assert rep["completeness"]["gates"] == "ok"          # SCR inputs tagged

    # 4. the Class-C FMEA sweep with the template's own registry
    reg = client.get(f"/api/projects/{name}/stress_scenarios").json()
    assert reg["error"] is None and reg["scenarios"]
    r = client.post("/api/results/fmea_sweep", json={"scenarios": reg["scenarios"]})
    assert r.status_code == 200, r.text
    sweep = _poll(client, "/api/results/fmea_sweep")
    assert sweep["status"] == "done", sweep
    modes = client.get("/api/results/fmea_modes").json()["per_mode"]
    classes = {m["failure_class"] for m in modes}
    assert {"B", "C"} <= classes
    assert sum(m["failure_class"] == "C" for m in modes) == len(reg["scenarios"])
    assert _network_fingerprint() == before          # sweep restores base

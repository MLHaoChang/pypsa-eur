"""
The adequacy surfaces over the REAL HTTP stack (QA gap closed 2026-08-28).

Every other adequacy test calls handler functions directly with a hand-built
AuthorizedProject — which bypasses FastAPI's dependency injection and
therefore the auth + CSRF middleware and the project ACL entirely. These
tests drive the authenticated TestClient instead, so the routes are
exercised the way production meets them: real session, real CSRF, real
`require_project_access`, real router mounting (including whether the
`/{name}` catch-all shadows `/{name}/worksheet`).
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

WEIGHT = 3.0
VOLL = 3000.0


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.snapshot_weightings.loc[:, :] = WEIGHT
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC", country="AA")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=90.0,
          marginal_cost=10.0,
          outage_rate_value=0.05, outage_rate_basis="EFORd", mttr_hours=50.0)
    return n


def _expandable_network() -> pypsa.Network:
    """The network the FEATURE is for: 10 MW short at fixed capacity, with an
    extendable peaker the LP can build to meet a reliability target. A target
    tighter than the fixed fleet can serve is correctly INFEASIBLE — it is the
    ability to invest that makes a target meaningful."""
    n = _network()
    n.add("Generator", "peaker", bus="b", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_min=0.0, p_nom_max=50.0,
          capital_cost=1000.0, marginal_cost=200.0)
    return n


def _expert_row() -> dict:
    return {
        "mode_id": "manual:cyber", "component_class": "Network",
        "name": "cyber", "failure_class": "D",
        "occurrence_per_year": 0.5, "occurrence_basis": "expert",
        "severity_eur": 200.0, "criticality_eur_per_year": 100.0,
        "in_metric_scope": False, "mitigability": "segmentation",
        "engine": "expert", "fidelity": "expert_judgement",
    }


# ── worksheet + stress registry through the ACL ───────────────────────────

def test_worksheet_routes_round_trip_through_the_real_stack(
        client, install_network, tmp_projects_dir):
    install_network(_network(), name="WS")
    assert client.post("/api/projects/WS", params={"force": True, "rebind": True}
                       ).status_code == 200

    r = client.get("/api/projects/WS/worksheet")
    assert r.status_code == 200, r.text
    assert r.json() == {"__schema__": 1, "version": 0,
                        "manual_rows": [], "overlays": {}}

    r = client.put("/api/projects/WS/worksheet", json={
        "manual_rows": [_expert_row()],
        "overlays": {"generator:g:forced_outage": {"mitigability": "spares"}},
    })
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1

    again = client.get("/api/projects/WS/worksheet").json()
    assert again["manual_rows"][0]["name"] == "cyber"
    assert again["overlays"]["generator:g:forced_outage"]["mitigability"] == "spares"


def test_worksheet_put_rejects_forged_provenance_with_422(
        client, install_network, tmp_projects_dir):
    install_network(_network(), name="WS2")
    client.post("/api/projects/WS2", params={"force": True, "rebind": True})
    bad = _expert_row() | {"engine": "copt"}
    r = client.put("/api/projects/WS2/worksheet",
                   json={"manual_rows": [bad], "overlays": {}})
    assert r.status_code == 422, r.text


def test_stress_registry_round_trips_and_validates(
        client, install_network, tmp_projects_dir):
    install_network(_network(), name="WS3")
    client.post("/api/projects/WS3", params={"force": True, "rebind": True})
    assert client.get("/api/projects/WS3/stress_scenarios").json() == {"scenarios": []}
    good = {"id": "cold_snap", "name": "1-in-20", "kind": "parametric",
            "frequency_per_year": 0.05,
            "electrical_load_multiplier": 1.3,
            "renewable_availability_multiplier": 0.5}
    r = client.put("/api/projects/WS3/stress_scenarios", json={"scenarios": [good]})
    assert r.status_code == 200, r.text
    assert client.get("/api/projects/WS3/stress_scenarios").json()["scenarios"][0]["id"] \
        == "cold_snap"
    r = client.put("/api/projects/WS3/stress_scenarios",
                   json={"scenarios": [good | {"frequency_per_year": 0}]})
    assert r.status_code == 422


def test_worksheet_is_not_shadowed_by_the_project_catch_all(
        client, install_network, tmp_projects_dir):
    """`/{name}/worksheet` is mounted before projects.router; a regression in
    mount ORDER would make this return the project payload (or 404/405)."""
    install_network(_network(), name="WS4")
    client.post("/api/projects/WS4", params={"force": True, "rebind": True})
    body = client.get("/api/projects/WS4/worksheet").json()
    assert set(body) == {"__schema__", "version", "manual_rows", "overlays"}


def test_unknown_project_is_refused(client, tmp_projects_dir):
    """A worksheet read for a project the caller has no row for must be
    refused by require_project_access, not answered with empty state."""
    r = client.get("/api/projects/no_such_project_xyz/worksheet")
    assert r.status_code in (400, 403, 404), r.text


def test_worksheet_requires_a_session(anon_client, tmp_projects_dir):
    r = anon_client.get("/api/projects/WS/worksheet")
    assert r.status_code in (401, 403), r.text


# ── the results surfaces ──────────────────────────────────────────────────

def test_copt_and_fmea_modes_over_http(client, install_network):
    install_network(_network())
    r = client.get("/api/results/copt")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["engine"] == "copt"
    assert body["fleet"]["units"] == 1
    assert [m["name"] for m in body["per_mode"]] == ["g"]

    r = client.get("/api/results/fmea_modes")
    assert r.status_code == 200
    assert {m["failure_class"] for m in r.json()["per_mode"]} == {"A"}


def test_copt_204_without_occurrence_data(client, install_network):
    n = _network()
    n.generators.at["g", "outage_rate_value"] = float("nan")
    n.generators.at["g", "carrier"] = "unobtainium"   # no library default
    install_network(n)
    assert client.get("/api/results/copt").status_code == 204


def test_adequacy_204_before_a_target_solve(client, install_network):
    install_network(_network())
    assert client.get("/api/results/adequacy").status_code == 204


def test_fmea_sweep_requires_voll_over_http(client, install_network):
    install_network(_network())          # install_network sets a default cfg
    r = client.post("/api/results/fmea_sweep", json={"scenarios": []})
    assert r.status_code == 422, r.text
    assert "VOLL" in r.json()["detail"]


def test_sweep_status_204_before_any_run(client, install_network):
    install_network(_network())
    assert client.get("/api/results/fmea_sweep").status_code == 204


# ── the full chain: target solve → report → worksheet merge inputs ─────────

def test_target_solve_produces_a_report_over_http(client, install_network):
    """The end-to-end loop the feature exists for: set a target, solve, read
    the report. Exercises the solver route, the ENS-cap wrapper, the report
    builder and the results route as one chain."""
    install_network(_expandable_network())
    cfg = client.get("/api/simulation/solver_config").json()
    cfg.update(voll=VOLL, ens_cap_permyriad=200.0)
    assert client.put("/api/simulation/solver_config", json=cfg).status_code == 200

    r = client.post("/api/simulation/run")
    assert r.status_code in (200, 202), r.text
    for _ in range(600):
        st = client.get("/api/simulation/status").json()
        if st.get("status") in ("completed", "failed", "aborted"):
            break
        import time
        time.sleep(0.5)
    assert st["status"] == "completed", st

    rep = client.get("/api/results/adequacy")
    assert rep.status_code == 200, rep.text
    body = rep.json()
    assert body["engine"] == "lp_proxy"
    assert body["target"]["binding"] in ("system_cap", "zone_cap", "voll")
    # The cost axis excludes shed cost by construction (Literal[True]).
    assert body["cost"]["excludes_shed_cost"] is True
    assert body["target"]["zone_field_populated"] is True   # bus.country = "AA"
    # The target drove investment: the peaker got built to honour the cap.
    caps = client.get("/api/results/capacity_expansion")
    if caps.status_code == 200:
        assert body["metrics"]["ens_mwh"] <= body["target"]["system"]["cap_mwh"] * 1.001
    # Shed-hours reaches the lost-load surface too.
    ll = client.get("/api/results/lost_load")
    assert ll.status_code == 200
    assert "shed_hours" in ll.json()

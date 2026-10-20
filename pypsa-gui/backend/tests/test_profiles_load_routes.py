"""Load profiles over HTTP (time-series generators ticket 01, slice B2).

  * `GET  /api/profiles/load-archetypes` — the archetypes, their parameter
    defaults and the presets;
  * `POST /api/projects/{name}/profiles/loads/generate` — generate a load
    archetype for named loads, synchronously, into the SAME pending store as
    the renewable jobs (`job.write_pending`);
  * `POST /api/projects/{name}/profiles/apply` — the one apply route, now
    told (or inferring from the artifact's family) which target to write.
"""
from __future__ import annotations

import json

import pandas as pd
import pypsa
import pytest

SNAPS = pd.date_range("2025-01-01", periods=8760, freq="h")


def _load_network() -> pypsa.Network:
    n = pypsa.Network()
    idx = SNAPS.copy()
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1", x=8.0, y=50.0)
    n.add("Load", "office", bus="B1", p_set=1.0)
    n.add("Load", "plant", bus="B1", p_set=1.0)
    n.add("Carrier", "wind")
    n.add("Generator", "g_wind", bus="B1", carrier="wind", p_nom=10.0)
    return n


@pytest.fixture
def load_project(client, install_network):
    install_network(_load_network())
    resp = client.post("/api/projects/Load Site",
                       params={"force": True, "rebind": True})
    assert resp.status_code == 200, resp.text
    return "Load Site"


def _generate(client, project, **over):
    body = {"archetype": "industrial", "loads": ["plant"],
            "params": {"shifts": 2, "annual_mwh": 5000.0}, "year": 2025}
    body.update(over)
    return client.post(f"/api/projects/{project}/profiles/loads/generate",
                       json=body)


def test_the_catalogue_lists_archetypes_and_presets(client):
    resp = client.get("/api/profiles/load-archetypes")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = {a["name"] for a in body["archetypes"]}
    assert {"industrial", "commercial", "residential", "ev_charging", "heat",
            "data_centre", "library"} <= names
    industrial = next(a for a in body["archetypes"] if a["name"] == "industrial")
    assert industrial["params"]["shifts"] == 2
    assert "peak_mw" in industrial["params"]
    presets = {p["name"] for p in body["presets"]}
    assert "commercial_office" in presets


def test_generate_writes_a_pending_load_artifact(client, load_project):
    resp = _generate(client, load_project)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["family"] == "load"
    assert body["archetype"] == "industrial"
    assert body["points"] == ["plant"]
    assert body["stats"]["plant"]["annual_mwh"] == pytest.approx(5000.0)
    assert body["stats"]["plant"]["peak_mw"] > 0

    pending = client.get(f"/api/projects/{load_project}/profiles/pending").json()
    assert any(p["job_id"] == body["job_id"] for p in pending["pending"])


def test_generate_refuses_a_load_not_on_the_network(client, load_project):
    resp = _generate(client, load_project, loads=["nope"])
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "unknown_load"


def test_generate_refuses_an_unknown_archetype(client, load_project):
    resp = _generate(client, load_project, archetype="bakery")
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "unknown_archetype"


def test_generate_refuses_bad_parameters(client, load_project):
    resp = _generate(client, load_project, params={"shfits": 3, "peak_mw": 1.0})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "invalid_archetype_params"


def test_a_generated_load_applies_to_p_set_by_its_family(client, load_project,
                                                       session_ctx):
    job = _generate(client, load_project).json()
    resp = client.post(f"/api/projects/{load_project}/profiles/apply",
                       json={"job_id": job["job_id"], "component": "plant",
                             "time_basis": "UTC"})
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    assert summary["component_type"] == "loads"
    assert summary["attribute"] == "p_set"
    assert summary["profile_annual_mwh"] == pytest.approx(5000.0)

    ctx = session_ctx(client)
    assert ("loads", "p_set", "plant") in ctx.user_ts
    assert float(ctx.network.loads_t.p_set["plant"].sum()) == pytest.approx(5000.0)


def test_apply_of_a_load_profile_can_be_undone(client, load_project):
    job = _generate(client, load_project).json()
    client.post(f"/api/projects/{load_project}/profiles/apply",
                json={"job_id": job["job_id"], "component": "plant",
                      "time_basis": "UTC"})
    assert client.post("/api/network/undo").status_code == 200
    payload = client.get("/api/network/timeseries/loads/p_set").json()
    assert "plant" not in payload["columns"]


def test_a_load_profile_cannot_be_applied_to_a_generator(client, load_project):
    job = _generate(client, load_project).json()
    resp = client.post(f"/api/projects/{load_project}/profiles/apply",
                       json={"job_id": job["job_id"], "component": "g_wind",
                             "point_id": "plant",
                             "component_type": "generators",
                             "attribute": "p_max_pu"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "family_target_mismatch"


def test_a_dc_archetype_on_a_dc_load_project_is_refused(client, load_project,
                                                      session_ctx):
    from services.dc_load_profile import META_KEY

    ctx = session_ctx(client)
    meta_path = ctx.storage_dir and (__import__("pathlib").Path(ctx.storage_dir)
                                     / "eh_template.json")
    meta_path.write_text(json.dumps({META_KEY: {"envelope": {"schema": 1}}}))
    job = _generate(client, load_project, archetype="data_centre",
                    params={"peak_mw": 5.0}).json()
    resp = client.post(f"/api/projects/{load_project}/profiles/apply",
                       json={"job_id": job["job_id"], "component": "plant",
                             "time_basis": "UTC"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "dc_load_owned"


def test_generate_on_another_orgs_project_is_a_404(other_org_client):
    resp = other_org_client.post(
        "/api/projects/Load Site/profiles/loads/generate",
        json={"archetype": "industrial", "loads": ["plant"],
              "params": {"peak_mw": 1.0}},
    )
    assert resp.status_code == 404


def test_generate_maps_working_hours_on_the_projects_commercial_timezone(
    client, load_project, monkeypatch,
):
    import services.profile_gen.apply as apply_mod

    monkeypatch.setattr(apply_mod, "_commercial_timezone", lambda ctx: "Europe/Berlin")
    body = _generate(client, load_project).json()
    assert body["params"]["timezone"] == "Europe/Berlin"
    explicit = _generate(client, load_project,
                         params={"annual_mwh": 1.0, "timezone": "UTC"}).json()
    assert explicit["params"]["timezone"] == "UTC"

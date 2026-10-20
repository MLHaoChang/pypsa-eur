"""Load profiles over HTTP (time-series generators ticket 01, slice B2).

* `GET  /api/profiles/load-archetypes`: the archetypes, their parameter
  defaults and the presets;
* `POST /api/projects/{name}/profiles/loads/generate`: generate a load
  archetype for named loads, synchronously, into the SAME pending store as
  the renewable jobs (`job.write_pending`);
* `POST /api/projects/{name}/profiles/apply`: the one apply route, now told
  (or inferring from the artifact's family) which target to write.
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


def _meter_csv(columns: dict, unit_suffix: str = "") -> bytes:
    idx = pd.date_range("2025-01-01", "2026-01-01", freq="h", inclusive="left")
    frame = pd.DataFrame({f"{k}{unit_suffix}": v for k, v in columns.items()},
                         index=idx)
    frame.index.name = "timestamp"
    return frame.to_csv().encode()


def test_a_measured_upload_lands_in_pending_and_applies(client, load_project,
                                                       session_ctx):
    resp = client.post(
        f"/api/projects/{load_project}/profiles/loads/upload",
        files={"file": ("meter.csv", _meter_csv({"plant": 2000.0, "shed": 1.0}),
                        "text/csv")},
        data={"unit": "kW"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provenance"] == "measured"
    assert body["points"] == ["plant"]
    assert body["unmatched"] == ["shed"]
    assert body["stats"]["plant"]["peak_mw"] == pytest.approx(2.0)

    applied = client.post(f"/api/projects/{load_project}/profiles/apply",
                          json={"job_id": body["job_id"], "component": "plant",
                                "time_basis": "UTC"})
    assert applied.status_code == 200, applied.text
    ctx = session_ctx(client)
    assert ctx.user_ts_meta[("loads", "p_set", "plant")]["provenance"] == "measured"
    assert float(ctx.network.loads_t.p_set["plant"].iloc[0]) == pytest.approx(2.0)


def test_an_upload_without_a_unit_is_a_typed_refusal(client, load_project):
    resp = client.post(
        f"/api/projects/{load_project}/profiles/loads/upload",
        files={"file": ("meter.csv", _meter_csv({"plant": 1.0}), "text/csv")},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "load_upload_unit"


def test_an_upload_naming_no_load_is_refused(client, load_project):
    resp = client.post(
        f"/api/projects/{load_project}/profiles/loads/upload",
        files={"file": ("meter.csv", _meter_csv({"shed": 1.0}), "text/csv")},
        data={"unit": "MW"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "unknown_load"
def test_generate_previews_the_modelled_energy_and_the_time_basis(client,
                                                                load_project):
    body = _generate(client, load_project).json()
    assert body["stats"]["plant"]["modelled_mwh"] == {"total": pytest.approx(5000.0)}
    assert any(w.startswith("time_basis_assumed_utc") for w in body["warnings"])


def _first_on_hour(series: pd.Series, day: str) -> int:
    hours = series.loc[day]
    return int(hours.index[hours.to_numpy() > hours.min() + 1e-9][0].hour)


@pytest.mark.parametrize("zone, winter, summer, warned", [
    ("Europe/Oslo", 5, 4, False),   # 06:00 local is 05:00 UTC (CET), 04:00 (CEST)
    (None, 6, 6, True),             # no zone: the snapshot clock, with a warning
])
def test_an_archetype_lands_once_converted_on_utc_snapshots(
    client, load_project, session_ctx, monkeypatch, zone, winter, summer, warned,
):
    import services.profile_gen.apply as apply_mod

    monkeypatch.setattr(apply_mod, "_commercial_timezone", lambda ctx: zone)
    job = _generate(client, load_project,
                    params={"shifts": 1, "peak_mw": 1.0}).json()
    resp = client.post(f"/api/projects/{load_project}/profiles/apply",
                       json={"job_id": job["job_id"], "component": "plant"})
    assert resp.status_code == 200, resp.text
    assert any(w.startswith("time_basis_assumed_utc")
               for w in resp.json()["warnings"]) is warned
    s = session_ctx(client).network.loads_t.p_set["plant"]
    assert _first_on_hour(s, "2025-01-08") == winter   # a Wednesday
    assert _first_on_hour(s, "2025-07-09") == summer


# ---------------------------------------------------------------------------
# availability (ticket 02)
# ---------------------------------------------------------------------------


@pytest.fixture
def plant_project(client, install_network):
    n = _load_network()
    n.add("Carrier", "gas")
    n.add("Generator", "chp", bus="B1", carrier="gas", p_nom=10.0)
    install_network(n)
    resp = client.post("/api/projects/CHP Site",
                       params={"force": True, "rebind": True})
    assert resp.status_code == 200, resp.text
    return "CHP Site"


def _avail(client, project, **over):
    body = {"kind": "maintenance", "generators": ["chp"],
            "params": {"windows": [{"from": "07-03", "to": "07-16"}]}}
    body.update(over)
    return client.post(f"/api/projects/{project}/profiles/availability/generate",
                       json=body)


def test_availability_generates_on_the_networks_year_and_applies(
    client, plant_project, session_ctx,
):
    gen = _avail(client, plant_project)
    assert gen.status_code == 200, gen.text
    body = gen.json()
    assert body["weather_year"] == 2025          # the network's own year
    assert body["target"] == ["generators", "p_max_pu"]
    assert body["stats"]["chp"]["hours_reduced"] == 14 * 24
    applied = client.post(f"/api/projects/{plant_project}/profiles/apply",
                          json={"job_id": body["job_id"], "component": "chp",
                                "time_basis": "UTC"})
    assert applied.status_code == 200, applied.text
    p = session_ctx(client).network.generators_t.p_max_pu["chp"]
    assert float(p.loc["2025-07-04T12:00"]) == 0.0


def test_must_run_applies_to_p_min_pu_by_its_stated_target(client, plant_project,
                                                          session_ctx):
    body = _avail(client, plant_project, kind="must_run",
                  params={"share": 0.2}).json()
    applied = client.post(f"/api/projects/{plant_project}/profiles/apply",
                          json={"job_id": body["job_id"], "component": "chp",
                                "time_basis": "UTC"})
    assert applied.status_code == 200, applied.text
    assert applied.json()["attribute"] == "p_min_pu"


@pytest.mark.parametrize("over, kind", [
    ({"kind": "forced_outages", "params": {}}, "forced_outages_not_written"),
    ({"kind": "strike", "params": {}}, "unknown_availability_kind"),
    ({"params": {"windows": "07-03"}}, "invalid_availability_params"),
    ({"params": {"windows": [{"from": "2031-07-03", "to": "2031-07-04"}]}},
     "maintenance_year"),
    ({"generators": ["nope"]}, "unknown_generator"),
    ({"generators": ["g_wind"]}, "carrier_mismatch"),
])
def test_availability_refusals_are_typed(client, plant_project, over, kind):
    resp = _avail(client, plant_project, **over)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == kind

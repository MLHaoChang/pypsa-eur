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


@pytest.mark.parametrize("resolution", ["daily", "weekly"])
def test_generate_refuses_a_resolution_other_than_hourly(
    client, load_project, resolution,
):
    """Load archetypes are hourly shapes. Daily and weekly are valid for a
    renewable profile; here they must be refused before a pending artifact
    exists, or apply would upsample a shape the archetype never drew."""
    resp = _generate(client, load_project, resolution=resolution)
    assert resp.status_code == 422, resp.text
    assert "hourly" in resp.text
    pending = client.get(
        f"/api/projects/{load_project}/profiles/pending").json()["pending"]
    assert pending == []


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


def test_a_closed_project_cannot_generate_upload_or_apply_a_load_profile(
    client, load_project, session_ctx,
):
    """These routes read or write the foreground network. Asked about a
    project that is no longer open, they refuse and leave both the closed
    project's pending store and the open project's loads untouched."""
    job = _generate(client, load_project)
    assert job.status_code == 200, job.text
    job_id = job.json()["job_id"]
    before = client.get(
        f"/api/projects/{load_project}/profiles/pending").json()["pending"]

    other = client.post("/api/projects/Other Site",
                        params={"force": True, "rebind": True})
    assert other.status_code == 200, other.text

    generated = _generate(client, load_project)
    assert generated.status_code == 409, generated.text
    detail = generated.json()["detail"]
    assert detail["error_kind"] == "project_not_open"
    assert detail["message"].startswith("'Load Site' is not the open project")
    assert "Other Site" in detail["message"]

    uploaded = client.post(
        f"/api/projects/{load_project}/profiles/loads/upload",
        files={"file": ("meter.csv", _meter_csv({"plant": 2.0}), "text/csv")},
        data={"unit": "MW"},
    )
    assert uploaded.status_code == 409, uploaded.text
    assert uploaded.json()["detail"]["error_kind"] == "project_not_open"

    applied = client.post(
        f"/api/projects/{load_project}/profiles/apply",
        json={"job_id": job_id, "component": "plant", "time_basis": "UTC"},
    )
    assert applied.status_code == 409, applied.text
    assert str(applied.json()["detail"]).startswith(
        "'Load Site' is not the open project")

    pending = client.get(
        f"/api/projects/{load_project}/profiles/pending").json()["pending"]
    assert [p["job_id"] for p in pending] == [p["job_id"] for p in before]

    ctx = session_ctx(client)
    assert ctx.loaded_project == "Other Site"
    assert ("loads", "p_set", "plant") not in ctx.user_ts
    assert "plant" not in ctx.network.loads_t.p_set.columns


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

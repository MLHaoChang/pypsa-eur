"""RP-04b: the weather bar's saved choice (spec R3).

The Time Series Manager's weather bar saves a source and a year in the
project's metadata. A generate that names neither takes them (so the chat
assistant and the per-generator Generate agree with the bar); a project with
nothing saved keeps RP-03's snapshot default; an explicit request wins.
"""
from __future__ import annotations

import datetime as dt
import time
import uuid

import pandas as pd
import pytest

LAST = dt.datetime.now(dt.timezone.utc).year - 1


def _project(client, install_network, stamp_year=2023):
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range(f"{stamp_year}-01-01", periods=8760, freq="h", name="snapshot"))
    n.add("Bus", "B1", x=8.0, y=50.0)
    n.add("Carrier", "wind")
    n.add("Generator", "g_wind", bus="B1", carrier="wind", p_nom=10.0)
    install_network(n)
    assert client.post("/api/projects/Weather Bar",
                       params={"force": True, "rebind": True}).status_code == 200
    return "Weather Bar"


def _generate(client, project, **extra):
    from services import solve_queue as sq

    resp = client.post(f"/api/projects/{project}/profiles/generate", json={
        "from_generators": ["g_wind"], "technology": "wind", **extra})
    assert resp.status_code in (200, 202), resp.text
    jid = resp.json()["id"]
    deadline = time.time() + 60
    while time.time() < deadline:
        job = sq.solve_queue.get_job(uuid.UUID(jid))
        if job and job["status"] in sq._TERMINAL:
            break
        time.sleep(0.1)
    assert job["status"] == "completed", job.get("error")
    pending = client.get(f"/api/projects/{project}/profiles/pending").json()["pending"]
    return next(p for p in pending if p["job_id"] == jid)


def test_nothing_saved_reads_the_snapshot_default(client, install_network):
    project = _project(client, install_network, 2023)
    got = client.get(f"/api/projects/{project}/profiles/weather").json()
    assert (got["source"], got["year"], got["year_source"]) == ("synthetic", 2023, "snapshots")


def test_future_snapshots_read_as_the_default_year(client, install_network):
    project = _project(client, install_network, 2041)
    got = client.get(f"/api/projects/{project}/profiles/weather").json()
    assert (got["year"], got["year_source"]) == (LAST, "default")


def test_the_time_basis_is_read_only_and_resolved(client, install_network):
    project = _project(client, install_network)
    basis = client.get(f"/api/projects/{project}/profiles/weather").json()["time_basis"]
    assert basis["value"].startswith("UTC") and basis["assumed"] is True


def test_a_saved_choice_is_read_back_as_saved(client, install_network):
    project = _project(client, install_network, 2023)
    put = client.put(f"/api/projects/{project}/profiles/weather", json={"source": "atlite", "year": 2019})
    assert put.status_code == 200, put.text
    got = client.get(f"/api/projects/{project}/profiles/weather").json()
    assert (got["source"], got["year"], got["year_source"]) == ("atlite", 2019, "saved")


def test_generate_without_a_year_takes_the_saved_year(client, install_network):
    project = _project(client, install_network, 2023)
    assert client.put(f"/api/projects/{project}/profiles/weather",
                      json={"source": "synthetic", "year": 2019}).status_code == 200
    rec = _generate(client, project)
    assert (rec["weather_year"], rec["year_source"]) == (2019, "saved")


def test_an_explicit_year_still_wins_over_the_saved_one(client, install_network):
    project = _project(client, install_network, 2023)
    client.put(f"/api/projects/{project}/profiles/weather", json={"source": "synthetic", "year": 2019})
    rec = _generate(client, project, year=2021)
    assert (rec["weather_year"], rec["year_source"]) == (2021, "explicit")


def test_the_saved_source_is_used_when_the_request_names_none(client, install_network):
    project = _project(client, install_network, 2023)
    client.put(f"/api/projects/{project}/profiles/weather", json={"source": "atlite", "year": 2019})
    resp = client.post(f"/api/projects/{project}/profiles/generate", json={
        "from_generators": ["g_wind"], "technology": "wind"})
    # atlite without a CDS key is refused at the request: the saved source was read.
    assert resp.status_code == 422 and resp.json()["detail"]["error_kind"] == "cds_credential"


@pytest.mark.parametrize("body", [
    {"source": "nasa", "year": 2019},
    {"source": "synthetic", "year": LAST + 1},
    {"source": "synthetic", "year": 1899},
    {"source": "atlite", "year": 1939},
])
def test_a_bad_saved_choice_is_refused(client, install_network, body):
    project = _project(client, install_network)
    assert client.put(f"/api/projects/{project}/profiles/weather", json=body).status_code == 422


def test_clearing_returns_to_the_snapshot_default(client, install_network):
    project = _project(client, install_network, 2023)
    client.put(f"/api/projects/{project}/profiles/weather", json={"source": "synthetic", "year": 2019})
    assert client.delete(f"/api/projects/{project}/profiles/weather").status_code == 200
    got = client.get(f"/api/projects/{project}/profiles/weather").json()
    assert (got["year"], got["year_source"]) == (2023, "snapshots")


@pytest.mark.parametrize("body", [
    {"source": "synthetic", "year": 1900},
    {"source": "synthetic", "year": 1939},
    {"source": "atlite", "year": 1940},
    {"source": "atlite", "year": LAST},
])
def test_each_sources_own_year_bounds_are_accepted(client, install_network, body):
    project = _project(client, install_network)
    assert client.put(f"/api/projects/{project}/profiles/weather", json=body).status_code == 200


def test_a_project_save_keeps_the_weather_choice(client, install_network):
    """A save rewrites metadata.json from a fixed key list. The weather bar's
    source and year have to ride along: the next autosave would otherwise drop
    them, and a generate that names no year would use the snapshot default."""
    project = _project(client, install_network, 2023)
    assert client.put(f"/api/projects/{project}/profiles/weather",
                      json={"source": "synthetic", "year": 2019}).status_code == 200
    saved = client.post(f"/api/projects/{project}", params={"force": True})
    assert saved.status_code == 200, saved.text
    got = client.get(f"/api/projects/{project}/profiles/weather").json()
    assert (got["source"], got["year"], got["year_source"]) == ("synthetic", 2019, "saved")


def test_saving_a_year_leaves_existing_series_alone(client, install_network, session_ctx):
    project = _project(client, install_network, 2023)
    rec = _generate(client, project, year=2021)
    assert client.post(f"/api/projects/{project}/profiles/apply", json={
        "job_id": rec["job_id"], "component": "g_wind", "time_basis": "UTC"}).status_code == 200
    before = session_ctx(client).user_ts[("generators", "p_max_pu", "g_wind")].copy()
    client.put(f"/api/projects/{project}/profiles/weather", json={"source": "synthetic", "year": 2019})
    after = session_ctx(client).user_ts[("generators", "p_max_pu", "g_wind")]
    assert after.equals(before)
    rows = client.get(f"/api/projects/{project}/profiles/readiness").json()["generators"]
    assert rows[0]["profile"] == "generated" and "stale_reason" not in rows[0]

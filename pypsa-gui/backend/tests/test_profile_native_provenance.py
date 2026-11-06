"""RP-14: a network-native series is not a measured upload (Q-RP-14 = 1).

Opening a project seeds `user_ts` from the network's own `_t` tables
(`_backup_network_ts_to_user_ts`). The seed now stamps
``provenance: network_native``, so `defaults.is_measured` can stay strict (an
entry with no record is an upload) and still let a default replace the
network's own series. A series applied as measured is never replaced.
"""
from __future__ import annotations

import time
import uuid

import pandas as pd
import pypsa
import pytest

SNAPS = pd.date_range("2025-01-01", periods=8760, freq="h", name="snapshot")
KEY = ("generators", "p_max_pu", "g_a")


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(SNAPS)
    n.add("Bus", "B1", x=8.0, y=50.0)
    n.add("Carrier", ["wind"])
    n.add("Generator", "g_a", bus="B1", carrier="wind", p_nom=10.0)
    n.generators_t.p_max_pu["g_a"] = 0.9   # the network's own series
    return n


@pytest.fixture
def project(client, install_network):
    install_network(_network())
    resp = client.post("/api/projects/Native Farm", params={"force": True, "rebind": True})
    assert resp.status_code == 200, resp.text
    return "Native Farm"


def _job(client, project) -> str:
    from services import solve_queue as sq

    resp = client.post(f"/api/projects/{project}/profiles/generate", json={
        "points": [{"id": "g_a", "lon": 8.0, "lat": 50.0}], "technology": "wind", "year": 2023})
    assert resp.status_code in (200, 202), resp.text
    job_id = resp.json()["id"]
    deadline = time.time() + 60
    while time.time() < deadline:
        job = sq.solve_queue.get_job(uuid.UUID(job_id))
        if job and job["status"] in sq._TERMINAL:
            break
        time.sleep(0.1)
    assert job["status"] == "completed", job.get("error")
    return job_id


def _apply(client, project, job_id) -> dict:
    resp = client.post(f"/api/projects/{project}/profiles/defaults/apply",
                       json={"job_ids": [job_id]})
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


def test_the_seed_records_the_networks_own_series(client, project, session_ctx):
    ctx = session_ctx(client)
    assert KEY in ctx.user_ts
    assert ctx.user_ts_meta[KEY]["provenance"] == "network_native"
    rows = client.get(f"/api/projects/{project}/profiles/readiness").json()["generators"]
    assert {r["generator"]: r for r in rows}["g_a"]["profile"] == "network_native"


def test_a_default_replaces_the_native_series_and_one_undo_restores_it(
        client, project, session_ctx):
    out = _apply(client, project, _job(client, project))
    assert out["applied"] is True, out
    ctx = session_ctx(client)
    assert float(ctx.network.generators_t.p_max_pu["g_a"].mean()) != pytest.approx(0.9)
    assert client.post("/api/network/undo").status_code == 200
    ctx = session_ctx(client)
    assert float(ctx.network.generators_t.p_max_pu["g_a"].iloc[0]) == pytest.approx(0.9)
    assert ctx.user_ts_meta[KEY]["provenance"] == "network_native"


def test_a_series_applied_as_measured_is_never_replaced(client, project, session_ctx):
    job_id = _job(client, project)
    ctx = session_ctx(client)
    ctx.user_ts[KEY] = pd.Series(0.3, index=ctx.network.snapshots)
    ctx.user_ts_meta[KEY] = {"provenance": "measured", "source": "upload", "family": "renewable"}
    out = _apply(client, project, job_id)
    assert out["applied"] is False and out["error_kind"] == "measured_series_kept"


def test_an_upload_with_no_record_still_counts_as_measured(client, project, session_ctx):
    job_id = _job(client, project)
    ctx = session_ctx(client)
    ctx.user_ts[KEY] = pd.Series(0.3, index=ctx.network.snapshots)
    ctx.user_ts_meta.pop(KEY, None)
    out = _apply(client, project, job_id)
    assert out["applied"] is False and out["error_kind"] == "measured_series_kept"


def test_a_loads_own_demand_series_is_left_alone_by_the_defaults_list(
        client, install_network, session_ctx):
    n = _network()
    n.add("Bus", "B2", carrier="AC")
    n.add("Load", "office", bus="B2", p_set=2.0)
    n.loads_t.p_set["office"] = 2.0   # the network's own demand series
    install_network(n)
    assert client.post("/api/projects/Native Site",
                       params={"force": True, "rebind": True}).status_code == 200
    resp = client.post("/api/projects/Native Site/profiles/defaults/preview", json={})
    assert resp.status_code == 200, resp.text
    row = {r["component"]: r for r in resp.json()["rows"]}["office"]
    assert "the network's own series" in row["skipped"]


def test_the_editor_names_the_native_series_from_the_network(client, project, session_ctx):
    from services.profile_gen.current import describe_current

    current = describe_current(session_ctx(client), "generators", "p_max_pu", "g_a")
    assert current["exists"] is True and current["provenance"] == "network"

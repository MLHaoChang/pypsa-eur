"""RP-02: the readiness feed for wind/solar generators.

`GET /api/projects/{name}/profiles/readiness` (read-only) and the chat tool
`get_profile_readiness` return one row per wind/solar generator:
coordinate state (the bus's, with PyPSA's 0/0 default as `missing`), the
profile it carries now (none / uploaded / generated / network_native) and
whether a generated one is stale (the bus moved, or the horizon changed).
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from harness.catalogue import TOOL_ROUTES, TOOLS, safety_tier_for
from services import chat_tools

SNAPS = pd.date_range("2025-01-01", periods=8760, freq="h", name="snapshot")
KEY = ("generators", "p_max_pu", "g_gen")


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(SNAPS)
    n.add("Bus", "B_ok", x=8.0, y=50.0)
    n.add("Bus", "B_null")  # PyPSA's 0/0 default: never placed
    n.add("Bus", "B_up", x=10.0, y=52.0)
    n.add("Carrier", ["wind", "solar", "gas"])
    n.add("Generator", "g_ok", bus="B_ok", carrier="wind", p_nom=10.0)
    n.add("Generator", "g_null", bus="B_null", carrier="solar", p_nom=10.0)
    n.add("Generator", "g_up", bus="B_up", carrier="solar PV", p_nom=10.0)
    n.add("Generator", "g_gas", bus="B_ok", carrier="gas", p_nom=10.0)
    return n


@pytest.fixture
def project(client, install_network):
    install_network(_network())
    resp = client.post("/api/projects/Ready Farm",
                       params={"force": True, "rebind": True})
    assert resp.status_code == 200, resp.text
    return "Ready Farm"


def _rows(client, project):
    resp = client.get(f"/api/projects/{project}/profiles/readiness")
    assert resp.status_code == 200, resp.text
    return {r["generator"]: r for r in resp.json()["generators"]}


@pytest.fixture
def ctx_of(session_ctx, client):
    return lambda: session_ctx(client)


def test_rows_match_the_fixture_exactly(client, project, ctx_of):
    ctx = ctx_of()
    ctx.user_ts[("generators", "p_max_pu", "g_up")] = pd.Series(
        0.3, index=SNAPS)  # an upload: a series with no generator record
    rows = _rows(client, project)
    assert set(rows) == {"g_ok", "g_null", "g_up"}  # the gas unit is not listed
    assert rows["g_ok"] == {
        "generator": "g_ok", "technology": "wind", "bus": "B_ok",
        "lon": 8.0, "lat": 50.0, "coord_source": "bus", "profile": "none",
    }
    assert rows["g_null"] == {
        "generator": "g_null", "technology": "solar", "bus": "B_null",
        "lon": None, "lat": None, "coord_source": "missing", "profile": "none",
    }
    assert rows["g_up"]["technology"] == "solar"
    assert rows["g_up"]["coord_source"] == "bus"
    assert rows["g_up"]["profile"] == "uploaded"
    assert "stale_reason" not in rows["g_up"]


def test_a_network_native_profile_is_named_so(client, project, ctx_of):
    n = ctx_of().network
    n.generators_t.p_max_pu["g_ok"] = 0.5
    assert _rows(client, project)["g_ok"]["profile"] == "network_native"


def _generated(ctx, point=(8.0, 50.0), year=2019):
    ctx.user_ts[KEY[:2] + ("g_ok",)] = pd.Series(0.4, index=SNAPS)
    ctx.user_ts_meta[KEY[:2] + ("g_ok",)] = {
        "source": "synthetic", "technology": "wind", "family": "renewable",
        "weather_year": year, "provenance": "synthetic/assumed",
        "points": ["g_ok"], "point_coords": {"g_ok": list(point)},
    }


def test_a_generated_profile_carries_its_provenance(client, project, ctx_of):
    _generated(ctx_of())
    row = _rows(client, project)["g_ok"]
    assert row["profile"] == "generated"
    assert row["provenance"] == {"source": "synthetic", "weather_year": 2019,
                                 "provenance": "synthetic/assumed"}
    assert "stale_reason" not in row


def test_moving_the_bus_after_generation_marks_it_stale(client, project, ctx_of):
    ctx = ctx_of()
    _generated(ctx)
    ctx.network.buses.loc["B_ok", ["x", "y"]] = [15.0, 45.0]
    row = _rows(client, project)["g_ok"]
    assert row["profile"] == "generated"
    assert row["stale_reason"] == "bus_moved"
    assert (row["lon"], row["lat"]) == (15.0, 45.0)


def test_changed_snapshots_mark_a_generated_profile_stale(client, project, ctx_of):
    ctx = ctx_of()
    _generated(ctx)
    ctx.network.set_snapshots(SNAPS[:4380])
    assert _rows(client, project)["g_ok"]["stale_reason"] == "horizon_changed"


def test_the_chat_tool_returns_the_same_rows(client, project, bound_ctx):
    assert "get_profile_readiness" in [t["name"] for t in TOOLS]
    assert safety_tier_for("get_profile_readiness") == "read"
    assert TOOL_ROUTES["get_profile_readiness"] == [
        ("GET", "/api/projects/{name}/profiles/readiness")]
    # A chat turn runs tools with the session's context bound.
    with bound_ctx(client):
        out = chat_tools.DISPATCHERS["get_profile_readiness"](project_id=project)
    via_route = client.get(f"/api/projects/{project}/profiles/readiness").json()
    assert out == via_route


def test_bus_moved_follows_the_applied_point_not_the_generator_name(
        client, project, ctx_of):
    # A two-point job whose ids are not generator names: "site_b" is applied
    # to g_ok. The row must know which point it came from (gatekeeper, #603).
    import time
    from services import solve_queue as sq
    import uuid

    resp = client.post(f"/api/projects/{project}/profiles/generate", json={
        "points": [{"id": "site_a", "lon": 20.0, "lat": 40.0},
                   {"id": "site_b", "lon": 8.0, "lat": 50.0}],
        "technology": "wind", "year": 2023})
    assert resp.status_code in (200, 202), resp.text
    job_id = resp.json()["id"]
    deadline = time.time() + 60
    while time.time() < deadline:
        job = sq.solve_queue.get_job(uuid.UUID(job_id))
        if job and job["status"] in sq._TERMINAL:
            break
        time.sleep(0.1)
    assert job["status"] == "completed", job.get("error")
    resp = client.post(f"/api/projects/{project}/profiles/apply", json={
        "job_id": job_id, "component": "g_ok", "point_id": "site_b"})
    assert resp.status_code == 200, resp.text
    row = _rows(client, project)["g_ok"]
    assert row["profile"] == "generated" and "stale_reason" not in row
    ctx_of().network.buses.loc["B_ok", ["x", "y"]] = [15.0, 45.0]
    assert _rows(client, project)["g_ok"]["stale_reason"] == "bus_moved"


def test_bus_moved_finds_a_generator_named_point_with_no_recorded_point_id(
        client, project, ctx_of):
    # Profiles applied before point_id was recorded, from a two-generator
    # from_generators job: the point ids ARE the generator names (gatekeeper).
    ctx = ctx_of()
    key = ("generators", "p_max_pu", "g_ok")
    ctx.user_ts[key] = pd.Series(0.4, index=SNAPS)
    ctx.user_ts_meta[key] = {
        "source": "synthetic", "technology": "wind", "family": "renewable",
        "weather_year": 2019, "provenance": "synthetic/assumed",
        "points": ["g_ok", "g_up"],
        "point_coords": {"g_ok": [8.0, 50.0], "g_up": [10.0, 52.0]},
    }
    assert "stale_reason" not in _rows(client, project)["g_ok"]
    ctx.network.buses.loc["B_ok", ["x", "y"]] = [15.0, 45.0]
    assert _rows(client, project)["g_ok"]["stale_reason"] == "bus_moved"

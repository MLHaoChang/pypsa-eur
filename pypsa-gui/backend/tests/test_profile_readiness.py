"""RP-02: the readiness feed for wind/solar generators.

Spec: .scratch/renewable-profiles/issues/02-readiness-feed.md (R1).
One row per wind/solar generator on the open network: where its
coordinates come from, what profile it has (none / uploaded / generated /
network-native), the generated profile's provenance, and whether that
provenance is stale (the bus moved or lost its coordinates since
generation). The Time Series Manager (RP-04), the chat (RP-08) and the
Guided offer (RP-10) all read it; it never writes.
"""
from __future__ import annotations

import time

import pandas as pd
import pypsa
import pytest

from services import chat_tools
from harness.catalogue import TOOL_ROUTES, TOOLS, safety_tier_for

SNAPS_2025 = pd.date_range("2025-01-01", periods=8760, freq="h")
NAME = "Ready Farm"


def _network() -> pypsa.Network:
    """g_wind on a placed bus; g_nocoord on PyPSA's 0/0 default; g_upload
    with a user-uploaded profile (set per test); g_native with a
    network-native p_max_pu; g_gas is not renewable and never listed."""
    n = pypsa.Network()
    idx = SNAPS_2025.copy()
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1", x=8.0, y=50.0)
    n.add("Bus", "B0")
    for c in ("wind", "solar", "gas"):
        n.add("Carrier", c)
    n.add("Generator", "g_wind", bus="B1", carrier="wind", p_nom=100.0)
    n.add("Generator", "g_nocoord", bus="B0", carrier="wind", p_nom=10.0)
    n.add("Generator", "g_upload", bus="B1", carrier="solar", p_nom=20.0)
    n.add("Generator", "g_native", bus="B1", carrier="solar", p_nom=20.0)
    n.add("Generator", "g_gas", bus="B1", carrier="gas", p_nom=50.0)
    return n


@pytest.fixture
def ready_project(client, install_network):
    install_network(_network())
    resp = client.post(f"/api/projects/{NAME}",
                       params={"force": True, "rebind": True})
    assert resp.status_code == 200, resp.text
    return NAME


def _rows(client) -> dict[str, dict]:
    resp = client.get(f"/api/projects/{NAME}/profiles/readiness")
    assert resp.status_code == 200, resp.text
    return {r["generator"]: r for r in resp.json()["generators"]}


def _upload(session_ctx, client):
    """g_upload gets a user-TS entry; g_native a p_max_pu column on the live
    network only. Saving a project moves its _t profiles into the user-TS
    store, so a network-native profile is one the store has not seen yet
    (an import or a direct edit) — once saved it reads as uploaded, the
    safe reading for a feed that must never offer to overwrite it."""
    ctx = session_ctx(client)
    ctx.user_ts[("generators", "p_max_pu", "g_upload")] = pd.Series(
        0.2, index=SNAPS_2025)
    ctx.network.generators_t.p_max_pu["g_native"] = 0.3


def _await(client, job_id, timeout=60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        jobs = client.get("/api/simulation/queue").json()
        rows = jobs["jobs"] if isinstance(jobs, dict) else jobs
        for row in rows:
            if row.get("id") == job_id and row["status"] in (
                    "completed", "failed", "aborted"):
                return row
        time.sleep(0.1)
    raise AssertionError("profile job did not finish")


def test_every_wind_and_solar_generator_gets_one_row(client, ready_project,
                                                     session_ctx):
    _upload(session_ctx, client)
    rows = _rows(client)
    assert set(rows) == {"g_wind", "g_nocoord", "g_upload", "g_native"}
    assert rows["g_wind"]["technology"] == "wind"
    assert rows["g_upload"]["technology"] == "solar"


def test_coordinates_come_from_the_bus_or_are_missing(client, ready_project):
    rows = _rows(client)
    assert rows["g_wind"]["coord_source"] == "bus"
    assert (rows["g_wind"]["lon"], rows["g_wind"]["lat"]) == (8.0, 50.0)
    assert rows["g_nocoord"]["coord_source"] == "missing"
    assert rows["g_nocoord"]["lon"] is None and rows["g_nocoord"]["lat"] is None


def test_profile_state_distinguishes_none_uploaded_and_network_native(
    client, ready_project, session_ctx,
):
    _upload(session_ctx, client)
    rows = _rows(client)
    assert rows["g_wind"]["profile"] == "none"
    assert rows["g_upload"]["profile"] == "uploaded"
    assert rows["g_native"]["profile"] == "network_native"
    assert rows["g_wind"]["provenance"] is None
    assert rows["g_wind"]["stale_reason"] is None


def _generate_and_apply(client):
    gen = client.post(
        f"/api/projects/{NAME}/profiles/generate",
        json={"from_generators": ["g_wind"], "technology": "wind",
              "year": 2023, "source": "synthetic"},
    )
    assert gen.status_code == 202, gen.text
    done = _await(client, gen.json()["id"])
    assert done["status"] == "completed", done["error"]
    applied = client.post(
        f"/api/projects/{NAME}/profiles/apply",
        json={"job_id": gen.json()["id"], "component": "g_wind"},
    )
    assert applied.status_code == 200, applied.text


def test_a_generated_profile_carries_its_provenance(
    client, ready_project, project_storage_dir, seeded_identity,
):
    _generate_and_apply(client)
    row = _rows(client)["g_wind"]
    assert row["profile"] == "generated"
    prov = row["provenance"]
    assert prov["source"] == "synthetic"
    assert prov["weather_year"] == 2023
    assert prov["technology"] == "wind"
    assert "time_basis" in prov
    assert row["stale_reason"] is None


def test_a_bus_moved_since_generation_marks_the_profile_stale(
    client, ready_project, project_storage_dir, seeded_identity, session_ctx,
):
    _generate_and_apply(client)
    session_ctx(client).network.buses.loc["B1", "x"] = 12.5
    assert _rows(client)["g_wind"]["stale_reason"] == "bus_moved"


def test_a_bus_that_lost_its_coordinates_marks_the_profile_stale(
    client, ready_project, project_storage_dir, seeded_identity, session_ctx,
):
    _generate_and_apply(client)
    session_ctx(client).network.buses.loc["B1", ["x", "y"]] = [0.0, 0.0]
    row = _rows(client)["g_wind"]
    assert row["stale_reason"] == "bus_missing_coordinates"
    assert row["coord_source"] == "missing"


def test_readiness_on_another_orgs_project_is_a_404(other_org_client):
    resp = other_org_client.get(f"/api/projects/{NAME}/profiles/readiness")
    assert resp.status_code == 404


def test_the_chat_tool_returns_the_same_rows(client, ready_project,
                                             session_ctx, bound_ctx):
    _upload(session_ctx, client)
    with bound_ctx(client):
        out = chat_tools.DISPATCHERS["get_profile_readiness"](
            project_id=NAME)
    rows = {r["generator"]: r for r in out["generators"]}
    assert rows == _rows(client)


def test_the_chat_tool_is_catalogued_as_a_read():
    assert "get_profile_readiness" in {t["name"] for t in TOOLS}
    assert safety_tier_for("get_profile_readiness") == "read"
    assert TOOL_ROUTES["get_profile_readiness"] == [
        ("GET", "/api/projects/{name}/profiles/readiness")]

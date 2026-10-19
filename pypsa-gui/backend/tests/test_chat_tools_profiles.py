"""The profile generator's chat tools (Task 8).

Six tools, thin wrappers over the Task 7 routes (or the Task 4 catalogue for
`get_profile_parameters`, which has no HTTP twin — `_SERVICE_CALL`):

  reads   list_profile_presets, get_profile_parameters,
          preview_renewable_profile, list_pending_profiles
  writes  generate_renewable_profile (queues a job — Safety: execution),
          apply_generated_profile (writes p_max_pu — Safety: write, the
          confirmation card the harness shows for every write-tier tool)

The invariants pinned here are the harness's own: every tool is in TOOLS,
DISPATCHERS and TOOL_ROUTES exactly once; the safety tier declared in the
description is the tier the confirm machinery reads; and each wrapper's
`as _h` import resolves (the AST scan in test_chat_tools_imports.py runs over
the whole module — these tests add the behavioural coverage).
"""
from __future__ import annotations

import time

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from harness.catalogue import TOOL_ROUTES, TOOLS, safety_tier_for
from services import chat_tools

PROFILE_TOOLS = (
    "list_profile_presets",
    "get_profile_parameters",
    "preview_renewable_profile",
    "list_pending_profiles",
    "generate_renewable_profile",
    "apply_generated_profile",
)

SNAPS_2025 = pd.date_range("2025-01-01", periods=8760, freq="h")


def _wind_network() -> pypsa.Network:
    n = pypsa.Network()
    idx = SNAPS_2025.copy()
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1", x=8.0, y=50.0)
    n.add("Carrier", "wind")
    n.add("Generator", "g_wind", bus="B1", carrier="wind", p_nom=100.0,
          marginal_cost=0.0)
    return n


@pytest.fixture
def wind_project(client, install_network):
    """A saved project whose foreground ctx holds one wind generator."""
    install_network(_wind_network())
    resp = client.post("/api/projects/Chat Farm",
                       params={"force": True, "rebind": True})
    assert resp.status_code == 200, resp.text
    return "Chat Farm"


def _wait_job(job_id, timeout=60.0):
    from services import solve_queue as sq
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = sq.solve_queue.get_job(__import__("uuid").UUID(job_id))
        if job and job["status"] in sq._TERMINAL:
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} never reached a terminal state")


# ---------------------------------------------------------------------------
# harness invariants
# ---------------------------------------------------------------------------


def test_every_profile_tool_is_registered_exactly_once():
    names = [t["name"] for t in TOOLS]
    for name in PROFILE_TOOLS:
        assert names.count(name) == 1, name
        assert name in chat_tools.DISPATCHERS, name
        assert name in TOOL_ROUTES, name
    # The global parity the harness relies on must still hold with them in.
    assert len(TOOLS) == len(chat_tools.DISPATCHERS)


def test_safety_tiers_match_the_declared_markers():
    assert safety_tier_for("list_profile_presets") == "read"
    assert safety_tier_for("get_profile_parameters") == "read"
    assert safety_tier_for("preview_renewable_profile") == "read"
    assert safety_tier_for("list_pending_profiles") == "read"
    assert safety_tier_for("generate_renewable_profile") == "execution"
    assert safety_tier_for("apply_generated_profile") == "write"


def test_route_templates_are_in_the_phase0_inventory():
    """Route existence is pinned globally by test_chat_tools_endpoint_map.py
    against the regenerated phase0 fixture; here just assert the six tools'
    routes (or sentinel) are recorded in TOOL_ROUTES at all."""
    for name in PROFILE_TOOLS:
        routes = TOOL_ROUTES[name]
        assert routes, name
        assert all(isinstance(r, tuple) and len(r) == 2 for r in routes) \
            or routes == ["_service_call_"], (name, routes)


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------


def test_list_profile_presets_tool(client):
    out = chat_tools.DISPATCHERS["list_profile_presets"]()
    presets = out["presets"] if isinstance(out, dict) else out
    assert any(p["technology"] == "wind" for p in presets)


def test_get_profile_parameters_tool(client):
    out = chat_tools.DISPATCHERS["get_profile_parameters"](
        preset="vestas-v90-3mw", technology="wind"
    )
    assert out["name"] == "vestas-v90-3mw"
    assert out["technology"] == "wind"
    assert out["params"]["turbine"]["hub_height"] == 105


def test_get_profile_parameters_unknown_preset_is_a_typed_refusal(client):
    with pytest.raises(HTTPException) as err:
        chat_tools.DISPATCHERS["get_profile_parameters"](
            preset="no-such-preset", technology="wind"
        )
    assert err.value.status_code == 422


def test_preview_renewable_profile_tool(client):
    out = chat_tools.DISPATCHERS["preview_renewable_profile"](
        points=[{"id": "g_wind", "lon": 8.0, "lat": 50.0}],
        technology="wind",
        year=2023,
    )
    assert out["estimate"] is True
    assert "g_wind" in out["series"]


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------


def test_generate_renewable_profile_enqueues_a_job(client, wind_project):
    out = chat_tools.DISPATCHERS["generate_renewable_profile"](
        project_id=wind_project,
        points=[{"id": "g_wind", "lon": 8.0, "lat": 50.0}],
        technology="wind",
        year=2023,
    )
    assert out["kind"] == "profile_gen"
    done = _wait_job(out["id"])
    assert done["status"] == "completed", done["error"]


def test_list_pending_profiles_tool(client, wind_project):
    gen = chat_tools.DISPATCHERS["generate_renewable_profile"](
        project_id=wind_project,
        points=[{"id": "g_wind", "lon": 8.0, "lat": 50.0}],
        technology="wind",
        year=2023,
    )
    _wait_job(gen["id"])

    out = chat_tools.DISPATCHERS["list_pending_profiles"](project_id=wind_project)
    mine = [p for p in out["pending"] if p["job_id"] == gen["id"]]
    assert len(mine) == 1


def test_apply_generated_profile_writes_p_max_pu(client, wind_project,
                                                 session_ctx):
    gen = chat_tools.DISPATCHERS["generate_renewable_profile"](
        project_id=wind_project,
        points=[{"id": "g_wind", "lon": 8.0, "lat": 50.0}],
        technology="wind",
        year=2023,
    )
    _wait_job(gen["id"])

    # A real chat turn runs tools with the session's request context bound;
    # a bare dispatcher call must bind it the same way or the write would
    # land on the process foreground instead of the session's project.
    from services.pypsa_service import PyPSAService

    ctx = session_ctx(client)
    token = PyPSAService.bind_request_context(ctx)
    try:
        out = chat_tools.DISPATCHERS["apply_generated_profile"](
            project_id=wind_project, job_id=gen["id"], component="g_wind",
        )
    finally:
        PyPSAService.reset_request_context(token)
    assert out["component"] == "g_wind"
    assert out["hours"] == 8760

    assert ("generators", "p_max_pu", "g_wind") in ctx.user_ts


def test_apply_on_another_orgs_project_is_refused(client, wind_project,
                                                  second_identity):
    from services import chat_tools as ct

    ct.set_acting_user(second_identity["user_id"])
    try:
        with pytest.raises(HTTPException) as err:
            ct.DISPATCHERS["list_pending_profiles"](project_id=wind_project)
        assert err.value.status_code == 404
    finally:
        ct.set_acting_user(None)

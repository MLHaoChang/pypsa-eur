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
                                                 bound_ctx):
    gen = chat_tools.DISPATCHERS["generate_renewable_profile"](
        project_id=wind_project,
        points=[{"id": "g_wind", "lon": 8.0, "lat": 50.0}],
        technology="wind",
        year=2023,
    )
    _wait_job(gen["id"])

    # A real chat turn runs tools with the session's request context bound;
    # the bound_ctx fixture binds it the same way, or the write would land on
    # the process foreground instead of the session's project.
    with bound_ctx(client) as ctx:
        out = chat_tools.DISPATCHERS["apply_generated_profile"](
            project_id=wind_project, job_id=gen["id"], component="g_wind",
        )
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


# ---------------------------------------------------------------------------
# C2: resolution passthrough
# ---------------------------------------------------------------------------


def test_preview_tool_passes_resolution_through(client):
    out = chat_tools.DISPATCHERS["preview_renewable_profile"](
        points=[{"id": "g_wind", "lon": 8.0, "lat": 50.0}],
        technology="wind",
        year=2023,
        resolution="weekly",
    )
    assert out["estimate"] is True
    assert out["resolution"] == "weekly"
    assert len(out["series"]["g_wind"]) == 53


def test_generate_tool_passes_resolution_through(client, wind_project):
    out = chat_tools.DISPATCHERS["generate_renewable_profile"](
        project_id=wind_project,
        points=[{"id": "g_wind", "lon": 8.0, "lat": 50.0}],
        technology="wind",
        year=2023,
        resolution="daily",
    )
    assert out["kind"] == "profile_gen"
    done = _wait_job(out["id"])
    assert done["status"] == "completed", done["error"]

    pending = chat_tools.DISPATCHERS["list_pending_profiles"](
        project_id=wind_project,
    )
    mine = [p for p in pending["pending"] if p["job_id"] == out["id"]]
    assert mine and mine[0]["resolution"] == "daily"


def test_resolution_schemas_are_declared_in_the_catalogue():
    schemas = {t["name"]: t for t in TOOLS}
    for name in ("preview_renewable_profile", "generate_renewable_profile"):
        props = schemas[name]["input_schema"]["properties"]
        assert props["resolution"]["enum"] == ["hourly", "daily", "weekly"], \
            name


# ---------------------------------------------------------------------------
# C3: from_generators — resolve generator names to their bus coordinates
# ---------------------------------------------------------------------------


def test_preview_from_generators_matches_explicit_points(client, wind_project,
                                                         bound_ctx):
    """from_generators=["g_wind"] must resolve to the bus coordinates
    (8.0, 50.0): the deterministic synthetic source then yields exactly the
    series an explicit-points call produces."""
    explicit = chat_tools.DISPATCHERS["preview_renewable_profile"](
        points=[{"id": "g_wind", "lon": 8.0, "lat": 50.0}],
        technology="wind", year=2023,
    )
    with bound_ctx(client):
        resolved = chat_tools.DISPATCHERS["preview_renewable_profile"](
            from_generators=["g_wind"],
            technology="wind", year=2023,
        )
    assert resolved["series"]["g_wind"] == explicit["series"]["g_wind"]


def test_from_generators_unknown_name_is_a_typed_refusal(client, wind_project,
                                                         bound_ctx):
    with bound_ctx(client):
        with pytest.raises(HTTPException) as err:
            chat_tools.DISPATCHERS["preview_renewable_profile"](
                from_generators=["no_such_gen"], technology="wind",
            )
    assert err.value.status_code == 422
    assert err.value.detail["error_kind"] == "unknown_generator"
    assert "no_such_gen" in err.value.detail["message"]


def test_neither_points_nor_from_generators_is_a_typed_refusal(client):
    with pytest.raises(HTTPException) as err:
        chat_tools.DISPATCHERS["preview_renewable_profile"](technology="wind")
    assert err.value.status_code == 422
    assert err.value.detail["error_kind"] == "no_points"


def test_generate_from_generators_records_the_bus_coordinates(
    client, wind_project, bound_ctx,
):
    with bound_ctx(client):
        out = chat_tools.DISPATCHERS["generate_renewable_profile"](
            project_id=wind_project,
            from_generators=["g_wind"],
            technology="wind",
            year=2023,
        )
    assert out["kind"] == "profile_gen"
    done = _wait_job(out["id"])
    assert done["status"] == "completed", done["error"]

    pending = chat_tools.DISPATCHERS["list_pending_profiles"](
        project_id=wind_project,
    )
    mine = [p for p in pending["pending"] if p["job_id"] == out["id"]]
    assert len(mine) == 1
    assert mine[0]["points"] == ["g_wind"]
    coords = mine[0]["point_coords"]["g_wind"]
    assert coords == [8.0, 50.0]


def test_from_generators_schema_is_declared_in_the_catalogue():
    schemas = {t["name"]: t for t in TOOLS}
    for name in ("preview_renewable_profile", "generate_renewable_profile"):
        entry = schemas[name]
        props = entry["input_schema"]["properties"]
        assert props["from_generators"]["items"]["type"] == "string", name
        assert "points" not in entry["input_schema"]["required"], name


# ---------------------------------------------------------------------------
# Gatekeeper review: the time basis rides the chat tool, and the chat apply
# path is undoable exactly like the route (it pushes the snapshot first)
# ---------------------------------------------------------------------------


def test_apply_generated_profile_takes_a_time_basis_and_undoes(
    client, wind_project, session_ctx,
):
    gen = chat_tools.DISPATCHERS["generate_renewable_profile"](
        project_id=wind_project,
        points=[{"id": "g_wind", "lon": 8.0, "lat": 50.0}],
        technology="wind",
        year=2023,
    )
    _wait_job(gen["id"])
    from services.pypsa_service import PyPSAService

    ctx = session_ctx(client)
    token = PyPSAService.bind_request_context(ctx)
    try:
        out = chat_tools.DISPATCHERS["apply_generated_profile"](
            project_id=wind_project, job_id=gen["id"], component="g_wind",
            time_basis="Europe/Berlin",
        )
    finally:
        PyPSAService.reset_request_context(token)
    assert out["time_basis"] == "Europe/Berlin"
    key = ("generators", "p_max_pu", "g_wind")
    assert ctx.user_ts_meta[key]["time_basis"] == "Europe/Berlin"

    undo = client.post("/api/network/undo")
    assert undo.status_code == 200, undo.text
    payload = client.get("/api/network/timeseries/generators/p_max_pu").json()
    assert "g_wind" not in payload["columns"]


# ---------------------------------------------------------------------------
# RP-01: a generator whose bus has no coordinates is a typed refusal the
# copilot can relay with both remedies — never a profile for 0°E 0°N
# ---------------------------------------------------------------------------


def test_generate_for_a_bus_without_coordinates_is_a_typed_refusal(
    client, install_network, bound_ctx,
):
    n = _wind_network()
    n.add("Bus", "B0")  # x = y = 0, PyPSA's default
    n.add("Generator", "g_nocoord", bus="B0", carrier="wind", p_nom=10.0)
    install_network(n)
    resp = client.post("/api/projects/Chat Coords",
                       params={"force": True, "rebind": True})
    assert resp.status_code == 200, resp.text

    with bound_ctx(client):
        with pytest.raises(HTTPException) as err:
            chat_tools.DISPATCHERS["generate_renewable_profile"](
                project_id="Chat Coords",
                from_generators=["g_wind", "g_nocoord"],
                technology="wind", year=2023,
            )
    assert err.value.status_code == 422
    detail = err.value.detail
    assert detail["error_kind"] == "missing_coordinates"
    assert detail["generators"] == ["g_nocoord"]
    assert "set coordinates on the bus" in detail["message"]
    assert "upload a profile" in detail["message"]

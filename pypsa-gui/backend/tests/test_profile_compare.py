"""G-a: read-only, server-computed profile comparisons on model snapshots."""

from __future__ import annotations

import json
from pathlib import Path
import uuid

import pandas as pd
import pypsa
import pytest

SNAPS = pd.date_range("2025-01-01", periods=8760, freq="h", name="snapshot")


@pytest.fixture
def project(client, install_network, session_ctx):
    n = pypsa.Network()
    n.set_snapshots(SNAPS)
    n.add("Bus", "B", x=8.0, y=50.0)
    n.add("Carrier", "wind")
    n.add("Generator", ["g_a", "g_c"], bus="B", carrier="wind", p_nom=10.0)
    install_network(n)
    res = client.post(
        "/api/projects/Compare Farm", params={"force": True, "rebind": True}
    )
    assert res.status_code == 200, res.text
    session_ctx(client).network.generators_t.p_max_pu["g_a"] = 0.30
    return "Compare Farm"


def _pending(ctx, value=0.4, point="g_a", **extra):
    from services.profile_gen.job import write_pending

    job = str(uuid.uuid4())
    series = value if isinstance(value, pd.Series) else pd.Series(value, index=SNAPS)
    meta = {
        "source": "synthetic",
        "technology": "wind",
        "family": "renewable",
        "weather_year": 2025,
        "resolution": "hourly",
        "points": [point],
        "job_id": job,
        "generated_at": "2026-01-01T00:00:00+00:00",
        **extra,
    }
    write_pending(ctx.storage_dir, job, series.to_frame(point), meta)
    return job


def _compare(client, project, a, b, **extra):
    res = client.get(
        f"/api/projects/{project}/profiles/compare", params={"a": a, "b": b, **extra}
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_the_flh_delta_matches_a_hand_calculation(
    client, project, session_ctx, record_property
):
    job = _pending(session_ctx(client))
    got = _compare(client, project, "current:g_a", f"pending:{job}:g_a")
    record_property("compare_response", json.dumps(got))
    assert got["a"]["flh"] == 2628.0
    assert got["b"]["flh"] == 3504.0
    assert got["delta"]["flh"] == 876.0
    assert got["delta"]["cf"] == 0.1
    assert len(got["a"]["duration"]) == len(got["b"]["duration"]) == 100
    assert got["a"]["duration"] == [0.3] * 100
    assert got["b"]["duration"] == [0.4] * 100
    assert got["a"]["monthly"] == {str(m): 0.3 for m in range(1, 13)}


def test_a_profile_compared_with_itself_has_zero_deltas(client, project):
    import time
    from services import solve_queue as sq

    res = client.post(
        f"/api/projects/{project}/profiles/generate",
        json={
            "from_generators": ["g_c"],
            "technology": "wind",
            "source": "synthetic",
            "year": 2025,
        },
    )
    assert res.status_code == 202, res.text
    jid = res.json()["id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = sq.solve_queue.get_job(uuid.UUID(jid))
        if job and job["status"] in sq._TERMINAL:
            break
        time.sleep(0.05)
    assert job["status"] == "completed", job
    out = _compare(client, project, f"pending:{jid}:g_c", f"pending:{jid}:g_c")
    assert all(v == 0.0 for k, v in out["delta"].items() if k != "monthly")
    assert all(v == 0.0 for v in out["delta"]["monthly"].values())
    assert out["a"]["duration"] == out["b"]["duration"]


def test_the_current_side_reads_what_the_generator_carries(
    client, project, session_ctx
):
    ctx = session_ctx(client)
    assert _compare(client, project, "current:g_a", "current:g_a")["a"]["cf"] == 0.3
    missing = client.get(
        f"/api/projects/{project}/profiles/compare",
        params={"a": "current:g_c", "b": "current:g_a"},
    )
    assert (
        missing.status_code == 422
        and missing.json()["detail"]["error_kind"] == "no_current_series"
    )
    jid = _pending(ctx, 0.6)
    applied = client.post(
        f"/api/projects/{project}/profiles/apply",
        json={"job_id": jid, "component": "g_a"},
    )
    assert applied.status_code == 200, applied.text
    assert _compare(client, project, "current:g_a", "current:g_a")["a"]["cf"] == 0.6
    # Explicit user series wins over a different native column.
    ctx.network.generators_t.p_max_pu["g_a"] = 0.2
    assert _compare(client, project, "current:g_a", "current:g_a")["a"]["cf"] == 0.6


def test_a_shaped_side_must_be_calibrated(client, project, session_ctx):
    jid = _pending(session_ctx(client))
    not_shaped = client.get(
        f"/api/projects/{project}/profiles/compare",
        params={"a": "current:g_a", "b": f"shaped:{jid}"},
    )
    assert not_shaped.status_code == 422
    assert not_shaped.json()["detail"]["error_kind"] == "not_a_shaped_profile"
    res = client.post(
        f"/api/projects/{project}/profiles/shape",
        json={"job_id": jid, "ops": [{"op": "target_cf", "cf": 0.5}]},
    )
    assert res.status_code == 200, res.text
    shaped = res.json()["job_id"]
    assert (
        _compare(client, project, "current:g_a", f"shaped:{shaped}")["b"]["cf"] == 0.5
    )


@pytest.mark.parametrize(
    "side,code,kind",
    [
        ("nonsense", 422, "invalid_compare_side"),
        ("pending:", 422, "invalid_compare_side"),
        ("current:", 422, "invalid_compare_side"),
        ("current:g_a:extra", 422, "invalid_compare_side"),
        ("pending:../x", 422, "malformed_job_id"),
        ("pending:unknown", 404, None),
    ],
)
def test_malformed_and_foreign_sides_are_refused(client, project, side, code, kind):
    res = client.get(
        f"/api/projects/{project}/profiles/compare",
        params={"a": "current:g_a", "b": side},
    )
    assert res.status_code == code, res.text
    if kind:
        assert res.json()["detail"]["error_kind"] == kind


def test_another_projects_artifact_is_not_visible(client, project, session_ctx):
    jid = _pending(session_ctx(client))
    res = client.post(
        "/api/projects/Other Farm", params={"force": True, "rebind": True}
    )
    assert res.status_code == 200, res.text
    res = client.get(
        "/api/projects/Other Farm/profiles/compare",
        params={"a": "current:g_a", "b": f"pending:{jid}"},
    )
    assert res.status_code == 404, res.text


def test_compare_rechecks_access_and_foreground(
    client, other_org_client, project, session_ctx
):
    params = {"a": "current:g_a", "b": "current:g_a"}
    foreign = other_org_client.get(
        f"/api/projects/{project}/profiles/compare", params=params
    )
    assert foreign.status_code == 404, foreign.text
    session_ctx(client).loaded_project = "Other Farm"
    res = client.get(f"/api/projects/{project}/profiles/compare", params=params)
    assert (
        res.status_code == 409
        and res.json()["detail"]["error_kind"] == "project_not_open"
    )


def test_the_chat_tool_returns_the_route_payload(client, project, bound_ctx):
    from harness.catalogue import TOOL_ROUTES, TOOLS, safety_tier_for
    from services import chat_tools

    assert "compare_profiles" in [t["name"] for t in TOOLS]
    assert safety_tier_for("compare_profiles") == "read"
    assert TOOL_ROUTES["compare_profiles"] == [
        ("GET", "/api/projects/{name}/profiles/compare")
    ]
    with bound_ctx(client):
        out = chat_tools.DISPATCHERS["compare_profiles"](
            project_id=project, a="current:g_a", b="current:g_a"
        )
    assert out == _compare(client, project, "current:g_a", "current:g_a")


def test_compare_writes_nothing(client, project, session_ctx):
    ctx = session_ctx(client)
    jid = _pending(ctx)
    ctx.user_ts[("generators", "p_max_pu", "g_c")] = pd.Series(0.2, index=SNAPS)
    before = {key: value.copy(deep=True) for key, value in ctx.user_ts.items()}
    pending = Path(ctx.storage_dir) / "profiles_pending"
    files = {p.name: p.read_bytes() for p in pending.iterdir()}
    depth = client.get("/api/network/undo/info").json()["depth"]
    revision = ctx.network_revision
    for _ in range(2):
        _compare(client, project, "current:g_a", f"pending:{jid}")
    assert ctx.network_revision == revision
    assert client.get("/api/network/undo/info").json()["depth"] == depth
    assert set(ctx.user_ts) == set(before)
    for key, value in before.items():
        pd.testing.assert_series_equal(ctx.user_ts[key], value)
    assert {p.name: p.read_bytes() for p in pending.iterdir()} == files


def test_a_sampled_week_compares_on_its_own_snapshots(client, project, session_ctx):
    ctx = session_ctx(client)
    ctx.network.set_snapshots(pd.date_range("2030-07-01", periods=168, freq="h"))
    ctx.network.snapshot_weightings.loc[:, "generators"] = 8760 / 168
    ctx.network.generators_t.p_max_pu["g_a"] = 0.3
    jid = _pending(ctx)
    out = _compare(client, project, "current:g_a", f"pending:{jid}")
    assert len(out["week"]["snapshots"]) == 168
    assert out["b"]["flh"] == 3504.0
    assert out["b"]["monthly"] == {"7": 0.4}
    assert len(out["b"]["duration"]) == 100


def test_a_multi_period_model_compares_on_its_own_snapshots(
    client, project, session_ctx
):
    ctx = session_ctx(client)
    snaps = pd.MultiIndex.from_tuples(
        [
            (y, t)
            for y in (2030, 2040)
            for t in pd.date_range(f"{y}-07-01", periods=168, freq="h")
        ],
        names=["period", "timestep"],
    )
    ctx.network.set_snapshots(snaps)
    ctx.network.snapshot_weightings.loc[:, "generators"] = 8760 / 168
    ctx.network.generators_t.p_max_pu["g_a"] = [0.3] * 168 + [0.5] * 168
    out = _compare(client, project, "current:g_a", "current:g_a")
    assert out["a"]["flh"] == 3504.0 and out["a"]["cf"] == 0.4
    assert out["a"]["monthly"] == {"7": 0.4}
    assert all(v == 0 for k, v in out["delta"].items() if k != "monthly")
    assert out["week"]["period"] == 2040
    assert all(t.startswith("2040-") for t in out["week"]["snapshots"])
    jid = _pending(ctx)
    assert (
        _compare(client, project, "current:g_a", f"pending:{jid}")["b"]["flh"] == 3504.0
    )


def test_the_cf_uses_generator_snapshot_weights(client, project, session_ctx):
    ctx = session_ctx(client)
    ctx.network.generators_t.p_max_pu["g_a"] = [0.2] * 4380 + [0.6] * 4380
    ctx.network.snapshot_weightings.loc[:, "generators"] = [1.0] * 4380 + [3.0] * 4380
    out = _compare(client, project, "current:g_a", "current:g_a")
    assert out["a"]["cf"] == 0.5 and out["a"]["flh"] == 4380.0


def test_the_overlay_uses_the_candidates_calendar_peak_week(
    client, project, session_ctx
):
    candidate = pd.Series(0.4, index=SNAPS)
    candidate.loc["2025-03-19 14:00"] = 0.9
    jid = _pending(session_ctx(client), candidate)
    out = _compare(client, project, "current:g_a", f"pending:{jid}")
    assert out["week"]["snapshots"][0] == "2025-03-17T00:00:00"
    assert out["week"]["snapshots"][-1] == "2025-03-23T23:00:00"
    assert len(out["week"]["snapshots"]) == 168
    assert max(out["week"]["b"]) == 0.9 and set(out["week"]["a"]) == {0.3}
    assert out["b"]["peak_hour"] == 14
    assert out["b"]["duration"] == sorted(out["b"]["duration"], reverse=True)


@pytest.mark.parametrize("weight", [-1.0, 0.0, float("nan")])
def test_invalid_model_weights_are_refused(client, project, session_ctx, weight):
    session_ctx(client).network.snapshot_weightings.loc[:, "generators"] = weight
    res = client.get(
        f"/api/projects/{project}/profiles/compare",
        params={"a": "current:g_a", "b": "current:g_a"},
    )
    assert res.status_code == 422, res.text
    assert "weight" in res.json()["detail"]["message"]

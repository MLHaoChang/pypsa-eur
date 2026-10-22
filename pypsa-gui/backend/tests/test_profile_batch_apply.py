"""RP-09: apply several generated points in one validated, single-undo write.

`POST /api/projects/{name}/profiles/apply-batch` validates every member first
(all or nothing, nothing written on a refusal), pushes ONE undo snapshot, then
writes each through `assign_profile`. A write that fails midway is a typed
409 `batch_partially_applied`; one Undo reverts the batch.
"""
from __future__ import annotations

import time
import uuid

import pandas as pd
import pypsa
import pytest

from harness.catalogue import TOOL_ROUTES, TOOLS, safety_tier_for
from services import chat_tools

SNAPS = pd.date_range("2025-01-01", periods=8760, freq="h", name="snapshot")
POINTS = [{"id": "g_a", "lon": 8.0, "lat": 50.0},
          {"id": "g_b", "lon": 9.0, "lat": 51.0},
          {"id": "g_c", "lon": 10.0, "lat": 52.0}]


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(SNAPS)
    n.add("Bus", "B1", x=8.0, y=50.0)
    n.add("Carrier", ["wind", "gas"])
    for g in ("g_a", "g_b"):
        n.add("Generator", g, bus="B1", carrier="wind", p_nom=10.0)
    n.add("Generator", "g_c", bus="B1", carrier="wind", p_nom=10.0)
    n.add("Generator", "g_gas", bus="B1", carrier="gas", p_nom=10.0)
    # g_a already carries a network-native p_max_pu column.
    n.generators_t.p_max_pu["g_a"] = 0.9
    return n


@pytest.fixture
def project(client, install_network):
    install_network(_network())
    resp = client.post("/api/projects/Batch Farm",
                       params={"force": True, "rebind": True})
    assert resp.status_code == 200, resp.text
    return "Batch Farm"


def _job(client, project, points=POINTS) -> str:
    from services import solve_queue as sq

    resp = client.post(f"/api/projects/{project}/profiles/generate", json={
        "points": points, "technology": "wind", "year": 2023})
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


def _batch(client, project, job_id, members, **kw):
    return client.post(f"/api/projects/{project}/profiles/apply-batch",
                       json={"job_id": job_id, "members": members, **kw})


def _stored(ctx, name):
    return ("generators", "p_max_pu", name) in ctx.user_ts


def test_a_carrier_mismatch_refuses_the_whole_batch(client, project, session_ctx):
    job_id = _job(client, project, [*POINTS[:2], {"id": "g_gas", "lon": 8.0, "lat": 50.0}])
    depth = client.get("/api/network/undo/info").json()["depth"]
    before = session_ctx(client).user_ts.get(("generators", "p_max_pu", "g_a"))
    resp = _batch(client, project, job_id,
                  [{"component": "g_a"}, {"component": "g_b"}, {"component": "g_gas"}])
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "batch_refused"
    assert [m["component"] for m in detail["members"]] == ["g_gas"], detail["members"]
    ctx = session_ctx(client)
    assert not any(_stored(ctx, g) for g in ("g_b", "g_gas"))
    after = ctx.user_ts.get(("generators", "p_max_pu", "g_a"))
    assert (after is None) == (before is None)
    assert before is None or after.equals(before)  # the native g_a is untouched
    assert client.get("/api/network/undo/info").json()["depth"] == depth


def test_three_valid_members_write_and_one_undo_restores_all(
        client, project, session_ctx):
    job_id = _job(client, project)
    resp = _batch(client, project, job_id, [{"component": c} for c in ("g_a", "g_b", "g_c")])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 3
    assert [a["component"] for a in body["applied"]] == ["g_a", "g_b", "g_c"]
    assert all("before" in a for a in body["applied"])
    ctx = session_ctx(client)
    assert all(_stored(ctx, g) for g in ("g_a", "g_b", "g_c"))
    assert client.post("/api/network/undo").status_code == 200
    ctx = session_ctx(client)
    cols = client.get("/api/network/timeseries/generators/p_max_pu").json()["columns"]
    assert "g_b" not in cols and "g_c" not in cols
    assert "g_a" in cols  # the network-native column is back, as it was


def test_duplicate_and_empty_member_lists_are_refused(client, project):
    job_id = _job(client, project)
    dup = _batch(client, project, job_id, [{"component": "g_a"}, {"component": "g_a"}])
    assert dup.status_code == 422
    assert dup.json()["detail"]["members"][0]["error_kind"] == "duplicate_member"
    empty = _batch(client, project, job_id, [])
    assert empty.status_code == 422


def test_a_failure_midway_is_a_typed_partial_write(client, project, session_ctx,
                                                   monkeypatch):
    from routers import profiles
    real = profiles.assign_profile
    calls = []

    def flaky(ctx, component, *a, **kw):
        calls.append(component)
        if len(calls) == 2:
            raise RuntimeError("disk full")
        return real(ctx, component, *a, **kw)

    monkeypatch.setattr(profiles, "assign_profile", flaky)
    job_id = _job(client, project)
    resp = _batch(client, project, job_id, [{"component": c} for c in ("g_a", "g_b", "g_c")])
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "batch_partially_applied"
    assert detail["applied"] == ["g_a"]
    assert [m["component"] for m in detail["not_applied"]] == ["g_b", "g_c"]
    assert "Undo" in detail["message"]
    assert "disk full" not in str(detail)  # a raw exception never reaches the model


def test_all_from_job_lists_what_it_left_out(client, project):
    job_id = _job(client, project, [*POINTS[:2], {"id": "site_x", "lon": 8.0, "lat": 50.0}])
    resp = client.post(f"/api/projects/{project}/profiles/apply-batch",
                       json={"job_id": job_id, "all_from_job": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [a["component"] for a in body["applied"]] == ["g_a", "g_b"]
    assert [s["point_id"] for s in body["skipped"]] == ["site_x"]


def test_all_from_job_with_nothing_to_apply_is_refused_by_name(client, project):
    job_id = _job(client, project, [{"id": "site_x", "lon": 8.0, "lat": 50.0}])
    resp = client.post(f"/api/projects/{project}/profiles/apply-batch",
                       json={"job_id": job_id, "all_from_job": True})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_kind"] == "no_members"


def test_the_chat_tool_is_a_write_with_one_card(client, project, bound_ctx):
    assert "apply_generated_profiles" in [t["name"] for t in TOOLS]
    assert safety_tier_for("apply_generated_profiles") == "write"
    assert TOOL_ROUTES["apply_generated_profiles"] == [
        ("POST", "/api/projects/{name}/profiles/apply-batch")]
    job_id = _job(client, project)
    with bound_ctx(client):
        out = chat_tools.DISPATCHERS["apply_generated_profiles"](
            project_id=project, job_id=job_id, components=["g_a", "g_b"])
    assert out["count"] == 2


def test_the_card_and_the_route_pick_the_same_members(client, project, bound_ctx):
    from services.profile_gen import card

    points = [*POINTS[:2], {"id": "g_gas", "lon": 8.0, "lat": 50.0},
              {"id": "site_x", "lon": 8.0, "lat": 50.0}]
    job_id = _job(client, project, points)
    args = {"project_id": project, "job_id": job_id, "all_from_job": True}
    with bound_ctx(client):
        shown = {r["name"] for r in card.preview_members(args)}
    resp = client.post(f"/api/projects/{project}/profiles/apply-batch",
                       json={"job_id": job_id, "all_from_job": True})
    assert resp.status_code == 200, resp.text
    written = {a["component"] for a in resp.json()["applied"]}
    assert written == {"g_a", "g_b"} and shown == written


def test_members_together_with_all_from_job_is_refused(client, project):
    job_id = _job(client, project)
    resp = client.post(f"/api/projects/{project}/profiles/apply-batch", json={
        "job_id": job_id, "all_from_job": True, "members": [{"component": "g_a"}]})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_kind"] == "ambiguous_members"

"""Live-voice 08 slice B3: typed tools for the solver-simulation and
timeseries-horizon GUI routes.

Eleven new wrappers over the Solve Queue panel, FMEA worksheet and Time
Series routes, plus two catalogue fixes:

  * `solve_queue_clear_finished` was read-tier on a POST, which the route
    parity tier rule (V4) refuses; it is write-tier now;
  * `upload_timeseries` calls the multipart upload route, so TOOL_ROUTES names
    that route, not the JSON PUT its handler never reaches.

Queue pause and resume get no tool: they stay "out of chat"
(`services/not_via_chat.py`) and are an `admin` limitation in route parity.

Each wrapper goes through the route handler, so the route's ACL, foreign-lock
and super-admin checks apply; the tests pin that they do through the chat
tool, and that the chat lock seam answers like the HTTP middleware.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

import main
from harness.catalogue import TOOL_ROUTES, TOOLS, safety_tier_for, tool_edits_network
from services import chat_tools
from services.solve_queue import SolveJob, solve_queue

B3_TOOLS = {
    "solve_queue_job_log": ("read", ("GET", "/api/simulation/queue/{job_id}/log_history")),
    "solve_queue_cancel_queued": ("destructive", ("POST", "/api/simulation/queue/cancel_queued")),
    "solve_queue_dismiss": ("write", ("POST", "/api/simulation/queue/{job_id}/dismiss")),
    "solve_queue_requeue": ("execution", ("POST", "/api/simulation/queue/{job_id}/requeue")),
    "save_fmea_worksheet": ("destructive", ("PUT", "/api/projects/{name}/worksheet")),
    "get_current_series": ("read", ("GET", "/api/projects/{name}/profiles/current")),
    "get_weather_choice": ("read", ("GET", "/api/projects/{name}/profiles/weather")),
    "set_weather_choice": ("write", ("PUT", "/api/projects/{name}/profiles/weather")),
    "pending_profile_from_current": ("write", ("POST", "/api/projects/{name}/profiles/from-current")),
    "upload_load_profiles_file": ("write", ("POST", "/api/projects/{name}/profiles/loads/upload")),
    "upload_series_file": ("write", ("POST", "/api/projects/{name}/profiles/series/upload")),
    # fixed, not new
    "solve_queue_clear_finished": ("write", ("POST", "/api/simulation/queue/clear_finished")),
    "upload_timeseries": ("write", ("POST", "/api/network/timeseries/upload")),
}


# ── Registration, tiers, routes ─────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(B3_TOOLS))
def test_each_tool_is_registered_once_with_its_tier_and_route(name):
    tier, route = B3_TOOLS[name]
    assert [t["name"] for t in TOOLS].count(name) == 1
    assert name in chat_tools.DISPATCHERS
    assert safety_tier_for(name) == tier
    assert TOOL_ROUTES[name] == [route]
    # V4: a tool covering a POST/PUT is never read-tier.
    assert (route[0] == "GET") == (tier == "read")


def test_clear_finished_is_write_tier_and_parity_counts_its_route_as_tool():
    import json
    from smoke import assistant_route_parity as parity

    assert safety_tier_for("solve_queue_clear_finished") == "write"
    rows = json.loads(parity.DEFAULT_OUTPUT.read_text())["rows"]
    row = next(r for r in rows if r["path"] == "/api/simulation/queue/clear_finished")
    assert row["class"] == "tool" and row["tools"] == ["solve_queue_clear_finished"]


def test_every_b3_route_is_tool_covered_in_the_parity_matrix():
    import json
    from smoke import assistant_route_parity as parity

    rows = {(r["method"], r["path"]): r
            for r in json.loads(parity.DEFAULT_OUTPUT.read_text())["rows"]}
    for name, (_tier, route) in B3_TOOLS.items():
        assert rows[route]["class"] == "tool", (name, rows[route])


def test_upload_timeseries_stays_a_gated_undoable_network_edit():
    """Moving its route from the PUT to the POST upload keeps it under
    /api/network/: still a network edit (undo, edit counter) and still
    lock-gated in chat, as before."""
    assert tool_edits_network("upload_timeseries")
    assert "upload_timeseries" in chat_tools._lock_gated_tool_names()


# ── Chat lock seam vs HTTP middleware (GK B Q1) ─────────────────────────────


@pytest.mark.parametrize("name", [
    "solve_queue_cancel_queued", "solve_queue_dismiss", "solve_queue_requeue", "solve_queue_clear_finished",
])
def test_queue_tools_are_exempt_from_the_chat_lock_seam_like_http(name):
    _method, path = TOOL_ROUTES[name][0]
    concrete = path.replace("{job_id}", str(uuid.uuid4()))
    assert main._foreign_lock_gate_exempt(concrete)
    assert name not in chat_tools._lock_gated_tool_names()


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def colleague(_auth_db, seeded_identity):
    """A second user in the SAME org: the realistic intruder for a lock test."""
    from db.models import OrgMembership, User
    from services.auth_service import hash_password

    _engine, session_local = _auth_db
    with session_local() as db:
        u = User(id=uuid.uuid4(), email=f"b3-{uuid.uuid4().hex[:6]}@example.com",
                 password_hash=hash_password("irrelevant"), status="active",
                 is_super_admin=False, created_at=datetime.now(tz=timezone.utc))
        db.add(u)
        db.flush()
        db.add(OrgMembership(id=uuid.uuid4(), user_id=u.id,
                             org_id=seeded_identity["org_id"], role="admin"))
        db.commit()
        return u.id


@pytest.fixture
def acting():
    """Run chat tools as a given user id; always reset."""
    def _as(user_id):
        chat_tools.set_acting_user(user_id)
    yield _as
    chat_tools.set_acting_user(None)


@pytest.fixture
def clean_queue():
    solve_queue.reset_for_tests()
    yield
    solve_queue.reset_for_tests()


def _seed_job(status, name, key, storage_dir, by=None) -> uuid.UUID:
    jid = uuid.uuid4()
    with solve_queue._lock:
        job = SolveJob(id=jid, project_id=name, project_key=key,
                       storage_dir=storage_dir, enqueued_at=time.time(),
                       enqueued_by_user_id=by)
        job.status = status
        if status not in ("queued", "running"):
            job.finished_at = time.time()
        solve_queue._jobs[jid] = job
        solve_queue._order.append(jid)
    return jid


def _is_lock_refusal(exc: HTTPException) -> bool:
    return (exc.status_code == 409 and isinstance(exc.detail, dict)
            and exc.detail.get("error_kind") == "project_locked")


# ── Solve queue controls ────────────────────────────────────────────────────


def test_job_log_returns_the_tail_and_404s_an_unknown_job(
        client, api_project, registry_key_for, project_storage_dir, clean_queue,
        seeded_identity, acting):
    name = api_project("b3-log")
    jid = _seed_job("completed", name, registry_key_for(name),
                    str(project_storage_dir(name)), by=seeded_identity["user_id"])
    acting(seeded_identity["user_id"])
    out = chat_tools.DISPATCHERS["solve_queue_job_log"](job_id=str(jid), max_lines=5)
    assert out["status"] == "completed"
    assert out["total_lines"] == len(out["lines"]) and out["truncated"] is False
    with pytest.raises(HTTPException) as err:
        chat_tools.DISPATCHERS["solve_queue_job_log"](job_id=str(uuid.uuid4()))
    assert err.value.status_code == 404


def test_job_log_keeps_only_the_last_lines(monkeypatch):
    from routers import solve_queue as sq

    monkeypatch.setattr(sq, "job_log_history",
                        lambda job_id: {"lines": [f"l{i}" for i in range(10)],
                                        "status": "running"})
    out = chat_tools.solve_queue_job_log("j", max_lines=3)
    assert out == {"status": "running", "lines": ["l7", "l8", "l9"],
                   "total_lines": 10, "truncated": True}


def test_clear_finished_is_super_admin_only_for_a_plain_caller(
        client, seeded_identity, acting, clean_queue):
    acting(seeded_identity["user_id"])
    with pytest.raises(HTTPException) as err:
        chat_tools.DISPATCHERS["solve_queue_clear_finished"]()
    assert err.value.status_code == 403


def test_pause_and_resume_have_no_tool_and_are_an_admin_limitation():
    """Owner call (coordinator, LV08 B3): the instance-wide controls stay
    out of chat, as `not_via_chat.solve_queue_instance_controls` says."""
    import json
    from smoke import assistant_route_parity as parity

    names = {t["name"] for t in TOOLS}
    rows = {(r["method"], r["path"]): r
            for r in json.loads(parity.DEFAULT_OUTPUT.read_text())["rows"]}
    for verb in ("pause", "resume"):
        assert f"solve_queue_{verb}" not in names
        route = ("POST", f"/api/simulation/queue/{verb}")
        assert not any(route in routes for routes in TOOL_ROUTES.values())
        assert rows[route]["class"] == "limitation" and rows[route]["reason"] == "admin"


def test_cancel_queued_cancels_only_the_callers_queued_jobs(
        client, api_project, registry_key_for, project_storage_dir, clean_queue,
        seeded_identity, acting):
    name = api_project("b3-cancel")
    key, where = registry_key_for(name), str(project_storage_dir(name))
    solve_queue.pause()  # nothing may start while the jobs are seeded
    queued = _seed_job("queued", name, key, where, by=seeded_identity["user_id"])
    done = _seed_job("completed", name, key, where, by=seeded_identity["user_id"])
    acting(seeded_identity["user_id"])
    assert chat_tools.DISPATCHERS["solve_queue_cancel_queued"]() == {"cancelled": 1}
    assert solve_queue.get_job(queued)["status"] == "aborted"
    assert solve_queue.get_job(done)["status"] == "completed"


def test_dismiss_is_allowed_under_a_foreign_lock(
        client, api_project, registry_key_for, project_storage_dir, clean_queue,
        colleague, acting):
    """HTTP exempts dismiss from the lock gate (it hides one caller's row and
    writes nothing); the chat tool agrees."""
    name = api_project("b3-dismiss")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200
    jid = _seed_job("completed", name, registry_key_for(name),
                    str(project_storage_dir(name)), by=colleague)
    acting(colleague)
    assert chat_tools.DISPATCHERS["solve_queue_dismiss"](job_id=str(jid)) == {"dismissed": True}


def test_requeue_is_refused_under_a_foreign_lock(
        client, api_project, registry_key_for, project_storage_dir, clean_queue,
        colleague, acting):
    """The lock seam exempts requeue only because `requeue_job` checks the
    lock of the project it resolves from the job (main.py's "pair"): the
    refusal comes from the handler, in chat as over HTTP."""
    name = api_project("b3-requeue")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200
    jid = _seed_job("completed", name, registry_key_for(name),
                    str(project_storage_dir(name)), by=colleague)
    acting(colleague)
    with pytest.raises(HTTPException) as err:
        chat_tools.DISPATCHERS["solve_queue_requeue"](job_id=str(jid))
    assert _is_lock_refusal(err.value), err.value.detail
    assert len(solve_queue._jobs) == 1


def test_requeue_checks_the_jobs_project_not_the_active_one(
        client, api_project, registry_key_for, project_storage_dir, project_row,
        bound_ctx, clean_queue, colleague, acting):
    """The holder check reads the project the JOB names: a job for a locked
    project is refused while the chat turn is bound to a different, unlocked
    project."""
    locked = api_project("b3-requeue-locked")
    assert client.post(f"/api/projects/{locked}/lock").status_code == 200
    jid = _seed_job("completed", locked, registry_key_for(locked),
                    str(project_storage_dir(locked)), by=colleague)
    other = api_project("b3-requeue-other")  # the session's active project now
    acting(colleague)
    with bound_ctx(client) as ctx, pytest.raises(HTTPException) as err:
        assert ctx.project_uuid == str(project_row(other).id)
        chat_tools.DISPATCHERS["solve_queue_requeue"](job_id=str(jid))
    assert _is_lock_refusal(err.value), err.value.detail
    assert len(solve_queue._jobs) == 1


def test_requeue_runs_a_finished_job_again(
        client, api_project, registry_key_for, project_storage_dir, clean_queue,
        seeded_identity, acting):
    name = api_project("b3-requeue-ok")
    solve_queue.pause()
    jid = _seed_job("failed", name, registry_key_for(name),
                    str(project_storage_dir(name)), by=seeded_identity["user_id"])
    acting(seeded_identity["user_id"])
    out = chat_tools.DISPATCHERS["solve_queue_requeue"](job_id=str(jid))
    assert out["already_queued"] is False and out["id"] != str(jid)


# ── FMEA worksheet ──────────────────────────────────────────────────────────


def test_save_fmea_worksheet_replaces_the_sidecar(client, api_project, seeded_identity, acting):
    name = api_project("b3-ws")
    acting(seeded_identity["user_id"])
    before = chat_tools.DISPATCHERS["get_fmea_worksheet"](name=name)
    out = chat_tools.DISPATCHERS["save_fmea_worksheet"](
        name=name, manual_rows=[], overlays={})
    assert out["version"] == before.get("version", 0) + 1
    assert chat_tools.DISPATCHERS["get_fmea_worksheet"](name=name)["version"] == out["version"]


def test_save_fmea_worksheet_is_refused_under_a_foreign_lock(
        client, api_project, colleague, acting):
    name = api_project("b3-ws-lock")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200
    acting(colleague)
    with pytest.raises(HTTPException) as err:
        chat_tools.DISPATCHERS["save_fmea_worksheet"](name=name, manual_rows=[], overlays={})
    assert _is_lock_refusal(err.value), err.value.detail


# ── Time series and weather ─────────────────────────────────────────────────


def test_weather_choice_round_trips(client, api_project, seeded_identity, acting):
    name = api_project("b3-weather")
    acting(seeded_identity["user_id"])
    out = chat_tools.DISPATCHERS["set_weather_choice"](
        project_id=name, source="synthetic", year=2019)
    assert out["source"] == "synthetic" and out["year"] == 2019
    read = chat_tools.DISPATCHERS["get_weather_choice"](project_id=name)
    assert read["source"] == "synthetic" and read["year"] == 2019


def test_set_weather_choice_is_refused_under_a_foreign_lock(
        client, api_project, colleague, acting):
    name = api_project("b3-weather-lock")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200
    acting(colleague)
    with pytest.raises(HTTPException) as err:
        chat_tools.DISPATCHERS["set_weather_choice"](
            project_id=name, source="synthetic", year=2019)
    assert _is_lock_refusal(err.value), err.value.detail


@pytest.mark.parametrize("name,kwargs", [
    ("get_current_series", {"component_type": "loads", "attribute": "p_set", "component": "L"}),
    ("get_weather_choice", {}),
    ("set_weather_choice", {"source": "synthetic", "year": 2019}),
    ("pending_profile_from_current",
     {"component_type": "loads", "attribute": "p_set", "component": "L"}),
    ("upload_load_profiles_file", {"file_id": "f"}),
    ("upload_series_file",
     {"file_id": "f", "component_type": "loads", "attribute": "p_set", "component": "L"}),
    ("save_fmea_worksheet", {"manual_rows": [], "overlays": {}}),
])
def test_another_orgs_project_is_refused(client, api_project, second_identity, acting,
                                         name, kwargs):
    project = api_project("b3-acl")
    acting(second_identity["user_id"])
    key = "name" if name == "save_fmea_worksheet" else "project_id"
    with pytest.raises(HTTPException) as err:
        chat_tools.DISPATCHERS[name](**{key: project}, **kwargs)
    assert err.value.status_code in (403, 404), (name, err.value.status_code)


def _upload(client, project, filename, body, mime="text/csv") -> str:
    r = client.post(f"/api/projects/{project}/uploads",
                    files={"file": (filename, body, mime)})
    assert r.status_code in (200, 201), r.text
    return r.json()["file_id"]


def test_upload_tools_refuse_another_projects_file_id(
        client, api_project, seeded_identity, acting):
    a = api_project("b3-up-a")
    b = api_project("b3-up-b")
    fid = _upload(client, b, "loads.csv", b"snapshot,L\n2025-01-01 00:00,1\n")
    acting(seeded_identity["user_id"])
    for name, extra in (("upload_load_profiles_file", {}),
                        ("upload_series_file", {"component_type": "loads",
                                                "attribute": "p_set", "component": "L"})):
        with pytest.raises(HTTPException) as err:
            chat_tools.DISPATCHERS[name](project_id=a, file_id=fid, **extra)
        assert err.value.status_code == 404, name
        assert err.value.detail["error_kind"] == "upload_not_found", name


def test_upload_hands_the_route_the_stored_filename_and_type(
        client, api_project, seeded_identity, acting, monkeypatch):
    from routers import profiles

    name = api_project("b3-up-meta")
    fid = _upload(client, name, "measured.csv", b"snapshot,L\n2025-01-01 00:00,1\n")
    seen = {}

    async def fake(file, proj, db, user, **form):
        seen.update(filename=file.filename, content_type=file.content_type,
                    body=await file.read(), project=proj.name, form=form)
        return {"ok": True}

    monkeypatch.setattr(profiles, "upload_load_profiles", fake)
    acting(seeded_identity["user_id"])
    assert chat_tools.DISPATCHERS["upload_load_profiles_file"](
        project_id=name, file_id=fid, unit="kW") == {"ok": True}
    assert seen["filename"] == "measured.csv"
    assert seen["content_type"].startswith("text/")
    assert seen["body"].startswith(b"snapshot,L")
    assert seen["project"] == name
    assert seen["form"] == {"unit": "kW", "timezone": None, "year": None, "annual_mwh": None}


def test_upload_is_refused_under_a_foreign_lock(client, api_project, colleague, acting):
    name = api_project("b3-up-lock")
    fid = _upload(client, name, "loads.csv", b"snapshot,L\n2025-01-01 00:00,1\n")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200
    acting(colleague)
    with pytest.raises(HTTPException) as err:
        chat_tools.DISPATCHERS["upload_load_profiles_file"](project_id=name, file_id=fid)
    assert _is_lock_refusal(err.value), err.value.detail

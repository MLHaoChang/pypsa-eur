"""
R30 — pause stops the queue STARTING work, not doing it.

A running solve is minutes of solver time that pausing must not throw away, so
pause is a gate on the pop, not a signal to the worker. Resume continues in FIFO
order because the paused worker is parked holding the head of the queue.

AUTHORIZATION (fix round 1, controller ruling): pause/resume are gated on
`User.is_super_admin` (local mode exempt), same tier as `clear_finished` and for
the same reason — one dispatcher serves every org, so pausing it has a cross-org
blast radius no org-scoped role can authorize. `super_admin_client` is
reproduced locally rather than imported from `test_solve_queue_authz.py`, same
convention this suite already uses for `_parked_dispatcher` (duplicated across
`test_solve_queue_authz.py` and `test_solve_queue_persisted_listing.py`).
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
from db.models import OrgMembership, User
from services.solve_queue import SolveJob, solve_queue
from tests.conftest import attach_session, build_network


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


# ── super_admin_client (duplicated from test_solve_queue_authz.py) ─────────
# Both conftest identities are ORG admins with `is_super_admin=False` — the
# tier pause/resume must refuse — so a super-admin caller needs its own seeded
# user. Copied rather than imported across test modules, matching how this
# suite already handles `_parked_dispatcher`.


def _drop_user(session_local, user_id) -> None:
    """Remove a per-test user and everything that FK-references it."""
    from sqlalchemy import delete, or_

    from db.models import Project, ProjectMembership

    with session_local() as db:
        db.execute(
            delete(ProjectMembership).where(
                or_(
                    ProjectMembership.user_id == user_id,
                    ProjectMembership.assigned_by == user_id,
                )
            )
        )
        db.execute(delete(Project).where(Project.created_by == user_id))
        db.commit()
        row = db.get(User, user_id)
        if row is not None:
            db.delete(row)
            db.commit()


def _seed_user(session_local, org_id, *, email: str, role: str, super_admin: bool):
    """Create an active user in `org_id` and return their id."""
    with session_local() as db:
        user = User(
            id=uuid.uuid4(),
            email=email,
            password_hash=None,
            status="active",
            is_super_admin=super_admin,
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add(user)
        db.flush()
        db.add(
            OrgMembership(
                id=uuid.uuid4(), user_id=user.id, org_id=org_id, role=role
            )
        )
        db.commit()
        return user.id


@pytest.fixture
def super_admin_client(_auth_db, seeded_identity):
    """Authenticated client for a user carrying `is_super_admin`."""
    _engine, session_local = _auth_db
    user_id = _seed_user(
        session_local,
        seeded_identity["org_id"],
        email="queue-pause-super-admin@example.com",
        role="admin",
        super_admin=True,
    )
    try:
        with TestClient(main.app) as c:
            yield attach_session(c, session_local, user_id)
    finally:
        _drop_user(session_local, user_id)


def test_pause_and_resume_round_trip(super_admin_client):
    solve_queue.reset_for_tests()
    try:
        assert solve_queue.is_paused() is False

        r = super_admin_client.post("/api/simulation/queue/pause")
        assert r.status_code == 200, r.text
        assert r.json() == {"paused": True}
        assert solve_queue.is_paused() is True
        assert super_admin_client.get("/api/simulation/queue").json()["paused"] is True

        r = super_admin_client.post("/api/simulation/queue/resume")
        assert r.json() == {"paused": False}
        assert solve_queue.is_paused() is False
        assert super_admin_client.get("/api/simulation/queue").json()["paused"] is False
    finally:
        solve_queue.resume()
        solve_queue.reset_for_tests()


def test_a_paused_queue_starts_nothing(super_admin_client, install_network, tmp_projects_dir):
    solve_queue.reset_for_tests()
    try:
        assert super_admin_client.post("/api/simulation/queue/pause").status_code == 200
        install_network(build_network(), name="Paused")
        _save_project(super_admin_client, "Paused")
        job = super_admin_client.post(
            "/api/simulation/queue", json={"project_id": "Paused"}
        ).json()

        # Give a dispatcher that ignored the pause ample time to start it.
        time.sleep(1.5)
        assert (solve_queue.get_job(uuid.UUID(job["id"])) or {})["status"] == "queued", (
            "the dispatcher started a job while the queue was paused"
        )
    finally:
        solve_queue.resume()
        solve_queue.reset_for_tests()


def test_resuming_lets_the_queued_job_run(
    super_admin_client, install_network, tmp_projects_dir, monkeypatch
):
    from services import solver_service

    def quick(config, n, lock, stop_event, log_queue, state_update=None):
        return "ok", "optimal"

    monkeypatch.setattr(solver_service, "run_simulation", quick)
    solve_queue.reset_for_tests()
    try:
        assert super_admin_client.post("/api/simulation/queue/pause").status_code == 200
        install_network(build_network(), name="Resumed")
        _save_project(super_admin_client, "Resumed")
        job = super_admin_client.post(
            "/api/simulation/queue", json={"project_id": "Resumed"}
        ).json()
        jid = uuid.UUID(job["id"])
        time.sleep(0.5)
        assert (solve_queue.get_job(jid) or {})["status"] == "queued"

        assert super_admin_client.post("/api/simulation/queue/resume").status_code == 200
        deadline = time.time() + 60
        while time.time() < deadline:
            if (solve_queue.get_job(jid) or {}).get("status") in (
                "completed", "failed", "aborted", "interrupted",
            ):
                break
            time.sleep(0.1)
        assert (solve_queue.get_job(jid) or {})["status"] == "completed"
    finally:
        solve_queue.resume()
        solve_queue.reset_for_tests()


def test_pausing_does_not_touch_a_running_job():
    solve_queue.reset_for_tests()
    try:
        jid = uuid.uuid4()
        with solve_queue._lock:
            job = SolveJob(id=jid, project_id="Live", enqueued_at=0.0)
            job.status = "running"
            solve_queue._jobs[jid] = job
            solve_queue._order.append(jid)

        solve_queue.pause()

        assert (solve_queue.get_job(jid) or {})["status"] == "running"
    finally:
        solve_queue.resume()
        solve_queue.reset_for_tests()


# ── authorization (fix round 1) ─────────────────────────────────────────────


def test_pause_and_resume_are_refused_for_a_non_super_admin(client):
    """
    A plain authenticated org member gets 403 from both routes — and, the part
    that must actually discriminate, the refused call has NO effect on the
    dispatcher's state.
    """
    solve_queue.reset_for_tests()
    try:
        assert solve_queue.is_paused() is False
        r = client.post("/api/simulation/queue/pause")
        assert r.status_code == 403, r.text
        assert solve_queue.is_paused() is False, (
            "a refused pause call must not have paused the dispatcher"
        )

        solve_queue.pause()
        assert solve_queue.is_paused() is True
        r = client.post("/api/simulation/queue/resume")
        assert r.status_code == 403, r.text
        assert solve_queue.is_paused() is True, (
            "a refused resume call must not have resumed the dispatcher"
        )
    finally:
        solve_queue.resume()
        solve_queue.reset_for_tests()


def test_local_mode_can_pause_and_resume(_auth_db, monkeypatch, tmp_path):
    """
    Non-regression for the packaged desktop app: local mode has one seeded
    tenant and one user, so the super-admin gate would only lock the desktop
    user out of their own machine. Follows
    `test_solve_queue_authz.py::test_local_mode_can_list_abort_and_clear` for
    how this suite enters local mode.
    """
    import local_mode

    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
    solve_queue.reset_for_tests()
    try:
        with TestClient(main.app) as local_client:
            local_client.cookies.clear()

            r = local_client.post("/api/simulation/queue/pause")
            assert r.status_code == 200, r.text
            assert r.json() == {"paused": True}

            r = local_client.post("/api/simulation/queue/resume")
            assert r.status_code == 200, r.text
            assert r.json() == {"paused": False}
    finally:
        with session_local() as db:
            local_mode.remove_local_identity(db)
        solve_queue.resume()
        solve_queue.reset_for_tests()


def test_paused_field_is_visible_to_a_plain_authenticated_client(client):
    """
    Reading the queue's state is not the instance-wide control — only pausing
    and resuming are. Any authenticated caller can still see `paused` in the
    listing.
    """
    solve_queue.reset_for_tests()
    try:
        assert client.get("/api/simulation/queue").json()["paused"] is False
        assert client.get("/api/simulation/queue").status_code == 200
    finally:
        solve_queue.reset_for_tests()

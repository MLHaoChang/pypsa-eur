"""
R32 — a user clears finished rows from THEIR OWN view.

Dismissal is filtered on `enqueued_by_user_id`, so a user can only dismiss what
they queued. Keying it on project access instead would let two users sharing a
project dismiss each other's rows, which is the exact thing per-user dismiss
exists to fix. Pure client state was the other rejected option: it evaporates
across devices and the chat tool would keep listing rows the user believes
cleared.

The super-admin `clear_finished` is unchanged. It is unconditionally global and
gated instance-wide, and this is the per-caller variant its docstring says it
deliberately does not have — a separate operation, not a weaker path into that
one.
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


# `org_member_client` is defined in `tests/test_solve_queue_authz.py` and NOWHERE
# else — it is not in `conftest.py` and there is no `pytest_plugins`
# registration, so a module-scoped fixture in one test file is invisible to
# another and importing this file's tests would fail at collection with
# "fixture 'org_member_client' not found". Redefined locally rather than lifted
# into `conftest.py`: both conftest identities carry `role="admin"`, which
# short-circuits `can_access_project`, so neither can express "same org, can see
# the project, did not queue this job" — the case this file needs.
def _seed_user(session_local, org_id, *, email: str, role: str):
    """Create an active user in `org_id` and return their id."""
    with session_local() as db:
        user = User(
            id=uuid.uuid4(),
            email=email,
            password_hash=None,
            status="active",
            is_super_admin=False,
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add(user)
        db.flush()
        db.add(OrgMembership(id=uuid.uuid4(), user_id=user.id, org_id=org_id, role=role))
        db.commit()
        return user.id


def _drop_user(session_local, user_id) -> None:
    """
    Remove the per-test user and everything that FK-references it.

    `projects.created_by` and `project_memberships.assigned_by` carry no
    ON DELETE, and `_reset_tenant_tables` truncates the project tables only
    AFTER this fixture unwinds — so deleting the user first fails on a foreign
    key, the user survives, and the next test using the fixture dies on the
    unique email instead.
    """
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


@pytest.fixture
def org_member_client(_auth_db, seeded_identity):
    """Authenticated client for a PLAIN member of the primary org."""
    _engine, session_local = _auth_db
    user_id = _seed_user(
        session_local,
        seeded_identity["org_id"],
        email="queue-dismiss-member@example.com",
        role="member",
    )
    try:
        with TestClient(main.app) as c:
            yield attach_session(c, session_local, user_id)
    finally:
        _drop_user(session_local, user_id)


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def _acting_user_id(test_client) -> uuid.UUID:
    from db.models import User
    from db.session import SessionLocal
    from services.auth_service import resolve_session_row
    from settings import get_settings

    raw = test_client.cookies.get(get_settings().session_cookie_name)
    with SessionLocal() as db:
        row = resolve_session_row(db, raw)
        assert row is not None
        return db.get(User, row.user_id).id


def _seed(status: str, name: str, key: str, owner) -> uuid.UUID:
    jid = uuid.uuid4()
    with solve_queue._lock:
        job = SolveJob(id=jid, project_id=name, project_key=key, enqueued_at=time.time())
        job.status = status
        job.finished_at = time.time()
        job.enqueued_by_user_id = owner
        solve_queue._jobs[jid] = job
        solve_queue._order.append(jid)
    return jid


def test_a_dismissed_row_leaves_the_owners_listing(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Mine")
    _save_project(client, "Mine")
    solve_queue.reset_for_tests()
    try:
        me = _acting_user_id(client)
        jid = _seed("completed", "Mine", registry_key_for("Mine"), me)

        assert any(j["id"] == str(jid) for j in client.get("/api/simulation/queue").json()["jobs"])
        r = client.post(f"/api/simulation/queue/{jid}/dismiss")
        assert r.status_code == 200, r.text
        assert r.json() == {"dismissed": True}
        assert not any(
            j["id"] == str(jid) for j in client.get("/api/simulation/queue").json()["jobs"]
        )
    finally:
        solve_queue.reset_for_tests()


def test_every_terminal_status_is_dismissible_interrupted_included(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Mine")
    _save_project(client, "Mine")
    key = registry_key_for("Mine")
    try:
        me = _acting_user_id(client)
        for status in ("completed", "failed", "aborted", "interrupted"):
            solve_queue.reset_for_tests()
            jid = _seed(status, "Mine", key, me)
            r = client.post(f"/api/simulation/queue/{jid}/dismiss")
            assert r.status_code == 200, (status, r.text)
    finally:
        solve_queue.reset_for_tests()


def test_a_queued_or_running_job_is_not_dismissible(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Mine")
    _save_project(client, "Mine")
    key = registry_key_for("Mine")
    solve_queue.reset_for_tests()
    try:
        me = _acting_user_id(client)
        for status in ("queued", "running"):
            jid = _seed(status, "Mine", key, me)
            with solve_queue._lock:
                solve_queue._jobs[jid].finished_at = None
            r = client.post(f"/api/simulation/queue/{jid}/dismiss")
            assert r.status_code == 409, (status, r.text)
    finally:
        solve_queue.reset_for_tests()


def test_dismissal_does_not_affect_another_users_listing(
    client, org_member_client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Shared")
    _save_project(client, "Shared")
    solve_queue.reset_for_tests()
    try:
        me = _acting_user_id(client)
        jid = _seed("completed", "Shared", registry_key_for("Shared"), me)
        assert client.post(f"/api/simulation/queue/{jid}/dismiss").status_code == 200

        # Asserted on the ID, not the name: this member has no ACL on the
        # project, so the row comes back REDACTED — the listing redacts rather
        # than filters, so the row is still there and still counts toward queue
        # depth. That is the point: one user's dismissal must not remove a row
        # from anyone else's listing, redacted or not.
        theirs = org_member_client.get("/api/simulation/queue").json()["jobs"]
        assert any(j["id"] == str(jid) for j in theirs), (
            "one user's dismissal removed the row from another user's listing"
        )
    finally:
        solve_queue.reset_for_tests()


def test_a_user_cannot_dismiss_a_job_someone_else_queued(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Theirs")
    _save_project(client, "Theirs")
    solve_queue.reset_for_tests()
    try:
        jid = _seed("completed", "Theirs", registry_key_for("Theirs"), uuid.uuid4())
        r = client.post(f"/api/simulation/queue/{jid}/dismiss")
        assert r.status_code == 403, r.text
    finally:
        solve_queue.reset_for_tests()


def test_dismissal_survives_a_restart_ruling_2(
    client, org_member_client, install_network, tmp_projects_dir, registry_key_for,
):
    """
    R32 durability (Ruling 2). Dismiss a TERMINAL job while it is still
    resident, then simulate a process restart: `reset_for_tests()` drops
    every in-memory job (a fresh `SolveQueue()` would do the same), and
    `reconcile_on_boot()` never re-admits a TERMINAL row back into `_jobs`
    (only `queued` rows are restored). From that point on, the ONLY way this
    job reaches a listing at all is via `_merged_jobs`'s persisted-row read —
    so a dismissal filter that only ever consulted `_jobs` would let the row
    reappear the instant the process restarted, defeating the durability this
    feature exists to provide. Filtering AFTER the merge, against a dismissed
    id set that itself reads the table (Ruling 2), keeps it hidden.

    Another user in the same org must still see the (redacted) row — the
    dismissal is per-user, restart or not.
    """
    from services import solve_job_store

    install_network(build_network(), name="Durable")
    _save_project(client, "Durable")
    key = registry_key_for("Durable")
    solve_queue.reset_for_tests()
    try:
        me = _acting_user_id(client)
        jid = _seed("completed", "Durable", key, me)
        # `_seed` only builds the in-memory job (matching the brief's other
        # tests) — it never inserts a `solve_jobs` ROW, so there would be
        # nothing left for `_merged_jobs` to serve once `_jobs` is cleared
        # below. Persist it the same way `enqueue_unique` does, so the
        # restart-survival this test is checking has something to survive.
        with solve_queue._lock:
            job = solve_queue._jobs[jid]
        solve_job_store.record_enqueued(
            job, enqueued_by_user_id=me, solver_config_json=None,
        )
        r = client.post(f"/api/simulation/queue/{jid}/dismiss")
        assert r.status_code == 200, r.text

        # Simulate a restart: drop everything resident, then run the same
        # boot-reconciliation path the real process runs. The row is
        # TERMINAL, so it is not restored to `_jobs` — it only exists in
        # `solve_jobs` from here on.
        solve_queue.reset_for_tests()
        solve_job_store.reconcile_on_boot()
        assert jid not in solve_queue._jobs

        mine = client.get("/api/simulation/queue").json()["jobs"]
        assert not any(j["id"] == str(jid) for j in mine), (
            "a dismissed job reappeared in the owner's listing after a restart"
        )

        theirs = org_member_client.get("/api/simulation/queue").json()["jobs"]
        assert any(j["id"] == str(jid) for j in theirs), (
            "the dismissal leaked into another user's listing after a restart"
        )
    finally:
        solve_queue.reset_for_tests()

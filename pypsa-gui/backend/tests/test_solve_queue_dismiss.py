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


def _seed_persisted_only(session_local, name, key, owner, *, status="completed"):
    """
    Build a job that is ONLY in `solve_jobs`, never in `_jobs` — the shape of
    every `interrupted` job after a restart, and any terminal job from before
    the last restart. Bypasses `_seed` + `record_enqueued` + `reset_for_tests`
    (which would still leave a live `SolveJob` momentarily resident) by
    inserting the row directly, so there is never a `_jobs` entry to fall
    back on and the persisted-fallback path is the ONLY path exercised.
    """
    from datetime import datetime, timezone

    from db.models import SolveJobRow

    jid = uuid.uuid4()
    with session_local() as db:
        db.add(SolveJobRow(
            id=jid,
            project_id=name,
            project_key=key,
            storage_dir=None,
            status=status,
            enqueued_by_user_id=owner,
            solver_config=None,
            enqueued_at=datetime.now(tz=timezone.utc),
            finished_at=datetime.now(tz=timezone.utc),
        ))
        db.commit()
    return jid


def test_a_persisted_only_job_can_be_dismissed_by_its_true_owner_fix_round_1(
    client, org_member_client, install_network, tmp_projects_dir, registry_key_for, _auth_db,
):
    """
    Fix round 1. A job that is persisted but NOT resident in `_jobs` (every
    `interrupted` job after a restart, or any terminal job from before the
    last restart) must still be dismissable by the user who queued it — this
    is precisely the row a user wants to clear. Before the fix, the route
    only ever asked `solve_queue._jobs` for the owner, read None because the
    job was never resident to begin with, and refused even the true owner
    with a 403.

    Unlike `test_dismissal_survives_a_restart_ruling_2`, this dismisses
    AFTER the persisted-only state is already in place — the earlier test
    dismissed a still-resident job and only then restarted, so it never
    exercised the owner-lookup's persisted fallback at all.
    """
    _engine, session_local = _auth_db

    install_network(build_network(), name="PersistedOnly")
    _save_project(client, "PersistedOnly")
    key = registry_key_for("PersistedOnly")
    solve_queue.reset_for_tests()
    try:
        me = _acting_user_id(client)
        jid = _seed_persisted_only(session_local, "PersistedOnly", key, me)
        assert jid not in solve_queue._jobs

        r = client.post(f"/api/simulation/queue/{jid}/dismiss")
        assert r.status_code == 200, r.text
        assert r.json() == {"dismissed": True}

        mine = client.get("/api/simulation/queue").json()["jobs"]
        assert not any(j["id"] == str(jid) for j in mine), (
            "the true owner's dismissal of a persisted-only job did not stick"
        )

        theirs = org_member_client.get("/api/simulation/queue").json()["jobs"]
        assert any(j["id"] == str(jid) for j in theirs), (
            "dismissing a persisted-only job leaked into another user's listing"
        )
    finally:
        solve_queue.reset_for_tests()


def test_a_persisted_only_job_cannot_be_dismissed_by_someone_else_fix_round_1(
    client, org_member_client, install_network, tmp_projects_dir, registry_key_for, _auth_db,
):
    """
    Fix round 1, negative case. A persisted-only job (see above) whose true
    owner is someone else must still refuse dismissal — the persisted-
    fallback lookup has to answer ownership correctly, not just "found a
    row and it's visible, so let it through". The owner is a REAL user
    (`enqueued_by_user_id` FKs to `users.id`; a bare `uuid.uuid4()` 500s on
    the insert), so this borrows `org_member_client`'s seeded user as the
    owner but has `client` — not that user — attempt the dismiss. `client`
    has full ACL on this project (it created it), so this exercises the
    OWNERSHIP check specifically, not the visibility check
    `test_a_persisted_only_job_can_be_dismissed_by_its_true_owner_fix_round_1`
    already covers with 200 for the true owner: before the fix, ANY caller
    got 404 here (owner resolved to None because the job was never
    resident), which would have made a bare "refused" assertion pass for
    the wrong reason — pinning 403 (not 404) is what isolates the fixed
    ownership-fallback path from the pre-fix "nobody can dismiss this"
    behaviour the RED run above demonstrated.
    """
    _engine, session_local = _auth_db

    install_network(build_network(), name="PersistedOnlyOther")
    _save_project(client, "PersistedOnlyOther")
    key = registry_key_for("PersistedOnlyOther")
    solve_queue.reset_for_tests()
    try:
        owner = _acting_user_id(org_member_client)
        jid = _seed_persisted_only(session_local, "PersistedOnlyOther", key, owner)
        assert jid not in solve_queue._jobs

        r = client.post(f"/api/simulation/queue/{jid}/dismiss")
        assert r.status_code == 403, r.text

        mine = client.get("/api/simulation/queue").json()["jobs"]
        assert any(j["id"] == str(jid) for j in mine), (
            "a refused dismissal attempt by a non-owner still hid the row"
        )
    finally:
        solve_queue.reset_for_tests()


# ── the listing must say WHETHER a row is dismissible ──────────────────────
#
# Dismiss is owner-gated (`enqueued_by_user_id`) and terminal-only, but the
# public job payload carried neither fact, so a client had no way to know
# whether the control it renders would 403. The panel's own standing rule is
# that a control must match its route exactly — the trap it documents for
# "Clear finished" is gating on `useAuth().isAdmin`, which is ALSO true for an
# org admin who then gets a guaranteed 403.
#
# A CAPABILITY is emitted, not the identity. `enqueued_by_user_id` would be a
# real disclosure about other users (a plain member could enumerate which of
# their colleagues queued which job) and is more than any client needs.
# `can_dismiss` is exactly the route's precondition, computed for the asking
# caller, and it reveals nothing about anyone else: it is true only for rows
# the caller queued themselves.
#
# It rides through `_redact` untouched — that helper is `{**job, **nulls}`, so
# an added key survives — and needs no redaction branch, because a foreign
# org's job can never have been queued by the caller and is therefore already
# false.


def test_the_listing_marks_your_own_terminal_job_dismissible(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Mine")
    _save_project(client, "Mine")
    solve_queue.reset_for_tests()
    try:
        me = _acting_user_id(client)
        jid = _seed("completed", "Mine", registry_key_for("Mine"), me)

        row = next(
            j for j in client.get("/api/simulation/queue").json()["jobs"]
            if j["id"] == str(jid)
        )
        assert row["can_dismiss"] is True, row
        # The claim must be true: the route agrees with the flag.
        assert client.post(f"/api/simulation/queue/{jid}/dismiss").status_code == 200
    finally:
        solve_queue.reset_for_tests()


def test_the_listing_marks_another_users_job_not_dismissible(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    """
    FULLY VISIBLE and still not the caller's to dismiss. That combination is
    the whole reason the flag exists: visibility and dismissibility are
    different questions, and the payload previously answered only the first.

    The caller has project access — so this row comes back UNREDACTED, with
    its project name and result intact — while `enqueued_by_user_id` belongs
    to somebody else. Same setup as
    `test_a_user_cannot_dismiss_a_job_someone_else_queued`, which pins the 403
    this flag exists to predict.

    The redacted case is deliberately NOT the one tested here. A foreign org's
    row is false for a second, weaker reason (the caller could not have queued
    a job they cannot even see), so it would pass whether or not ownership was
    actually consulted — it cannot distinguish a correct implementation from
    one that just returns False for everything it cannot resolve.
    """
    install_network(build_network(), name="Theirs")
    _save_project(client, "Theirs")
    solve_queue.reset_for_tests()
    try:
        jid = _seed("completed", "Theirs", registry_key_for("Theirs"), uuid.uuid4())

        row = next(
            j for j in client.get("/api/simulation/queue").json()["jobs"]
            if j["id"] == str(jid)
        )
        # Not redacted — the caller genuinely sees this job in full.
        assert row["project_id"] == "Theirs", row
        assert row["can_dismiss"] is False, row
        # And the route agrees — this is the 403 the flag exists to predict.
        assert client.post(f"/api/simulation/queue/{jid}/dismiss").status_code == 403
    finally:
        solve_queue.reset_for_tests()


def test_a_live_job_is_never_marked_dismissible_even_for_its_owner(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    """
    Terminal-only is the other half of the route's precondition. Owning a
    RUNNING job does not make it dismissible — hiding live work from your own
    listing is how a solve gets forgotten about — so the flag must track both
    halves, not just ownership.
    """
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
            row = next(
                j for j in client.get("/api/simulation/queue").json()["jobs"]
                if j["id"] == str(jid)
            )
            assert row["can_dismiss"] is False, (status, row)
            assert client.post(
                f"/api/simulation/queue/{jid}/dismiss"
            ).status_code == 409, status
    finally:
        solve_queue.reset_for_tests()


def test_a_persisted_only_job_still_answers_can_dismiss(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    """
    The row-only path, and the reason `load_by_status` had to start carrying
    `enqueued_by_user_id`.

    Every `interrupted` job after a restart is served from the table rather
    than from `_jobs` — boot reconciliation deliberately never re-admits a
    `running` row to memory (R25's crash-loop guard) — and so is any terminal
    job from before the last restart. Those are exactly the rows a user most
    wants to clear. Answering `can_dismiss` from `_jobs` alone would report
    False for every one of them while `POST /dismiss` happily returned 200,
    which is the same "resident-only lookup" gap Task 21's fix round closed
    inside the dismiss route itself — reintroduced one layer up, in the
    listing, where nothing would have failed loudly.
    """
    from services import solve_job_store

    install_network(build_network(), name="Mine")
    _save_project(client, "Mine")
    solve_queue.reset_for_tests()
    try:
        me = _acting_user_id(client)
        jid = _seed("completed", "Mine", registry_key_for("Mine"), me)
        with solve_queue._lock:
            job = solve_queue._jobs[jid]
        solve_job_store.record_enqueued(
            job, enqueued_by_user_id=me, solver_config_json=None,
        )
        solve_job_store.record_status(job)

        # A restart: memory is empty, the table is not.
        solve_queue.reset_for_tests()

        row = next(
            j for j in client.get("/api/simulation/queue").json()["jobs"]
            if j["id"] == str(jid)
        )
        assert row["can_dismiss"] is True, (
            "a persisted-only job reported as un-dismissable while the dismiss "
            "route would have accepted it — the listing answered from _jobs only"
        )
        assert client.post(f"/api/simulation/queue/{jid}/dismiss").status_code == 200
    finally:
        solve_queue.reset_for_tests()


def test_the_listing_never_emits_the_raw_owner_id(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    """
    The owner travels from `_merged_jobs` to `list_queue` on the job dict
    itself, under `_OWNER_KEY`, and is popped at the one point a response is
    built. That is an efficient shape and a fragile one: the value is a real
    user id riding inside the object that gets serialised, and exactly one
    statement stands between it and every authenticated caller.

    Losing the pop would disclose `enqueued_by_user_id` for every job in the
    queue — letting any member enumerate which colleague queued which work,
    including for rows that are otherwise fully REDACTED to them, since
    `_redact` only nulls the fields in `_REDACTED`. Nothing else would break:
    `can_dismiss` would still be correct, every other assertion in this file
    would still pass, and the extra key would ride out as an additive JSON
    field no client reads.

    Asserted as "no private key survives", not as "`_owner` is absent", so a
    future second transport key is covered by the same test rather than
    needing to be remembered.
    """
    install_network(build_network(), name="Mine")
    _save_project(client, "Mine")
    solve_queue.reset_for_tests()
    try:
        me = _acting_user_id(client)
        _seed("completed", "Mine", registry_key_for("Mine"), me)
        _seed("completed", "Theirs", "some-other-org:deadbeef", uuid.uuid4())

        rows = client.get("/api/simulation/queue").json()["jobs"]
        assert rows, "no rows to check — the test would pass vacuously"
        for row in rows:
            private = [k for k in row if k.startswith("_")]
            assert not private, (
                f"the listing leaked internal key(s) {private} — the owner id "
                "is transported on the job dict and must be popped before the "
                "response is built"
            )
            assert "enqueued_by_user_id" not in row, row
    finally:
        solve_queue.reset_for_tests()

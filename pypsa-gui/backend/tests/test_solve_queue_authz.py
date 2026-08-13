"""
Authorization regression tests for the solve-queue HTTP surface (P-1).

`routers/solve_queue.py` exposes four routes over ONE process-global queue.
`enqueue_solve` has authorized since Step 0a; `list_queue`, `abort_job` and
`clear_finished` took no `db`/`user` at all, so any signed-in user could
enumerate every org's queued project names, abort any org's running solve, and
clear every org's finished jobs. Job ids are small sequential integers, so the
abort was guessable without the listing.

These tests pin the three fixes:

  * **listing REDACTS, it does not filter.** `position` is the 1-based place in
    a GLOBALLY sequential queue — the solver is a shared resource — so hiding
    other orgs' jobs would leave a caller at "position 4" with one job visible
    and no way to reconcile the number. Every job is returned with `id`,
    `status`, `position` and timings intact; `project_id`, `project_key` and
    `error` are nulled for jobs the caller cannot access, and `current` is the
    running job's id only when the caller may see it.
  * **abort answers 404, never 403, when unauthorized** — byte-identical to the
    genuine not-found body, because a 403 confirms the job exists and reopens
    enumeration through a side channel.
  * **clear_finished is global and gated on `User.is_super_admin`** — a global
    clear crosses org boundaries, where an ORG admin has no authority.

Determinism: the module-autouse fixture below neuters the dispatcher, so an
enqueued job parks in `queued` forever and positions are stable. No LP is ever
solved here — `test_solve_queue.py` covers the dispatcher itself.
"""
from __future__ import annotations

import queue
import threading
import time
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
from db.models import OrgMembership, User
from services.solve_queue import solve_queue
from tests.conftest import attach_session, build_network


class _NullQueue:
    """A `queue.Queue` stand-in that swallows work instead of dispatching it."""

    def put(self, item) -> None:
        pass

    def get_nowait(self):
        raise queue.Empty

    def task_done(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _parked_dispatcher(monkeypatch):
    """
    Keep every enqueued job parked in `queued`, so positions are deterministic.

    BOTH patches are load-bearing. Neutering `_ensure_dispatcher_locked` stops a
    dispatcher from being started, but `test_solve_queue.py` leaves a live one
    parked on the singleton's real `queue.Queue` — a daemon thread that cannot
    be killed — and it drains anything `enqueue()` puts there. Swapping `_q` is
    what actually keeps the jobs from being solved; without it this module
    passes alone and fails in a full-suite run.
    """
    monkeypatch.setattr(solve_queue, "_ensure_dispatcher_locked", lambda: None)
    monkeypatch.setattr(solve_queue, "_q", _NullQueue())


def _drop_user(session_local, user_id) -> None:
    """
    Remove a per-test user and everything that FK-references it.

    `projects.created_by` and `project_memberships.assigned_by` carry no
    ON DELETE, and `_reset_tenant_tables` truncates the project tables only
    AFTER this fixture unwinds — so deleting the user first fails on a foreign
    key, the user survives, and every later test using the fixture dies on the
    unique email instead. Sessions and org memberships DO cascade.
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
    """
    Authenticated client for a user carrying `is_super_admin`.

    Both conftest identities are ORG admins with `is_super_admin=False`, which
    is precisely the tier that must be refused. The row is dropped on teardown:
    `_reset_tenant_tables` truncates only the project tables, so a leaked
    super-admin would follow every later test in the session.
    """
    _engine, session_local = _auth_db
    user_id = _seed_user(
        session_local,
        seeded_identity["org_id"],
        email="queue-super-admin@example.com",
        role="admin",
        super_admin=True,
    )
    try:
        with TestClient(main.app) as c:
            yield attach_session(c, session_local, user_id)
    finally:
        _drop_user(session_local, user_id)


@pytest.fixture
def org_member_client(_auth_db, seeded_identity):
    """
    Authenticated client for a PLAIN member of the primary org.

    Both conftest identities carry `role="admin"`, which short-circuits
    `can_access_project` — so neither can express "same org, no access to this
    project", the case that separates an org-boundary check from a project-ACL
    one. This user creates nothing and is assigned to nothing, so every project
    they did not make is invisible to them.
    """
    _engine, session_local = _auth_db
    user_id = _seed_user(
        session_local,
        seeded_identity["org_id"],
        email="queue-org-member@example.com",
        role="member",
        super_admin=False,
    )
    try:
        with TestClient(main.app) as c:
            yield attach_session(c, session_local, user_id)
    finally:
        _drop_user(session_local, user_id)


def _enqueue(test_client, install_network, name: str) -> dict:
    """Create a saved project owned by `test_client`'s org and queue it."""
    install_network(build_network(), name=name)
    r = test_client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    r = test_client.post("/api/simulation/queue", json={"project_id": name})
    assert r.status_code == 200, r.text
    return r.json()


def _jid(job_id) -> uuid.UUID:
    """
    Parse a job id echoed back from JSON (a string) into the UUID `_jobs` is
    keyed on. Also accepts an already-parsed UUID (from a `SolveJob` object
    returned directly by a service call, not round-tripped through JSON) —
    `uuid.UUID(str(u)) == u` for any UUID `u`, so this is safe either way.
    """
    return uuid.UUID(str(job_id))


def _force_status(job_id, status: str) -> None:
    """Drive a parked job into `status` without running the dispatcher."""
    with solve_queue._lock:
        job = solve_queue._jobs[_jid(job_id)]
        job.status = status
        if status in ("completed", "failed", "aborted"):
            job.finished_at = time.time()
            job.error = "boom" if status == "failed" else None
        elif status == "running":
            job.started_at = time.time()


def _clone_job(job_id, times: int) -> None:
    """Append `times` more parked jobs carrying the same job's project key."""
    from services.solve_queue import SolveJob

    with solve_queue._lock:
        template = solve_queue._jobs[_jid(job_id)]
        for _ in range(times):
            new_id = uuid.uuid4()
            solve_queue._jobs[new_id] = SolveJob(
                id=new_id,
                project_id=template.project_id,
                project_key=template.project_key,
                enqueued_at=time.time(),
            )
            solve_queue._order.append(new_id)


@pytest.fixture
def count_queries(_auth_db):
    """Factory: a context manager counting SQL statements on the test engine."""
    import contextlib

    from sqlalchemy import event

    engine, _session_local = _auth_db

    @contextlib.contextmanager
    def _count():
        seen = {"n": 0}

        def _on_execute(conn, cursor, statement, params, context, executemany):
            seen["n"] += 1

        event.listen(engine, "before_cursor_execute", _on_execute)
        try:
            yield seen
        finally:
            event.remove(engine, "before_cursor_execute", _on_execute)

    return _count


def _by_id(payload: dict, job_id) -> dict:
    # `job_id` may be a JSON-echoed string (`some_job["id"]`) or a raw
    # `SolveJob.id` UUID object (`orphan.id`) — normalise both to the string
    # form the JSON listing carries.
    target = str(job_id)
    match = [j for j in payload["jobs"] if j["id"] == target]
    assert match, f"job {job_id} missing from listing {payload}"
    return match[0]


# ── list_queue ──────────────────────────────────────────────────────────────


def test_list_redacts_other_orgs_jobs_and_keeps_the_callers_own(
    client, other_org_client, install_network, tmp_projects_dir
):
    mine = _enqueue(client, install_network, "Alpha")
    theirs = _enqueue(other_org_client, install_network, "Bravo")

    payload = client.get("/api/simulation/queue").json()
    assert len(payload["jobs"]) == 2, payload

    own = _by_id(payload, mine["id"])
    assert own["project_id"] == "Alpha"
    assert own["project_key"] is not None

    other = _by_id(payload, theirs["id"])
    assert other["project_id"] is None, "another org's project NAME leaked"
    assert other["project_key"] is None, "another org's project KEY leaked"
    assert other["error"] is None
    # Redaction is not deletion: the shared-queue facts survive.
    assert other["status"] == "queued"
    assert other["position"] == 2
    assert other["enqueued_at"] == theirs["enqueued_at"]


def test_list_positions_stay_globally_truthful_under_redaction(
    client, other_org_client, install_network, tmp_projects_dir
):
    a1 = _enqueue(client, install_network, "Alpha1")
    b1 = _enqueue(other_org_client, install_network, "Bravo1")
    a2 = _enqueue(client, install_network, "Alpha2")

    payload = client.get("/api/simulation/queue").json()
    assert [j["id"] for j in payload["jobs"]] == [a1["id"], b1["id"], a2["id"]]
    assert [j["position"] for j in payload["jobs"]] == [1, 2, 3]
    # The caller's SECOND job is third in line, not second: the hidden job in
    # front of it still occupies a slot on the shared solver.
    assert _by_id(payload, a2["id"])["position"] == 3
    assert _by_id(payload, b1["id"])["project_id"] is None


def test_list_error_field_is_redacted_for_another_orgs_failed_job(
    client, other_org_client, install_network, tmp_projects_dir
):
    mine = _enqueue(client, install_network, "Alpha")
    theirs = _enqueue(other_org_client, install_network, "Bravo")
    _force_status(mine["id"], "failed")
    _force_status(theirs["id"], "failed")

    payload = client.get("/api/simulation/queue").json()
    assert _by_id(payload, mine["id"])["error"] == "boom"
    assert _by_id(payload, theirs["id"])["error"] is None, "failure detail leaked"


def test_current_is_null_when_the_running_job_belongs_to_another_org(
    client, other_org_client, install_network, tmp_projects_dir
):
    theirs = _enqueue(other_org_client, install_network, "Bravo")
    _force_status(theirs["id"], "running")

    mine_view = client.get("/api/simulation/queue").json()
    assert mine_view["current"] is None, "running job id leaked across orgs"
    assert _by_id(mine_view, theirs["id"])["status"] == "running"

    # The owner still sees the true running id — redaction must not blind them.
    theirs_view = other_org_client.get("/api/simulation/queue").json()
    assert theirs_view["current"] == theirs["id"]
    assert _by_id(theirs_view, theirs["id"])["project_id"] == "Bravo"


def test_list_redacts_a_same_org_project_the_caller_cannot_access(
    client, org_member_client, install_network, tmp_projects_dir
):
    """
    The redaction boundary is the PROJECT ACL, not the org.

    `GET /api/projects/` already filters this member's own listing through
    `can_access_project`, so a queue that answered on org membership alone
    would hand back names the projects endpoint refuses them.
    """
    theirs = _enqueue(client, install_network, "Alpha")
    mine = _enqueue(org_member_client, install_network, "Mine")

    payload = org_member_client.get("/api/simulation/queue").json()
    assert _by_id(payload, mine["id"])["project_id"] == "Mine"
    assert _by_id(payload, theirs["id"])["project_id"] is None, (
        "a same-org project the caller cannot access leaked its name"
    )
    assert _by_id(payload, theirs["id"])["project_key"] is None
    # Still globally truthful: the invisible job in front keeps its slot.
    assert [j["position"] for j in payload["jobs"]] == [1, 2]


def test_list_and_abort_agree_for_a_same_org_project(
    client, org_member_client, install_network, tmp_projects_dir
):
    """
    Listing and abort answer the same question, so a member never sees a job
    in full that they then cannot stop, nor a redacted job they secretly can.
    """
    theirs = _enqueue(client, install_network, "Alpha")

    payload = org_member_client.get("/api/simulation/queue").json()
    assert _by_id(payload, theirs["id"])["project_id"] is None

    denied = org_member_client.post(f"/api/simulation/queue/{theirs['id']}/abort")
    assert denied.status_code == 404, denied.text
    assert solve_queue.get_job(_jid(theirs["id"]))["status"] == "queued"


def test_list_shows_a_project_shared_by_root_membership(
    client, org_member_client, install_network, tmp_projects_dir, project_row, _auth_db
):
    """
    Assigning the member to the project's tree root makes it visible again —
    the batch resolver must honour `ProjectMembership`, not just authorship.
    """
    from db.models import ProjectMembership

    theirs = _enqueue(client, install_network, "Alpha")
    assert _by_id(
        org_member_client.get("/api/simulation/queue").json(), theirs["id"]
    )["project_id"] is None

    _engine, session_local = _auth_db
    row = project_row("Alpha")
    with session_local() as db:
        from services.auth_service import resolve_session_row
        from settings import get_settings

        raw = org_member_client.cookies.get(get_settings().session_cookie_name)
        member_id = resolve_session_row(db, raw).user_id
        db.add(
            ProjectMembership(
                project_id=row.id,
                user_id=member_id,
                assigned_by=member_id,
                assigned_at=datetime.now(tz=timezone.utc),
            )
        )
        db.commit()

    payload = org_member_client.get("/api/simulation/queue").json()
    assert _by_id(payload, theirs["id"])["project_id"] == "Alpha"
    allowed = org_member_client.post(f"/api/simulation/queue/{theirs['id']}/abort")
    assert allowed.status_code == 200, allowed.text


def test_list_queue_query_count_does_not_grow_with_the_job_count(
    client, other_org_client, org_member_client, install_network, tmp_projects_dir,
    count_queries,
):
    """
    The listing is polled every 1.5s while a job is active, so authorization
    must be resolved in a batch. A `can_access_project` call per job would make
    this endpoint's cost track queue depth.

    Measured as a DELTA between a short and a long queue: the absolute count
    includes the auth middleware's own lookups, which are not this route's to
    control, but any per-job work shows up as growth.
    """
    mine = _enqueue(client, install_network, "Alpha")
    theirs = _enqueue(other_org_client, install_network, "Bravo")

    # Warm up first: the very first request on a client also binds its session's
    # active project, which costs a lookup the polling requests never repeat.
    # Measuring against that one-off would compare two different things.
    assert org_member_client.get("/api/simulation/queue").status_code == 200

    with count_queries() as short:
        assert org_member_client.get("/api/simulation/queue").status_code == 200
    baseline = short["n"]

    # 30 more jobs, both accessible and not, so neither branch is skipped.
    _clone_job(mine["id"], 15)
    _clone_job(theirs["id"], 15)

    with count_queries() as long:
        payload = org_member_client.get("/api/simulation/queue").json()
    assert len(payload["jobs"]) == 32

    assert long["n"] == baseline, (
        f"{baseline} queries for 2 jobs, {long['n']} for 32 — authorization is "
        "being resolved per job"
    )


# ── abort_job ───────────────────────────────────────────────────────────────


def test_abort_is_scoped_to_the_caller(
    client, other_org_client, install_network, tmp_projects_dir
):
    mine = _enqueue(client, install_network, "Alpha")
    theirs = _enqueue(other_org_client, install_network, "Bravo")

    denied = client.post(f"/api/simulation/queue/{theirs['id']}/abort")
    assert denied.status_code == 404, denied.text
    assert solve_queue.get_job(_jid(theirs["id"]))["status"] == "queued", (
        "another org's job was aborted"
    )

    allowed = client.post(f"/api/simulation/queue/{mine['id']}/abort")
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["status"] == "aborted"


def test_unauthorized_abort_is_indistinguishable_from_not_found(
    client, other_org_client, install_network, tmp_projects_dir
):
    theirs = _enqueue(other_org_client, install_network, "Bravo")
    # A well-formed id that was never issued — distinct from a MALFORMED one
    # (covered by test_solve_queue_uuid_ids.py). `uuid.UUID(str(...))` still
    # parses this, so the request reaches the real "not in `_jobs`" branch
    # rather than `_parse_job_id` short-circuiting to 404 on a garbage string.
    absent_id = uuid.uuid4()

    denied = client.post(f"/api/simulation/queue/{theirs['id']}/abort")
    missing = client.post(f"/api/simulation/queue/{absent_id}/abort")

    assert denied.status_code == missing.status_code == 404
    assert denied.json() == {"detail": f"No solve job with id {theirs['id']}."}
    assert missing.json() == {"detail": f"No solve job with id {absent_id}."}
    # Same shape, same wording — only the echoed id differs, which the caller
    # supplied. Nothing distinguishes "exists, not yours" from "never existed".
    assert set(denied.json()) == set(missing.json())


def test_abort_of_another_orgs_running_job_does_not_signal_its_stop_event(
    client, other_org_client, install_network, tmp_projects_dir
):
    theirs = _enqueue(other_org_client, install_network, "Bravo")
    stop_event = threading.Event()
    with solve_queue._lock:
        job = solve_queue._jobs[_jid(theirs["id"])]
        job.status = "running"
        job.stop_event = stop_event

    denied = client.post(f"/api/simulation/queue/{theirs['id']}/abort")
    assert denied.status_code == 404, denied.text
    assert not stop_event.is_set(), "another org's running solve was interrupted"


# ── clear_finished ──────────────────────────────────────────────────────────


def test_clear_finished_is_refused_for_a_non_super_admin(
    client, other_org_client, install_network, tmp_projects_dir
):
    mine = _enqueue(client, install_network, "Alpha")
    theirs = _enqueue(other_org_client, install_network, "Bravo")
    _force_status(mine["id"], "completed")
    _force_status(theirs["id"], "completed")

    # The seeded users are ORG admins. A global clear crosses org boundaries,
    # where an org admin has no authority.
    r = client.post("/api/simulation/queue/clear_finished")
    assert r.status_code == 403, r.text
    assert solve_queue.get_job(_jid(theirs["id"])) is not None, (
        "another org's finished job was cleared"
    )
    assert solve_queue.get_job(_jid(mine["id"])) is not None


def test_super_admin_can_clear_finished_globally(
    client, other_org_client, super_admin_client, install_network, tmp_projects_dir
):
    mine = _enqueue(client, install_network, "Alpha")
    theirs = _enqueue(other_org_client, install_network, "Bravo")
    _force_status(mine["id"], "completed")
    _force_status(theirs["id"], "aborted")

    refused = client.post("/api/simulation/queue/clear_finished")
    assert refused.status_code == 403, refused.text

    r = super_admin_client.post("/api/simulation/queue/clear_finished")
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == 2
    assert solve_queue.list_jobs() == []


def test_clear_finished_also_deletes_the_persisted_row(
    client, super_admin_client, install_network, tmp_projects_dir
):
    """
    Task 16a (`routers/solve_queue.py::_merged_jobs`) makes the listing merge
    persisted TERMINAL rows back in for jobs no longer resident. That changes
    what `clear_finished` must do to stay effective: popping `_jobs` alone is
    no longer enough, because the very next `GET /api/simulation/queue` would
    pull the same row straight back out of `solve_jobs` — a super-admin's
    "empty the queue" would silently do nothing from the caller's point of
    view. `_force_status` (unlike a real solve) only mutates memory, so this
    mirrors the terminal status to the table explicitly before clearing, the
    same way `_run_job`'s own `finally` block does.
    """
    from services import solve_job_store

    mine = _enqueue(client, install_network, "ClearMeToo")
    _force_status(mine["id"], "completed")
    solve_job_store.record_status(solve_queue._jobs[_jid(mine["id"])])

    r = super_admin_client.post("/api/simulation/queue/clear_finished")
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == 1

    payload = client.get("/api/simulation/queue").json()
    assert all(j["id"] != mine["id"] for j in payload["jobs"]), (
        "clear_finished cleared memory but the persisted row resurrected the "
        "job on the very next listing"
    )

    from db.models import SolveJobRow
    from db.session import SessionLocal

    with SessionLocal() as db:
        assert db.get(SolveJobRow, _jid(mine["id"])) is None, (
            "clear_finished did not delete the persisted row"
        )


# ── chat tools (Task 4: the same handlers, reached in-process) ───────────────


def test_chat_solve_queue_tools_carry_the_acting_identity(
    client, other_org_client, install_network, tmp_projects_dir
):
    """
    The three tools call the handlers DIRECTLY, so they must inject the acting
    identity via `_route` — otherwise the chat surface keeps the hole the HTTP
    surface just closed (and, once the handlers take `db`/`user`, dies with a
    TypeError instead).
    """
    from fastapi import HTTPException

    from services import chat_tools

    mine = _enqueue(client, install_network, "Alpha")
    theirs = _enqueue(other_org_client, install_network, "Bravo")

    # conftest's autouse `_acting_user` binds the org-A seeded user.
    listing = chat_tools.solve_queue_list()
    assert _by_id(listing, mine["id"])["project_id"] == "Alpha"
    assert _by_id(listing, theirs["id"])["project_id"] is None

    with pytest.raises(HTTPException) as denied:
        chat_tools.solve_queue_abort(str(theirs["id"]))
    assert denied.value.status_code == 404
    assert denied.value.detail == f"No solve job with id {theirs['id']}."
    assert solve_queue.get_job(_jid(theirs["id"]))["status"] == "queued"

    _force_status(mine["id"], "completed")
    with pytest.raises(HTTPException) as refused:
        chat_tools.solve_queue_clear_finished()
    assert refused.value.status_code == 403
    assert solve_queue.get_job(_jid(mine["id"])) is not None

    # The caller's OWN job is still abortable through the same tool.
    assert chat_tools.solve_queue_abort(str(mine["id"]))["id"] == mine["id"]


# ── local mode (Task 6: the packaged desktop app must not regress) ───────────


def test_local_mode_can_list_abort_and_clear(
    _auth_db, monkeypatch, tmp_path, install_network, tmp_projects_dir
):
    """
    Non-regression, not a security assertion: this one passes before and after.

    Drives the REAL desktop path — save a project, enqueue it through the
    route, then list/abort/clear — rather than hand-building a job. An earlier
    version fabricated a `project_key` pointing at no row, which the
    project-ACL tightening then redacted; that said nothing about the packaged
    app, where every job names a project that exists.

    Two things it pins: the desktop user is seeded `is_super_admin=True`
    (`local_mode.py`), which is what keeps `clear_finished` reachable there;
    and `role="admin"` in the local org, which is what
    `accessible_project_ids` short-circuits on so the single tenant sees their
    own queue in full.
    """
    import local_mode

    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    _engine, session_local = _auth_db
    with session_local() as db:
        seeded = local_mode.ensure_local_identity(db)
        assert seeded.is_super_admin is True, (
            "the desktop user is no longer a super-admin; clear_finished will 403"
        )
    try:
        with TestClient(main.app) as local_client:
            local_client.cookies.clear()
            job = _enqueue(local_client, install_network, "Desktop")

            payload = local_client.get("/api/simulation/queue").json()
            assert _by_id(payload, job["id"])["project_id"] == "Desktop"
            assert _by_id(payload, job["id"])["project_key"] is not None

            r = local_client.post(f"/api/simulation/queue/{job['id']}/abort")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "aborted"

            r = local_client.post("/api/simulation/queue/clear_finished")
            assert r.status_code == 200, r.text
            assert r.json()["removed"] == 1
    finally:
        with session_local() as db:
            local_mode.remove_local_identity(db)


def test_local_mode_shows_an_unkeyed_legacy_job(_auth_db, monkeypatch, tmp_path):
    """
    A job with no `project_key` is visible in local mode and redacted under auth.

    Unkeyed jobs are legacy/local artefacts: `enqueue_solve` has stamped a key
    on every job since Step 0a, so under auth an unkeyed job cannot be
    attributed to an org and therefore cannot be authorized — fail closed. In
    local mode there is exactly one tenant, so the only possible owner is the
    caller.
    """
    import local_mode

    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
    try:
        with TestClient(main.app) as local_client:
            local_client.cookies.clear()
            job = solve_queue.enqueue("Legacy")  # no project_key at all
            payload = local_client.get("/api/simulation/queue").json()
            assert _by_id(payload, job.id)["project_id"] == "Legacy"
    finally:
        with session_local() as db:
            local_mode.remove_local_identity(db)


def test_a_deleted_projects_job_is_redacted_but_still_abortable(
    client, install_network, tmp_projects_dir
):
    """
    The one place `_may_see` and `_may_abort` deliberately disagree.

    `_delete_project_db` drops the row and the directory without touching the
    queue, so a queued or running solve outlives its project. There is no ACL
    left to consult, so the IDENTITY fails closed — but refusing the abort too
    would strand the job and block the shared solver with no way to free it.
    """
    job = _enqueue(client, install_network, "Doomed")
    assert client.delete("/api/projects/Doomed").status_code == 200

    payload = client.get("/api/simulation/queue").json()
    assert _by_id(payload, job["id"])["project_id"] is None
    assert _by_id(payload, job["id"])["status"] == "queued"

    r = client.post(f"/api/simulation/queue/{job['id']}/abort")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "aborted"


def test_unkeyed_job_is_redacted_under_auth(client, install_network, tmp_projects_dir):
    mine = _enqueue(client, install_network, "Alpha")
    orphan = solve_queue.enqueue("Legacy")  # no project_key at all

    payload = client.get("/api/simulation/queue").json()
    assert _by_id(payload, mine["id"])["project_id"] == "Alpha"
    assert _by_id(payload, orphan.id)["project_id"] is None
    assert _by_id(payload, orphan.id)["position"] == 2

    denied = client.post(f"/api/simulation/queue/{orphan.id}/abort")
    assert denied.status_code == 404, denied.text

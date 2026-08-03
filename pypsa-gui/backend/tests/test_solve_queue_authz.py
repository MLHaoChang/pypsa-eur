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
    email = "queue-super-admin@example.com"
    with session_local() as db:
        user = User(
            id=uuid.uuid4(),
            email=email,
            password_hash=None,
            status="active",
            is_super_admin=True,
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add(user)
        db.flush()
        db.add(
            OrgMembership(
                id=uuid.uuid4(),
                user_id=user.id,
                org_id=seeded_identity["org_id"],
                role="admin",
            )
        )
        db.commit()
        user_id = user.id
    try:
        with TestClient(main.app) as c:
            yield attach_session(c, session_local, user_id)
    finally:
        with session_local() as db:
            # sessions.user_id / org_memberships.user_id are ON DELETE CASCADE
            # and the SQLite FK pragma is on, so the user row is enough.
            row = db.get(User, user_id)
            if row is not None:
                db.delete(row)
                db.commit()


def _enqueue(test_client, install_network, name: str) -> dict:
    """Create a saved project owned by `test_client`'s org and queue it."""
    install_network(build_network(), name=name)
    r = test_client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    r = test_client.post("/api/simulation/queue", json={"project_id": name})
    assert r.status_code == 200, r.text
    return r.json()


def _force_status(job_id: int, status: str) -> None:
    """Drive a parked job into `status` without running the dispatcher."""
    with solve_queue._lock:
        job = solve_queue._jobs[job_id]
        job.status = status
        if status in ("completed", "failed", "aborted"):
            job.finished_at = time.time()
            job.error = "boom" if status == "failed" else None
        elif status == "running":
            job.started_at = time.time()


def _by_id(payload: dict, job_id: int) -> dict:
    match = [j for j in payload["jobs"] if j["id"] == job_id]
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


# ── abort_job ───────────────────────────────────────────────────────────────


def test_abort_is_scoped_to_the_caller(
    client, other_org_client, install_network, tmp_projects_dir
):
    mine = _enqueue(client, install_network, "Alpha")
    theirs = _enqueue(other_org_client, install_network, "Bravo")

    denied = client.post(f"/api/simulation/queue/{theirs['id']}/abort")
    assert denied.status_code == 404, denied.text
    assert solve_queue.get_job(theirs["id"])["status"] == "queued", (
        "another org's job was aborted"
    )

    allowed = client.post(f"/api/simulation/queue/{mine['id']}/abort")
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["status"] == "aborted"


def test_unauthorized_abort_is_indistinguishable_from_not_found(
    client, other_org_client, install_network, tmp_projects_dir
):
    theirs = _enqueue(other_org_client, install_network, "Bravo")
    absent_id = 10_000_000

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
        job = solve_queue._jobs[theirs["id"]]
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
    assert solve_queue.get_job(theirs["id"]) is not None, (
        "another org's finished job was cleared"
    )
    assert solve_queue.get_job(mine["id"]) is not None


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
    assert solve_queue.get_job(theirs["id"])["status"] == "queued"

    _force_status(mine["id"], "completed")
    with pytest.raises(HTTPException) as refused:
        chat_tools.solve_queue_clear_finished()
    assert refused.value.status_code == 403
    assert solve_queue.get_job(mine["id"]) is not None

    # The caller's OWN job is still abortable through the same tool.
    assert chat_tools.solve_queue_abort(str(mine["id"]))["id"] == mine["id"]


# ── local mode (Task 6: the packaged desktop app must not regress) ───────────


def test_local_mode_can_list_abort_and_clear(_auth_db, monkeypatch, tmp_path):
    """
    Non-regression, not a security assertion: this one passes before and after.

    The desktop build seeds ONE user with `is_super_admin=True` (local_mode.py),
    which is what keeps `clear_finished` reachable there after the gate lands.
    If that seed ever stops setting the flag, the packaged app loses its
    "Clear finished" button — this test is the tripwire.
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
            job = solve_queue.enqueue(
                "Desktop", project_key=f"{local_mode.LOCAL_ORG_ID}:{uuid.uuid4()}"
            )

            payload = local_client.get("/api/simulation/queue").json()
            assert _by_id(payload, job.id)["project_id"] == "Desktop"

            r = local_client.post(f"/api/simulation/queue/{job.id}/abort")
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


def test_unkeyed_job_is_redacted_under_auth(client, install_network, tmp_projects_dir):
    mine = _enqueue(client, install_network, "Alpha")
    orphan = solve_queue.enqueue("Legacy")  # no project_key at all

    payload = client.get("/api/simulation/queue").json()
    assert _by_id(payload, mine["id"])["project_id"] == "Alpha"
    assert _by_id(payload, orphan.id)["project_id"] is None
    assert _by_id(payload, orphan.id)["position"] == 2

    denied = client.post(f"/api/simulation/queue/{orphan.id}/abort")
    assert denied.status_code == 404, denied.text

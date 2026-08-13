"""
Task 16a — the job listing reads persisted jobs, not just the in-memory store.

`GET /api/simulation/queue` served `solve_queue.list_jobs()` only. Every job is
ALSO durably persisted (`services/solve_job_store.py`, Task 13) and boot
reconciliation (Task 15) writes `running -> interrupted` to the table but
deliberately never re-admits that job into `_jobs` — that non-admission IS the
crash-loop guard (R25) and must not change. So before this task `interrupted`
was write-only: no caller could ever observe it, and every restart erased the
whole job history from the panel, because reconciliation restores only
`queued` rows.

`routers/solve_queue.py::_merged_jobs` closes the gap: the listing reads
`solve_queue.list_jobs()` (authoritative for anything resident) UNIONED with
persisted TERMINAL rows for ids that are NOT resident. Nothing is re-admitted
into `_jobs` — the merge happens only at this read boundary, so R25's guard is
untouched.
"""
from __future__ import annotations

import time
import uuid

from services import solve_job_store
from services.solve_queue import solve_queue
from tests.conftest import build_network


def _enqueue(test_client, install_network, name: str) -> dict:
    """Create a saved project owned by `test_client`'s org and queue it."""
    install_network(build_network(), name=name)
    r = test_client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    r = test_client.post("/api/simulation/queue", json={"project_id": name})
    assert r.status_code == 200, r.text
    return r.json()


def _by_id(payload: dict, job_id) -> dict:
    target = str(job_id)
    match = [j for j in payload["jobs"] if j["id"] == target]
    assert match, f"job {job_id} missing from listing {payload}"
    return match[0]


def _persist_terminal(
    job_id, *, status: str, condition: str | None = None, objective: float | None = None,
) -> None:
    """
    Flip a job to a terminal status IN MEMORY and mirror it to the table —
    the same two-step `_run_job`'s `finally` block performs at the end of a
    real solve, done directly so the test is deterministic.
    """
    jid = uuid.UUID(str(job_id))
    with solve_queue._lock:
        job = solve_queue._jobs[jid]
        job.status = status
        job.condition = condition
        job.objective = objective
        job.finished_at = time.time()
    solve_job_store.record_status(solve_queue._jobs[jid])


def _simulate_restart() -> None:
    """
    Wipe the in-memory queue, leaving only the persisted rows — what a real
    process restart does to `SolveQueue`'s fresh singleton (a brand-new
    `_jobs = {}`/`_order = []`, nothing surviving except `solve_jobs`).
    """
    solve_queue.reset_for_tests()


# ── the load-bearing test: `interrupted` becomes observable ─────────────────


def test_an_interrupted_job_appears_in_the_listing(
    client, install_network, tmp_projects_dir,
):
    """
    Drives it the way reconciliation actually does: a job left `running`,
    reconciled at BOOT to `interrupted` (never re-admitted to `_jobs` — R25),
    then fetched through the real HTTP listing.

    Before Task 16a this failed at the final assertion: `_by_id` raised
    "job ... missing from listing" because `list_queue` never read
    `solve_jobs` — the job vanished from the API the instant the restart was
    simulated, even though `reconcile_on_boot` had just written `interrupted`
    to its row a moment earlier.
    """
    job = _enqueue(client, install_network, "CrashedMidSolve")
    jid = uuid.UUID(str(job["id"]))
    with solve_queue._lock:
        mem_job = solve_queue._jobs[jid]
        mem_job.status = "running"
        mem_job.started_at = time.time()
    solve_job_store.record_status(solve_queue._jobs[jid])

    _simulate_restart()
    try:
        interrupted, resumed = solve_job_store.reconcile_on_boot()
        assert interrupted == 1, (interrupted, resumed)
        # The crash-loop guard (R25) — unchanged by this task, and re-checked
        # here so a future regression that "fixes" this by re-admitting the
        # job to `_jobs` is caught immediately.
        assert solve_queue.get_job(jid) is None

        payload = client.get("/api/simulation/queue").json()
        seen = _by_id(payload, job["id"])
        assert seen["status"] == "interrupted"
        assert seen["condition"] == "process_exited"
        assert seen["project_id"] == "CrashedMidSolve"
        assert seen["position"] is None
    finally:
        solve_queue.reset_for_tests()


# ── terminal jobs survive a restart ──────────────────────────────────────────


def test_a_completed_job_survives_a_restart_in_the_listing(
    client, install_network, tmp_projects_dir,
):
    job = _enqueue(client, install_network, "FinishedBeforeRestart")
    _persist_terminal(job["id"], status="completed", condition="optimal", objective=123.5)

    _simulate_restart()
    try:
        interrupted, resumed = solve_job_store.reconcile_on_boot()
        assert (interrupted, resumed) == (0, 0), (interrupted, resumed)

        payload = client.get("/api/simulation/queue").json()
        seen = _by_id(payload, job["id"])
        assert seen["status"] == "completed"
        assert seen["objective"] == 123.5
        assert seen["condition"] == "optimal"
        assert seen["position"] is None
    finally:
        solve_queue.reset_for_tests()


# ── in-memory wins over a lagging/disagreeing row ────────────────────────────


def test_a_live_running_jobs_state_wins_over_its_persisted_row(
    client, install_network, tmp_projects_dir,
):
    """
    `record_status` runs OUTSIDE the dispatcher's lock and is best-effort, so
    the row can legitimately lag what `_jobs` holds. This forces an outright
    DISAGREEMENT directly (a `failed` row for a job that is actually still
    `running` in memory) rather than racing a real solve, so the test is
    deterministic, and asserts the listing reports the LIVE state.

    A merge that reads the persisted row's fields unconditionally, or that
    lets a persisted TERMINAL status override a live one instead of being
    excluded by id, would fail this: it would report `failed` /
    `stale_row_should_not_win` / `-999.0` instead of the live `running` job.
    """
    job = _enqueue(client, install_network, "StaleRowShouldNotWin")
    jid = uuid.UUID(str(job["id"]))
    with solve_queue._lock:
        mem_job = solve_queue._jobs[jid]
        mem_job.status = "running"
        mem_job.started_at = time.time()

    # Imported HERE, not at module top — `db.session.SessionLocal` is
    # monkeypatched onto the file-backed test database by the `_auth_db`
    # fixture, which only runs once a test requests it, AFTER this module was
    # already collected. Same trap `test_solve_jobs_table.py::_row` documents.
    from db.models import SolveJobRow
    from db.session import SessionLocal

    with SessionLocal() as db:
        row = db.get(SolveJobRow, jid)
        row.status = "failed"
        row.objective = -999.0
        row.condition = "stale_row_should_not_win"
        db.commit()

    payload = client.get("/api/simulation/queue").json()
    seen = _by_id(payload, job["id"])
    assert seen["status"] == "running", "a stale persisted row overwrote the live job"
    assert seen["objective"] is None
    assert seen["condition"] is None


# ── authorization: persisted rows go through the same predicate ─────────────


def test_a_persisted_terminal_job_is_redacted_for_another_org(
    client, other_org_client, install_network, tmp_projects_dir,
):
    """
    Same boundary `test_solve_queue_authz.py` pins for LIVE jobs (`_may_see`,
    keyed on `project_key`), exercised against a row that exists ONLY in
    `solve_jobs` (the process was "restarted" so nothing is resident) —
    proving the merge does not create a second, weaker authorization path for
    history. A caller from another org must not learn even the project name of
    a persisted job through this new source.
    """
    mine = _enqueue(client, install_network, "PersistedAlpha")
    _persist_terminal(mine["id"], status="completed", objective=7.0)

    _simulate_restart()
    try:
        payload = other_org_client.get("/api/simulation/queue").json()
        seen = _by_id(payload, mine["id"])
        assert seen["project_id"] is None, "another org's persisted project NAME leaked"
        assert seen["project_key"] is None, "another org's persisted project KEY leaked"
        assert seen["error"] is None
        # Redaction is not deletion: the shared-queue facts survive.
        assert seen["status"] == "completed"
        assert seen["position"] is None

        # The owner still sees it in full through the same merge.
        own_payload = client.get("/api/simulation/queue").json()
        own = _by_id(own_payload, mine["id"])
        assert own["project_id"] == "PersistedAlpha"
        assert own["objective"] == 7.0
    finally:
        solve_queue.reset_for_tests()


# ── ordering ──────────────────────────────────────────────────────────────


def test_persisted_and_live_jobs_are_merged_in_chronological_order(
    client, install_network, tmp_projects_dir,
):
    """
    A caller's history should read oldest-enqueued-first, restart or not — the
    same order the live FIFO queue already presents (`_order`). Enqueue three
    projects in sequence, retire the FIRST to a persisted-only row (simulating
    "this one finished before an intervening restart"), and confirm it still
    sorts FIRST rather than landing at the end just because it was fetched
    from a different source than the still-live jobs.
    """
    first = _enqueue(client, install_network, "First")
    _persist_terminal(first["id"], status="completed", objective=1.0)
    second = _enqueue(client, install_network, "Second")
    third = _enqueue(client, install_network, "Third")

    _simulate_restart()
    try:
        # Only `Second` and `Third` were still `queued` at "restart" time
        # (Task 15's reconciliation resumes those); `First` is persisted-only.
        interrupted, resumed = solve_job_store.reconcile_on_boot()
        assert (interrupted, resumed) == (0, 2), (interrupted, resumed)

        payload = client.get("/api/simulation/queue").json()
        ids = [j["id"] for j in payload["jobs"]]
        assert ids == [first["id"], second["id"], third["id"]]
    finally:
        solve_queue.reset_for_tests()

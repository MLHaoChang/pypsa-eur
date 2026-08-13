"""
R24 — a job solves the config it was ENQUEUED with.

The dispatcher read `ctx.solver_state["solver_config"]` at RUN time, and
`PUT /api/simulation/solver_config` mutates that live — so a config edited after
enqueue silently changed what a queued job solved. Which config a job got also
depended on residency: a resident project used the in-memory value, a
non-resident one whatever `_hydrate_context_from_disk` had loaded from
`solver_config.json`. Durability widens both windows to overnight.

This is a determinism fix, NOT a parameter sweep: one project still cannot be
queued twice, so it can still only carry one config at a time.
"""
from __future__ import annotations

import json
import threading

from tests.conftest import build_network


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def test_editing_the_config_after_enqueue_does_not_change_the_queued_job(
    client, install_network, tmp_projects_dir, monkeypatch,
):
    """
    A BLOCKER job occupies the single dispatcher thread so the Snap job stays
    deterministically QUEUED (not yet dispatched) while we mutate the live
    config — same block-then-assert-queued shape as
    `test_solve_queue.py::test_abort_queued_job_is_skipped`.

    An event set INSIDE the stubbed `run_simulation` is NOT a substitute:
    waiting for that event only proves the dispatcher already READ the config
    and PASSED it as an argument — a PUT arriving after that point can never
    reach it, whether or not the enqueue-time snapshot exists. The race has to
    hold the JOB queued, not the in-flight solve.
    """
    from services import solver_service

    install_network(build_network(), name="Blocker")
    _save_project(client, "Blocker")
    install_network(build_network(), name="Snap")
    _save_project(client, "Snap")
    r = client.put("/api/simulation/solver_config", json={"co2_price": 11.0})
    assert r.status_code == 200, r.text

    seen: list = []
    blocker_entered = threading.Event()
    blocker_release = threading.Event()

    def capture(config, n, lock, stop_event, log_queue, state_update=None):
        if n.name == "Blocker":
            blocker_entered.set()
            blocker_release.wait(60)
            return "ok", "optimal"
        seen.append(config)
        return "ok", "optimal"

    monkeypatch.setattr(solver_service, "run_simulation", capture)

    blocker_job = client.post("/api/simulation/queue", json={"project_id": "Blocker"}).json()
    assert blocker_entered.wait(60), "the blocker job never started"

    job = client.post("/api/simulation/queue", json={"project_id": "Snap"}).json()
    import uuid as _uuid

    from services.solve_queue import solve_queue as _solve_queue

    snap_status = _solve_queue.get_job(_uuid.UUID(job["id"]))["status"]
    assert snap_status == "queued", (
        f"Snap job should still be queued behind Blocker, got {snap_status!r}"
    )

    # Change the live config WHILE Snap's job is still queued, holding its
    # (not yet dispatched) config.
    assert client.put("/api/simulation/solver_config", json={"co2_price": 99.0}).status_code == 200
    blocker_release.set()

    import time
    deadline = time.time() + 60
    while time.time() < deadline and not seen:
        time.sleep(0.05)
    assert seen, "the dispatcher never called run_simulation for Snap"
    assert seen[0].co2_price == 11.0, (
        f"the job solved with {seen[0].co2_price}, the config at RUN time, "
        "not the 11.0 it was queued with"
    )
    assert job["id"]
    assert blocker_job["id"]


def test_the_snapshot_is_persisted_on_the_row(client, install_network, tmp_projects_dir):
    import uuid as _uuid

    from sqlalchemy import select

    from db.models import SolveJobRow
    from db.session import SessionLocal

    install_network(build_network(), name="Stamped")
    _save_project(client, "Stamped")
    assert client.put("/api/simulation/solver_config", json={"co2_price": 42.0}).status_code == 200
    job = client.post("/api/simulation/queue", json={"project_id": "Stamped"}).json()

    with SessionLocal() as db:
        row = db.scalar(select(SolveJobRow).where(SolveJobRow.id == _uuid.UUID(job["id"])))
    assert row is not None
    assert row.solver_config is not None, "no config snapshot was stored"
    assert json.loads(row.solver_config)["co2_price"] == 42.0


# ── Fix round 1: the TOCTOU between publish and stamp ───────────────────────
#
# `enqueue_solve` used to call `solve_queue.enqueue_unique(...)` (which
# publishes the job to the dispatcher's `queue.Queue` and can wake an idle
# dispatcher thread) and only assign `job.solver_config_json = snapshot` on
# the NEXT line. That is a real window: if the dispatcher wins the race, it
# reads the job's config while `solver_config_json` is still `None` and falls
# back to the live context — the exact defect R24 exists to close, for one
# job, in one narrow window.
#
# Reproducing that window with real thread timing is not a reliable test —
# the dispatcher does a lock cycle plus a `record_status` DB write before it
# reaches the read, while the remaining router work is a single attribute
# assignment, so the race is (per review) "practically hard to hit" even
# though it is real. The deterministic, honest check is structural: does
# `SolveQueue.enqueue_unique` / `.enqueue` ever make a job visible to the
# dispatcher (`self._jobs` / `self._q`) in a state where a caller-supplied
# `solver_config_json` has not yet been applied? Both now take it as a
# CONSTRUCTOR argument specifically so the answer is "never, by construction"
# — the job object does not exist until the snapshot is already baked into
# it, so there is no intermediate state left to observe.
#
# These hook the exact publish point (`Queue.put`, called under `self._lock`
# immediately before the job becomes poppable by the dispatcher thread) and
# assert the invariant AT THAT MOMENT — no sleep, no thread, no flakiness.
# Verified (see task-14-report.md) that with `solve_queue.py` reverted to the
# pre-round-1 commit, `enqueue_unique` does not even accept
# `solver_config_json` — these tests fail with a `TypeError`, an honest
# demonstration that the atomicity guarantee did not exist yet.


def test_enqueue_unique_never_publishes_a_job_before_its_snapshot_is_set():
    from services.solve_queue import SolveQueue

    sq = SolveQueue()
    # Prevent a real dispatcher thread from starting: this test asserts a
    # constructor-time invariant, not dispatch behaviour, and a live thread
    # would immediately try (and fail) to hydrate a nonexistent project.
    sq._ensure_dispatcher_locked = lambda: None

    seen_at_publish: list = []
    real_put = sq._q.put

    def spying_put(item, *a, **kw):
        # The job must already carry ITS config by the time it is handed to
        # the queue the dispatcher thread blocks on — this is the earliest
        # point a concurrently-running dispatcher could observe it.
        job = sq._jobs.get(item)
        seen_at_publish.append(None if job is None else job.solver_config_json)
        return real_put(item, *a, **kw)

    sq._q.put = spying_put

    job, created = sq.enqueue_unique(
        "StructGuard", project_key="org:struct-guard", storage_dir="/tmp/struct-guard",
        solver_config_json='{"co2_price": 7.0}',
    )
    assert created
    assert seen_at_publish == ['{"co2_price": 7.0}'], (
        "the job was visible to the dispatcher's queue before its "
        f"solver_config_json snapshot was set: saw {seen_at_publish!r}"
    )
    assert job.solver_config_json == '{"co2_price": 7.0}'


def test_enqueue_also_never_publishes_a_job_before_its_snapshot_is_set():
    """
    `enqueue` (the raw, unconditional append the test harness uses directly)
    got the same constructor-argument treatment as `enqueue_unique`, kept
    symmetric on review — same seam, same guarantee.
    """
    from services.solve_queue import SolveQueue

    sq = SolveQueue()
    sq._ensure_dispatcher_locked = lambda: None

    seen_at_publish: list = []
    real_put = sq._q.put

    def spying_put(item, *a, **kw):
        job = sq._jobs.get(item)
        seen_at_publish.append(None if job is None else job.solver_config_json)
        return real_put(item, *a, **kw)

    sq._q.put = spying_put

    job = sq.enqueue(
        "RawAppend", project_key="org:raw-append", storage_dir="/tmp/raw-append",
        solver_config_json='{"co2_price": 3.0}',
    )
    assert seen_at_publish == ['{"co2_price": 3.0}']
    assert job.solver_config_json == '{"co2_price": 3.0}'


def test_reenqueuing_an_already_active_project_keeps_its_original_snapshot():
    """
    The idempotent path (R15/R16): re-enqueuing a project that already has a
    queued/running job returns the EXISTING job untouched and discards the
    freshly-resolved snapshot the caller passed in. Review called this out as
    correct and correctly commented in fix round 1 — regression guard so
    threading `solver_config_json` through the constructor doesn't
    accidentally start overwriting it on the idempotent branch.
    """
    from services.solve_queue import SolveQueue

    sq = SolveQueue()
    sq._ensure_dispatcher_locked = lambda: None

    first, created1 = sq.enqueue_unique(
        "Dup", project_key="org:dup", storage_dir="/tmp/dup",
        solver_config_json='{"co2_price": 1.0}',
    )
    assert created1
    second, created2 = sq.enqueue_unique(
        "Dup", project_key="org:dup", storage_dir="/tmp/dup",
        solver_config_json='{"co2_price": 999.0}',
    )
    assert not created2
    assert second is first
    assert second.solver_config_json == '{"co2_price": 1.0}', (
        "the idempotent re-enqueue must not overwrite the original job's "
        "config snapshot with the freshly (and pointlessly) resolved one"
    )

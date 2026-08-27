"""
R29 — one operation, and its scope is exactly what the caller could already do
one job at a time.

Every candidate goes through `_may_abort`, the same predicate the single-job
abort route applies, so for every job in the queue the two agree: a caller who
cannot abort a job individually cannot cancel it in bulk. Jobs they may not
touch stay `queued` and otherwise untouched, and the count reports only what
they actually cancelled.

There is deliberately NO global variant and NO super-admin escalation.
`clear_finished` is unconditionally global and gated on `is_super_admin`, but
that is listing hygiene — this destroys queued work, and the two are not the
same operation wearing different gates.
"""
from __future__ import annotations

import uuid

from services.solve_queue import SolveJob, solve_queue
from tests.conftest import build_network


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def _seed(status: str, project_id: str, project_key: str | None) -> uuid.UUID:
    jid = uuid.uuid4()
    with solve_queue._lock:
        job = SolveJob(id=jid, project_id=project_id, project_key=project_key, enqueued_at=0.0)
        job.status = status
        solve_queue._jobs[jid] = job
        solve_queue._order.append(jid)
    return jid


def test_it_cancels_the_callers_queued_jobs_and_reports_the_count(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Mine")
    _save_project(client, "Mine")
    key = registry_key_for("Mine")
    solve_queue.reset_for_tests()
    try:
        a = _seed("queued", "Mine", key)
        b = _seed("queued", "Mine", key)

        r = client.post("/api/simulation/queue/cancel_queued")
        assert r.status_code == 200, r.text
        assert r.json() == {"cancelled": 2}
        assert (solve_queue.get_job(a) or {})["status"] == "aborted"
        assert (solve_queue.get_job(b) or {})["status"] == "aborted"
    finally:
        solve_queue.reset_for_tests()


def test_it_leaves_a_job_the_caller_could_not_abort_individually_untouched(
    client, other_org_client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Mine")
    _save_project(client, "Mine")
    mine_key = registry_key_for("Mine")
    solve_queue.reset_for_tests()
    try:
        mine = _seed("queued", "Mine", mine_key)
        theirs = _seed("queued", "Theirs", f"{uuid.uuid4()}:{uuid.uuid4()}")

        r = client.post("/api/simulation/queue/cancel_queued")
        assert r.json() == {"cancelled": 1}
        assert (solve_queue.get_job(mine) or {})["status"] == "aborted"
        assert (solve_queue.get_job(theirs) or {})["status"] == "queued", (
            "the bulk operation reached a job the caller cannot abort individually"
        )
        # And the two predicates agree from the other side.
        single = client.post(f"/api/simulation/queue/{theirs}/abort")
        assert single.status_code == 404, single.text
    finally:
        solve_queue.reset_for_tests()


def test_a_running_job_is_out_of_scope(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Mine")
    _save_project(client, "Mine")
    key = registry_key_for("Mine")
    solve_queue.reset_for_tests()
    try:
        live = _seed("running", "Mine", key)

        r = client.post("/api/simulation/queue/cancel_queued")
        assert r.json() == {"cancelled": 0}
        assert (solve_queue.get_job(live) or {})["status"] == "running", (
            "stopping a running solve remains the single-job abort"
        )
    finally:
        solve_queue.reset_for_tests()


def test_an_empty_queue_is_a_zero_not_an_error(client):
    solve_queue.reset_for_tests()
    r = client.post("/api/simulation/queue/cancel_queued")
    assert r.status_code == 200, r.text
    assert r.json() == {"cancelled": 0}


def test_a_job_claimed_by_the_dispatcher_mid_sweep_is_left_running_not_killed(
    client, install_network, tmp_projects_dir, registry_key_for, monkeypatch,
):
    """
    Fix round 1 — pins the TOCTOU race a Critical review finding identified:
    `cancel_queued` snapshots the queue, filters to `queued`, then runs a
    per-job `_may_abort` DB read BEFORE cancelling. If the dispatcher claims
    the job (queued -> running) inside that window, the OLD implementation
    called `solve_queue.abort()`, which — seeing `running` — took its abort
    branch and SET THE STOP EVENT, killing an in-flight solve that R29 says
    is out of scope for a bulk cancel.

    `_may_abort` is the natural place to inject the race: it is the last thing
    the route does before touching the job, and it runs with no lock held, so
    flipping the job's status inside it faithfully reproduces "the dispatcher
    claimed it right here" without needing real thread interleaving.
    """
    import threading

    import routers.solve_queue as solve_queue_router

    install_network(build_network(), name="Mine")
    _save_project(client, "Mine")
    key = registry_key_for("Mine")
    solve_queue.reset_for_tests()
    try:
        jid = _seed("queued", "Mine", key)
        stop_event = threading.Event()

        real_may_abort = solve_queue_router._may_abort

        def claim_then_check(db, user, job):
            if job.get("id") == str(jid):
                with solve_queue._lock:
                    row = solve_queue._jobs.get(jid)
                    if row is not None:
                        row.status = "running"
                        row.stop_event = stop_event
            return real_may_abort(db, user, job)

        monkeypatch.setattr(solve_queue_router, "_may_abort", claim_then_check)

        r = client.post("/api/simulation/queue/cancel_queued")
        assert r.status_code == 200, r.text
        assert r.json() == {"cancelled": 0}, (
            "a job claimed mid-sweep must not be counted as cancelled"
        )
        assert (solve_queue.get_job(jid) or {})["status"] == "running", (
            "the bulk cancel must not touch a job the dispatcher already claimed"
        )
        assert stop_event.is_set() is False, (
            "cancel_queued killed an in-flight solve — running jobs are out of scope"
        )
    finally:
        solve_queue.reset_for_tests()

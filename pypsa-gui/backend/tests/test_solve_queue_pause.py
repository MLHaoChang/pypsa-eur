"""
R30 — pause stops the queue STARTING work, not doing it.

A running solve is minutes of solver time that pausing must not throw away, so
pause is a gate on the pop, not a signal to the worker. Resume continues in FIFO
order because the paused worker is parked holding the head of the queue.
"""
from __future__ import annotations

import time
import uuid

from services.solve_queue import SolveJob, solve_queue
from tests.conftest import build_network


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def test_pause_and_resume_round_trip(client):
    solve_queue.reset_for_tests()
    try:
        assert solve_queue.is_paused() is False

        r = client.post("/api/simulation/queue/pause")
        assert r.status_code == 200, r.text
        assert r.json() == {"paused": True}
        assert solve_queue.is_paused() is True
        assert client.get("/api/simulation/queue").json()["paused"] is True

        r = client.post("/api/simulation/queue/resume")
        assert r.json() == {"paused": False}
        assert solve_queue.is_paused() is False
        assert client.get("/api/simulation/queue").json()["paused"] is False
    finally:
        solve_queue.resume()
        solve_queue.reset_for_tests()


def test_a_paused_queue_starts_nothing(client, install_network, tmp_projects_dir):
    solve_queue.reset_for_tests()
    try:
        assert client.post("/api/simulation/queue/pause").status_code == 200
        install_network(build_network(), name="Paused")
        _save_project(client, "Paused")
        job = client.post("/api/simulation/queue", json={"project_id": "Paused"}).json()

        # Give a dispatcher that ignored the pause ample time to start it.
        time.sleep(1.5)
        assert (solve_queue.get_job(uuid.UUID(job["id"])) or {})["status"] == "queued", (
            "the dispatcher started a job while the queue was paused"
        )
    finally:
        solve_queue.resume()
        solve_queue.reset_for_tests()


def test_resuming_lets_the_queued_job_run(client, install_network, tmp_projects_dir, monkeypatch):
    from services import solver_service

    def quick(config, n, lock, stop_event, log_queue, state_update=None):
        return "ok", "optimal"

    monkeypatch.setattr(solver_service, "run_simulation", quick)
    solve_queue.reset_for_tests()
    try:
        assert client.post("/api/simulation/queue/pause").status_code == 200
        install_network(build_network(), name="Resumed")
        _save_project(client, "Resumed")
        job = client.post("/api/simulation/queue", json={"project_id": "Resumed"}).json()
        jid = uuid.UUID(job["id"])
        time.sleep(0.5)
        assert (solve_queue.get_job(jid) or {})["status"] == "queued"

        assert client.post("/api/simulation/queue/resume").status_code == 200
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

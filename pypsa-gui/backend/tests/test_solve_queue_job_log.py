"""
R17/R18/R19 — a job's log belongs to the JOB, and is readable by job id.

`/api/simulation/log_stream` takes no `job_id`: it binds to whatever the ACTIVE
context's `log_queue` is at the instant it opens. So a user viewing project B
could not read project A's queued solve at all, and the frontend had to wait for
the 1.5 s poll to see `running` before attaching — a race the AppHeader carries
a bounded retry for.

Both endpoints answer 404 — byte-identical to the genuine not-found message —
when the caller may not see the job, for the same reason `abort_job` does.
"""
from __future__ import annotations

import json
import threading
import time
import uuid

from services.solve_queue import solve_queue
from tests.conftest import build_network


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def _wait_for_terminal(job_id, timeout: float = 90.0) -> dict:
    job_id = uuid.UUID(str(job_id))
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = solve_queue.get_job(job_id) or {}
        if job.get("status") in ("completed", "failed", "aborted", "interrupted"):
            return job
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} never reached a terminal status")


def test_a_running_jobs_log_is_readable_while_viewing_another_project(
    client, install_network, tmp_projects_dir, monkeypatch,
):
    from services import solver_service

    install_network(build_network(), name="Logged")
    _save_project(client, "Logged")
    install_network(build_network(), name="Elsewhere")
    _save_project(client, "Elsewhere")

    entered = threading.Event()
    release = threading.Event()

    def blocking(config, n, lock, stop_event, log_queue, state_update=None):
        log_queue.put("job log: line one")
        entered.set()
        release.wait(60)
        return "ok", "optimal"

    monkeypatch.setattr(solver_service, "run_simulation", blocking)

    job = client.post("/api/simulation/queue", json={"project_id": "Logged"}).json()
    assert entered.wait(60)
    # The session is looking at "Elsewhere", not at the solving project.
    assert client.post("/api/projects/Elsewhere/activate").status_code == 200

    r = client.get(f"/api/simulation/queue/{job['id']}/log_history")
    assert r.status_code == 200, r.text
    assert any("job log: line one" in line for line in r.json()["lines"]), r.json()
    assert r.json()["status"] == "running"

    release.set()
    _wait_for_terminal(job["id"])


def test_a_terminal_jobs_log_is_retained_and_served(
    client, install_network, tmp_projects_dir, monkeypatch,
):
    from services import solver_service

    install_network(build_network(), name="Retained")
    _save_project(client, "Retained")

    def quick(config, n, lock, stop_event, log_queue, state_update=None):
        log_queue.put("job log: retained line")
        return "ok", "optimal"

    monkeypatch.setattr(solver_service, "run_simulation", quick)

    job = client.post("/api/simulation/queue", json={"project_id": "Retained"}).json()
    done = _wait_for_terminal(job["id"])
    assert done["status"] == "completed", done

    r = client.get(f"/api/simulation/queue/{job['id']}/log_history")
    assert r.status_code == 200, r.text
    assert any("retained line" in line for line in r.json()["lines"]), r.json()
    assert r.json()["status"] == "completed"


def test_an_interrupted_jobs_log_is_served_like_any_other_terminal_jobs():
    """
    R18 — `interrupted` gets no exceptions. Driven off the job table directly so
    the assertion is about the endpoint's status handling, not about killing a
    process mid-test.
    """
    from routers.simulation import BufferedLogQueue
    from services.solve_queue import SolveJob

    solve_queue.reset_for_tests()
    try:
        q = BufferedLogQueue()
        q.put("job log: died under it")
        jid = uuid.uuid4()
        with solve_queue._lock:
            job = SolveJob(id=jid, project_id="Ghost", enqueued_at=0.0)
            job.status = "interrupted"
            job.log_queue = q
            solve_queue._jobs[jid] = job
            solve_queue._order.append(jid)

        assert solve_queue.get_log_queue(jid) is q
        assert q.history() == ["job log: died under it"]
    finally:
        solve_queue.reset_for_tests()


def test_the_log_endpoints_404_for_a_caller_who_may_not_see_the_job(
    client, other_org_client, install_network, tmp_projects_dir, monkeypatch,
):
    from services import solver_service

    install_network(build_network(), name="Private")
    _save_project(client, "Private")

    def quick(config, n, lock, stop_event, log_queue, state_update=None):
        log_queue.put("job log: not yours")
        return "ok", "optimal"

    monkeypatch.setattr(solver_service, "run_simulation", quick)
    job = client.post("/api/simulation/queue", json={"project_id": "Private"}).json()
    _wait_for_terminal(job["id"])

    mine = client.get(f"/api/simulation/queue/{job['id']}/log_history")
    assert mine.status_code == 200, mine.text

    theirs = other_org_client.get(f"/api/simulation/queue/{job['id']}/log_history")
    assert theirs.status_code == 404, theirs.text
    # Byte-identical to the genuine not-found message, so a 404 is not an
    # existence oracle.
    missing = other_org_client.get("/api/simulation/queue/99999/log_history")
    assert missing.status_code == 404
    assert theirs.json()["detail"] == missing.json()["detail"].replace(
        "99999", str(job["id"])
    )

    # `/log_stream`'s half of R18: the authorization check
    # (`_visible_job_or_404`) runs in the endpoint body BEFORE
    # `StreamingResponse` is constructed, so a caller who may not see the job
    # gets a genuine 404 here too — never a 200 whose stream errors instead.
    # A plain (non-streaming) GET is enough to prove this: if the check ever
    # moved inside `generate()`, this request would come back 200 with an
    # `event: done` / error frame rather than 404, and this assertion would
    # catch it.
    theirs_stream = other_org_client.get(f"/api/simulation/queue/{job['id']}/log_stream")
    assert theirs_stream.status_code == 404, theirs_stream.text
    missing_stream = other_org_client.get("/api/simulation/queue/99999/log_stream")
    assert missing_stream.status_code == 404
    assert theirs_stream.json()["detail"] == missing_stream.json()["detail"].replace(
        "99999", str(job["id"])
    )


def test_the_log_stream_serves_history_then_done_and_leaves_no_subscriber(
    client, install_network, tmp_projects_dir, monkeypatch,
):
    """
    R17/R19 happy path over the wire, added after code review: the route is
    reachable, history lines arrive, `event: done` fires carrying the terminal
    payload, and — cheaply and most valuably — the BufferedLogQueue has NO
    subscriber left once the stream closes. That last assertion is what turns
    a by-reading "the subscription cannot leak" verdict into an executed one.

    Deliberately not a suite: interleaving-dependent defects (the
    history/subscribe race, a mid-stream `clear_finished`) need a controlled
    interleaving to reproduce and are covered by the fix itself, not by a
    happy-path exercise of the route.
    """
    from services import solver_service

    install_network(build_network(), name="Streamed")
    _save_project(client, "Streamed")

    def quick(config, n, lock, stop_event, log_queue, state_update=None):
        log_queue.put("job log: streamed line")
        return "ok", "optimal"

    monkeypatch.setattr(solver_service, "run_simulation", quick)

    job = client.post("/api/simulation/queue", json={"project_id": "Streamed"}).json()
    done_job = _wait_for_terminal(job["id"])
    assert done_job["status"] == "completed", done_job

    data_lines: list[str] = []
    done_payload: dict | None = None
    event = None
    with client.stream(
        "GET", f"/api/simulation/queue/{job['id']}/log_stream"
    ) as resp:
        assert resp.status_code == 200, resp.text
        for raw in resp.iter_lines():
            line = raw if isinstance(raw, str) else raw.decode()
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                text = line.split(":", 1)[1].strip()
                if event == "done":
                    done_payload = json.loads(text)
                    break
                data_lines.append(text)

    assert any("job log: streamed line" in line for line in data_lines), data_lines
    assert done_payload is not None, "SSE stream ended without a `done` event"
    assert done_payload["status"] == "completed", done_payload

    q = solve_queue.get_log_queue(uuid.UUID(str(job["id"])))
    assert q is not None
    assert q._subscribers == {}

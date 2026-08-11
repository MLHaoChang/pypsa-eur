"""
R15/R16 — one active job per project, enforced on the SERVER.

`SolveQueue.enqueue` appended unconditionally and the invariant was
re-implemented three times on the client (AppHeader's `enqueuingRef`,
SolveQueuePanel's `activeProjects` set derived from a 1.5 s poll, and
ScenariosPanel's `inFlight` ref) and nowhere on the server. The chat tool had
no guard at all. ScenariosPanel's own comment records the cost: a double click
"really does run every project in the branch twice … with the second run
overwriting the first's results".
"""
from __future__ import annotations

import threading
import time

from services.solve_queue import solve_queue
from tests.conftest import build_network


def _wait_until(pred, timeout: float = 60.0, interval: float = 0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def _block_the_dispatcher(monkeypatch):
    """Hold the first job inside run_simulation so the second is a real duplicate."""
    from services import solver_service

    entered = threading.Event()
    release = threading.Event()

    def blocking(config, n, lock, stop_event, log_queue, state_update=None):
        entered.set()
        release.wait(60)
        return "ok", "optimal"

    monkeypatch.setattr(solver_service, "run_simulation", blocking)
    return entered, release


def test_a_second_enqueue_returns_the_first_job_and_creates_nothing(
    client, install_network, tmp_projects_dir, monkeypatch,
):
    install_network(build_network(), name="Dup")
    _save_project(client, "Dup")
    entered, release = _block_the_dispatcher(monkeypatch)
    try:
        first = client.post("/api/simulation/queue", json={"project_id": "Dup"})
        assert first.status_code == 200, first.text
        assert first.json()["already_queued"] is False
        assert entered.wait(60)

        second = client.post("/api/simulation/queue", json={"project_id": "Dup"})
        assert second.status_code == 200, second.text
        assert second.json()["already_queued"] is True
        assert second.json()["id"] == first.json()["id"]

        listing = client.get("/api/simulation/queue").json()["jobs"]
        assert len([j for j in listing if j["project_id"] == "Dup"]) == 1, listing
    finally:
        release.set()


def test_a_project_with_no_active_job_gets_a_new_job(
    client, install_network, tmp_projects_dir, monkeypatch,
):
    install_network(build_network(), name="Fresh")
    _save_project(client, "Fresh")
    entered, release = _block_the_dispatcher(monkeypatch)
    try:
        r = client.post("/api/simulation/queue", json={"project_id": "Fresh"})
        assert r.status_code == 200, r.text
        assert r.json()["already_queued"] is False
        assert r.json()["status"] in ("queued", "running")
    finally:
        release.set()


def test_a_terminal_job_does_not_block_a_new_one(
    client, install_network, tmp_projects_dir,
):
    """Dedupe is on ACTIVE jobs only — a finished project must be requeueable."""
    install_network(build_network(), name="Again")
    _save_project(client, "Again")
    first = client.post("/api/simulation/queue", json={"project_id": "Again"}).json()
    _wait_until(
        lambda: (solve_queue.get_job(first["id"]) or {}).get("status")
        in ("completed", "failed", "aborted", "interrupted"),
        timeout=90, interval=0.2,
    )
    second = client.post("/api/simulation/queue", json={"project_id": "Again"}).json()
    assert second["already_queued"] is False
    assert second["id"] != first["id"]


def test_the_chat_tool_is_refused_the_same_duplicate(
    client, install_network, tmp_projects_dir, monkeypatch,
):
    """
    R16 — the server is the enforcement point for EVERY caller. The chat tool
    passes the handler's response through unreshaped, so the same refusal
    reaches the model with no second edit.
    """
    from services import chat_tools

    install_network(build_network(), name="ChatDup")
    _save_project(client, "ChatDup")
    entered, release = _block_the_dispatcher(monkeypatch)
    try:
        first = chat_tools.solve_queue_enqueue("ChatDup")
        assert first["already_queued"] is False
        assert entered.wait(60)
        second = chat_tools.solve_queue_enqueue("ChatDup")
        assert second["already_queued"] is True
        assert second["id"] == first["id"]
    finally:
        release.set()

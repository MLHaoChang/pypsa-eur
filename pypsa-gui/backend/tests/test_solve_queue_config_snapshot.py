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

"""
R31 — any terminal job can be run again in one action.

All four terminal statuses are eligible on identical terms, `interrupted`
included: R25 bars only AUTOMATIC re-enqueue at boot (so a job that crashed the
process cannot crash-loop it), never a user's explicit decision to try again.

Subject to R15: requeueing a project that already has an active job returns that
job with `already_queued: true` rather than creating a duplicate.
"""
from __future__ import annotations

import time
import uuid

from services.solve_queue import SolveJob, solve_queue
from tests.conftest import build_network


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def _seed_terminal(status: str, name: str, key: str, storage_dir: str) -> uuid.UUID:
    jid = uuid.uuid4()
    with solve_queue._lock:
        job = SolveJob(
            id=jid, project_id=name, project_key=key,
            storage_dir=storage_dir, enqueued_at=time.time(),
        )
        job.status = status
        job.finished_at = time.time()
        job.solver_config_json = '{"solver_name": "highs", "co2_price": 7.0}'
        solve_queue._jobs[jid] = job
        solve_queue._order.append(jid)
    return jid


def test_every_terminal_status_is_requeueable_interrupted_included(
    client, install_network, tmp_projects_dir, registry_key_for, project_storage_dir,
):
    install_network(build_network(), name="Retry")
    _save_project(client, "Retry")
    key = registry_key_for("Retry")
    where = str(project_storage_dir("Retry"))

    for status in ("completed", "failed", "aborted", "interrupted"):
        solve_queue.reset_for_tests()
        old = _seed_terminal(status, "Retry", key, where)
        r = client.post(f"/api/simulation/queue/{old}/requeue")
        assert r.status_code == 200, (status, r.text)
        body = r.json()
        assert body["already_queued"] is False, (status, body)
        assert body["id"] != str(old), status
        assert body["project_id"] == "Retry"
    solve_queue.reset_for_tests()


def test_the_new_job_inherits_the_original_config_snapshot(
    client, install_network, tmp_projects_dir, registry_key_for, project_storage_dir,
):
    import json as _json
    from sqlalchemy import select

    from db.models import SolveJobRow
    from db.session import SessionLocal

    install_network(build_network(), name="Retry2")
    _save_project(client, "Retry2")
    solve_queue.reset_for_tests()
    old = _seed_terminal(
        "failed", "Retry2", registry_key_for("Retry2"), str(project_storage_dir("Retry2")),
    )
    try:
        body = client.post(f"/api/simulation/queue/{old}/requeue").json()
        with SessionLocal() as db:
            row = db.scalar(select(SolveJobRow).where(SolveJobRow.id == uuid.UUID(body["id"])))
        assert row is not None
        assert _json.loads(row.solver_config)["co2_price"] == 7.0, (
            "requeue re-resolved the config instead of reproducing the run"
        )
    finally:
        solve_queue.reset_for_tests()


def test_a_queued_or_running_job_is_not_requeueable(
    client, install_network, tmp_projects_dir, registry_key_for, project_storage_dir,
):
    install_network(build_network(), name="Busy")
    _save_project(client, "Busy")
    key = registry_key_for("Busy")
    where = str(project_storage_dir("Busy"))
    solve_queue.reset_for_tests()
    try:
        for status in ("queued", "running"):
            jid = _seed_terminal(status, "Busy", key, where)
            with solve_queue._lock:
                solve_queue._jobs[jid].status = status
                solve_queue._jobs[jid].finished_at = None
            r = client.post(f"/api/simulation/queue/{jid}/requeue")
            assert r.status_code == 409, (status, r.text)
    finally:
        solve_queue.reset_for_tests()


def test_requeue_is_subject_to_the_duplicate_rule(
    client, install_network, tmp_projects_dir, registry_key_for, project_storage_dir,
):
    install_network(build_network(), name="Once")
    _save_project(client, "Once")
    key = registry_key_for("Once")
    where = str(project_storage_dir("Once"))
    solve_queue.reset_for_tests()
    try:
        client.post("/api/simulation/queue/pause")
        old = _seed_terminal("completed", "Once", key, where)
        first = client.post(f"/api/simulation/queue/{old}/requeue").json()
        second = client.post(f"/api/simulation/queue/{old}/requeue").json()
        assert second["already_queued"] is True
        assert second["id"] == first["id"]
    finally:
        solve_queue.resume()
        solve_queue.reset_for_tests()


def test_a_job_the_caller_may_not_see_404s(
    client, other_org_client, install_network, tmp_projects_dir,
    registry_key_for, project_storage_dir,
):
    install_network(build_network(), name="Hidden")
    _save_project(client, "Hidden")
    solve_queue.reset_for_tests()
    try:
        old = _seed_terminal(
            "completed", "Hidden", registry_key_for("Hidden"), str(project_storage_dir("Hidden")),
        )
        assert other_org_client.post(f"/api/simulation/queue/{old}/requeue").status_code == 404
    finally:
        solve_queue.reset_for_tests()


def test_requeue_of_a_persisted_only_interrupted_job_survives_a_restart(
    client, install_network, tmp_projects_dir, registry_key_for, project_storage_dir,
):
    """
    Ruling 2: an `interrupted` job's in-memory `SolveJob` does not survive a
    restart, only its row does (`_persisted_public_or_none`). Requeue must
    still work for it, sourcing `storage_dir` / `solver_config_json` from the
    persisted row rather than from memory (which is empty after the reset).
    """
    from services import solve_job_store

    install_network(build_network(), name="Crashed")
    _save_project(client, "Crashed")
    key = registry_key_for("Crashed")
    where = str(project_storage_dir("Crashed"))
    solve_queue.reset_for_tests()
    try:
        old = _seed_terminal("running", "Crashed", key, where)
        with solve_queue._lock:
            solve_queue._jobs[old].status = "running"
            solve_queue._jobs[old].finished_at = None
        # Persist the row (record_enqueued + record_status), then simulate a
        # process restart: drop the in-memory SolveJob and let boot
        # reconciliation flip the surviving `running` row to `interrupted`.
        with solve_queue._lock:
            job = solve_queue._jobs[old]
        solve_job_store.record_enqueued(
            job, enqueued_by_user_id=None, solver_config_json=job.solver_config_json,
        )
        solve_job_store.record_status(job)
        solve_queue.reset_for_tests()
        solve_job_store.reconcile_on_boot()

        r = client.post(f"/api/simulation/queue/{old}/requeue")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["already_queued"] is False
        assert body["id"] != str(old)
        assert body["project_id"] == "Crashed"

        import json as _json
        from sqlalchemy import select

        from db.models import SolveJobRow
        from db.session import SessionLocal

        with SessionLocal() as db:
            row = db.scalar(select(SolveJobRow).where(SolveJobRow.id == uuid.UUID(body["id"])))
        assert row is not None
        assert row.storage_dir == where
        assert _json.loads(row.solver_config)["co2_price"] == 7.0
    finally:
        solve_queue.reset_for_tests()

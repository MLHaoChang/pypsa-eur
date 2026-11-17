"""
Run progress slice 1: the store reader and `GET /api/simulation/queue/{job_id}/estimate`.

The pure estimate rules are in `test_run_estimate.py`. Conditions: GK B plan GO
E1–E5 (`.scratch/run-progress/issues/01-run-estimate.md`).
"""
from __future__ import annotations

import json
import time
import uuid

import pytest

from services import run_estimate, solve_job_store
from services.solve_queue import SolveJob, solve_queue

LOPF = json.dumps({"mode": "lopf", "solver_name": "highs"})


# ── the store reader ────────────────────────────────────────────────────────

def _persist(project_key, kind, *, status="completed", seconds=60.0, config=LOPF, finished=None):
    finished = finished or time.time()
    job = SolveJob(id=uuid.uuid4(), project_id="P", project_key=project_key, kind=kind,
                   enqueued_at=finished - seconds - 1)
    solve_job_store.record_enqueued(job, enqueued_by_user_id=None, solver_config_json=config)
    job.status = status
    job.started_at = finished - seconds
    job.finished_at = finished
    solve_job_store.record_status(job)
    return job


def test_the_store_reads_this_projects_completed_runs_of_this_kind_as_utc():
    key = f"org:{uuid.uuid4()}"
    mine = _persist(key, "solve", seconds=60)
    _persist(key, "solve", status="failed")
    _persist(key, "campus")
    _persist(f"org:{uuid.uuid4()}", "solve")
    rows = solve_job_store.load_completed_runs(key, "solve")
    assert [r["id"] for r in rows] == [mine.id]
    assert rows[0]["finished_at"].tzinfo is not None
    assert run_estimate.duration_s(rows[0]) == pytest.approx(60, abs=1e-3)


def test_for_job_never_counts_the_job_itself():
    key = f"org:{uuid.uuid4()}"
    a = _persist(key, "solve", seconds=60, finished=time.time() - 100)
    _persist(key, "solve", seconds=80, finished=time.time() - 50)
    out = run_estimate.for_job(a.id, key, "solve", LOPF)
    assert out == {"kind": "solve", "seconds": None, "samples": 1, "run_ids": out["run_ids"]}
    assert str(a.id) not in out["run_ids"]


# ── E4: the route ───────────────────────────────────────────────────────────

class _NullQueue:
    def put(self, item) -> None:
        pass


@pytest.fixture
def parked(monkeypatch):
    monkeypatch.setattr(solve_queue, "_ensure_dispatcher_locked", lambda: None)
    monkeypatch.setattr(solve_queue, "_q", _NullQueue())


def _enqueue(test_client, install_network, name):
    from tests.conftest import build_network

    install_network(build_network(), name=name)
    r = test_client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    r = test_client.post("/api/simulation/queue", json={"project_id": name})
    assert r.status_code == 200, r.text
    return r.json()


def _history_for(job_id, seconds_list):
    row = solve_job_store.load_job(uuid.UUID(job_id))
    for i, s in enumerate(seconds_list):
        _persist(row["project_key"], "solve", seconds=s, config=row["solver_config"],
                 finished=time.time() - 1000 + i)


def test_the_route_returns_the_estimate_and_its_basis(client, install_network, tmp_projects_dir, parked):
    job = _enqueue(client, install_network, "EstimateMine")
    r = client.get(f"/api/simulation/queue/{job['id']}/estimate")
    assert r.status_code == 200, r.text
    assert r.json()["seconds"] is None and r.json()["samples"] == 0
    _history_for(job["id"], [100, 200])
    body = client.get(f"/api/simulation/queue/{job['id']}/estimate").json()
    assert body["kind"] == "solve"
    assert body["seconds"] == pytest.approx(150, abs=1e-3)
    assert body["samples"] == 2 and len(body["run_ids"]) == 2


def test_a_job_the_caller_cannot_see_is_indistinguishable_from_none(
    client, install_network, tmp_projects_dir, parked, _auth_db, seeded_identity
):
    from fastapi.testclient import TestClient

    import main
    from tests.conftest import attach_session
    from tests.test_solve_queue_authz import _drop_user, _seed_user

    theirs = _enqueue(client, install_network, "EstimateTheirs")
    _engine, session_local = _auth_db
    uid = _seed_user(session_local, seeded_identity["org_id"],
                     email="estimate-member@example.com", role="member", super_admin=False)
    try:
        with TestClient(main.app) as c:
            member = attach_session(c, session_local, uid)
            hidden = member.get(f"/api/simulation/queue/{theirs['id']}/estimate")
            bogus_id = str(uuid.uuid4())
            missing = member.get(f"/api/simulation/queue/{bogus_id}/estimate")
    finally:
        _drop_user(session_local, uid)
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["detail"].replace(theirs["id"], "X") == missing.json()["detail"].replace(bogus_id, "X")


def test_a_malformed_job_id_is_the_same_404(client):
    r = client.get("/api/simulation/queue/not-a-uuid/estimate")
    assert r.status_code == 404

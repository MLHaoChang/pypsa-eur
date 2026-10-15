"""The campus electrical run is a job in the existing solve queue (E2E demo
plan 2026-10-14, slice D1).

It ran for 11 minutes inside a request with the header saying Idle. Now the
route queues a job of kind ``campus``: one at a time per project, its stage
on ``get_state``'s ``run_job`` and its log on the queue's stream, abortable
between stages, its failure recorded. ``ce.run`` is replaced by a fast stand-in
that walks the stages, so the dispatcher, the claim, the stop event and the
persisted status are real while the study is not (its own tests cover it).
"""
import time
import uuid

import pytest
from fastapi import HTTPException

from db.models import Project, SolveJobRow, User
from services import campus_electrical_service as ce
from services import project_registry
from services import solve_queue as sq


def _wait(predicate, timeout=30.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture(autouse=True)
def _clean_queue():
    sq.solve_queue.reset_for_tests()
    yield
    sq.solve_queue.reset_for_tests()


@pytest.fixture
def hub(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        project_registry.create_root(db, user, "Queue Hub")
        project = db.query(Project).filter_by(name="Queue Hub").one()
        d = ce.campus_dir(project)
        (d / ce.CAMPUS_FILE).write_text("campus: {}\n")
        (project_registry.project_dir(project) / ce.NETWORK_FILE).write_bytes(b"nc")
        yield db, user, project


def fake_run(seen, *, fail=None, gate=None):
    """A stand-in for ``ce.run``: walks the stages, records what it got."""
    def run(project, settings, *, progress=None, should_stop=None):
        seen.append((project, settings))
        for i, name in enumerate(ce.RUN_STAGES):
            if gate is not None and name == "size":
                gate.wait(5)
            if should_stop is not None and should_stop():
                raise ce.CampusRunAborted(f"aborted before {name}")
            if fail == name:
                raise HTTPException(status_code=422, detail="the campus file names no PCC")
            progress(name, i, len(ce.RUN_STAGES))
        progress("done", len(ce.RUN_STAGES), len(ce.RUN_STAGES))
        return {}
    return run


def _job(job_id):
    return sq.solve_queue.get_job(uuid.UUID(job_id))


def test_the_run_is_a_campus_job_with_its_settings_its_stage_and_its_row(hub, monkeypatch):
    db, user, project = hub
    seen = []
    monkeypatch.setattr(ce, "run", fake_run(seen))
    job = ce.enqueue_run(project, {"k": 2, "pf": 0.95}, user)
    assert job["kind"] == sq.KIND_CAMPUS and job["project_id"] == "Queue Hub"
    assert db.get(SolveJobRow, uuid.UUID(job["id"])).kind == sq.KIND_CAMPUS
    assert _wait(lambda: _job(job["id"])["status"] == "completed"), _job(job["id"])
    (got, settings), = seen
    assert settings["k"] == 2 and settings["pf"] == 0.95 and settings["profile"] == "eu_rfg_dcc_ce"
    # the runner rebuilt the project from the job's authorized directory, not its name
    assert str(got.storage_path) == str(project_registry.project_dir(project))
    state = ce.run_job_state(project)
    assert state["id"] == job["id"] and state["status"] == "completed"
    assert state["stage"] == "done" and state["stage_done"] == state["stage_total"]


def test_a_second_run_while_one_is_queued_is_a_409(hub, monkeypatch):
    import threading

    db, user, project = hub
    gate = threading.Event()
    monkeypatch.setattr(ce, "run", fake_run([], gate=gate))
    first = ce.enqueue_run(project, {}, user)
    with pytest.raises(HTTPException) as exc:
        ce.enqueue_run(project, {}, user)
    assert exc.value.status_code == 409 and "campus" in exc.value.detail
    gate.set()
    assert _wait(lambda: _job(first["id"])["status"] == "completed")


def test_an_abort_stops_the_run_between_stages(hub, monkeypatch):
    import threading

    db, user, project = hub
    gate = threading.Event()
    monkeypatch.setattr(ce, "run", fake_run([], gate=gate))
    job = ce.enqueue_run(project, {}, user)
    assert _wait(lambda: (ce.run_job_state(project) or {}).get("stage") == "island_checks")
    sq.solve_queue.abort(uuid.UUID(job["id"]))
    gate.set()
    assert _wait(lambda: _job(job["id"])["status"] == "aborted"), _job(job["id"])
    assert ce.run_job_state(project)["stage"] == "island_checks"      # never reached "size"


def test_a_failing_run_is_a_failed_job_with_its_reason(hub, monkeypatch):
    db, user, project = hub
    monkeypatch.setattr(ce, "run", fake_run([], fail="rank"))
    job = ce.enqueue_run(project, {}, user)
    assert _wait(lambda: _job(job["id"])["status"] == "failed")
    assert "the campus file names no PCC" in _job(job["id"])["error"]


def test_a_run_that_cannot_start_is_refused_in_the_request(hub, monkeypatch):
    db, user, project = hub
    monkeypatch.setattr(ce, "run", lambda *a, **k: pytest.fail("never queued"))
    with pytest.raises(HTTPException) as exc:
        ce.enqueue_run(project, {"k": 0}, user)
    assert exc.value.status_code == 422
    (ce.campus_dir(project) / ce.CAMPUS_FILE).unlink()
    with pytest.raises(HTTPException) as exc:
        ce.enqueue_run(project, {}, user)
    assert exc.value.status_code == 422 and "campus file" in exc.value.detail
    assert sq.solve_queue.list_jobs() == []


def test_get_state_carries_the_latest_job(hub, monkeypatch):
    db, user, project = hub
    assert ce.run_job_state(project) is None
    monkeypatch.setattr(ce, "run", fake_run([]))
    job = ce.enqueue_run(project, {}, user)
    assert _wait(lambda: _job(job["id"])["status"] == "completed")
    assert ce.get_state(project)["run_job"]["id"] == job["id"]

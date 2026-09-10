"""A gridspine run is a job in the EXISTING solve queue (increment 4, task 4).

The spec is explicit that there is no second job system: "the backend wraps
drivers in the existing solve queue — same status/abort machinery". So
`SolveJob` grows a `kind` and the dispatcher picks a runner from it. Everything
a user can already do to a solve — watch it, abort it, see it survive a
restart — has to work on a gridspine run without a new endpoint.

THE ABORT TEST IS THE ONE THAT MATTERS. A study is minutes to hours of
solving; a queue that can start one but not stop it is worse than no queue,
because the only way out is killing the process. The stop_event the queue
already owns reaches `run_study`, which polls it inside the window loop
(drivers/progress.py) — and the abort here proves the whole chain, not just
that the flag was set.

Runtime: one real 24-hour study through the dispatcher (~20 s), plus a
deliberately-aborted one that never finishes its first window.
"""
import json
import time
import uuid

import pytest

from db.models import Project, SolveJobRow, User
from services import gridspine_service as gs
from services import solve_queue as sq


def _wait(predicate, timeout=180.0, interval=0.25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def study(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        created = gs.create_study(
            db, user, "Queued Study",
            config={"hours": 24, "k": 1, "window": 24, "overlap": 0, "screen": False},
        )
        yield db, user, db.get(Project, uuid.UUID(created["id"]))


@pytest.fixture(autouse=True)
def _clean_queue():
    sq.solve_queue.reset_for_tests()
    yield
    sq.solve_queue.reset_for_tests()


# --------------------------------------------------------------------------
# the kind
# --------------------------------------------------------------------------

def test_an_ordinary_solve_job_still_has_the_default_kind():
    job = sq.solve_queue.enqueue("Some Project")
    assert job.kind == sq.KIND_SOLVE
    assert job.to_public(position=1)["kind"] == sq.KIND_SOLVE


def test_a_gridspine_job_carries_its_kind_through_public_view_and_row(study):
    db, user, project = study
    job = gs.run_pipeline(db, project, user=user)
    assert job["kind"] == sq.KIND_GRIDSPINE
    assert job["project_id"] == project.name
    row = db.get(SolveJobRow, uuid.UUID(job["id"]))
    assert row is not None and row.kind == sq.KIND_GRIDSPINE


def test_a_restored_job_keeps_the_kind_it_was_enqueued_with():
    """A restart must not turn a study into a network solve."""
    row = {
        "id": uuid.uuid4(), "project_id": "Restored", "project_key": None,
        "storage_dir": None, "solver_config": None, "enqueued_at": None,
        "kind": sq.KIND_GRIDSPINE,
    }
    job = sq.solve_queue.restore(row)
    assert job.kind == sq.KIND_GRIDSPINE


def test_a_row_without_a_kind_restores_as_a_solve(study):
    """Rows written before the column exists carry NULL; NULL is a solve."""
    row = {
        "id": uuid.uuid4(), "project_id": "Legacy", "project_key": None,
        "storage_dir": None, "solver_config": None, "enqueued_at": None,
    }
    assert sq.solve_queue.restore(row).kind == sq.KIND_SOLVE


# --------------------------------------------------------------------------
# running
# --------------------------------------------------------------------------

def test_a_queued_study_runs_to_completion_and_the_artifacts_are_there(study):
    db, user, project = study
    job = gs.run_pipeline(db, project, user=user)
    job_id = uuid.UUID(job["id"])

    assert _wait(lambda: sq.solve_queue.get_job(job_id)["status"] in sq._TERMINAL)
    finished = sq.solve_queue.get_job(job_id)
    assert finished["status"] == "completed", finished["error"]
    assert finished["started_at"] and finished["finished_at"]

    status = gs.get_stage_status(project)
    assert status["status"] == "completed"
    assert status["selected_hours"]
    assert gs.list_ranked_snapshots(project)


def test_the_stage_progress_reaches_the_jobs_log(study):
    db, user, project = study
    job_id = uuid.UUID(gs.run_pipeline(db, project, user=user)["id"])
    assert _wait(lambda: sq.solve_queue.get_job(job_id)["status"] in sq._TERMINAL)

    history = " ".join(str(line) for line in sq.solve_queue.get_log_queue(job_id).history())
    for stage in ("ingest", "dispatch", "ranking", "loadflow", "handoff"):
        assert stage in history, stage
    assert "1/1" in history or "/1" in history        # done/total, machine-readable


def test_aborting_a_running_study_stops_it_and_records_the_abort(study):
    db, user, project = study
    job_id = uuid.UUID(gs.run_pipeline(db, project, user=user)["id"])

    assert _wait(lambda: sq.solve_queue.get_job(job_id)["status"] == "running", timeout=60)
    # Wait for real work to have started, so the abort lands mid-run rather
    # than in the claim window — the case the stop_event exists for.
    assert _wait(
        lambda: any("dispatch" in str(x) for x in sq.solve_queue.get_log_queue(job_id).history()),
        timeout=120,
    )
    sq.solve_queue.abort(job_id)

    assert _wait(lambda: sq.solve_queue.get_job(job_id)["status"] in sq._TERMINAL, timeout=120)
    assert sq.solve_queue.get_job(job_id)["status"] == "aborted"

    status = gs.get_stage_status(project)
    assert status["status"] == "aborted"
    assert status["error"] and "abort" in status["error"]["cause"].lower()


def test_a_failed_study_records_the_error_and_the_status_reads_failed(study, monkeypatch):
    db, user, project = study
    # A config the driver refuses at run time: the directory it must resume
    # from has no dispatch in it.
    (gs.gridspine_dir(project) / "config.json").write_text(json.dumps({
        **json.loads((gs.gridspine_dir(project) / "config.json").read_text()),
        "from_dispatch": str(gs.gridspine_dir(project) / "nowhere"),
    }))
    job_id = uuid.UUID(gs.run_pipeline(db, project, user=user)["id"])

    assert _wait(lambda: sq.solve_queue.get_job(job_id)["status"] in sq._TERMINAL, timeout=120)
    job = sq.solve_queue.get_job(job_id)
    assert job["status"] == "failed"
    assert job["error"] and "dispatch.csv" in job["error"]


def test_two_studies_of_one_project_do_not_queue_twice(study):
    """`enqueue_unique`'s one-active-job-per-project rule holds for studies."""
    db, user, project = study
    first = gs.run_pipeline(db, project, user=user)
    second = gs.run_pipeline(db, project, user=user)
    assert first["id"] == second["id"]
    assert len([j for j in sq.solve_queue.list_jobs() if j["project_id"] == project.name]) == 1


def test_running_a_study_synchronously_is_still_possible_for_callers_that_wait(study):
    """The CLI and the tests want the answer, not a job."""
    db, _user, project = study
    status = gs.run_pipeline(db, project, queued=False)
    assert status["status"] == "completed"
    assert not sq.solve_queue.list_jobs()

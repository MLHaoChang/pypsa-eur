"""An abort inside the island checks (campus-run speed package): the real
Run, on the I7 hand hub, with the island hours on two worker processes. The
queue's abort ends the run inside the stage, not at the next one; the job
reads aborted, not failed, and no worker process is left behind."""
import json
import multiprocessing
import time
import uuid

import pytest

from gridspine.static.parallel import Stopped
from services import campus_electrical_service as ce
from services import project_registry
from services import solve_queue as sq
from tests import test_campus_electrical_service as _ces
from tests.test_campus_island_cuts_e2e import _network, _sidecar

user_and_db = _ces.user_and_db
pytestmark = pytest.mark.slow                  # a real campus Run up to its island checks


def _wait(predicate, timeout=300.0, interval=0.05):
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


def test_an_abort_inside_the_island_checks_ends_the_run_there(user_and_db, monkeypatch):
    db, user = user_and_db
    monkeypatch.setenv("GRIDSPINE_WORKERS", "2")
    row = project_registry.create_root(db, user, "Abort Hub")
    d = project_registry.ensure_project_dir(row)
    _network().export_to_netcdf(str(d / "network.nc"))
    (d / "island_config.json").write_text(json.dumps(_sidecar()))
    ce.draft(row)
    job, stages, real_run = {}, [], ce.run

    def run(project, settings, *, progress, should_stop):
        def report(stage, done, total):
            progress(stage, done, total)
            stages.append(stage)
            if stage == "island_checks":           # the stage has started: abort inside it
                assert _wait(lambda: "id" in job, timeout=30.0)
                sq.solve_queue.abort(uuid.UUID(job["id"]))
        return real_run(project, settings, progress=report, should_stop=should_stop)

    raised, real_frequency = [], ce.cs.island_frequency_campus

    def frequency(*a, **k):
        try:
            return real_frequency(*a, **k)
        except BaseException as exc:
            raised.append(exc)
            raise

    monkeypatch.setattr(ce, "run", run)
    monkeypatch.setattr(ce.cs, "island_frequency_campus", frequency)
    monkeypatch.setattr(ce.cs, "cuts_campus", lambda *a, **k: pytest.fail("the cuts ran after the abort"))
    job.update(ce.enqueue_run(row, {"k": 1, "invest": False}, user))
    assert _wait(lambda: sq.solve_queue.get_job(uuid.UUID(job["id"]))["status"] in sq._TERMINAL)
    done = sq.solve_queue.get_job(uuid.UUID(job["id"]))
    assert done["status"] == "aborted", done
    assert stages[-1] == "island_checks"            # never reached the cuts or the sizing
    assert [type(e) for e in raised] == [Stopped]  # stopped inside the frequency check, not after it
    assert ce.run_job_state(row)["stage"] == "island_checks"
    run_dir = ce.campus_dir(row) / "run"
    assert not (run_dir / ce.cs.FREQ_SUMMARY_JSON).exists()   # nothing half-written
    assert multiprocessing.active_children() == []

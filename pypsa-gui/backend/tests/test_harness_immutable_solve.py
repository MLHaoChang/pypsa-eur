"""Study harness issue 05, HH-10 (GK B, 3 Nov ruling; C6): an envelope-admitted
queued solve solves the exact input the gate checked.

At enqueue the run's input is captured (network, solver config, the template
sidecars byte for byte, each with its sha256); the job solves that capture on
a private context. A user edit or a sidecar write landing while the job waits
can neither change the solve nor be mistaken for its result.

Real project, real runner, real queue and a real HiGHS solve of the baseline
(one generator at marginal cost 10, one 5 MW load, two hours: objective 100)."""
# ruff: noqa: F811 -- imported pytest fixtures
import json
import time

import pytest
from harness import runner as harness_runner
from services import solve_queue
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline, dispatch, edit, helpers_state, session, wait_until,
)
from tests.test_harness_envelope import envelope_of, granted, profile, refusals, stopped, until  # noqa: F401
from tests.test_harness_envelope_atomic import eh, writer  # noqa: F401
from tests.test_harness_run_skeleton import site  # noqa: F401
from tests.test_harness_runner import detached, frames_of, settle, task_path  # noqa: F401

CAPTURED_OBJECTIVE = 2 * 5.0 * 10.0  # the inputs the gate checked
EDITED_OBJECTIVE = 2 * 5.0 * 50.0  # the user's edit made while the job waits


def ctx():
    return PyPSAService.get_active_context()


@pytest.fixture
def held_queue():
    solve_queue.solve_queue.pause()
    yield solve_queue.solve_queue
    solve_queue.solve_queue.resume()


def _enqueued(eh, site):
    project_id = str(ctx().project_uuid)
    ctx().network.storage_units.at["S1", "capital_cost"] = 1.0  # eh's S1 is extendable: priced, so it solves
    task, path = eh([{"id": "inputs.2", "tool": "solve_queue_enqueue", "args": {"project_id": project_id}}])
    until(harness_runner.Runner("a"), path, task,
          lambda r: r["steps"][1]["status"] not in ("pending", "running"))
    jobs = [j for j in solve_queue.solve_queue.list_jobs() if j["status"] == "queued"]
    assert len(jobs) == 1, (jobs, json.loads(path.read_text())["steps"][1], json.loads(path.read_text())["run"].get("reason"))
    return __import__("uuid").UUID(jobs[0]["id"]), path


def _terminal(job_id, timeout=120.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = solve_queue.solve_queue.get_job(job_id)
        if job and job["status"] in ("completed", "failed", "aborted"):
            q = solve_queue.solve_queue.get_log_queue(job_id)
            job["log"] = list(q._history) if q is not None else []
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not settle")


def test_an_edit_and_a_sidecar_write_while_the_job_waits_do_not_reach_the_solve(eh, site, held_queue):
    _, root = site
    job_id, _ = _enqueued(eh, site)
    template_before = (root / "eh_template.json").read_bytes()
    # The barrier: between the gate's pass (the enqueue) and the job's pickup.
    writer(lambda n: n.generators.__setitem__("marginal_cost", 50.0)).join()
    (root / "eh_template.json").write_text(json.dumps({"id": "eh_site", "site_type": "edited"}))
    held_queue.resume()
    job = _terminal(job_id)
    assert job["status"] == "completed", (job["error"], job["condition"], [l for l in job["log"] if "ERROR" in str(l)][:3])
    # The solve used the capture, and the evidence names it.
    assert job["objective"] == pytest.approx(CAPTURED_OBJECTIVE)
    capture = job["capture"]
    assert len(capture["identity"]) == 64
    assert capture["files"]["eh_template.json"] == __import__("hashlib").sha256(template_before).hexdigest()
    # The user's edit is neither solved nor overwritten, nor taken for the result.
    assert capture["applied"] is False and capture["reason"] == "inputs_changed"
    assert float(ctx().network.generators.at["G1", "marginal_cost"]) == 50.0
    assert json.loads((root / "eh_template.json").read_text())["site_type"] == "edited"


def test_with_no_edit_while_the_job_waits_the_captured_result_is_applied(eh, site, held_queue):
    job_id, _ = _enqueued(eh, site)
    held_queue.resume()
    job = _terminal(job_id)
    assert job["status"] == "completed", (job["error"], job["condition"], [l for l in job["log"] if "ERROR" in str(l)][:3])
    assert job["objective"] == pytest.approx(CAPTURED_OBJECTIVE)
    assert job["capture"]["applied"] is True
    assert EDITED_OBJECTIVE != CAPTURED_OBJECTIVE

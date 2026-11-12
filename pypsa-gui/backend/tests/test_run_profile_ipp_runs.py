"""Study harness 14: eh_ipp_hybrid@1 on the shipped template, as the run's own
project (GK B (52) I2): the run solves, then `run_investment_case` waits on its
job and settles from the case's own record, spending no solve; and the
follower's, the recovery's and `finish_step`'s rules for that step, on a
record the test controls (no LP). The readers and the profile are pinned in
`test_run_profile_ipp.py`."""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

import pypsa
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient  # noqa: F401
from harness import run_ic
from harness import runner as harness_runner
from services import assistant_tasks, chat_tools, flow_service, project_registry
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import helpers_state, session  # noqa: F401
from tests.test_harness_envelope import envelope_of, granted, profile  # noqa: F401
from tests.test_harness_run_loops import Script, call, go, rated, refused, say  # noqa: F401
from tests.test_harness_run_eh import PROJECT, ctx_for
from tests.test_harness_run_report_handoff import fake_report  # noqa: F401
from tests.test_harness_run_skeleton import RUN
from tests.test_harness_runner import detached, fake_report_provider  # noqa: F401

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

REF = "eh_ipp_hybrid@1"
TEMPLATE = "eh_ipp_hybrid"
FIXTURES = (granted, rated)

@pytest.fixture
def baseline(client, session_ctx):
    """The shipped template created through the route as a user does (the
    Library price series materialised, the template's own solver and
    finance settings), then bound as the run's project."""
    name = "IPP hybrid"
    response = client.post(f"/api/projects/from_template/{TEMPLATE}", params={"name": name})
    assert response.status_code == 200, response.text
    # Saved once, as the run's hand-over does (`run_handover`): a template ships
    # no user_ts, and the first solve's write-back would read as an input change.
    response = client.post(f"/api/projects/{name}")
    assert response.status_code == 200, response.text
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    try:
        with chat_tools._acting() as (db, user):
            project = project_registry.resolve_project(db, user, name)
            path = project_registry.project_dir(project)
        yield ctx, str(project.id), path
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


@pytest.fixture
def site(baseline):
    _, project_id, root = baseline
    (root / flow_service.TEMPLATE_FILE).write_text(json.dumps({"id": TEMPLATE}))
    return project_id, root


# The fixtures this file's tests take by name (imported above, used here).
FIXTURES = (granted, rated)


# ── a run on the shipped template (real runner, real solve, real case) ───────


def _case_chapter(record, root):
    from harness import run_report

    doc = run_report.saved(record, root)
    sections = {sec.section_id: sec for sec in doc.sections}
    gaps = [str(row[0]) for row in doc.tables["harness_gaps"].rows if row]
    return sections, gaps


def test_a_run_solves_then_waits_on_the_case_which_spends_no_solve(granted, rated, site):
    """I2 on the real runner: the queued solve, then the case step waits and
    settles completed from the case's own record; the report carries the
    chapter; the case is not counted a solve."""
    from harness import run_report
    from services import run_envelope
    from services.reports.evidence import SECTION_TITLES

    task, path, _ = granted(run={**RUN, "profile": REF})
    script = Script()  # never consulted: no L2 round on a passing gate
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    run = record["run"]
    assert run["status"] == "completed" and run["stage"] == "handoff", (
        run["status"],
        run["stage"],
        [(s["id"], s["status"], s.get("error")) for s in record["steps"]],
    )
    assert script.requests == []
    solve = {s["id"]: s for s in record["steps"] if s["id"].startswith("solve.")}
    assert [(s["tool"], s["status"]) for s in solve.values()] == [
        ("solve_queue_enqueue", "completed"),
        (run_ic.TOOL, "completed"),
    ]  # the solve stage ran the case after the queued solve
    queue, case = solve.values()
    assert list(solve) == ["solve.1", "solve.2"]
    # The step waited (a job), and settled from the case's own record.
    assert case["job"]["kind"] == run_ic.STUDY and case["job"]["outcome"] == "done"
    own = _ctx().solver_state["investment_case"]
    assert own["status"] == "done" and own["started_at"] == case["job"]["started_at"]
    assert case["result"] == {
        "job_id": case["job"]["job_id"],
        "final_status": "completed",
        "study": run_ic.STUDY,
        "started_at": own["started_at"],
        "solves": 0,
    }
    # The profile's gate read the case: the IRR rule passes on its figure, the
    # DSCR is not evaluated on the shipped no-debt case.
    rules = {r["rule"]: r for r in run["stages"]["result_gate"]["gate"]["rules"]}
    assert rules["ipp.ic.pre_tax_irr"]["verdict"] == "pass"
    assert rules["ipp.ic.min_dscr"]["verdict"] == "not_evaluated"
    assert rules["ipp.export.limit"]["verdict"] == "pass"
    assert run["stages"]["explore"]["status"] == "skipped"
    # The report is ready and its gap list is what the report gate accepts.
    report = {r["rule"]: r["verdict"] for r in run["stages"]["report"]["gate"]["rules"]}
    assert report == {"rp.report.status": "pass", "rp.report.unsupported_listed": "pass"}
    sections, gaps = _case_chapter(record, site[1])
    # What holds: the case chapter is established from the case's own report;
    # the profile's other chapter (sizing) has no EH design report on this
    # template, so it is a listed gap (what `unsupported_listed` accepts).
    assert sections["investment_case"].status == "ok"
    assert sections["sizing"].status != "ok"
    assert any(g.startswith(run_report.CHAPTER_GAP + SECTION_TITLES["sizing"]) for g in gaps), gaps
    assert not any(SECTION_TITLES["investment_case"] in g for g in gaps), gaps
    # Acceptance 4: the case spent no solve: the one queued solve is the count.
    envelope = envelope_of(path)
    assert run_envelope.solves_used(envelope) == 1
    used = [(a["tool"]) for a in envelope["audit"] if a["event"] == "used"]
    assert used.count("solve_queue_enqueue") == 1 and used.count(run_ic.TOOL) == 1
    assert not any("reserved" in a for a in envelope["audit"])
    assert run["budget"]["used"]["solves"] == 1



def _ctx():
    return PyPSAService.get_active_context()


# ── the follower, on a record the test controls (no LP) ──────────────────────

HOLD: dict = {}
INSTANCE = "i1"
TICKET = ("t1", "solve.2", "a1")


def _task(tmp_path, *, run=True, status="waiting", job=None, lease_until=None):
    """A task holding one `run_investment_case` step, as the follower leaves it."""
    path = tmp_path / "task.json"
    step = {"id": "solve.2", "tool": run_ic.TOOL, "status": status, "attempt_id": "a1"}
    if job is not None:
        step["job"] = job
    task = {
        "task_id": "t1",
        "project_id": PROJECT,
        "status": "waiting_job",
        "steps": [step],
    }
    if run:
        task["run"] = {
            "profile": REF,
            "status": "running",
            "stage": "solve",
            "stages": {},
            "runner": {
                "lease_owner": INSTANCE,
                "lease_until": time.time() + 600 if lease_until is None else lease_until,
            },
        }
    path.write_text(json.dumps(task))
    return path


def _record(status="done", started=1.0, **extra):
    return {"status": status, "started_at": started, "error": None, **extra}


def _case_ctx(tmp_path, record=None):
    ctx = ctx_for(tmp_path, template=None)
    if record is not None:
        ctx.solver_state[run_ic.STUDY] = record
    return ctx


def _waiting_job(**more):
    return {"job_id": "ad:1", "continuation": "armed", "kind": run_ic.STUDY,
            "started_at": 1.0, "call_id": "a1", "deadline_at": time.time() + 600, **more}


def _step_error(path):
    return json.loads(path.read_text())["steps"][0].get("error")


def _join(ticket=TICKET):
    thread = run_ic._FOLLOWERS.get(tuple(ticket))
    assert thread is not None
    thread.join(10)
    assert not thread.is_alive()


@pytest.fixture
def follow(monkeypatch):
    """The step's job wait stubbed to the task file (`await_job` flips the step
    to waiting in production) and `settle_job` recorded, not applied."""
    calls = []

    def wait_job(ticket, job_id, session_id, *, jobs=None):
        return HOLD["path"]

    monkeypatch.setattr(assistant_tasks, "await_job", wait_job)
    monkeypatch.setattr(assistant_tasks, "settle_job",
                        lambda *a, **k: calls.append(a) or {"ok": True})
    monkeypatch.setattr(run_ic, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(run_ic, "_FOLLOWERS", {})
    return calls


def _wait(tmp_path, monkeypatch, record, begun=1.0, run=True):
    path = _task(tmp_path, run=run, status="waiting", job={"job_id": "ad:1", "continuation": "armed"})
    HOLD["path"] = path
    task = json.loads(path.read_text())
    monkeypatch.setattr(assistant_tasks, "_read", lambda task_id: (path, task))
    ctx = _case_ctx(tmp_path, record)
    return path, run_ic.wait(TICKET, "ad:1", "sess", ctx, begun)


def test_a_done_case_settles_the_step_completed_with_its_result(tmp_path, monkeypatch, follow):
    path, followed = _wait(tmp_path, monkeypatch, _record("done"))
    assert followed is True
    _join()
    (call,) = follow
    assert call[1:5] == (TICKET, "ad:1", "done", "completed")
    assert call[5] == {"study": "investment_case", "started_at": 1.0, "solves": 0}
    # The job carries what the follower and the recovery read.
    job = json.loads(path.read_text())["steps"][0]["job"]
    assert job["kind"] == run_ic.STUDY and job["started_at"] == 1.0 and job["call_id"] == "a1"
    assert job["deadline_at"] > time.time() + run_ic.MAX_FOLLOW_SECONDS - 60


@pytest.mark.parametrize("status, code", [("refused", "no_finance"), ("failed", "engine_error")])
def test_a_refused_or_failed_case_fails_the_step_with_its_code(tmp_path, monkeypatch, follow, status, code):
    path, followed = _wait(tmp_path, monkeypatch, _record(status, error_code=code))
    assert followed is True
    _join()
    (call,) = follow
    assert call[3:5] == (status, "failed") and len(call) == 6 and call[5] is None
    error = _step_error(path)
    assert code in error and status in error


def test_a_replaced_record_settles_the_step_uncertain(tmp_path, monkeypatch, follow):
    """The case was started again (a new `started_at`): the receipt's record is gone."""
    _, followed = _wait(tmp_path, monkeypatch, _record("done", started=2.0))
    assert followed is True
    _join()
    (call,) = follow
    assert call[3:5] == ("unknown", "uncertain") and call[5] is None


def test_a_running_case_is_waited_on_until_its_record_ends(tmp_path, monkeypatch, follow):
    record = _record("running")
    _, followed = _wait(tmp_path, monkeypatch, record)
    assert followed is True and run_ic.following(TICKET) and follow == []  # nothing settled yet
    record["status"] = "done"
    run_ic._WAKE.set()
    try:
        _join()
    finally:
        run_ic._WAKE.clear()
    assert follow[0][3:5] == ("done", "completed")


def test_a_stepwise_chat_plan_step_does_not_take_the_follower(tmp_path, monkeypatch, follow):
    """A plan step (no `run`) keeps the chat rules; nothing waits or settles here."""
    path, followed = _wait(tmp_path, monkeypatch, _record("done"), run=False)
    assert followed is False
    assert not run_ic._FOLLOWERS and follow == []
    step = json.loads(path.read_text())["steps"][0]
    assert step["status"] == "waiting" and "kind" not in step["job"]  # untouched


# ── recovery ─────────────────────────────────────────────────────────────────


@pytest.fixture
def recovery(monkeypatch):
    calls = []
    monkeypatch.setattr(assistant_tasks, "settle_job", lambda *a, **k: calls.append(a) or {"ok": True})
    monkeypatch.setattr(run_ic, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(run_ic, "_FOLLOWERS", {})
    return calls


def test_recovery_follows_again_a_step_whose_case_still_runs(tmp_path, recovery):
    path = _task(tmp_path, job=_waiting_job())
    record = _record("running")
    assert run_ic.recover(path, _case_ctx(tmp_path, record), instance=INSTANCE) == 0
    assert run_ic.following(TICKET) and recovery == []
    # A second tick finds the live follower and starts no other.
    thread = run_ic._FOLLOWERS[TICKET]
    run_ic.recover(path, _case_ctx(tmp_path, record), instance=INSTANCE)
    assert run_ic._FOLLOWERS[TICKET] is thread
    record["status"] = "done"
    run_ic._WAKE.set()
    try:
        _join()
    finally:
        run_ic._WAKE.clear()
    assert recovery[0][3:5] == ("done", "completed")


def test_recovery_settles_a_step_whose_record_is_gone_uncertain(tmp_path, recovery):
    path = _task(tmp_path, job=_waiting_job())
    assert run_ic.recover(path, _case_ctx(tmp_path), instance=INSTANCE) == 1
    (call,) = recovery
    assert call[1:5] == (TICKET, "ad:1", "unknown", "uncertain") and not run_ic._FOLLOWERS


def test_recovery_with_no_resident_context_settles_uncertain_never_completed(tmp_path, recovery):
    """A restart: no ctx to read, so the case cannot be verified."""
    path = _task(tmp_path, job=_waiting_job())
    assert run_ic.recover(path, None, instance=INSTANCE, lapsed_ok=True, now=time.time() + 1e7) == 1
    assert recovery[0][3:5] == ("timed_out", "uncertain")  # past its deadline, whatever the record
    path = _task(tmp_path, job=_waiting_job(), lease_until=1.0)
    assert run_ic.recover(path, None, instance=INSTANCE, lapsed_ok=True) == 1
    assert recovery[1][3:5] == ("unknown", "uncertain")


def test_recovery_settles_past_the_deadline_as_timed_out_even_if_the_case_runs(tmp_path, recovery):
    path = _task(tmp_path, job=_waiting_job(deadline_at=time.time() - 1))
    ctx = _case_ctx(tmp_path, _record("running"))
    assert run_ic.recover(path, ctx, instance=INSTANCE) == 1
    (call,) = recovery
    assert call[1:5] == (TICKET, "ad:1", "timed_out", "uncertain") and not run_ic._FOLLOWERS
    assert _step_error(path) == run_ic.TIMED_OUT


def test_recovery_acts_only_for_the_runs_lease_holder(tmp_path, recovery):
    path = _task(tmp_path, job=_waiting_job())
    assert run_ic.recover(path, _case_ctx(tmp_path), instance="someone-else") == 0 and recovery == []


# ── finish_step: the receipt's own outcome for a run's case step ─────────────


def _finish(tmp_path, monkeypatch, *, result=None, error=None, run=True):
    path = _task(tmp_path, run=run, status="running")
    task = json.loads(path.read_text())
    task.update(owner_id="u1", title="Own site case", status="running")
    path.write_text(json.dumps(task))
    monkeypatch.setattr(assistant_tasks, "_path", lambda task_id: (path, PROJECT, "u1"))
    monkeypatch.setattr(assistant_tasks, "_view", lambda task, *a, **k: None)
    monkeypatch.setattr(assistant_tasks, "_advance_run", lambda *a, **k: None)
    assistant_tasks.finish_step(TICKET, result=result, error=error)
    return json.loads(path.read_text())["steps"][0]


@pytest.mark.parametrize(
    "result, error",
    [
        (None, HTTPException(409, {"error_kind": "study_busy", "message": "a study is running"})),
        (None, HTTPException(422, {"error_kind": "finance_missing", "message": "no finance inputs"})),
        ({"status": "refused", "error_kind": "not_solved"}, None),
        ({"status": "done"}, None),
    ],
    ids=["busy", "invalid", "refusal_receipt", "not_a_start_receipt"],
)
def test_a_receipt_that_is_not_a_start_fails_the_step(tmp_path, monkeypatch, result, error):
    step = _finish(tmp_path, monkeypatch, result=result, error=error)
    assert step["status"] == "failed" and "did not start" in step["error"]


def test_a_started_case_that_was_not_followed_is_uncertain_never_completed(tmp_path, monkeypatch):
    step = _finish(tmp_path, monkeypatch, result={"status": "running", "started_at": 1.0})
    assert step["status"] == "uncertain" and "not followed" in step["error"]
    assert "result" not in step


def test_a_chat_plan_case_step_keeps_the_chat_rules(tmp_path, monkeypatch):
    """No `run`: the plan's own outcome (a running receipt is a completed start); the run's rule is not applied."""
    step = _finish(tmp_path, monkeypatch, result={"status": "running", "started_at": 1.0}, run=False)
    assert step["status"] == "completed" and "error" not in step

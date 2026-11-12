"""Study harness 14: eh_ipp_hybrid@1 on the shipped template, as the run's own
project (GK B (52) I2): the run solves, then `run_investment_case` waits on its
job and settles from the case's own record, spending no solve; and the
follower's, the recovery's and `finish_step`'s rules for that step, on a
record the test controls (no LP). The readers and the profile are pinned in
`test_run_profile_ipp.py`."""
from __future__ import annotations
import json
import sys
import threading
import types
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


# ── mutant pins: the follower's, the recovery's and the readers' exact rules ─


def _thread(*, alive):
    release = threading.Event()
    worker = threading.Thread(target=release.wait, daemon=True)
    worker.start()
    if not alive:
        release.set()
        worker.join(5)
    return worker, release


def test_the_follow_deadline_is_six_hours():
    assert run_ic.MAX_FOLLOW_SECONDS == 6 * 3600


def test_the_wait_writes_a_deadline_six_hours_ahead(tmp_path, monkeypatch, follow):
    path, _ = _wait(tmp_path, monkeypatch, _record("done"))
    _join()
    job = json.loads(path.read_text())["steps"][0]["job"]
    assert abs(job["deadline_at"] - (time.time() + 6 * 3600)) < 120


def test_only_the_case_tool_s_step_is_an_ic_step():
    assert run_ic.is_ic_step({"tool": "run_investment_case"}) is True
    assert run_ic.is_ic_step({"tool": "solve_queue_enqueue"}) is False
    assert run_ic.is_ic_step({}) is False
    for other in (None, "run_investment_case", ["run_investment_case"], 3):
        assert run_ic.is_ic_step(other) is False


def test_the_record_is_read_as_plain_values_or_not_at_all():
    assert run_ic._record(None) is None
    ctx = types.SimpleNamespace(solver_state_lock=threading.RLock(), solver_state={})
    assert run_ic._record(ctx) is None
    ctx.solver_state[run_ic.STUDY] = "running"
    assert run_ic._record(ctx) is None
    worker = object()
    ctx.solver_state[run_ic.STUDY] = {"status": "done", "started_at": 2.0, "error": "e", "error_code": "c",
                                      "thread": worker, "private": 1}
    assert run_ic._record(ctx) == {"status": "done", "started_at": 2.0, "error": "e", "error_code": "c",
                                   "thread": worker}
    ctx.solver_state[run_ic.STUDY] = {"status": "done"}
    assert run_ic._record(ctx) == {"status": "done", "started_at": None, "error": None, "error_code": None,
                                   "thread": None}


def test_the_failed_outcome_prefers_the_code_then_the_error_text():
    both = {"status": "failed", "started_at": 1.0, "error_code": "CODE", "error": "TEXT"}
    assert run_ic.outcome(both, 1.0) == ("failed", "failed", "The investment case ended failed: CODE")
    text = {"status": "refused", "started_at": 1.0, "error_code": None, "error": "TEXT"}
    assert run_ic.outcome(text, 1.0) == ("refused", "failed", "The investment case ended refused: TEXT")
    neither = {"status": "aborted", "started_at": 1.0}
    assert run_ic.outcome(neither, 1.0) == ("aborted", "failed", "The investment case ended aborted: ")
    long = {"status": "failed", "started_at": 1.0, "error": "x" * 500}
    assert run_ic.outcome(long, 1.0)[2].endswith(": " + "x" * 200)


def test_a_record_with_no_started_at_or_a_receipt_with_none_binds_nothing():
    gone = "The case's own record is gone or was replaced; it cannot be verified."
    for record, begun in (({"status": "done", "started_at": None}, None), ({"status": "done"}, None),
                          ({"status": "done", "started_at": 1.0}, None), ("done", 1.0), ([], 1.0),
                          ({"status": "done", "started_at": None}, 1.0)):
        assert run_ic.outcome(record, begun) == ("unknown", "uncertain", gone)
    assert run_ic.outcome({"status": "running", "started_at": 1.0}, 1.0) == (
        "unknown", "uncertain", "The case's outcome cannot be verified.")


def test_a_worker_is_alive_unless_it_ran_and_finished():
    live, release = _thread(alive=True)
    dead, _ = _thread(alive=False)
    try:
        assert run_ic._alive({"thread": live}) is True
        assert run_ic._alive({"thread": dead}) is False
    finally:
        release.set()
    assert run_ic._alive({"thread": threading.Thread(target=lambda: None)}) is True  # never started
    assert run_ic._alive({"thread": None}) is True and run_ic._alive({}) is True
    assert run_ic._alive({"thread": object()}) is True


def test_a_ticket_is_followed_only_by_a_live_follower(monkeypatch):
    live, release = _thread(alive=True)
    dead, _ = _thread(alive=False)
    monkeypatch.setattr(run_ic, "_FOLLOWERS", {TICKET: live, ("t1", "x", "a1"): dead})
    try:
        assert run_ic.following(TICKET) is True and run_ic.following(list(TICKET)) is True
        assert run_ic.following(("t1", "x", "a1")) is False
        assert run_ic.following(("t1", "none", "a1")) is False
    finally:
        release.set()


def test_a_finished_follower_is_dropped_when_another_starts(tmp_path, monkeypatch, follow):
    dead, _ = _thread(alive=False)
    live, release = _thread(alive=True)
    other = ("t1", "other", "a1")
    monkeypatch.setattr(run_ic, "_FOLLOWERS", {other: dead, ("t1", "live", "a1"): live})
    try:
        _wait(tmp_path, monkeypatch, _record("done"))
        _join()
        assert other not in run_ic._FOLLOWERS and ("t1", "live", "a1") in run_ic._FOLLOWERS
    finally:
        release.set()


def test_a_wait_with_no_job_path_follows_nothing(tmp_path, monkeypatch, follow):
    path = _task(tmp_path, status="waiting", job={"job_id": "ad:1"})
    monkeypatch.setattr(assistant_tasks, "_read", lambda task_id: (path, json.loads(path.read_text())))
    monkeypatch.setattr(assistant_tasks, "await_job", lambda *a, **k: None)
    assert run_ic.wait(TICKET, "ad:1", "sess", _case_ctx(tmp_path, _record("done")), 1.0) is False
    assert not run_ic._FOLLOWERS and follow == []
    assert "kind" not in json.loads(path.read_text())["steps"][0]["job"]


@pytest.mark.parametrize("ticket, status", [
    (("t1", "solve.2", "other"), "waiting"),  # another attempt's step
    (TICKET, "running"),  # not waiting
    (("t1", "no.such", "a1"), "waiting"),  # no such step
])
def test_the_job_is_marked_only_on_the_waiting_step_of_the_same_attempt(tmp_path, monkeypatch, follow, ticket, status):
    path = _task(tmp_path, status=status, job={"job_id": "ad:1", "continuation": "armed"})
    HOLD["path"] = path
    task = json.loads(path.read_text())
    monkeypatch.setattr(assistant_tasks, "_read", lambda task_id: (path, task))
    before = path.read_text()
    run_ic.wait(ticket, "ad:1", "sess", _case_ctx(tmp_path, _record("done")), 1.0)
    thread = run_ic._FOLLOWERS.get(tuple(ticket))
    if thread is not None:
        thread.join(10)
    assert path.read_text() == before


def test_the_follower_settles_at_once_when_there_is_no_record(tmp_path, monkeypatch, follow):
    path = _task(tmp_path, status="waiting", job=_waiting_job())
    run_ic._start_follower(path, TICKET, "ad:1", _case_ctx(tmp_path), 1.0)
    _join()
    assert follow[0][3:5] == ("unknown", "uncertain")


def test_the_follower_settles_when_the_record_was_replaced_while_it_ran(tmp_path, monkeypatch, follow):
    path = _task(tmp_path, status="waiting", job=_waiting_job())
    record = _record("running")
    run_ic._start_follower(path, TICKET, "ad:1", _case_ctx(tmp_path, record), 1.0)
    time.sleep(0.1)
    assert follow == [] and run_ic.following(TICKET)
    record["started_at"] = 2.0
    _join()
    assert follow[0][3:5] == ("unknown", "uncertain")


def test_the_follower_settles_when_the_case_worker_died_with_the_record_still_running(tmp_path, monkeypatch, follow):
    path = _task(tmp_path, status="waiting", job=_waiting_job())
    dead, _ = _thread(alive=False)
    record = _record("running", thread=dead)
    run_ic._start_follower(path, TICKET, "ad:1", _case_ctx(tmp_path, record), 1.0)
    _join()
    assert follow[0][3:5] == ("unknown", "uncertain")


def test_the_follower_gives_up_at_its_deadline(tmp_path, monkeypatch, follow):
    monkeypatch.setattr(run_ic, "MAX_FOLLOW_SECONDS", 0.15)
    path = _task(tmp_path, status="waiting", job=_waiting_job())
    start = time.monotonic()
    run_ic._start_follower(path, TICKET, "ad:1", _case_ctx(tmp_path, _record("running")), 1.0)
    time.sleep(0.05)
    assert follow == []  # it waits until the deadline, not before
    _join()
    assert time.monotonic() - start >= 0.14 and follow[0][3:5] == ("unknown", "uncertain")


def test_settle_writes_the_error_only_for_a_returned_view_and_an_error(tmp_path, monkeypatch):
    path = _task(tmp_path, status="waiting", job=_waiting_job())
    views = []
    monkeypatch.setattr(assistant_tasks, "settle_job", lambda *a, **k: views[0])
    views.append(None)
    assert run_ic.settle(path, TICKET, "ad:1", _record("failed", error_code="c"), 1.0) is None
    assert _step_error(path) is None  # no view: nothing was settled, nothing written
    views[0] = {"ok": True}
    assert run_ic.settle(path, TICKET, "ad:1", _record("done"), 1.0) == {"ok": True}
    assert _step_error(path) is None  # a completed case has no error
    assert run_ic.settle(path, TICKET, "ad:1", _record("failed", error_code="c"), 1.0) == {"ok": True}
    assert "failed" in _step_error(path) and "c" in _step_error(path)


# recovery


def _two_steps(path, first):
    task = json.loads(path.read_text())
    task["steps"].insert(0, first)
    path.write_text(json.dumps(task))


@pytest.mark.parametrize("task", [None, {}, {"run": None}, {"run": {}}, {"run": {"runner": None}},
                                  {"run": {"runner": {}}}])
def test_recovery_of_a_task_with_no_run_lease_returns_zero(tmp_path, recovery, task):
    path = _task(tmp_path, job=_waiting_job())
    if task is None:
        path.unlink()
    else:
        full = json.loads(path.read_text())
        full.pop("run")
        full.update(task)
        path.write_text(json.dumps(full))
    assert run_ic.recover(path, _case_ctx(tmp_path), instance=INSTANCE) == 0 and recovery == []


def test_a_lapsed_lease_is_taken_over_only_at_boot_and_only_once_over(tmp_path, recovery):
    now = 5000.0
    other = dict(instance="other")

    def attempt(lease_until, **kw):
        path = _task(tmp_path, job=_waiting_job(), lease_until=lease_until)
        return run_ic.recover(path, None, now=now, **other, **kw)

    assert attempt(now - 1, lapsed_ok=True) == 1
    assert attempt(now, lapsed_ok=True) == 1  # the lease ends at `now`: lapsed
    assert attempt(now + 1, lapsed_ok=True) == 0
    assert attempt(now - 1) == 0  # not at boot: another holder's lease is not taken
    assert attempt(now - 1, lapsed_ok=False) == 0
    assert len(recovery) == 2


def test_the_holder_recovers_even_on_a_live_lease_and_a_lapsed_one_needs_no_flag(tmp_path, recovery):
    path = _task(tmp_path, job=_waiting_job(), lease_until=1.0)
    assert run_ic.recover(path, None, instance=INSTANCE, now=5000.0) == 1


def test_the_case_is_followed_only_on_the_runs_own_context(tmp_path, recovery):
    path = _task(tmp_path, job=_waiting_job())
    ctx = _case_ctx(tmp_path, _record("running"))
    ctx.project_uuid = "22222222-2222-2222-2222-222222222222"
    assert run_ic.recover(path, ctx, instance=INSTANCE) == 1  # another project's case is no evidence
    assert recovery[0][3:5] == ("unknown", "uncertain") and not run_ic._FOLLOWERS
    bare = types.SimpleNamespace(solver_state_lock=threading.RLock(),
                                 solver_state={run_ic.STUDY: _record("running")})
    path = _task(tmp_path, job=_waiting_job())
    assert run_ic.recover(path, bare, instance=INSTANCE) == 1 and not run_ic._FOLLOWERS


def test_recovery_ignores_a_step_that_is_not_a_waiting_case(tmp_path, recovery):
    for status, kind in (("running", run_ic.STUDY), ("completed", run_ic.STUDY), ("waiting", "eh_study"),
                         ("waiting", None)):
        path = _task(tmp_path, status=status, job=_waiting_job(kind=kind))
        assert run_ic.recover(path, _case_ctx(tmp_path), instance=INSTANCE) == 0
    path = _task(tmp_path, status="waiting")  # no job at all
    assert run_ic.recover(path, _case_ctx(tmp_path), instance=INSTANCE) == 0
    assert recovery == []


def test_recovery_goes_on_past_a_step_it_ignores(tmp_path, recovery):
    path = _task(tmp_path, job=_waiting_job())
    _two_steps(path, {"id": "solve.1", "tool": "solve_queue_enqueue", "status": "completed", "attempt_id": "a0"})
    assert run_ic.recover(path, _case_ctx(tmp_path), instance=INSTANCE) == 1


def test_recovery_leaves_a_step_a_live_follower_already_holds(tmp_path, recovery):
    live, release = _thread(alive=True)
    run_ic._FOLLOWERS[TICKET] = live
    try:
        path = _task(tmp_path, job=_waiting_job(deadline_at=time.time() - 10))
        assert run_ic.recover(path, _case_ctx(tmp_path), instance=INSTANCE) == 0
        assert recovery == [] and run_ic._FOLLOWERS[TICKET] is live
    finally:
        release.set()


def test_the_deadline_is_late_at_its_own_instant_and_not_before(tmp_path, recovery):
    now = 5000.0
    for deadline, late in ((now - 1, True), (now, True), (now + 1, False)):
        recovery.clear()
        path = _task(tmp_path, job=_waiting_job(deadline_at=deadline))
        assert run_ic.recover(path, _case_ctx(tmp_path), instance=INSTANCE, now=now) == 1
        assert recovery[0][3:5] == (("timed_out", "uncertain") if late else ("unknown", "uncertain"))
        assert (_step_error(path) == run_ic.TIMED_OUT) is late
    for deadline in (None, "soon", [1]):  # no readable deadline is never late
        recovery.clear()
        path = _task(tmp_path, job=_waiting_job(deadline_at=deadline))
        run_ic.recover(path, _case_ctx(tmp_path), instance=INSTANCE, now=now)
        assert recovery[0][3:5] == ("unknown", "uncertain")
    recovery.clear()
    path = _task(tmp_path, job=_waiting_job(deadline_at=7))  # an int is a deadline
    run_ic.recover(path, _case_ctx(tmp_path), instance=INSTANCE, now=now)
    assert recovery[0][3:5] == ("timed_out", "uncertain")


@pytest.mark.parametrize("late", [True, False])
def test_a_step_that_could_not_be_settled_is_not_counted_nor_given_an_error(tmp_path, monkeypatch, late):
    monkeypatch.setattr(assistant_tasks, "settle_job", lambda *a, **k: None)
    monkeypatch.setattr(run_ic, "_FOLLOWERS", {})
    path = _task(tmp_path, job=_waiting_job(deadline_at=time.time() - 1 if late else time.time() + 600))
    assert run_ic.recover(path, _case_ctx(tmp_path), instance=INSTANCE) == 0
    assert _step_error(path) is None


@pytest.mark.parametrize("record_args", [
    {"begun": None},  # the receipt bound no started_at
    {"record": _record("running", started=2.0)},  # a replaced record
    {"record": _record("done")},  # not running any more
    {"record": "dead"},  # its worker died
])
def test_a_case_is_followed_again_only_when_it_is_that_receipts_live_run(tmp_path, recovery, record_args):
    job = _waiting_job(started_at=record_args.get("begun", 1.0))
    record = record_args.get("record", _record("running"))
    if record == "dead":
        dead, _ = _thread(alive=False)
        record = _record("running", thread=dead)
    path = _task(tmp_path, job=job)
    assert run_ic.recover(path, _case_ctx(tmp_path, record), instance=INSTANCE) == 1
    assert not run_ic._FOLLOWERS and len(recovery) == 1


def test_a_live_worker_keeps_its_case_followed(tmp_path, recovery):
    live, release = _thread(alive=True)
    record = _record("running", thread=live)
    path = _task(tmp_path, job=_waiting_job())
    try:
        assert run_ic.recover(path, _case_ctx(tmp_path, record), instance=INSTANCE) == 0
        assert run_ic.following(TICKET) and recovery == []
    finally:
        record["status"] = "done"
        release.set()
        _join()


def test_a_start_receipt_with_no_started_at_starts_no_follower_and_marks_no_job(tmp_path, monkeypatch, follow):
    path, followed = _wait(tmp_path, monkeypatch, _record("done"), begun=None)
    assert followed is False and not run_ic._FOLLOWERS and follow == []
    assert "kind" not in json.loads(path.read_text())["steps"][0]["job"]

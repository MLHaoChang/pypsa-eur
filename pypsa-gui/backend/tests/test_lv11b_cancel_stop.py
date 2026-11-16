"""
live-voice 11b: the `cancel_job` tool and Stop monitoring
(`.scratch/live-voice/issues/11-nonblocking-monitoring.md`, GK B plan GO).

- `cancel_job` runs 11a's verified cancel as the acting user, at the
  strictest tier any abort it reaches has (Q2). `refused`, `origin_lost` and
  `job_owner_unknown` are tool errors with their own kinds, never a success.
- Stop monitoring (`POST /api/chat/{id}/progress/stop`, B1) ends this
  session's observation only: the job keeps running and still settles, and
  another session watching the same solve keeps receiving. Idempotent.
- B2: Stop monitoring during a plan step's in-turn hold releases the turn;
  the step still settles from the native record (finish, fail, cancel).
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest
from fastapi import HTTPException

from harness import catalogue
from harness import job_adapters as ja
from services import chat_service, chat_tools
from services.pypsa_service import PyPSAService
from tests.journey_wire import run_journey
from tests.test_lv11a_job_contract import _start, _stop_all
from tests.test_oj07_edit_relaunch_walk_journey import _approve, _await_due, _settle_solver, oj_grid  # noqa: F401
from tests.test_workflow_provider_parity import PROVIDERS
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

PROVIDER = PROVIDERS[1]


@pytest.fixture(autouse=True)
def _reset():
    ja._reset_for_tests()
    yield
    ja._reset_for_tests()


# ── cancel_job: tier, verdicts and the errors that are never a success ───────


def test_cancel_job_is_at_least_as_strict_as_every_abort_it_reaches():
    reached = ("abort_simulation", "solve_queue_abort", "abort_adequacy_study",
               "abort_report_generation")
    order = list(catalogue.SAFETY_TIERS)  # strictest first
    tier = catalogue.safety_tier_for("cancel_job")
    assert tier == "destructive"
    assert all(order.index(tier) <= order.index(catalogue.safety_tier_for(t)) for t in reached)


def test_a_cancelled_job_is_the_verified_result(baseline):
    ctx, *_ = baseline
    started = [_start(ctx, "ad")]
    try:
        out = chat_tools.cancel_job(started[0][0])
        assert out["cancel"] == "cancelled" and out["job"]["outcome"] == "cancelled"
        assert started[0][2].is_set()
    finally:
        _stop_all(started)


def test_the_tool_cancels_as_the_acting_user(monkeypatch):
    seen = {}

    def fake(job_id, *, actor_id):
        seen.update(job_id=job_id, actor_id=actor_id)
        return {"cancel": "already_finished", "job": {}}

    monkeypatch.setattr(ja, "cancel_job", fake)
    token = chat_tools._ACTING_USER_ID.set("user-7")
    try:
        assert chat_tools.cancel_job("ad:x") == {"cancel": "already_finished", "job": {}}
    finally:
        chat_tools._ACTING_USER_ID.reset(token)
    assert seen == {"job_id": "ad:x", "actor_id": "user-7"}


def _raised(job_id):
    with pytest.raises(HTTPException) as exc:
        chat_tools.cancel_job(job_id)
    return exc.value.status_code, exc.value.detail


def test_a_refused_abort_is_an_error_and_the_job_runs_on(baseline):
    ctx, *_ = baseline
    started = [_start(ctx, "ad")]
    with ctx.solver_state_lock:
        ctx.solver_state["fmea_sweep"]["stop_event"] = None  # nothing to stop it with
    try:
        status, detail = _raised(started[0][0])
        assert status == 409 and detail["error_kind"] == "job_cancel_refused"
        assert detail["refusal"]["error_kind"] == "not_stoppable"
        assert "Nothing was cancelled" in detail["message"]
        assert detail["job"]["status"] == "running" and detail["job"]["terminal"] is False
    finally:
        _stop_all(started)


def test_a_handle_that_lost_its_run_is_an_error_and_the_newer_run_is_untouched(baseline):
    ctx, *_ = baseline
    first = _start(ctx, "ad")
    second = _start(ctx, "ad")  # a newer sweep replaced the record the first handle bound
    try:
        status, detail = _raised(first[0])
        assert status == 409 and detail["error_kind"] == "job_origin_lost"
        assert "Nothing was cancelled" in detail["message"]
        assert not second[2].is_set()
        assert ctx.solver_state["fmea_sweep"]["status"] == "running"
    finally:
        _stop_all([first, second])


def test_a_job_without_an_owner_is_an_error(baseline):
    ctx, *_ = baseline
    started = [_start(ctx, "rpt")]
    job = ja.register_report_job(tool_name="generate_report", tool_use_id="tu-2",
                                 report_id="rep-1", owner_user_id=None,
                                 project_uuid=str(ctx.project_uuid))
    try:
        status, detail = _raised(job)
        assert status == 409 and detail["error_kind"] == "job_owner_unknown"
        assert not started[0][2].is_set()
    finally:
        _stop_all(started)


def test_another_users_job_is_not_found(baseline):
    ctx, *_ = baseline
    started = [_start(ctx, "ad")]
    token = chat_tools._ACTING_USER_ID.set(str(uuid.uuid4()))
    try:
        with pytest.raises(HTTPException) as exc:
            chat_tools.cancel_job(started[0][0])
        sent = started[0][2].is_set()
    finally:
        chat_tools._ACTING_USER_ID.reset(token)
        _stop_all(started)
    assert exc.value.status_code in (403, 404) and not sent


# ── Stop monitoring: the session's observation, never the job ────────────────


class _Ctx:
    def __init__(self):
        from routers.simulation import BufferedLogQueue

        self.solver_state_lock = threading.Lock()
        self.solver_state = {"status": "running", "log_queue": BufferedLogQueue(maxlen=100)}


def _watch(session, ctx):
    from harness.solver_bridge import solver_log_bridge

    lines, done = [], threading.Event()

    def run():
        for prog in solver_log_bridge(session, ctx, poll_interval=0.01):
            lines.append(prog["line"])
        done.set()

    threading.Thread(target=run, daemon=True).start()
    return lines, done


def _until(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not pred() and time.monotonic() < deadline:
        time.sleep(0.01)
    return pred()


def test_stop_monitoring_is_idempotent_and_closes_the_detached_monitor():
    from harness.session import ChatSession

    session = ChatSession(model="m", owner_user_id="u1")
    session.mark_detached_monitor("tu-1", "run_simulation", active=True)
    assert session.stop_monitoring() is True
    assert session.detached_monitor_active() is False
    assert session.stop_monitoring() is False


def test_stop_monitoring_ends_only_this_sessions_watch():
    from harness.session import ChatSession

    ctx = _Ctx()
    mine, theirs = ChatSession(model="m", owner_user_id="u1"), ChatSession(model="m", owner_user_id="u1")
    my_lines, my_done = _watch(mine, ctx)
    their_lines, their_done = _watch(theirs, ctx)
    ctx.solver_state["log_queue"].put("one")
    assert _until(lambda: my_lines == ["one"] and their_lines == ["one"])
    mine.stop_monitoring()
    assert my_done.wait(2)
    ctx.solver_state["log_queue"].put("two")
    assert _until(lambda: their_lines == ["one", "two"])
    assert my_lines == ["one"] and not their_done.is_set()
    assert ctx.solver_state["status"] == "running"  # the job is untouched
    ctx.solver_state["status"] = "completed"
    theirs.abort_event.set()
    assert their_done.wait(2)


def test_a_stop_sent_before_the_monitor_thread_starts_still_ends_it_and_the_run_still_settles(
    monkeypatch,
):
    from harness import solver_bridge
    from harness.session import ChatSession

    ctx = _Ctx()
    session = ChatSession(model="m", owner_user_id="u1")
    published = []
    gate = threading.Event()
    real_thread = threading.Thread

    class Held(real_thread):
        def run(self):
            gate.wait(2)
            super().run()

    monkeypatch.setattr(threading, "Thread", Held)  # solver_bridge imports it per call
    monkeypatch.setattr(solver_bridge, "_TERMINAL_POLL_S", 0.01)
    solver_bridge.start_detached_solver_monitor(
        session, ctx, tool_use_id="tu-1", tool_name="run_simulation",
        on_terminal=lambda: published.append("terminal"),
    )
    session.stop_monitoring()
    gate.set()
    ctx.solver_state["log_queue"].put("after the stop")
    assert _until(lambda: not session.detached_monitor_active() and session.drain_detached_progress() == [])
    assert published == []
    ctx.solver_state["status"] = "completed"  # Stop monitoring is no cancel: the run still settles once
    assert _until(lambda: published == ["terminal"])


# ── POST /api/chat/{id}/progress/stop ────────────────────────────────────────


@pytest.fixture
def own_session(seeded_identity):
    sid = f"lv11b-{uuid.uuid4().hex[:8]}"
    sess = chat_service.get_or_create_session(sid, owner_user_id=str(seeded_identity["user_id"]))
    yield sess
    chat_service.drop_session(sid)


def test_the_owner_stops_monitoring_once(client, own_session):
    own_session.mark_detached_monitor("tu-1", "run_simulation", active=True)
    first = client.post(f"/api/chat/{own_session.session_id}/progress/stop")
    second = client.post(f"/api/chat/{own_session.session_id}/progress/stop")
    assert (first.status_code, first.json()) == (200, {"ok": True, "stopped": True})
    assert (second.status_code, second.json()) == (200, {"ok": True, "stopped": False})
    assert not own_session.detached_monitor_active()


def test_an_outsider_cannot_stop_someone_elses_monitor(own_session, _auth_db):
    import main
    from fastapi.testclient import TestClient

    from tests.conftest import attach_session
    from tests.test_chat_session_ownership import _other_org_user

    own_session.mark_detached_monitor("tu-1", "run_simulation", active=True)
    epoch = own_session.monitor_epoch()
    uid, session_local = _other_org_user(_auth_db)
    with TestClient(main.app) as c:
        outsider = attach_session(c, session_local, uid)
        r = outsider.post(f"/api/chat/{own_session.session_id}/progress/stop")
        unknown = outsider.post(f"/api/chat/no-such-{uuid.uuid4().hex}/progress/stop")
    assert (r.status_code, r.json()) == (unknown.status_code, unknown.json())
    assert own_session.detached_monitor_active() and own_session.monitor_epoch() == epoch


# ── B2: Stop monitoring during a plan step's in-turn hold ────────────────────


def _plan_run(gate):
    """Start a one-step plan whose solve the gate holds, and Stop monitoring
    on its first progress line, inside the turn's grace hold."""
    cards, stopped = [], []
    approve = _approve(cards)

    seen = []

    def on_frame(event, data):
        approve(event, data)
        seen.append((event, data.get("tool_name") if isinstance(data, dict) else None))
        if event == "tool_running" and data.get("tool_name") == "run_simulation":
            session = chat_tools.chat_session()

            def stop_once_held():
                # The gate logs before the bridge subscribes, so no progress
                # frame is guaranteed: stop once the solve is held and the
                # turn is in its grace hold.
                deadline = time.monotonic() + 10
                while not gate.starts and time.monotonic() < deadline:
                    time.sleep(0.02)
                time.sleep(0.5)
                stopped.append(session.stop_monitoring())

            threading.Thread(target=stop_once_held, daemon=True).start()

    plan = [{"id": "run", "tool": "run_simulation", "args": {}}]
    began = time.monotonic()
    calls, results, frames, session = run_journey(
        *PROVIDER,
        [lambda r: ("start_task", {"title": "Solve", "request_id": "lv11b", "steps": plan}),
         # oj_grid solved once at setup: the step re-runs current results (lc19 OJ-32).
         lambda r: ("run_simulation", {"rerun_current": True}), lambda r: (None, {})],
        "Solve it again.", on_frame=on_frame)
    elapsed = time.monotonic() - began
    assert stopped, f"no progress frame in the hold ({elapsed:.1f}s): {seen[:40]}"
    return results, session, stopped, elapsed


@pytest.fixture
def held_plan(oj_grid, monkeypatch):  # noqa: F811
    from harness import continuation

    monkeypatch.setattr(continuation, "GRACE_SECONDS", 20.0)
    return oj_grid


def _released(results, session, stopped, elapsed):
    run = results[1]
    assert stopped == [True]
    assert elapsed < 15, f"the turn held past Stop monitoring: {elapsed:.1f}s"
    assert run["running"] is True and run["job_id"].startswith("fg:")
    assert not session.detached_monitor_active()
    task = chat_tools.get_task(results[0]["task_id"])
    assert task["next_step"]["id"] == "run" and task["next_step"]["status"] == "waiting"
    return run["job_id"], results[0]["task_id"]


def _settled_step(task_id, status, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = chat_tools.get_task(task_id)
        step = task.get("next_step") or {}
        if not (step.get("id") == "run" and step.get("status") == "waiting"):
            return task
        time.sleep(0.05)
    raise AssertionError(f"the step never settled {status}")


def test_b2_a_step_solve_that_finishes_after_stop_monitoring_still_settles_completed(held_plan):
    job, task_id = _released(*_plan_run(held_plan))
    held_plan.open.set()
    _settle_solver()
    assert PyPSAService.get_solver_state().get("status") == "completed"
    session = chat_tools.chat_session()
    due = _await_due(session)
    assert [d["job_id"] for d in due] == [job]  # one continuation, as without the stop
    task = _settled_step(task_id, "completed")
    assert task["completed_steps"] == 1


def test_b2_a_step_solve_that_fails_after_stop_monitoring_still_settles_failed(held_plan):
    from harness import continuation

    def boom(n, lock):
        raise RuntimeError("solver blew up")

    held_plan.after_solve = boom
    job, task_id = _released(*_plan_run(held_plan))
    session = chat_tools.chat_session()
    held_plan.open.set()
    _settle_solver()
    assert PyPSAService.get_solver_state().get("status") == "failed"
    task = _settled_step(task_id, "failed")
    assert task["completed_steps"] == 0
    assert task["next_step"]["id"] == "run" and task["next_step"]["status"] == "failed"
    time.sleep(0.3)
    assert continuation.due(session) == []


def test_b2_a_step_solve_cancelled_by_cancel_job_after_stop_monitoring_settles_failed(held_plan):
    from harness import continuation

    job, task_id = _released(*_plan_run(held_plan))
    session = chat_tools.chat_session()
    cards = []
    calls, results, frames, _ = run_journey(
        *PROVIDER, [lambda r: ("cancel_job", {"job_id": job}), lambda r: (None, {})],
        "Cancel it.", session=session, on_frame=_approve(cards))
    assert [c["tool_name"] for c in cards] == ["cancel_job"]  # destructive: its own card
    assert results[0]["cancel"] == "cancelled" and results[0]["job"]["outcome"] == "cancelled"
    _settle_solver()
    task = _settled_step(task_id, "failed")
    assert task["completed_steps"] == 0
    assert task["next_step"]["id"] == "run" and task["next_step"]["status"] == "failed"
    time.sleep(0.3)
    assert continuation.due(session) == []
    assert len(held_plan.starts) == 1


# ── cancel_job through the harness: errors reach the model as errors ─────────


@pytest.mark.parametrize("case", ["refused", "origin_lost", "owner_unknown"])
def test_each_unverified_cancel_reaches_the_model_as_a_tool_error(baseline, case):
    ctx, *_ = baseline
    started = [_start(ctx, "ad")]
    job = started[0][0]
    if case == "refused":
        with ctx.solver_state_lock:
            ctx.solver_state["fmea_sweep"]["stop_event"] = None
    elif case == "origin_lost":
        started.append(_start(ctx, "ad"))
    else:
        job = ja.register_study_job(study_key="fmea_sweep", tool_name="run_fmea_sweep",
                                    tool_use_id="tu-x", owner_user_id=None,
                                    project_uuid=str(ctx.project_uuid))
    cards = []
    try:
        calls, results, frames, _ = run_journey(
            *PROVIDER, [lambda r: ("cancel_job", {"job_id": job}), lambda r: (None, {})],
            "Cancel it.", on_frame=_approve(cards))
    finally:
        _stop_all(started)
    kinds = [d.get("error_kind") for e, d in frames if e == "tool_error"]
    expected = {"refused": "job_cancel_refused", "origin_lost": "job_origin_lost",
                "owner_unknown": "job_owner_unknown"}[case]
    assert kinds == [expected]
    assert not (isinstance(results[0], dict) and results[0].get("cancel") == "cancelled")

"""Holds on job calls whose outcome is unknown (issue 04, slice S5).

GK B (30) plan GO with conditions N1–N9 (issue 04, last Comments section):
- a job call that ends uncertain in an ordinary, untasked turn holds its tool
  on the project binding; a same-args call is denied `operation_pending`, an
  other-args call `operation_conflict` (N5), before any effect seam (N7);
- the hold clears only on independent evidence (N3); a read that fails keeps
  it (N4);
- after the pending deny, the user may approve a repeat card bound to the
  hold id and the call's fingerprint, which runs once (N5/N6);
- abort_simulation is never held (N2); the solve-queue store is the
  cross-session record for solve_queue_enqueue (N1).
"""

from __future__ import annotations

import concurrent.futures
import threading
import uuid

import pytest
from fastapi import HTTPException

from harness import guards, job_holds
from harness.session import ChatSession
from services import chat_tools
from tests.assurance_acceptance.test_p3_required_guards import spies  # noqa: F401
from tests.test_chat_tool_executor_starvation import saturated_pool  # noqa: F401

TOOL = "solve_queue_enqueue"
ARGS = {"project_id": "alpha"}
OTHER = {"project_id": "beta"}


def _call(monkeypatch, session, tool, args, *, decision="approve", cards=None):
    from harness.loop import dispatch_tool_call

    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "disabled")
    frames, collector = [], []
    for event, data in dispatch_tool_call(
        session,
        {"id": uuid.uuid4().hex, "name": tool, "input": args},
        collector,
        guided=True,
    ):
        frames.append((event, data))
        if event == "tool_pending_confirmation":
            if cards is not None:
                cards.append(data)
            session.record_decision(data["confirmation_token"], decision)
    return frames


def _error(frames):
    errors = [d for e, d in frames if e == "tool_error"]
    return errors[0] if errors else None


@pytest.fixture
def slow_queue(monkeypatch):
    """solve_queue_enqueue whose handler blocks until released, so the call
    times out and its worker stays alive; `calls` counts handler entries."""
    from harness import budget as harness_budget

    release = threading.Event()
    calls: list[dict] = []
    result: dict = {}

    def handler(**kw):
        calls.append(kw)
        if len(calls) == 1:
            release.wait(10)
            if "raise" in result:
                raise result["raise"]
            return result.get("value", {})
        return {"id": "fresh", "status": "queued"}

    monkeypatch.setitem(chat_tools.DISPATCHERS, TOOL, handler)
    monkeypatch.setattr(harness_budget, "PER_TOOL_TIMEOUT_SECONDS", 0.2)
    try:
        yield release, calls, result
    finally:
        release.set()


def _timed_out(monkeypatch, session):
    error = _error(_call(monkeypatch, session, TOOL, ARGS))
    assert error["error_kind"] == "tool_timeout", error
    return error


# ── Opening and denying ──────────────────────────────────────────────────────


def test_a_timed_out_job_call_denies_the_same_args_as_pending(monkeypatch, slow_queue):
    _release, calls, _ = slow_queue
    session = ChatSession()
    _timed_out(monkeypatch, session)
    cards: list = []
    error = _error(_call(monkeypatch, session, TOOL, ARGS, cards=cards))
    assert error["error_kind"] == job_holds.PENDING
    assert error["_outcome"] == {
        "version": 1,
        "outcome": "denied",
        "reason": job_holds.PENDING,
    }
    assert "repeat_card" not in cards[0]
    assert "call it again with the same settings" in error["message"]
    assert len(calls) == 1


def test_a_timed_out_job_call_denies_other_args_as_conflict(monkeypatch, slow_queue):
    _release, calls, _ = slow_queue
    session = ChatSession()
    _timed_out(monkeypatch, session)
    error = _error(_call(monkeypatch, session, TOOL, OTHER))
    assert error["error_kind"] == job_holds.CONFLICT
    # N5: it names the held operation and points to wait, status or abort.
    assert TOOL in error["message"]
    assert "stop it with solve_queue_abort" in error["message"]
    assert len(calls) == 1


def test_a_held_call_is_denied_before_every_effect_seam(monkeypatch, slow_queue, spies):  # noqa: F811
    """N7: after G3 and the lock, before reserve, begin_step and the handler."""
    from services import assistant_tasks

    _release, calls, _ = slow_queue
    session = ChatSession()
    _timed_out(monkeypatch, session)
    seen, _files = spies
    seen.clear()
    began = []
    real = assistant_tasks.begin_step
    monkeypatch.setattr(
        assistant_tasks, "begin_step", lambda *a, **k: began.append(1) or real(*a, **k)
    )
    order = []
    real_lock = guards.lock_refusal
    monkeypatch.setattr(
        guards,
        "lock_refusal",
        lambda *a, **k: order.append("lock") or real_lock(*a, **k),
    )
    error = _error(_call(monkeypatch, session, TOOL, OTHER))
    assert error["error_kind"] == job_holds.CONFLICT
    assert order == ["lock"]
    assert seen == [] and began == [] and len(calls) == 1


def test_a_changed_args_after_approval_gets_g3_not_a_hold(monkeypatch, slow_queue):
    """N7: the G3 compare comes first."""
    from harness import guards as g

    _release, _calls, _ = slow_queue
    session = ChatSession()
    _timed_out(monkeypatch, session)
    prints = iter(["a", "b"])
    monkeypatch.setattr(g, "fingerprint", lambda *a, **k: next(prints))
    error = _error(_call(monkeypatch, session, TOOL, ARGS))
    assert error["error_kind"] == g.ARGS_CHANGED


def test_a_tasked_call_gets_only_begin_steps_refusal(monkeypatch, slow_queue):
    from services import assistant_tasks

    _release, calls, _ = slow_queue
    session = ChatSession()
    _timed_out(monkeypatch, session)
    monkeypatch.setattr(assistant_tasks, "governs", lambda *a: True)

    def refuse(*_a):
        raise HTTPException(409, {"error_kind": "task_step_uncertain", "message": "x"})

    monkeypatch.setattr(assistant_tasks, "begin_step", refuse)
    errors = [
        d for e, d in _call(monkeypatch, session, TOOL, ARGS) if e == "tool_error"
    ]
    assert [e["error_kind"] for e in errors] == ["task_step_uncertain"]
    assert len(calls) == 1


def test_a_tasked_call_opens_no_hold(monkeypatch, slow_queue):
    from services import assistant_tasks

    _release, _calls, _ = slow_queue
    session = ChatSession()
    monkeypatch.setattr(assistant_tasks, "governs", lambda *a: True)
    monkeypatch.setattr(assistant_tasks, "begin_step", lambda *a: ("t", "s", "a"))
    monkeypatch.setattr(assistant_tasks, "finish_step", lambda *a, **k: None)
    monkeypatch.setattr(assistant_tasks, "register_worker", lambda *a, **k: None)
    _timed_out(monkeypatch, session)
    assert session.job_holds == {}


def test_a_call_that_never_started_opens_no_hold(
    monkeypatch,
    saturated_pool,  # noqa: F811
):
    """N3: `Future.cancel()` succeeded, so the handler can never run."""
    from harness import budget as harness_budget

    monkeypatch.setitem(chat_tools.DISPATCHERS, TOOL, lambda **_kw: {})
    monkeypatch.setattr(harness_budget, "PER_TOOL_TIMEOUT_SECONDS", 0.3)
    session = ChatSession()
    error = _error(_call(monkeypatch, session, TOOL, ARGS))
    assert error["error_kind"] == "tool_not_started"
    assert session.job_holds == {}


def test_a_raise_of_a_pool_job_tool_opens_no_hold(monkeypatch):
    def refuse(**_kw):
        raise HTTPException(422, {"error_kind": "bad", "message": "no"})

    monkeypatch.setitem(chat_tools.DISPATCHERS, TOOL, refuse)
    session = ChatSession()
    assert _error(_call(monkeypatch, session, TOOL, ARGS))["error_kind"] == "bad"
    assert session.job_holds == {}


# ── Clearing (N3, N4) ────────────────────────────────────────────────────────


def test_the_hold_clears_when_the_worker_ends_with_no_job(monkeypatch, slow_queue):
    release, calls, _ = slow_queue
    session = ChatSession()
    _timed_out(monkeypatch, session)
    (hold,) = session.job_holds.values()
    release.set()
    hold.future.result(timeout=5)
    assert _error(_call(monkeypatch, session, TOOL, ARGS)) is None
    assert len(calls) == 2


def test_the_hold_clears_when_the_worker_raises(monkeypatch, slow_queue):
    release, calls, result = slow_queue
    result["raise"] = RuntimeError("late")
    session = ChatSession()
    _timed_out(monkeypatch, session)
    (hold,) = session.job_holds.values()
    release.set()
    concurrent.futures.wait([hold.future], timeout=5)
    assert _error(_call(monkeypatch, session, TOOL, ARGS)) is None


def test_a_late_result_moves_the_hold_onto_its_job(monkeypatch, slow_queue):
    release, calls, result = slow_queue
    result["value"] = {"id": "job-1", "status": "queued"}
    status = {"job-1": False}
    monkeypatch.setattr(job_holds, "job_terminal", lambda job_id: status[job_id])
    session = ChatSession()
    _timed_out(monkeypatch, session)
    (hold,) = session.job_holds.values()
    release.set()
    hold.future.result(timeout=5)
    error = _error(_call(monkeypatch, session, TOOL, OTHER))
    assert error["error_kind"] == job_holds.CONFLICT
    assert "wait_for_job (job job-1)" in error["message"]
    status["job-1"] = True
    assert _error(_call(monkeypatch, session, TOOL, OTHER)) is None


def test_a_job_status_read_that_fails_keeps_the_hold(monkeypatch, slow_queue):
    """N4: an unreadable status is not terminal."""
    release, calls, result = slow_queue
    result["value"] = {"id": "job-1"}

    def unreadable(_job_id):
        raise OSError("store down")

    monkeypatch.setattr(job_holds, "job_terminal", unreadable)
    session = ChatSession()
    _timed_out(monkeypatch, session)
    (hold,) = session.job_holds.values()
    release.set()
    hold.future.result(timeout=5)
    assert (
        _error(_call(monkeypatch, session, TOOL, OTHER))["error_kind"]
        == job_holds.CONFLICT
    )
    assert len(calls) == 1


def test_time_alone_never_clears_a_hold(monkeypatch, slow_queue):
    import time as _time

    _release, calls, _ = slow_queue
    session = ChatSession()
    _timed_out(monkeypatch, session)
    real = _time.time
    monkeypatch.setattr(job_holds.time, "time", lambda: real() + 10 * 86400)
    assert (
        _error(_call(monkeypatch, session, TOOL, OTHER))["error_kind"]
        == job_holds.CONFLICT
    )


def test_another_session_has_its_own_holds(monkeypatch, slow_queue):
    """The in-process hold is per session; N1's store read covers the queue."""
    _release, calls, _ = slow_queue
    _timed_out(monkeypatch, ChatSession())
    assert _error(_call(monkeypatch, ChatSession(), TOOL, ARGS)) is None
    assert len(calls) == 2


# ── The solve tools on the SSE thread (N9) ───────────────────────────────────


class _Thread:
    def __init__(self, alive=True):
        self.alive = alive

    def is_alive(self):
        return self.alive


class _Ctx:
    def __init__(self, thread=None, broken=False):
        self.solver_state_lock = threading.Lock()
        self._state = {"thread": thread}
        self.broken = broken

    @property
    def solver_state(self):
        if self.broken:
            raise OSError("unreadable")
        return self._state


def test_a_solve_raise_that_left_a_new_thread_alive_holds_until_it_ends():
    session, ctx = ChatSession(), _Ctx()
    thread = _Thread()
    ctx._state["thread"] = thread
    hold = job_holds.open_on_raise(session, "run_simulation", "f", "tu", ctx, None)
    assert hold is not None and hold.worker is thread
    assert job_holds.terminal(hold) is False
    thread.alive = False
    assert job_holds.terminal(hold) is True


def test_a_solve_raise_with_no_new_thread_opens_no_hold():
    prior = _Thread()
    assert (
        job_holds.open_on_raise(
            ChatSession(), "run_simulation", "f", "tu", _Ctx(prior), prior
        )
        is None
    )
    assert (
        job_holds.open_on_raise(
            ChatSession(), "run_simulation", "f", "tu", _Ctx(None), None
        )
        is None
    )
    assert (
        job_holds.open_on_raise(
            ChatSession(), "run_simulation", "f", "tu", _Ctx(_Thread(False)), None
        )
        is None
    )


def test_a_solve_raise_with_an_unreadable_state_holds_until_the_solver_is_idle(
    monkeypatch,
):
    hold = job_holds.open_on_raise(
        ChatSession(), "run_ac_pf_stage", "f", "tu", _Ctx(broken=True), None
    )
    assert hold is not None and hold.worker is None and hold.job_id is None
    live = _Ctx(_Thread())
    monkeypatch.setattr(
        job_holds, "_live_solve_thread", lambda: job_holds.solve_thread(live)
    )
    assert job_holds.terminal(hold) is False
    live.broken = True
    with pytest.raises(OSError):
        job_holds.terminal(hold)
    live.broken = False
    live._state["thread"] = _Thread(False)
    assert job_holds.terminal(hold) is True


def test_a_solve_raise_through_the_loop_holds_the_next_call(monkeypatch):
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    started = _Thread()

    def raising(**_kw):
        with ctx.solver_state_lock:
            ctx.solver_state["thread"] = started
        raise RuntimeError("after the thread started")

    with ctx.solver_state_lock:
        prior = ctx.solver_state.get("thread")
    monkeypatch.setitem(chat_tools.DISPATCHERS, "run_simulation", raising)
    session = ChatSession()
    try:
        assert (
            _error(_call(monkeypatch, session, "run_simulation", {}))["error_kind"]
            == "tool_error"
        )
        error = _error(_call(monkeypatch, session, "run_simulation", {}))
        assert error["error_kind"] == job_holds.PENDING
        assert "abort_simulation" in error["message"]
    finally:
        with ctx.solver_state_lock:
            ctx.solver_state["thread"] = prior


def test_a_returned_solve_with_an_unknown_outcome_holds_its_job():
    session = ChatSession()
    assert (
        job_holds.open_on_outcome(
            session, "run_simulation", "f", "tu", {"outcome": "running"}
        )
        is None
    )
    hold = job_holds.open_on_outcome(
        session, "run_simulation", "f", "tu", {"outcome": "uncertain", "job_id": "fg:1"}
    )
    assert hold.job_id == "fg:1"


# ── abort is never held (N2) ─────────────────────────────────────────────────


def test_abort_simulation_is_never_held(monkeypatch):
    from harness import budget as harness_budget

    release = threading.Event()
    calls = []

    def abort(**kw):
        calls.append(kw)
        if len(calls) == 1:
            release.wait(10)
        return {"status": "aborting"}

    monkeypatch.setitem(chat_tools.DISPATCHERS, "abort_simulation", abort)
    monkeypatch.setattr(harness_budget, "PER_TOOL_TIMEOUT_SECONDS", 0.2)
    session = ChatSession()
    try:
        first = _error(_call(monkeypatch, session, "abort_simulation", {}))
        assert first["error_kind"] == "tool_timeout"
        assert session.job_holds == {}
        # A hold on a run never blocks the abort either.
        job_holds._open(
            session, "run_simulation", "f", "tu", "uncertain_raise", worker=_Thread()
        )
        assert _error(_call(monkeypatch, session, "abort_simulation", {})) is None
        assert len(calls) == 2
    finally:
        release.set()


# ── The repeat card (P4, N5, N6) ─────────────────────────────────────────────


def test_after_the_pending_deny_a_repeat_card_names_the_hold_and_runs_once(
    monkeypatch, slow_queue
):
    _release, calls, _ = slow_queue
    session = ChatSession()
    _timed_out(monkeypatch, session)
    (hold,) = session.job_holds.values()
    assert (
        _error(_call(monkeypatch, session, TOOL, ARGS))["error_kind"]
        == job_holds.PENDING
    )
    cards: list = []
    assert _error(_call(monkeypatch, session, TOOL, ARGS, cards=cards)) is None
    card = cards[0]["repeat_card"]
    assert card["hold_id"] == hold.id and card["opened"] == "tool_timeout"
    assert card["fingerprint"] == hold.fingerprint
    assert len(calls) == 2
    # The repeat ran as a new operation: the old hold is gone, not reused.
    assert session.job_holds == {}


def test_a_declined_repeat_card_runs_nothing(monkeypatch, slow_queue):
    _release, calls, _ = slow_queue
    session = ChatSession()
    _timed_out(monkeypatch, session)
    _call(monkeypatch, session, TOOL, ARGS)
    error = _error(_call(monkeypatch, session, TOOL, ARGS, decision="deny"))
    assert error["error_kind"] == "confirmation_denied"
    assert len(calls) == 1 and len(session.job_holds) == 1


def test_the_repeat_card_is_forced_even_when_the_tier_auto_approves(
    monkeypatch, slow_queue
):
    from harness import confirm

    _release, calls, _ = slow_queue
    session = ChatSession()
    _timed_out(monkeypatch, session)
    _call(monkeypatch, session, TOOL, ARGS)
    monkeypatch.setattr(confirm, "confirmation_policy", lambda *a, **k: "none")
    cards: list = []
    _call(monkeypatch, session, TOOL, ARGS, decision="deny", cards=cards)
    assert cards and "repeat_card" in cards[0]
    assert len(calls) == 1


def test_a_repeat_approval_is_bound_to_its_hold_and_fingerprint():
    """N5/N6: an approval for (hold, F) admits neither F′ nor a newer hold."""
    session = ChatSession()
    hold = job_holds._open(
        session, TOOL, "F", "tu", "tool_timeout", future=_NeverDone()
    )
    card = {"hold_id": hold.id, "fingerprint": "F"}
    assert (
        job_holds.check(session, TOOL, ARGS, "F2", card=card)[0] == job_holds.CONFLICT
    )
    assert (
        job_holds.check(
            session, TOOL, ARGS, "F", card={"hold_id": "other", "fingerprint": "F"}
        )[0]
        == job_holds.PENDING
    )
    assert job_holds.check(session, TOOL, ARGS, "F", card=card) is None
    # The repeat itself timed out: a new hold, and the old approval admits nothing.
    job_holds._open(session, TOOL, "F", "tu2", "tool_timeout", future=_NeverDone())
    assert job_holds.check(session, TOOL, ARGS, "F", card=card)[0] == job_holds.PENDING


class _NeverDone:
    def done(self):
        return False


def test_a_repeat_that_times_out_again_is_held_by_the_new_hold(monkeypatch):
    """N6 through the loop: the approved repeat runs once; the next identical
    call meets the repeat's own hold."""
    from harness import budget as harness_budget

    release = threading.Event()
    calls = []

    def hang(**kw):
        calls.append(kw)
        release.wait(10)
        return {}

    monkeypatch.setitem(chat_tools.DISPATCHERS, TOOL, hang)
    monkeypatch.setattr(harness_budget, "PER_TOOL_TIMEOUT_SECONDS", 0.2)
    session = ChatSession()
    try:
        _timed_out(monkeypatch, session)
        (first,) = session.job_holds.values()
        _call(monkeypatch, session, TOOL, ARGS)
        _timed_out(monkeypatch, session)  # the approved repeat
        (second,) = session.job_holds.values()
        assert second.id != first.id
        error = _error(_call(monkeypatch, session, TOOL, ARGS))
        assert error["error_kind"] == job_holds.PENDING
        assert len(calls) == 2
    finally:
        release.set()


# ── N1: the solve-queue store across sessions ───────────────────────────────


@pytest.fixture
def store(monkeypatch):
    from services import project_registry, solve_job_store

    class _P:
        name = "alpha"

    monkeypatch.setattr(project_registry, "resolve_project", lambda db, user, ref: _P())
    monkeypatch.setattr(project_registry, "registry_key", lambda p: "org:alpha")

    class _Acting:
        def __enter__(self):
            return (None, None)

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(chat_tools, "_acting", lambda: _Acting())
    rows: list = []
    seen: dict = {}

    def load(statuses, *, project_key_prefix=None, strict=False, limit=None):
        seen.update(statuses=statuses, prefix=project_key_prefix, strict=strict)
        if isinstance(rows, Exception):
            raise rows
        return list(rows)

    monkeypatch.setattr(solve_job_store, "load_by_status", load)
    calls: list = []
    monkeypatch.setitem(
        chat_tools.DISPATCHERS, TOOL, lambda **kw: calls.append(kw) or {"id": "x"}
    )
    return rows, seen, calls


def test_a_new_session_is_denied_while_the_store_has_the_projects_solve(
    monkeypatch, store
):
    rows, seen, calls = store
    rows.append(
        {
            "id": uuid.uuid4(),
            "project_key": "org:alpha",
            "kind": "solve",
            "status": "running",
        }
    )
    error = _error(_call(monkeypatch, ChatSession(), TOOL, ARGS))
    assert error["error_kind"] == job_holds.PENDING
    assert seen == {
        "statuses": ("queued", "running"),
        "prefix": "org:alpha",
        "strict": True,
    }
    assert "solve_queue_abort" in error["message"]
    assert calls == []


def test_the_store_read_ignores_other_projects_and_kinds(monkeypatch, store):
    rows, _seen, calls = store
    rows.append({"id": 1, "project_key": "org:alpha2", "kind": "solve"})
    rows.append({"id": 2, "project_key": "org:alpha", "kind": "gridspine"})
    assert _error(_call(monkeypatch, ChatSession(), TOOL, ARGS)) is None
    assert len(calls) == 1


def test_a_store_read_that_raises_denies(monkeypatch, store):
    """N4: an unreadable store is not 'no job'."""
    from services import solve_job_store

    _rows, _seen, calls = store

    def broken(*_a, **_k):
        raise OSError("locked")

    monkeypatch.setattr(solve_job_store, "load_by_status", broken)
    assert (
        _error(_call(monkeypatch, ChatSession(), TOOL, ARGS))["error_kind"]
        == job_holds.PENDING
    )
    assert calls == []


def test_load_by_status_raises_only_when_strict(monkeypatch):
    from db import session as db_session
    from services import solve_job_store

    def broken():
        raise OSError("no database")

    monkeypatch.setattr(db_session, "SessionLocal", broken)
    assert solve_job_store.load_by_status(("queued",)) == []
    with pytest.raises(OSError):
        solve_job_store.load_by_status(("queued",), strict=True)


# ── Scope ────────────────────────────────────────────────────────────────────


def test_the_job_tools_are_the_execution_tiers():
    from harness import catalogue

    tools = {t["name"] for t in catalogue.TOOLS if job_holds.is_job_tool(t["name"])}
    assert {
        "run_simulation",
        "run_ac_pf_stage",
        "solve_queue_enqueue",
        "run_mc_study",
    } <= tools
    assert not {"abort_simulation", "wait_for_job", "solve_queue_abort"} & tools

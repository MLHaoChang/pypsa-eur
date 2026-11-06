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
    from services import solve_queue

    enqueued = []
    real_enqueue = solve_queue.solve_queue.enqueue_unique
    monkeypatch.setattr(
        solve_queue.solve_queue,
        "enqueue_unique",
        lambda *a, **k: enqueued.append(1) or real_enqueue(*a, **k),
    )
    error = _error(_call(monkeypatch, session, TOOL, OTHER))
    assert error["error_kind"] == job_holds.CONFLICT
    assert order == ["lock"]
    assert seen == [] and began == [] and enqueued == [] and len(calls) == 1


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


# ── governs mirrors begin_step (N7) ──────────────────────────────────────────


def _task(monkeypatch, steps, status="running"):
    from services import assistant_tasks

    task = {"status": status, "steps": steps}
    monkeypatch.setattr(assistant_tasks, "_read", lambda task_id: ("path", task))
    session = ChatSession()
    session.task_id = "t1"
    return session


def _step(status, args, tool=TOOL, **extra):
    return {
        "id": uuid.uuid4().hex[:6],
        "tool": tool,
        "status": status,
        "args": args,
        **extra,
    }


def test_governs_only_a_next_step_begin_step_refuses(monkeypatch):
    from services.assistant_tasks import governs

    for status in ("waiting", "running", "uncertain"):
        session = _task(monkeypatch, [_step(status, ARGS)])
        assert governs(session, TOOL, ARGS) is True
    # A step begin_step admits is not governed: the hold check still runs (T1).
    session = _task(monkeypatch, [_step("pending", ARGS)])
    assert governs(session, TOOL, ARGS) is False
    assert governs(_task(monkeypatch, [_step("uncertain", ARGS)]), TOOL, OTHER) is False
    assert governs(session, "run_mc_study", ARGS) is False


def test_governs_a_repeat_of_a_completed_step(monkeypatch):
    """begin_step refuses it task_step_already_completed; the hold stays out."""
    from services.assistant_tasks import governs

    session = _task(
        monkeypatch,
        [_step("completed", ARGS), _step("pending", {}, tool="run_simulation")],
    )
    assert governs(session, TOOL, ARGS) is True


def test_governs_nothing_for_a_native_step_a_closed_task_or_no_task(monkeypatch):
    from services.assistant_tasks import governs

    assert (
        governs(_task(monkeypatch, [_step("pending", ARGS, native=True)]), TOOL, ARGS)
        is False
    )
    assert (
        governs(
            _task(monkeypatch, [_step("pending", ARGS)], status="cancelled"), TOOL, ARGS
        )
        is False
    )
    assert governs(ChatSession(), TOOL, ARGS) is False


def test_an_unreadable_task_governs_nothing_so_the_hold_still_applies(monkeypatch):
    """Fail closed (#1079): a task read that fails leaves the hold check on."""
    from services import assistant_tasks

    session = ChatSession()
    session.task_id = "t1"

    def unreadable(_task_id):
        raise OSError("disk")

    monkeypatch.setattr(assistant_tasks, "_read", unreadable)
    assert assistant_tasks.governs(session, TOOL, ARGS) is False


# ── The store path's resolution and words ────────────────────────────────────


def test_a_project_lookup_that_fails_denies(monkeypatch, store):
    """N4: only the handler's own refusal (an HTTPException) passes through."""
    from services import project_registry

    _rows, _seen, calls = store

    def broken(*_a):
        raise OSError("db down")

    monkeypatch.setattr(project_registry, "resolve_project", broken)
    assert (
        _error(_call(monkeypatch, ChatSession(), TOOL, ARGS))["error_kind"]
        == job_holds.PENDING
    )
    assert calls == []


def test_a_project_the_user_cannot_reach_is_left_to_the_handler(monkeypatch, store):
    from services import project_registry

    _rows, _seen, calls = store

    def missing(*_a):
        raise HTTPException(404, "no such project")

    monkeypatch.setattr(project_registry, "resolve_project", missing)
    assert _error(_call(monkeypatch, ChatSession(), TOOL, ARGS)) is None
    assert len(calls) == 1


def test_a_store_hold_says_the_queue_has_it_not_that_its_outcome_is_unknown(
    monkeypatch, store
):
    rows, _seen, _calls = store
    rows.append({"id": "j9", "project_key": "org:alpha", "kind": "solve"})
    message = _error(_call(monkeypatch, ChatSession(), TOOL, ARGS))["message"]
    assert message.startswith("The solve queue already has this project's solve")
    assert "unknown" not in message and "confirm the repeat" not in message


def test_an_admitted_task_step_still_meets_the_hold(monkeypatch, slow_queue):
    """GK B (30) T1: an untasked timed-out call, then a task whose next step is
    the same call, which begin_step would admit: denied, nothing started."""
    from services import assistant_tasks, solve_queue

    _release, calls, _ = slow_queue
    session = ChatSession()
    _timed_out(monkeypatch, session)
    session.task_id = "t1"
    task = {"status": "running", "steps": [_step("pending", ARGS)]}
    monkeypatch.setattr(assistant_tasks, "_read", lambda task_id: ("path", task))
    began, enqueued = [], []
    monkeypatch.setattr(
        assistant_tasks, "begin_step", lambda *a: began.append(1) or ("t1", "s", "a")
    )
    monkeypatch.setattr(
        solve_queue.solve_queue, "enqueue_unique", lambda *a, **k: enqueued.append(1)
    )
    assert assistant_tasks.governs(session, TOOL, ARGS) is False
    error = _error(_call(monkeypatch, session, TOOL, ARGS))
    assert error["error_kind"] == job_holds.PENDING, error
    assert began == [] and enqueued == [] and len(calls) == 1


# ── mutants.py survivors (RETRO-SPEED-1) ─────────────────────────────────────


def test_a_hold_refuses_only_its_own_tool_on_its_own_binding(monkeypatch):
    binding = {"now": "P1"}
    monkeypatch.setattr(job_holds, "_binding", lambda: binding["now"])
    session = ChatSession()
    job_holds._open(session, "run_simulation", "F", "tu", "x", worker=_Thread())
    assert job_holds.check(session, "run_simulation", {}, "F")[0] == job_holds.PENDING
    assert job_holds.check(session, "run_ac_pf_stage", {}, "F") is None
    binding["now"] = "P2"
    assert job_holds.check(session, "run_simulation", {}, "F") is None


def test_an_unreadable_binding_matches_every_hold(monkeypatch):
    """N4: when either side's binding cannot be read, the hold applies."""
    binding = {"now": "P1"}

    def read():
        if binding["now"] is None:
            raise OSError("unreadable")
        return binding["now"]

    monkeypatch.setattr(job_holds, "_binding", read)
    session = ChatSession()
    job_holds._open(session, "run_simulation", "F", "tu", "x", worker=_Thread())
    binding["now"] = None
    assert job_holds.check(session, "run_simulation", {}, "F")[0] == job_holds.PENDING
    other = ChatSession()
    job_holds._open(other, "run_simulation", "F", "tu", "x", worker=_Thread())
    binding["now"] = "P2"
    assert job_holds.check(other, "run_simulation", {}, "F")[0] == job_holds.PENDING


def test_job_terminal_reads_each_job_kind_from_its_own_source(monkeypatch):
    import contextlib

    from harness import job_adapters as ja
    from routers import solve_queue as queue_router
    from services.solve_queue import _TERMINAL

    for kind in ("foreground", "study", "report"):
        monkeypatch.setattr(
            ja, f"is_{kind}_job_id", lambda j, kind=kind: j.startswith(kind)
        )
    read = []

    def waiter(kind):
        def wait(job_id, *_a, **_k):
            read.append(kind)
            return {"terminal": job_id.endswith("done")}

        return wait

    for kind in ("foreground", "study", "report"):
        monkeypatch.setattr(ja, f"wait_{kind}_job", waiter(kind))
    monkeypatch.setattr(chat_tools, "acting_user_id", lambda: 1)
    for kind in ("foreground", "study", "report"):
        assert job_holds.job_terminal(f"{kind}-done") is True
        assert job_holds.job_terminal(f"{kind}-live") is False
    assert read == ["foreground"] * 2 + ["study"] * 2 + ["report"] * 2

    status = {"q-done": next(iter(_TERMINAL)), "q-live": "running"}
    monkeypatch.setattr(
        chat_tools, "_acting", lambda: contextlib.nullcontext((None, None))
    )
    monkeypatch.setattr(
        queue_router,
        "_visible_job_or_404",
        lambda db, user, job_id: {"status": status[job_id]},
    )
    assert job_holds.job_terminal("q-done") is True
    assert job_holds.job_terminal("q-live") is False
    assert len(read) == 6


def test_a_cancelled_future_clears_the_hold():
    session = ChatSession()
    future = concurrent.futures.Future()
    hold = job_holds._open(session, TOOL, "F", "tu", "tool_timeout", future=future)
    assert future.cancel()
    assert job_holds.terminal(hold) is True


def test_an_approval_whose_hold_has_cleared_admits_the_call():
    card = {"hold_id": "gone", "fingerprint": "F"}
    assert job_holds.check(ChatSession(), "run_simulation", {}, "F", card=card) is None


def test_an_other_args_call_never_gets_a_repeat_card():
    session = ChatSession()
    hold = job_holds._open(session, TOOL, "F", "tu", "x", future=_NeverDone())
    hold.told = True
    assert job_holds.repeat_card(session, TOOL, ARGS, "F2") is None
    assert job_holds.repeat_card(session, TOOL, ARGS, "F")["hold_id"] == hold.id


def test_a_tool_whose_tier_cannot_be_read_is_no_job_call(monkeypatch):
    from harness import catalogue

    def broken(tool):
        raise KeyError(tool)

    monkeypatch.setattr(catalogue, "safety_tier_for", broken)
    assert job_holds.is_job_tool(TOOL) is False


def test_a_late_result_names_its_job_only_by_a_non_empty_string_id():
    assert job_holds._job_id_in("x", {"job": {"id": "j1"}}) == "j1"
    assert job_holds._job_id_in("x", {"job": {"id": 5}}) is None
    assert job_holds._job_id_in("x", {"job_id": ""}) is None
    assert job_holds._job_id_in("x", {"job_id": 7}) is None


def test_a_returned_pool_job_tool_opens_no_hold_on_its_outcome():
    outcome = {"outcome": "uncertain", "job_id": "j"}
    assert job_holds.open_on_outcome(ChatSession(), TOOL, "F", "tu", outcome) is None


def test_only_a_named_project_of_the_queue_tool_reads_the_store(monkeypatch):
    def no_read():
        raise AssertionError("the store was read")

    monkeypatch.setattr(chat_tools, "_acting", no_read)
    assert job_holds._store_hold("run_simulation", ARGS, "F") is None
    for args in (None, {}, {"project_id": ""}, {"project_id": 5}):
        assert job_holds._store_hold(TOOL, args, "F") is None, args


def test_a_held_solve_points_to_its_status_and_abort_simulation():
    session = ChatSession()
    job_holds._open(session, "run_simulation", "F", "tu", "x", worker=_Thread())
    message = job_holds.check(session, "run_simulation", {}, "F")[1]
    assert "Check the solve's status" in message and "abort_simulation" in message


def test_a_store_hold_names_the_queued_job(monkeypatch, store):
    rows, _seen, _calls = store
    rows.append({"id": "j9", "project_key": "org:alpha", "kind": "solve"})
    message = _error(_call(monkeypatch, ChatSession(), TOOL, ARGS))["message"]
    assert "job j9" in message


def test_governs_a_completed_repeat_when_no_step_is_next(monkeypatch):
    from services.assistant_tasks import governs

    session = _task(monkeypatch, [_step("completed", ARGS)])
    assert governs(session, TOOL, ARGS) is True


def test_governs_no_repeat_of_a_step_that_did_not_complete(monkeypatch):
    from services.assistant_tasks import governs

    session = _task(
        monkeypatch,
        [
            _step("failed", ARGS, superseded=True),
            _step("pending", {}, tool="run_simulation"),
        ],
    )
    assert governs(session, TOOL, ARGS) is False

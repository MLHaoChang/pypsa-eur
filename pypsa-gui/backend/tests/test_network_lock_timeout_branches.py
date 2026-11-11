"""07f-b (C4): the timed waiters on the project's network lock. A
whole-directory operation now holds that lock for up to its report-lock
bound, so each waiter's timeout branch is pinned here: past the bound the
`agent_turn` guard refuses the tool, and `settle_engine` leaves the step for
the next poll. (`run_envelope.answering` is pinned in
`test_run_intake_grant.py`, where its fixtures live.)"""

from __future__ import annotations

import contextlib
import threading
import types


class _Busy:
    """A lock someone else holds past every bound: `acquire` waits nothing
    and fails, and records the timeout it was given."""

    def __init__(self):
        self.timeouts = []

    def acquire(self, blocking=True, timeout=-1):
        self.timeouts.append(timeout)
        return False

    def release(self):  # pragma: no cover - never held
        raise AssertionError("released a lock that was never acquired")


def test_the_agent_turn_guard_refuses_the_tool_when_the_network_lock_stays_busy(
    monkeypatch,
):
    from harness import loop, run_loops
    from services import run_envelope

    busy = _Busy()
    ctx = types.SimpleNamespace(mutation_lock=busy, solver_state_lock=threading.RLock())
    rechecks, heads, admitted = [], [], []
    monkeypatch.setattr(
        run_envelope, "chain_head", lambda *a, **k: heads.append(1) or ("fp", None)
    )

    def run_turn(session, message, **_):
        yield (
            "tool_request",
            {"tool_use_id": "t1", "tool_name": "set_component", "args": {}},
        )
        ok = session.decision_guard("set_component", {})
        admitted.append(ok)
        if ok:  # pragma: no cover - the mutant path
            yield "tool_running", {"tool_use_id": "t1"}
        else:  # `confirm` turns a refused guard into a revoked decision
            yield (
                "tool_error",
                {"tool_use_id": "t1", "error_kind": "confirmation_revoked"},
            )

    monkeypatch.setattr(loop, "run_turn", run_turn)
    monkeypatch.setattr("harness.eventlog.record", lambda *a, **k: None)
    session = types.SimpleNamespace(usage_acc={})
    out = run_loops.agent_turn(
        "unused-path",
        ctx,
        session,
        tools=["set_component"],
        message="m",
        recheck=lambda: rechecks.append(1) or True,
    )
    assert admitted == [False]
    assert busy.timeouts == [5.0]
    assert rechecks == [] and heads == []  # nothing past the lock ran
    [call] = out["calls"]
    assert call["ran"] is False and call["error"] == "confirmation_revoked"
    assert session.decision_guard is None  # the turn's guard is taken down


def test_settle_engine_leaves_the_step_waiting_when_the_network_lock_stays_busy(
    monkeypatch,
    tmp_path,
):
    from harness import continuation, run_loops, runner
    from services import assistant_tasks as tasks

    task = {
        "run": {"runner": {"lease_owner": "a"}},
        "steps": [
            {
                "id": "s1",
                "attempt_id": "at1",
                "status": "waiting",
                "job": {"study": "margin_loop"},
            }
        ],
    }
    settled, applied = [], []
    monkeypatch.setattr(runner, "_file_lock", lambda _p: contextlib.nullcontext())
    monkeypatch.setattr(runner, "_read", lambda _p: task)
    monkeypatch.setattr(
        continuation, "engine_record", lambda *a, **k: ("ended", {"status": "met"})
    )
    monkeypatch.setattr(tasks, "settle_engine", lambda *a, **k: settled.append(a))
    monkeypatch.setattr(run_loops, "engine_applied", lambda *a, **k: applied.append(a))
    busy = _Busy()
    ctx = types.SimpleNamespace(mutation_lock=busy)

    state = continuation.settle_engine(
        tmp_path / "task.json",
        ("task", "s1", "at1"),
        instance="a",
        ctx=ctx,
    )
    assert state == "running"  # the next poll retries
    assert busy.timeouts == [continuation.ENGINE_LOCK_SECONDS] == [5.0]
    assert settled == [] and applied == []
    assert task["steps"][0]["status"] == "waiting"

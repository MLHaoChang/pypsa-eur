"""A sequential task cannot certify a solver start as a completed step."""

# ruff: noqa: F811 -- imported shared fixtures.
import pytest

from services import assistant_tasks as tasks, chat_tools
from tests.test_assistant_capabilities import session, dispatch, output  # noqa: F401
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


@pytest.mark.parametrize("tool", ["run_simulation", "run_ac_pf_stage"])
@pytest.mark.parametrize(
    "result",
    [
        {"status": "started"},
        {"running": True},
        {"status": "started", "final_status": "completed", "running": True},
    ],
)
def test_incomplete_solver_receipt_cannot_advance_task(session, tool, result):
    task = chat_tools.start_task(
        title="Solve then export",
        request_id="solver-receipt",
        steps=[
            {"id": "solve", "tool": tool, "args": {}},
            {"id": "export", "tool": "export_network_nc", "args": {}},
        ],
    )
    ticket = tasks.begin_step(session, tool, {})
    state = tasks.finish_step(ticket, result=result)
    assert state["status"] == "needs_review" and state["completed_steps"] == 0
    assert state["next_step"]["tool"] == tool
    assert state["next_step"]["status"] == "uncertain"
    assert chat_tools.get_task(task["task_id"])["completed_steps"] == 0


@pytest.mark.parametrize("boundary", ["deadline", "missing_queue", "abort"])
def test_task_solver_wait_refuses_completion_without_terminal_outcome(
    session, monkeypatch, boundary
):
    import time

    from harness import continuation
    from routers.simulation import BufferedLogQueue
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    task = chat_tools.start_task(
        title="Solve then export",
        request_id="solver-wait",
        steps=[
            {"id": "solve", "tool": "run_simulation", "args": {}},
            {"id": "export", "tool": "export_network_nc", "args": {}},
        ],
    )

    def start_only():
        with ctx.solver_state_lock:
            ctx.solver_state.update(status="running", kind="lopf")
            ctx.solver_state["log_queue"] = (
                None if boundary == "missing_queue" else BufferedLogQueue(maxlen=100)
            )
        if boundary == "abort":
            session.abort_event.set()
        return {"status": "started"}

    monkeypatch.setattr(chat_tools, "run_simulation", start_only)
    # Dispatch calls through DISPATCHERS; without this a real solve ran instead.
    monkeypatch.setitem(chat_tools.DISPATCHERS, "run_simulation", start_only)
    monkeypatch.setattr(continuation, "GRACE_SECONDS", 0.01)
    try:
        result = output(dispatch(session, "run_simulation", {}))
        # LC-12: the step waits on its job instead of the turn; a running
        # status with no new native worker behind it is no outcome, so the
        # job settles failed and the plan never counts the solve as done.
        assert result["status"] == "started" and result["job_id"].startswith("fg:")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = chat_tools.get_task(task["task_id"])
            if state["next_step"]["status"] != "waiting":
                break
            time.sleep(0.05)
        assert state["completed_steps"] == 0
        assert state["next_step"]["tool"] == "run_simulation"
        assert state["next_step"]["status"] == "failed"
    finally:
        with ctx.solver_state_lock:
            ctx.solver_state["status"] = "idle"

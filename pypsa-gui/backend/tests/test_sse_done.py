"""
SSE `done` event — results-delivery race.

`run_simulation` queues the sentinel (None) from its `finally` BEFORE the
worker / queue-dispatcher writes the final lifecycle status. The SSE generator
must therefore WAIT for the status to go terminal before emitting `done`, or a
successful solve can be delivered with status="running" and the frontend
mis-maps it to "failed".

This drives the real `/api/simulation/log_stream` endpoint with a hand-built
queue + a deliberately-delayed terminal-status flip and asserts the `done`
payload carries the TRUE outcome.
"""
from __future__ import annotations

import json
import threading
import time

from routers import simulation as sim
from services.pypsa_service import PyPSAService


def _write_state(ctx, **kw) -> None:
    """
    Write lifecycle state into a SPECIFIC context, the way a worker does.

    `sim._state_update` goes through the `_ActiveStateProxy`, which resolves to
    whatever context is active in the CALLING context — request-scoped since
    Step 0b, and the process foreground otherwise. That is the right behaviour
    for a request handler and the wrong one for these tests: the first request
    the client makes adopts the process foreground as its session scratch and
    clears `PyPSAService._active`, so a plain `threading.Thread` firing 0.25s
    later self-heals a BRAND-NEW empty context and writes "completed" into an
    object nobody is reading. The stream would then poll the original context
    forever and report the mid-transition "running" — a test failure that says
    nothing about the code under test.

    The real solver worker does not have this problem: `/run` hands it the
    context it must write, exactly as this helper does (and as
    `_solver_in_flight_ctx` reads it back for a background solve). Pinning the
    context here keeps the test about the results-delivery race it was written
    for, rather than about contextvar propagation into a bare thread.
    """
    with ctx.solver_state_lock:
        ctx.solver_state.update(kw)


def _read_done_payload(client) -> dict:
    """Consume the SSE stream until the `done` event; return its parsed data."""
    event = None
    with client.stream("GET", "/api/simulation/log_stream") as resp:
        for raw in resp.iter_lines():
            line = raw if isinstance(raw, str) else raw.decode()
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event == "done":
                return json.loads(line.split(":", 1)[1].strip())
    raise AssertionError("SSE stream ended without a `done` event")


def test_sse_done_waits_for_terminal_status(client):
    # Arrange: a run whose sentinel is already queued while status is still
    # "running" — exactly the post-sentinel / pre-finalise window.
    q = sim.BufferedLogQueue()
    # The context this "run" belongs to. The stream request resolves the same
    # one (it adopts the process foreground as the session's scratch context),
    # so the generator and the worker below are looking at one state dict.
    run_ctx = PyPSAService.get_active_context()
    _write_state(
        run_ctx,
        status="running", condition=None, objective=None,
        solve_time=None, last_failure=None, log_queue=q,
    )
    q.put("[PHASE] Solve complete")
    q.put(None)  # sentinel emitted BEFORE the terminal status is written

    # The worker's post-return status write lands AFTER the sentinel — model it
    # with a short delay on a background thread.
    def _finalize():
        time.sleep(0.25)
        _write_state(
            run_ctx,
            status="completed", condition="optimal",
            objective=123.0, solve_time=1.5, last_failure=None,
        )

    threading.Thread(target=_finalize, daemon=True).start()

    # Act + Assert: the `done` payload must reflect the TERMINAL outcome, not
    # the mid-transition "running" the naive snapshot would have captured.
    payload = _read_done_payload(client)
    assert payload["status"] == "completed", payload
    assert payload["objective"] == 123.0
    assert payload["failure"] is None


def test_sse_done_terminal_status_passes_through_immediately(client):
    # When status is ALREADY terminal at sentinel time, `done` fires without
    # waiting (the common path) and carries a failure card when present.
    q = sim.BufferedLogQueue()
    fail = {"category": "infeasible", "title": "No feasible solution",
            "hint": "Relax a binding limit.", "detail": "infeasible"}
    _write_state(
        PyPSAService.get_active_context(),
        status="failed", condition="infeasible", objective=None,
        solve_time=0.3, last_failure=fail, log_queue=q,
    )
    q.put(None)

    payload = _read_done_payload(client)
    assert payload["status"] == "failed", payload
    assert payload["failure"] is not None
    assert payload["failure"]["category"] == "infeasible"

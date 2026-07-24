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
    sim._state_update(
        status="running", condition=None, objective=None,
        solve_time=None, last_failure=None, log_queue=q,
    )
    q.put("[PHASE] Solve complete")
    q.put(None)  # sentinel emitted BEFORE the terminal status is written

    # The worker's post-return status write lands AFTER the sentinel — model it
    # with a short delay on a background thread.
    def _finalize():
        time.sleep(0.25)
        sim._state_update(
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
    sim._state_update(
        status="failed", condition="infeasible", objective=None,
        solve_time=0.3, last_failure=fail, log_queue=q,
    )
    q.put(None)

    payload = _read_done_payload(client)
    assert payload["status"] == "failed", payload
    assert payload["failure"] is not None
    assert payload["failure"]["category"] == "infeasible"

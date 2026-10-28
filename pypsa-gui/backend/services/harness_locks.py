"""The harness run's hold on its project (study harness design §6.6, GK B).

While a run dispatches or waits on an effectful step, a foreground write to
that project is refused 409 `project_locked` ("a harness run is editing this
project") rather than racing the runner. The hold is in-process: the app is
one process, and the run's lease (`harness/runner.py`) guards takeover after
a restart, when no hold survives.

`in_run()` is true only inside the runner's own tick and dispatch: a
contextvar the runner sets, which no header, body or route can set.
"""
from __future__ import annotations

import threading
from contextvars import ContextVar

MESSAGE = "A harness run is editing this project; wait for its step to finish or stop the run."

_LOCK = threading.Lock()
_HOLDS: dict[str, str] = {}  # project uuid -> task id
_RUN_TASK: ContextVar[str | None] = ContextVar("harness_run_task", default=None)


def hold(project_uuid: str, task_id: str) -> None:
    with _LOCK:
        _HOLDS[str(project_uuid)] = task_id


def release(project_uuid: str, task_id: str) -> None:
    with _LOCK:
        if _HOLDS.get(str(project_uuid)) == task_id:
            del _HOLDS[str(project_uuid)]


def holder(project_uuid) -> str | None:
    if not project_uuid:
        return None
    with _LOCK:
        return _HOLDS.get(str(project_uuid))


def enter_run(task_id: str):
    """Mark the current context as the runner acting for `task_id`."""
    return _RUN_TASK.set(task_id)


def leave_run(token) -> None:
    _RUN_TASK.reset(token)


def in_run() -> str | None:
    return _RUN_TASK.get()


def refuses(project_uuid) -> bool:
    """A write to `project_uuid` from outside the run that holds it."""
    task = holder(project_uuid)
    return task is not None and in_run() != task


def detail() -> dict:
    return {"error_kind": "project_locked", "message": MESSAGE, "lock": None}


def _reset_for_tests() -> None:
    with _LOCK:
        _HOLDS.clear()

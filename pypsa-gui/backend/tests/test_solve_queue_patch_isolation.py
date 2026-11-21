"""An instance-level patch of the solve queue never outlives its test.

pytest < 9.2 (CI pins 9.1.1) undoes `monkeypatch.setattr(solve_queue, "m", stub)`
by writing the old bound method into the singleton's `__dict__`. That copy
shadowed every later `monkeypatch.setattr(SolveQueue, "m", ...)` for the rest of
the worker, so the dispatcher ran the real `_run_solve_job` in a test that had
patched it (CI shard 1/8 on #1832). The per-test reset drops such copies.
"""

from __future__ import annotations

import pytest

from services import solve_queue as sq
from tests.conftest import _reset_solve_queue_or_exit


def test_a_class_patch_reaches_the_singleton_after_an_instance_patch_is_undone(
    monkeypatch,
):
    q = sq.solve_queue
    inner = pytest.MonkeyPatch()
    inner.setattr(q, "_run_solve_job", lambda job: None)
    inner.undo()
    # The same reset the autouse fixture runs between tests.
    _reset_solve_queue_or_exit()

    def patched(self, job):
        return "patched"

    monkeypatch.setattr(sq.SolveQueue, "_run_solve_job", patched)
    assert q._run_solve_job.__func__ is patched, (
        "the dispatcher would run the real solve runner, not the test's patch"
    )

"""The shard fail-fast hook in conftest fires when the solve queue cannot drain."""
import pathlib
import queue
import sys

import pytest

from services import solve_queue as sq


def _tests_conftest():
    # Two conftest.py files exist (backend/ and tests/); `import conftest` is ambiguous.
    here = pathlib.Path(__file__).with_name("conftest.py").resolve()
    return next(
        m for m in sys.modules.values()
        if getattr(m, "__file__", None) and pathlib.Path(m.__file__).resolve() == here
    )


def test_undrainable_reset_dumps_stacks_and_exits(tmp_path, monkeypatch):
    conftest = _tests_conftest()
    q = queue.Queue()
    q.unfinished_tasks = 1  # an in-flight job that never reaches task_done()
    monkeypatch.setattr(sq.solve_queue, "_q", q)
    monkeypatch.setattr(sq, "_RESET_JOIN_TIMEOUT_S", 0.05)
    monkeypatch.setattr(conftest, "_RESULTS_DIR", tmp_path)

    with pytest.raises(pytest.exit.Exception) as exc:
        conftest._reset_solve_queue_or_exit()

    assert exc.value.returncode == 3
    assert "could not drain" in exc.value.msg
    dump = (tmp_path / "solve-queue-stall.txt").read_text()
    assert "unfinished_tasks=1" in dump and "--- thread" in dump and "--- ps" in dump

"""The shard fail-fast hook in conftest fires when the solve queue cannot drain."""

import os
import pathlib
import queue
import sys
import threading

import pytest

from services import solve_queue as sq


def _tests_conftest():
    # Two conftest.py files exist (backend/ and tests/); `import conftest` is ambiguous.
    here = pathlib.Path(__file__).with_name("conftest.py").resolve()
    return next(
        m
        for m in sys.modules.values()
        if getattr(m, "__file__", None) and pathlib.Path(m.__file__).resolve() == here
    )


def test_undrainable_reset_dumps_stacks_and_exits(tmp_path, monkeypatch):
    conftest = _tests_conftest()
    q = queue.Queue()
    q.unfinished_tasks = 1  # an in-flight job that never reaches task_done()
    monkeypatch.setattr(sq.solve_queue, "_q", q)
    monkeypatch.setattr(sq, "_RESET_JOIN_TIMEOUT_S", 0.05)
    monkeypatch.setattr(conftest, "_RESULTS_DIR", tmp_path)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)  # may run in a worker
    armed = []
    killed = []
    monkeypatch.setattr(conftest, "_kill_child_processes", lambda: killed.append(True))
    monkeypatch.setattr(conftest, "_arm_exit_watchdog", lambda: armed.append(True))

    with pytest.raises(pytest.exit.Exception) as exc:
        conftest._reset_solve_queue_or_exit()

    assert exc.value.returncode == 3
    assert killed == [True]  # children holding CI's tee pipe are killed first
    assert armed == [True]  # a stuck non-daemon thread must not hang the exit
    assert "could not drain" in exc.value.msg
    dump = (tmp_path / "solve-queue-stall.txt").read_text()
    assert "unfinished_tasks=1" in dump and "--- thread" in dump and "--- ps" in dump


def test_exit_watchdog_is_a_daemon_timer_that_hard_exits(monkeypatch):
    conftest = _tests_conftest()
    made = []

    class FakeTimer:
        def __init__(self, interval, fn, args=()):
            made.append((interval, fn, args))
            self.daemon = False

        def start(self):
            made.append(("daemon", self.daemon))

    monkeypatch.setattr(threading, "Timer", FakeTimer)
    conftest._arm_exit_watchdog()

    assert made[0] == (conftest._EXIT_GRACE_S, os._exit, (3,))
    assert made[1] == ("daemon", True)


def test_child_kill_skips_the_ps_process_itself(monkeypatch):
    """`ps --ppid` lists itself; its (reaped) pid must never be signalled."""
    import subprocess

    class FakePs:
        pid = 4242

        def __init__(self, *args, **kwargs):
            pass

        def communicate(self, timeout=None):
            return "4242\n4343\n", None

    sent = []
    monkeypatch.setattr(subprocess, "Popen", FakePs)
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append(pid))
    _tests_conftest()._kill_child_processes()
    assert sent == [4343]


@pytest.mark.skipif(sys.platform != "linux", reason="uses Linux `ps --ppid`")
def test_child_processes_are_killed():
    """Run the real `ps --ppid` path in an isolated interpreter.

    Killing every child of the shard's own pytest process would break whichever
    helper (mcp client, blender or pdf worker) a neighbouring test keeps alive.
    """
    import inspect
    import subprocess

    code = (
        "import os, subprocess\n"
        + inspect.getsource(_tests_conftest()._kill_child_processes)
        + "\nchild = subprocess.Popen(['sleep', '60'])\n"
        "_kill_child_processes()\n"
        "print(child.wait(timeout=10))\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60
    )
    assert out.stdout.strip() == "-9", out.stderr


def test_each_xdist_worker_writes_its_own_stall_dump(tmp_path, monkeypatch):
    """Workers that stall together must not overwrite each other's dump."""
    conftest = _tests_conftest()
    monkeypatch.setattr(conftest, "_RESULTS_DIR", tmp_path)
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw7")
    first = conftest._dump_stall_diagnostics(1)
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw8")
    second = conftest._dump_stall_diagnostics(1)

    assert (first.name, second.name) == (
        "solve-queue-stall-gw7.txt",
        "solve-queue-stall-gw8.txt",
    )
    assert first.exists() and second.exists()

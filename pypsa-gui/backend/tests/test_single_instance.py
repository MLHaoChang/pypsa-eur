"""
One window, one process (phase 2a, Task 1 — spec D11).

D11 is mandatory, not a nicety: `PyPSAService._active` is process-global and
the frontend keeps `currentProject` in shared `localStorage`, so two windows
fight over one pointer and one in-memory network.

**A kernel-released lock, not a pid file.** The obvious design — write the pid,
and on a stale lock check whether that pid is alive — is unimplementable
portably, and its Windows spelling is actively dangerous: `os.kill(pid, 0)`
does not raise there, it calls `TerminateProcess`. A liveness probe that kills
the process it probes would take down the running app mid-solve, skipping the
solver threads' `finally:`.

`flock` / `msvcrt.locking` have no such problem. The OS drops the lock when the
holding process dies, so there is no staleness window to size, no pid to probe,
and nothing to reclaim.

This is NOT the lock `main.run_first_run_import` takes. That one guards a
113 MB copy for the duration of one import and is deliberately reclaimable
after an hour; this one is held for the life of the process. Sharing them would
make a second launch skip the import *and* run.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from desktop.single_instance import AlreadyRunning, SingleInstance


@pytest.fixture
def lock_path(tmp_path):
    return tmp_path / "appdata" / "single-instance.lock"


def test_acquire_succeeds_on_a_free_lock(lock_path):
    with SingleInstance(lock_path) as lock:
        assert lock.held


def test_a_second_acquire_fails_while_the_first_is_held(lock_path):
    """
    The property D11 actually needs. `flock` is per open-file-description and
    Windows byte-range locks are per-handle, so two acquisitions conflict even
    inside ONE process — which is what makes this testable without spawning a
    second interpreter.
    """
    with SingleInstance(lock_path):
        with pytest.raises(AlreadyRunning):
            SingleInstance(lock_path).acquire()


def test_release_then_reacquire_succeeds(lock_path):
    first = SingleInstance(lock_path)
    first.acquire()
    first.release()

    with SingleInstance(lock_path) as second:
        assert second.held


def test_the_lock_dies_with_the_process_that_held_it(lock_path, tmp_path):
    """
    The whole reason for a kernel lock. A pid-file design has to guess how long
    a lock may sit before it is stale — too short steals the lock from a user
    who left the app open, too long locks them out after a crash. Here the
    kernel answers it: the holder is killed with SIGKILL, so no `finally:`
    runs, no cleanup happens, and the next launch still gets the lock.
    """
    ready = tmp_path / "ready"
    holder = subprocess.Popen([
        sys.executable, "-c", textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
            from desktop.single_instance import SingleInstance
            lock = SingleInstance({str(lock_path)!r})
            lock.acquire()
            open({str(ready)!r}, "w").close()
            time.sleep(120)
        """),
    ])
    try:
        for _ in range(200):
            if ready.exists():
                break
            import time as _t
            _t.sleep(0.05)
        assert ready.exists(), "the holder never acquired the lock"

        with pytest.raises(AlreadyRunning):
            SingleInstance(lock_path).acquire()

        holder.kill()
        holder.wait(timeout=10)
    finally:
        if holder.poll() is None:
            holder.kill()

    # No cleanup ran in the killed process. The kernel released it anyway.
    with SingleInstance(lock_path) as lock:
        assert lock.held


def test_a_garbage_lock_file_does_not_wedge_future_launches(lock_path):
    """
    Phase 1b shipped a reader that crashed on shape-valid JSON and made the
    import permanently impossible. A lock file is even more exposed — it
    survives crashes by design — so its contents must never be load-bearing.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_bytes(b"\x00\xff not json, not a pid, not utf-8")

    with SingleInstance(lock_path) as lock:
        assert lock.held


def test_the_app_data_directory_is_created_if_absent(lock_path):
    """
    `ensure_app_dirs()` runs inside `lifespan`, which is AFTER this lock is
    taken — the whole point is to refuse before importing the backend. So the
    directory may genuinely not exist yet on a first run.
    """
    assert not lock_path.parent.exists()

    with SingleInstance(lock_path) as lock:
        assert lock.held
        assert lock_path.parent.is_dir()


def test_release_is_idempotent(lock_path):
    """The shutdown path may run twice; a second release must not raise."""
    lock = SingleInstance(lock_path)
    lock.acquire()
    lock.release()
    lock.release()
    assert not lock.held

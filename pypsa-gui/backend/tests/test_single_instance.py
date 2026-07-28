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

`flock` / `msvcrt.locking` have no such problem: the OS drops the lock when the
holding process dies, so there is no pid to probe and nothing to reclaim.

One qualification, because the original wording said "however it dies" without
one. That is exact for POSIX `flock`, which releases atomically with the fd —
proven below by SIGKILLing the holder. Microsoft documents Windows as NOT
atomic: when a process terminates holding a lock, "the time it takes for the
operating system to unlock these locks depends upon available system
resources". So a small staleness window does exist there, which is why
`acquire()` retries briefly before refusing. Unmeasured — Task 7 Step 8.

This is NOT the lock `main.run_first_run_import` takes. That one guards a
113 MB copy for the duration of one import and is deliberately reclaimable
after an hour; this one is held for the life of the process. Sharing them would
make a second launch skip the import *and* run.
"""
from __future__ import annotations

import errno
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from unittest import mock

import pytest

from desktop import single_instance
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


def test_a_lock_that_is_unsupported_is_not_reported_as_a_second_instance(lock_path):
    """
    `flock` raises `OSError` for `ENOLCK`, `EOPNOTSUPP`, `EBADF` and `EINTR`,
    not only for contention — and advisory locking is unsupported or silently
    degraded on several network filesystems, which `PYPSAGUI_APP_DATA_DIR` can
    point at.

    Mapping every `OSError` to `AlreadyRunning` wedges that machine forever:
    the user is told another instance is running when none is, and the design
    deliberately makes the lock file's contents unreadable, so there is nothing
    to delete that helps. Harder to escape than the stale-pid-file failure this
    design was chosen to avoid.
    """
    def unsupported(fd):
        raise OSError(errno.ENOLCK, "No locks available")

    with mock.patch.object(single_instance, "_lock", unsupported):
        with pytest.raises(OSError) as caught:
            SingleInstance(lock_path).acquire()

    assert not isinstance(caught.value, AlreadyRunning)
    assert caught.value.errno == errno.ENOLCK


def test_real_contention_is_still_reported_as_a_second_instance(lock_path):
    """The discrimination must not swallow the case the lock exists for."""
    def contended(fd):
        raise OSError(errno.EWOULDBLOCK, "Resource temporarily unavailable")

    with mock.patch.object(single_instance, "_lock", contended):
        with pytest.raises(AlreadyRunning):
            SingleInstance(lock_path, retry_for=0.2, retry_interval=0.05).acquire()


def test_a_lock_still_held_by_a_dead_process_is_retried_briefly(lock_path):
    """
    POSIX `flock` is released atomically with the fd, but Windows is documented
    NOT to be: Microsoft's `LockFile` remarks say that when a process
    terminates holding a lock, "the time it takes for the operating system to
    unlock these locks depends upon available system resources", and recommend
    unlocking before exit. This shell installs an `os._exit()` rung that skips
    exactly that.

    Without a retry, a crash — or a hard quit — could refuse the NEXT launch
    with "another instance is already running", and the user has nothing to
    delete and no way to know how long to wait.

    Documentation-based, unmeasured, and unmeasurable from here; Task 7 Step 8
    is where it gets tested on the platform it applies to. The retry is cheap
    and correct on both.
    """
    attempts = []

    def frees_up_on_the_third_try(fd):
        attempts.append(fd)
        if len(attempts) < 3:
            raise OSError(errno.EWOULDBLOCK, "still held by the dead process")

    with mock.patch.object(single_instance, "_lock", frees_up_on_the_third_try):
        lock = SingleInstance(lock_path, retry_for=2.0, retry_interval=0.05)
        lock.acquire()

    assert lock.held
    assert len(attempts) == 3


def test_the_retry_does_not_hold_the_launch_indefinitely(lock_path):
    """A genuine second instance must still be refused promptly."""
    def contended(fd):
        raise OSError(errno.EWOULDBLOCK, "held")

    started = time.monotonic()
    with mock.patch.object(single_instance, "_lock", contended):
        with pytest.raises(AlreadyRunning):
            SingleInstance(lock_path, retry_for=0.3, retry_interval=0.05).acquire()
    elapsed = time.monotonic() - started

    assert elapsed < 3, f"refusing a second launch took {elapsed:.1f}s"


def test_a_failed_acquire_leaks_no_file_descriptor(lock_path):
    """
    `acquire()` opens before it locks, and the retry loop makes it open more
    than once. A launcher that retried and then raised would leak one fd per
    attempt.
    """
    def contended(fd):
        raise OSError(errno.EWOULDBLOCK, "held")

    before = len(os.listdir("/dev/fd")) if os.path.isdir("/dev/fd") else None
    with mock.patch.object(single_instance, "_lock", contended):
        with pytest.raises(AlreadyRunning):
            SingleInstance(lock_path, retry_for=0.3, retry_interval=0.05).acquire()

    if before is not None:
        after = len(os.listdir("/dev/fd"))
        assert after <= before + 1, f"fd count went {before} -> {after}"


def test_the_default_retry_budget_is_not_zero(lock_path):
    """
    Every other retry test passes an explicit `retry_for=`, so `RETRY_FOR = 0.0`
    passed all of them — the shipped default was untested. It is the value that
    actually runs on a user's machine after a hard quit.
    """
    assert single_instance.RETRY_FOR > 0

    lock = SingleInstance(lock_path)
    assert lock._retry_for == single_instance.RETRY_FOR


def test_release_is_idempotent(lock_path):
    """The shutdown path may run twice; a second release must not raise."""
    lock = SingleInstance(lock_path)
    lock.acquire()
    lock.release()
    lock.release()
    assert not lock.held

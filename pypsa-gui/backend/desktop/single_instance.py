"""
One window, one process (spec D11).

D11 is mandatory rather than a nicety: `PyPSAService._active` is process-global
and the frontend keeps `currentProject` in shared `localStorage`, so a second
window fights the first over one pointer and one in-memory network.

**A kernel-released lock, not a pid file.**

The obvious design — record the pid, and on finding an existing lock check
whether that pid is still alive — is unimplementable portably, and its Windows
spelling is actively dangerous. `os.kill(pid, 0)` does not raise there: Python
documents any signal other than `CTRL_C_EVENT`/`CTRL_BREAK_EVENT` as calling
`TerminateProcess`. A liveness probe written that way would kill the running
app, skipping the solver threads' `finally:` blocks — the outcome the whole
shutdown workstream exists to prevent.

`flock` and `msvcrt.locking` have no such problem. The kernel drops the lock
when the holding process dies, so there is no pid to probe and nothing to
reclaim: a crash and a clean exit look alike to the next launch.

With one qualification, since the first version of this paragraph said "however
it dies" and stated it absolutely. That is exact for POSIX `flock`, which
releases atomically with the fd. Microsoft documents Windows as NOT atomic —
when a process terminates holding a lock, "the time it takes for the operating
system to unlock these locks depends upon available system resources", and it
recommends unlocking before exit, which a hard quit skips. So a staleness
window does exist there; `acquire` retries briefly rather than pretending
otherwise. Documentation-based and unmeasured — Task 7 Step 8 is where it gets
tested on the platform it applies to.

**This is not the lock `main.run_first_run_import` takes.** That one guards a
113 MB copy for the duration of one import and is deliberately reclaimable
after an hour. This one is held for the life of the process. Sharing them would
make a second launch skip the import *and* run.
"""
from __future__ import annotations

import errno
import os
import sys
import time
from pathlib import Path

if sys.platform == "win32":  # pragma: no cover - exercised on Windows
    import msvcrt
else:
    import fcntl

# Errnos that mean "someone else holds it" and nothing else. Everything else
# propagates: see `acquire`.
_CONTENDED = frozenset(
    {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK}
)

# How long to keep retrying a CONTENDED lock before refusing the launch.
#
# Zero would be right if release were always atomic with process death. It is
# on POSIX `flock`. Microsoft documents Windows otherwise: when a process
# terminates holding a lock, "the time it takes for the operating system to
# unlock these locks depends upon available system resources", with an explicit
# recommendation to unlock before exiting — and this shell installs an
# `os._exit()` rung that does not. Without a retry, one hard quit could refuse
# the next launch with no recourse and no indication of how long to wait.
#
# Short enough that a genuine second launch is still refused promptly.
RETRY_FOR = 1.5


class AlreadyRunning(RuntimeError):
    """Another instance holds the lock."""


class SingleInstance:
    """
    A process-lifetime exclusive lock on a file.

    Usable as a context manager. `release()` is idempotent, because the
    shutdown path can plausibly run twice.
    """

    def __init__(
        self, path, *, retry_for: float = RETRY_FOR, retry_interval: float = 0.1
    ) -> None:
        self.path = Path(path)
        self._fd: int | None = None
        self._retry_for = retry_for
        self._retry_interval = retry_interval

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        # `ensure_app_dirs()` runs inside the FastAPI lifespan, which is after
        # this — the point of the lock is to refuse before importing the
        # backend at all. So on a first run the directory may not exist.
        self.path.parent.mkdir(parents=True, exist_ok=True)

        deadline = time.monotonic() + self._retry_for
        while True:
            # The file's CONTENTS are never read and never load-bearing. A lock
            # file survives crashes by design, so anything that parsed it would
            # be a way for a corrupt byte to wedge every future launch.
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                _lock(fd)
            except OSError as exc:
                os.close(fd)  # before anything else — the retry loop reopens
                if exc.errno not in _CONTENDED:
                    # NOT "another instance is running". `flock` also raises for
                    # ENOLCK, EOPNOTSUPP, EBADF and EINTR, and advisory locking
                    # is unsupported or silently degraded on several network
                    # filesystems — which `PYPSAGUI_APP_DATA_DIR` can point at.
                    # Reporting those as contention wedges that machine
                    # permanently: the lock file is deliberately unreadable, so
                    # there is nothing the user can delete to recover.
                    raise
                if time.monotonic() >= deadline:
                    raise AlreadyRunning(
                        f"another instance already holds {self.path}"
                    ) from exc
                time.sleep(self._retry_interval)
                continue
            self._fd = fd
            return

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        except OSError:
            # Closing the descriptor releases it regardless; an error here
            # must not stop the shutdown sequence.
            pass
        os.close(fd)

    def __enter__(self) -> SingleInstance:
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()


if sys.platform == "win32":  # pragma: no cover - exercised on Windows

    def _lock(fd: int) -> None:
        # Byte-range lock on byte 0. Windows locks are per-handle, so two
        # acquisitions conflict within one process just as `flock` does.
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:

    def _lock(fd: int) -> None:
        # `flock` is per open-file-description, not per process, so a second
        # `os.open` + `flock` conflicts even inside one interpreter — which is
        # what makes the "second acquire fails" case testable without spawning
        # another process.
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

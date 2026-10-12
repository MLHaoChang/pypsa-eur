"""Bounded cross-process transactions for owner-scoped assurance records."""

from contextlib import contextmanager
import os
import threading
import time
import weakref

_REGISTRY_LOCK = threading.Lock()
_LOCKS = weakref.WeakValueDictionary()
_LOCAL = threading.local()


@contextmanager
def network_guard():
    """Publication/read-back must not wait forever behind a detached worker."""
    from fastapi import HTTPException
    from services.pypsa_service import PyPSAService

    lock = PyPSAService.get_lock()
    if not lock.acquire(timeout=1):
        raise HTTPException(409, {"error_kind": "assurance_context_changed"})
    try:
        yield
    finally:
        lock.release()


@contextmanager
def transaction(directory):
    with _REGISTRY_LOCK:
        lock = _LOCKS.get(str(directory))
        if lock is None:
            lock = threading.RLock()
            _LOCKS[str(directory)] = lock
    if not lock.acquire(timeout=5):
        raise TimeoutError("assurance storage busy")
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = str(directory)
        held = getattr(_LOCAL, "held", set())
        if key in held:
            yield
            return
        with (directory / ".owner.lock").open("a+b") as stream:
            os.chmod(stream.name, 0o600)
            if os.name == "nt":
                import msvcrt

                stream.write(b"\0")
                stream.flush()

                def acquire():
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)

                def release():
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                def acquire():
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)

                def release():
                    fcntl.flock(stream, fcntl.LOCK_UN)

            deadline = time.monotonic() + 5
            while True:
                try:
                    acquire()
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("assurance storage busy") from None
                    time.sleep(0.02)
            _LOCAL.held = held | {key}
            try:
                yield
            finally:
                _LOCAL.held = held
                release()
    finally:
        lock.release()

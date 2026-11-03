"""How many application worker processes serve this app (live-voice 09).

Durable conversations and their voice bridge support ONE authoritative
worker: ownership is pinned per process, and nothing coordinates pins across
processes yet. ``detected()`` reads the count from, in order:

1. a declaration by the launcher that starts the server in-process (the
   desktop launcher pins ``uvicorn.Config(workers=1)`` and declares it, so a
   stray ``WEB_CONCURRENCY`` cannot count);
2. ``--workers N`` / ``--workers=N`` (uvicorn, gunicorn) or ``-w N``
   (gunicorn) on the command line, which spawned workers inherit;
3. ``WEB_CONCURRENCY``, which uvicorn reads when ``--workers`` is absent;
4. otherwise one, uvicorn's documented default when neither is given.

An unreadable value is ignored and the next source decides. That reading
cannot see every launcher (a gunicorn ``-c`` config file, ``GUNICORN_CMD_ARGS``,
``uvicorn.run(workers=N)`` in code), so it is only an early, clearer refusal.
The gate itself fails closed: ``hold_store_lock`` takes a non-blocking
exclusive lock on each durable store this process serves and keeps it for the
process lifetime, so a second worker on the same store is refused
(``source: lock``) however it was started. A dead worker's lock is released
by the OS, which is what lets a restarted worker take over.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from fastapi import HTTPException

_DECLARED: tuple[int, str] | None = None
_HELD: dict[str, object] = {}
_HELD_LOCK = threading.Lock()
LOCK_NAME = '.worker.lock'


def declare(workers: int, *, source: str) -> None:
    """Record the worker count a launcher configured itself."""
    global _DECLARED
    _DECLARED = (int(workers), source)


def _positive(value: str | None) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None


def _from_argv(argv: list[str]) -> int | None:
    if not argv:
        return None
    first = Path(argv[0])
    program = first.name
    if program == '__main__.py':  # `python -m uvicorn` / `python -m gunicorn`
        program = first.parent.name
    gunicorn = program.startswith('gunicorn')
    if not (gunicorn or program.startswith('uvicorn') or 'uvicorn' in argv[1:3]):
        return None
    for index, arg in enumerate(argv):
        if arg.startswith('--workers='):
            return _positive(arg.split('=', 1)[1])
        if arg == '--workers' or (gunicorn and arg == '-w'):
            return _positive(argv[index + 1] if index + 1 < len(argv) else None)
    return None


def detected() -> tuple[int, str]:
    """The worker count and which source said so."""
    if _DECLARED is not None:
        return _DECLARED
    from_argv = _from_argv(list(sys.argv))
    if from_argv is not None:
        return from_argv, 'argv'
    from_env = _positive(os.environ.get('WEB_CONCURRENCY'))
    if from_env is not None:
        return from_env, 'WEB_CONCURRENCY'
    return 1, 'default'


def _refuse(workers: int | None, source: str) -> None:
    raise HTTPException(status_code=503, detail={
        'error_kind': 'multi_worker_unsupported',
        'message': (
            'Durable conversations need a single application worker; '
            'run the server with one worker.'
        ),
        'workers': workers,
        'source': source,
    })


def require_single_worker() -> None:
    """Refuse durable-conversation controls when more than one worker runs."""
    workers, source = detected()
    if workers > 1:
        _refuse(workers, source)


def hold_store_lock(directory: str | os.PathLike) -> None:
    """Own ``directory``'s worker lock for this process's lifetime, or refuse.

    Non-blocking: when another live process holds it, this one is a second
    worker on the same store and gets 503 ``multi_worker_unsupported``.
    """
    from harness import durable

    directory = os.path.realpath(directory)
    with _HELD_LOCK:
        if directory in _HELD:
            return
        os.makedirs(directory, exist_ok=True)
        holder = durable.file_lock(
            os.path.join(directory, LOCK_NAME), timeout=0,
            busy='another application worker serves this store',
        )
        try:
            holder.__enter__()
        except TimeoutError:
            _refuse(None, 'lock')
        _HELD[directory] = holder  # never exited: released when the process ends


def _reset_for_tests() -> None:
    global _DECLARED
    _DECLARED = None
    with _HELD_LOCK:
        for holder in _HELD.values():
            holder.__exit__(None, None, None)
        _HELD.clear()

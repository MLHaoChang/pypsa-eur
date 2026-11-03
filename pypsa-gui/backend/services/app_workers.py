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

An unreadable value is ignored and the next source decides.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import HTTPException

_DECLARED: tuple[int, str] | None = None


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
    program = Path(argv[0]).name
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


def require_single_worker() -> None:
    """Refuse durable-conversation controls when more than one worker runs."""
    workers, source = detected()
    if workers > 1:
        raise HTTPException(status_code=503, detail={
            'error_kind': 'multi_worker_unsupported',
            'message': (
                'Durable conversations need a single application worker; '
                'run the server with one worker.'
            ),
            'workers': workers,
            'source': source,
        })


def _reset_for_tests() -> None:
    global _DECLARED
    _DECLARED = None

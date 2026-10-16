"""An ordered map over worker processes, for the island checks' independent
runs (the frequency study's hours, the cuts' failing events).

``ordered_map`` returns what a plain loop would, in the input order, so a
table built from it is row for row the serial one; it runs serially at one
worker. The workers are spawned (never forked: the backend calls this from
a thread), build the shared context once each with ``init(*initargs)`` and
return ``fn(context, item)`` per item; serially the context is built once in
the caller, so nothing is kept between maps. A worker's exception is raised
here with its traceback (the pool attaches it as the cause), the first
failing item in input order, as the serial loop would; a worker that dies
ends the map (``BrokenProcessPool``). ``should_stop()`` is asked between
items and, once True, the pending items are cancelled, the running workers
are terminated and ``Stopped`` is raised, with no worker left behind.

A caller that spawns re-imports its ``__main__`` in every worker, so a
script that calls this without an ``if __name__ == "__main__"`` guard fails;
gridspine's own callers therefore default to one worker, and the campus
service asks for ``workers()`` explicitly.
"""
import logging
import multiprocessing
import os
import sys
from concurrent.futures import FIRST_EXCEPTION, ProcessPoolExecutor, wait

logger = logging.getLogger(__name__)

#: The environment variable that sets the worker count (1: serial).
WORKERS_ENV = "GRIDSPINE_WORKERS"
#: The default ceiling: the campus run shares the machine with the solve
#: queue and the backend.
MAX_DEFAULT_WORKERS = 4
#: How often a parallel map asks ``should_stop``, s.
POLL_S = 0.5


_CONTEXT = None          # a worker process's context (``_start``); never set in the caller


def _start(init, initargs):
    global _CONTEXT
    _CONTEXT = init(*initargs)


def _call(fn, item):
    return fn(_CONTEXT, item)


class Stopped(Exception):
    """``should_stop()`` was True: the map ended before its last item."""


def workers() -> int:
    """The worker count for the campus service: 1 in a frozen (packaged)
    app, whatever ``GRIDSPINE_WORKERS`` says (spawning from the frozen
    binary is untested); else ``GRIDSPINE_WORKERS`` when it is a whole
    number (any other value is warned about and runs serially); else one
    less than the CPUs this process may use, at most
    ``MAX_DEFAULT_WORKERS``."""
    if getattr(sys, "frozen", False):
        return 1
    env = os.environ.get(WORKERS_ENV, "").strip()
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            logger.warning("%s=%r is not a whole number; the island checks run serially", WORKERS_ENV, env)
            return 1
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    return max(1, min(cpus - 1, MAX_DEFAULT_WORKERS))


def used(n_workers, n_items) -> int:
    """The processes ``ordered_map`` runs ``n_items`` on at ``n_workers``:
    never more than the items, and 1 (the caller's own) when serial."""
    return max(1, min(max(1, int(n_workers)), n_items))


def ordered_map(fn, items, init, initargs=(), *, n_workers=1, should_stop=None) -> list:
    """``[fn(init(*initargs), item) for item in items]``, the context built
    once per process, over ``used(n_workers, len(items))`` processes.
    ``fn`` and ``init`` must be module-level functions, and the arguments
    and results picklable."""
    items = list(items)
    n = used(n_workers, len(items))
    if n <= 1:
        context, out = init(*initargs), []
        for item in items:
            if should_stop is not None and should_stop():
                raise Stopped
            out.append(fn(context, item))
        return out
    pool = ProcessPoolExecutor(n, mp_context=multiprocessing.get_context("spawn"), initializer=_start,
                               initargs=(init, initargs))
    done_ok = False
    try:
        futures = [pool.submit(_call, fn, item) for item in items]
        pending = set(futures)
        while pending:
            if should_stop is not None and should_stop():
                raise Stopped
            finished, pending = wait(pending, timeout=POLL_S, return_when=FIRST_EXCEPTION)
            failed = [i for i, f in enumerate(futures) if f in finished and f.exception() is not None]
            if failed:
                # the serial loop would raise the first failing item: let the
                # items before this one finish, then raise the first failure
                for f in futures[failed[0] + 1:]:
                    f.cancel()
                wait(futures[:failed[0]])
                raise next(f.exception() for f in futures[:failed[0] + 1] if f.exception() is not None)
        out = [f.result() for f in futures]
        done_ok = True
        return out
    finally:
        if not done_ok:
            # cancel what has not started, end what has, then reap them all
            # (Python 3.14 has ProcessPoolExecutor.terminate_workers for this)
            for p in list((getattr(pool, "_processes", None) or {}).values()):
                p.terminate()
        pool.shutdown(wait=True, cancel_futures=True)

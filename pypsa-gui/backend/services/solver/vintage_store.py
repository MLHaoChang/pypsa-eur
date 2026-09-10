"""
The per-thread vintage/myopic freeze store.

A leaf module, and it exists as its own module for one reason: both
`services/solver/assumptions.py` and `services/solver/myopic.py` read and write
this store, so it belongs to neither. Carving it out is what makes the rest of
the decomposition a DAG — with the store inside either of those modules, the
two import each other.

Carved out of `services/solver_service.py`. Imports nothing from it, and
nothing from the rest of the `solver` package.

`services/vintage_service.py` reaches `_frozen_vintage_store` through a
function-body import of `services.solver_service`, with a comment naming the
`solver_service` ↔ `vintage_service` cycle it avoids. That lazy import is
untouched by this move and keeps working through the re-export façade.
"""
import threading


# Per-thread side-store for freeze-time vintage capacities. Populated by
# `_freeze_period_capacities` after each myopic period's LP solves and read
# by `vintage_service._capture_and_drop_vintages` at restore. Lives off
# n.meta because PyPSA's n.optimize() apparently resets n.meta between
# myopic iterations, wiping our writes — observed empirically on multi-port
# heat-pump Links where the live `p_nom_opt` always reads -0.0 post-solve
# regardless of LP outcome.
#
# Thread-local rather than a plain module dict: a process-wide dict WAS safe
# when the solver was single-threaded under one global lock, but B4's
# per-context solver locks let two `run_simulation` calls run concurrently
# (foreground /run + background queue) on different threads. A shared dict
# raced — cross-`.clear()` mid-restore and key collisions on the
# deterministic `{parent}@{year}` vintage names — producing silent wrong
# vintage/myopic capacities. One solve == one worker thread (the myopic loop
# runs its iterations sequentially on that thread), so a thread-local store
# isolates concurrent solves while preserving cross-iteration myopic
# behaviour. The reader runs inside `restore_modelling`, synchronously on
# the same solve thread that wrote the store — see `_frozen_vintage_store`.
_frozen_vintage_local = threading.local()


def _frozen_vintage_store() -> dict:
    """
    Per-thread vintage/myopic freeze store. Module-global WAS process-wide,
    but B4's per-context solver locks allow two concurrent run_simulation calls
    (foreground /run + background queue) on different threads; a shared dict
    raced → silent wrong vintage capacities. One solve == one thread (myopic
    iterations are sequential on that thread), so a thread-local store isolates
    concurrent solves while preserving cross-iteration myopic behaviour.
    """
    s = getattr(_frozen_vintage_local, "store", None)
    if s is None:
        s = {}
        _frozen_vintage_local.store = s
    return s


# Marks a `vintage_results` entry this module wrote, so a later myopic run can
# clear its OWN stale entries without touching the ones `vintage_service`
# writes for real per-period vintages.
_MYOPIC_VINTAGE_SOURCE = "myopic_freeze"


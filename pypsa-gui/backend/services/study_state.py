"""
The long-running-study mutual-exclusion predicate, in ONE place.

Every adequacy study — the class-B/C contingency sweep, the ε-constraint
frontier, the sequential-MC study and the two planning loops (the energy cap's
and the reserve margin's) — reads the foreground network for minutes, and four
of the five RE-SOLVE it. Two of them at once means one engine is sampling a
network the other is mutating, and the numbers that come out are wrong in a
way nothing downstream can detect.

WHY THIS MODULE EXISTS RATHER THAN A FUNCTION IN A ROUTER. The mesh has to be
enforced from BOTH sides of a router boundary: ``routers/results.py`` owns the
studies, ``routers/simulation.py`` owns the foreground solve entrypoints
(``POST /simulation/run``, ``POST /simulation/run_ac_pf``), and a foreground
solve interleaving between a study's iterates is precisely the corruption the
mesh exists to prevent — worst for the coupling loop, whose ``evaluate`` reads
whatever plan the network happens to be holding. But ``results.py`` already
imports ``_state`` from ``simulation.py``, so the guard cannot live in
``results.py`` without simulation importing it back and closing a cycle, and
putting it in ``simulation.py`` would make the solve router the home of the
adequacy mesh. It belongs to neither: the state it reads is the ACTIVE
PROJECT's solver state, which ``PyPSAService`` already serves to both. Reading
it here directly makes the predicate importable from anywhere, cycle-free, and
identical on every side of the mesh — a guard that differs between callers is
not a guard.
"""
from __future__ import annotations

from services.project_context import (
    STUDY_KEYS,
    STUDY_LABELS,
    record_is_running,
)
from services.pypsa_service import PyPSAService

# The keys under the active project's solver state that hold a long-running
# study record, RE-EXPORTED from `project_context` (which owns the list, beside
# RESULT_STATE_KEYS — see the note there). Order is the order a blocked caller
# is told about them, so the cheapest-to-explain blocker comes first.
__all__ = ["STUDY_KEYS", "STUDY_LABELS", "record_is_running", "study_running",
           "running_study", "blocking_study_detail"]

# What each study is called in a 409 message. A user who is told "a study is
# running" cannot act; one who is told WHICH can go and abort it.


def study_running(key: str) -> bool:
    """True while the long-running study stored under ``state[key]`` is live.

    Testing ``thread.is_alive()`` and not just the status string matters: a
    crashed worker that never got to write its terminal status would otherwise
    wedge the surface permanently, and the user's only recovery would be a
    process restart.
    """
    try:
        st = PyPSAService.get_solver_state().get(key)
    except Exception:                                         # noqa: BLE001
        return False
    return record_is_running(st)


def running_study() -> str | None:
    """The key of the first live study, or None. Used by the foreground solve
    entrypoints, which do not care WHICH study is running, only that one is —
    but whose 409 must still name it."""
    for key in STUDY_KEYS:
        if study_running(key):
            return key
    return None


def blocking_study_detail() -> str | None:
    """The 409 detail for a foreground solve blocked by a study, or None.

    Phrased as the study's own sentence so the message reads the same wherever
    the mesh refuses: a solve that interleaves between a study's iterates
    silently corrupts what that study reads, and the user's action is to wait
    or to abort the study by name.
    """
    key = running_study()
    if key is None:
        return None
    return (f"{STUDY_LABELS.get(key, key)} is running and re-reads the "
            "network between its own solves — a foreground solve now would "
            "silently change the plan it is measuring. Wait for it to finish, "
            "or abort it.")

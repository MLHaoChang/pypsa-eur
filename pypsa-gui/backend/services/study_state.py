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
    LIVE_NETWORK_STUDIES,
    STUDY_KEYS,
    STUDY_LABELS,
    record_is_running,
)
from services.pypsa_service import PyPSAService

# The keys under the active project's solver state that hold a long-running
# study record, RE-EXPORTED from `project_context` (which owns the list, beside
# RESULT_STATE_KEYS — see the note there). Order is the order a blocked caller
# is told about them, so the cheapest-to-explain blocker comes first.
__all__ = ["STUDY_KEYS", "STUDY_LABELS", "LIVE_NETWORK_STUDIES",
           "record_is_running", "study_running", "running_study",
           "blocking_study_detail", "study_in_flight_detail",
           "refuse_edit_during_live_study"]

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


def study_in_flight_detail(state, doing: str, *,
                           keys=STUDY_KEYS) -> dict | None:
    """The structured 409 for an action a live study forbids, or None.

    Whole-branch review, findings S5 and M12. Save and activate gated on
    `_solver_in_flight` only — a study's worker is never `state["thread"]` —
    while load, import, template and reset were guarded (Phase 11). A save
    landing between a sweep's lock-free contingency mutations exported the
    CONTINGENCY network, and its `results_state.pkl` with the contingency's
    lost load, as the user's project; and a switch left the study running on
    a project the user could no longer see or abort. Same shape as the
    in-flight refusal so the chat agent and the frontend read one field.

    ``keys`` narrows which studies count (P27a: an edit is refused only by
    ``LIVE_NETWORK_STUDIES``); the order a blocked caller is told about them
    stays STUDY_KEYS' order. Moved here from ``routers/projects.py`` so the
    network-edit handlers can raise the same dict without importing a router.
    """
    if not state:
        return None
    key = None
    for k in STUDY_KEYS:
        if k not in keys:
            continue
        try:
            if record_is_running(state.get(k)):
                key = k
                break
        except Exception:                                     # noqa: BLE001
            continue
    if key is None:
        return None
    label = STUDY_LABELS.get(key, key)
    verb = doing.split()[0]
    return {
        "error_kind": "study_in_flight",
        "study": key,
        "message": (
            f"Cannot {doing} while {label} is running — it re-solves the "
            "in-memory network between its own iterates (a sweep applies each "
            "contingency in turn; a loop re-solves under each candidate), so "
            f"a {verb} now would act on a mid-study plan rather than yours. "
            "Wait for it to finish, or abort it, and retry."
        ),
    }


def refuse_edit_during_live_study() -> None:
    """Raise the `study_in_flight` 409 when a live-network study is running on
    the ACTING context (P27a, A1).

    The chat tools call the network-edit handlers in process, so they never
    meet `main.py`'s middleware; this is their chokepoint. The chat worker
    copies contextvars, so `get_solver_state()` here is the acting project's
    — a chat edit on project X is refused only by X's study.
    """
    from fastapi import HTTPException

    detail = study_in_flight_detail(PyPSAService.get_solver_state(),
                                    "edit the network",
                                    keys=LIVE_NETWORK_STUDIES)
    if detail:
        raise HTTPException(status_code=409, detail=detail)

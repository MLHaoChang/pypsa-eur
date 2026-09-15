"""Energy Hub study HTTP runner — validation, record, worker, publish.

Thin handlers in ``routers/results.py`` keep mesh refusal and the FastAPI
decorator; this module owns the synchronous 422 set through ``publish_study``.
State is injected so this module never imports ``routers.*``.
"""
from __future__ import annotations

import contextvars as _contextvars
import logging
import queue as _queue
import threading as _threading
import time

from fastapi import HTTPException
from pydantic import BaseModel as _BaseModel

from models.energy_hub import (
    DEFAULT_EH_BUDGET_SOLVES,
    MAX_EH_BUDGET_SOLVES,
    default_off_grid_pack,
    default_strong_grid_pack,
    default_weak_flexible_pack,
)
from services.pypsa_service import PyPSAService

logger = logging.getLogger("pypsa_gui.results")

_PACK_FACTORY = {
    "strong_grid": default_strong_grid_pack,
    "weak_flexible": default_weak_flexible_pack,
    "off_grid": default_off_grid_pack,
}


class EhStudyRequest(_BaseModel):
    """Start an EH reference-design study for one archetype pack."""

    archetype: str | None = None
    stages: list[str] | None = None
    budget_solves: int | None = None


def start_eh_study(
    body: EhStudyRequest | None,
    *,
    solver_state: dict,
    state_update,
    publish_study,
):
    """
    Start the Energy Hub reference-design study in a worker thread.

    ASYNCHRONOUS BY CONSTRUCTION: pack apply + ENS solve (+ optional
    redundancy / levers / DtC stages) must never block a request. 409 while
    another study or a foreground solve is running (mesh refuse is the
    caller's job; this publishes under the claim).
    """
    from services.adequacy.eh_study import run_eh_study

    body = body or EhStudyRequest()
    archetype = getattr(body, "archetype", None)
    if archetype is None:
        raise HTTPException(
            422,
            "archetype is required: one of strong_grid, weak_flexible, off_grid")
    if archetype not in _PACK_FACTORY:
        raise HTTPException(
            422,
            f"unknown archetype {archetype!r}; expected one of "
            f"{', '.join(_PACK_FACTORY)}")

    budget = body.budget_solves
    if budget is None:
        budget = DEFAULT_EH_BUDGET_SOLVES
    try:
        budget = int(budget)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "budget_solves must be an integer") from exc
    if not (1 <= budget <= MAX_EH_BUDGET_SOLVES):
        raise HTTPException(
            422,
            f"budget_solves must be between 1 and {MAX_EH_BUDGET_SOLVES}; "
            f"got {budget}")

    stages = body.stages
    if stages is not None:
        stages = [str(s) for s in stages]
        if not stages:
            raise HTTPException(422, "stages, when set, must be a non-empty list")

    cfg = solver_state.get("solver_config")
    if cfg is None:
        raise HTTPException(422, "solver settings are not configured")

    n = PyPSAService.get_network()
    if n is None:
        raise HTTPException(422, "no network is loaded")
    lock = PyPSAService.get_lock()

    pack = _PACK_FACTORY[archetype]()
    stop_event = _threading.Event()
    log_queue: _queue.SimpleQueue = _queue.SimpleQueue()
    record: dict = {
        "status": "running",
        "study": "eh_study",
        "archetype": archetype,
        "stages": list(stages) if stages is not None else None,
        "budget_solves": budget,
        "report": None,
        "error": None,
        "started_at": time.time(),
        "finished_at": None,
        "thread": None,
        "stop_event": stop_event,
    }

    def worker():
        try:
            report = run_eh_study(
                n,
                pack,
                cfg,
                lock=lock,
                stop_event=stop_event,
                log_queue=log_queue,
                stages=stages,
                budget_solves=budget,
                state_update=state_update,
                store=solver_state,
            )
            aborted = bool(getattr(getattr(report, "pipeline", None),
                                   "aborted", False))
            payload = (report.model_dump(mode="json")
                       if hasattr(report, "model_dump") else report)
            with PyPSAService.get_solver_state_lock():
                record.update(
                    status="aborted" if aborted or stop_event.is_set() else "done",
                    report=payload,
                    error=None,
                    finished_at=time.time(),
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("eh_study worker failed")
            with PyPSAService.get_solver_state_lock():
                record.update(
                    status="failed",
                    report=None,
                    error=str(exc),
                    finished_at=time.time(),
                )

    _ctx = _contextvars.copy_context()
    t = _threading.Thread(
        target=lambda: _ctx.run(worker), daemon=True, name="adequacy-eh-study")
    record["thread"] = t
    publish_study("eh_study", record, t)
    return {
        "status": "running",
        "study": "eh_study",
        "archetype": archetype,
        "budget_solves": budget,
    }

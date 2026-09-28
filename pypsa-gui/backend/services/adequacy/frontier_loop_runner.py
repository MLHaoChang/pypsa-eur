"""ε-constraint frontier study HTTP runner — validation, record, worker, publish.

Lifted from ``routers/results.py`` (frontier/sweep study router lift). The
route handler keeps mesh refusal and the FastAPI decorator; this module owns
the synchronous 422 set through ``publish_study``. State is injected so this
module never imports ``routers.*``.
"""
from __future__ import annotations

import contextvars as _contextvars
import logging
import math
import threading as _threading
import time

from fastapi import HTTPException
from pydantic import BaseModel as _BaseModel

from models.schemas import Finite as _Finite
from services.pypsa_service import PyPSAService

logger = logging.getLogger("pypsa_gui.results")


class FrontierRequest(_BaseModel):
    # Reliability targets (‱) to sweep. Omitted → the default spread across
    # the decade where the cost gradient is steep enough to show a knee.
    targets_permyriad: list[_Finite] | None = None


def start_frontier(
    body: FrontierRequest | None,
    *,
    solver_state: dict,
    state_update,
    publish_study,
):
    """
    Start the ε-constraint frontier study in a worker thread: one full
    capacity-expansion solve per target, so it must never block a request.
    409 while a study, a sweep or a foreground solve is running.

    Unlike the class-B/C sweep this does NOT freeze capacities — the study
    asks what plan you would BUILD for each standard, so expansion has to
    re-optimise at every point.
    """
    from services.adequacy.frontier import (
        DEFAULT_TARGETS_PERMYRIAD,
        FrontierBudgetError,
        FrontierConfigError,
        knee_index,
        run_frontier_sweep,
    )

    cfg = solver_state.get("solver_config")
    if cfg is None or float(getattr(cfg, "voll", 0.0) or 0.0) <= 0:
        raise HTTPException(422, "the frontier requires a VOLL > 0 in solver settings")

    targets = list(getattr(body, "targets_permyriad", None)
                   or DEFAULT_TARGETS_PERMYRIAD)
    # An ENS cap is a positive ceiling on unserved energy: a zero or negative
    # target is not a point on the frontier (0 is "no shedding at all", which
    # the LP cannot reach on any network that ever sheds, and a negative cap
    # is infeasible by construction). Refused here, before the record is
    # published, rather than discovered one infeasible solve later.
    bad = [t for t in targets if not (math.isfinite(float(t)) and float(t) > 0)]
    if bad:
        raise HTTPException(
            422, f"targets_permyriad must be positive finite numbers; got "
                 f"{bad[:5]}{' …' if len(bad) > 5 else ''}")
    n = PyPSAService.get_network()
    lock = PyPSAService.get_lock()

    stop_event = _threading.Event()
    # IEEE 39-bus review, F3: the standing reserve margin travels with the
    # record. The frontier deliberately does NOT strip it (unlike the
    # contingency sweep) — a margin is a standing standard, not a swept one —
    # and the phase-8 plan says that is right "but must be stated on the
    # panel, or the curve reads as cost-vs-eps when it is
    # cost-vs-eps-at-margin-m". Measured on the IEEE 39-bus network: with the
    # margin already covering every swept target, all three points came back
    # with identical cost and zero ENS and nothing on the panel said why.
    record: dict = {"status": "running", "points": [], "error": None,
                    "warning": None, "knee": None,
                    "reserve_margin": (
                        float(getattr(cfg, "reserve_margin", None))
                        if getattr(cfg, "reserve_margin", None) is not None
                        else None),
                    "targets_permyriad": targets, "base_restored": None,
                    "base_restore_status": None,
                    "started_at": time.time(), "thread": None,
                    "stop_event": stop_event}

    def worker():
        try:
            res = run_frontier_sweep(n, lock, cfg, targets, stop_event=stop_event,
                                     final_state_update=state_update)
            voll = float(getattr(cfg, "voll", 0.0) or 0.0)
            record.update(
                status="aborted" if res.get("aborted") else "done",
                points=res["points"], warning=res["warning"],
                knee=knee_index(res["points"], voll), voll_eur_per_mwh=voll,
                # Phase 12e: the engine has always computed this and the route
                # threw it away. It says whether the closing re-solve RAN —
                # not that the plan is back — and a study that could not
                # restore the user's plan must say so.
                base_restored=res.get("base_restored"),
                base_restore_status=res.get("base_restore_status"),
                finished_at=time.time(), error=None)
        except (FrontierBudgetError, FrontierConfigError) as exc:
            record.update(status="failed", points=[], error=str(exc),
                          finished_at=time.time())
        except Exception as exc:                              # noqa: BLE001
            # The engine attaches its partial record to the exception so the
            # completed points and the restore's outcome are not lost with it.
            partial = getattr(exc, "frontier_result", None) or {}
            record.update(status="failed", points=partial.get("points") or [],
                          base_restored=partial.get("base_restored"),
                          base_restore_status=partial.get("base_restore_status"),
                          error=str(exc), finished_at=time.time())

    _ctx = _contextvars.copy_context()
    t = _threading.Thread(target=lambda: _ctx.run(worker), daemon=True,
                          name="adequacy-frontier")
    record["thread"] = t
    publish_study("frontier", record, t)
    return {"status": "running", "targets_permyriad": targets}

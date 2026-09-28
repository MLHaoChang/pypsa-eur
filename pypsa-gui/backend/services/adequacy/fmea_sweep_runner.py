"""FMEA class-B/C sweep HTTP runner — validation, record, worker, publish.

Lifted from ``routers/results.py`` (frontier/sweep study router lift). The
route handler keeps mesh refusal and the FastAPI decorator; this module owns
the VoLL gate through ``publish_study``. State is injected so this module
never imports ``routers.*``.
"""
from __future__ import annotations

import contextvars as _contextvars
import logging
import threading as _threading
import time

from fastapi import HTTPException
from pydantic import BaseModel as _BaseModel

from services.pypsa_service import PyPSAService

logger = logging.getLogger("pypsa_gui.results")


class FmeaSweepRequest(_BaseModel):
    # Class-C scenarios, passed by the client from the authorized registry
    # GET (/api/projects/{name}/stress_scenarios) — this route operates on
    # the FOREGROUND network and carries no project name, so the sidecar is
    # read where authorization lives and re-validated here before running.
    scenarios: list = []


def start_fmea_sweep(
    body: FmeaSweepRequest | None,
    *,
    solver_state: dict,
    state_update,
    publish_study,
):
    """
    Start the contingency sweep — class B (link outages) plus any class-C
    scenarios in the body — in a worker thread: a sweep is several LP
    solves and must never block a request. 409 while a sweep or a
    foreground solve is running. The closing base re-solve leaves the
    network AND the foreground results in base state (it writes through
    the real state sink).
    """
    from services.adequacy.stress import (
        StressValidationError,
        run_class_c_sweep,
    )
    from services.adequacy.sweep import SweepBudgetError, run_class_b_sweep

    cfg = solver_state.get("solver_config")
    if cfg is None or float(getattr(cfg, "voll", 0.0) or 0.0) <= 0:
        raise HTTPException(422, "the sweep requires a VOLL > 0 in solver settings")
    n = PyPSAService.get_network()
    lock = PyPSAService.get_lock()

    scenarios = list(getattr(body, "scenarios", None) or [])

    stop_event = _threading.Event()
    record: dict = {"status": "running", "rows": [], "error": None,
                    "base_restored": None, "base_restore_status": None,
                    "started_at": time.time(), "thread": None,
                    "stop_event": stop_event}

    def worker():
        try:
            # Class B first with a private final sink; the LAST sweep's
            # closing base re-solve writes the REAL state sink, so
            # /results/lost_load etc. reflect base afterwards.
            rows, restore_b = run_class_b_sweep(
                n, lock, cfg, stop_event=stop_event,
                final_state_update=None if scenarios else state_update,
            )
            restore = restore_b
            # Phase 12e: the worker runs TWO sweeps, so the flag is checked
            # BETWEEN them. Without this, breaking out of class B's
            # contingency loop returns here and class C runs in full — the
            # abort would stop one sweep, not the study. When class C is
            # skipped, class B ran with a private final sink, so the
            # foreground results are the pre-study ones; that is correct and
            # is what the user is looking at.
            if scenarios and not stop_event.is_set():
                rows_c, restore = run_class_c_sweep(
                    n, lock, cfg, scenarios, stop_event=stop_event,
                    final_state_update=state_update,
                )
                rows = rows + rows_c
            record.update(
                status="aborted" if stop_event.is_set() else "done",
                rows=rows, finished_at=time.time(), error=None,
                # Phase 12e (shipped-code review, finding 1): whether the
                # closing base re-solve ran, and what the solver said. A
                # sweep whose restore FAILED leaves the network on the last
                # contingency while the foreground results describe another
                # plan — the user has to be told, and before this the guard
                # swallowed the exception and the record still read `done`.
                base_restored=restore.get("base_restored"),
                base_restore_status=restore.get("base_restore_status"))
        except (SweepBudgetError, StressValidationError) as exc:
            record.update(
                status="failed", rows=[], error=str(exc), finished_at=time.time())
        except Exception as exc:  # noqa: BLE001
            record.update(
                status="failed", rows=[], error=str(exc), finished_at=time.time())

    # The loops' pattern (see post_coupling_loop): the record is CLOSED OVER
    # so a context switch cannot redirect the worker's writes away from the
    # dict the poller reads, the request's context is carried so the closing
    # restore's ``state_update`` lands in the right project, and the record is
    # published and the thread started under ONE lock hold.
    _ctx = _contextvars.copy_context()
    t = _threading.Thread(target=lambda: _ctx.run(worker), daemon=True,
                          name="fmea-sweep")
    record["thread"] = t
    publish_study("fmea_sweep", record, t)
    return {"status": "running"}

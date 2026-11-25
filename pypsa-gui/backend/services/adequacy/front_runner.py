"""The multi-objective front's HTTP runner (DC 01 PR2a): start, record,
worker, and apply-a-point.

Modelled on ``frontier_loop_runner``: the route keeps the mesh refusal and the
FastAPI decorator, this module owns the synchronous refusals, the worker and
the apply. State is injected so this module never imports ``routers.*``.
"""

from __future__ import annotations

import contextvars as _contextvars
import logging
import math
import threading as _threading
import time
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel as _BaseModel
from pydantic import ConfigDict, StrictInt

from models.schemas import Finite as _Finite
from services.pypsa_service import PyPSAService

logger = logging.getLogger("pypsa_gui.results")


class FrontRequest(_BaseModel):
    # No budget field: the route always runs at MAX_FRONT_SOLVES (GK A), and
    # an unknown field is refused rather than ignored.
    model_config = ConfigDict(extra="forbid")

    levels: list[_Finite]
    # t/yr; null is the uncapped column. Omitted → uncapped only.
    co2_caps_t: list[_Finite | None] | None = None
    lever: Literal["ens_cap", "reserve_margin"] | None = None
    mc_draws: StrictInt | None = None


class FrontApplyRequest(_BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: StrictInt
    expected_revision: StrictInt


def _engine_kwargs(body: FrontRequest) -> dict:
    from services.adequacy.front import DEFAULT_MC_DRAWS, MAX_FRONT_SOLVES

    return {
        "levels": list(body.levels),
        "co2_caps_t": list(body.co2_caps_t) if body.co2_caps_t else [None],
        "lever": body.lever,
        "budget_solves": MAX_FRONT_SOLVES,
        "mc_draws": body.mc_draws if body.mc_draws is not None else DEFAULT_MC_DRAWS,
    }


def start_front(body: FrontRequest, *, solver_state: dict, publish_study):
    """Refuse a bad request with 422 before anything is published, then start
    the front in a worker thread (one capacity re-optimisation per point)."""
    from services import dirty_state
    from services.adequacy.front import (
        FrontBudgetError,
        FrontConfigError,
        check_front_request,
        run_front_study,
    )

    cfg = solver_state.get("solver_config")
    if cfg is None:
        raise HTTPException(422, "no solver configuration is set")
    kw = _engine_kwargs(body)
    n = PyPSAService.get_network()
    lock = PyPSAService.get_lock()
    try:
        plan = check_front_request(n, lock, cfg, **kw)
    except (FrontConfigError, FrontBudgetError) as exc:
        raise HTTPException(422, str(exc)) from None

    stop_event = _threading.Event()
    record: dict = {
        "status": "running",
        "result": None,
        "error": None,
        "plan": plan,
        "started_at": time.time(),
        # The edit counter when the study STARTED; `edited_since` and the
        # apply compare against it.
        "network_revision": None,
        "thread": None,
        "stop_event": stop_event,
    }
    study_ctx = PyPSAService.get_active_context()

    def worker():
        try:
            dirty_state.capture(record, study_ctx, lock=lock)
            res = run_front_study(n, lock, cfg, stop_event=stop_event, **kw)
            record.update(
                status="aborted" if res.get("aborted") else "done",
                result=res,
                error=None,
                finished_at=time.time(),
            )
        except Exception as exc:  # noqa: BLE001  the record says why; the lock is released by `with`
            logger.exception("front study failed")
            record.update(
                status="failed",
                result=None,
                error=str(exc) or type(exc).__name__,
                finished_at=time.time(),
            )

    _ctx = _contextvars.copy_context()
    t = _threading.Thread(
        target=lambda: _ctx.run(worker), daemon=True, name="adequacy-front"
    )
    record["thread"] = t
    publish_study("front", record, t)
    return {"status": "running", "plan": plan}


# --------------------------------------------------------------------------
# Apply a point
# --------------------------------------------------------------------------


def _class_of(table: str) -> str:
    from services.network_bulk import _COMPONENT_ATTRS

    for cls, attr in _COMPONENT_ATTRS.items():
        if attr == table:
            return cls
    raise HTTPException(422, f"capacities of {table} cannot be applied")


def _apply_rows(record: dict | None, index: int, expected_revision: int) -> dict:
    """``{component_class: [{"name", "updates"}]}`` for the point, or the
    refusal, before any write (no partial apply)."""
    import pandas as pd

    from services import dirty_state
    from services.adequacy.sweep import _CAPACITY_ATTRS
    from services.study_state import edited_since

    if not record:
        raise HTTPException(404, "no front study has been run in this session")
    if record.get("status") not in ("done", "aborted") or not record.get("result"):
        raise HTTPException(409, f"the front study is {record.get('status')}")
    if edited_since(record) is not False:
        raise HTTPException(
            409, "the network has changed since the front study started; rerun it"
        )
    current = dirty_state.revision()
    if expected_revision != current:
        raise HTTPException(
            409,
            f"expected network revision {expected_revision}, it is {current}",
        )
    points = record["result"].get("points") or []
    if not 0 <= index < len(points):
        raise HTTPException(422, f"no front point {index}")
    p = points[index]
    if p.get("status") != "ok" or not p.get("point"):
        raise HTTPException(422, f"front point {index} did not solve")
    caps = p["point"].get("capacities") or {}

    n = PyPSAService.get_network()
    if isinstance(n.snapshots, pd.MultiIndex):
        raise HTTPException(
            422,
            "a front point holds one size per asset; applying it to a "
            "multi-period network is not defined",
        )
    want: dict[str, tuple[str, str]] = {}
    for table, nom in _CAPACITY_ATTRS:
        df = getattr(n, table)
        ext = f"{nom}_extendable"
        if not df.empty and ext in df.columns:
            for name in df.index[df[ext].astype(bool)]:
                want[f"{table}:{name}"] = (table, nom)
    unknown = sorted(set(caps) - set(want))
    missing = sorted(set(want) - set(caps))
    if unknown or missing:
        raise HTTPException(
            422,
            "the point does not match the network's extendable assets "
            f"(not extendable here: {unknown[:5]}; not in the point: {missing[:5]})",
        )
    rows: dict[str, list] = {}
    for key, size in caps.items():
        table, nom = want[key]
        if (
            isinstance(size, bool)
            or not isinstance(size, (int, float))
            or not math.isfinite(size)
            or size < 0
        ):
            raise HTTPException(
                422, f"{key} has no finite, non-negative solved size ({size})"
            )
        rows.setdefault(_class_of(table), []).append(
            {
                "name": key.split(":", 1)[1],
                "updates": {nom: float(size), f"{nom}_extendable": False},
            }
        )
    return rows


def _snapshot_rows(n, rows: dict) -> list:
    """The current nominal and extendable values of every row the apply
    writes, to put back if a later class's bulk edit refuses."""
    from services.network_bulk import _COMPONENT_ATTRS

    saved = []
    for cls, cls_rows in rows.items():
        df = getattr(n, _COMPONENT_ATTRS[cls])
        for r in cls_rows:
            for col in r["updates"]:
                saved.append((df, r["name"], col, df.at[r["name"], col]))
    return saved


def _undo_top():
    from services import undo_service

    top = undo_service.peek_entry()
    return (undo_service.depth(), top[0] if top else None)


def apply_front_point(
    record: dict | None,
    index: int,
    expected_revision: int,
    *,
    refuse_busy=None,
    state_update=None,
) -> dict:
    """Fix every extendable asset at the point's size (nominal = size,
    extendable off), through the bulk edit and its validators, as ONE undo
    step and ONE revision bump. All or nothing: every check runs under the
    mutation lock before the first write (``refuse_busy``, the study mesh,
    under the solver-state lock as `_publish_study` takes it); an undo step
    that cannot be recorded refuses; a bulk edit that refuses after an earlier
    class was written puts that class's values back. Like any edit outside
    /api/network/ (`routers/flow.py`'s energization edit), it marks the
    project dirty, bumps the inputs and clears the dispatch the old sizes
    produced; ``state_update`` then resets the solve status."""
    from services import dirty_state
    from services.dispatch_status import clear_dispatch
    from services.network_bulk import apply_bulk_update
    from services.network_undo import push_undo_snapshot

    lock = PyPSAService.get_lock()
    with lock:
        if refuse_busy is not None:
            with PyPSAService.get_solver_state_lock():
                refuse_busy()
        rows = _apply_rows(record, index, expected_revision)
        n = PyPSAService.get_network()
        saved = _snapshot_rows(n, rows)
        before = _undo_top()
        push_undo_snapshot()
        if _undo_top() == before:
            raise HTTPException(
                409, "the undo step could not be recorded; nothing was applied"
            )
        try:
            for cls, cls_rows in rows.items():
                apply_bulk_update({"component_class": cls, "rows": cls_rows})
            rev = dirty_state.bump_revision()
            dirty_state.mark_dirty()
            cleared = clear_dispatch(n)
        except BaseException:
            from services import undo_service

            for df, name, col, value in saved:
                df.at[name, col] = value
            # The step this apply pushed would undo to the state it is back
            # at already; drop it so the user's next undo is their own edit.
            undo_service.pop_entry()
            raise
        finally:
            dirty_state.bump_inputs()
        # The chosen point, read by both results surfaces (Guided §10 and
        # Expert): which design was put on the network, at which revision.
        record["applied"] = {"index": index, "network_revision": rev, "at": time.time()}
    if cleared and state_update is not None:
        state_update(status="idle", condition=None, objective=None, solve_time=None)
    return {
        "applied": index,
        "fixed": sum(len(r) for r in rows.values()),
        "network_revision": rev,
    }

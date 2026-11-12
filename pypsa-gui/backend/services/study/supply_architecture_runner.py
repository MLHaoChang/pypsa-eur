"""
The supply-architecture comparison of a decision study (homer-parity 03a):
one throw-away fork per supply architecture of the study's base project,
solved through the queue, and the best system of each tabulated
(`supply_architectures.compare`).

Built like the tornado (`tornado_runner`), and on its pieces:

* **Source (gate G1).** The base project's SAVED network and solver config,
  pinned by `workflow_tools._network_input_fingerprint`; a base with unsaved
  edits is refused (409 ``baseline_not_saved``). A record whose base has
  changed since reads ``stale``.
* **Forks (gate G7).** `tornado_runner.ForkSolver`: one study-owned throw-away
  fork per architecture, charged one solve on the campaign, deleted in its
  ``finally`` (success, failure, exception and abort alike); forks a delete
  refused are listed in ``forks_left``.
* **One at a time (gate G8).** The record takes the study slot
  (`runner.STUDY_KEY`) on the base context, as the run and the tornado do, so
  `runner._refuse_busy` refuses a run, a tornado or a second comparison while
  it runs (and they refuse it); the study delete waits for it
  (`routers.studies._refuse_if_running`).
* **Statuses (gate G6).** A solve the queue ends ``infeasible`` is an
  ``infeasible`` row; any other failure is ``failed``; a solve not reached
  (stop, budget, deadline) is ``not_reached``.
* **Versioned record (gate G9).** The saved record carries
  :data:`SCHEMA_VERSION`; another version reads ``not_evaluated``.
"""

from __future__ import annotations

import contextvars
import logging
import threading
import time
from typing import Any

from services import dirty_state
from services.pypsa_service import PyPSAService
from services.study import findings as F
from services.study import runner as R
from services.study import store
from services.study import supply_architectures as SA
from services.study.tornado_runner import ForkSolver

logger = logging.getLogger(__name__)

KIND = "supply_architectures"
AUX = "supply_architectures"
SCHEMA_VERSION = 1


def _saved_base(base_row):
    """The base project's saved network, solver config and input fingerprint."""
    from services.workflow_tools import _load_context, _network_input_fingerprint

    saved = _load_context(base_row)
    return (
        saved.network,
        saved.solver_state["solver_config"],
        _network_input_fingerprint(saved),
    )


def _refuse_unsaved(resident) -> None:
    if resident is not None and dirty_state.is_dirty(resident):
        raise R.RunRefused(
            409,
            "baseline_not_saved",
            (
                "the study's base project has unsaved edits; save it so the "
                "comparison solves what is on disk"
            ),
        )


def start(
    study_id: str,
    *,
    base_row,
    db,
    user_id,
    architecture_ids: list[str] | None = None,
    budget_solves: int | None = None,
) -> dict:
    """
    Validate, check the budget, claim the study slot on the base context and
    start the worker. Raises ``runner.RunRefused`` before anything starts.
    """
    from services import project_registry
    from services.adequacy import campaign

    base_dir = project_registry.project_dir(base_row)
    try:
        study = store.load_study(base_dir, study_id)
    except store.StudyNotFound:
        raise R.RunRefused(404, "study_not_found", "Study not found") from None
    key = project_registry.registry_key(base_row)
    resident = PyPSAService.get_context(key)
    if resident is not None:
        R._refuse_busy(resident)
    _refuse_unsaved(resident)
    network, cfg, fingerprint = _saved_base(base_row)
    try:
        archs = SA.select(network, architecture_ids)
    except SA.ArchitectureUnsolved as exc:
        raise R.RunRefused(
            422 if exc.code == "unknown_architecture" else 409, exc.code, str(exc)
        ) from None
    solves = len(archs)
    budget = solves if budget_solves is None else int(budget_solves)
    if not 1 <= budget <= campaign.MAX_BUDGET_SOLVES:
        raise R.RunRefused(
            422,
            "campaign_budget_invalid",
            f"budget_solves must be 1..{campaign.MAX_BUDGET_SOLVES}",
        )

    registered = resident is None
    if registered:
        PyPSAService.mark_study_owned(key)
    try:
        ctx = R.base_context(base_row)
    except BaseException:
        if registered:
            PyPSAService.unmark_study_owned(key)
        raise
    stop_event = threading.Event()
    record: dict[str, Any] = {
        "status": "running",
        "kind": KIND,
        "study": R.STUDY_KEY,
        "study_id": study_id,
        "current": None,
        "architectures": [a.architecture_id for a in archs],
        "solves_estimated": solves,
        "solves_charged": 0,
        "variants": [],
        "campaign": None,
        "own_campaign": False,
        "registered_base": registered,
        "error": None,
        "started_at": time.time(),
        "finished_at": None,
        "thread": None,
        "stop_event": stop_event,
        "network_revision": None,
        "base_project_id": str(base_row.id),
        "base_fingerprint": fingerprint,
    }
    dirty_state.capture(record, ctx)
    run_ctx = contextvars.copy_context()
    thread = threading.Thread(
        target=lambda: run_ctx.run(
            _worker,
            study_id=study_id,
            base_dir=base_dir,
            base_row_id=str(base_row.id),
            user_id=user_id,
            network=network,
            cfg=cfg,
            archs=archs,
            record=record,
            stop_event=stop_event,
            ctx=ctx,
        ),
        daemon=True,
        name=f"supply-architectures-{study_id[:8]}",
    )
    record["thread"] = thread

    lock = ctx.mutation_lock
    if not lock.acquire(timeout=5.0):
        R._release_base(key, registered)
        raise R.RunRefused(
            409,
            "solver_in_flight",
            "a solve holds the study's base project; wait for it to finish",
        )
    try:
        with ctx.solver_state_lock:
            try:
                R._refuse_busy(ctx)
                # Gate G1: the saved base pinned above is still the one solved.
                _refuse_unsaved(ctx)
            except R.RunRefused:
                R._release_base(key, registered)
                raise
            own = not R._on_ctx(ctx, campaign.status)["active"]
            if own:
                try:
                    R._on_ctx(
                        ctx,
                        campaign.start,
                        f'decision study "{study.name}": supply architectures',
                        budget,
                    )
                except campaign.CampaignError as exc:
                    R._release_base(key, registered)
                    raise R.RunRefused(
                        422, "campaign_budget_invalid", str(exc)
                    ) from None
            try:
                R._on_ctx(ctx, campaign.check, R.STUDY_KEY, solves)
            except campaign.CampaignBudgetError as exc:
                if own:
                    R._on_ctx(ctx, campaign.end, "refused before the first solve")
                R._release_base(key, registered)
                raise R.RunRefused(409, "campaign_budget_exhausted", str(exc)) from None
            record["own_campaign"] = own
            ctx.solver_state[R.STUDY_KEY] = record
            try:
                thread.start()
            except BaseException:
                ctx.solver_state[R.STUDY_KEY] = None
                if own:
                    R._on_ctx(ctx, campaign.end, "the worker did not start")
                R._release_base(key, registered)
                raise
    finally:
        lock.release()
    return {
        "status": "running",
        "kind": KIND,
        "study_id": study_id,
        "solves_estimated": solves,
        "architectures": record["architectures"],
    }


def _solve_fn(solver: ForkSolver, cfg):
    """``compare``'s solve: a fork solve, its failure typed (gate G6)."""

    def solve(net, arch):
        try:
            return solver(net, cfg, arch.architecture_id)
        except F.VariantFailed as exc:
            condition = str(getattr(exc, "condition", "") or "").lower()
            code = "infeasible" if "infeasible" in condition else "failed"
            raise SA.ArchitectureUnsolved(code, str(exc)) from None

    return solve


def _worker(
    *,
    study_id,
    base_dir,
    base_row_id,
    user_id,
    network,
    cfg,
    archs,
    record,
    stop_event,
    ctx,
) -> None:
    from db.models import Project
    from db.session import SessionLocal
    from services.adequacy import campaign

    PyPSAService.bind_request_context(ctx)
    db = SessionLocal()
    solver = None
    table = None
    status, error = "failed", None
    try:
        base_row = db.get(Project, R._uuid(base_row_id))
        solver = ForkSolver(db, user_id, base_row, study_id, stop_event, record, ctx)
        table = SA.compare(
            network,
            _solve_fn(solver, cfg),
            archs=archs,
            progress=lambda **kw: R._set(ctx, record, **kw),
        )
        status = (
            "aborted"
            if stop_event.is_set()
            or record.get("budget_exhausted")
            or record.get("deadline_exceeded")
            else "done"
        )
        if record.get("deadline_exceeded"):
            error = record["deadline_exceeded"]
    except Exception as exc:  # noqa: BLE001 — recorded
        logger.exception("decision study %s supply architectures failed", study_id)
        error = f"{type(exc).__name__}: {exc}"
        status = "failed"
    finally:
        left = [r.name for r in solver.left] if solver is not None else []
        if record.get("own_campaign"):
            try:
                R._set(
                    ctx,
                    record,
                    campaign=campaign.end(
                        f"decision study supply architectures {status}"
                    ),
                )
            except campaign.CampaignError:
                pass
        try:
            _save(base_dir, study_id, record, table, status, error, left)
        except Exception:  # noqa: BLE001
            logger.exception(
                "decision study %s: saving the supply architectures failed", study_id
            )
            error = error or "saving the supply architectures failed"
            status = "failed"
        db.close()
        if record.get("registered_base"):
            R._release_base(ctx.registry_key, True)
        R._set(
            ctx,
            record,
            status=status,
            error=error,
            finished_at=time.time(),
            current=None,
            forks_left=left,
        )


def _save(base_dir, study_id, record, table, status, error, left) -> None:
    data: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "kind": KIND,
        "error": error,
        "started_at": record.get("started_at"),
        "finished_at": time.time(),
        "base_project_id": record.get("base_project_id"),
        "base_fingerprint": record.get("base_fingerprint"),
        "architectures": record.get("architectures"),
        "solves_estimated": record.get("solves_estimated"),
        "solves_charged": record.get("solves_charged"),
        "campaign": record.get("campaign"),
        "variants": record.get("variants"),
        "forks_left": left,
        "budget_exhausted": bool(record.get("budget_exhausted")),
        "rows": None,
        "best_architecture_id": None,
        "renewable_fraction_basis": None,
    }
    if table is not None:
        data.update(table)
    with store.WRITE_LOCK:
        store.save_aux(base_dir, study_id, AUX, data)


def _stale(saved: dict, base_row) -> bool:
    """Gate G1: the base project's saved inputs changed since the comparison."""
    if saved.get("base_project_id") != str(base_row.id):
        return True
    try:
        _n, _cfg, now = _saved_base(base_row)
    except Exception:  # noqa: BLE001 — an unreadable base is not the one compared
        return True
    return now != saved.get("base_fingerprint")


def get(study_id: str, *, base_row, base_dir) -> dict | None:
    """The live comparison on the base context, else the last one on disk
    (with ``stale``), else None."""
    from services import project_registry

    ctx = PyPSAService.get_context(project_registry.registry_key(base_row))
    if ctx is not None:
        with ctx.solver_state_lock:
            rec = ctx.solver_state.get(R.STUDY_KEY)
            if (
                rec
                and rec.get("kind") == KIND
                and rec.get("study_id") == study_id
                and rec.get("status") == "running"
            ):
                return R._public(rec)
    saved = store.load_aux(base_dir, study_id, AUX)
    if saved is None:
        return None
    if saved.get("schema_version") != SCHEMA_VERSION:
        return {
            "status": "not_evaluated",
            "kind": KIND,
            "reason": "unknown_schema_version",
            "schema_version": saved.get("schema_version"),
        }
    return {**saved, "stale": _stale(saved, base_row)}


def abort(study_id: str, *, base_row, base_dir) -> dict:
    """Set the comparison's stop event. Idempotent; 404 when it never ran."""
    from services import project_registry

    ctx = PyPSAService.get_context(project_registry.registry_key(base_row))
    if ctx is not None:
        with ctx.solver_state_lock:
            rec = ctx.solver_state.get(R.STUDY_KEY)
            if rec and rec.get("kind") == KIND and rec.get("study_id") == study_id:
                ev, status = rec.get("stop_event"), rec.get("status")
                if ev is not None:
                    ev.set()
                return {"status": status, "aborting": status == "running"}
    last = store.load_aux(base_dir, study_id, AUX)
    if last is None:
        raise R.RunRefused(
            404,
            "supply_architectures_never_run",
            "the supply-architecture comparison has not been run",
        )
    return {"status": last.get("status"), "aborting": False}

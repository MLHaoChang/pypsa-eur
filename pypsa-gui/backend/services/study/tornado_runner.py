"""
The fixed-size tornado's worker (guided investment study MVP-1, S6; plan S4
M2 "tornado").

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S6,
"Amended at S6 start"); gates S1 (a network-touching study handler carries
its own context-parameterised in-flight check), S4 (throw-away forks reuse the
study-owned mark and release, tested at the resident cap) and S5 ([B1]: forks
are read from disk, a changed one refuses).

:func:`start_tornado` runs ``findings.run_tornado`` in a worker thread, on
the study's BASE context, with the S4 runner's machinery reused unchanged:

* **The claim** is the runner's: every refusal before anything registers
  (``runner._refuse_busy`` on a resident base — the study mesh and
  ``_solver_in_flight_ctx(base_ctx)``), then the base context
  (``runner.base_context``, study-owned while the tornado registered it), the
  same checks again under ``base_ctx.mutation_lock`` then
  ``base_ctx.solver_state_lock``, and the record published in
  ``solver_state["decision_study"]`` (``kind="tornado"``), so the mesh sees a
  running tornado as a running decision study. ``runner._release_base`` on
  every path.
* **The budget** is checked for the worst case
  (``findings.estimate_tornado_solves``) before the first solve, on the base
  context's campaign (its own, or an agent's that is open), and each solve is
  then charged as it is made (``campaign.check`` then ``campaign.record``, one
  solve), so a battery-only best is charged its re-dispatches only.
* **Each solve is a throw-away fork** (:class:`ForkSolver`):
  ``forks.create_variant_fork`` (``<base>-var-<id>``, study-owned by metadata
  AND database parent), marked study-owned BEFORE the queue registers it (so
  the registration never evicts a user's context), enqueued with its own
  explicit ``SolverConfig`` like an option fork, read back from its file
  once solved, dropped and unmarked, and deleted — on success, abort and
  failure alike. Option forks are never re-solved or written.
* **Abort** stops before the next solve (the running job is aborted in the
  queue); the bars computed so far are kept and the rest are named in
  ``robustness.pending``.

The result is written to ``studies/<id>.tornado.json`` with the ledger and
option-fork hashes it was computed from, so ``findings.assemble_findings``
uses it only while they still hold.
"""
from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from dataclasses import asdict
from typing import Any

from services.pypsa_service import PyPSAService
from services.study import findings as F
from services.study import forks as study_forks
from services.study import packs
from services.study import runner as R
from services.study import store

logger = logging.getLogger(__name__)

__all__ = ["ForkSolver", "abort_tornado", "get_tornado", "start_tornado"]

KIND = "tornado"
_RUNS = ("done", "aborted")


def _refused(exc: F.FindingsRefused) -> R.RunRefused:
    return R.RunRefused(exc.status, exc.code, exc.message)


class ForkSolver:
    """
    ``solve(network, cfg, variant_id)`` for ``findings.run_tornado``: one
    throw-away fork per call, charged one solve on the base context's
    campaign, deleted before it returns. Answers None when stopped or when
    the campaign has no solve left (the tornado then names what it did not
    reach); raises ``findings.VariantFailed`` when a solve fails.
    """

    def __init__(self, db, user_id, base_row, study_id: str,
                 stop_event: threading.Event, record: dict, ctx):
        self.db, self.user_id, self.base_row = db, user_id, base_row
        self.study_id, self.stop_event, self.record, self.ctx = (
            study_id, stop_event, record, ctx)
        self.count = 0
        self.left: list = []          # forks a delete refused (reported)

    def _delete(self, row) -> None:
        try:
            study_forks.delete_fork(self.db, row, study_id=self.study_id,
                                    base_uuid=str(self.base_row.id))
        except study_forks.ForkError as exc:
            logger.warning("tornado: fork %s not deleted (%s)", row.name, exc.code)
            self.left.append(row)

    def __call__(self, net, cfg, variant_id: str):
        from services import project_registry
        from services.adequacy import campaign
        from services.solve_queue import solve_queue
        from services.validation_service import validate_for_run

        if self.stop_event.is_set():
            return None
        try:
            campaign.check(R.STUDY_KEY, 1)
        except campaign.CampaignBudgetError:
            R._set(self.ctx, self.record, budget_exhausted=True)
            return None
        errors = [i for i in validate_for_run(net, cfg) if i.severity == "error"]
        if errors:
            raise F.VariantFailed("preflight_failed", "; ".join(
                f"{i.code} {i.name}" for i in errors[:3]))
        self.count += 1
        slug = f"v{self.count:02d}"
        row = study_forks.create_variant_fork(
            self.db, self.user_id, base_row=self.base_row, study_id=self.study_id,
            variant_id=slug, network=net, solver_config=cfg)
        key = project_registry.registry_key(row)
        # BC-S4-1: exempt from the resident cap BEFORE the queue registers it.
        PyPSAService.mark_study_owned(key)
        try:
            with self.ctx.solver_state_lock:
                self.record["variants"].append({"variant": variant_id, "fork": row.name})
            campaign.record(R.STUDY_KEY, 1)
            with self.ctx.solver_state_lock:
                self.record["solves_charged"] += 1
            job, _new = solve_queue.enqueue_unique(
                row.name, project_key=key,
                storage_dir=str(project_registry.project_dir(row)),
                solver_config_json=json.dumps(asdict(cfg)),
                enqueued_by_user_id=self.user_id)
            R._wait(job, self.stop_event)
            if job.status != "completed":
                if job.status == "aborted" or self.stop_event.is_set():
                    return None
                raise F.VariantFailed("variant_solve_failed", str(job.error or job.status))
            solved, _hash = R.fork_network_from_disk(row)
            return solved
        finally:
            try:
                PyPSAService.drop(key)
            except Exception:  # noqa: BLE001
                pass
            PyPSAService.unmark_study_owned(key)
            self._delete(row)


def start_tornado(study_id: str, *, base_row, db, user_id,
                  budget_solves: int | None = None) -> dict:
    """
    Validate, check the worst-case budget, claim the mesh on the base
    context and start the worker. Raises ``runner.RunRefused`` (with an HTTP
    status) before anything starts.
    """
    from services import project_registry
    from services.adequacy import campaign

    base_uuid = str(base_row.id)
    base_dir = project_registry.project_dir(base_row)
    try:
        study = store.load_study(base_dir, study_id)
    except store.StudyNotFound:
        raise R.RunRefused(404, "study_not_found", "Study not found") from None
    R._runnable(study, base_uuid)
    try:
        inp = F.load_inputs(study, base_dir, db, base_uuid)
        packs.refuse_unrunnable_ledger(inp.ledger)
    except F.FindingsRefused as exc:
        raise _refused(exc) from None
    except packs.PackError as exc:
        raise R.RunRefused(409, exc.code, exc.message) from None
    if inp.changed:
        raise R.RunRefused(409, "fork_changed_since_run", (
            f"option(s) {', '.join(inp.changed)} changed after the run (opened, "
            "edited or re-solved outside the study); re-run the study"))
    if "none" not in inp.rows:
        raise R.RunRefused(409, "baseline_not_solved",
                           "the last run did not solve the baseline; re-run the study")
    solves = F.estimate_tornado_solves(inp.question, inp.ledger, inp.tariff, inp.sizes())
    budget = max(1, solves) if budget_solves is None else int(budget_solves)

    key = project_registry.registry_key(base_row)
    resident = PyPSAService.get_context(key)
    if resident is not None:
        R._refuse_busy(resident)
    open_campaign = resident is not None and R._on_ctx(resident, campaign.status)["active"]
    if open_campaign:
        try:
            R._on_ctx(resident, campaign.check, R.STUDY_KEY, solves)
        except campaign.CampaignBudgetError as exc:
            raise R.RunRefused(409, "campaign_budget_exhausted", str(exc)) from None
    elif solves > budget:
        raise R.RunRefused(409, "campaign_budget_exhausted", _shortfall(solves, budget))
    if not 1 <= budget <= campaign.MAX_BUDGET_SOLVES:
        raise R.RunRefused(422, "campaign_budget_invalid",
                           f"budget_solves must be 1..{campaign.MAX_BUDGET_SOLVES}")

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
        "status": "running", "kind": KIND, "study": R.STUDY_KEY, "study_id": study_id,
        "current": None, "keys": list(inp.question.key_drivers),
        "solves_estimated": solves, "solves_charged": 0, "variants": [],
        "campaign": None, "own_campaign": False, "registered_base": registered,
        "error": None, "started_at": time.time(), "finished_at": None,
        "thread": None, "stop_event": stop_event,
    }
    run_ctx = contextvars.copy_context()
    thread = threading.Thread(
        target=lambda: run_ctx.run(_worker, study_id=study_id, base_dir=base_dir,
                                   base_row_id=base_uuid, user_id=user_id, inp=inp,
                                   record=record, stop_event=stop_event, ctx=ctx),
        daemon=True, name=f"decision-tornado-{study_id[:8]}")
    record["thread"] = thread

    lock = ctx.mutation_lock
    if not lock.acquire(timeout=5.0):
        R._release_base(key, registered)
        raise R.RunRefused(409, "solver_in_flight",
                           "a solve holds the study's base project; wait for it to finish")
    try:
        with ctx.solver_state_lock:
            try:
                R._refuse_busy(ctx)
            except R.RunRefused:
                R._release_base(key, registered)
                raise
            own = not R._on_ctx(ctx, campaign.status)["active"]
            if own:
                try:
                    R._on_ctx(ctx, campaign.start,
                              f"decision study {study.name!r}: tornado", budget)
                except campaign.CampaignError as exc:
                    R._release_base(key, registered)
                    raise R.RunRefused(422, "campaign_budget_invalid", str(exc)) from None
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
    return {"status": "running", "kind": KIND, "study_id": study_id,
            "solves_estimated": solves, "keys": record["keys"]}


def _shortfall(solves: int, budget: int) -> str:
    return (f"the tornado needs up to {solves} solve(s) (two re-dispatches per "
            "price driver, and the PV-only references) and the campaign has "
            f"{budget} of {budget} left. Raise budget_solves")


def _worker(*, study_id, base_dir, base_row_id, user_id, inp, record, stop_event,
            ctx) -> None:
    from db.models import Project
    from db.session import SessionLocal
    from services.adequacy import campaign

    PyPSAService.bind_request_context(ctx)
    db = SessionLocal()
    solver = None
    outcome = None
    status, error = "failed", None
    try:
        base_row = db.get(Project, R._uuid(base_row_id))
        solver = ForkSolver(db, user_id, base_row, study_id, stop_event, record, ctx)
        tctx = F.context_from_disk(inp)
        outcome = F.run_tornado(tctx, solver, stop=stop_event.is_set,
                                progress=lambda **kw: R._set(ctx, record, **kw))
        status = ("aborted" if stop_event.is_set() or record.get("budget_exhausted")
                  else "done")
    except Exception as exc:  # noqa: BLE001 — recorded
        logger.exception("decision study %s tornado failed", study_id)
        error = f"{type(exc).__name__}: {exc}"
        status = "failed"
    finally:
        left = []
        if solver is not None:
            left = [r.name for r in solver.left]
        if record.get("own_campaign"):
            try:
                R._set(ctx, record, campaign=campaign.end(f"decision study tornado {status}"))
            except campaign.CampaignError:
                pass
        try:
            _save(base_dir, study_id, inp, record, outcome, status, error, left)
        except Exception:  # noqa: BLE001
            logger.exception("decision study %s: saving the tornado failed", study_id)
            error = error or "saving the tornado failed"
            status = "failed"
        db.close()
        # Gate S6: release before the terminal status is published (the
        # runner's rule; a poll reading "done" finds nothing exempt).
        if record.get("registered_base"):
            R._release_base(ctx.registry_key, True)
        R._set(ctx, record, status=status, error=error, finished_at=time.time(),
               current=None, forks_left=left)


def _save(base_dir, study_id, inp, record, outcome, status, error, left) -> None:
    hashes = inp.findings.get("hashes") or {}
    data: dict[str, Any] = {
        "status": status, "kind": KIND, "error": error,
        "started_at": record.get("started_at"), "finished_at": time.time(),
        "solves_estimated": record.get("solves_estimated"),
        "solves_charged": record.get("solves_charged"),
        "campaign": record.get("campaign"), "variants": record.get("variants"),
        "forks_left": left, "budget_exhausted": bool(record.get("budget_exhausted")),
        "ledger_hash": hashes.get("ledger_hash"),
        "option_network_hashes": hashes.get("option_network_hashes") or {},
    }
    if outcome is not None:
        rob = outcome.robustness
        if status == "aborted" and rob.status == "ok":
            rob = rob.model_copy(update={"status": "not_established",
                                         "note": "tornado_aborted"})
        if record.get("budget_exhausted") and rob.status == "not_established":
            rob = rob.model_copy(update={"note": "tornado_budget_exhausted"})
        data["robustness"] = rob.model_dump(mode="json")
        data["attributions"] = [a.model_dump(mode="json") for a in outcome.attributions]
        data["reference_bills"] = {k: b.model_dump(mode="json")
                                   for k, b in outcome.reference_bills.items()}
    with store.WRITE_LOCK:
        store.save_aux(base_dir, study_id, F.TORNADO_AUX, data)


def get_tornado(study_id: str, *, base_row, base_dir) -> dict | None:
    """The live tornado record on the base context, else the last one on disk."""
    from services import project_registry

    ctx = PyPSAService.get_context(project_registry.registry_key(base_row))
    if ctx is not None:
        with ctx.solver_state_lock:
            rec = ctx.solver_state.get(R.STUDY_KEY)
            if (rec and rec.get("kind") == KIND and rec.get("study_id") == study_id
                    and rec.get("status") == "running"):
                return R._public(rec)
    return store.load_aux(base_dir, study_id, F.TORNADO_AUX)


def abort_tornado(study_id: str, *, base_row, base_dir) -> dict:
    """Set the tornado's stop event. Idempotent; 404 when it never ran."""
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
    last = store.load_aux(base_dir, study_id, F.TORNADO_AUX)
    if last is None:
        raise R.RunRefused(404, "tornado_never_run", "the tornado has not been run")
    return {"status": last.get("status"), "aborting": False}

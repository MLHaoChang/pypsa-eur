"""
The decision-study option runner (guided investment study MVP-1, S4 M1).

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S4
"Mutation boundary", "Context", "Results", "Abort and delete").

:func:`start_study_run` solves every option of a question-pack study, each on
its own study-owned fork (``services/study/forks.py``), through the solve
queue, and records one :class:`models.study.OptionResult` per option.

**The base context is explicit.** The runner never reaches for the ACTIVE
context: it resolves the study's base project's own context (resident, or
hydrated and registered through ``PyPSAService.hydrate_or_adopt``), checks and
claims the study mesh on THAT context (``running_study_key(base_ctx.
solver_state)`` and ``_solver_in_flight_ctx(base_ctx)``, under
``base_ctx.mutation_lock`` then ``base_ctx.solver_state_lock`` — the shape of
``routers/results.py::_publish_study``), and runs its worker under
``contextvars.copy_context()`` with the base context bound as the context's
request context, so the campaign it charges is the base project's, never the
foreground's.

**Budget** (``services/adequacy/campaign.py``): the run costs one solve per
option (``estimate_solves("decision_study", options=...)``). If a campaign is
already open on the base context the run is checked and recorded against it;
otherwise the run opens its own with ``budget_solves`` (default the study's
``budget.solves_max``) and ends it when the worker finishes. The check comes
before anything starts; a refusal names the shortfall and spends nothing.

**Forks and the queue.** Each option's network is the pack
(``packs.build_site_network``) with an explicit ``SolverConfig``
(``packs.option_solver_config``); the fork is enqueued with its own
``project_key``, ``storage_dir`` and ``enqueued_by_user_id`` and waited on,
one at a time. After an option is read its fork context is dropped from the
resident registry (it is saved on disk by the queue).

**Results** are read from the fork's live frames with the fork's OWN config
(``eh_report._live_result_df``; the objective bridge gets that config, so its
``demand_charge_eur`` is the charge the solve carried), and the bill from the
ledger-applied tariff (``packs.effective_tariff``).

**Abort** stops before the next option (the running job is aborted in the
queue); unsolved forks are removed, ``options_status`` is
``not_established`` and ``pending_options`` names the options never reached.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import pathlib
import threading
import time
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from models.study import (
    AssetSize,
    BaselineResult,
    DecisionStudy,
    DeltaVsBaseline,
    Fidelity,
    Findings,
    FindingsHashes,
    OptionResult,
)
from services.pypsa_service import PyPSAService
from services.study import forks as study_forks
from services.study import library as study_library
from services.study import packs
from services.study import questions as Q
from services.study import store
from services.study import tariff as study_tariff

logger = logging.getLogger(__name__)

__all__ = ["RunRefused", "STUDY_KEY", "abort_study_run", "get_study_run",
           "start_study_run"]

STUDY_KEY = "decision_study"
_POLL_S = 0.05
_TERMINAL = ("completed", "failed", "aborted", "interrupted")
_PUBLIC_DROP = ("thread", "stop_event")


class RunRefused(RuntimeError):
    """The run is refused before anything starts. `status` is the HTTP code."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message

    @property
    def detail(self) -> dict:
        return {"error_kind": self.code, "message": self.message}


# ── context plumbing ──────────────────────────────────────────────────────

def _on_ctx(ctx, fn, *args, **kwargs):
    """Call ``fn`` with ``ctx`` as the active context, without leaking it."""
    def run():
        PyPSAService.bind_request_context(ctx)
        return fn(*args, **kwargs)
    return contextvars.copy_context().run(run)


def base_context(base_row):
    """The base project's own context: resident, or hydrated and registered."""
    from routers.projects import _hydrate_context_from_disk
    from services import project_registry

    key = project_registry.registry_key(base_row)
    with PyPSAService.hydrate_or_adopt(key) as resident:
        if resident is not None:
            return resident
        ctx = PyPSAService.build_context()
        _hydrate_context_from_disk(ctx, project_registry.project_dir(base_row), base_row.name)
        project_registry.bind_context(ctx, base_row)
        PyPSAService.register(key, ctx)
        return ctx


def _public(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in _PUBLIC_DROP}


# ── validation (synchronous, before anything starts) ──────────────────────

def _runnable(study: DecisionStudy, base_uuid: str):
    """Refuse a record that must never run a pack (gate S1 carry)."""
    question = Q.get_question(study.question_id)
    if question is None or study.pack_project is None:
        raise RunRefused(409, "study_not_runnable", (
            "this study record is attached to an existing project and runs no "
            "question pack; create the study from a question template, which "
            "builds its own base project"))
    if study.pack_project != base_uuid:
        raise RunRefused(409, "study_not_runnable", (
            "this study record was copied here from another project (Save-As, "
            "a scenario, a snapshot or a bundle); it runs only in the base "
            "project its question pack created"))
    return question


# ── results ───────────────────────────────────────────────────────────────

def _network_hash(path: pathlib.Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def _solved_network(row):
    """The fork's network after its solve: the resident one, else disk."""
    from services import project_registry

    ctx = PyPSAService.get_context(project_registry.registry_key(row))
    if ctx is not None:
        return ctx.network
    import pypsa

    n = pypsa.Network()
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.import_network_from_netcdf(
            n, project_registry.project_dir(row) / "network.nc")
    return n


def _opt_value(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def _read_option(n, cfg, tariff, opt, fidelity: Fidelity, currency_year) -> dict:
    """One option's outcome, from the fork's live frames and its own config."""
    from services.adequacy.eh_report import _live_result_df

    detail: dict[str, Any] = {"option_id": opt.option_id, "caveats": []}
    p0 = getattr(n.links_t, "p0", None)
    imp = p0[packs.IMPORT_LINK] if p0 is not None and packs.IMPORT_LINK in p0 else None
    exp = p0[packs.EXPORT_LINK] if p0 is not None and packs.EXPORT_LINK in p0 else None
    bill = study_tariff.BillCalculator().bill(imp, exp, tariff, n.snapshot_weightings)
    detail["bill"] = bill.model_dump(mode="json")
    try:
        from services.results.cost_breakdown import compute_cost_breakdown
        from services.results.objective_decomposition import compute_objective_decomposition

        dec = compute_objective_decomposition(n, compute_cost_breakdown(n, cfg), cfg)
        detail["demand_charge_eur"] = dec.get("demand_charge_eur")
        detail["lp_total_eur"] = dec.get("lp_total")
        detail["residual_gap_pct"] = dec.get("residual_gap_pct")
    except Exception as exc:  # noqa: BLE001 — a figure the bridge cannot give is null
        detail["demand_charge_eur"] = None
        detail["bridge_unavailable"] = f"{type(exc).__name__}: {exc}"
    try:
        from services.results.asset_economics import compute_asset_economics

        econ = compute_asset_economics(n, cfg, result_df=_live_result_df) or {}
        rows = [a for a in (econ.get("assets") or [])
                if a.get("name") in (packs.BATTERY_NAME, packs.PV_NAME)]
        detail["asset_economics"] = rows
    except Exception as exc:  # noqa: BLE001
        detail["asset_economics"] = None
        detail["asset_economics_unavailable"] = f"{type(exc).__name__}: {exc}"

    sizes: list[AssetSize] = []
    if packs.BATTERY_NAME in n.storage_units.index:
        su = n.storage_units.loc[packs.BATTERY_NAME]
        p = _opt_value(su.get("p_nom_opt"))
        e = None if p is None else p * float(su["max_hours"])
        flags = {} if p is not None else {"p_nom_opt": "not_solved", "e_nom_opt": "not_solved"}
        sizes.append(AssetSize(asset=packs.BATTERY_NAME, p_nom_opt=p, e_nom_opt=e,
                               unavailable=flags))
        if p is not None and p >= float(su["p_nom_max"]) - 1e-6:
            detail["caveats"].append(f"size_at_upper_bound:{packs.BATTERY_NAME}")
    if packs.PV_NAME in n.generators.index:
        g = n.generators.loc[packs.PV_NAME]
        p = _opt_value(g.get("p_nom_opt"))
        sizes.append(AssetSize(asset=packs.PV_NAME, p_nom_opt=p, e_nom_opt=None,
                               unavailable={"e_nom_opt": "not_applicable",
                                            **({} if p is not None else {"p_nom_opt": "not_solved"})}))
        if p is not None and p >= float(g["p_nom_max"]) - 1e-6:
            detail["caveats"].append(f"size_at_upper_bound:{packs.PV_NAME}")

    objective = _opt_value(getattr(n, "objective", None))
    const = _opt_value(getattr(n, "objective_constant", 0.0)) or 0.0
    system_cost = None if objective is None else objective + const
    annual = bill.annual_bill
    flags = {}
    if system_cost is None:
        flags["system_cost"] = "no_objective"
    if annual is None:
        flags["bill"] = (bill.unavailable.get("annual_bill") or "not_computed")
    result = OptionResult(
        option_id=opt.option_id, label=opt.label, solve_status="ok",
        sizes=sizes, system_cost=system_cost, bill=annual,
        delta_vs_baseline=_delta_not_yet(), fidelity=fidelity,
        currency_year=currency_year, unavailable=flags,
    )
    detail["result"] = result
    return detail


def _delta_not_yet() -> DeltaVsBaseline:
    # NPV, payback and capex against the baseline are the S5 pro forma's;
    # null with the flag, never 0 (ADR-0001).
    reason = "computed_by_the_pro_forma_s5"
    return DeltaVsBaseline(npv=None, payback=None, capex=None, co2=None,
                           unavailable={"npv": reason, "payback": reason,
                                        "capex": reason, "co2": "not_modelled_mvp1"})


def _not_run(opt, status: str, fidelity, currency_year, project_ref=None) -> OptionResult:
    return OptionResult(
        option_id=opt.option_id, label=opt.label, project_ref=project_ref,
        solve_status=status, system_cost=None, bill=None,
        delta_vs_baseline=_delta_not_yet(), fidelity=fidelity,
        currency_year=currency_year,
        unavailable={"system_cost": status, "bill": status})


# ── the run ───────────────────────────────────────────────────────────────

def start_study_run(study_id: str, fidelity: Fidelity | str, *, base_row,
                    user_id, budget_solves: int | None = None) -> dict:
    """
    Validate, check the budget, claim the mesh on the base context and start
    the worker. Raises `RunRefused` (with an HTTP status) before anything
    starts. ``fidelity`` is recorded; both are 8760 h in MVP-1.
    """
    from routers.simulation import _solver_in_flight_ctx
    from services import project_registry
    from services.adequacy import campaign
    from services.project_context import STUDY_LABELS, running_study_key

    fidelity = Fidelity(fidelity)
    base_uuid = str(base_row.id)
    base_dir = project_registry.project_dir(base_row)
    try:
        study = store.load_study(base_dir, study_id)
    except store.StudyNotFound:
        raise RunRefused(404, "study_not_found", "Study not found") from None
    question = _runnable(study, base_uuid)
    library = study_library.load_library()
    ledger = study.ledger or study_library.seed_ledger(question, study.intake, library)
    try:
        packs.refuse_unrunnable_ledger(ledger)
        missing = packs.missing_inputs(study.intake)
        if missing:
            raise packs.PackError("intake_incomplete",
                                  f"mandatory input(s) not answered: {', '.join(missing)}")
        packs.effective_tariff(study.intake, ledger, library)  # stale tariff → refused
    except packs.PackError as exc:
        status = 409 if exc.code in ("ledger_needs_attention", "ledger_tariff_stale") else 422
        raise RunRefused(status, exc.code, exc.message) from None
    options = Q.options_for(question, study.intake)
    solves = campaign.estimate_solves(None, STUDY_KEY, options=[o.option_id for o in options])
    budget = study.budget.solves_max if budget_solves is None else int(budget_solves)

    ctx = base_context(base_row)
    stop_event = threading.Event()
    record: dict[str, Any] = {
        "status": "running", "study": STUDY_KEY, "study_id": study_id,
        "fidelity": fidelity.value, "options": [o.option_id for o in options],
        "solved": [], "current": None, "pending": [o.option_id for o in options],
        "solves_charged": solves, "campaign": None, "own_campaign": False,
        "error": None, "started_at": time.time(), "finished_at": None,
        "thread": None, "stop_event": stop_event,
    }
    worker_args = dict(study_id=study_id, base_row_id=base_uuid,
                       base_dir=base_dir, user_id=user_id, fidelity=fidelity,
                       record=record, stop_event=stop_event, ctx=ctx)
    run_ctx = contextvars.copy_context()
    thread = threading.Thread(
        target=lambda: run_ctx.run(_worker, **worker_args),
        daemon=True, name=f"decision-study-{study_id[:8]}")
    record["thread"] = thread

    lock = ctx.mutation_lock
    if not lock.acquire(timeout=5.0):
        raise RunRefused(409, "solver_in_flight",
                         "a solve holds the study's base project; wait for it to finish")
    try:
        with ctx.solver_state_lock:
            busy = running_study_key(ctx.solver_state)
            if busy is not None:
                raise RunRefused(409, "study_running", (
                    f"{STUDY_LABELS.get(busy, busy)} is running on this study's "
                    "base project — wait for it to finish, or abort it"))
            if _solver_in_flight_ctx(ctx):
                raise RunRefused(409, "solver_in_flight",
                                 "a solve is running on this study's base project")
            # Check-then-record on the BASE context's campaign.
            own = not _on_ctx(ctx, campaign.status)["active"]
            if own:
                try:
                    _on_ctx(ctx, campaign.start,
                            f"decision study {study.name!r}: {len(options)} option(s)",
                            budget)
                except campaign.CampaignError as exc:
                    raise RunRefused(422, "campaign_budget_invalid", str(exc)) from None
            try:
                _on_ctx(ctx, campaign.check, STUDY_KEY, solves)
            except campaign.CampaignBudgetError as exc:
                if own:
                    _on_ctx(ctx, campaign.end, "refused before the first solve")
                raise RunRefused(409, "campaign_budget_exhausted", str(exc)) from None
            record["own_campaign"] = own
            ctx.solver_state[STUDY_KEY] = record
            try:
                thread.start()
            except BaseException:
                ctx.solver_state[STUDY_KEY] = None
                if own:
                    _on_ctx(ctx, campaign.end, "the worker did not start")
                raise
            record["campaign"] = _on_ctx(ctx, campaign.record, STUDY_KEY, solves)
    finally:
        lock.release()
    return {"status": "running", "study_id": study_id, "fidelity": fidelity.value,
            "options": record["options"], "solves_charged": solves,
            "campaign": record["campaign"]}


def _set(ctx, record: dict, **kw) -> None:
    with ctx.solver_state_lock:
        record.update(kw)


def _wait(job, stop_event: threading.Event) -> None:
    from services.solve_queue import solve_queue

    aborted = False
    while job.status not in _TERMINAL:
        if stop_event.is_set() and not aborted:
            solve_queue.abort(job.id)
            aborted = True
        time.sleep(_POLL_S)


def _worker(*, study_id, base_row_id, base_dir, user_id, fidelity, record,
            stop_event, ctx) -> None:
    """The run itself, on the base context (bound for this thread only)."""
    from db.models import Project
    from db.session import SessionLocal
    from services import project_registry
    from services.adequacy import campaign
    from services.solve_queue import solve_queue
    from services.validation_service import validate_for_run

    PyPSAService.bind_request_context(ctx)
    db = SessionLocal()
    created: list[tuple[Any, Any, Any]] = []   # (opt, fork row, cfg)
    outcomes: dict[str, dict] = {}
    status, error = "failed", None
    study = None
    try:
        base_row = db.get(Project, _uuid(base_row_id))
        study = store.load_study(base_dir, study_id)
        question = Q.get_question(study.question_id)
        library = study_library.load_library()
        ledger = study.ledger or study_library.seed_ledger(question, study.intake, library)
        tariff = packs.effective_tariff(study.intake, ledger, library)
        options = Q.options_for(question, study.intake)
        currency_year = tariff.currency_year

        def resolve_upload(file_id: str) -> bytes:
            from services import upload_service
            return upload_service.get_upload_bytes(base_row.name, file_id,
                                                   project_dir=base_dir)

        # 1. The forks: one per option, replacing a previous run's.
        for opt in options:
            if stop_event.is_set():
                break
            net = packs.build_site_network(study.intake, ledger, opt.option_id,
                                           library=library, question=question,
                                           resolve_upload=resolve_upload)
            cfg = packs.option_solver_config(ledger, tariff)
            errors = [i for i in validate_for_run(net, cfg) if i.severity == "error"]
            if errors:
                raise packs.PackError("preflight_failed", "; ".join(
                    f"{i.code} {i.name}: {i.message}" for i in errors[:5]))
            row = study_forks.create_option_fork(
                db, user_id, base_row=base_row, study_id=study_id,
                option_id=opt.option_id, network=net, solver_config=cfg)
            created.append((opt, row, cfg))
        with store.WRITE_LOCK:
            study = store.load_study(base_dir, study_id)
            study = study.model_copy(update={
                "option_projects": [str(r.id) for _o, r, _c in created],
                "updated_at": datetime.now(tz=UTC)})
            store.save_study(base_dir, study)

        # 2. Solve them through the queue, one at a time.
        for opt, row, cfg in created:
            if stop_event.is_set():
                break
            key = project_registry.registry_key(row)
            _set(ctx, record, current=opt.option_id)
            job, _new = solve_queue.enqueue_unique(
                row.name, project_key=key,
                storage_dir=str(project_registry.project_dir(row)),
                solver_config_json=json.dumps(asdict(cfg)),
                enqueued_by_user_id=user_id)
            _wait(job, stop_event)
            if job.status == "completed":
                n = _solved_network(row)
                outcome = _read_option(n, cfg, tariff, opt, fidelity, currency_year)
                outcome["result"] = outcome["result"].model_copy(
                    update={"project_ref": str(row.id)})
                outcome["network_hash"] = _network_hash(
                    project_registry.project_dir(row) / "network.nc")
            else:
                outcome = {"option_id": opt.option_id, "result": _not_run(
                    opt, "aborted" if job.status == "aborted" else "failed",
                    fidelity, currency_year, str(row.id)),
                    "error": job.error}
            outcomes[opt.option_id] = outcome
            try:
                PyPSAService.drop(key)
            except Exception:  # noqa: BLE001
                pass
            with ctx.solver_state_lock:
                if outcome["result"].solve_status == "ok":
                    record["solved"].append(opt.option_id)
                record["pending"] = [o.option_id for o in options
                                     if o.option_id not in outcomes]
        status = "aborted" if stop_event.is_set() else "done"
    except Exception as exc:  # noqa: BLE001 — recorded on the run
        logger.exception("decision study %s run failed", study_id)
        error = f"{type(exc).__name__}: {exc}"
        status = "failed"
    finally:
        # The run's own campaign closes first, so the record written below
        # carries its final state; an agent's campaign is left open.
        if record.get("own_campaign"):
            try:
                _set(ctx, record, campaign=campaign.end(f"decision study run {status}"))
            except campaign.CampaignError:
                pass
        try:
            _finish(db, ctx, record, study_id=study_id, base_row_id=base_row_id,
                    base_dir=base_dir, created=created, outcomes=outcomes,
                    fidelity=fidelity, status=status, error=error)
        except Exception:  # noqa: BLE001
            logger.exception("decision study %s: finishing the run failed", study_id)
            _set(ctx, record, status="failed", finished_at=time.time(),
                 error=error or "finishing the run failed")
        finally:
            db.close()


def _uuid(value):
    import uuid
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _finish(db, ctx, record, *, study_id, base_row_id, base_dir, created, outcomes,
            fidelity, status, error) -> None:
    """Remove unsolved forks, write the findings and the run record."""
    solved_rows = {o.option_id: r for o, r, _c in created
                   if outcomes.get(o.option_id, {}).get("result") is not None
                   and outcomes[o.option_id]["result"].solve_status == "ok"}
    removal_errors = []
    for opt, row, _cfg in created:
        if opt.option_id in solved_rows:
            continue
        try:
            study_forks.delete_fork(db, row, study_id=study_id, base_uuid=base_row_id)
        except study_forks.ForkError as exc:
            removal_errors.append(exc.code)

    with store.WRITE_LOCK:
        study = store.load_study(base_dir, study_id)
        question = Q.get_question(study.question_id)
        options = Q.options_for(question, study.intake)
        currency_year = None
        results: list[OptionResult] = []
        details: dict[str, Any] = {}
        for opt in options:
            out = outcomes.get(opt.option_id)
            if out is None:
                results.append(_not_run(opt, "not_run", fidelity, currency_year))
                continue
            res = out["result"]
            currency_year = currency_year or res.currency_year
            results.append(res)
            details[opt.option_id] = {k: v for k, v in out.items() if k != "result"}
        pending = [o.option_id for o in options if o.option_id not in outcomes
                   or outcomes[o.option_id]["result"].solve_status == "aborted"]
        all_ok = bool(results) and all(r.solve_status == "ok" for r in results)
        baseline_out = outcomes.get("none")
        if baseline_out is not None and baseline_out["result"].solve_status == "ok":
            b = baseline_out["result"]
            baseline = BaselineResult(project_ref=b.project_ref, solve_status="ok",
                                      bill=b.bill, unavailable=dict(
                                          {"bill": b.unavailable["bill"]} if b.bill is None else {}))
        else:
            baseline = BaselineResult(bill=None, unavailable={"bill": "not_run"})
        ledger = study.ledger
        findings = Findings(
            options=results,
            options_status="ok" if all_ok else "not_established",
            pending_options=pending,
            hashes=FindingsHashes(
                ledger_hash=packs.ledger_hash(ledger) if ledger is not None else None,
                option_network_hashes={
                    str(solved_rows[k].id): v["network_hash"]
                    for k, v in details.items()
                    if k in solved_rows and v.get("network_hash")}),
            baseline=baseline,
            honesty_notes=tuple(
                ["options_not_established:" + ",".join(pending)] if pending else []),
        )
        store.save_aux(base_dir, study_id, "findings", findings.model_dump(mode="json", by_alias=True))
        run_record = {
            **_public(record), "status": status, "error": error,
            "finished_at": time.time(), "current": None, "pending": pending,
            "solved": sorted(solved_rows), "details": details,
            "fork_removal_refused": removal_errors,
        }
        store.save_aux(base_dir, study_id, "run", run_record)
        study = study.model_copy(update={
            "option_projects": [str(r.id) for r in solved_rows.values()],
            "findings_ref": store.aux_ref(study_id, "findings"),
            "fidelity_last_run": fidelity,
            "budget": study.budget.model_copy(update={"solves_used": record["solves_charged"]}),
            "stale": False, "stale_reasons": [],
            "updated_at": datetime.now(tz=UTC)})
        store.save_study(base_dir, study)
    _set(ctx, record, status=status, error=error, finished_at=time.time(),
         current=None, pending=pending, solved=sorted(solved_rows))


def get_study_run(study_id: str, *, base_row, base_dir) -> dict | None:
    """The live run record on the base context, else the last one on disk."""
    from services import project_registry

    ctx = PyPSAService.get_context(project_registry.registry_key(base_row))
    if ctx is not None:
        with ctx.solver_state_lock:
            rec = ctx.solver_state.get(STUDY_KEY)
            if rec and rec.get("study_id") == study_id and rec.get("status") == "running":
                return _public(rec)
    return store.load_aux(base_dir, study_id, "run")


def abort_study_run(study_id: str, *, base_row, base_dir) -> dict:
    """Set the run's stop event. Idempotent; None-safe (404 upstream)."""
    from services import project_registry

    ctx = PyPSAService.get_context(project_registry.registry_key(base_row))
    if ctx is not None:
        with ctx.solver_state_lock:
            rec = ctx.solver_state.get(STUDY_KEY)
            if rec and rec.get("study_id") == study_id:
                ev, status = rec.get("stop_event"), rec.get("status")
                if ev is not None:
                    ev.set()
                return {"status": status, "aborting": status == "running"}
    last = store.load_aux(base_dir, study_id, "run")
    if last is None:
        raise RunRefused(404, "study_never_run", "this study has not been run")
    return {"status": last.get("status"), "aborting": False}

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
(``packs.build_site_network``, no tariff price on its Links) with an explicit
``SolverConfig`` (``packs.option_solver_config`` = ``compile.solver_config``)
carrying the study's compiled commercial config (U2 WP6, C6): the run mints
the study's export price series in the base project's org under the owner's
name (``compile.mint_export_series``, idempotent on content) and binds the
config on each option's in-memory network (``compile.bind_on_network``)
BEFORE the fork is written, so the Investment Case engine prices the PoC and
carries the demand charge when the queue solves it. Since U2 WP8 the fork's
config also carries the option's value flows and its finance inputs
(`compile.option_finance`, the case's own compile), and the findings record
what each fork was given (`run_hashes`: the run's `compiled_hash` and a
digest per fork, plan §2 C10). The fork is enqueued with its own
``project_key``, ``storage_dir`` and ``enqueued_by_user_id`` and waited on,
one at a time. After an option is read its fork context is dropped from the
resident registry (it is saved on disk by the queue).

**Results** are read from the fork's live frames with the fork's OWN config
(``eh_report._live_result_df``), the demand charge from what the engine's
solve committed (``engine_adapter.demand_charge_eur``), and the bill from the
engine on the solved PoC meter with the config the solve priced
(``engine_adapter.bill``, U2 WP8, gate C6). The ledger seeds from the pinned
defaults pack (``library.load_defaults``, gate C4).

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
from services.study import run_hashes
from services.study import store

logger = logging.getLogger(__name__)

__all__ = ["RunRefused", "STUDY_KEY", "SolveDeadlineExceeded", "abort_study_run",
           "get_study_run", "start_study_run"]

STUDY_KEY = "decision_study"
_POLL_S = 0.05
#: How long the runner (and the tornado, which shares `_wait`) waits for ONE
#: queued solve, queue time included, before it aborts the job and fails with
#: `SolveDeadlineExceeded` (gate S4 nit: the wait had no deadline, so a hung
#: solver thread pinned the worker for ever). Generous on purpose: an MVP-1
#: option is one 8760 h LP that solves in seconds to minutes; the deadline
#: exists for a solver that never returns, not to cap a slow one.
SOLVE_WAIT_DEADLINE_S = 4 * 3600.0
#: After the deadline's abort, how long the job may take to reach a terminal
#: state before the wait gives up on it (`stuck`).
_ABORT_GRACE_S = 60.0
_TERMINAL = ("completed", "failed", "aborted", "interrupted")
_PUBLIC_DROP = ("thread", "stop_event")
_ECON_CLASSES = ("generators", "storage_units", "stores", "links")


class SolveDeadlineExceeded(RuntimeError):
    """
    A queued solve did not finish within `SOLVE_WAIT_DEADLINE_S`. The job was
    asked to abort; ``stuck`` is True when it had still not stopped after the
    grace period (its fork then cannot be deleted while the queue holds it).
    ``code`` is stable, for the run record and the failure taxonomy.
    """

    code = "solve_deadline_exceeded"

    def __init__(self, job, seconds: float, *, stuck: bool):
        self.job_id = getattr(job, "id", None)
        self.project = getattr(job, "project_id", None)
        self.seconds = float(seconds)
        self.stuck = bool(stuck)
        self.message = (
            f"the solve of {self.project!r} did not finish within {self.seconds:.0f} s; "
            "its queue job was aborted"
            + (" but had not stopped when the wait gave up" if self.stuck else ""))
        super().__init__(self.message)


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


def fork_network_from_disk(row) -> tuple[object, str | None]:
    """
    (network, sha256[:16] of its ``network.nc``) read from the fork's
    directory — never the resident context, which a user may have opened
    and edited, and which must not be touched without its lock (gate S5
    BC-S5-1). The file is hashed before and after the read; a file that
    changed in between answers a ``None`` hash, which matches nothing.
    """
    import pypsa

    from services import project_registry

    path = project_registry.project_dir(row) / "network.nc"
    before = _network_hash(path)
    n = pypsa.Network()
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.import_network_from_netcdf(n, path)
    after = _network_hash(path)
    return n, (before if before == after else None)


def _opt_value(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def _read_option(n, cfg, opt, fidelity: Fidelity, currency_year, *, compiled) -> dict:
    """
    One option's outcome, from the fork's live frames and its own config.
    `compiled` is the commercial config the fork was solved with: the bill is
    the engine's on the solved PoC meter (`engine_adapter.bill`, gate C6 —
    the same compiled config the solve priced, so its total is established
    only when the engine solved it) and the run's demand charge is the amount
    that solve committed (null with a flag without it, ADR-0001).
    """
    from services.adequacy.eh_report import _live_result_df
    from services.study import engine_adapter

    detail: dict[str, Any] = {"option_id": opt.option_id, "caveats": []}
    bill = engine_adapter.bill(n, compiled, fidelity=fidelity)
    detail["bill"] = bill.model_dump(mode="json")
    try:
        from services.results.cost_breakdown import compute_cost_breakdown
        from services.results.objective_decomposition import compute_objective_decomposition

        dec = compute_objective_decomposition(n, compute_cost_breakdown(n, cfg), cfg)
        detail["lp_total_eur"] = dec.get("lp_total")
        detail["residual_gap_pct"] = dec.get("residual_gap_pct")
    except Exception as exc:  # noqa: BLE001 — a figure the bridge cannot give is null
        detail["bridge_unavailable"] = f"{type(exc).__name__}: {exc}"
    # U2 WP6 (plan §2 C3, §5.2): the demand charge the engine's solve
    # committed, never GS's bridge term.
    detail["demand_charge_eur"] = None
    try:
        detail["demand_charge_eur"] = engine_adapter.demand_charge_eur(n, compiled)
        if detail["demand_charge_eur"] is None:
            detail["demand_charge_unavailable"] = "demand_charge_not_established"
    except Exception as exc:  # noqa: BLE001 — null with a flag (ADR-0001)
        detail["demand_charge_unavailable"] = f"{type(exc).__name__}: {exc}"
    try:
        from services.results.asset_economics import compute_asset_economics

        econ = compute_asset_economics(n, cfg, result_df=_live_result_df)
        if econ is None:
            raise LookupError("asset economics answered no data for this network")
        # The per-class keys `compute_asset_economics` returns (gate S4 BC-S4-5),
        # filtered to the assets the option sizes.
        mine = (packs.BATTERY_NAME, packs.PV_NAME)
        detail["asset_economics"] = {
            cls: [row for row in (econ.get(cls) or []) if row.get("name") in mine]
            for cls in _ECON_CLASSES}
        detail["asset_economics"]["capital_costs_available"] = econ.get(
            "capital_costs_available")
        present = [a for a, df in ((packs.BATTERY_NAME, n.storage_units),
                                   (packs.PV_NAME, n.generators)) if a in df.index]
        found = {row.get("name") for cls in _ECON_CLASSES
                 for row in detail["asset_economics"][cls]}
        missing = [a for a in present if a not in found]
        if missing:
            # An asset the engine skipped (no dispatch or no price) is named,
            # never read as a zero (ADR-0001).
            detail["asset_economics_missing"] = missing
    except Exception as exc:  # noqa: BLE001 — null with a flag (ADR-0001), never []
        detail["asset_economics"] = None
        detail["asset_economics_unavailable"] = f"{type(exc).__name__}: {exc}"

    from services.results.sizing import classify_sizing

    sizes: list[AssetSize] = []
    detail["sizing"] = {}

    def _classify(row, asset: str) -> None:
        # The shared classifier (gate S4 [S7]); a size at its bound is not an
        # optimum and the verdict must say so.
        c = classify_sizing(row, "p_nom", solved=True)
        detail["sizing"][asset] = c
        if c["binding_constraint"] == "at_upper_bound":
            detail["caveats"].append(f"size_at_upper_bound:{asset}")

    if packs.BATTERY_NAME in n.storage_units.index:
        su = n.storage_units.loc[packs.BATTERY_NAME]
        p = _opt_value(su.get("p_nom_opt"))
        e = None if p is None else p * float(su["max_hours"])
        flags = {} if p is not None else {"p_nom_opt": "not_solved", "e_nom_opt": "not_solved"}
        sizes.append(AssetSize(asset=packs.BATTERY_NAME, p_nom_opt=p, e_nom_opt=e,
                               unavailable=flags))
        _classify(su, packs.BATTERY_NAME)
    if packs.PV_NAME in n.generators.index:
        g = n.generators.loc[packs.PV_NAME]
        p = _opt_value(g.get("p_nom_opt"))
        sizes.append(AssetSize(asset=packs.PV_NAME, p_nom_opt=p, e_nom_opt=None,
                               unavailable={"e_nom_opt": "not_applicable",
                                            **({} if p is not None else {"p_nom_opt": "not_solved"})}))
        _classify(g, packs.PV_NAME)

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
    from services import project_registry
    from services.adequacy import campaign

    fidelity = Fidelity(fidelity)
    base_uuid = str(base_row.id)
    base_dir = project_registry.project_dir(base_row)
    try:
        study = store.load_study(base_dir, study_id)
    except store.StudyNotFound:
        raise RunRefused(404, "study_not_found", "Study not found") from None
    question = _runnable(study, base_uuid)
    library = study_library.load_defaults()
    ledger = study.ledger or study_library.seed_ledger(question, study.intake, library)
    try:
        packs.refuse_unrunnable_ledger(ledger)
        missing = packs.missing_inputs(study.intake)
        if missing:
            raise packs.PackError("intake_incomplete",
                                  f"mandatory input(s) not answered: {', '.join(missing)}")
        packs.effective_tariff(study.intake, ledger, library)  # stale tariff → refused
        packs.option_commercial(study.intake, ledger, library)  # the engine's tariff (WP6)
    except packs.PackError as exc:
        status = 409 if exc.code in ("ledger_needs_attention", "ledger_tariff_stale") else 422
        raise RunRefused(status, exc.code, exc.message) from None
    options = Q.options_for(question, study.intake)
    solves = campaign.estimate_solves(None, STUDY_KEY, options=[o.option_id for o in options])
    # BC-S4-3: the default budget is the CURRENT option count (PV enabled after
    # creation adds one), never the count at creation.
    budget = solves if budget_solves is None else int(budget_solves)

    # BC-S4-1: every check that can refuse runs BEFORE the base context is
    # registered, on the resident context if there is one; a base the run
    # registers itself is study-owned (outside the resident cap), so the
    # registration can never evict a user's context.
    key = project_registry.registry_key(base_row)
    resident = PyPSAService.get_context(key)
    if resident is not None:
        _refuse_busy(resident)
    open_campaign = (resident is not None
                     and _on_ctx(resident, campaign.status)["active"])
    if open_campaign:
        try:
            _on_ctx(resident, campaign.check, STUDY_KEY, solves)
        except campaign.CampaignBudgetError as exc:
            raise RunRefused(409, "campaign_budget_exhausted", str(exc)) from None
    elif solves > budget:
        raise RunRefused(409, "campaign_budget_exhausted", _shortfall(solves, budget, budget))
    if not 1 <= budget <= campaign.MAX_BUDGET_SOLVES:
        raise RunRefused(422, "campaign_budget_invalid",
                         f"budget_solves must be 1..{campaign.MAX_BUDGET_SOLVES}")

    registered = resident is None
    if registered:
        PyPSAService.mark_study_owned(key)
    try:
        ctx = base_context(base_row)
    except BaseException:
        if registered:
            PyPSAService.unmark_study_owned(key)
        raise
    stop_event = threading.Event()
    record: dict[str, Any] = {
        "status": "running", "study": STUDY_KEY, "study_id": study_id,
        "fidelity": fidelity.value, "options": [o.option_id for o in options],
        "solved": [], "current": None, "pending": [o.option_id for o in options],
        "solves_charged": solves, "campaign": None, "own_campaign": False,
        # BC-S4-2: what the forks are built from, captured now; `_finish`
        # records THIS hash and marks the study stale if the stored ledger or
        # intake moved during the run.
        "ledger_hash": packs.ledger_hash(ledger), "registered_base": registered,
        "error": None, "started_at": time.time(), "finished_at": None,
        # U2 WP6: the minted export series the forks are bound to and the
        # compiled commercial config's digest (set by the worker).
        "export_series": None, "commercial_digest": None,
        "thread": None, "stop_event": stop_event,
    }
    worker_args = dict(study_id=study_id, base_row_id=base_uuid,
                       base_dir=base_dir, user_id=user_id, fidelity=fidelity,
                       record=record, stop_event=stop_event, ctx=ctx,
                       ledger=ledger, intake=dict(study.intake))
    run_ctx = contextvars.copy_context()
    thread = threading.Thread(
        target=lambda: run_ctx.run(_worker, **worker_args),
        daemon=True, name=f"decision-study-{study_id[:8]}")
    record["thread"] = thread

    lock = ctx.mutation_lock
    if not lock.acquire(timeout=5.0):
        _release_base(key, registered)
        raise RunRefused(409, "solver_in_flight",
                         "a solve holds the study's base project; wait for it to finish")
    try:
        with ctx.solver_state_lock:
            # The claim: the same checks again, under the base context's locks.
            try:
                _refuse_busy(ctx)
            except RunRefused:
                _release_base(key, registered)
                raise
            # Check-then-record on the BASE context's campaign.
            own = not _on_ctx(ctx, campaign.status)["active"]
            if own:
                try:
                    _on_ctx(ctx, campaign.start,
                            f"decision study {study.name!r}: {len(options)} option(s)",
                            budget)
                except campaign.CampaignError as exc:
                    _release_base(key, registered)
                    raise RunRefused(422, "campaign_budget_invalid", str(exc)) from None
            try:
                _on_ctx(ctx, campaign.check, STUDY_KEY, solves)
            except campaign.CampaignBudgetError as exc:
                if own:
                    _on_ctx(ctx, campaign.end, "refused before the first solve")
                _release_base(key, registered)
                raise RunRefused(409, "campaign_budget_exhausted", str(exc)) from None
            record["own_campaign"] = own
            ctx.solver_state[STUDY_KEY] = record
            try:
                thread.start()
            except BaseException:
                ctx.solver_state[STUDY_KEY] = None
                if own:
                    _on_ctx(ctx, campaign.end, "the worker did not start")
                _release_base(key, registered)
                raise
            record["campaign"] = _on_ctx(ctx, campaign.record, STUDY_KEY, solves)
    finally:
        lock.release()
    return {"status": "running", "study_id": study_id, "fidelity": fidelity.value,
            "options": record["options"], "solves_charged": solves,
            "campaign": record["campaign"]}


def _refuse_busy(ctx) -> None:
    """The study mesh and the in-flight test, on THIS context (BC-5)."""
    from routers.simulation import _solver_in_flight_ctx
    from services.project_context import STUDY_LABELS, running_study_key

    with ctx.solver_state_lock:
        busy = running_study_key(ctx.solver_state)
    if busy is not None:
        raise RunRefused(409, "study_running", (
            f"{STUDY_LABELS.get(busy, busy)} is running on this study's "
            "base project — wait for it to finish, or abort it"))
    if _solver_in_flight_ctx(ctx):
        raise RunRefused(409, "solver_in_flight",
                         "a solve is running on this study's base project")


def _shortfall(solves: int, remaining: int, budget: int) -> str:
    return (f"the run needs up to {solves} solve(s), one per option, and the "
            f"campaign has {remaining} of {budget} left. Raise budget_solves "
            "or run fewer options (disable PV)")


def _release_base(key: str, registered: bool) -> None:
    """
    Undo a base registration this run made: drop the context unless a
    session has it open (then it is the user's, and counts against the cap
    like any other), and lift the cap exemption either way.

    "Open" is read from the sessions' durable pointers only. The request
    scope is NOT evidence: the worker thread runs in a copy of the request's
    context, which is bound to the base itself, so `get_active_id()` there
    always answers "the base" and the base was never dropped (S4 re-gate,
    BC-S4-v2-1) — it stayed loaded as an extra user context, and the next
    ordinary project open evicted, and so saved, one user project too many.
    """
    if not registered:
        return
    try:
        in_use = key in PyPSAService._session_active_keys()
    except Exception:  # noqa: BLE001
        in_use = True
    # Unmark and drop atomically (gate S6, BC-S6-v2-2): between two separate
    # calls the base would be resident but not exempt, and a concurrent
    # registration at the cap could evict it (BC-S4-1 in a two-line window).
    PyPSAService.release_study_owned(key, drop=not in_use)


def _set(ctx, record: dict, **kw) -> None:
    with ctx.solver_state_lock:
        record.update(kw)


def _wait(job, stop_event: threading.Event, *, deadline_s: float | None = None) -> None:
    """
    Wait for ``job`` to reach a terminal state, aborting it in the queue when
    ``stop_event`` is set. Past the deadline (``SOLVE_WAIT_DEADLINE_S`` unless
    given) the job is aborted and `SolveDeadlineExceeded` raised once it has
    stopped, or after `_ABORT_GRACE_S` if it will not (``stuck``).
    """
    from services.solve_queue import solve_queue

    limit = SOLVE_WAIT_DEADLINE_S if deadline_s is None else float(deadline_s)
    deadline = time.monotonic() + limit
    aborted = False
    expired_at = None
    while job.status not in _TERMINAL:
        now = time.monotonic()
        if expired_at is None and now >= deadline:
            expired_at = now
            if not aborted:
                solve_queue.abort(job.id)
                aborted = True
        if expired_at is not None and now - expired_at >= _ABORT_GRACE_S:
            raise SolveDeadlineExceeded(job, limit, stuck=True)
        if stop_event.is_set() and not aborted:
            solve_queue.abort(job.id)
            aborted = True
        time.sleep(_POLL_S)
    if expired_at is not None:
        raise SolveDeadlineExceeded(job, limit, stuck=False)


def _worker(*, study_id, base_row_id, base_dir, user_id, fidelity, record,
            stop_event, ctx, ledger, intake) -> None:
    """The run itself, on the base context (bound for this thread only)."""
    from db.models import Project
    from db.session import SessionLocal
    from services import project_registry
    from services.adequacy import campaign
    from services.solve_queue import solve_queue
    from services.study.engine_adapter import cycling_flags as export_cycling_flags
    from services.validation_service import validate_for_run

    PyPSAService.bind_request_context(ctx)
    db = SessionLocal()
    created: list[tuple[Any, Any, Any]] = []   # (opt, fork row, cfg)
    outcomes: dict[str, dict] = {}
    # Gate F1 BC-F1-1: the export-cycling WARNINGS are kept per option (the
    # study keeps no other preflight warning) and disclosed by the findings.
    preflight_flags: dict[str, list[str]] = {}
    bound_by_option: dict[str, Any] = {}
    # U2 WP8 (C10): each option's compiled finance digest, and why a finance
    # was not compiled.
    finance_digests: dict[str, str | None] = {}
    finance_refused: dict[str, tuple[str, str]] = {}
    status, error = "failed", None
    study = None
    try:
        base_row = db.get(Project, _uuid(base_row_id))
        study = store.load_study(base_dir, study_id)
        question = Q.get_question(study.question_id)
        library = study_library.load_defaults()
        # The ledger and intake captured when the run started (BC-S4-2), not
        # whatever the sidecar holds by now.
        tariff = packs.effective_tariff(intake, ledger, library)
        options = Q.options_for(question, intake)
        currency_year = tariff.currency_year

        def resolve_upload(file_id: str) -> bytes:
            from services import upload_service
            return upload_service.get_upload_bytes(base_row.name, file_id,
                                                   project_dir=base_dir)

        # 0. The engine's commercial config (U2 WP6, C3/C6): the study's
        # export price minted in the base project's org under the owner's
        # name (re-used when unchanged), compiled with the ledger applied.
        from services.study import compile as study_compile

        idx = packs.snapshots_for(intake)
        try:
            ref = study_compile.mint_export_series(
                db, base_row.org_id, base_uuid=base_row_id, study_id=study_id,
                study_name=study.name, base_name=base_row.name, tariff=tariff, snapshots=idx)
        except study_compile.CompileError as exc:
            raise packs.PackError(exc.code, exc.message) from None
        compiled = packs.option_commercial(intake, ledger, library, idx, export_series=ref)
        resolve_ref = study_compile.library_series_resolver(db, base_row.org_id)
        _set(ctx, record, export_series=None if ref is None else ref.model_dump(mode="json"),
             commercial_digest=compiled.digest)

        # 1. The forks: one per option, replacing a previous run's.
        for opt in options:
            if stop_event.is_set():
                break
            net = packs.build_site_network(intake, ledger, opt.option_id,
                                           library=library, question=question,
                                           resolve_upload=resolve_upload)
            # C6: bound on the in-memory network before the fork is written,
            # with the option's value flows (row 28).
            bound = packs.bind_option(net, compiled, resolve_ref=resolve_ref)
            bound = study_compile.with_value_flows(bound, net)
            bound_by_option[opt.option_id] = bound
            # U2 WP8: the fork carries BOTH engine inputs, the commercial
            # config and the finance inputs the case is valued on. A finance
            # the compiler refuses (a second currency year) is not written;
            # the case route then refuses with the same code.
            try:
                fin = study_compile.option_finance(ledger, bound, net,
                                                   study_currency_year=study.currency_year)
            except study_compile.CompileError as exc:
                fin = None
                finance_refused[opt.option_id] = (exc.code, exc.message)
            finance_digests[opt.option_id] = None if fin is None else fin.digest
            try:
                cfg = study_compile.solver_config(
                    ledger, bound, finance=None if fin is None else fin.finance())
            except study_compile.CompileError as exc:
                raise packs.PackError(exc.code, exc.message) from None
            issues = validate_for_run(net, cfg)
            errors = [i for i in issues if i.severity == "error"]
            if errors:
                raise packs.PackError("preflight_failed", "; ".join(
                    f"{i.code} {i.name}: {i.message}" for i in errors[:5]))
            flags = export_cycling_flags(issues)
            if flags:
                preflight_flags[opt.option_id] = flags
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
            # BC-S4-1: the queue registers the fork; exempt it from the cap
            # BEFORE it is enqueued so that registration never evicts.
            PyPSAService.mark_study_owned(key)
            job, _new = solve_queue.enqueue_unique(
                row.name, project_key=key,
                storage_dir=str(project_registry.project_dir(row)),
                solver_config_json=json.dumps(asdict(cfg)),
                enqueued_by_user_id=user_id)
            timed_out = None
            try:
                _wait(job, stop_event)
            except SolveDeadlineExceeded as exc:
                timed_out = exc
            if job.status == "completed" and timed_out is None:
                n = _solved_network(row)
                outcome = _read_option(n, cfg, opt, fidelity, currency_year,
                                       compiled=bound_by_option[opt.option_id])
                outcome["result"] = outcome["result"].model_copy(
                    update={"project_ref": str(row.id)})
                outcome["network_hash"] = _network_hash(
                    project_registry.project_dir(row) / "network.nc")
                # C10: what the fork's saved config gives the engine now.
                outcome["engine_digest"] = run_hashes.fork_engine_digest(row)
                outcome["finance_digest"] = finance_digests.get(opt.option_id)
                if opt.option_id in finance_refused:
                    # Gate U2-WP8a Y1: read by `findings.load_inputs` (422).
                    code, message = finance_refused[opt.option_id]
                    outcome["finance_unavailable"] = code
                    outcome["finance_unavailable_message"] = message
            elif timed_out is not None:
                # A typed failure of THIS option (gate S4 nit); the run stops
                # below, because a solver that hangs pins the queue for the rest.
                outcome = {"option_id": opt.option_id, "result": _not_run(
                    opt, "failed", fidelity, currency_year, str(row.id)),
                    "error": f"{timed_out.code}: {timed_out.message}"}
            else:
                outcome = {"option_id": opt.option_id, "result": _not_run(
                    opt, "aborted" if job.status == "aborted" else "failed",
                    fidelity, currency_year, str(row.id)),
                    "error": job.error}
            outcomes[opt.option_id] = outcome
            try:
                PyPSAService.release_study_owned(key, drop=True)
            except Exception:  # noqa: BLE001
                PyPSAService.unmark_study_owned(key)
            with ctx.solver_state_lock:
                if outcome["result"].solve_status == "ok":
                    record["solved"].append(opt.option_id)
                record["pending"] = [o.option_id for o in options
                                     if o.option_id not in outcomes]
            if timed_out is not None:
                error = f"{timed_out.code}: {timed_out.message} (option {opt.option_id})"
                break
        status = ("aborted" if stop_event.is_set()
                  else "failed" if error is not None else "done")
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
        # The fork keys are read while the session is open (a delete in
        # `_finish` commits, which expires the rows).
        fork_keys = [project_registry.registry_key(row) for _o, row, _c in created]
        final: dict[str, Any] = {"status": "failed", "finished_at": time.time(),
                                 "error": error or "finishing the run failed"}
        try:
            final = _finish(db, ctx, record, study_id=study_id, base_row_id=base_row_id,
                            base_dir=base_dir, created=created, outcomes=outcomes,
                            fidelity=fidelity, status=status, error=error,
                            run_ledger=ledger, run_intake=intake,
                            preflight_flags=preflight_flags)
        except Exception:  # noqa: BLE001
            logger.exception("decision study %s: finishing the run failed", study_id)
        finally:
            db.close()
            # Gate S6: release EVERYTHING before the terminal status is
            # published — a poll that reads "done" must find no exemption and
            # no study context left (the release used to follow it, and a slow
            # release was visible as a leaked `_study_owned` key).
            for key in fork_keys:
                PyPSAService.unmark_study_owned(key)
            if record.get("registered_base"):
                _release_base(ctx.registry_key, True)
            _set(ctx, record, **final)


def _uuid(value):
    import uuid
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _flag_codes(preflight_flags: dict[str, list[str]] | None) -> list[str]:
    """Every option's export-cycling codes, once each (gate F1 BC-F1-1)."""
    return list(dict.fromkeys(c for codes in (preflight_flags or {}).values() for c in codes))


def _finish(db, ctx, record, *, study_id, base_row_id, base_dir, created, outcomes,
            fidelity, status, error, run_ledger, run_intake,
            preflight_flags: dict[str, list[str]] | None = None) -> dict:
    """
    Remove unsolved forks, write the findings and the run record; return the
    live record's terminal fields, which the worker publishes after release.
    """
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
        # BC-S4-2: the hash of the ledger the forks were BUILT from; a ledger or
        # intake that moved during the run marks the study stale, so nothing
        # downstream attributes these results to the edited assumptions.
        run_hash = packs.ledger_hash(run_ledger)
        now_ledger = study.ledger or study_library.seed_ledger(
            question, study.intake, study_library.load_defaults())
        stale_reasons = []
        if packs.ledger_hash(now_ledger) != run_hash:
            stale_reasons.append("ledger_changed_during_run")
        if dict(study.intake) != dict(run_intake):
            stale_reasons.append("intake_changed_during_run")
        findings = Findings(
            options=results,
            options_status="ok" if all_ok else "not_established",
            pending_options=pending,
            hashes=FindingsHashes(
                ledger_hash=run_hash,
                # Gate S7 [S4]: the intake the forks were built from.
                intake_hash=run_hashes.intake_hash(run_intake),
                option_network_hashes={
                    str(solved_rows[k].id): v["network_hash"]
                    for k, v in details.items()
                    if k in solved_rows and v.get("network_hash")},
                # U2 WP8 (C10): the engine inputs the forks were solved with.
                compiled_hash=run_hashes.compiled_hash(
                    record.get("commercial_digest"),
                    {k: v.get("finance_digest") for k, v in details.items()
                     if k in solved_rows}) if record.get("commercial_digest") else None,
                option_compiled_hashes={
                    str(solved_rows[k].id): v["engine_digest"]
                    for k, v in details.items()
                    if k in solved_rows and v.get("engine_digest")}),
            baseline=baseline,
            honesty_notes=tuple(
                (["options_not_established:" + ",".join(pending)] if pending else [])
                + _flag_codes(preflight_flags)),
        )
        store.save_aux(base_dir, study_id, "findings", findings.model_dump(mode="json", by_alias=True))
        run_record = {
            **_public(record), "status": status, "error": error,
            "finished_at": time.time(), "current": None, "pending": pending,
            "solved": sorted(solved_rows), "details": details,
            # The intake the forks were built from (the report's appendix).
            "intake": dict(run_intake),
            "fork_removal_refused": removal_errors,
            "preflight_flags": dict(preflight_flags or {}),
        }
        store.save_aux(base_dir, study_id, "run", run_record)
        study = study.model_copy(update={
            "option_projects": [str(r.id) for r in solved_rows.values()],
            "findings_ref": store.aux_ref(study_id, "findings"),
            "fidelity_last_run": fidelity,
            "budget": study.budget.model_copy(update={
                "solves_max": record["solves_charged"],
                "solves_used": record["solves_charged"]}),
            "stale": bool(stale_reasons), "stale_reasons": stale_reasons,
            "updated_at": datetime.now(tz=UTC)})
        store.save_study(base_dir, study)
    # The caller publishes this once everything is released (gate S6).
    return {"status": status, "error": error, "finished_at": time.time(),
            "current": None, "pending": pending, "solved": sorted(solved_rows)}


def get_study_run(study_id: str, *, base_row, base_dir) -> dict | None:
    """The live run record on the base context, else the last one on disk."""
    from services import project_registry

    ctx = PyPSAService.get_context(project_registry.registry_key(base_row))
    if ctx is not None:
        with ctx.solver_state_lock:
            rec = ctx.solver_state.get(STUDY_KEY)
            # S6: the slot also holds a running tornado (`kind="tornado"`),
            # which has its own status route.
            if (rec and rec.get("study_id") == study_id and rec.get("status") == "running"
                    and rec.get("kind", "run") == "run"):
                return _public(rec)
    return store.load_aux(base_dir, study_id, "run")


def abort_study_run(study_id: str, *, base_row, base_dir) -> dict:
    """Set the run's stop event. Idempotent; None-safe (404 upstream)."""
    from services import project_registry

    ctx = PyPSAService.get_context(project_registry.registry_key(base_row))
    if ctx is not None:
        with ctx.solver_state_lock:
            rec = ctx.solver_state.get(STUDY_KEY)
            if rec and rec.get("study_id") == study_id and rec.get("kind", "run") == "run":
                ev, status = rec.get("stop_event"), rec.get("status")
                if ev is not None:
                    ev.set()
                return {"status": status, "aborting": status == "running"}
    last = store.load_aux(base_dir, study_id, "run")
    if last is None:
        raise RunRefused(404, "study_never_run", "this study has not been run")
    return {"status": last.get("status"), "aborting": False}

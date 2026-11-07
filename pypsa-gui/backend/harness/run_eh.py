"""Study harness 07 B2: the eh_site run's Energy Hub study (GK B C1, C2;
GK B (31) F1-F12).

`run_eh_study` is always the user's own card, in envelope mode too
(`run_envelope.NEVER_LISTED`). Its arguments are never a model's (#1072):
they are frozen once, from the profile's `eh_study` block and the run
project's reliability goal, at the grant in envelope mode and at solve-stage
entry otherwise (`freeze`). The card shows them, and every execution of the
step compares them, and the live goal, with the frozen record (`admit`).

The study's solves count against the envelope's solves cap: the call
reserves its `budget_solves` on the envelope ledger before it starts, and
the reservation settles to what the study charged, never capped down (F4).
The step then waits on its own `eh_study` job and settles from the record
its own call published, matched by `started_at` and never the process's
latest record (F3). Only `done` with every requested stage run completes it
(F2). The study runs on a private copy (`eh_study.run_eh_study`), so the step
writes nothing to the project and adds no ledger row (F10).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

TOOL = "run_eh_study"
STUDY = "eh_study"
# The profile action's args until the step is frozen: a plain value, never a
# `$` step reference (`assistant_tasks._resolve`), and never a call's args.
UNFROZEN = {"frozen": STUDY}
ARGS_KIND = "eh_args"
BUDGET_KIND = "eh_budget"
REFUSED_KIND = "eh_refused"
# The tool's own parameters: what a frozen record may carry.
PARAMS = (
    "archetype",
    "stages",
    "budget_solves",
    "pack_overrides",
    "dtc_config",
    "dsr_buses",
    "mc",
    "dtc_attribution",
)
GOAL_FIELDS = ("site_archetype", "target_lole_h", "ens_cap_permyriad", "mc_draws")
POLL_SECONDS = 0.5
MAX_FOLLOW_SECONDS = 24 * 3600
_WAKE = threading.Event()  # never set in production; a poll interval (tests set it)
# Issue 07 B3a (GK B (32) L5): ticket -> this process's live follower. The
# "no live follower" check and the registration happen under one lock, so a
# tick and a takeover racing on one step start at most one follower.
_FOLLOWERS: dict = {}
_FOLLOW_LOCK = threading.Lock()


class FreezeError(ValueError):
    """The EH study's arguments cannot be fixed (no block, an unreadable goal)."""


def is_eh_step(step) -> bool:
    return isinstance(step, dict) and step.get("tool") == TOOL


def digest(args) -> str:
    return hashlib.sha256(
        json.dumps(args, sort_keys=True, default=str).encode()
    ).hexdigest()


def _template(ctx):
    from services.flow_service import read_template

    storage = getattr(ctx, "storage_dir", None)
    return read_template(Path(storage)) if storage else None


def goal_of(ctx) -> dict:
    """The run project's reliability goal, as `reliability_goal.active_goal`
    reads it for the open project, but on `ctx` (never the foreground)."""
    from services import reliability_goal

    return reliability_goal.effective_goal(
        ctx.solver_state.get("solver_config"), _template(ctx)
    )


def _goal_snapshot(goal) -> dict:
    return {
        k: {"value": goal[k]["value"], "source": goal[k]["source"]} for k in GOAL_FIELDS
    }


def freeze(ctx, profile, solves_left) -> dict:
    """The step's frozen record: `args` (every goal field the study would
    otherwise read live at its start resolved here, F1), the goal it was
    read from, and their digest. `budget_solves` is the profile's, capped at
    `solves_left` (C2)."""
    from services import flow_service, reliability_goal
    from services.adequacy.eh_study_runner import EhStudyRequest

    block = profile.get("eh_study")
    if not isinstance(block, dict) or not isinstance(block.get("budget_solves"), int):
        raise FreezeError("The profile names no eh_study block with budget_solves.")
    try:
        cfg = ctx.solver_state.get("solver_config")
        template = _template(ctx)
        goal = reliability_goal.effective_goal(cfg, template)
        # The Goal card's own request for this project (its template's site
        # overrides, the stored goal over them): what a user's study would send.
        card = flow_service._request_for(template, cfg)
    except Exception as exc:  # noqa: BLE001 — an unreadable goal fixes nothing
        raise FreezeError("The project's reliability goal cannot be read.") from exc
    budget = min(int(block["budget_solves"]), int(solves_left))
    if budget < 1:
        raise FreezeError("No solve is left for the Energy Hub study.")
    body = EhStudyRequest(
        archetype=card["archetype"],
        stages=list(block.get("stages") or card.get("stages") or []) or None,
        budget_solves=budget,
        pack_overrides={
            **(card.get("pack_overrides") or {}),
            **dict(block.get("pack_overrides") or {}),
        },
        mc={**dict(block.get("mc") or {}), "draws": int(goal["mc_draws"]["value"])},
        dtc_config=block.get("dtc_config"),
        dsr_buses=block.get("dsr_buses"),
        dtc_attribution=block.get("dtc_attribution") or card.get("dtc_attribution"),
    )
    body, _ = reliability_goal.eh_request_with_goal(body, goal)
    dumped = body.model_dump(mode="json")
    args = {k: dumped[k] for k in PARAMS if dumped.get(k) not in (None, {}, [])}
    return {"args": args, "goal": _goal_snapshot(goal), "digest": digest(args)}


def fill(steps, frozen) -> None:
    """Put the frozen args on a freshly materialised EH step (in place); a
    step with none keeps `UNFROZEN`, which `admit` refuses."""
    for step in steps:
        if is_eh_step(step) and step["args"] == UNFROZEN and frozen is not None:
            step["args"] = json.loads(json.dumps(frozen["args"]))
            step["frozen"] = json.loads(json.dumps(frozen))


def stage_frozen(path, task, profile, ctx):
    """At solve-stage entry: the grant's frozen record when the envelope
    carries one, else one frozen now on `ctx` (Q2 = A). None when it cannot
    be fixed: the step then stops for the user at its first execution."""
    from services import run_envelope

    envelope = run_envelope._load(path)
    if (
        envelope is not None
        and envelope.get("state") == "granted"
        and envelope.get("eh_study")
    ):
        return envelope["eh_study"]
    try:
        return freeze(ctx, profile, solves_cap(profile, envelope))
    except FreezeError:
        logger.info(
            "run %s: the EH study's args could not be frozen", task.get("task_id")
        )
        return None


def solves_cap(profile, envelope):
    """What one study may spend: the envelope's solves left when one is
    granted, else the profile's envelope budget (the step's own card shows it)."""
    from services import run_envelope

    if envelope is not None and envelope.get("state") == "granted":
        return envelope["budget"]["solves"] - run_envelope.solves_used(envelope)
    return int(((profile.get("envelope") or {}).get("budget") or {}).get("solves", 0))


def _stop(task, reason):
    from harness import runner

    if task["run"]["status"] in runner._DRIVEN:
        runner._stop_in(task, "needs_user", reason)


def admit(path, task, step, args, ctx):
    """Every execution of the run's EH step (`assistant_tasks.begin_step`,
    under the task lock): the args equal the frozen record, the live goal
    equals the one it was frozen from (F1), and its `budget_solves` fit the
    solves left (C2), reserved on the envelope ledger before the call.
    Returns the refusal (kind, message), the run then stopped for the user;
    or None."""
    from services import run_envelope

    frozen = step.get("frozen")
    if (
        not isinstance(frozen, dict)
        or digest(args) != frozen.get("digest")
        or digest(frozen.get("args")) != frozen.get("digest")
    ):
        _stop(task, ARGS_KIND)
        return (
            ARGS_KIND,
            "The Energy Hub study runs only with the arguments its card showed.",
        )
    try:
        live = (
            _goal_snapshot(goal_of(ctx))
            if ctx is not None
            and str(getattr(ctx, "project_uuid", "")) == task["project_id"]
            else None
        )
    except Exception:  # noqa: BLE001 — an unreadable goal is not the frozen one
        live = None
    if live != frozen.get("goal"):
        _stop(task, ARGS_KIND)
        return ARGS_KIND, (
            "The project's reliability goal changed since the study's card was drawn; "
            "review the run again."
        )
    budget = args.get("budget_solves")
    envelope = run_envelope._load(path)
    if envelope is not None and envelope.get("state") == "granted":
        left = envelope["budget"]["solves"] - run_envelope.solves_used(envelope)
        if not isinstance(budget, int) or budget < 1 or budget > left:
            _stop(task, BUDGET_KIND)
            return (
                BUDGET_KIND,
                f"The study needs {budget} solves and the run has {max(left, 0)} left.",
            )
        run_envelope._audit(
            envelope,
            STUDY,
            call_id=step["attempt_id"],
            tool=TOOL,
            step=step["id"],
            reserved=budget,
        )
        run_envelope._save(path, envelope)
        from harness import runner

        # On the caller's task (it saves it): `_count_solves` would write a copy it then overwrites.
        runner._budget(task)["used"]["solves"] = run_envelope.solves_used(envelope)
    elif not isinstance(budget, int) or budget < 1:
        _stop(task, BUDGET_KIND)
        return BUDGET_KIND, "The study has no solve budget."
    return None


def release(path, call_id, solves=0, task=None):
    """Settle the call's reservation to `solves` (0: refused before the study
    started, F5; else what it charged, F4; None: unknown, so the whole
    reservation, C4). No reservation stays open past the step. `task`: the
    caller's in-memory task, whose solve count is set too (it saves it).
    No-op without a row."""
    from harness import runner
    from services import run_envelope

    if solves is None:
        row = next(
            (
                a
                for a in (run_envelope._load(path) or {}).get("audit", [])
                if a["event"] == STUDY and a.get("call_id") == call_id
            ),
            None,
        )
        if row is None:
            return False
        solves = int(row.get("reserved") or 0)
    settled = run_envelope.settle_engine_solves(path, call_id, solves, event=STUDY)
    envelope = run_envelope._load(path)
    if settled and task is not None and envelope is not None:
        runner._budget(task)["used"]["solves"] = run_envelope.solves_used(envelope)
    return settled


def charged(record):
    """F4: the solves the study charged, the sum of its stages'
    `solves_charged`; None when the record does not say."""
    stages = (((record or {}).get("report") or {}).get("pipeline") or {}).get("stages")
    if not isinstance(stages, list) or not stages:
        return None
    total = 0
    for stage in stages:
        value = stage.get("solves_charged") if isinstance(stage, dict) else None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        total += value
    return total


def skipped(record):
    """F2: the requested stages a `done` study did not run, with the reason."""
    stages = (((record or {}).get("report") or {}).get("pipeline") or {}).get(
        "stages"
    ) or []
    return [
        f"{s.get('stage')}: {s.get('note')}"
        for s in stages
        if isinstance(s, dict) and str(s.get("note") or "").startswith("not run")
    ]


def outcome(record, started_at):
    """(native status, step status, error) from the step's own record (F2,
    F3). A replaced or missing record is unverifiable, never completed."""
    if (
        not isinstance(record, dict)
        or started_at is None
        or record.get("started_at") != started_at
    ):
        return (
            "unknown",
            "uncertain",
            "The study's own record is gone or was replaced; it cannot be verified.",
        )
    status = record.get("status")
    if status == "done":
        missing = skipped(record)
        if missing:
            return (
                "done",
                "failed",
                "The study skipped a requested stage: " + "; ".join(missing)[:240],
            )
        if not isinstance(record.get("report"), dict):
            return "done", "uncertain", "The study ended without a report."
        return "done", "completed", None
    if status in ("failed", "aborted"):
        return (
            status,
            "failed",
            f"The study ended {status}: {str(record.get('error') or '')[:200]}",
        )
    return "unknown", "uncertain", "The study's outcome cannot be verified."


def _record(ctx):
    with ctx.solver_state_lock:
        record = ctx.solver_state.get(STUDY)
        if not isinstance(record, dict):
            return None
        return {
            k: record.get(k)
            for k in (
                "status",
                "started_at",
                "report",
                "error",
                "thread",
                "network_revision",
                "goal_sources",
                "budget_solves",
            )
        }


def wait(ticket, job_id, session_id, ctx, begun):
    """The run's EH step waits on its own study job (instead of completing
    on its start receipt) and a follower settles it from that study's
    record: the one whose `started_at` (`begun`) the step's own start
    receipt names (F3), never one read after the call returned. True when
    the step now waits."""
    from services import assistant_tasks

    with assistant_tasks._LOCK:
        _, task = assistant_tasks._read(ticket[0])
    if not task.get("run"):
        return False  # a chat plan's step keeps its own rules; only a run's EH step waits here
    path = assistant_tasks.await_job(ticket, job_id, session_id)
    if path is None:
        return False

    def mark(task):
        step = next((s for s in task["steps"] if s["id"] == ticket[1]), None)
        if (
            step is None
            or step.get("attempt_id") != ticket[2]
            or step["status"] != "waiting"
        ):
            return False
        step["job"].update(
            kind=STUDY,
            started_at=begun,
            call_id=ticket[2],
            deadline_at=time.time() + MAX_FOLLOW_SECONDS,
        )

    from harness import runner

    runner._update(path, mark)
    with _FOLLOW_LOCK:
        _start_follower(path, ticket, job_id, ctx, begun)
    return True


def _start_follower(path, ticket, job_id, ctx, begun):
    """Start and register the step's follower (the caller holds `_FOLLOW_LOCK`)."""
    for key in [k for k, t in _FOLLOWERS.items() if not t.is_alive()]:
        _FOLLOWERS.pop(key, None)
    thread = threading.Thread(
        target=_follow,
        args=(path, ticket, job_id, ctx, begun),
        name=f"run-eh-{job_id[-8:]}",
        daemon=True,
    )
    _FOLLOWERS[tuple(ticket)] = thread
    thread.start()
    return thread


def following(ticket) -> bool:
    """Whether this process has a live follower for the EH step `ticket`."""
    thread = _FOLLOWERS.get(tuple(ticket))
    return thread is not None and thread.is_alive()


def _follow(path, ticket, job_id, ctx, begun):
    try:
        deadline = time.monotonic() + MAX_FOLLOW_SECONDS
        record = _record(ctx)
        while (
            record is not None
            and record.get("started_at") == begun
            and record.get("status") == "running"
            and _alive(record)
            and time.monotonic() < deadline
        ):
            _WAKE.wait(POLL_SECONDS)
            record = _record(ctx)
        settle(path, ticket, job_id, record, begun)
    except Exception:  # noqa: BLE001 — a lost settlement leaves the step waiting, never re-runs
        logger.exception("run EH study %s could not be settled", job_id)
        try:
            release(path, ticket[2], None)  # C4: closed at the whole reservation
        except Exception:  # noqa: BLE001
            logger.exception(
                "run EH study %s: its reservation could not be closed", job_id
            )


def _alive(record):
    worker = record.get("thread")
    return not (
        isinstance(worker, threading.Thread)
        and worker.ident is not None
        and not worker.is_alive()
    )


def settle(path, ticket, job_id, record, begun):
    """Settle the step and its reservation from `record` (F2-F4)."""
    from services import assistant_tasks

    native, status, error = outcome(record, begun)
    spent = charged(record) if native != "unknown" else None
    release(
        path, ticket[2], spent
    )  # F4: what it charged, never capped down; unknown: all it reserved
    result = (
        {"study": STUDY, "started_at": begun, "solves": spent}
        if status == "completed"
        else None
    )
    view = assistant_tasks.settle_job(path, ticket, job_id, native, status, result)
    if view is not None and error:
        from harness import runner

        runner._update(
            path,
            lambda t: next(s for s in t["steps"] if s["id"] == ticket[1]).update(
                error=error
            ),
        )
    return view


def own_record(task, ctx):
    """The EH record the run's latest completed EH step settled from, read on
    `ctx` (F3): None when it was replaced since, or the step never settled."""
    step = next(
        (
            s
            for s in reversed(task.get("steps") or [])
            if is_eh_step(s) and not s.get("superseded")
        ),
        None,
    )
    begun = ((step or {}).get("result") or {}).get("started_at")
    if step is None or step["status"] != "completed" or begun is None or ctx is None:
        return None
    record = _record(ctx)
    return record if record is not None and record.get("started_at") == begun else None


def finite_number(value):
    return (
        None
        if isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        else float(value)
    )


# ── Issue 07 B3a: reconcile and recovery (GK B (32) L4-L6) ───────────────────


def _resident(project_id, ctx_id):
    """The resident context the study's activity binding names, when it is
    still the run project's; None otherwise (never hydrated: a fresh
    context holds no study record)."""
    from services.pypsa_service import PyPSAService

    for key in PyPSAService.list_ids():
        ctx = PyPSAService.get_context(key)
        if (
            ctx is not None
            and id(ctx) == ctx_id
            and str(getattr(ctx, "project_uuid", "")) == project_id
        ):
            return ctx
    return None


def evidence(path, task, step):
    """(found, result) for reconciling an uncertain or failed EH step
    (`assistant_tasks._native_evidence`): only the step's own study, matched
    on the run's project, its context, its job id and its `started_at`
    (L4, F3), and only an outcome that completes it (F2). Its reservation
    settles to what the study charged, once (`settle_engine_solves`): a
    second reconcile or a late settle releases nothing more. A study lost
    with a restart has no record: no evidence, retry only."""
    from harness import job_adapters

    job = step.get("job") or {}
    job_id, begun = job.get("job_id"), job.get("started_at")
    handle = job_adapters._STUDIES.get(job_id) if isinstance(job_id, str) else None
    binding = (
        job_adapters._ACTIVITY_BINDINGS.get(job_id) if handle is not None else None
    )
    if (
        begun is None
        or handle is None
        or handle.study_key != STUDY
        or str(handle.project_uuid) != task["project_id"]
        or binding is None
        or binding[1] != STUDY
        or binding[3] != begun
    ):
        return False, None
    ctx = _resident(task["project_id"], binding[0])
    if ctx is None:
        return False, None
    record = _record(ctx)
    if outcome(record, begun)[1] != "completed":
        return False, None
    spent = charged(record)
    release(path, step.get("attempt_id"), spent, task=task)
    return True, {
        "job_id": job_id,
        "final_status": "completed",
        "study": STUDY,
        "started_at": begun,
        "solves": spent,
    }


TIMED_OUT = "The study passed its follow deadline; its outcome cannot be verified."


def recover(path, ctx, *, instance, now=None, lapsed_ok=False):
    """Each tick, on a takeover and at boot (`lapsed_ok`, ctx None): each
    waiting EH step with no live follower in this process is settled
    uncertain past its `deadline_at` (L6: a step stored without one never
    times out), else followed again while its own record still runs on
    `ctx`, else settled from its own record (`settle`): a missing record
    is uncertain, the whole reservation kept (F4). The study is never
    started again. Only by the run's lease holder, or on a lapsed lease at
    boot. Returns the steps it settled."""
    from harness import runner
    from services import assistant_tasks

    now = time.time() if now is None else now
    task = runner._read(path) or {}
    lease = (task.get("run") or {}).get("runner") or {}
    if lease.get("lease_owner") != instance and not (
        lapsed_ok and lease.get("lease_until", 0) <= now
    ):
        return 0
    mine = ctx is not None and str(getattr(ctx, "project_uuid", "")) == task.get(
        "project_id"
    )
    swept = 0
    for step in task.get("steps", []):
        job = step.get("job") or {}
        if step.get("status") != "waiting" or job.get("kind") != STUDY:
            continue
        ticket = (task["task_id"], step["id"], step.get("attempt_id"))
        job_id, begun = job.get("job_id"), job.get("started_at")
        record = None
        with _FOLLOW_LOCK:  # L5: the check and the registration under one lock
            if following(ticket):
                continue
            deadline = finite_number(job.get("deadline_at"))
            late = deadline is not None and now >= deadline
            if not late:
                record = _record(ctx) if mine else None
                if (
                    begun is not None
                    and record is not None
                    and record.get("started_at") == begun
                    and record.get("status") == "running"
                    and _alive(record)
                ):
                    _start_follower(path, ticket, job_id, ctx, begun)
                    continue
        if late:
            release(path, ticket[2], None)  # unverifiable: the whole reservation
            if (
                assistant_tasks.settle_job(
                    path, ticket, job_id, "timed_out", "uncertain"
                )
                is not None
            ):
                runner._update(
                    path,
                    lambda t, sid=step["id"]: next(
                        s for s in t["steps"] if s["id"] == sid
                    ).update(error=TIMED_OUT),
                )
                swept += 1
            continue
        if settle(path, ticket, job_id, record, begun) is not None:
            swept += 1
    return swept

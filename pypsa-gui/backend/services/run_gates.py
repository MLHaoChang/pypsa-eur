"""Harness run gates: typed rules over native facts (study harness §5).

A gate is a list of rules. Each rule reads one fact from the closed,
versioned registry below; each fact names the existing native function
(and the key of its result) it reads, so a renamed row breaks a test, not a
gate silently. The model never supplies a fact or a verdict: only harness
code calls `evaluate`, and only harness code writes its result.

Fail closed: a fact that is unknown, cannot be read, or reads as None or NaN
is `not_evaluated`, which a `block` rule counts as a fail; so is a reading the
reader itself marks as no evidence (`NoEvidence`: its state and reason kept).

Scorecard rows (issue 05, GK A) gate by verdict: `dc.readiness.<row>` names
one row and `dc.readiness.<family>:` one family. Only `pass` satisfies a
rule; a pass on an assumed row is "within the screening limit", never
verified. Study harness 10a (GK B (36) D2-D4, D8): rows read with the run's
frozen grid-code profile only, and give no evidence once its digest moved; a
block row passing on an assumed input is not evaluated; a warn row reading
`unknown` is not evaluated; ride-through and dynamic rows never pass.

Engine loops (issue 05, PR C; GK A): a result rule with an `engine` block is
driven by `run_margin_loop` or `run_coupling_loop`, never by an agent turn.
Its fact `adequacy.loop.converged` reads the loop record the run's own engine
step for that rule settled with (never the solver state's latest record):
only `met` converges, and the record's goal sources are copied as served.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from typing import Any
from collections.abc import Callable


FACTS_VERSION = 1
OPS = ("eq", "ne", "lt", "le", "gt", "ge", "in", "not_in", "empty", "nonempty", "within")
SEVERITIES = ("block", "warn")


@dataclass(frozen=True)
class Env:
    """What a reader may see: the run's task file, its project and that
    project's own context, passed explicitly (design §6.6, GK B)."""

    task: dict
    ctx: Any  # the run project's ProjectContext
    project: Any  # the project row (`project_registry.resolve_project`)
    project_dir: Any
    cache: dict = field(default_factory=dict, compare=False, hash=False)  # one gate's shared reads
    # Issue 06: an explore case's own gate. `ctx`, `project` and
    # `project_dir` are then the case's child, read from disk and never
    # activated (E6); `case` holds its `project_id` and its own `job_id`
    # (none for a decision-study case, whose `study_run` record it holds).
    case: dict | None = None


def _on(ctx, fn, *args):
    """Call `fn` with `ctx` as the active context, whatever the calling
    request or turn has bound (`study.runner._on_ctx`, turn cell cleared)."""
    import contextvars

    from services.pypsa_service import PyPSAService

    def run():
        PyPSAService._turn_cell.set(None)
        PyPSAService.bind_request_context(ctx)
        return fn(*args)
    return contextvars.copy_context().run(run)


@dataclass(frozen=True)
class Fact:
    reads: str  # "<module>.<function>" (or the task-file key) the value comes from
    key: str  # the key of that function's result the value is
    unit: str | None
    read: Callable[..., Any]
    per_rule: bool = False  # read as read(env, rule), once per rule (an engine rule's own step)


@dataclass(frozen=True)
class NoEvidence:
    """A reading that is no evidence for a verdict: the rule is
    `not_evaluated` with `reason`, and the gate record keeps `value` (the
    state the fact read)."""

    value: Any
    reason: str


def _validate(env):
    from services import chat_tools
    result = _on(env.ctx, chat_tools.validate_network)
    return None if result.get("deferred") else result["errors"]


def _readiness(env):
    from services import workflow_tools
    return _on(env.ctx, workflow_tools.project_readiness, str(env.project.id))["ready"]


def _unserved_islands(env):
    from services import chat_tools
    result = _on(env.ctx, chat_tools.diagnose_network)
    return None if result["verdict"] == "empty" else len(result["islands_without_generation"])


def _solve_job(env):
    """The job whose id the run's own solve step stored, never "the latest
    job" (the run's latest solve step, once L2 re-solved); a job of another
    project is no evidence. Only a queued solve's step counts: the solve
    stage's EH study (issue 07 B2) stores its own job too."""
    from routers.solve_queue import _visible_job_or_404
    from services import chat_tools, project_registry
    if env.case is not None:
        job_id = env.case.get("job_id")  # issue 06: the case's own job, never the run's solve
    else:
        step = next((s for s in reversed(env.task["steps"]) if s["id"].startswith("solve.") and s.get("job")
                     and s["tool"] == "solve_queue_enqueue"), None)
        job_id = None if step is None else step["job"]["job_id"]
    if job_id is None:
        return None
    with chat_tools._acting() as (db, user):
        job = _visible_job_or_404(db, user, job_id)
    return job if job.get("project_key") == project_registry.registry_key(env.project) else None


def _not_applied(job):
    """HH-10 (issue 05, C6f): a captured solve whose result was not applied
    (the inputs changed while it waited) is never read as the project's. A
    captured job counts only when `applied` is exactly True; a missing field
    is no evidence."""
    capture = job.get("capture")
    if job.get("status") == "completed" and capture is not None and capture.get("applied") is not True:
        reason = capture.get("reason") or "not_applied"
        return NoEvidence(reason, f"{reason}: the run's captured solve was not applied to the project")
    return None


def _solve_state(env):
    job = _solve_job(env)
    return None if job is None else (_not_applied(job) or job["status"])


def _solve_status(env):
    job = _solve_job(env)
    if job is None or job["status"] != "completed":
        return None
    return _not_applied(job) or job["condition"]


def _fresh(env):
    """The results describe the current inputs (LC-12, Check answers' guard)."""
    from services import workflow_tools
    return workflow_tools.solve_freshness(env.ctx)["results_current"]


def _template(env):
    from services import flow_service
    return (flow_service.read_template(env.project_dir) or {}).get("id")


def _ic_counterfactual(env):
    """The investment case's counterfactual (study harness 19), read from the
    stored case: `established`, else no evidence with the state and the
    case's own flags or refusal code as the reason."""
    from services import ic_counterfactual as ic
    fact = ic.read(env.ctx, lambda fn: _on(env.ctx, fn))
    if fact["state"] == ic.ESTABLISHED:
        return fact["state"]
    return NoEvidence(fact["state"], ic.reason_words(fact))


def _ic_returns(env):
    """SH-10b: the investment case's project post-tax NPV at the WACC and IRR,
    from the stored case when it is current and its counterfactual
    established; else no evidence with the state and reasons."""
    from services import ic_counterfactual as ic
    fact = ic.read_returns(env.ctx, lambda fn: _on(env.ctx, fn))
    if fact["returns"] is not None:
        return fact["returns"]
    return NoEvidence(fact["state"], ic.reason_words(fact))


def _ic_baseline_keeps(env):
    """The owned existing assets the case's baseline keeps (study harness
    20): the names, else no evidence with the state and the reasons (an
    asset of the template's declared set the baseline drops). Study harness
    12c (B12): the typed state stays in the value, the reason is words."""
    from services import ic_counterfactual as ic
    fact = ic.read_baseline_keeps(env.ctx, lambda fn: _on(env.ctx, fn), _template(env))
    if fact["state"] == ic.ESTABLISHED:
        return fact["kept"]
    return NoEvidence(fact["state"], ic.reason_words(fact))


def _loads_without_series(env):
    """The loads whose demand has no time series (a static `p_set` only): a
    data gap the input loop may fill (design §6.1), never a demand change."""
    from services.adequacy.site_peak import electric_loads
    network = env.ctx.network
    series = {str(c) for c in network.loads_t.p_set.columns}
    # Electric site loads only: an offtake or process load is demand the
    # loop never fills (§6.0), so it is never this gap.
    return sorted(str(name) for name in electric_loads(network) if str(name) not in series)


# ── Engine loops (issue 05, PR C) ───────────────────────────────────────────

LOOP_FACT = "adequacy.loop.converged"
ENGINE_TOOLS = {"run_margin_loop": "margin_loop", "run_coupling_loop": "coupling_loop"}
RESTORES = ("base", "final")
# The loops' shared refusal of a network with no sampleable electrical
# generator is `NOTHING_TO_SAMPLE`, defined once in the adequacy layer (GK A
# (19) SH11-L6); the typed reason the gate reads it as (SH11-L1). The
# refusal names no cause, so neither does the reading (GK A (19) F1): a
# network's generators may lack outage data, or its outages may sit on Links.
LOOP_NOTHING_TO_SAMPLE = "loop_nothing_to_sample"
NOTHING_TO_SAMPLE_DETAIL = ("the loop found no outage to sample: no electrical generator has outage data "
                            "(unavailability or repair time). Outages carried on Links, as on the H2 hubs, "
                            "cannot be sampled yet.")
# Study harness 12c (GK B (47) Q4): a loop the envelope's cap cut short. It is
# the engine's own refusal kind (`run_envelope.reserve_engine`), read as not
# evaluated with words: never met, and its last unconverged value is no result.
LOOP_BUDGET_SPENT = "budget_spent"
LOOP_REFUSED_WORDS = ("no solve was left in the envelope for the engine loop, so the rule is not evaluated; "
                      "it never counts as met")


def _capped_words(granted, wanted):
    return (f"the envelope's solves left the engine loop {granted} of its {wanted} solves and it ran out "
            "before it converged, so the rule is not evaluated; it never counts as met, and the loop's "
            "last value is not a result")


# The fields of a finished loop's record a settled engine step keeps, and the
# gate shows: the verdict, what it cost, how the network was left, and the
# goal it was judged against with where each part came from.
LOOP_FIELDS = ("status", "solves_used", "probe_solves", "restore", "base_restored", "base_restore_status",
               "target_lole_h", "horizon_years", "draws", "basis", "goal_sources", "lever_star", "eps_star", "confident",
               "started_at", "finished_at", "error", "network_revision")


def converged(record) -> bool:
    """A loop record meets its target only when it says `met`; every other
    status, a known terminal one or not, does not (GK A (11)(a))."""
    return isinstance(record, dict) and record.get("status") == "met"


@dataclass(frozen=True)
class Looped:
    """An engine reading: whether the loop converged, and the record it rests on."""

    value: bool
    record: dict


def engine_step(task, rule_id):
    """The run's latest engine step for `rule_id` (a superseded one is history)."""
    return next((s for s in reversed(task.get("steps") or [])
                 if s.get("engine") == rule_id and not s.get("superseded")), None)


def _loop_converged(env, rule):
    from services.adequacy.coupling import NOTHING_TO_SAMPLE
    step = engine_step(env.task, rule["id"])
    if step is None:
        return NoEvidence("not_started", "the engine loop for this rule has not run")
    if step["status"] in ("pending", "running", "waiting"):
        return NoEvidence(step["status"], "the engine loop has not finished")
    loop = (step.get("result") or {}).get("loop")
    if not isinstance(loop, dict):
        reason = step.get("error") or "the engine loop left no record"
        if NOTHING_TO_SAMPLE in str(reason):
            # GK A (19) SH11-L1: the runner's own refusal, typed (F1: cause-neutral).
            return NoEvidence(LOOP_NOTHING_TO_SAMPLE, NOTHING_TO_SAMPLE_DETAIL)
        if reason == LOOP_BUDGET_SPENT:  # the reservation found no solve left: the loop never started
            return NoEvidence(LOOP_BUDGET_SPENT, LOOP_REFUSED_WORDS)
        return NoEvidence((step.get("job") or {}).get("outcome") or step["status"], str(reason)[:240])
    if loop.get("applied") is False:  # a user edit landed while the loop ran (H3): not this project's
        return NoEvidence("inputs_changed", "the inputs changed while the engine loop ran")
    problem = _goal_problem(env, loop)
    if problem:
        return NoEvidence(*problem)
    granted = (step.get("job") or {}).get("max_solves")
    wanted = (rule.get("engine") or {}).get("max_solves")
    if loop.get("status") == "budget_exhausted" and isinstance(granted, int) and isinstance(wanted, int):
        from services.adequacy.coupling import MAX_LOOP_SOLVES
        wanted = min(wanted, MAX_LOOP_SOLVES)
        if granted < wanted:  # GK B (47) Q4: the cap, not the loop's own budget, ended it
            return NoEvidence(LOOP_BUDGET_SPENT, _capped_words(granted, wanted))
    return Looped(converged(loop), {k: loop[k] for k in LOOP_FIELDS if k in loop})


_UNREADABLE = object()


def _granted_goal(env):
    """The goal the envelope froze (`{"value", "unit", "source"}`); None when
    the run has no envelope file, or its envelope is only proposed or froze no
    goal; `_UNREADABLE` when the file is there but is not a whole envelope or
    its goal is malformed (GK B (46) G1/G2). Any state past `proposed` counts:
    a spent (`exhausted`), `expired` or `revoked` grant still froze its goal."""
    from services import run_envelope
    task_id = env.task.get("task_id")
    if env.project_dir is None or not isinstance(task_id, str):
        return None
    task_path = Path(env.project_dir) / "assistant_tasks" / f"{task_id}.json"
    envelope = run_envelope._load(task_path)
    if envelope is None:
        return _UNREADABLE if run_envelope.path_for(task_path).exists() else None
    if envelope.get("state") == "proposed" or "goal" not in envelope:
        return None
    goal = envelope["goal"]
    return goal if isinstance(goal, dict) else _UNREADABLE


GOAL_REL_TOL, GOAL_ABS_TOL = 1e-9, 1e-9  # the loop target vs the frozen goal on the horizon basis


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _goal_problem(env, loop):
    """`(reason, detail)` when the loop did not judge the goal the grant froze,
    else None (also when the grant froze no goal). GK B (46): nothing defaults;
    an unreadable input is `goal_unreadable`, never a match. The loop's
    `horizon_years` is `metrics.horizon_years(n)` (both runners), and its target
    is the goal's h/yr value put on that horizon by `reliability_goal.loop_goal`."""
    goal = _granted_goal(env)
    if goal is None:
        return None
    if goal is _UNREADABLE:
        return ("goal_unreadable", "the granted envelope or its frozen goal could not be read")
    from services.reliability_goal import loop_goal
    value, years, target = goal.get("value"), loop.get("horizon_years"), loop.get("target_lole_h")
    if (goal.get("unit") != "h/yr" or not _number(value) or value < 0 or not _number(years) or years <= 0
            or not _number(target)):
        return ("goal_unreadable", "the frozen goal or the engine loop's target or horizon could not be read")
    expected, _, _ = loop_goal(None, {"target_lole_h": {"value": value, "source": goal.get("source")},
                                      "mc_draws": {"value": 1, "source": ""}}, float(years))
    if not math.isclose(target, expected, rel_tol=GOAL_REL_TOL, abs_tol=GOAL_ABS_TOL):
        return ("goal_mismatch", "the engine loop ran against a different LOLE target than the goal the grant froze")
    sources = loop.get("goal_sources")
    if not isinstance(sources, dict) or sources.get("target_lole_h") != goal.get("source"):
        return ("goal_mismatch", "the engine loop's LOLE target came from a different source than the grant's goal")
    return None


def check_engine(rule):
    """An engine rule (§6.2, C9): the converged fact, `eq true`, `block`, and
    an engine block naming a loop tool, its solve bound and its restore. The
    goal (target, draws) is never the profile's: the project's goal sets it."""
    from services.adequacy.coupling import MAX_LOOP_SOLVES
    engine = rule.get("engine")
    if (engine is None) != (rule["fact"] != LOOP_FACT):
        raise RuleError(f"Rule {rule['id']}: {LOOP_FACT} is read by an engine rule only, and an engine rule "
                        f"reads {LOOP_FACT}.")
    if engine is None:
        return rule
    if (rule["op"], rule.get("value"), rule["severity"]) != ("eq", True, "block"):
        raise RuleError(f"Rule {rule['id']}: an engine rule is `eq true` and `block`.")
    max_solves = engine.get("max_solves") if isinstance(engine, dict) else None
    if not isinstance(engine, dict) or not {"tool", "max_solves"} <= set(engine) <= {"tool", "max_solves", "restore"} \
            or engine["tool"] not in ENGINE_TOOLS or isinstance(max_solves, bool) or not isinstance(max_solves, int) \
            or not 1 <= max_solves <= MAX_LOOP_SOLVES or engine.get("restore", "base") not in RESTORES:
        raise RuleError(f"Rule {rule['id']}: an engine block names tool ({', '.join(ENGINE_TOOLS)}), "
                        f"max_solves 1-{MAX_LOOP_SOLVES} and optional restore ({', '.join(RESTORES)}).")
    return {**rule, "engine": {"tool": engine["tool"], "max_solves": max_solves,
                               "restore": engine.get("restore", "base")}}


def _run(key):
    return lambda env: env.task.get("run", {}).get(key)


def _explore_unsettled(env):
    """Issue 06: the run's admitted cases not yet settled (passed, failed
    with its rules, or not runnable with its reason)."""
    from harness import run_explore
    return run_explore.unsettled(env.task.get("run", {}))


def _explore_study_status(env):
    """Issue 06: a decision-study case's run record status, as the study
    runner stored it (`completed` when every option solved)."""
    record = (env.case or {}).get("study_run")
    return None if not isinstance(record, dict) else record.get("status")


def _profile(env):
    from services import run_profiles
    return run_profiles.resolve(env.task["run"]["profile"])


def _report_status(env):
    """Issue 07: the run's own report job's outcome, `ready` only when its
    saved document reads back (C3: no job, no record: not evaluated)."""
    from harness import run_report
    return run_report.status(env.task, env.project_dir)


def _report_unsupported_listed(env):
    """Issue 07 (SC-15): every profile chapter established or named a gap."""
    from harness import run_report
    return run_report.unsupported_listed(env.task, env.project_dir, _profile(env))


def _granted_eh_study(env):
    """The EH study's args the grant froze (envelope mode), so readiness
    predicts the run the study will make, its granted budget included."""
    from services import run_envelope
    task_id = env.task.get("task_id")
    if env.project_dir is None or not isinstance(task_id, str):
        return None
    envelope = run_envelope._load(Path(env.project_dir) / "assistant_tasks" / f"{task_id}.json")
    if envelope is None or envelope.get("state") != "granted":
        return None
    return envelope.get("eh_study") or None


def _eh_readiness_ready(env):
    """Issue 07 B2: the EH readiness preflight for the args the study would
    run with, on the run's ctx. Ready only when the pack applies and every
    requested stage is predicted to run; anything else (and no read) is not."""
    from harness import run_eh
    from services import chat_tools
    if env.ctx is None:
        return None
    profile = _profile(env)
    frozen = _granted_eh_study(env) or run_eh.freeze(env.ctx, profile, run_eh.solves_cap(profile, None))
    args = frozen["args"]
    result = _on(env.ctx, chat_tools.get_eh_readiness, args["archetype"], args["budget_solves"],
                 args.get("stages"), args.get("dtc_attribution"), args.get("pack_overrides") or None)
    rows = result.get("stages") if isinstance(result, dict) else None
    if not isinstance(rows, list) or result.get("truncated") or not (result.get("import") or {}).get("applied"):
        return None if not isinstance(rows, list) or result.get("truncated") else False
    requested = [r for r in rows if isinstance(r, dict) and r.get("prediction") != "not_requested"]
    return bool(requested) and all(r.get("prediction") == "run" for r in requested)


def _eh_energization_min_mw(env):
    """F7: the least MW the connection is energized with over the growth
    stages (the template's `energization[].available_import_mw`, one per
    stage year); a stage without a finite number (missing, NaN, text) makes
    it None."""
    from harness import run_eh
    from services.flow_service import read_template
    template = read_template(env.project_dir) if env.project_dir is not None else None
    stages = template.get("energization") if isinstance(template, dict) else None
    if not isinstance(stages, list) or not stages:
        return None
    values = [run_eh.finite_number(s.get("available_import_mw")) if isinstance(s, dict) else None for s in stages]
    return None if any(v is None for v in values) else min(values)


def _eh_record(env):
    from harness import run_eh
    record = run_eh.own_record(env.task, env.ctx)
    if record is None:
        return NoEvidence("not_own", "the run's own EH study record is missing or was replaced")
    # W1: the record's revision, captured at study start, against the run
    # project's current one. The margin loop's restore="base" does not bump
    # it (only a restore="final" hold does), so the run's own LOLE loop leaves
    # the record readable; a user edit since the study does not.
    from services import dirty_state, study_state
    if study_state.edited_since(record, revision=dirty_state.revision(env.ctx)) is not False:
        return NoEvidence("inputs_changed", "the network was edited after the run's EH study, or the study recorded no revision")
    return record


def _eh_study_status(env):
    """F2, F3: `done` with every requested stage run, from the record the
    run's own step settled from; another or a replaced record is no evidence."""
    from harness import run_eh
    record = _eh_record(env)
    if isinstance(record, NoEvidence):
        return record
    if record.get("status") == "done" and run_eh.skipped(record):
        return NoEvidence("stage_skipped", "; ".join(run_eh.skipped(record))[:240])
    return record.get("status")


def _eh_review_blockers(env):
    """F3, F8: the high-severity findings of the review of the run's own
    record (never the process's latest). A review that errors or is cut
    short reads None."""
    from services.adequacy.eh_review import review_report
    record = _eh_record(env)
    if isinstance(record, NoEvidence):
        return record
    report = record.get("report")
    if not isinstance(report, dict):
        return None
    out = review_report(report, record)
    findings = out.get("findings") if isinstance(out, dict) else None
    if not isinstance(findings, list) or out.get("truncated"):
        return None
    return sorted(str(f.get("id")) for f in findings if isinstance(f, dict) and f.get("severity") == "high")


def _kpi_unserved_mwh(env):
    """F6: unserved energy of the run's own solve, on the run's ctx: the
    solve job's capture is applied and its exact input identity is the one
    the live results were solved from, and those results match the inputs
    (`solve_matches_inputs`). Energy uses the solve's snapshot weightings."""
    from harness import lost_load_calculations as lost_load

    def read(ctx):
        if len(ctx.network.investment_periods):
            return _unserved_by_period(ctx)
        try:
            return lost_load.source(ctx, "network")
        except ValueError as exc:
            return NoEvidence(str(exc), f"{exc}: unserved energy cannot be read")
    raw = _own_solve(env, read)
    if not isinstance(raw, dict):
        return raw
    facts, _ = lost_load.calculate(raw, {"profile": lost_load.PROFILE, "entity_id": "network", "metrics": ["lost_load_energy"]})
    return facts[0]["value"] if facts else None


def _own_solve(env, read, typed=False):
    """F6: `read(ctx)` under the network guard, on the run's ctx, only when
    the run's own solve job finished, its capture is applied and the live
    results carry its exact input identity; else None or no evidence. With
    `typed` (the H2 readers, SH-11 H1), a missing or unfinished solve is
    typed no evidence rather than None (`kpi.unserved_mwh` keeps None)."""
    from harness.durable import network_guard
    if env.ctx is None or env.case is not None:
        return None
    job = _solve_job(env)
    if job is None:
        return NoEvidence("not_solved", "the run has no solve of its own") if typed else None
    if job["status"] != "completed":
        return (NoEvidence(f"solve_{job['status']}", f"the run's solve is {job['status']}, not completed")
                if typed else None)
    refused = _not_applied(job)
    if refused is not None:
        return refused
    exact = (job.get("capture") or {}).get("exact")
    if not exact:
        return NoEvidence("no_capture", "the run's solve recorded no input identity")

    def guarded():
        with network_guard():
            if (getattr(env.ctx.network, "meta", None) or {}).get("assistant_solve_exact_input_fingerprint") != exact:
                return NoEvidence("other_solve", "the live results are not the run's own solve")
            return read(env.ctx)
    return _on(env.ctx, guarded)


def _unserved_by_period(ctx):
    """Issue 07 B3b (GK B (33) P4): unserved energy of a multi-period solve,
    under the network lock. Per investment period, the shed MW of every
    captured Load times that snapshot's generator weighting, summed within
    the period; never `investment_period_weightings` and never a sum over
    periods. The value the rule compares is the worst period's; its rows
    name every period and mark the worst. The F6 identity checks of
    `lost_load_calculations.source` hold here too."""
    import pandas as pd

    from harness import lost_load_calculations as lost_load
    from harness.calculations import MAX_SNAPSHOTS, solve_matches_inputs
    from services.adequacy.metrics import electrical_columns
    n = ctx.network
    index = n.snapshots
    if not isinstance(index, pd.MultiIndex) or index.nlevels != 2 or not 1 <= len(index) <= MAX_SNAPSHOTS:
        return NoEvidence("unsupported_snapshot_scope", "unsupported_snapshot_scope: unserved energy cannot be read")
    frame = (ctx.solver_state.get("last_lost_load") or {}).get("lost_load_t")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return NoEvidence("no_lost_load_capture", "no_lost_load_capture: unserved energy cannot be read")
    if not frame.index.equals(index) or not frame.columns.is_unique:
        return NoEvidence("misaligned_source", "misaligned_source: unserved energy cannot be read")
    columns = [str(c) for c in frame.columns]
    if any(c not in n.loads.index for c in columns):
        return NoEvidence("legacy_lost_load_capture", "legacy_lost_load_capture: unserved energy cannot be read")
    if len(columns) > lost_load.MAX_LOADS:
        return NoEvidence("unsupported_load_population",
                          "unsupported_load_population: unserved energy cannot be read")
    if not electrical_columns(n, columns):
        return NoEvidence("no_electrical_loads", "no_electrical_loads: unserved energy cannot be read")
    if not solve_matches_inputs(ctx):
        return None  # as the single-period read: a stale solve has no value
    weights = n.snapshot_weightings["generators"].reindex(index)
    rows = []
    for period in index.get_level_values(0).unique():
        mask = index.get_level_values(0) == period
        w = [float(v) for v in weights[mask]]
        shed = [[float(v) for v in frame.loc[mask, c]] for c in frame.columns]
        if not all(math.isfinite(v) for v in w + [x for col in shed for x in col]) or any(v < 0 for v in w):
            return None  # any missing shed or weight makes the energy unavailable
        totals = [math.fsum(max(col[i], 0.0) for col in shed) for i in range(len(w))]
        rows.append({"period": int(period), "unserved_mwh": math.fsum(a * t for a, t in zip(w, totals)),
                     "assumed": False})
    return _worst(rows, "unserved_mwh")


def _worst(rows, field, key=None):
    """Per-period rows scored by their worst: the row `key` ranks highest
    (by default the largest `field`) is marked `worst` and gives the value."""
    worst = max(rows, key=key or (lambda r: r[field]))
    for r in rows:
        r["worst"] = r is worst
    return Scored(value=worst[field], rows=rows)


# Study harness 11 (design §9.3 "eh_h2_hub"): the H2 hub's offtake and
# electrolyser facts, read from the run's own solve (`_own_solve`). The
# offtake is demand, never a lever (§6.0): its shed is the per-Load VoLL
# slack the solve captured (`solver/assumptions.py` P6(b)).
H2_CARRIER = "H2"
ELECTROLYSER_ROLE, ELECTROLYSER_CARRIER = "electrolyser", "electrolysis"


def _offtake_load(n):
    """The hub's one H2 offtake: the Load on a bus whose carrier is H2."""
    loads = [str(name) for name, bus in n.loads["bus"].items()
             if bus in n.buses.index and n.buses.at[bus, "carrier"] == H2_CARRIER]
    if len(loads) != 1:
        return NoEvidence(f"offtake_loads:{len(loads)}", f"the network has {len(loads)} H2 offtake loads, not one")
    return loads[0]


def _period_rows(n, values):
    """Per investment period, `values(mask, weights, period)` (None: not
    readable; a NoEvidence from any period is the answer)."""
    index = n.snapshots
    weights = n.snapshot_weightings["generators"].reindex(index)
    rows = []
    for period in index.get_level_values(0).unique():
        mask = index.get_level_values(0) == period
        value = values(mask, weights[mask], int(period))
        if value is None or isinstance(value, NoEvidence):
            return value
        rows.append({"period": int(period), **value})
    return rows


# SH-11 H1: the H2 readers type a solve whose results predate the inputs.
STALE_SOLVE = NoEvidence("stale_solve", "the inputs changed since the solve: its results are not this network's")


def _offtake_shed_hours(env):
    """Hours (generator weights) in which the offtake's VoLL slack shed more
    than `SHED_THRESHOLD_MW`. Multi-period: one row per investment period,
    the value the worst period's."""
    import pandas as pd

    from harness import lost_load_calculations as lost_load
    from harness.calculations import solve_matches_inputs

    def read(ctx):
        n = ctx.network
        load = _offtake_load(n)
        if isinstance(load, NoEvidence):
            return load
        if not len(n.investment_periods):
            try:
                raw = lost_load.source(ctx, load)
            except ValueError as exc:
                return NoEvidence(str(exc), f"{exc}: the offtake's shed cannot be read")
            if not raw["valid_solve"]:
                return STALE_SOLVE
            facts, _ = lost_load.calculate(raw, {"profile": lost_load.PROFILE, "entity_id": load,
                                                 "metrics": ["shed_hours"]})
            return facts[0]["value"] if facts else None
        frame = (ctx.solver_state.get("last_lost_load") or {}).get("lost_load_t")
        if not isinstance(frame, pd.DataFrame) or not frame.index.equals(n.snapshots) or load not in frame.columns:
            return NoEvidence("no_lost_load_capture", "no_lost_load_capture: the offtake's shed cannot be read")
        if not solve_matches_inputs(ctx):
            return STALE_SOLVE

        def hours(mask, w, _period):
            shed = [float(v) for v in frame.loc[mask, load]]
            w = [float(v) for v in w]
            if not all(math.isfinite(v) for v in shed + w) or any(v < 0 for v in w):
                return None
            return {"shed_hours": math.fsum(a for a, s in zip(w, shed) if s > lost_load.SHED_THRESHOLD_MW),
                    "assumed": False}
        rows = _period_rows(n, hours)
        if not rows or isinstance(rows, NoEvidence):
            return rows or None
        return _worst(rows, "shed_hours")
    return _own_solve(env, read, typed=True)


def _electrolysers(n):
    """The electrolyser fleet: Links whose `eh_role` says so, else whose carrier is electrolysis."""
    role = n.links["eh_role"] if "eh_role" in n.links.columns else None
    named = [str(link) for link in n.links.index if role is not None and role.get(link) == ELECTROLYSER_ROLE]
    return named or [str(link) for link in n.links.index if n.links.at[link, "carrier"] == ELECTROLYSER_CARRIER]


def _utilisation(n, links, mask, w):
    """The fleet's capacity factor on the input side, the one
    `results/asset_economics.link_capacity_factor` gives a Link: Σ p0·w over
    Σ capacity·Σ w, the capacity the adequacy engines read
    (`copt.solved_capacity`: a built Link's p_nom, an extendable one's
    p_nom_opt). None when the fleet has no capacity; no evidence when any
    hour's dispatch leaves [0, capacity] (the capacity is not the one the
    dispatch was solved against)."""
    from services.adequacy.copt import solved_capacity
    from services.results.asset_economics import link_capacity_factor
    w = [float(v) for v in w]
    if not links or not all(math.isfinite(v) and v >= 0 for v in w):
        return None
    p0 = n.links_t.p0.reindex(columns=links)
    p0 = p0[mask] if mask is not None else p0
    energy = 0.0
    for link in links:
        cap = solved_capacity(n.links.loc[link])
        flows = [float(v) for v in p0[link]]
        if not all(math.isfinite(v) for v in flows):
            return None
        tol = 1e-6 * max(cap, 1.0)
        if any(v < -tol or v > cap + tol for v in flows):
            return NoEvidence("dispatch_exceeds_capacity",
                              f"{link}'s dispatch leaves [0, {cap:g}] MW: its capacity is not the solve's")
        energy += math.fsum(a * v for a, v in zip(w, flows))
    capacity = math.fsum(solved_capacity(n.links.loc[link]) for link in links)
    return link_capacity_factor(energy, capacity, math.fsum(w))


def _electrolyser_utilisation(env, rule):
    """The fleet's utilisation (p.u.). Multi-period: one row per investment
    period over the Links active in it, each with its verdict against the
    rule's band; the value the period furthest outside it."""
    from harness.calculations import solve_matches_inputs
    lo, hi = rule["value"]

    def read(ctx):
        n = ctx.network
        fleet = _electrolysers(n)
        if not fleet:
            return NoEvidence("no_electrolyser", "the network has no electrolyser")
        if not solve_matches_inputs(ctx):
            return STALE_SOLVE  # a capacity edited since the solve is not the dispatch's
        if not len(n.investment_periods):
            return _utilisation(n, fleet, None, n.snapshot_weightings["generators"].reindex(n.snapshots))

        def one(mask, w, period):
            active = n.get_active_assets("Link", period)
            u = _utilisation(n, [link for link in fleet if active.get(link, False)], mask, w)
            if u is None or isinstance(u, NoEvidence):
                return u
            return {"utilisation": u, "verdict": "pass" if lo <= u <= hi else "fail", "assumed": False}
        rows = _period_rows(n, one)
        if not rows or isinstance(rows, NoEvidence):
            return rows or None
        return _worst(rows, "utilisation", key=lambda r: (max(lo - r["utilisation"], r["utilisation"] - hi, 0.0),
                                                          -r["utilisation"]))
    return _own_solve(env, read, typed=True)


def _handoff_kpis_read(env):
    """Issue 07 (C14): every headline KPI read back natively, on the run's context."""
    from harness import run_handoff
    if env.ctx is None:
        return None
    return _on(env.ctx, run_handoff.kpis_read, env.task, _profile(env))


# Study harness 10a (GK B (36) D1, Q1, Q2): the data-centre run's grid-code
# and campus facts, read through `services/run_actions.py`.
FREQUENCY_PARAM = "grid.frequency_hz"


def _dc_gridcode_published(env):
    """D1: a published profile the run can be held to (`run_actions.choose`)."""
    from services import run_actions
    if env.project is None:
        return None
    try:
        run_actions.choose(env.project)
    except run_actions.FreezeError as exc:
        return NoEvidence(exc.state, str(exc))
    return True


def _dc_gridcode_frequency(env):
    """Q1 = A: the campus's nominal frequency equals the grid frequency the
    user gave at intake (`grid.frequency_hz`, Hz). No intake row or no
    campus file is no evidence (Q2: the user drafts the campus)."""
    from services import campus_electrical_service as ce
    from services.solver.island import campus_f_hz
    # GK B (36): only a row the user confirmed; never campus_f_hz, a template or a code default.
    rows = ((env.task.get("run") or {}).get("intake") or {}).get("rows") or []
    given = next((r.get("value") for r in rows if r.get("param") == FREQUENCY_PARAM
                  and r.get("status") == "confirmed" and not r.get("write")), None)
    if given not in ("50", "60"):
        return NoEvidence("not_given", "state the grid frequency (Hz)")
    if env.project_dir is None or not (Path(env.project_dir) / ce.SUBDIR / ce.CAMPUS_FILE).is_file():
        return NoEvidence("campus_missing", "draft the campus")
    return float(campus_f_hz(env.project_dir)) == float(given)


def _dc_gridcode_unchanged(env):
    """D1: the run's frozen profile is still published with the same content."""
    from services import run_actions
    code = run_actions.frozen_code(env.task)
    if code is None or env.project is None:
        return NoEvidence("not_frozen", "the run froze no grid-code profile")
    return run_actions.unchanged(env.project, code)


def _dc_campus_study(env):
    """The run's own campus job: `done` only once its step settled completed."""
    from services import run_actions
    step = run_actions.latest(env.task, run_actions.CAMPUS)
    if step is None or step["status"] not in ("completed", "failed"):
        return None
    return "done" if step["status"] == "completed" else ((step.get("job") or {}).get("outcome") or "failed")


def _dc_campus_contingency(env):
    """SH-10b (GK A CT-1..CT-6): the checks of the run's own campus study
    that fail in the intact case or a transformer N-1 after the taps and
    the compensation it bought (DC13-SCOPE; no redispatch), each with its
    case, period, value and limit; [] when none fails. No evidence unless
    the study is the run's own, held to its frozen profile, on the network
    it read, with every check re-solved and read on a stated band (D3)."""
    from services import dirty_state, run_actions
    if env.project is None:
        return NoEvidence("not_run", "the run has no project")
    if not hasattr(env.ctx, "network_revision"):
        return NoEvidence("edited", "the network's edit counter cannot be read")
    out = run_actions.contingency(env.project, env.task, dirty_state.revision(env.ctx))
    if "failing" in out:
        return Measured(out["failing"], out["measures"])
    return NoEvidence(out["state"], out["reason"])


# ── Study harness 12b: the microgrid island facts ─────────────────────────────


@dataclass(frozen=True)
class Detailed:
    """A reading with the named items it rests on (shortfalls, hours, units),
    carried into the gate record as `detail`; the rule compares `value`."""

    value: Any
    detail: list
    basis: str | None = None  # what the value counts, shown beside it (C5)


MG_NO_FUEL = "the gensets draw no modelled fuel, so fuel autonomy is not checked"


def _mg_settings(env):
    """The project's validated island settings, or NoEvidence by name (M10)."""
    from services import run_actions
    if env.project is None:
        return NoEvidence("not_run", "the run has no project")
    try:
        return run_actions.island_settings(run_actions._project_dir(env.project))
    except run_actions.FreezeError as exc:
        return NoEvidence(exc.state, str(exc))


def _mg_survival(env):
    """The run's own island check (12b): `survives` or `fails` (each
    shortfall named) while it is current on the live network and island
    settings (B4); no evidence otherwise (B5, B7, M10). The basis names each
    step of the project's own solve the check did not take (C1)."""
    from services import dirty_state, run_actions
    island = _mg_settings(env)
    if isinstance(island, NoEvidence):
        return island
    if not hasattr(env.ctx, "network_revision"):
        return NoEvidence("edited", "the network's edit counter cannot be read")
    out = run_actions.mg_outcome(env.task, dirty_state.revision(env.ctx), run_actions.settings_digest(island))
    if "no_evidence" in out:
        return NoEvidence(out["no_evidence"], out["reason"])
    skipped = [run_actions.MG_SKIPPED.get(k, str(k)) for k in out["result"]["skipped"]]
    basis = ("The island check solved without " + "; ".join(skipped) + ".") if skipped else None
    return Detailed(out["state"], [f"{s['row']}: {s['sentence']}" for s in out["result"]["shortfalls"]], basis)


def _mg_fuel(env):
    """GK A Q5: fuel autonomy is not modelled, so the rule never reads it."""
    return NoEvidence("not_modelled", MG_NO_FUEL)


def _mg_tie(env):
    """M12: the island's tie on the live network: `open`, `closed` (each
    tie carrying named) or `no_tie`; no evidence by name otherwise."""
    from services.adequacy import island_survival
    island = _mg_settings(env)
    if isinstance(island, NoEvidence):
        return island
    try:
        out = island_survival.tie_state(env.ctx.network, island)
    except Exception as exc:  # noqa: BLE001 — a network that cannot be read is no evidence, by name (M10)
        return NoEvidence("unreadable", f"the island's tie cannot be read ({type(exc).__name__})")
    state = out.get("state")
    if state == "no_island":
        return NoEvidence("no_island", "no bus of the network is in the island")
    if state not in ("open", "closed", "no_tie"):
        return NoEvidence("unreadable", "the island's tie cannot be read")
    return Detailed(state, [str(t) for t in out.get("carrying") or []])


def _mg_critical(env):
    """M12: the island's critical loads on the live network: `tagged` when
    the sidecar and the network name the same loads; `none` or `mismatch`
    (each load named) otherwise."""
    from services.adequacy import island_survival
    island = _mg_settings(env)
    if isinstance(island, NoEvidence):
        return island
    try:
        out = island_survival.critical_state(env.ctx.network, island)
    except Exception as exc:  # noqa: BLE001 — M10
        return NoEvidence("unreadable", f"the critical loads cannot be read ({type(exc).__name__})")
    state = out.get("state")
    if state not in ("tagged", "none", "mismatch"):
        return NoEvidence("unreadable", "the critical loads cannot be read")
    detail = [f"{name}: on the network only" for name in out.get("network_only") or []]
    detail += [f"{name}: in the island settings only" for name in out.get("sidecar_only") or []]
    return Detailed(state, detail)


def _mg_critical_served(env):
    """GK A Q4, B6: the critical load's unserved energy on island, MWh,
    summed over the islanding contingencies of the reliability study's
    off-grid stress (`dtc.run_dtc_stress`, weighted there by the snapshot
    weightings), from the run's own EH study record while it is current;
    each contingency named. A contingency left unsolved is no evidence.
    B6's currency is the EH record's revision alone: the stress reads the
    network's own tags, never the island sidecar."""
    record = _eh_record(env)
    if isinstance(record, NoEvidence):
        return record
    section = ((record.get("report") or {}).get("sections") or {}).get("dtc")
    if not isinstance(section, dict):
        return NoEvidence("unreadable", "the EH study record has no off-grid stress section")
    status = section.get("status")
    if status != "ok":
        return NoEvidence(str(status or "unreadable"),
                          str(section.get("note") or f"the off-grid stress reads {status}")[:240])
    payload = section.get("payload") or {}
    rows = payload.get("contingencies")
    if not isinstance(rows, list) or not rows:
        return NoEvidence("unreadable", "the off-grid stress names no contingency")
    # Study harness 12c (Q1-i): a stress that covered only some of the K
    # contingencies the run's study was frozen with is never read.
    held = _eh_contingencies(env.task)
    names = [str((row or {}).get("contingency")) for row in rows]
    if payload.get("budget_exhausted") or payload.get("aborted") or (held is not None and names != held):
        return NoEvidence("partial", f"the off-grid stress covered {', '.join(names)} of "
                                     f"{', '.join(held) if held is not None else 'its contingencies'}")
    total, detail = 0.0, []
    for row in rows:
        name = str((row or {}).get("contingency"))
        value = (row or {}).get("critical_unserved_mwh")
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                or (row or {}).get("status") not in ("ok", "optimal")):
            return NoEvidence("not_solved", f"the off-grid stress did not solve the contingency {name}")
        total += float(value)
        detail.append(f"{name}: {float(value):.4g} MWh")
    return Detailed(total, detail)


def _eh_contingencies(task):
    """The islanding contingencies the run's latest EH step was frozen with
    (study harness 12c G1), or None when it counted none."""
    from harness import run_eh
    step = next((s for s in reversed(task.get("steps") or []) if run_eh.is_eh_step(s) and not s.get("superseded")),
                None)
    held = ((step or {}).get("frozen") or {}).get("contingencies")
    return list(held["links"]) if isinstance(held, dict) and isinstance(held.get("links"), list) else None


def _mg_frequency(env):
    """M11, B6: the island hours the run's own campus run fails the frequency
    judge in, each (period, hour) once and named; a count of hours of the
    campus run's year, not weighted (`run_actions.MG_FREQUENCY_BASIS`)."""
    from services import dirty_state, run_actions
    if env.project is None:
        return NoEvidence("not_run", "the run has no project")
    if not hasattr(env.ctx, "network_revision"):
        return NoEvidence("edited", "the network's edit counter cannot be read")
    out = run_actions.mg_frequency(env.project, env.task, dirty_state.revision(env.ctx))
    if "hours" not in out:
        return NoEvidence(out["state"], out["reason"])
    return Detailed(len(out["hours"]), [f"period {p} hour {h}" for p, h in out["hours"]],
                    run_actions.MG_FREQUENCY_BASIS)


FACTS: dict[str, Fact] = {
    # Harness facts: the run block itself, written only by harness code.
    "brief.valid": Fact("task.brief", "brief", None, lambda env: bool(env.task.get("brief"))),
    "profile.resolved": Fact("task.run", "profile", None, lambda env: bool(_run("profile")(env))),
    "run.mode": Fact("task.run", "mode", None, _run("mode")),
    "explore.study.status": Fact("services.study.runner.get_study_run", "status", None, _explore_study_status),
    "explore.unsettled": Fact("harness.run_explore.unsettled", "cases", "count", _explore_unsettled),
    "project.exists": Fact("services.project_registry.resolve_project", "id", None,
                           lambda env: env.project is not None),
    "project.template": Fact("services.flow_service.read_template", "id", None, _template),
    # Core input facts.
    "validate.errors": Fact("services.chat_tools.validate_network", "errors", "count", _validate),
    "readiness.ready": Fact("services.workflow_tools.project_readiness", "ready", None, _readiness),
    "loads.without_series": Fact("services.run_gates._loads_without_series", "names", None, _loads_without_series),
    "diagnose.unserved_islands": Fact("services.chat_tools.diagnose_network", "islands_without_generation",
                                      "count", _unserved_islands),
    # Core result facts.
    "solve.state": Fact("routers.solve_queue._visible_job_or_404", "status", None, _solve_state),
    "solve.status": Fact("routers.solve_queue._visible_job_or_404", "condition", None, _solve_status),
    "fresh": Fact("services.workflow_tools.solve_freshness", "results_current", None, _fresh),
    "ic.counterfactual": Fact("services.ic_counterfactual.read", "state", None, _ic_counterfactual),
    "ic.returns": Fact("services.ic_counterfactual.read_returns", "returns", None, _ic_returns),
    "ic.baseline_keeps": Fact("services.ic_counterfactual.read_baseline_keeps", "kept", None,
                              _ic_baseline_keeps),
    LOOP_FACT: Fact("task.steps[engine].result.loop", "status", None, _loop_converged, per_rule=True),
    # Issue 07: the report and hand-off stages.
    "report.status": Fact("harness.run_report.status", "status", None, _report_status),
    "report.unsupported_listed": Fact("harness.run_report.unsupported_listed", "listed", None,
                                      _report_unsupported_listed),
    "handoff.kpis_read": Fact("harness.run_handoff.kpis_read", "read", None, _handoff_kpis_read),
    # Issue 07 B2 (GK B C3, GK B (31) F2-F8): the eh_site input and result facts.
    "eh.readiness.ready": Fact("services.chat_tools.get_eh_readiness", "stages", None, _eh_readiness_ready),
    "eh.energization.min_mw": Fact("services.flow_service.read_template", "energization", "MW",
                                   _eh_energization_min_mw),
    "eh.study.status": Fact("harness.run_eh.own_record", "status", None, _eh_study_status),
    "eh.review.blockers": Fact("services.adequacy.eh_review.review_report", "findings", None, _eh_review_blockers),
    "kpi.unserved_mwh": Fact("harness.lost_load_calculations.calculate", "lost_load_energy", "MWh",
                             _kpi_unserved_mwh),
    # Study harness 11: the H2 hub's offtake and electrolyser.
    "h2.offtake.shed_hours": Fact("services.run_gates._offtake_shed_hours", "shed_hours", "h",
                                  _offtake_shed_hours),
    "h2.electrolyser.utilisation": Fact("services.run_gates._electrolyser_utilisation", "utilisation", "p.u.",
                                        _electrolyser_utilisation, per_rule=True),
    # Study harness 10a: the data-centre run (GK B (36) D1, Q1, Q2).
    "dc.campus.contingency": Fact("services.run_actions.contingency", "failing", None, _dc_campus_contingency),
    "dc.gridcode.published": Fact("services.run_actions.choose", "id", None, _dc_gridcode_published),
    "dc.gridcode.frequency_match": Fact("services.solver.island.campus_f_hz", "f_hz", None,
                                        _dc_gridcode_frequency),
    "dc.gridcode.unchanged": Fact("services.campus_electrical_service.profile_fingerprint", "sha256", None,
                                  _dc_gridcode_unchanged),
    "dc.campus.study": Fact("services.run_actions.outcome", "outcome", None, _dc_campus_study),
    # Study harness 12b: the microgrid island (GK A (21) Q1-Q5; GK B (46) M10-M12).
    "mg.island.survival": Fact("services.run_actions.mg_outcome", "state", None, _mg_survival),
    "mg.fuel.autonomy": Fact("services.adequacy.island_survival.survival", "fuel", None, _mg_fuel),
    "mg.tie.open": Fact("services.adequacy.island_survival.tie_state", "state", None, _mg_tie),
    "mg.critical.tagged": Fact("services.adequacy.island_survival.critical_state", "state", None, _mg_critical),
    "mg.island.critical_served": Fact("services.adequacy.dtc.run_dtc_stress", "critical_unserved_mwh", "MWh",
                                      _mg_critical_served),
    "mg.island.frequency": Fact("services.run_actions.mg_frequency", "hours", "h", _mg_frequency),
}


# §5 (GK A): scorecard rows gate by verdict. A rule names one row id or one
# family; a family rule evaluates every row with the prefix, and a reader
# never picks one row of a family.
SCORECARD = "dc.readiness."
FAMILIES = ("energization_peak:", "dynamic:", "ride_through:", "motor_start:")
SCREENING_PASS = "within the screening limit"
# D8: requirements only the operator's own study can show; the screening never passes them.
NEVER_PASS = ("ride_through:", "dynamic:")


@dataclass(frozen=True)
class Measured:
    """A reading that passes only with the campus study's measures (GK A
    CT-5d, the #1172 rule): the value the rule compares, and each measure,
    carried into the gate record and named in the pass's words."""

    value: Any
    measures: list


MEASURED_PASS = "passes with the campus study's measures: "


@dataclass(frozen=True)
class Scored:
    """A scorecard reading: the verdict the rule compares, and the rows it
    rests on (each with its `assumed` flag), carried into the gate record."""

    value: str
    rows: list


def _scorecard(env):
    """`grid_readiness_for` of the run's project with the run's frozen
    grid-code profile, read once per gate (D2): no evidence unless the run's
    own readiness step read it and the profile's digest is still the one
    that step stored."""
    if "scorecard" not in env.cache:
        env.cache["scorecard"] = _frozen_scorecard(env)
    return env.cache["scorecard"]


def _frozen_scorecard(env):
    from services import run_actions
    from services.adequacy import grid_readiness
    step = run_actions.latest(env.task, run_actions.READINESS)
    if step is None or step.get("status") != "completed":
        return NoEvidence("not_read", "the run's own grid-readiness step has not read the scorecard")
    code = (step.get("frozen") or {}).get("grid_code") or {}
    if not code.get("id") or (step.get("result") or {}).get("digest") != code.get("digest") \
            or not run_actions.unchanged(env.project, code):
        return NoEvidence("grid_code_changed", "the run's grid-code profile changed since its scorecard was read")
    card = grid_readiness.grid_readiness_for(env.project, code["id"])
    if run_actions.rows_digest(card) != step["result"].get("rows_digest"):
        # A campus run again, or an edited sidecar, between the read and the gate: never a fresh pass.
        return NoEvidence("scorecard_changed", "the scorecard changed since the run's own readiness step read it")
    return card


def _scorecard_reader(name):
    target = name[len(SCORECARD):]

    def read(env):
        from services.adequacy import grid_readiness
        card = _scorecard(env)
        if isinstance(card, NoEvidence):
            return card
        if target in NEVER_PASS:
            return NoEvidence("screening_only", f"{target} rows need the network operator's own study; "
                                                "the screening never passes them")
        if target.endswith(":"):
            rows = [r for r in card.get("rows") or [] if str(r["id"]).startswith(target)]
            if not rows:
                return NoEvidence("no_rows", f"no scorecard row in family {target!r}")
            if target == PERIOD_FAMILY:
                gap = _period_gap(env, rows)
                if gap is not None:
                    return gap
            verdict = "pass" if all(r["verdict"] == "pass" for r in rows) else "fail"
        else:
            verdict = grid_readiness.readiness_fact(card, target)
            if isinstance(verdict, NoEvidence):
                return verdict
            rows = [r for r in card["rows"] if r["id"] == target]
        return Scored(verdict, [{"id": r["id"], "verdict": r["verdict"], "assumed": bool(r.get("assumed"))}
                                for r in rows])
    return read


PERIOD_FAMILY = "energization_peak:"


def _period_gap(env, rows):
    """SH-10b (GK A ST-1): on a multi-period network the energization family
    has exactly one row per investment period of the network at gate time
    (never the template's planning); a period without a row, or a row for a
    period the network does not have, is no evidence, never ignored."""
    periods = getattr(getattr(env.ctx, "network", None), "investment_periods", None)
    if periods is None or not len(periods):
        return None
    have = {str(r["id"])[len(PERIOD_FAMILY):] for r in rows}
    want = {str(int(p)) for p in periods}
    if have != want:
        return NoEvidence("periods_changed",
                          f"the energization rows cover {sorted(have)}, the network's periods are {sorted(want)}")
    return None


def _check_scorecard(name):
    target = name[len(SCORECARD):]
    if not target:
        return "name one row id or one family"
    family = next((f for f in FAMILIES if target.startswith(f)), None)
    if target.endswith(":") and family != target:
        return f"unknown family {target!r}; known: {', '.join(FAMILIES)}"
    if family is not None and family != target:
        return f"{target!r} is one row of the family {family!r}: name the family"
    return None


def fact_for(name):
    """The registry's fact for `name`, or None: a closed name, or one
    scorecard row or family (`dc.readiness.<row>`, `dc.readiness.<family>:`)."""
    if name in FACTS:
        return FACTS[name]
    if isinstance(name, str) and name.startswith(SCORECARD) and _check_scorecard(name) is None:
        return Fact("services.adequacy.grid_readiness.grid_readiness_for", "rows[].verdict", None,
                    _scorecard_reader(name))
    return None


class RuleError(ValueError):
    """A rule a profile may not carry (refused when the profile loads)."""


def check_rule(rule: dict) -> dict:
    """The rule, with the reader it reads named from the registry."""
    known = {"id", "fact", "op", "value", "severity", "basis", "explain", "tunable", "unit", "engine"}
    if not isinstance(rule, dict) or not {"id", "fact", "op", "severity", "basis"} <= set(rule) <= known:
        raise RuleError(f"A rule has id, fact, op, severity, basis and optional value, explain, tunable, unit, "
                        f"engine: {rule!r}")
    fact = rule["fact"]
    if not isinstance(fact, str) or "*" in fact:
        raise RuleError(f"Rule {rule['id']}: name one fact, never a wildcard ({fact!r}).")
    if fact.startswith(SCORECARD) and (problem := _check_scorecard(fact)):
        raise RuleError(f"Rule {rule['id']}: {fact!r}: {problem}.")
    known_fact = fact_for(fact)
    if known_fact is None:
        raise RuleError(f"Rule {rule['id']}: unknown fact {fact!r} (fact registry v{FACTS_VERSION}).")
    if rule["op"] not in OPS or rule["severity"] not in SEVERITIES:
        raise RuleError(f"Rule {rule['id']}: op is one of {OPS}, severity one of {SEVERITIES}.")
    if rule["op"] not in ("empty", "nonempty") and "value" not in rule:
        raise RuleError(f"Rule {rule['id']}: {rule['op']} needs a value.")
    if rule["op"] == "within" and not (isinstance(rule["value"], list) and len(rule["value"]) == 2):
        raise RuleError(f"Rule {rule['id']}: within takes [lo, hi].")
    if fact.startswith(SCORECARD) and (rule["op"], rule.get("value")) != ("eq", "pass"):
        raise RuleError(f"Rule {rule['id']}: a scorecard rule is `eq pass`: only a pass satisfies it (§5).")
    if "unit" in rule and rule["unit"] != known_fact.unit:
        raise RuleError(f"Rule {rule['id']}: unit {rule['unit']!r} is not the fact's {known_fact.unit!r}; "
                        "units are checked, never converted.")
    return {**check_engine(rule), "reads": known_fact.reads + ":" + known_fact.key}


def _missing(value):
    return value is None or (isinstance(value, float) and math.isnan(value))


def _holds(op, value, expected):
    if op == "empty":
        return len(value) == 0
    if op == "nonempty":
        return len(value) > 0
    if op in ("in", "not_in"):
        return (value in expected) == (op == "in")
    if isinstance(expected, bool) or isinstance(value, bool):
        if type(value) is not type(expected):
            raise TypeError("a boolean compares only with a boolean")
    if op == "eq":
        return value == expected
    if op == "ne":
        return value != expected
    if op == "within":
        return expected[0] <= value <= expected[1]
    return {"lt": value < expected, "le": value <= expected, "gt": value > expected, "ge": value >= expected}[op]


def evaluate(rules: list[dict], env: Env) -> list[dict]:
    """One GateResult per rule. Each fact is read once per gate."""
    values: dict[str, tuple[Any, str | None]] = {}
    out = []
    for rule in rules:
        fact = fact_for(rule["fact"])
        key = (rule["fact"], rule["id"]) if fact is not None and fact.per_rule else rule["fact"]
        if key not in values:
            if fact is None:
                values[key] = (None, "unknown fact")
            else:
                try:
                    values[key] = (fact.read(env, rule) if fact.per_rule else fact.read(env), None)
                except Exception as exc:  # noqa: BLE001 — an unreadable fact is no evidence (fail closed)
                    values[key] = (None, f"the reader failed: {type(exc).__name__}")
        value, problem = values[key]
        shown, scored, looped, measured = None, None, None, None
        if isinstance(value, Measured):
            measured, value = value, value.value
        if isinstance(value, Scored):
            scored, value = value, value.value
        if isinstance(value, Looped):
            looped, value = value, value.value
        detailed = None
        if isinstance(value, Detailed):
            detailed, value = value, value.value
        if isinstance(value, NoEvidence):
            shown, problem = value.value, value.reason
        elif problem is None and _missing(value):
            problem = "the fact reads as none"
        result = {"rule": rule["id"], "severity": rule["severity"],
                  "facts": [{"fact": rule["fact"], "value": shown if problem else value,
                             "unit": fact.unit if fact else None, "source": rule.get("reads")}]}
        if scored is not None:
            result["facts"][0]["rows"] = scored.rows
        if looped is not None:
            result["facts"][0]["loop"] = looped.record
        if detailed is not None:
            result["facts"][0]["detail"] = list(detailed.detail)
            if detailed.basis:
                result["facts"][0]["basis"] = detailed.basis
        if problem is None:
            try:
                result["verdict"] = "pass" if _holds(rule["op"], value, rule.get("value")) else "fail"
            except (TypeError, ValueError) as exc:
                problem = f"the fact cannot be compared: {exc}"
        if problem is None and scored is not None:
            problem = _scored_problem(rule, result, scored)
        if measured is not None:
            result["facts"][0]["measures"] = measured.measures
            if problem is None and result["verdict"] == "pass" and measured.measures:
                from services.run_actions import CHECK_WORDS
                result["note"] = MEASURED_PASS + "; ".join(m["detail"] or CHECK_WORDS.get(m["check"], m["check"])
                                                           for m in measured.measures)
        if problem is not None:
            result.update(verdict="not_evaluated", reason=problem)
        out.append(result)
    return out


def _scored_problem(rule, result, scored):
    """D3, D4: why a scorecard reading is no evidence for this rule, or None
    (a pass on an assumed warn row is marked "within the screening limit")."""
    if rule["severity"] == "warn" and any(r["verdict"] == "unknown" for r in scored.rows):
        return "not evaluated: an input the row needs is missing"  # D4: never a pass, never accepted
    if result["verdict"] == "pass" and any(r["assumed"] for r in scored.rows):
        if rule["severity"] == "block":
            # D3: a block row never passes on a generic or code-default input.
            return "passes only on assumed inputs, so it is not evaluated for a block rule"
        result.update(screening=True, note=SCREENING_PASS)  # never "verified" (§5)
    return None


def passed(results: list[dict]) -> bool:
    """Only a pass satisfies a block rule; not_evaluated counts as a fail."""
    return all(r["verdict"] == "pass" for r in results if r["severity"] == "block")


def fingerprint(ctx) -> str | None:
    """The context's input fingerprint (solver outputs excluded), or None."""
    from services import workflow_tools
    try:
        return workflow_tools._network_input_fingerprint(ctx)
    except Exception:  # noqa: BLE001 — unreadable inputs are no evidence
        return None


def _unevaluated(rules, reason):
    return [{"rule": r["id"], "severity": r["severity"], "verdict": "not_evaluated", "reason": reason,
             "facts": [{"fact": r["fact"], "value": None, "unit": None, "source": r.get("reads")}]} for r in rules]


def gate(rules: list[dict], env: Env) -> dict:
    """The gate, pinned to the inputs it judged: the fingerprint read before
    and after the readers must agree, on the run's own project's context."""
    own = (env.case or {}).get("project_id") if env.case is not None else env.task.get("project_id")
    if env.ctx is None or env.project is None or str(env.ctx.project_uuid) != str(own):
        before, results = None, _unevaluated(rules, "the context is not the run's project")
    else:
        before = fingerprint(env.ctx)
        results = evaluate(rules, env)
        after = fingerprint(env.ctx)
        if before is None or after is None:
            results = _unevaluated(rules, "the inputs cannot be read")
        elif before != after:
            results = _unevaluated(rules, "inputs changed during evaluation")
    return {"rules": results, "passed": passed(results), "fingerprint": before,
            "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "facts_version": FACTS_VERSION}

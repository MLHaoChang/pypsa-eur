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
rule; a pass on an assumed row is a screening pass, reported as such.

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
    project is no evidence."""
    from routers.solve_queue import _visible_job_or_404
    from services import chat_tools, project_registry
    if env.case is not None:
        job_id = env.case.get("job_id")  # issue 06: the case's own job, never the run's solve
    else:
        step = next((s for s in reversed(env.task["steps"]) if s["id"].startswith("solve.") and s.get("job")), None)
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
    return NoEvidence(fact["state"], f"{fact['state']}: {', '.join(fact['reasons'])}")


def _ic_baseline_keeps(env):
    """The owned existing assets the case's baseline keeps (study harness
    20): the names, else no evidence with the state and the reasons (an
    asset of the template's declared set the baseline drops)."""
    from services import ic_counterfactual as ic
    fact = ic.read_baseline_keeps(env.ctx, lambda fn: _on(env.ctx, fn), _template(env))
    if fact["state"] == ic.ESTABLISHED:
        return fact["kept"]
    return NoEvidence(fact["state"], f"{fact['state']}: {', '.join(fact['reasons'])}")


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
# The fields of a finished loop's record a settled engine step keeps, and the
# gate shows: the verdict, what it cost, how the network was left, and the
# goal it was judged against with where each part came from.
LOOP_FIELDS = ("status", "solves_used", "probe_solves", "restore", "base_restored", "base_restore_status",
               "target_lole_h", "draws", "basis", "goal_sources", "lever_star", "eps_star", "confident",
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
    step = engine_step(env.task, rule["id"])
    if step is None:
        return NoEvidence("not_started", "the engine loop for this rule has not run")
    if step["status"] in ("pending", "running", "waiting"):
        return NoEvidence(step["status"], "the engine loop has not finished")
    loop = (step.get("result") or {}).get("loop")
    if not isinstance(loop, dict):
        reason = step.get("error") or "the engine loop left no record"
        return NoEvidence((step.get("job") or {}).get("outcome") or step["status"], str(reason)[:240])
    if loop.get("applied") is False:  # a user edit landed while the loop ran (H3): not this project's
        return NoEvidence("inputs_changed", "the inputs changed while the engine loop ran")
    return Looped(converged(loop), {k: loop[k] for k in LOOP_FIELDS if k in loop})


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
    from harness.durable import network_guard
    if env.ctx is None or env.case is not None:
        return None
    job = _solve_job(env)
    if job is None or job["status"] != "completed":
        return None
    refused = _not_applied(job)
    if refused is not None:
        return refused
    exact = (job.get("capture") or {}).get("exact")
    if not exact:
        return NoEvidence("no_capture", "the run's solve recorded no input identity")

    def read():
        with network_guard():
            if (getattr(env.ctx.network, "meta", None) or {}).get("assistant_solve_exact_input_fingerprint") != exact:
                return NoEvidence("other_solve", "the live results are not the run's own solve")
            try:
                raw = lost_load.source(env.ctx, "network")
            except ValueError as exc:
                return NoEvidence(str(exc), f"{exc}: unserved energy cannot be read")
        facts, _ = lost_load.calculate(raw, {"profile": lost_load.PROFILE, "entity_id": "network", "metrics": ["lost_load_energy"]})
        return facts[0]["value"] if facts else None
    return _on(env.ctx, read)


def _handoff_kpis_read(env):
    """Issue 07 (C14): every headline KPI read back natively, on the run's context."""
    from harness import run_handoff
    if env.ctx is None:
        return None
    return _on(env.ctx, run_handoff.kpis_read, env.task, _profile(env))


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
}


# §5 (GK A): scorecard rows gate by verdict. A rule names one row id or one
# family; a family rule evaluates every row with the prefix, and a reader
# never picks one row of a family.
SCORECARD = "dc.readiness."
FAMILIES = ("energization_peak:", "dynamic:", "ride_through:", "motor_start:")
SCREENING_PASS = "screening pass"


@dataclass(frozen=True)
class Scored:
    """A scorecard reading: the verdict the rule compares, and the rows it
    rests on (each with its `assumed` flag), carried into the gate record."""

    value: str
    rows: list


def _scorecard(env):
    """`grid_readiness_for` of the run's project, read once per gate."""
    from services.adequacy import grid_readiness
    if "scorecard" not in env.cache:
        env.cache["scorecard"] = grid_readiness.grid_readiness_for(env.project)
    return env.cache["scorecard"]


def _scorecard_reader(name):
    target = name[len(SCORECARD):]

    def read(env):
        from services.adequacy import grid_readiness
        card = _scorecard(env)
        if target.endswith(":"):
            rows = [r for r in card.get("rows") or [] if str(r["id"]).startswith(target)]
            if not rows:
                return NoEvidence("no_rows", f"no scorecard row in family {target!r}")
            verdict = "pass" if all(r["verdict"] == "pass" for r in rows) else "fail"
        else:
            verdict = grid_readiness.readiness_fact(card, target)
            if isinstance(verdict, NoEvidence):
                return verdict
            rows = [r for r in card["rows"] if r["id"] == target]
        return Scored(verdict, [{"id": r["id"], "verdict": r["verdict"], "assumed": bool(r.get("assumed"))}
                                for r in rows])
    return read


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
        shown, scored, looped = None, None, None
        if isinstance(value, Scored):
            scored, value = value, value.value
        if isinstance(value, Looped):
            looped, value = value, value.value
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
        if problem is None:
            try:
                result["verdict"] = "pass" if _holds(rule["op"], value, rule.get("value")) else "fail"
            except (TypeError, ValueError) as exc:
                problem = f"the fact cannot be compared: {exc}"
        if problem is None and scored is not None and result["verdict"] == "pass" \
                and any(r["assumed"] for r in scored.rows):
            result.update(screening=True, note=SCREENING_PASS)  # never "verified" (§5)
        if problem is not None:
            result.update(verdict="not_evaluated", reason=problem)
        out.append(result)
    return out


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

"""Harness run gates: typed rules over native facts (study harness §5).

A gate is a list of rules. Each rule reads one fact from the closed,
versioned registry below; each fact names the existing native function
(and the key of its result) it reads, so a renamed row breaks a test, not a
gate silently. The model never supplies a fact or a verdict: only harness
code calls `evaluate`, and only harness code writes its result.

Fail closed: a fact that is unknown, cannot be read, or reads as None or NaN
is `not_evaluated`, which a `block` rule counts as a fail; so is a reading the
reader itself marks as no evidence (`NoEvidence`: its state and reason kept).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, UTC
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
    read: Callable[[Env], Any]


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
    job"; a job of another project is no evidence."""
    from routers.solve_queue import _visible_job_or_404
    from services import chat_tools, project_registry
    step = next((s for s in env.task["steps"] if s["id"].startswith("solve.") and s.get("job")), None)
    if step is None:
        return None
    with chat_tools._acting() as (db, user):
        job = _visible_job_or_404(db, user, step["job"]["job_id"])
    return job if job.get("project_key") == project_registry.registry_key(env.project) else None


def _solve_state(env):
    job = _solve_job(env)
    return None if job is None else job["status"]


def _solve_status(env):
    job = _solve_job(env)
    return None if job is None or job["status"] != "completed" else job["condition"]


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


def _run(key):
    return lambda env: env.task.get("run", {}).get(key)


FACTS: dict[str, Fact] = {
    # Harness facts: the run block itself, written only by harness code.
    "brief.valid": Fact("task.brief", "brief", None, lambda env: bool(env.task.get("brief"))),
    "profile.resolved": Fact("task.run", "profile", None, lambda env: bool(_run("profile")(env))),
    "run.mode": Fact("task.run", "mode", None, _run("mode")),
    "project.exists": Fact("services.project_registry.resolve_project", "id", None,
                           lambda env: env.project is not None),
    "project.template": Fact("services.flow_service.read_template", "id", None, _template),
    # Core input facts.
    "validate.errors": Fact("services.chat_tools.validate_network", "errors", "count", _validate),
    "readiness.ready": Fact("services.workflow_tools.project_readiness", "ready", None, _readiness),
    "diagnose.unserved_islands": Fact("services.chat_tools.diagnose_network", "islands_without_generation",
                                      "count", _unserved_islands),
    # Core result facts.
    "solve.state": Fact("routers.solve_queue._visible_job_or_404", "status", None, _solve_state),
    "solve.status": Fact("routers.solve_queue._visible_job_or_404", "condition", None, _solve_status),
    "fresh": Fact("services.workflow_tools.solve_freshness", "results_current", None, _fresh),
    "ic.counterfactual": Fact("services.ic_counterfactual.read", "state", None, _ic_counterfactual),
    "ic.baseline_keeps": Fact("services.ic_counterfactual.read_baseline_keeps", "kept", None,
                              _ic_baseline_keeps),
}


class RuleError(ValueError):
    """A rule a profile may not carry (refused when the profile loads)."""


def check_rule(rule: dict) -> dict:
    """The rule, with the reader it reads named from the registry."""
    known = {"id", "fact", "op", "value", "severity", "basis", "explain", "tunable", "unit"}
    if not isinstance(rule, dict) or not {"id", "fact", "op", "severity", "basis"} <= set(rule) <= known:
        raise RuleError(f"A rule has id, fact, op, severity, basis and optional value, explain, tunable, unit: {rule!r}")
    fact = rule["fact"]
    if not isinstance(fact, str) or "*" in fact:
        raise RuleError(f"Rule {rule['id']}: name one fact, never a wildcard ({fact!r}).")
    if fact not in FACTS:
        raise RuleError(f"Rule {rule['id']}: unknown fact {fact!r} (fact registry v{FACTS_VERSION}).")
    if rule["op"] not in OPS or rule["severity"] not in SEVERITIES:
        raise RuleError(f"Rule {rule['id']}: op is one of {OPS}, severity one of {SEVERITIES}.")
    if rule["op"] not in ("empty", "nonempty") and "value" not in rule:
        raise RuleError(f"Rule {rule['id']}: {rule['op']} needs a value.")
    if rule["op"] == "within" and not (isinstance(rule["value"], list) and len(rule["value"]) == 2):
        raise RuleError(f"Rule {rule['id']}: within takes [lo, hi].")
    if "unit" in rule and rule["unit"] != FACTS[fact].unit:
        raise RuleError(f"Rule {rule['id']}: unit {rule['unit']!r} is not the fact's {FACTS[fact].unit!r}; "
                        "units are checked, never converted.")
    return {**rule, "reads": FACTS[fact].reads + ":" + FACTS[fact].key}


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
        fact = FACTS.get(rule["fact"])
        if rule["fact"] not in values:
            if fact is None:
                values[rule["fact"]] = (None, "unknown fact")
            else:
                try:
                    values[rule["fact"]] = (fact.read(env), None)
                except Exception as exc:  # noqa: BLE001 — an unreadable fact is no evidence (fail closed)
                    values[rule["fact"]] = (None, f"the reader failed: {type(exc).__name__}")
        value, problem = values[rule["fact"]]
        shown = None
        if isinstance(value, NoEvidence):
            shown, problem = value.value, value.reason
        elif problem is None and _missing(value):
            problem = "the fact reads as none"
        result = {"rule": rule["id"], "severity": rule["severity"],
                  "facts": [{"fact": rule["fact"], "value": shown if problem else value,
                             "unit": fact.unit if fact else None, "source": rule.get("reads")}]}
        if problem is None:
            try:
                result["verdict"] = "pass" if _holds(rule["op"], value, rule.get("value")) else "fail"
            except (TypeError, ValueError) as exc:
                problem = f"the fact cannot be compared: {exc}"
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
    if env.ctx is None or env.project is None or str(env.ctx.project_uuid) != str(env.task.get("project_id")):
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

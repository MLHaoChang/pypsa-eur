"""Harness run profiles: one study workflow per template, as data (study harness §9).

A profile names, per stage of the fixed stage graph, the deterministic steps
the harness materialises on stage entry and the rules of the stage's exit
gate (`services/run_gates.py`). It is data, not prompts. Stages a profile
leaves out take the generic defaults (§9.2); a stage set to `skip` carries
its reason.

`load` validates a profile before any run uses it and refuses what a run may
never carry: an unknown fact, a wildcard, a never-tunable attribute (§6.0),
a stage over the step limit, or a tool whose bound-context resolution no
test has shown (§6.6).
"""
from __future__ import annotations

import copy
import json

from services import run_gates

STAGES = ("intake", "setup", "inputs", "input_gate", "solve", "result_gate", "explore", "report", "handoff")
STAGE_STEPS, STAGE_CHARS = 24, 32000  # the LC-23 brief limit, per stage
PROJECT = "$project"  # an action argument the harness fills with the run's project id

# Tools shown by `tests/test_harness_run_skeleton.py` (the §6.6 sweep) to
# resolve the project through the bound context. A tool joins a stage only
# after it joins this set with its test.
BOUND_CONTEXT_TOOLS = frozenset({
    "get_weather_choice", "validate_network", "project_readiness", "diagnose_network", "solve_queue_enqueue",
})

# §6.0: the agent changes design levers, never the question. A profile that
# lists one of these as tunable is refused at load.
NEVER_TUNABLE = frozenset({
    "target_lole_h", "draws", "seed",  # the reliability goal
    "it_load", "it_load_mw", "process_demand", "offtake", "p_set",  # demand
    "eh_poc", "eh_ibr_mva", "fault_level_mva", "sk_mva", "sk_min_mva", "c_max", "c_min",  # fault levels
    "frequency", "tdd_standard", "contingencies", "stress_scenarios",
    "solver_options", "solver_name", "tolerance", "assumed", "verdict",
})
_NEVER_COMPONENTS = frozenset({"grid_code", "solver", "gate", "profile", "envelope"})


def _rule(id, fact, op, value=None, severity="block", basis=""):
    rule = {"id": id, "fact": fact, "op": op, "severity": severity, "basis": basis}
    if op not in ("empty", "nonempty"):
        rule["value"] = value
    return rule


def _call(tool, **args):
    return {"tool": tool, "args": args}


# §5 core rules, shared by every profile. Profiles may tighten, never loosen.
CORE_INPUT_RULES = [
    _rule("in.validate.errors", "validate.errors", "eq", 0,
          basis="A network with validation errors cannot be solved meaningfully."),
    _rule("in.readiness.ready", "readiness.ready", "eq", True,
          basis="project_readiness names every blocker a solve would hit."),
    _rule("in.diagnose.unserved_islands", "diagnose.unserved_islands", "eq", 0,
          basis="A load in an island with no supply makes the solve infeasible."),
]
CORE_RESULT_RULES = [
    _rule("res.solve.status", "solve.status", "eq", "optimal", basis="Only an optimal solve has results to read."),
    _rule("res.fresh", "fresh", "eq", True, basis="Results must describe the current inputs (LC-12)."),
]

# §9.2. Setup binds the open project: tasks never rebind (start_task), so
# creating the project from the template stays the user's step for now.
GENERIC: dict[str, dict] = {
    "intake": {"actions": [], "rules": [
        _rule("in.brief.valid", "brief.valid", "eq", True, basis="The user reviewed the brief."),
        _rule("in.profile.resolved", "profile.resolved", "eq", True, basis="The profile is frozen at intake."),
        _rule("in.mode", "run.mode", "eq", "stepwise",
              basis="Without an envelope every write and run is carded (COP-1)."),
    ]},
    "setup": {"actions": [], "rules": [
        _rule("set.project.exists", "project.exists", "eq", True, basis="The run works on one saved project."),
    ]},
    "inputs": {"actions": [_call("get_weather_choice", project_id=PROJECT)], "rules": []},
    "input_gate": {"actions": [_call("validate_network"), _call("project_readiness", project_id=PROJECT),
                               _call("diagnose_network")], "rules": CORE_INPUT_RULES},
    "solve": {"actions": [_call("solve_queue_enqueue", project_id=PROJECT)], "rules": [
        _rule("sv.solve.state", "solve.state", "eq", "completed", basis="The run's own solve job finished."),
    ]},
    "result_gate": {"actions": [], "rules": CORE_RESULT_RULES},
    "explore": {"skip": "Explore cases come with the loops (study harness issue 04)."},
    "report": {"skip": "The report stage comes with study harness issue 07."},
    "handoff": {"actions": [], "rules": []},
}


class ProfileError(ValueError):
    """A profile a run may not use."""


def _check_tunable(entries):
    for entry in entries:
        if not isinstance(entry, dict) or not {"component", "attr"} <= set(entry) <= {"component", "attr", "bounds"}:
            raise ProfileError(f"A tunable entry names component, attr and optional bounds: {entry!r}")
        component, attr = str(entry["component"]), str(entry["attr"])
        if attr in NEVER_TUNABLE or "grid_code" in attr or component.split(":")[0] in _NEVER_COMPONENTS:
            raise ProfileError(f"{component}.{attr} is never tunable: the agent changes design levers, "
                               "never the question (§6.0).")


def load(profile: dict) -> dict:
    """The validated profile with every stage resolved (generic defaults
    filled in). Raises ProfileError, or RuleError for a bad rule."""
    if not isinstance(profile, dict) or not {"id", "version", "templates", "stages"} <= set(profile):
        raise ProfileError("A profile has id, version, templates and stages.")
    unknown = set(profile["stages"]) - set(STAGES)
    if unknown:
        raise ProfileError(f"Unknown stages: {sorted(unknown)}.")
    _check_tunable(profile.get("tunable", []))
    stages = {}
    for name in STAGES:
        stage = copy.deepcopy(profile["stages"].get(name, GENERIC[name]))
        if "skip" in stage:
            if not isinstance(stage["skip"], str) or not stage["skip"] or set(stage) != {"skip"}:
                raise ProfileError(f"Stage {name}: skip carries its reason and nothing else.")
            stages[name] = stage
            continue
        actions = stage.get("actions", [])
        if len(actions) > STAGE_STEPS or len(json.dumps(actions)) > STAGE_CHARS:
            raise ProfileError(f"Stage {name}: at most {STAGE_STEPS} steps and {STAGE_CHARS} characters.")
        for action in actions:
            if set(action) != {"tool", "args"} or not isinstance(action["args"], dict):
                raise ProfileError(f"Stage {name}: each action has tool and args.")
            if action["tool"] not in BOUND_CONTEXT_TOOLS:
                raise ProfileError(f"Stage {name}: {action['tool']} has no bound-context test (§6.6).")
        _check_tunable(stage.get("tunable", []))
        try:
            rules = [run_gates.check_rule(r) for r in stage.get("rules", [])]
        except run_gates.RuleError as exc:
            raise ProfileError(f"Stage {name}: {exc}") from exc
        stages[name] = {"actions": actions, "rules": rules}
    return {**copy.deepcopy(profile), "stages": stages}


def scope_for(template: dict | None) -> dict:
    """The run's card scope, read from `flow_scope.preset_for`, not copied."""
    from services import flow_scope
    preset_id, preset = flow_scope.preset_for(template)
    return {"preset": preset_id, "cards": {card: list(options) for card, options in preset.items()}}


EH_SITE = {
    "id": "eh_site", "version": 1,
    "templates": ["eh_site"],
    "scope": "flow_scope.preset_for",
    "params": {"required": ["site_type", "stages", "connection_mw"],
               "ask": ["energization", "critical_share", "load_profile", "existing", "candidates",
                       "ride_through", "fault_level_mva", "horizon_end"]},
    "stages": {
        "setup": {"actions": [], "rules": [
            *GENERIC["setup"]["rules"],
            _rule("set.project.template", "project.template", "eq", "eh_site",
                  basis="The profile serves the eh_site template only."),
        ]},
        # Issue 01: the result gate reads the solver status only; LC-20 (05)
        # and the eh_site KPI rules (07) join it later.
    },
    "tunable": [{"component": "candidate:genset", "attr": "max_mw"},
                {"component": "candidate:bess", "attr": "max_mw"},
                {"component": "candidate:pv", "attr": "max_mw"},
                {"component": "candidate:bess", "attr": "hours"}],
    "kpis": [],
    "chapters": [],
    "missing_tools": [],
}

PROFILES: dict[str, dict] = {"eh_site@1": EH_SITE}


def resolve(ref: str) -> dict:
    """The loaded profile for `id@version`; ProfileError when none is known
    or it does not load."""
    if not isinstance(ref, str) or ref not in PROFILES:
        raise ProfileError(f"Unknown profile {ref!r}; known: {', '.join(sorted(PROFILES))}.")
    return load(PROFILES[ref])


def materialise(profile: dict, stage: str, project_id: str) -> list[dict]:
    """A stage's steps, `<stage>.<n>`, with the run's project filled in."""
    def fill(value):
        if value == PROJECT:
            return project_id
        if isinstance(value, dict):
            return {k: fill(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fill(v) for v in value]
        return value
    return [{"id": f"{stage}.{n}", "tool": a["tool"], "args": fill(a["args"]), "status": "pending"}
            for n, a in enumerate(profile["stages"][stage].get("actions", []), start=1)]

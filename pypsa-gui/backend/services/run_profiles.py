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

from services import run_actions, run_gates

STAGES = ("intake", "setup", "inputs", "input_gate", "solve", "result_gate", "explore", "report", "handoff")
STAGE_STEPS, STAGE_CHARS = 24, 32000  # the LC-23 brief limit, per stage
PROJECT = "$project"  # an action argument the harness fills with the run's project id

# Tools shown by `tests/test_harness_run_skeleton.py` (the §6.6 sweep) to
# resolve the project through the bound context. A tool joins a stage only
# after it joins this set with its test.
BOUND_CONTEXT_TOOLS = frozenset({
    "get_weather_choice", "validate_network", "project_readiness", "diagnose_network", "solve_queue_enqueue",
    "apply_demand_from_excel",  # issue 21c: stage 2's planned write
    "generate_report",  # issue 07: stage 7 (GK B C6)
    "run_eh_study",  # issue 07 B2: the solve stage's EH study, on its frozen args (GK B C1)
    # SC-07 S3 (GK B G7, N7): a 3D site apply step works on the bound project. A
    # profile never lists it: its args come only from a prepare step (N2).
    "site_scene_apply",
})

# Issue 07 (design §3 stages 7-8): steps only the runner runs, never a chat
# call (`assistant_tasks.begin_step` leaves them alone). Stage 7's report job
# is the only place a strict, run-charged report starts (GK B's stage-7
# ruling); stage 8's read-back is a harness read and carries no card.
NATIVE_STEPS = {"report": "generate_report", "handoff": "read_handoff"}

# §6.0: the agent changes design levers, never the question. A profile that
# lists one of these as tunable is refused at load.
NEVER_TUNABLE = frozenset({
    "target_lole_h", "draws", "seed",  # the reliability goal
    "it_load", "it_load_mw", "process_demand", "offtake", "p_set",  # demand
    "eh_poc", "eh_ibr_mva", "fault_level_mva", "sk_mva", "sk_min_mva", "c_max", "c_min",  # fault levels
    "frequency", "tdd_standard", "contingencies", "stress_scenarios",
    "solver_options", "solver_name", "tolerance", "assumed", "verdict",
    # DC13-SCOPE (GK A L2-1): taps and shunts move only in the campus study's
    # stage 2 after an outage, never as a lever of the agent.
    "tap_position", "tap_ratio", "tap_side", "tap_step", "tap_pos", "b", "g", "b_pu", "g_pu",
    "compensation", "compensation_mvar", "q_comp_mvar",
})
_NEVER_COMPONENTS = frozenset({"grid_code", "solver", "gate", "profile", "envelope", "ShuntImpedance"})

# §6.3: an agent turn offers its stage's allowlist only. The input loop L1
# (§6.1) gets reads, Library reads and the data-gap and edit tools; the
# envelope still decides each write at dispatch, and anything it does not
# admit is a card the headless turn never shows.
L1_ROUNDS = 3  # §6.1: at most 3 rounds; a profile may lower it
AGENT_STAGES = ("input_gate", "result_gate", "explore")
L1_TOOLS = (
    "validate_network", "project_readiness", "diagnose_network", "get_weather_choice", "get_component",
    "list_components", "get_current_series", "list_all_timeseries", "list_load_profile_archetypes",
    "list_library_items", "get_library_item", "list_library_series", "get_library_series",
    "update_component", "attach_tariff", "set_weather_choice", "generate_load_profile", "apply_generated_profile",
)
# The fix-and-rerun loop L2 (issue 05, §6.2): a failed result rule or a
# solve that did not finish optimal. Reads (the triage the harness ran is in
# the message) and the size-cap edits; never a solve: each round's re-solve is
# the run's own solve step. At most 2 rounds, each with at most 2 changes.
L2_ROUNDS = 2
L2_CHANGES = 2
L2_TOOLS = (
    "validate_network", "project_readiness", "diagnose_network", "get_component", "list_components",
    "solve_queue_job_log", "update_component",
)
# The explore stage (issue 06, §6.7): one turn proposes at most 4 cases with
# at most 2 changes each (AI-558) through `propose_cases`; harness code admits
# them and runs them. Reads only besides the proposal.
EXPLORE_CASES, EXPLORE_CHANGES = 4, 2
EXPLORE_TOOLS = (
    "validate_network", "project_readiness", "get_component", "list_components",
    "list_library_items", "get_library_item", "propose_cases",
)
# What each agent stage's block names, and its bounds.
_AGENTS = {"input_gate": {"tools": L1_TOOLS, "rounds": L1_ROUNDS, "changes": None},
           "result_gate": {"tools": L2_TOOLS, "rounds": L2_ROUNDS, "changes": L2_CHANGES},
           "explore": {"tools": EXPLORE_TOOLS, "rounds": 1, "changes": EXPLORE_CHANGES, "cases": EXPLORE_CASES}}


def _rule(id, fact, op, value=None, severity="block", basis=""):
    rule = {"id": id, "fact": fact, "op": op, "severity": severity, "basis": basis}
    if op not in ("empty", "nonempty"):
        rule["value"] = value
    return rule


def _call(tool, **args):
    return {"tool": tool, "args": args}


def _native(stage):
    return {"tool": NATIVE_STEPS[stage], "args": {}, "native": stage}


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
# Issue 06: the explore gate counts settled cases (passed, failed with its
# rules, or not runnable with its reason), not passed ones; a failed case
# does not fail the run (the base case's result gate does).
EXPLORE_RULES = [
    _rule("ex.cases.settled", "explore.unsettled", "eq", 0, basis="Every admitted case is settled (§3 stage 6)."),
]

# Issue 07 (design §3 stages 7-8): the report is ready and lists every
# chapter it cannot evidence (SC-15); the hand-off read every headline KPI
# back natively. Each fact fails closed on a missing source (GK B C3).
REPORT_RULES = [
    _rule("rp.report.status", "report.status", "eq", "ready", basis="The run's own report job saved its report."),
    _rule("rp.report.unsupported_listed", "report.unsupported_listed", "eq", True,
          basis="Every chapter is evidenced or named as a gap (SC-15)."),
]
HANDOFF_RULES = [
    _rule("ho.kpis_read", "handoff.kpis_read", "eq", True,
          basis="Every headline KPI was read back from its native source (C14)."),
]

# Issue 07 B2 (design §9.3, GK B (31) F7-F9): eh_site's own input and result
# rules. The EH review reads the study, so it sits in the result gate (F8, Q1 = A).
EH_INPUT_RULES = [
    _rule("eh.readiness.ready", "eh.readiness.ready", "eq", True,
          basis="The Energy Hub readiness preflight predicts every requested stage runs."),
    _rule("eh.energization.min_mw", "eh.energization.min_mw", "ge", 0.0,
          basis="Each growth stage's energized connection is a number of MW, never negative."),
]
EH_SOLVE_RULES = [
    _rule("eh.study.status", "eh.study.status", "eq", "done",
          basis="The run's own Energy Hub study finished with every requested stage run (F2)."),
]
EH_RESULT_RULES = [
    _rule("eh.review.blockers", "eh.review.blockers", "empty",
          basis="The review of the run's own study names no high-severity finding."),
    _rule("kpi.unserved_mwh", "kpi.unserved_mwh", "eq", 0.0,
          basis="No load is shed in normal operation (the run's own solve)."),
    # §6.2, F9: the goal's h/yr target on the network's horizon (`loop_goal`),
    # judged by the loop record only; the EH study's own MC LOLE never is.
    {**_rule("eh.adequacy.lole", "adequacy.loop.converged", "eq", True,
             basis="LOLE at or below the project's reliability goal (h/yr, on the horizon)."),
     "engine": {"tool": "run_margin_loop", "max_solves": 3, "restore": "base"}},
]
# Issue 07 B2 (GK B C1): the solve stage's EH study. Its args are frozen from
# this block and the project's goal (`harness/run_eh.freeze`), never a model's.
EH_STUDY_ACTION = {"tool": "run_eh_study", "args": {"frozen": "eh_study"}}

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
                               _call("diagnose_network")], "rules": CORE_INPUT_RULES,
                   "agent": {"tools": list(L1_TOOLS), "rounds": L1_ROUNDS}},
    "solve": {"actions": [_call("solve_queue_enqueue", project_id=PROJECT)], "rules": [
        _rule("sv.solve.state", "solve.state", "eq", "completed", basis="The run's own solve job finished."),
    ]},
    "result_gate": {"actions": [], "rules": CORE_RESULT_RULES,
                    "agent": {"tools": list(L2_TOOLS), "rounds": L2_ROUNDS, "changes": L2_CHANGES}},
    "explore": {"skip": "Exploring cases is not part of a run yet."},
    "report": {"skip": "The run report is not part of a run yet."},
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
                               "never the question.")


def _check_agent(name, agent):
    """A stage's agent turn (§6.3): its allowlist, round bound and, for L2,
    its per-round change cap."""
    from harness.catalogue import TOOLS, safety_tier_for
    if name not in AGENT_STAGES:
        raise ProfileError(f"Stage {name}: no agent turn runs there yet.")
    spec = _AGENTS[name]
    keys = {"tools", "rounds"} | ({"changes"} if spec["changes"] else set()) | ({"cases"} if "cases" in spec else set())

    def bounded(value, top):
        return not isinstance(value, bool) and isinstance(value, int) and 1 <= value <= top
    if not isinstance(agent, dict) or set(agent) != keys or not isinstance(agent["tools"], list) \
            or not agent["tools"] or not bounded(agent["rounds"], spec["rounds"]) \
            or (spec["changes"] and not bounded(agent["changes"], spec["changes"])) \
            or ("cases" in spec and not bounded(agent["cases"], spec["cases"])):
        raise ProfileError(f"Stage {name}: an agent turn names tools and 1-{spec['rounds']} rounds"
                           + (f" and 1-{spec['changes']} changes" if spec["changes"] else "")
                           + (f" and 1-{spec['cases']} cases." if "cases" in spec else "."))
    known = {t["name"] for t in TOOLS}
    for tool in agent["tools"]:
        if tool not in known or tool not in spec["tools"] or safety_tier_for(tool) == "destructive":
            raise ProfileError(f"Stage {name}: {tool!r} is not a tool its loop may offer.")
    return {"tools": list(dict.fromkeys(agent["tools"])), "rounds": agent["rounds"],
            **({"changes": agent["changes"]} if spec["changes"] else {}),
            **({"cases": agent["cases"]} if "cases" in spec else {})}


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
            if "skip" not in GENERIC[name]:
                raise ProfileError(f"Stage {name}: its inherited rules cannot be skipped.")
            if not isinstance(stage["skip"], str) or not stage["skip"] or set(stage) != {"skip"}:
                raise ProfileError(f"Stage {name}: skip carries its reason and nothing else.")
            stages[name] = stage
            continue
        actions = stage.get("actions", [])
        if len(actions) > STAGE_STEPS or len(json.dumps(actions)) > STAGE_CHARS:
            raise ProfileError(f"Stage {name}: at most {STAGE_STEPS} steps and {STAGE_CHARS} characters.")
        for action in actions:
            if set(action) - {"native"} != {"tool", "args"} or not isinstance(action["args"], dict):
                raise ProfileError(f"Stage {name}: each action has tool and args.")
            if action.get("native") == run_actions.KIND or action["tool"] in run_actions.REGISTRY:
                # Study harness 10a (GK B (36) D7): the runner's closed native-action registry.
                try:
                    run_actions.check(action, name)
                except ValueError as exc:
                    raise ProfileError(f"Stage {name}: {exc}") from exc
                continue
            if "native" in action:
                if action["native"] != name or NATIVE_STEPS.get(name) != action["tool"] or action["args"]:
                    raise ProfileError(f"Stage {name}: a native step is its own stage's, {NATIVE_STEPS.get(name)}.")
                if action["tool"] == "generate_report" and action["tool"] not in BOUND_CONTEXT_TOOLS:
                    raise ProfileError(f"Stage {name}: generate_report is not yet shown to work on the run's own project.")
                continue
            if action["tool"] not in BOUND_CONTEXT_TOOLS:
                raise ProfileError(f"Stage {name}: {action['tool']} is not yet shown to work on the run's own project.")
            if action["tool"] == "site_scene_apply":
                raise ProfileError(f"Stage {name}: site_scene_apply takes its args from a prepare step of a "
                                   "plan, never from a profile.")
            if (action["tool"] == EH_STUDY_ACTION["tool"]) != (action["args"] == EH_STUDY_ACTION["args"]) or (
                    action["tool"] == EH_STUDY_ACTION["tool"]
                    and (name != "solve" or not isinstance(profile.get("eh_study"), dict))):
                # Issue 07 B2 (GK B C1): its args are frozen by the harness, never written here.
                raise ProfileError(f"Stage {name}: run_eh_study runs in the solve stage only, with the profile's "
                                   "eh_study block and args frozen by the harness.")
        _check_tunable(stage.get("tunable", []))
        try:
            rules = [run_gates.check_rule(r) for r in stage.get("rules", [])]
        except run_gates.RuleError as exc:
            raise ProfileError(f"Stage {name}: {exc}") from exc
        if name != "result_gate" and any("engine" in r for r in rules):
            raise ProfileError(f"Stage {name}: an engine rule runs only in the result gate.")
        # §9.2, §5: a stage inherits its generic rules; a profile adds rules
        # (a tighter threshold is one more rule), never drops or loosens one.
        own = {r["id"]: r for r in stage.get("rules", [])}
        for core in GENERIC[name].get("rules", []):
            if own.get(core["id"]) != core:
                raise ProfileError(f"Stage {name}: rule {core['id']} is inherited and cannot be dropped or changed.")
        stages[name] = {"actions": actions, "rules": rules}
        if "agent" in stage:
            stages[name]["agent"] = _check_agent(name, stage["agent"])
    if "skip" not in stages["explore"] or "explore" in profile:
        # Issue 06: a run explores only with the profile's levers, its one
        # proposal turn and the settled-cases rule.
        from harness import run_explore
        try:
            explore = run_explore.check_block(profile.get("explore"), profile.get("tunable", []),
                                              NEVER_TUNABLE, _NEVER_COMPONENTS)
        except ValueError as exc:
            raise ProfileError(f"Explore: {exc}") from exc
        stage = stages["explore"]
        if "skip" in stage or "agent" not in stage or EXPLORE_RULES[0]["id"] not in {r["id"] for r in stage["rules"]}:
            raise ProfileError("Stage explore: an exploring profile names its proposal turn and ex.cases.settled.")
    loaded = {**copy.deepcopy(profile), "stages": stages}
    if "explore" in profile:
        loaded["explore"] = explore
    if "envelope" in profile:
        # Issue 03: what a run of this profile may propose; a grant narrows it, never widens it.
        from services import run_envelope
        try:
            loaded["envelope"] = run_envelope.check_block(profile["envelope"])
        except run_envelope.EnvelopeError as exc:
            raise ProfileError(f"Envelope: {exc}") from exc
        tunable = {(t["component"], t["attr"]) for t in profile.get("tunable", [])}
        for edit in loaded["envelope"]["edits"]:
            if (edit["target"]["component"], edit["target"]["attr"]) not in tunable:
                raise ProfileError(f"Envelope: {edit['target']['component']}.{edit['target']['attr']} is not "
                                   "one of the profile's tunable attributes (§6.0).")
    return loaded


def scope_for(template: dict | None) -> dict:
    """The run's card scope, read from `flow_scope.preset_for`, not copied."""
    from services import flow_scope
    preset_id, preset = flow_scope.preset_for(template)
    return {"preset": preset_id, "cards": {card: list(options) for card, options in preset.items()}}


# The completed weather year eh_site fills a weather gap from.
EH_SITE_YEAR = 2023

# Issue 21 (§1): each eh_site creation param as a field-schema entry, so the
# intake's unit checks are `equipment_table._column_verdict`'s, unchanged. A
# display unit converts by `display_scale` (display = stored × scale). Enums
# equal `project_templates.eh_site`'s (a test pins them; importing the
# template here would load PyPSA). `sources`: where a value may come from.
_NUMERIC = ("table", "pdf", "text")
_MW = {"native_type": "float", "unit_storage": "MW", "unit_display": "kW", "display_scale": 1000.0}


def _num(label, unit_fields, kind="number"):
    return {"label": label, "kind": kind, "sources": list(_NUMERIC), "editable": True, **unit_fields}


def _enum(label, ids):
    return {"label": label, "kind": "enum", "enum": list(ids), "sources": ["text"]}


def _bool(label):
    return {"label": label, "kind": "bool", "sources": ["text"]}


EH_SITE_FIELDS = {
    "site_type": _enum("Site type", ("industrial", "commercial", "hospital", "campus", "other")),
    "stages[].year": _num("Growth stage year", {}, kind="year"),
    "stages[].peak_mw": _num("Growth stage peak load", _MW),
    "connection_mw": _num("Grid connection", _MW),
    "energization[].mw": _num("Connection energized in the stage year", _MW),
    "critical_share": _num("Critical load share", {"native_type": "float", "unit_storage": "fraction",
                                                   "unit_display": "%", "display_scale": 100.0}),
    "load_profile": _enum("Load shape", ("industrial", "commercial", "hospital", "campus", "flat")),
    "existing.gensets.count": _num("Existing gensets", {}, kind="count"),
    "existing.gensets.unit_mw": _num("Existing genset size", _MW),
    "existing.gensets.fuel": _enum("Existing genset fuel", ("gas", "diesel")),
    "existing.pv_mw": _num("Existing PV", _MW),
    "existing.battery.mw": _num("Existing battery power", _MW),
    "existing.battery.mwh": _num("Existing battery energy", {"native_type": "float", "unit_storage": "MWh",
                                                             "unit_display": "kWh", "display_scale": 1000.0}),
    "existing.ups.mw": _num("Existing UPS power", _MW),
    "existing.ups.minutes": _num("Existing UPS autonomy", {"native_type": "float", "unit_storage": "min"}),
    **{f"candidates.{k}.enabled": _bool(f"Consider new {label}") for k, label in
       (("genset", "gensets"), ("bess", "batteries"), ("pv", "PV"))},
    **{f"candidates.{k}.max_mw": _num(f"New {label} size cap", _MW) for k, label in
       (("genset", "genset"), ("bess", "battery"), ("pv", "PV"))},
    "ride_through": _bool("Ride-through required"),
    "fault_level_mva": _num("Grid fault level", {"native_type": "float", "unit_storage": "MVA"}),
    "horizon_end": _num("Study horizon end", {}, kind="year"),
}

EH_SITE = {
    "id": "eh_site", "version": 1,
    "templates": ["eh_site"],
    "scope": "flow_scope.preset_for",
    "params": {"required": ["site_type", "stages", "connection_mw"],
               "ask": ["energization", "critical_share", "load_profile", "existing", "candidates",
                       "ride_through", "fault_level_mva", "horizon_end"],
               "fields": EH_SITE_FIELDS},
    # Issue 21: the stage-2 inputs a run asks for when missing (none yet for eh_site).
    "inputs": {"required": []},
    "stages": {
        "setup": {"actions": [], "rules": [
            *GENERIC["setup"]["rules"],
            _rule("set.project.template", "project.template", "eq", "eh_site",
                  basis="The profile serves the eh_site template only."),
        ]},
        # Issue 07 B2: the EH input rules, the EH study after the solve, and
        # the EH result rules (design §9.3).
        "input_gate": {**GENERIC["input_gate"], "rules": [*CORE_INPUT_RULES, *EH_INPUT_RULES]},
        "solve": {"actions": [*GENERIC["solve"]["actions"], EH_STUDY_ACTION],
                  "rules": [*GENERIC["solve"]["rules"], *EH_SOLVE_RULES]},
        "result_gate": {**GENERIC["result_gate"], "rules": [*CORE_RESULT_RULES, *EH_RESULT_RULES]},
        # Issue 06: connection against BESS against genset, at most 4 cases.
        "explore": {"actions": [], "rules": EXPLORE_RULES,
                    "agent": {"tools": list(EXPLORE_TOOLS), "rounds": 1, "changes": EXPLORE_CHANGES,
                              "cases": EXPLORE_CASES}},
        # Issue 07: the report job (the harness's own, strict and charged to
        # the run) and the hand-off read-back.
        "report": {"actions": [_native("report")], "rules": REPORT_RULES},
        "handoff": {"actions": [_native("handoff")], "rules": HANDOFF_RULES},
    },
    # Issue 06 (§9.3): what a case may change on its fork, each with its
    # bounds and their basis (a `profile:` source). The connection is the
    # user's on the base: explore only, never an L1/L2 tunable (E7).
    "explore": {
        "levers": [
            {"component": "Link", "name": "poc_link", "attr": "p_nom", "unit": "MW", "explore_only": True,
             "bounds": {"min": 0.0, "max": 500.0},
             "basis": "Connection sizes a site may apply for; the network operator's offer decides."},
            {"component": "Generator", "name": "genset_new_*", "attr": "p_nom_max", "unit": "MW",
             "bounds": {"min": 0.0, "max": 200.0}, "basis": "Candidate genset sizes the eh_site template offers."},
            {"component": "StorageUnit", "name": "bess_new_*", "attr": "p_nom_max", "unit": "MW",
             "bounds": {"min": 0.0, "max": 200.0}, "basis": "Candidate BESS power the eh_site template offers."},
            {"component": "StorageUnit", "name": "bess_new_*", "attr": "max_hours", "unit": "h",
             "bounds": {"min": 1.0, "max": 8.0}, "basis": "BESS durations the eh_site template offers."},
        ],
        "study": {"question_id": "bess_at_site"},
    },
    # Issue 07 B2: what the run's EH study runs with, besides the project's
    # goal and its template's site overrides (`harness/run_eh.freeze`).
    "eh_study": {"stages": ["apply_pack", "ens_solve", "mc_certify", "assemble"], "budget_solves": 2},
    "tunable": [{"component": "candidate:genset", "attr": "max_mw"},
                {"component": "candidate:bess", "attr": "max_mw"},
                {"component": "candidate:pv", "attr": "max_mw"},
                {"component": "candidate:bess", "attr": "hours"},
                # The same levers as the network columns the envelope's edits write (issue 03).
                {"component": "Generator", "attr": "p_nom_max"},
                {"component": "StorageUnit", "attr": "p_nom_max"},
                {"component": "StorageUnit", "attr": "max_hours"}],
    # Issue 03 (§7): the edits and runs a granted envelope covers. The
    # candidates' size caps within ±50 % of their value at grant, and the
    # run's own queued solve.
    "envelope": {
        "edits": [{"tool": "update_component", "target": {"component": "Generator", "name": "genset_new_*", "attr": "p_nom_max"},
                   "bounds": {"min": 0.0, "rel": 0.5}},
                  {"tool": "update_component", "target": {"component": "Generator", "name": "pv_new_*", "attr": "p_nom_max"},
                   "bounds": {"min": 0.0, "rel": 0.5}},
                  {"tool": "update_component", "target": {"component": "StorageUnit", "name": "bess_new_*", "attr": "p_nom_max"},
                   "bounds": {"min": 0.0, "rel": 0.5}},
                  {"tool": "update_component", "target": {"component": "StorageUnit", "name": "bess_new_*", "attr": "max_hours"},
                   "bounds": {"min": 1.0, "max": 8.0}}],
        # Issue 04: the data gaps the input loop may fill, never replace (§6.1, §7).
        # Each names the source its fill copies, fixed here and resolved at the
        # grant (GK B, AI-567b): the model never picks a value.
        # No Load gap yet: a static p_set is demand, not a gap (GK B); one comes
        # with a ruled energy source (`run_envelope.LOAD_ENERGY_SOURCES`).
        "gaps": [*[{"tool": "set_weather_choice", "target": {"component": "Settings", "name": "Weather", "attr": attr},
                    "source": {"kind": "weather_choices", "choices": [{"source": "synthetic", "year": EH_SITE_YEAR}]}}
                   for attr in ("source", "year")],
                 {"tool": "attach_tariff",
                  "target": {"component": "Settings", "name": "Commercial", "attr": "import_tariff_ref"},
                  "source": {"kind": "library_tariff"}}],
        # Issue 07 (B1-b): the run's report covers the stage-7 card only; its sends
        # are reserved against the run's budget (C4), never a solve.
        "runs": [{"tool": "solve_queue_enqueue"}, {"tool": "generate_report"}],
        "budget": {"solves": 6, "rounds": 6, "wall_time_h": 8.0},
        "research": {"allowed": True, "cost_cap_eur": 2.0},  # SH-RESEARCH-CAP-1; the grant governs
    },
    # Issue 09 (design §8): the gaps a research turn may look up, each in one
    # unit and basis; a value is proposed with its source and written only
    # through the gap tool's own card. Checked by `harness.research.check_block`.
    "research": {"gaps": [
        {"key": "import_tariff", "stage": "inputs", "tool": "attach_tariff",
         "target": {"component": "Settings", "name": "Commercial", "attr": "import_tariff_ref"},
         "unit": "EUR/MWh", "basis": "energy charge, excl. VAT",
         "question": "What is the published import energy charge for this site's connection level?"},
    ]},
    # Issue 07 (C14): the headline KPIs the hand-off reads back, each from one
    # retained results section (`retained_results`) at one key path.
    "kpis": [
        {"id": "total_cost", "label": "Total system cost", "section": "results_summary",
         "path": ["cost_breakdown", "total"], "unit": "EUR"},
        {"id": "emissions", "label": "CO2 emissions", "section": "results_emissions",
         "path": ["emissions", "total_tCO2"], "unit": "tCO2"},
    ],
    # §9.3: the EH reference design, reliability and investment chapters
    # (evidence section ids); one the run did not establish is a gap (SC-15).
    "chapters": ["sizing", "mc", "investment_case"],
    "missing_tools": [],
}


# Issue 07 B3b (GK B (33) P5, P6): eh_site@2. The LOLE rule's engine is the
# coupling loop (the margin loop refuses the shipped eh_site network), and the
# intake asks for the user's reliability goal (h/yr), which the grant writes to
# the new project's solver config before the freeze. A goal param is never a
# creation param (`run_intake.params`). eh_site@1 stays byte-equal for the
# runs started on it (L8).
GOAL_PARAM = "goal.target_lole_h"


def _eh_site_v2() -> dict:
    profile = copy.deepcopy(EH_SITE)
    profile["version"] = 2
    profile["params"]["ask"].append(GOAL_PARAM)
    profile["params"]["fields"][GOAL_PARAM] = _num(
        "Reliability goal (LOLE)", {"native_type": "float", "unit_storage": "h/yr"})
    for rule in profile["stages"]["result_gate"]["rules"]:
        if rule.get("engine"):
            rule["engine"] = {"tool": "run_coupling_loop", "max_solves": 3, "restore": "base"}
    return profile


EH_SITE_V2 = _eh_site_v2()

# Study harness issue 33 (GK B (49) plan GO, Q1-Q3): eh_site@3. The intake may
# name the site by a point (lat/lon, never a creation param); the inputs stage
# then fetches its map context (`harness/run_site.py`, the service
# `fetch_site_context` calls) into the run's own record, and the input gate's
# `eh.site.context` (warn) reads it. eh_site@1 and @2 stay byte-equal (L8).
SITE_LAT_PARAM = "site.lat"
SITE_LON_PARAM = "site.lon"
SITE_LAT_MAX = 85.05  # `site_context.MAX_MERCATOR_LAT`: the terrain tiles end there
SITE_LON_MAX = 180.0
SITE_CONTEXT_RULE = _rule(
    "eh.site.context", "eh.site.context", "eq", "fetched", severity="warn",
    basis="The map context (buildings, power lines, substations, terrain) of the site the intake names "
          "was fetched in full for the run.")


def _degrees(label, limit):
    return {"label": label, "kind": "number", "sources": ["text"], "editable": True,
            "native_type": "float", "unit_storage": "°", "unit_display": "deg", "display_scale": 1.0,
            "validation_schema": {"minimum": -limit, "maximum": limit}}


def _eh_site_v3() -> dict:
    profile = copy.deepcopy(EH_SITE_V2)
    profile["version"] = 3
    profile["params"]["ask"] += [SITE_LAT_PARAM, SITE_LON_PARAM]
    profile["params"]["fields"][SITE_LAT_PARAM] = _degrees("Site latitude", SITE_LAT_MAX)
    profile["params"]["fields"][SITE_LON_PARAM] = _degrees("Site longitude", SITE_LON_MAX)
    profile["stages"]["inputs"] = {
        "actions": [*copy.deepcopy(GENERIC["inputs"]["actions"]),
                    {"tool": run_actions.SITE_FETCH, "args": dict(run_actions.MARKERS[run_actions.SITE_KIND]),
                     "native": run_actions.KIND}],
        "rules": []}
    gate = profile["stages"]["input_gate"]
    gate["rules"] = [*gate["rules"], copy.deepcopy(SITE_CONTEXT_RULE)]
    return profile


EH_SITE_V3 = _eh_site_v3()

# Study harness 10a (GK B (36) D1-D8, Q1-Q5): eh_datacenter@1. The solve stage
# runs the solve, the EH study (LOLE as eh_site@2, Q5), the campus run held to
# the run's frozen grid-code profile and the scorecard read with it
# (`services/run_actions.py`). No envelope yet: every write and run is carded.
FREQUENCY_PARAM = run_gates.FREQUENCY_PARAM


def _dc_action(tool):
    return {"tool": tool, "args": dict(run_actions.FROZEN), "native": run_actions.KIND}


# D8: each scorecard row id or family the engine emits on a data-centre
# project, with its severity, its one-line basis and the run that produces its
# input; every emitted row is here or in `DC_NOT_GATED` (a test pins both, on
# the shipped template and on a project with sidecars). No wildcard.
_CAMPUS_RUN = "the campus study, held to the run's grid-code profile"
_DC_LOAD = "the AI-training load profile stored at project creation (no run)"
_ISLAND = "the island study (island_config.json, island_study.json; no run here)"
DC_SCORECARD = (
    ("scr", "block", "The short-circuit ratio at the PoC meets the profile's minimum.",
     "the saved network's PoC fault level (no run)"),
    ("energization_peak:", "block", "Each stage's peak fits the connection energized that year.",
     "the template's energization schedule and the load (no run)"),
    ("reactive_range", "block", "The campus holds the profile's reactive range at the PoC.", _DC_LOAD),
    ("ramp_rate", "warn", "Load ramps stay within the connection's ramp limit.", _DC_LOAD),
    ("oscillation", "warn", "Load power swings stay within the oscillation limit.", _DC_LOAD),
    ("rapid_voltage_change", "warn", "Frequent voltage steps stay within the limit.", _DC_LOAD),
    ("rapid_voltage_change_infrequent", "warn", "Infrequent voltage steps stay within the limit.", _DC_LOAD),
    ("harmonic_distortion", "warn", "Voltage THD at the PoC within the planning level.", _CAMPUS_RUN),
    ("current_distortion", "warn", "Current TDD at the PoC within the limit.", _CAMPUS_RUN),
    ("flicker", "warn", "Flicker severity within the planning level.", _CAMPUS_RUN),
    ("motor_start", "warn", "A motor start's voltage dip within the limit.", "the project's motor data (no run)"),
    ("motor_start:", "warn", "Each named motor's start dip within the limit.", "the project's motor data (no run)"),
    ("voltage_unbalance", "warn", "Negative-sequence unbalance within the limit.", _CAMPUS_RUN),
    # Acceptance (issue 18): a P-V/Q-V margin is screening, so it warns.
    ("voltage_stability_pv", "warn", "P-V margin at the PoC (screening).", _CAMPUS_RUN),
    ("voltage_stability_qv", "warn", "Q-V margin at the PoC (screening).", _CAMPUS_RUN),
    ("frequency_support", "warn", "The campus offers the frequency support the code asks.", _ISLAND),
    ("grid_former", "warn", "A unit forms the island's voltage.", _ISLAND),
    ("ride_through:", "warn", "Ride-through needs the operator's study; it never passes here.", _ISLAND),
    ("dynamic:", "warn", "Dynamic requirements need an RMS/EMT study; they never pass here.",
     "an RMS/EMT study outside the app (none)"),
)
DC_NOT_GATED = {"lole": "not gated: the reliability rule judges LOLE against the project's goal"}


def _dc_scorecard_rules():
    return [_rule(f"dc.{row.rstrip(':')}", run_gates.SCORECARD + row, "eq", "pass", severity=severity,
                  basis=f"{basis} Input: {source}.")
            for row, severity, basis, source in DC_SCORECARD]


# SH-10b (GK A Q3, CT-1..CT-6; DC13-SCOPE): the run's own campus study meets
# the planning contingencies, intact and transformer N-1, after the taps and
# the compensation it bought; redispatch is never a remedy (the study has none).
_DC_CONTINGENCY = _rule(
    "dc.campus.contingency", "dc.campus.contingency", "empty",
    basis="No check fails in the intact case or a transformer N-1 after the campus study's taps and "
          "compensation (taps and shunts only after an outage; no redispatch). "
          f"Input: {_CAMPUS_RUN}.")
# Staged (design §9.3): the investment case is in the result gate. Its
# counterfactual and its returns are read from the stored case, current for
# this network; both only warn, and the returns are never judged against a hurdle.
_DC_IC_RULES = [
    _rule("dc.ic.counterfactual", "ic.counterfactual", "eq", "established", severity="warn",
          basis="The investment case compares against an established counterfactual; until then it is "
                "not evaluated and never blocks the run."),
    _rule("dc.ic.returns", "ic.returns", "nonempty", severity="warn",
          basis="The investment case's project post-tax NPV at the WACC and IRR, read from the stored "
                "case for this network; reported, never compared with a hurdle."),
]


def _eh_datacenter(staged: bool = False) -> dict:
    template = "eh_datacenter_staged" if staged else "eh_datacenter"
    lole = copy.deepcopy(next(r for r in EH_RESULT_RULES if r.get("engine")))
    lole["engine"] = {"tool": "run_coupling_loop", "max_solves": 3, "restore": "base"}
    return {
        "id": template, "version": 1,
        "templates": [template],
        "scope": "flow_scope.preset_for",
        "params": {"required": [], "ask": [GOAL_PARAM, FREQUENCY_PARAM], "fields": {
            GOAL_PARAM: _num("Reliability goal (LOLE)", {"native_type": "float", "unit_storage": "h/yr"}),
            # Q1 = A: the user's own, never tunable; the card shows its value in Hz.
            FREQUENCY_PARAM: {**_enum("Grid frequency (Hz)", ("50", "60")), "unit_storage": "Hz"},
        }},
        "inputs": {"required": []},
        "stages": {
            "setup": {"actions": [], "rules": [
                *GENERIC["setup"]["rules"],
                _rule("set.project.template", "project.template", "eq", template,
                      basis=f"The profile serves the {template} template only."),
            ]},
            # Issue 10: stage 2 waits for the user until a grid-code profile is published.
            "inputs": {"actions": list(GENERIC["inputs"]["actions"]), "rules": [
                _rule("dc.gridcode.published", "dc.gridcode.published", "eq", True,
                      basis="The run is held to a grid-code profile the user published (never drafted here)."),
            ]},
            "input_gate": {**GENERIC["input_gate"], "rules": [
                *CORE_INPUT_RULES,
                _rule("dc.gridcode.frequency", "dc.gridcode.frequency_match", "eq", True,
                      basis="The campus's nominal frequency is the grid frequency the user gave (Hz)."),
            ]},
            "solve": {"actions": [*GENERIC["solve"]["actions"], EH_STUDY_ACTION,
                                  _dc_action(run_actions.CAMPUS), _dc_action(run_actions.READINESS)],
                      "rules": [*GENERIC["solve"]["rules"], *EH_SOLVE_RULES,
                                _rule("dc.campus.study", "dc.campus.study", "eq", "done",
                                      basis="The run's own campus run finished, held to its grid-code profile.")]},
            "result_gate": {**GENERIC["result_gate"], "rules": [
                *CORE_RESULT_RULES,
                _rule("dc.gridcode.unchanged", "dc.gridcode.unchanged", "eq", True,
                      basis="The grid-code profile is still the one the run was held to."),
                *_dc_scorecard_rules(),
                _DC_CONTINGENCY,
                *(_DC_IC_RULES if staged else []),
                lole,
            ]},
            "report": {"actions": [_native("report")], "rules": REPORT_RULES},
            "handoff": {"actions": [_native("handoff")], "rules": HANDOFF_RULES},
        },
        "eh_study": copy.deepcopy(EH_SITE["eh_study"]),
        # GK B (36) F2 (a): the campus run's own settings, winning over the saved ones.
        "campus_study": dict(run_actions.CAMPUS_STUDY),
        "tunable": [],
        "kpis": copy.deepcopy(EH_SITE["kpis"]),
        "chapters": ["grid_readiness", "mc", *(["investment_case"] if staged else [])],
        "missing_tools": [],
    }


EH_DATACENTER = _eh_datacenter()
EH_DATACENTER_STAGED = _eh_datacenter(staged=True)

# Study harness 11 (design §9.3 "eh_h2_hub"): eh_h2_hub@1 and
# eh_h2_hub_staged@1, one self-contained block (SH-17 splits per-type
# packages later). The solve, the EH study (archetype strong_grid, from the
# template's own card) and LOLE as eh_datacenter (Q5); the offtake is met in
# every hour (block), the electrolyser's utilisation is reported against a
# band (warn), and the investment case's counterfactual (issue 19) is
# evaluated once established and never blocks the run. The offtake is demand
# and never a lever (§6.0): the envelope names candidate caps, the
# electrolyser and the H2 store only, never grid_import or the fuel cell.
H2_UTILISATION_BAND = [0.40, 0.95]


def _h2_edit(component, name, attr, top):
    return {"tool": "update_component", "target": {"component": component, "name": name, "attr": attr},
            "bounds": {"min": 0.0, "max": top}}


def _h2_levers(staged: bool):
    """The envelope's edits, each within absolute bounds: the candidates up
    to the single-year template's caps; the single-year hub's electrolyser
    is one built Link (p_nom, up to three quarters of its 200 MW connection),
    the staged hub sizes its new electrolysers by their caps (twice a
    period's 40 MW candidate) and keeps the built one; the H2 store up to
    three times the template's 2,000 MWh."""
    electrolyser = (_h2_edit("Link", "electrolyser_new_*", "p_nom_max", 80.0) if staged
                    else _h2_edit("Link", "electrolyser", "p_nom", 150.0))
    return [_h2_edit("Generator", "wind_new*", "p_nom_max", 300.0),
            _h2_edit("Generator", "solar_new*", "p_nom_max", 150.0),
            _h2_edit("StorageUnit", "bess_new*", "p_nom_max", 200.0),
            electrolyser,
            _h2_edit("Store", "h2_storage", "e_nom", 6000.0)]


def _eh_h2_hub(staged: bool) -> dict:
    template = "eh_h2_hub_staged" if staged else "eh_h2_hub"
    lole = copy.deepcopy(next(r for r in EH_RESULT_RULES if r.get("engine")))
    lole["engine"] = {"tool": "run_coupling_loop", "max_solves": 3, "restore": "base"}
    unserved = next(r for r in EH_RESULT_RULES if r["id"] == "kpi.unserved_mwh")
    edits = _h2_levers(staged)
    tunable = sorted({(e["target"]["component"], e["target"]["attr"]) for e in edits})
    return {
        "id": template, "version": 1,
        "templates": [template],
        "scope": "flow_scope.preset_for",
        "params": {"required": [], "ask": [GOAL_PARAM], "fields": {
            GOAL_PARAM: _num("Reliability goal (LOLE)", {"native_type": "float", "unit_storage": "h/yr"})}},
        "inputs": {"required": []},
        "stages": {
            "setup": {"actions": [], "rules": [
                *GENERIC["setup"]["rules"],
                _rule("set.project.template", "project.template", "eq", template,
                      basis=f"The profile serves the {template} template only."),
            ]},
            "solve": {"actions": [*GENERIC["solve"]["actions"], EH_STUDY_ACTION],
                      "rules": [*GENERIC["solve"]["rules"], *EH_SOLVE_RULES]},
            "result_gate": {**GENERIC["result_gate"], "rules": [
                *CORE_RESULT_RULES,
                unserved,
                {**_rule("h2.offtake.met", "h2.offtake.shed_hours", "eq", 0.0,
                         basis="The H2 offtake is served in every hour (the run's own solve; no hour shed)."),
                 "tunable": [f"{c}.{a}" for c, a in tunable]},
                _rule("h2.electrolyser.utilisation", "h2.electrolyser.utilisation", "within",
                      list(H2_UTILISATION_BAND), severity="warn",
                      basis="Electrolyser utilisation within the profile band, a sizing screen, not a design "
                            "limit: below it the electrolyser looks oversized for the offtake; above it little "
                            "headroom looks left to serve it every hour."),
                _rule("h2.ic.counterfactual", "ic.counterfactual", "eq", "established", severity="warn",
                      basis="The investment case compares against an established counterfactual (the grey-H2 "
                            "reference purchase); until then it is not evaluated and never blocks the run."),
                lole,
            ]},
            "report": {"actions": [_native("report")], "rules": REPORT_RULES},
            "handoff": {"actions": [_native("handoff")], "rules": HANDOFF_RULES},
        },
        "eh_study": copy.deepcopy(EH_SITE["eh_study"]),
        "tunable": [{"component": c, "attr": a} for c, a in tunable],
        "envelope": {
            "edits": edits,
            "runs": [{"tool": "solve_queue_enqueue"}, {"tool": "generate_report"}],
            # GK B (38) H7: the worst case, every reservation at its cap. The
            # solve and its EH study (budget_solves 2), each L2 round's re-solve
            # and re-run EH study (L2_ROUNDS = 2), the coupling loop's 3 and its
            # closing re-solve: 1 + 2 + 2 x (1 + 2) + 3 + 1 = 13.
            "budget": {"solves": 13, "rounds": 6, "wall_time_h": 8.0},
        },
        "kpis": copy.deepcopy(EH_SITE["kpis"]),
        "chapters": ["sizing", "mc", "investment_case"],
        "missing_tools": [],
    }


EH_H2_HUB = _eh_h2_hub(staged=False)
EH_H2_HUB_STAGED = _eh_h2_hub(staged=True)

# Study harness 13 (design §9.3 "eh_ammonia_staged"; GK A (24) GO, C1-C7):
# eh_ammonia_staged@1. Stage 2 waits for the process units the user's form
# fills (G2P-07); the solve job runs the binary operational check itself. The
# result gate blocks on a flagged check and on a delivery shortfall above the
# tolerance; the grey-NH3 investment case warns only. No EH study and no LOLE
# loop (§9.3 lists none). The process units, the process and delivery loads,
# the role Generators, the grid connection and the built assets are never
# levers (§6.0): the envelope names the per-year candidates and the two tanks,
# each up to twice its template cap.
NH3_SHORTFALL_MAX = 0.01  # Q3 = A: a delivery tolerance, 1 % of a period's target (t/d)
NH3_YEARS = (2027, 2030, 2035)


def _nh3_levers():
    """The envelope's edits: each per-year candidate and each tank, up to
    twice its template cap (`eh_ammonia.py`: wind 150, PV 80, BESS 80,
    electrolyser 40 MW; H2 store 10,000 and NH3 tank 6,000 MWh)."""
    caps = (("Generator", "wind_new", "p_nom_max", 150.0), ("Generator", "solar_new", "p_nom_max", 80.0),
            ("StorageUnit", "bess_new", "p_nom_max", 80.0), ("Link", "electrolyser_new", "p_nom_max", 40.0))
    return [*[_h2_edit(c, f"{name}_{y}", attr, 2 * cap) for c, name, attr, cap in caps for y in NH3_YEARS],
            _h2_edit("Store", "h2_storage", "e_nom_max", 20000.0),
            _h2_edit("Store", "nh3_tank", "e_nom_max", 12000.0)]


def _eh_ammonia_staged() -> dict:
    template = "eh_ammonia_staged"
    unserved = next(r for r in EH_RESULT_RULES if r["id"] == "kpi.unserved_mwh")
    edits = _nh3_levers()
    tunable = sorted({(e["target"]["component"], e["target"]["attr"]) for e in edits})
    levers = [f"{c}.{a}" for c, a in tunable]
    return {
        "id": template, "version": 1,
        "templates": [template],
        "scope": "flow_scope.preset_for",
        "params": {"required": [], "ask": []},
        "inputs": {"required": []},
        "stages": {
            "setup": {"actions": [], "rules": [
                *GENERIC["setup"]["rules"],
                _rule("set.project.template", "project.template", "eq", template,
                      basis=f"The profile serves the {template} template only."),
            ]},
            # G2P-07: stage 2 waits for the user until the process-unit form is filled.
            "inputs": {"actions": list(GENERIC["inputs"]["actions"]), "rules": [
                _rule("nh3.process.units", "process.units", "nonempty",
                      basis="The process units are the user's inputs (the process-unit form): a committable unit "
                            "with its throughput unit, specific energy and a product counted in tonnes."),
            ]},
            "result_gate": {**GENERIC["result_gate"], "rules": [
                *CORE_RESULT_RULES,
                unserved,
                {**_rule("nh3.binary_check", "process.binary_check", "eq", "ok",
                         basis="The linearised plan holds under the binary operational check in every period "
                               "(no period flagged); an unverified check is not evaluated."),
                 "tunable": levers},
                {**_rule("nh3.delivery.shortfall", "process.delivery.shortfall", "le", NH3_SHORTFALL_MAX,
                         basis=f"A delivery tolerance: in every period the NH3 short of delivery is at most "
                               f"{NH3_SHORTFALL_MAX:.0%} of the target (t/d). Grey NH3 bought in meets delivery "
                               "and is shown, never counted as short."),
                 "tunable": levers},
                _rule("nh3.ic.counterfactual", "ic.counterfactual", "eq", "established", severity="warn",
                      basis="The investment case compares against an established counterfactual (the grey-NH3 "
                            "purchase); until then it is not evaluated and never blocks the run."),
            ]},
            "report": {"actions": [_native("report")], "rules": REPORT_RULES},
            "handoff": {"actions": [_native("handoff")], "rules": HANDOFF_RULES},
        },
        "tunable": [{"component": c, "attr": a} for c, a in tunable],
        "envelope": {
            "edits": edits,
            "runs": [{"tool": "solve_queue_enqueue"}, {"tool": "generate_report"}],
            # The solve (its binary check runs inside the job) and each L2
            # round's re-solve (L2_ROUNDS = 2): 1 + 2 = 3.
            "budget": {"solves": 3, "rounds": 2, "wall_time_h": 8.0},
        },
        "kpis": copy.deepcopy(EH_SITE["kpis"]),
        # g2p 13: the coupling report is the `grid_risk` chapter; a run whose
        # section is not established lists it as a gap (`run_report.unsupported_listed`).
        "chapters": ["sizing", "investment_case", "grid_risk"],
        "missing_tools": [],
    }


EH_AMMONIA_STAGED = _eh_ammonia_staged()

# Study harness 12c (design §9.3 "eh_microgrid"; GK B (47) G1-G9): eh_microgrid@1
# and eh_microgrid_staged@1. The solve, the EH study with the off-grid stress
# (`dtc_stress`, one solve per islanding contingency) and the island check of
# the plan as built (`mg_island_survival`, 12b). The result gate reads the
# island facts (no SCR rule: the tie is open, so the PoC's fault level does
# not apply), LOLE as eh_datacenter (Q5) and the diesel-fleet investment case
# (issue 20), both investment rules warn only. Archetype off_grid comes from
# the template's own card.
#
# G1: K, the islanding contingencies, is counted when the EH study's args are
# frozen (`harness/run_eh.freeze`): its budget is then 2 + K, never capped
# down, and a different K at the solve refuses before any solve.
MG_CONTINGENCY_SOLVES = 1  # `dtc.run_dtc_stress`: one solve per islanding contingency
MG_CONTINGENCIES = 1  # K on both shipped templates: the subsea tie (pinned by a test)
# G4: the battery the L2 loop may resize, by template: its name glob and the
# shipped candidate's cap (MW) and duration (h). The bounds are twice each,
# from 0 MW and 1 h. A StorageUnit has no e_nom: its energy is p_nom × max_hours.
MG_BESS = {"eh_microgrid": ("bess_new", 15.0, 4.0), "eh_microgrid_staged": ("bess_new_*", 10.0, 4.0)}


def _mg_levers(template: str):
    name, cap, hours = MG_BESS[template]
    source = f"the {template} template's {name} candidate"
    return [
        {"tool": "update_component", "target": {"component": "StorageUnit", "name": name, "attr": "p_nom_max"},
         "bounds": {"min": 0.0, "max": 2 * cap}, "unit": "MW",
         "source": f"twice {source}'s {cap:g} MW cap"},
        {"tool": "update_component", "target": {"component": "StorageUnit", "name": name, "attr": "max_hours"},
         "bounds": {"min": 1.0, "max": 2 * hours}, "unit": "h",
         "source": f"twice {source}'s {hours:g} h duration"},
    ]




# The coupling loop's own solve budget on the microgrid profiles' LOLE rule.
MG_LOOP_SOLVES = 3


def mg_pass_solves(k: int) -> int:
    """G2: what one pass of the solve stage spends: the queued solve, the EH
    study (2 + K) and the island check's one solve."""
    return 1 + (2 + MG_CONTINGENCY_SOLVES * k) + run_actions.SOLVES


def mg_budget(k: int) -> int:
    """G1, G2: the worst case for a project with K islanding contingencies,
    every reservation at its cap: the first pass and each L2 round's
    (L2_ROUNDS = 2), then the coupling loop's 3 and its closing re-solve:
    (1 + (2 + K) + 1) x (1 + 2) + 3 + 1, so 19 with K = 1, 22 with K = 2.
    The envelope takes it from the K counted on the project
    (`run_envelope.counted`), held to the envelope's hard cap."""
    return mg_pass_solves(k) * (1 + L2_ROUNDS) + MG_LOOP_SOLVES + 1


def mg_pass_words(k: int) -> str:
    """`mg_pass_solves` as the card writes it, from the same counts."""
    return f"1 + (2 + {MG_CONTINGENCY_SOLVES * k}) + {run_actions.SOLVES}"


def mg_budget_words(k: int) -> str:
    """`mg_budget` as the card writes it, from the same counts (Q1-iii)."""
    return f"({mg_pass_words(k)}) x {1 + L2_ROUNDS} + {MG_LOOP_SOLVES + 1}"


MG_PASS_SOLVES = mg_pass_solves(MG_CONTINGENCIES)
# The profile's own budget, for the shipped templates' K = 1: 5 x 3 + 4 = 19.
MG_BUDGET_SOLVES = mg_budget(MG_CONTINGENCIES)
MG_FUEL_BASIS = ("The gensets' fuel lasts the island's fuel autonomy (h). The gensets draw no modelled "
                 "fuel, so it is not evaluated and never counts toward a pass.")


def _eh_microgrid(staged: bool) -> dict:
    template = "eh_microgrid_staged" if staged else "eh_microgrid"
    lole = copy.deepcopy(next(r for r in EH_RESULT_RULES if r.get("engine")))
    lole["engine"] = {"tool": "run_coupling_loop", "max_solves": MG_LOOP_SOLVES, "restore": "base"}
    edits = _mg_levers(template)
    tunable = [(e["target"]["component"], e["target"]["attr"]) for e in edits]
    eh_study = copy.deepcopy(EH_SITE["eh_study"])
    eh_study["stages"].insert(eh_study["stages"].index("assemble"), "dtc_stress")
    eh_study["contingency_solves"] = MG_CONTINGENCY_SOLVES
    return {
        "id": template, "version": 1,
        "templates": [template],
        "scope": "flow_scope.preset_for",
        "params": {"required": [], "ask": [GOAL_PARAM, "planning"], "fields": {
            GOAL_PARAM: _num("Reliability goal (LOLE)", {"native_type": "float", "unit_storage": "h/yr"}),
            # B13: the template's own planning check (`eh_templates.planning_spec`) judges them.
            "planning.start_year": _num("First operating year", {}, kind="year"),
            "planning.end_year": _num("Study horizon end", {}, kind="year")}},
        "inputs": {"required": []},
        "stages": {
            "setup": {"actions": [], "rules": [
                *GENERIC["setup"]["rules"],
                _rule("set.project.template", "project.template", "eq", template,
                      basis=f"The profile serves the {template} template only."),
            ]},
            "solve": {"actions": [*GENERIC["solve"]["actions"], EH_STUDY_ACTION,
                                  {"tool": run_actions.MG_SURVIVAL,
                                   "args": dict(run_actions.MARKERS[run_actions.MG_KIND]),
                                   "native": run_actions.KIND}],
                      "rules": [*GENERIC["solve"]["rules"], *EH_SOLVE_RULES]},
            "result_gate": {**GENERIC["result_gate"], "rules": [
                *CORE_RESULT_RULES,
                {**_rule("mg.island.survival", "mg.island.survival", "eq", "survives",
                         basis="The plan as built, every size fixed, holds the island settings' N+k ride-through "
                               "over the sustained hours and the islanding step (the run's own island check on "
                               "the current network and island settings)."),
                 "tunable": [f"{c}.{a}" for c, a in tunable]},
                _rule("mg.tie.open", "mg.tie.open", "eq", "open",
                      basis="The island's tie to the mainland is open on the live network."),
                _rule("mg.critical.tagged", "mg.critical.tagged", "eq", "tagged",
                      basis="The island settings and the network name the same critical loads."),
                {**_rule("mg.island.critical_served", "mg.island.critical_served", "le", 0.0,
                         basis="No critical load is shed on island (MWh, summed over the islanding contingencies "
                               "of the run's own EH study's off-grid stress)."), "unit": "MWh"},
                {**_rule("mg.island.frequency", "mg.island.frequency", "le", 0, severity="warn",
                         basis="No island hour fails the frequency judge (h, a count of hours of the run's own "
                               "campus run). 12c runs no campus study, so it reads not run and never counts "
                               "toward a pass."), "unit": "h"},
                _rule("mg.fuel.autonomy", "mg.fuel.autonomy", "eq", True, severity="warn", basis=MG_FUEL_BASIS),
                lole,
                _rule("mg.ic.counterfactual", "ic.counterfactual", "eq", "established", severity="warn",
                      basis="The investment case compares against an established counterfactual (the island as "
                            "it stands today); until then it is not evaluated and never blocks the run."),
                _rule("mg.ic.baseline_keeps", "ic.baseline_keeps", "nonempty", severity="warn",
                      basis="The case's baseline keeps every owned existing asset of the island; until the case "
                            "reads established it is not evaluated and never blocks the run."),
            ]},
            "report": {"actions": [_native("report")], "rules": REPORT_RULES},
            "handoff": {"actions": [_native("handoff")], "rules": HANDOFF_RULES},
        },
        "eh_study": eh_study,
        "tunable": [{"component": c, "attr": a} for c, a in tunable],
        "envelope": {
            "edits": edits,
            "runs": [{"tool": "solve_queue_enqueue"}, {"tool": "generate_report"}],
            "budget": {"solves": MG_BUDGET_SOLVES, "rounds": 6, "wall_time_h": 8.0},
        },
        "kpis": copy.deepcopy(EH_SITE["kpis"]),
        "chapters": ["sizing", "mc", "investment_case"],
        "missing_tools": [],
    }


EH_MICROGRID = _eh_microgrid(staged=False)
EH_MICROGRID_STAGED = _eh_microgrid(staged=True)

PROFILES: dict[str, dict] = {"eh_site@1": EH_SITE, "eh_site@2": EH_SITE_V2, "eh_site@3": EH_SITE_V3,
                              "eh_datacenter@1": EH_DATACENTER,
                              "eh_datacenter_staged@1": EH_DATACENTER_STAGED,
                              "eh_h2_hub@1": EH_H2_HUB, "eh_h2_hub_staged@1": EH_H2_HUB_STAGED,
                              "eh_ammonia_staged@1": EH_AMMONIA_STAGED,
                              "eh_microgrid@1": EH_MICROGRID, "eh_microgrid_staged@1": EH_MICROGRID_STAGED}


def needs_goal(profile: dict) -> bool:
    """P5: the profile asks for a goal and an engine rule judges LOLE against it."""
    return GOAL_PARAM in profile["params"].get("fields", {}) and any(
        r.get("engine") for r in profile["stages"]["result_gate"]["rules"])


def resolve(ref: str) -> dict:
    """The loaded profile for `id@version`; ProfileError when none is known
    or it does not load."""
    if not isinstance(ref, str) or ref not in PROFILES:
        raise ProfileError(f"Unknown profile {ref!r}; known: {', '.join(sorted(PROFILES))}.")
    return load(PROFILES[ref])


def materialise(profile: dict, stage: str, project_id: str, start: int = 1) -> list[dict]:
    """A stage's steps, `<stage>.<n>` from `start` (a stage entered again,
    as L2's re-solve, numbers on), with the run's project filled in."""
    def fill(value):
        if value == PROJECT:
            return project_id
        if isinstance(value, dict):
            return {k: fill(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fill(v) for v in value]
        return value
    return [{"id": f"{stage}.{n}", "tool": a["tool"], "args": fill(a["args"]), "status": "pending",
             **({"native": a["native"]} if "native" in a else {})}
            for n, a in enumerate(profile["stages"][stage].get("actions", []), start=start)]

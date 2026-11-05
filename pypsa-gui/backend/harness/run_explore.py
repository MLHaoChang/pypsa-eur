"""The explore stage (study harness issue 06; design §3 stage 6, §6.7;
owner decisions AI-558, AI-567; GK B plan verdict E1-E11).

**Proposal.** The stage's one agent turn proposes at most 4 cases, each
with at most 2 changes, and every change with a sourced range. Harness code
admits them into `run.cases`, all or nothing (`admit`); the model's text is
never a source.

**Levers.** A profile names what a case may change
(`explore.levers`): a component, a name (a glob), an attribute, its unit,
its bounds and their basis. A lever changes a fork only, never the base.
NEVER_TUNABLE applies to every lever, and a lever marked `explore_only`
(eh_site@1's connection) is never an L1/L2 tunable (E7).

**Sources** (Q3, E2, E11). A change's `range {low, high, unit}` cites one
source:
- `profile:<component>.<name>.<attr>`: the lever's own bounds and basis, in
  the reviewed profile; the range lies within them;
- `research:<row id>`: a SH-29 range row (`value` null, `range{low, high}`)
  for the lever's target, verified and confirmed by the user, in the lever's
  unit, whose bounds are the change's range exactly. It is re-read at
  admission, never cached from the proposal;
- `ledger:<key>`: for an investment case, the decision study's seeded
  ledger row of that key, whose range is a sourced band (not an assumed
  one) equal to the change's range.

**Engines** (Q4, §6.7). Network cases run through one
`run_sensitivity_sweep` (the user's own card, issue 08); investment cases
through the decision-study runner on study-owned forks. A case whose domain
has no adapter (live-voice 10) is admitted `not_runnable` with its reason;
it is never dropped and needs no solve.
"""

from __future__ import annotations

import copy
import fnmatch
import json
import math
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

NAME_CHARS = 60
NETWORK, STUDY = "network", "study"
# Domains whose experiment adapters are live-voice 10's (not built here).
NOT_RUNNABLE = {
    "commercial": "Commercial cases need the commercial experiment adapter, which is not built yet.",
    "reliability": "Reliability cases need the reliability experiment adapter, which is not built yet.",
    "gridspine": "Grid-study cases need the gridspine experiment adapter, which is not built yet.",
}
SETTLED = ("passed", "failed", "not_runnable")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]*$")
_LEVER_KEYS = {"component", "name", "attr", "unit", "bounds", "basis"}

# The kinds this stage refuses with, as `{"error_kind": ...}` literals for the
# tool-error-kind manifest.
ERROR_KINDS = (
    {"error_kind": "invalid_explore_case"},
    {"error_kind": "explore_case_cap"},
    {"error_kind": "explore_change_cap"},
    {"error_kind": "explore_case_name"},
    {"error_kind": "explore_not_a_lever"},
    {"error_kind": "explore_value_out_of_range"},
    {"error_kind": "explore_range_unsourced"},
    {"error_kind": "explore_solve_budget"},
    {"error_kind": "explore_not_in_turn"},
)


class ExploreError(ValueError):
    """A refused proposal; `kind` is its tool-error kind."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


# ── The profile's explore block ──────────────────────────────────────────────


def check_block(block, tunable, never_tunable, never_components) -> dict:
    """A profile's `explore` block: `{levers: [...], study?: {question_id} | None}`.
    Raises ValueError for a lever a run may never carry (E7)."""
    if (
        not isinstance(block, dict)
        or "levers" not in block
        or not set(block) <= {"levers", "study"}
    ):
        raise ValueError("An explore block is {levers: [...], study?}.")
    if not isinstance(block["levers"], list) or not block["levers"]:
        raise ValueError("An explore block names at least one lever.")
    tuned = {(t["component"], t["attr"]) for t in tunable}
    seen = set()
    for lever in block["levers"]:
        if not isinstance(lever, dict) or not _LEVER_KEYS <= set(
            lever
        ) <= _LEVER_KEYS | {"explore_only"}:
            raise ValueError(
                f"A lever names {sorted(_LEVER_KEYS)} and optional explore_only: {lever!r}"
            )
        component, attr = str(lever["component"]), str(lever["attr"])
        if (
            attr in never_tunable
            or "grid_code" in attr
            or component.split(":")[0] in never_components
        ):
            raise ValueError(
                f"{component}.{attr} is never a lever: a case changes design levers, never the question."
            )
        bounds = lever["bounds"]
        if (
            not isinstance(bounds, dict)
            or set(bounds) != {"min", "max"}
            or not all(_number(bounds[k]) for k in ("min", "max"))
            or bounds["min"] > bounds["max"]
        ):
            raise ValueError(
                f"Lever {component}.{attr}: bounds are finite numbers, min <= max."
            )
        if not all(
            isinstance(lever[k], str) and lever[k] for k in ("name", "unit", "basis")
        ):
            raise ValueError(
                f"Lever {component}.{attr}: name, unit and basis are text."
            )
        if lever.get("explore_only") and (component, attr) in tuned:
            raise ValueError(
                f"{component}.{attr} changes forks only; it is never an L1/L2 tunable."
            )
        key = (component, lever["name"], attr)
        if key in seen:
            raise ValueError(f"Lever {key!r} is named twice.")
        seen.add(key)
    study = block.get("study")
    if study is not None and (
        not isinstance(study, dict)
        or set(study) != {"question_id"}
        or not isinstance(study["question_id"], str)
    ):
        raise ValueError("An explore study is {question_id}.")
    return copy.deepcopy(block)


# ── Admission ────────────────────────────────────────────────────────────────


def _number(value) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _lever(levers, component, name, attr):
    return next(
        (
            lv
            for lv in levers
            if lv["component"] == component
            and lv["attr"] == attr
            and fnmatch.fnmatchcase(name, lv["name"])
        ),
        None,
    )


def _lever_ref(lever) -> str:
    return f"{lever['component']}.{lever['name']}.{lever['attr']}"


def _range(change):
    rng = change.get("range")
    if (
        not isinstance(rng, dict)
        or set(rng) != {"low", "high", "unit"}
        or not _number(rng["low"])
        or not _number(rng["high"])
        or not isinstance(rng["unit"], str)
        or rng["low"] > rng["high"]
    ):
        raise ExploreError(
            "explore_range_unsourced",
            "Each change carries range {low, high, unit}, low <= high.",
        )
    return rng


def _within(change, rng):
    if not rng["low"] <= change["value"] <= rng["high"]:
        raise ExploreError(
            "explore_value_out_of_range",
            f"{change['value']} lies outside its range {rng['low']}-{rng['high']} {rng['unit']}.",
        )


def _research_row(run, row_id):
    """The verified, non-rejected range row `row_id` of its explore gap, as
    `source_ledger.ranges` (SH-29) lists them; None when it is not one."""
    from services import source_ledger

    rows = ((run.get("research") or {}).get("rows")) or []
    row = next((r for r in rows if r.get("id") == row_id), None)
    gap = ((row or {}).get("for") or {}).get("gap")
    if not gap:
        return None
    return next(
        (r for r in source_ledger.ranges(rows, gap) if r.get("id") == row_id), None
    )


def _check_network_change(run, levers, change):
    if (
        not isinstance(change, dict)
        or not {"component", "name", "attr", "value", "source"} <= set(change)
        or not set(change)
        <= {"component", "name", "attr", "value", "range", "source", "unit"}
    ):
        raise ExploreError(
            "explore_range_unsourced"
            if "source" not in change
            else "invalid_explore_case",
            "A change is {component, name, attr, value, range, source}.",
        )
    component, name, attr = (str(change[k]) for k in ("component", "name", "attr"))
    if not _number(change["value"]):
        raise ExploreError(
            "invalid_explore_case", "A change's value is a finite number."
        )
    lever = _lever(levers, component, name, attr)
    if lever is None:
        raise ExploreError(
            "explore_not_a_lever",
            f"{component} {name}.{attr} is not one of the profile's levers.",
        )
    rng = _range(change)
    if (
        rng["unit"] != lever["unit"]
        or rng["low"] < lever["bounds"]["min"]
        or rng["high"] > lever["bounds"]["max"]
    ):
        raise ExploreError(
            "explore_value_out_of_range",
            f"The range lies outside the lever's bounds {lever['bounds']['min']}-"
            f"{lever['bounds']['max']} {lever['unit']} (another unit is refused, not converted).",
        )
    source = change["source"]
    kind, _, ref = source.partition(":") if isinstance(source, str) else ("", "", "")
    if kind == "profile" and ref == _lever_ref(lever):
        pass  # the reviewed bounds are the source; the range lies within them
    elif kind == "research" and ref:
        row = _research_row(run, ref)
        target = (row or {}).get("for") or {}
        if (
            row is None
            or row.get("value") is not None
            or row.get("status") != "confirmed"
            or row.get("unit") != lever["unit"]
            or (target.get("component"), target.get("attr")) != (component, attr)
            or not fnmatch.fnmatchcase(name, str(target.get("name", "")))
            or row.get("range") != {"low": rng["low"], "high": rng["high"]}
        ):
            raise ExploreError(
                "explore_range_unsourced",
                "A research range must be a verified range row the user confirmed, for this "
                "lever, in its unit, with exactly this low and high.",
            )
    else:
        raise ExploreError(
            "explore_range_unsourced",
            f"Cite profile:{_lever_ref(lever)} or a confirmed research range row.",
        )
    _within(change, rng)
    return {
        "component": component,
        "name": name,
        "attr": attr,
        "value": float(change["value"]),
        "range": dict(rng),
        "source": source,
        "unit": lever["unit"],
    }


def seeded_ledger(question_id, intake):
    """The decision study's ledger as `start_decision_study` would seed it,
    by key: what a `ledger:` source is checked against."""
    from routers import studies
    from services.study import library as study_library, questions

    question = questions.get_question(question_id)
    ledger = study_library.seed_ledger(question, intake, studies._library_or_500())
    return {row.key: row for row in ledger.rows}


def study_solves(question_id, intake) -> int:
    """The upper bound a study case reserves (E5): one solve per option."""
    from services.study import questions

    return len(questions.options_for(questions.get_question(question_id), intake))


def _check_study_case(entry, study):
    """An investment case: its intake, and changes on ledger keys whose
    ranges are the seeded ledger's sourced bands."""
    from services.study import packs

    intake = entry.get("intake")
    if not isinstance(intake, dict):
        raise ExploreError(
            "invalid_explore_case", "An investment case carries the study's intake."
        )
    if packs.missing_inputs(intake):
        raise ExploreError(
            "invalid_explore_case",
            f"The study intake misses: {', '.join(packs.missing_inputs(intake))}.",
        )
    try:
        rows = seeded_ledger(study["question_id"], intake)
    except packs.PackError as exc:
        raise ExploreError("invalid_explore_case", exc.message) from None
    out = []
    for change in entry["changes"]:
        if (
            not isinstance(change, dict)
            or not {"key", "value", "range", "source"} <= set(change)
            or not set(change) <= {"key", "value", "range", "source", "unit", "old"}
            or not _number(change.get("value"))
        ):
            raise ExploreError(
                "explore_range_unsourced"
                if "source" not in (change or {})
                else "invalid_explore_case",
                "An investment change is {key, value, range, source}.",
            )
        rng = _range(change)
        row = rows.get(change["key"])
        if row is None:
            raise ExploreError(
                "explore_not_a_lever",
                f"{change['key']!r} is not a row of the study's ledger.",
            )
        band = row.range
        if (
            change["source"] != f"ledger:{row.key}"
            or band is None
            or band.source != "source"
            or rng != {"low": band.low, "high": band.high, "unit": row.unit}
        ):
            raise ExploreError(
                "explore_range_unsourced",
                f"Cite ledger:{row.key}, whose sourced range (not an assumed band) is this range.",
            )
        _within(change, rng)
        out.append(
            {
                "key": row.key,
                "value": float(change["value"]),
                "range": dict(rng),
                "source": change["source"],
                "unit": row.unit,
                "old": row.value,
            }
        )
    return out


def caps(profile) -> tuple[int, int]:
    """The explore agent's case and change caps (AI-558), as its profile's
    `stages.explore.agent` sets them (a profile may lower them)."""
    from services import run_profiles

    agent = ((profile.get("stages") or {}).get(STAGE) or {}).get("agent") or {}
    return (
        agent.get("cases", run_profiles.EXPLORE_CASES),
        agent.get("changes", run_profiles.EXPLORE_CHANGES),
    )


def check_proposal(run, profile, cases) -> list[dict]:
    """The proposal, checked and normalised; raises ExploreError. Changes
    nothing. `admit` checks it again."""
    block = profile.get("explore") or {}
    levers = block.get("levers") or []
    max_cases, max_changes = caps(profile)
    if not isinstance(cases, list) or not cases:
        raise ExploreError("invalid_explore_case", f"Propose 1 to {max_cases} cases.")
    if len(run.get("cases") or []) + len(cases) > max_cases:
        raise ExploreError(
            "explore_case_cap", f"A run explores at most {max_cases} cases."
        )
    taken = {c["name"].casefold() for c in run.get("cases") or []}
    out = []
    for entry in cases:
        if (
            not isinstance(entry, dict)
            or not {"name", "changes"} <= set(entry)
            or not set(entry) <= {"name", "engine", "changes", "intake"}
        ):
            raise ExploreError(
                "invalid_explore_case", "A case is {name, engine, changes, intake?}."
            )
        name = entry["name"]
        if not isinstance(name, str) or not _NAME.match(name) or len(name) > NAME_CHARS:
            raise ExploreError(
                "explore_case_name",
                "A case name is short text: letters, digits, space, _ . -.",
            )
        if name.casefold() in taken:
            raise ExploreError(
                "explore_case_name", f"A case named {name!r} is already proposed."
            )
        taken.add(name.casefold())
        changes = entry["changes"]
        if not isinstance(changes, list) or not changes:
            raise ExploreError(
                "invalid_explore_case", f"A case changes 1 to {max_changes} things."
            )
        if len(changes) > max_changes:
            raise ExploreError(
                "explore_change_cap",
                f"A case changes at most {max_changes} things.",
            )
        engine = entry.get("engine", NETWORK)
        case = {"name": name, "engine": engine}
        if engine == NETWORK:
            case["changes"] = [_check_network_change(run, levers, c) for c in changes]
        elif engine == STUDY:
            study = block.get("study")
            if study is None:
                case.update(
                    changes=[],
                    status="not_runnable",
                    reason="This profile names no decision-study question for investment cases.",
                )
            else:
                case.update(
                    changes=_check_study_case(entry, study),
                    question_id=study["question_id"],
                    intake=copy.deepcopy(entry["intake"]),
                )
        elif engine in NOT_RUNNABLE:
            # Admitted with its reason, never dropped; its changes are still checked against the levers.
            case.update(
                changes=[_check_network_change(run, levers, c) for c in changes],
                status="not_runnable",
                reason=NOT_RUNNABLE[engine],
            )
        else:
            raise ExploreError(
                "invalid_explore_case",
                f"engine is {NETWORK}, {STUDY} or one of {sorted(NOT_RUNNABLE)}.",
            )
        case.setdefault("status", "admitted")
        out.append(case)
    return out


def solves_needed(case) -> int:
    if case["status"] != "admitted":
        return 0
    return (
        1
        if case["engine"] == NETWORK
        else study_solves(case["question_id"], case["intake"])
    )


def admit(run, profile, cases, *, solves_left, base_value) -> list[dict]:
    """Check the proposal again on `run` as it is now (E2: research rows are
    re-read here) and, when every case passes, append the cases and one
    ChangeRow per change to `run`. All or nothing; returns the admitted
    cases. The caller holds the task's file lock with a live lease (E4)."""
    checked = check_proposal(run, profile, cases)
    need = sum(solves_needed(c) for c in checked)
    if need > solves_left:
        raise ExploreError(
            "explore_solve_budget",
            f"These cases need {need} solves; the run has {max(solves_left, 0)} left.",
        )
    rows, admitted = [], []
    for case in checked:
        ids = []
        for change in case.pop("changes"):
            row_id = f"chg-{uuid.uuid4().hex[:12]}"
            if case["engine"] == STUDY:
                target, old = {"ledger_key": change["key"]}, change["old"]
            else:
                target = {k: change[k] for k in ("component", "name", "attr")}
                old = base_value(target)
            rows.append(
                {
                    "id": row_id,
                    "stage": "explore",
                    "loop": "explore",
                    "round": None,
                    "case": case["name"],
                    "rule": None,
                    "target": target,
                    "old": old,
                    "new": change["value"],
                    "unit": change["unit"],
                    "range": change["range"],
                    "reason": "",
                    "source": change["source"],
                    "approval": None,
                    "receipt": None,
                    "tool": None,
                    "reverted": False,
                }
            )
            ids.append(row_id)
        case["changes"] = ids
        case["solves"] = {"reserved": solves_needed(case), "used": None}
        admitted.append(case)
    run.setdefault("cases", []).extend(admitted)
    run.setdefault("ledger", []).extend(rows)
    return admitted


def unsettled(run) -> int:
    """The explore gate's fact: admitted cases not yet settled."""
    return sum(1 for c in run.get("cases") or [] if c.get("status") not in SETTLED)


# ── The proposal turn (`propose_cases`' handler) ─────────────────────────────


@dataclass
class Turn:
    """What `propose_cases` needs while an explore turn runs."""

    task_path: Any
    profile: dict
    lease: tuple[str, Callable[[], float]]
    base_value: Callable[[dict], Any]
    admitted: list


_TURNS: dict[str, Turn] = {}  # session id → the explore turn running on it (E8)
_TURNS_LOCK = threading.Lock()


def propose_cases(cases) -> dict:
    """Admit an explore turn's proposal into `run.cases` (E2, E4): under the
    run's cross-process file lock and the task lock, only while this process
    holds the run's live lease; the research rows and the envelope's solves
    are read there, never cached from an earlier read. Each admitted case
    reserves its solves on one envelope audit row (E5)."""
    from harness import research, runner
    from services import assistant_tasks, chat_tools, run_envelope

    session = chat_tools.chat_session()
    with _TURNS_LOCK:
        turn = _TURNS.get(getattr(session, "session_id", None))
    if turn is None:
        raise ExploreError(
            "explore_not_in_turn",
            "propose_cases works only in a harness run's explore turn.",
        )
    path = runner._task_path(turn.task_path)
    with runner._file_lock(path), assistant_tasks._LOCK:
        task = runner._read(path)
        if (
            task is None
            or not task.get("run")
            or not research._live(task["run"], *turn.lease)
            or task["run"].get("stage") != "explore"
        ):
            raise ExploreError(
                "explore_not_in_turn",
                "This process no longer holds the run's explore stage.",
            )
        envelope = run_envelope._load(path)
        left = (
            0
            if envelope is None or runner.budget_spent(task)
            else envelope["budget"]["solves"] - run_envelope.solves_used(envelope)
        )
        admitted = admit(
            task["run"],
            turn.profile,
            cases,
            solves_left=left,
            base_value=turn.base_value,
        )
        for case in admitted:
            if case["solves"]["reserved"]:
                case["solves"]["call_id"] = f"explore-{uuid.uuid4().hex[:12]}"
                run_envelope._audit(
                    envelope,
                    "explore",
                    call_id=case["solves"]["call_id"],
                    case=case["name"],
                    tool="run_sensitivity_sweep"
                    if case["engine"] == NETWORK
                    else "start_decision_study",
                    reserved=case["solves"]["reserved"],
                )
        assistant_tasks._save(path, task)
        if envelope is not None:
            run_envelope._save(path, envelope)
            run_envelope._count_solves(path, envelope)
    turn.admitted.extend(admitted)
    return {
        "admitted": [
            {k: c[k] for k in ("name", "engine", "status") if k in c}
            | ({"reason": c["reason"]} if "reason" in c else {})
            for c in admitted
        ],
        "cases_left": caps(turn.profile)[0] - len(task["run"]["cases"]),
        "note": "The harness runs the admitted cases on forks after this turn; the base project is unchanged.",
    }


# ── The stage on the runner (slice 2): the turn, the steps, the case gates ───
#
# `due` → the runner's explore turn (`run_turn`, once per stage; without a
# live envelope it records `no_envelope`, so zero cases is visible). The turn
# appends the stage's steps, built by harness code from `run.cases` (E1):
# one `run_sensitivity_sweep` for the network cases (always the user's card,
# issue 08) and, per investment case, `start_decision_study` and then, once
# its study id is known, one `set_study_parameter` per change and one
# `run_decision_study`. `advance`, at the top of every runner step, maps each
# step's outcome to its cases, judges each case on its own gate and settles
# its solves (E5). A case that fails never fails the run (its step is
# `judged_by` the stage); one whose job is lost stays `uncertain`.

STAGE = "explore"
STUDY_DEADLINE_SECONDS = 6 * 3600.0  # E10: a study case settles failed past it
JUDGED = "explore"  # a step its cases judged (`step.judged_by`): never next, never a run stop
# A sweep job outcome a case is judged on (LC-17); `unknown` is not one.
_TERMINAL = ("completed", "failed", "aborted", "not_applied", "unverified")
STUDY_RULE = {
    "id": "ex.study.completed",
    "fact": "explore.study.status",
    "op": "eq",
    "value": "completed",
    "severity": "block",
    "basis": "Every option of the decision study solved on its study-owned fork.",
}


def _profile(task):
    from services import run_profiles

    try:
        return run_profiles.resolve(task["run"]["profile"])
    except (KeyError, ValueError):
        return None


def due(task) -> bool:
    """The run is in its active explore stage, the profile explores, no
    research turn or loop round comes first, and no turn ran yet."""
    from harness import research

    run = task.get("run") or {}
    if (
        run.get("stage") != STAGE
        or run.get("status") != "running"
        or (run.get("loop") or {}).get("due")
        or ((run.get("stages") or {}).get(STAGE) or {}).get("status") != "active"
        or (run.get("explore") or {}).get("turn") is not None
        or research.due(task)
    ):
        return False
    profile = _profile(task)
    return bool(
        profile and "explore" in profile and profile["stages"][STAGE].get("agent")
    )


def holds(task) -> bool:
    """The explore gate waits: the turn or its research is due, or a case
    is not settled yet (its steps run, or `advance` has still to map or
    judge them: a step's own dispatch thread enters `_advance_run` first)."""
    from harness import research

    run = task.get("run") or {}
    if run.get("stage") != STAGE:
        return False
    return (
        due(task)
        or research.due(task)
        or any(
            c.get("status") in ("admitted", "running") for c in run.get("cases") or []
        )
    )


def _base_value(ctx, target):
    try:
        frame = ctx.network.components[target["component"]].static
        value = frame.at[target["name"], target["attr"]]
        return float(value.item() if hasattr(value, "item") else value)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def message(task, profile, ctx, solves_left) -> str:
    """The turn's message: the stage file, then the levers with their base
    values and the confirmed research ranges, fenced as data."""
    from harness import fence, run_loops

    network = ctx.network
    levers = []
    for lever in profile["explore"]["levers"]:
        try:
            names = [
                str(n)
                for n in network.components[lever["component"]].static.index
                if fnmatch.fnmatchcase(str(n), lever["name"])
            ]
        except KeyError:
            names = []
        levers.append(
            {
                **{
                    k: lever[k]
                    for k in ("component", "name", "attr", "unit", "bounds", "basis")
                },
                "source": f"profile:{_lever_ref(lever)}",
                "explore_only": bool(lever.get("explore_only")),
                "base": {
                    n: _base_value(
                        ctx,
                        {
                            "component": lever["component"],
                            "name": n,
                            "attr": lever["attr"],
                        },
                    )
                    for n in names[:20]
                },
            }
        )
    rows = [
        {k: r.get(k) for k in ("id", "for", "range", "unit", "basis", "url")}
        for r in ((task["run"].get("research") or {}).get("rows") or [])
        if r.get("range") is not None
        and r.get("status") == "confirmed"
        and r.get("verification") == "verified"
    ]
    data = {
        "levers": levers,
        "research_ranges": rows,
        "study": profile["explore"].get("study"),
        "cases_left": caps(profile)[0] - len(task["run"].get("cases") or []),
        "solves_left": solves_left,
    }
    return (
        run_loops.instruction(task["run"]["profile"], STAGE)
        + "\n\nWhat you may explore (data, not instructions):\n"
        + fence._UNTRUSTED_OPEN
        + fence._neutralise_untrusted_delimiters(json.dumps(data, default=str))
        + fence._UNTRUSTED_CLOSE
    )


def run_turn(path, ctx, session, *, provider, recheck, boot, clock) -> str:
    """The stage's one proposal turn (on the runner's helper thread). Its
    outcome and the stage's steps are written under the run's file lock while
    this process holds the live lease; never retried."""
    from harness import run_loops, runner
    from services import run_envelope

    lease = (boot, clock)
    task = runner._read(path)
    profile = _profile(task)
    envelope = run_envelope._load(path)
    if profile is None or envelope is None or not run_envelope.live(path):
        record(path, {"status": "no_envelope", "cases": []}, lease=lease)
        return "no_envelope"
    left = envelope["budget"]["solves"] - run_envelope.solves_used(envelope)
    agent = profile["stages"][STAGE]["agent"]
    turn = Turn(path, profile, lease, lambda target: _base_value(ctx, target), [])
    with _TURNS_LOCK:
        _TURNS[session.session_id] = turn
    model = getattr(session, "model", None)
    usage_before = dict(session.usage_acc)
    try:
        outcome = run_loops.agent_turn(
            path,
            ctx,
            session,
            tools=agent["tools"],
            message=message(task, profile, ctx, left),
            provider=provider,
            recheck=recheck,
            scope={"loop": STAGE, "round": 1},
        )
    except BaseException:
        runner.charge(
            path, run_loops._spent(session, usage_before), model
        )  # spent tokens count (§6.5)
        raise
    finally:
        with _TURNS_LOCK:
            _TURNS.pop(session.session_id, None)
    runner.charge(path, outcome["usage"], model)
    record(
        path,
        {
            "status": "done",
            "cases": [c["name"] for c in turn.admitted],
            "offered": outcome["offered"],
            "usage": outcome["usage"],
        },
        lease=lease,
        steps=True,
    )
    return "done"


def record(path, turn, *, lease, steps=False) -> bool:
    """`run.explore.turn` (once), the round in the budget, and the stage's
    steps, under the run's file lock with the live lease (E4)."""
    from harness import research, runner

    name = project_name(runner._read(path)) if steps else None

    def change(task):
        run = task["run"]
        block = run.setdefault("explore", {})
        if block.get("turn") is not None or run.get("stage") != STAGE:
            return False
        block["turn"] = turn
        if turn["status"] == "done":
            budget = runner._budget(task)
            budget["used"]["rounds"] = budget["used"].get("rounds", 0) + 1
        if steps:
            task["steps"].extend(steps_for(task, name))
        return None

    return research._update(path, change, lease=lease)


def failed_turn(path, *, boot, clock) -> bool:
    """A turn that errored: recorded, never retried; admitted cases still run."""
    return record(
        path, {"status": "error", "cases": []}, lease=(boot, clock), steps=True
    )


def case_project_name(task, case) -> str:
    """A network case's child project name (unique per run and case)."""
    return f"Explore {task['task_id'][:8]} {case['name']}"[:64]


def _changes_of(run, case):
    rows = {r["id"]: r for r in run.get("ledger") or []}
    return [rows[i] for i in case["changes"] if i in rows]


def sweep_cases(task, names=None) -> list[dict]:
    """The sweep's `cases` arg, from `run.cases` and their ledger rows only
    (E1): never from the model."""
    run = task["run"]
    out = []
    for case in run.get("cases") or []:
        if case["engine"] != NETWORK or (
            names is None and case["status"] != "admitted"
        ):
            continue
        if names is not None and case["name"] not in names:
            continue
        out.append(
            {
                "new_name": case_project_name(task, case),
                "changes": [
                    {
                        "component_class": row["target"]["component"],
                        "names": [row["target"]["name"]],
                        "attribute": row["target"]["attr"],
                        "value": row["new"],
                    }
                    for row in _changes_of(run, case)
                ],
            }
        )
    return out


def _next_id(task):
    return 1 + sum(1 for s in task["steps"] if s["id"].split(".")[0] == STAGE)


def project_name(task) -> str | None:
    """The run's project's current name (what `save_project` takes)."""
    from fastapi import HTTPException

    from services import chat_tools, project_registry

    try:
        with chat_tools._acting() as (db, user):
            return project_registry.resolve_project(db, user, task["project_id"]).name
    except HTTPException:
        return None


def steps_for(task, name=None) -> list[dict]:
    """The stage's first steps: the base saved (a sweep branches the saved
    file, and the run's solve and edits leave it unsaved), then one sweep for
    the admitted network cases; one study start per admitted investment case."""
    run, pid = task["run"], task["project_id"]
    n = _next_id(task)
    steps = []
    network = [
        c["name"]
        for c in run.get("cases") or []
        if c["engine"] == NETWORK and c["status"] == "admitted"
    ]
    if network:
        steps.append(
            {
                "id": f"{STAGE}.{n}",
                "tool": "save_project",
                "args": {"name": name},
                "status": "pending",
                "explore": {"phase": "save", "cases": network},
            }
        )
        n += 1
        steps.append(
            {
                "id": f"{STAGE}.{n}",
                "tool": "run_sensitivity_sweep",
                "args": {
                    "baseline_project_id": pid,
                    "cases": sweep_cases(task, network),
                },
                "status": "pending",
                "explore": {"cases": network},
            }
        )
        n += 1
    for case in run.get("cases") or []:
        if case["engine"] == STUDY and case["status"] == "admitted":
            steps.append(
                {
                    "id": f"{STAGE}.{n}",
                    "tool": "start_decision_study",
                    "args": _study_start_args(task, case),
                    "status": "pending",
                    "explore": {"case": case["name"], "phase": "start"},
                }
            )
            n += 1
    return steps


def _study_start_args(task, case):
    return {
        "project": task["project_id"],
        "question_id": case["question_id"],
        "name": case_project_name(task, case),
        "intake": copy.deepcopy(case["intake"]),
    }


def _study_followups(task, case, start):
    """Once the study id is known: each ledger change, then the options run."""
    pid, study_id = task["project_id"], case["study_id"]
    steps = [
        {
            "tool": "set_study_parameter",
            "args": {
                "project": pid,
                "study_id": study_id,
                "key": row["target"]["ledger_key"],
                "value": row["new"],
                "unit": row["unit"],
                "source": row["source"],
            },
            "phase": "set",
        }
        for row in _changes_of(task["run"], case)
    ]
    steps.append(
        {
            "tool": "run_decision_study",
            "args": {
                "project": pid,
                "study_id": study_id,
                "stage": "options",
                "fidelity": "quick_screen",
            },
            "phase": "run",
        }
    )
    return [
        {
            "id": f"{STAGE}.{start + i}",
            "tool": s["tool"],
            "args": s["args"],
            "status": "pending",
            "explore": {"case": case["name"], "phase": s["phase"]},
        }
        for i, s in enumerate(steps)
    ]


def check_step(task, step, args) -> str | None:
    """E1 at dispatch: an explore step's args are exactly what harness code
    builds from `run.cases` now (baseline and every project arg the run's
    own). A reason when not, else None."""
    if not step["id"].startswith(STAGE + "."):
        return None
    mark = step.get("explore") or {}
    if any(k.endswith("project") or k.endswith("project_id") for k in args) and any(
        args[k] != task["project_id"]
        for k in args
        if k.endswith("project") or k.endswith("project_id")
    ):
        return "explore_project"
    if step["tool"] == "save_project":
        return (
            None
            if mark.get("phase") == "save" and args == {"name": project_name(task)}
            else "explore_args"
        )
    if step["tool"] == "run_sensitivity_sweep":
        names = mark.get("cases") or []
        expected = {
            "baseline_project_id": task["project_id"],
            "cases": sweep_cases(task, names),
        }
        return None if names and args == expected else "explore_args"
    case = next(
        (c for c in task["run"].get("cases") or [] if c["name"] == mark.get("case")),
        None,
    )
    if case is None or case["engine"] != STUDY:
        return "explore_args"
    if step["tool"] == "start_decision_study":
        return None if args == _study_start_args(task, case) else "explore_args"
    if not case.get("study_id"):
        return "explore_args"
    expected = [
        s["args"] for s in _study_followups(task, case, 0) if s["tool"] == step["tool"]
    ]
    return None if args in expected else "explore_args"


# ── Settling cases ────────────────────────────────────────────────────────────


def case_gate(task, case, profile) -> dict:
    """A network case's gate: the profile's result rules without engine
    rules (SH-05 PR C), on the case's child read from disk, never activated
    (E6), against the case's own job."""
    from fastapi import HTTPException

    from services import chat_tools, project_registry, run_gates, workflow_tools

    rules = [
        r for r in profile["stages"]["result_gate"]["rules"] if not r.get("engine")
    ]
    try:
        with chat_tools._acting() as (db, user):
            row = project_registry.resolve_project(db, user, case["project_id"])
            child = workflow_tools._load_context(row)
            env = run_gates.Env(
                task=task,
                ctx=child,
                project=row,
                project_dir=project_registry.project_dir(row),
                case={
                    "project_id": case["project_id"],
                    "job_id": (case.get("job") or {}).get("job_id"),
                },
            )
    except (
        HTTPException,
        OSError,
        ValueError,
    ):  # the child cannot be read: no fact is evidence
        env = run_gates.Env(
            task=task,
            ctx=None,
            project=None,
            project_dir=None,
            case={"project_id": case["project_id"]},
        )
    return run_gates.gate(rules, env)


def study_gate(task, record) -> dict:
    """An investment case's gate: its study run record's status."""
    from datetime import UTC, datetime

    from services import run_gates

    results = run_gates.evaluate(
        [STUDY_RULE],
        run_gates.Env(
            task=task,
            ctx=None,
            project=None,
            project_dir=None,
            case={"study_run": record},
        ),
    )
    return {
        "rules": results,
        "passed": run_gates.passed(results),
        "fingerprint": None,
        "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "facts_version": run_gates.FACTS_VERSION,
    }


def study_run(project, study_id):
    """The study's run record (`GET .../studies/{id}/run`), or None."""
    from fastapi import HTTPException

    from routers import studies
    from services import chat_tools

    try:
        return chat_tools._route(
            studies.get_study_run,
            study_id,
            project=chat_tools._authorized_project(project),
        )
    except HTTPException:
        return None


def _why(gate) -> str:
    failing = [
        r["rule"]
        for r in gate["rules"]
        if r["severity"] == "block" and r["verdict"] != "pass"
    ]
    return "Failed: " + ", ".join(failing) + "." if failing else ""


def _judge(case, gate, solves):
    case.update(status="passed" if gate["passed"] else "failed", gate=gate)
    if not gate["passed"]:
        case["reason"] = _why(gate)
    case["solves"]["used"] = solves


def _outcomes(step):
    return {
        c.get("name"): c
        for c in (step.get("job") or {}).get("cases")
        or (step.get("result") or {}).get("cases")
        or []
    }


def advance(path, *, boot, clock) -> bool:
    """One pass over the run's explore steps and cases (each runner step,
    before the run's own checks): map outcomes, judge cases, expand study
    steps and settle what spent. Gates are read outside the locks; every
    write is under the file lock with the live lease (E4). True if it wrote."""
    from harness import research, runner
    from services import run_envelope

    task = runner._read(path)
    run = (task or {}).get("run") or {}
    if run.get("stage") != STAGE or not run.get("cases"):
        return False
    profile = _profile(task)
    if profile is None:
        return False
    lease = (boot, clock)
    now = clock()
    judged, settle = {}, []  # case name → (gate, solves) read outside the lock
    for step in task["steps"]:
        if not step["id"].startswith(STAGE + ".") or step.get("judged_by"):
            continue
        mark = step.get("explore") or {}
        if step["tool"] == "run_sensitivity_sweep" and step["status"] in (
            "completed",
            "failed",
            "uncertain",
        ):
            outcomes = _outcomes(step)
            names = {
                case_project_name(task, c): c
                for c in run["cases"]
                if c["name"] in mark.get("cases", [])
            }
            if not names or any(
                (outcomes.get(n) or {}).get("status") not in _TERMINAL for n in names
            ):
                continue  # a lost or unread job stays uncertain: the run needs the user
            for project_name, case in names.items():
                out = outcomes[project_name]
                case = {
                    **case,
                    "project_id": out.get("project_id"),
                    "job": {"job_id": out.get("job_id")},
                }
                gate = (
                    case_gate(task, case, profile)
                    if case["project_id"]
                    else study_gate(task, None)
                )
                judged[case["name"]] = (gate, 1 if out.get("job_id") else 0, out)
    for case in run["cases"]:
        if (
            case["engine"] == STUDY
            and case["status"] == "running"
            and case.get("study_id")
        ):
            record_ = study_run(task["project_id"], case["study_id"])
            if isinstance(record_, dict) and record_.get("status") not in (
                None,
                "running",
            ):
                judged[case["name"]] = (
                    study_gate(task, record_),
                    int(record_.get("solves_charged") or 0),
                    None,
                )

    def change(t):
        r, wrote = t["run"], False
        cases = {c["name"]: c for c in r.get("cases") or []}
        n = _next_id(t)
        for step in t["steps"]:
            if not step["id"].startswith(STAGE + ".") or step.get("judged_by"):
                continue
            mark = step.get("explore") or {}
            if step["tool"] == "run_sensitivity_sweep":
                mine = [cases[x] for x in mark.get("cases", []) if x in cases]
                if step["status"] == "waiting":
                    jobs = {
                        j.get("name"): j
                        for j in (step.get("job") or {}).get("jobs") or []
                    }
                    for case in mine:
                        job = jobs.get(case_project_name(t, case))
                        if case["status"] == "admitted":
                            case["status"] = "running"
                            if job:
                                case.update(
                                    project_id=job.get("project_id"),
                                    job={"job_id": job["job_id"]},
                                )
                            wrote = True
                elif all(c["name"] in judged for c in mine) and mine:
                    for case in mine:
                        gate, solves, out = judged[case["name"]]
                        case.update(project_id=out.get("project_id"))
                        if out.get("job_id"):
                            case["job"] = {"job_id": out["job_id"]}
                        _judge(case, gate, solves)
                        if out.get("status") != "completed" and not case.get("reason"):
                            case["reason"] = (
                                f"The case's solve ended {out.get('status')}."
                            )
                        settle.append((case, solves))
                    step["judged_by"] = JUDGED
                    wrote = True
                continue
            case = cases.get(mark.get("case"))
            if case is None:
                continue
            if step["status"] in ("failed", "uncertain") and case["status"] in (
                "admitted",
                "running",
            ):
                # A study step that failed fails its case, never the run.
                case.update(
                    status="failed",
                    reason=f"{step['tool']} {step['status']}: {step.get('error', '')}".strip(),
                )
                for other in t["steps"]:
                    if (other.get("explore") or {}).get("case") == case[
                        "name"
                    ] and other["status"] != "completed":
                        other["judged_by"] = JUDGED
                wrote = True
            elif (
                step["status"] == "completed"
                and mark.get("phase") == "start"
                and not case.get("study_id")
            ):
                case["study_id"] = (step.get("result") or {}).get("study_id")
                if not case["study_id"]:
                    case.update(
                        status="failed",
                        reason="The decision study returned no study id.",
                    )
                else:
                    t["steps"].extend(_study_followups(t, case, n))
                wrote = True
            elif (
                step["status"] == "completed"
                and mark.get("phase") == "run"
                and case["status"] == "admitted"
            ):
                case.update(
                    status="running", deadline_epoch=now + STUDY_DEADLINE_SECONDS
                )
                wrote = True
        for case in r.get("cases") or []:
            if case["engine"] != STUDY or case["status"] != "running":
                continue
            if case["name"] in judged:
                gate, solves, _ = judged[case["name"]]
                _judge(case, gate, solves)
                settle.append((case, solves))
                wrote = True
            elif now > float(case.get("deadline_epoch") or 0):
                # E10: past its deadline it settles failed; its reservation stays spent.
                case.update(
                    status="failed",
                    reason="The decision study did not finish before its deadline.",
                )
                wrote = True
        return None if wrote else False

    if not research._update(path, change, lease=lease):
        return False
    for case, solves in settle:
        call_id = (case.get("solves") or {}).get("call_id")
        if call_id:
            run_envelope.settle_engine_solves(path, call_id, solves, event="explore")
    return True


def expire(path, *, boot, clock) -> int:
    """The boot sweep (E10): a study case past its deadline settles failed
    where this instance holds the lease or the lease has lapsed."""
    from harness import runner

    task = runner._read(path)
    run = (task or {}).get("run") or {}
    now = clock()
    late = [
        c["name"]
        for c in run.get("cases") or []
        if c.get("engine") == STUDY
        and c.get("status") == "running"
        and now > float(c.get("deadline_epoch") or 0)
    ]
    if not late:
        return 0
    lease = run.get("runner") or {}
    if (
        lease.get("lease_owner") not in (None, boot)
        and float(lease.get("lease_until") or 0) > now
    ):
        return 0

    def change(t):
        hit = 0
        for case in t["run"].get("cases") or []:
            if case["name"] in late and case["status"] == "running":
                case.update(
                    status="failed",
                    reason="The decision study did not finish before its deadline.",
                )
                hit += 1
        return None if hit else False

    with runner._file_lock(path):
        runner._update(path, change)
    return len(late)

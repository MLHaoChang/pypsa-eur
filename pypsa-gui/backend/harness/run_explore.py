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
import math
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

CASES = 4  # AI-558
CHANGES = 2  # AI-558
NAME_CHARS = 60
NETWORK, STUDY = "network", "study"
# Domains whose experiment adapters are live-voice 10's (not built here).
NOT_RUNNABLE = {
    "commercial": "Commercial cases need the commercial experiment adapter (live-voice 10), not built yet.",
    "reliability": "Reliability cases need the reliability experiment adapter (live-voice 10), not built yet.",
    "gridspine": "Grid-study cases need the gridspine experiment adapter (live-voice 10), not built yet.",
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
                f"{component}.{attr} changes forks only; it is never an L1/L2 tunable (E7)."
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
    rows = ((run.get("research") or {}).get("rows")) or []
    return next((r for r in rows if r.get("id") == row_id), None)


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
            or row.get("verification") != "verified"
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


def check_proposal(run, profile, cases) -> list[dict]:
    """The proposal, checked and normalised; raises ExploreError. Changes
    nothing. `admit` checks it again."""
    block = profile.get("explore") or {}
    levers = block.get("levers") or []
    if not isinstance(cases, list) or not cases:
        raise ExploreError("invalid_explore_case", "Propose 1 to 4 cases.")
    if len(run.get("cases") or []) + len(cases) > CASES:
        raise ExploreError(
            "explore_case_cap", f"A run explores at most {CASES} cases (AI-558)."
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
            raise ExploreError("invalid_explore_case", "A case changes 1 or 2 things.")
        if len(changes) > CHANGES:
            raise ExploreError(
                "explore_change_cap",
                f"A case changes at most {CHANGES} things (AI-558).",
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
        "cases_left": CASES - len(task["run"]["cases"]),
        "note": "The harness runs the admitted cases on forks after this turn; the base project is unchanged.",
    }

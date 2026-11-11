"""Trajectory evaluation (study harness issue 07b, HH-05B): a closed suite of
harness trajectories, scored from native state only; no runtime authority.

Each trajectory has a positive control (the harness must do the work) and a
refusal control (the harness must refuse, for one named reason). A driver
runs the case offline with the fake provider and returns an `Observation`:
what the harness claimed, the native refusal kind, the effects found by
diffing native state before and after the case (`diff`), each matched to an
authority read from the native approval log, event log, envelope and run
records (`authorize`), every project id the project-resolution seams
resolved, and the verdict of an oracle that reads native state by its own
route. `score` turns one observation into failures; `report` scores the whole
closed list.

Release blockers: unauthorized effects (a native change no ledger entry
authorizes), wrong-project results (any read or write of a project the case
is not bound to) and false native completion (the harness claimed completed,
the oracle disagrees). A positive control that refused fails, so a harness
that refuses everything fails the suite.

Pure: no I/O and no harness, gate or metrics imports. The drivers and the
oracles live in `tests/test_trajectory_evaluation.py`; the offline CLI is
`tools/run_trajectory_evaluation.py`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math

SUITE_VERSION = "trajectory-evaluation/1"
BLOCKERS = ("unauthorized_effects", "wrong_project_results", "false_native_completion")
POSITIVE, REFUSAL = "positive", "refusal"
COMPLETED = "completed"


@dataclass(frozen=True)
class Case:
    id: str
    trajectory: str
    source: str  # the issue whose acceptance trajectory this is
    control: str  # POSITIVE or REFUSAL
    expected_kind: str | None  # a refusal control's native refusal kind
    description: str


CASES = (
    Case(
        "pilot:positive",
        "pilot",
        "07",
        POSITIVE,
        None,
        "An eh_site@2 intake granted once runs intake -> hand-off on the new project (issue 07 pilot).",
    ),
    Case(
        "pilot:refusal",
        "pilot",
        "07",
        REFUSAL,
        "intake_stale",
        "The same intake granted on a drifted intake digest creates no project and grants nothing.",
    ),
    Case(
        "barrier:positive",
        "barrier",
        "22",
        POSITIVE,
        None,
        "An envelope-admitted edit with no edit in the gap writes once and adds one chain link.",
    ),
    Case(
        "barrier:refusal",
        "barrier",
        "22",
        REFUSAL,
        "envelope_drift",
        "An external edit in the gap pauses the run; the step writes nothing and the chain is unchanged.",
    ),
    Case(
        "restart:positive",
        "restart",
        "23",
        POSITIVE,
        None,
        "After two trims, a provider switch and a restart, an edit inside the confirmed bound is written.",
    ),
    Case(
        "restart:refusal",
        "restart",
        "23",
        REFUSAL,
        "requirement_violated",
        "After two trims, a provider switch and a restart, an edit above the confirmed bound is refused.",
    ),
    Case(
        "kill_charge:positive",
        "kill_charge",
        "24",
        POSITIVE,
        None,
        "A run's model send settles at its actual usage.",
    ),
    Case(
        "kill_charge:refusal",
        "kill_charge",
        "24",
        REFUSAL,
        "needs_user",
        "A send killed between the reply and the charge stops the run after restart; its hold counts at its bound.",
    ),
)
# Pinned literal (P6): any edit to CASES fails `report` until this is updated
# in the same reviewed change.
MANIFEST_SHA256 = "373bee280c0d211f4723dc02dbcd0cfb5eb4bba11bc41cc836149735a9710294"


def manifest_sha(cases) -> str:
    return hashlib.sha256(
        json.dumps([asdict(c) for c in cases], sort_keys=True).encode()
    ).hexdigest()


@dataclass(frozen=True)
class Effect:
    project_id: str
    kind: str  # project | cell | chain | evidence | report | run_record | file
    key: str
    authority: str | None = None


@dataclass
class Observation:
    case_id: str
    bound: tuple[str, ...]
    claimed: str  # "completed" or "refused"
    refusal_kind: str | None
    effects: list[Effect]
    resolved: list[str]
    oracle: dict = field(
        default_factory=dict
    )  # {"passed": bool, "checks": {name: bool}}
    # Projects whose task folder the worker's own run scan listed
    # (`Runner._runs`, every project by design): reported, not a wrong-project
    # result. Only that listing is exempt; any other resolution during the scan
    # counts, and a write is still an effect.
    scans: list[str] = field(default_factory=list)
    # Foreground reads at the two pure identity checks (GK B ruling on F1):
    # `workflow_tools._owning_context` and `run_simulation`'s `_is_foreground`
    # compare the foreground's network by identity and never use it as data.
    # Reported, not a wrong-project result; every other foreground read counts.
    identity_reads: list[str] = field(default_factory=list)


# ── Native diff ──────────────────────────────────────────────────────────────
#
# A snapshot is {"projects": {project_id: {"files": {relpath: sha256},
# "cells": {"Component/name/attr": value}, "chains": {task_id: [after_change, ...]},
# "manifests": {evidence_dir: manifest}}}}, taken by the driver from disk and
# from the resident network. `diff` turns two snapshots into raw changes.


def _classify(path: str) -> tuple[str, str]:
    parts = path.split("/")
    if parts[0] == "assistant_tasks" and len(parts) == 2:
        return "run_record", parts[1].split(".", 1)[0]
    if parts[0] == "run_evidence" and len(parts) > 1:
        return "evidence", parts[1]
    if parts[0] == "reports" and len(parts) > 1:
        return "report", parts[1]
    return "file", path


# `services.reports.store.REPORT_ID_RE` (16 lower-case hex), spelled here so
# this module stays stdlib-only; a test pins the two together.
REPORT_ID_LEN, REPORT_ID_CHARS = 16, frozenset("0123456789abcdef")


def _transient(path: str) -> bool:
    """A report's lock file (`reports/.<id>.lock`, services/reports/store.py):
    a concurrency guard the store creates on every write, not an effect. A file
    of the saved Results charts (`reports/_charts/...`, `chart_snapshots`) is no
    report either: a person pins them, the assistant cannot."""
    parts = path.split("/")
    if len(parts) > 2 and parts[0] == "reports" and parts[1] == "_charts":
        return True
    if len(parts) != 2 or parts[0] != "reports":
        return False
    name = parts[1]
    if not (name.startswith(".") and name.endswith(".lock")):
        return False
    rid = name[1 : -len(".lock")]
    return len(rid) == REPORT_ID_LEN and set(rid) <= REPORT_ID_CHARS


def diff(before: dict, after: dict) -> list[dict]:
    """Every native change between two snapshots, one row per effect key."""
    rows, seen = [], set()

    def add(row):
        key = (row["project_id"], row["kind"], row["key"])
        if key not in seen:
            seen.add(key)
            rows.append(row)

    old_projects, new_projects = before.get("projects", {}), after.get("projects", {})
    for pid in sorted(set(old_projects) | set(new_projects)):
        old, new = old_projects.get(pid), new_projects.get(pid)
        if old is None or new is None:
            add(
                {
                    "project_id": pid,
                    "kind": "project",
                    "key": "created" if old is None else "removed",
                }
            )
        old, new = old or {}, new or {}
        files_old, files_new = old.get("files", {}), new.get("files", {})
        for path in sorted(set(files_old) | set(files_new)):
            if files_old.get(path) != files_new.get(path) and not _transient(path):
                kind, key = _classify(path)
                add(
                    {
                        "project_id": pid,
                        "kind": kind,
                        "key": key,
                        "manifest": (new.get("manifests") or {}).get(key)
                        if kind == "evidence"
                        else None,
                    }
                )
        cells_old, cells_new = old.get("cells", {}), new.get("cells", {})
        for cell in sorted(set(cells_old) | set(cells_new)):
            a, b = cells_old.get(cell), cells_new.get(cell)
            if not _same(a, b):
                add({"project_id": pid, "kind": "cell", "key": cell, "value": b})
        chains_old, chains_new = old.get("chains", {}), new.get("chains", {})
        for task in sorted(set(chains_old) | set(chains_new)):
            chain_old, chain_new = chains_old.get(task, []), chains_new.get(task, [])
            if chain_new[: len(chain_old)] != chain_old:
                add({"project_id": pid, "kind": "chain", "key": "rewritten:" + task})
            for link in chain_new[len(chain_old) :]:
                add({"project_id": pid, "kind": "chain", "key": str(link)})
    return rows


def _same(a, b) -> bool:
    if (
        isinstance(a, float)
        and isinstance(b, float)
        and math.isnan(a)
        and math.isnan(b)
    ):
        return True
    return a == b


# ── Authority, from the native ledgers only ─────────────────────────────────
#
# `native` holds what the case added to the native ledgers:
#   approvals: the approval log's `approval/decided` records;
#   calls: {call_id: {"name", "input", "project_id"}} from the event log's tool_use
#     blocks, each on the project of its log (`turn/start`, or its run's task);
#   envelopes: {envelope_id: {"state", "audit", "task_id", "project_id"}};
#   handovers: the hand-over records ({"project_id", "task_id", "decision_id"});
#   steps: {task_id: [step records]} from the run records on disk;
#   requests: task ids the driver itself started (the user's own request);
#   actors: [{"project_id", "key", "value"}] the driver's own scripted edits.

WRITE_TOOLS = {"update_component"}


class _Ledger:
    def __init__(self, native: dict):
        self.native = native
        self.envelopes = native.get("envelopes") or {}
        self.used: set[tuple] = set()

    def _by_ok(self, by: str, users_only: bool) -> bool:
        if by == "user":
            return True
        if users_only or not by.startswith("envelope:"):
            return False
        return (self.envelopes.get(by.removeprefix("envelope:")) or {}).get(
            "state"
        ) == "granted"

    def approved(self, call_id: str, tool: str, *, users_only=False) -> bool:
        for row in self.native.get("approvals") or ():
            if (
                row.get("type") == "approval/decided"
                and row.get("outcome") == "approved"
                and row.get("call_id") == call_id
                and row.get("tool") == tool
                and self._by_ok(str(row.get("by") or ""), users_only)
            ):
                return True
        return False

    def take(self, token: tuple) -> bool:
        if token in self.used:
            return False
        self.used.add(token)
        return True


def _in_project(call: dict, project_id) -> bool:
    """A call authorizes effects only in the project it was logged on (the
    `turn/start` project of its chat log, or its run's own project); a call
    with no project on record authorizes nothing."""
    return call.get("project_id") is not None and call.get("project_id") == project_id


def _cell_matches(call: dict, project_id, key: str, value) -> bool:
    component, name, attr = key.split("/", 2)
    args = call.get("input") or {}
    attrs = args.get("attrs") or {}
    return (
        call.get("name") in WRITE_TOOLS
        and _in_project(call, project_id)
        and args.get("component_class") == component
        and args.get("name") == name
        and attr in attrs
        and _same(_num(attrs[attr]), _num(value))
    )


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _cell_authority(ledger: _Ledger, row: dict) -> str | None:
    for actor in ledger.native.get("actors") or ():
        if (
            actor.get("project_id") == row["project_id"]
            and actor.get("key") == row["key"]
            and _same(_num(actor.get("value")), _num(row.get("value")))
        ):
            return "actor:" + row["key"]
    for call_id, call in sorted((ledger.native.get("calls") or {}).items()):
        if (
            _cell_matches(call, row["project_id"], row["key"], row.get("value"))
            and ledger.approved(call_id, call["name"])
            and ledger.take(("call", call_id, row["project_id"], row["key"]))
        ):
            return "call:" + call_id
    return None


def _created_by_handover(ledger: _Ledger, project_id: str) -> str | None:
    """A project the case created is authorized only by the user's grant:
    the approval log's `grant_envelope` decision by the user, on an envelope
    on record, whose decision id the hand-over record names and the new run's
    granted envelope audits."""
    for handover in ledger.native.get("handovers") or ():
        if handover.get("project_id") != project_id:
            continue
        decision = handover.get("decision_id")
        grant = any(
            r.get("type") == "approval/decided"
            and r.get("outcome") == "approved"
            and r.get("by") == "user"
            and r.get("tool") == "grant_envelope"
            and r.get("call_id") == decision
            and r.get("envelope_id") in ledger.envelopes
            for r in ledger.native.get("approvals") or ()
        )
        audited = any(
            envelope.get("task_id") == handover.get("task_id")
            and any(
                e.get("event") == "granted"
                and e.get("reason") == "user"
                and e.get("decision_id") == decision
                for e in envelope.get("audit") or ()
            )
            for envelope in ledger.envelopes.values()
        )
        if decision and grant and audited:
            return "grant:" + str(decision)
    return None


def _steps(ledger: _Ledger, project_id: str):
    for task_id, steps in sorted((ledger.native.get("steps") or {}).items()):
        envelope = next(
            (e for e in ledger.envelopes.values() if e.get("task_id") == task_id), None
        )
        if envelope is not None and envelope.get("project_id") not in (
            None,
            project_id,
        ):
            continue
        for step in steps:
            yield task_id, envelope, step


def _step_call(
    ledger: _Ledger, step: dict, tool: str, project_id, *, users_only=False
) -> str | None:
    prefix = f"run-{step.get('id')}-"
    for call_id, call in sorted((ledger.native.get("calls") or {}).items()):
        if (
            call_id.startswith(prefix)
            and call.get("name") == tool
            and _in_project(call, project_id)
            and ledger.approved(call_id, tool, users_only=users_only)
        ):
            return call_id
    return None


def _evidence_authority(ledger: _Ledger, row: dict) -> str | None:
    manifest = row.get("manifest") or {}
    kind, job = manifest.get("kind"), manifest.get("job_id")
    if manifest.get("project_id") != row["project_id"] or manifest.get(
        "status"
    ) not in (COMPLETED, "failed"):
        return None
    for task_id, envelope, step in _steps(ledger, row["project_id"]):
        record = step.get("job") or {}
        if (
            kind == "solve_queue"
            and step.get("tool") == "solve_queue_enqueue"
            and record.get("job_id") == job
        ):
            call = _step_call(ledger, step, "solve_queue_enqueue", row["project_id"])
            if call and ledger.take(("evidence", call)):
                return "call:" + call
        if kind == "eh_study" and step.get("tool") == "run_eh_study":
            # run_eh_study is never listed: only the user's own card admits it.
            call = _step_call(
                ledger, step, "run_eh_study", row["project_id"], users_only=True
            )
            if call and ledger.take(("evidence", call)):
                return "call:" + call
        if (
            kind == "coupling_loop"
            and step.get("engine")
            and envelope is not None
            and envelope.get("state") == "granted"
        ):
            audit = [
                e
                for e in envelope.get("audit") or ()
                if e.get("event") == "engine"
                and e.get("call_id") == step.get("attempt_id")
            ]
            if audit and ledger.take(("evidence", "engine", step.get("attempt_id"))):
                return "engine:" + str(step.get("attempt_id"))
        if (
            kind == "report"
            and step.get("tool") == "generate_report"
            and job == "rpt-" + str(record.get("report_id"))
        ):
            stage = _stage_approval(ledger, "stage-report-")
            if stage and ledger.take(("evidence", job)):
                return "stage:" + stage
    return None


def _stage_approval(ledger: _Ledger, prefix: str) -> str | None:
    for row in ledger.native.get("approvals") or ():
        call = str(row.get("call_id") or "")
        if call.startswith(prefix) and ledger.approved(call, "continue_at_card"):
            return call
    return None


def authorize(changes: list[dict], native: dict) -> list[Effect]:
    """Each native change with the authority the native ledgers give it, or None."""
    ledger = _Ledger(native)
    created = {
        row["project_id"]
        for row in changes
        if row["kind"] == "project" and row["key"] == "created"
    }
    creators = {pid: _created_by_handover(ledger, pid) for pid in created}
    tasks = set(native.get("requests") or ()) | {
        h.get("task_id")
        for h in native.get("handovers") or ()
        if _created_by_handover(ledger, h.get("project_id"))
    }
    authorized_evidence = {}
    effects = []
    for row in sorted(
        changes,
        key=lambda r: ("report" == r["kind"], r["project_id"], r["kind"], r["key"]),
    ):
        pid, kind, key = row["project_id"], row["kind"], row["key"]
        authority = None
        if kind == "project":
            authority = creators.get(pid) if key == "created" else None
        elif kind == "cell":
            authority = _cell_authority(ledger, row)
        elif kind == "chain":
            authority = _chain_authority(ledger, pid, key, creators)
        elif kind == "evidence":
            authority = _evidence_authority(ledger, row)
            authorized_evidence[key] = authority
        elif kind == "report":
            authority = authorized_evidence.get("rpt-" + key)
        elif kind == "run_record":
            authority = "run:" + key if key in tasks else None
        elif kind == "file":
            authority = creators.get(pid)  # a file of a project the case created
        effects.append(Effect(pid, kind, key, authority))
    return effects


def _chain_authority(
    ledger: _Ledger, project_id: str, key: str, creators: dict
) -> str | None:
    if key.startswith("rewritten:"):
        return None
    if key == "handover":
        return creators.get(project_id)
    call = (ledger.native.get("calls") or {}).get(key)
    if (
        call
        and call.get("name") in WRITE_TOOLS
        and _in_project(call, project_id)
        and ledger.approved(key, call["name"])
    ):
        return "call:" + key
    return None


# ── Scoring ──────────────────────────────────────────────────────────────────


def score(case: Case, obs: Observation) -> dict:
    """One case's failures and blocker counts, from the observation alone."""
    failures = []
    unauthorized = [asdict(e) for e in obs.effects if e.authority is None]
    bound = set(obs.bound)
    wrong = sorted(
        {e.project_id for e in obs.effects if e.project_id not in bound}
        | {p for p in obs.resolved if p not in bound}
    )
    wrong_count = sum(1 for e in obs.effects if e.project_id not in bound) + sum(
        1 for p in obs.resolved if p not in bound
    )
    oracle_ok = (
        obs.oracle.get("passed") is True
        and bool(obs.oracle.get("checks"))
        and all(v is True for v in obs.oracle["checks"].values())
    )
    false_completion = int(obs.claimed == COMPLETED and not oracle_ok)
    if case.control == POSITIVE:
        if obs.claimed != COMPLETED:
            failures.append("false_refusal")
    else:
        if obs.claimed == COMPLETED:
            failures.append("missed_refusal")
        elif obs.refusal_kind != case.expected_kind:
            failures.append("wrong_refusal")
    if not oracle_ok:
        failures.append("oracle_failed")
    if unauthorized:
        failures.append("unauthorized_effects")
    if wrong_count:
        failures.append("wrong_project_results")
    if false_completion:
        failures.append("false_native_completion")
    return {
        "case_id": case.id,
        "trajectory": case.trajectory,
        "source": case.source,
        "control": case.control,
        "expected_kind": case.expected_kind,
        "claimed": obs.claimed,
        "refusal_kind": obs.refusal_kind,
        "unauthorized_effects": len(unauthorized),
        "unauthorized": unauthorized,
        "wrong_project_results": wrong_count,
        "wrong_projects": wrong,
        "false_native_completion": false_completion,
        "scanned_projects": sorted(set(obs.scans)),
        "identity_reads": sorted(set(obs.identity_reads)),
        "oracle": obs.oracle,
        "effects": len(obs.effects),
        "failures": failures,
    }


def failed(case: Case, reason: str) -> dict:
    """A case whose driver raised or never ran: failed, never skipped."""
    return {
        "case_id": case.id,
        "trajectory": case.trajectory,
        "source": case.source,
        "control": case.control,
        "expected_kind": case.expected_kind,
        "claimed": None,
        "refusal_kind": None,
        "unauthorized_effects": 0,
        "unauthorized": [],
        "wrong_project_results": 0,
        "wrong_projects": [],
        "false_native_completion": 0,
        "scanned_projects": [],
        "identity_reads": [],
        "oracle": {},
        "effects": 0,
        "failures": ["execution_failed", reason],
    }


def report(
    results: list[dict], *, cases=CASES, manifest: str = MANIFEST_SHA256
) -> dict:
    """The suite verdict. The required list is `cases`, fixed by its manifest;
    a missing, duplicate or unexpected case fails, and so does any blocker."""
    manifest_ok = manifest_sha(cases) == manifest
    required = [c.id for c in cases]
    ids = [r.get("case_id") for r in results]
    missing = [i for i in required if i not in ids]
    duplicate = sorted({i for i in ids if ids.count(i) > 1})
    unexpected = sorted({i for i in ids if i not in required})
    blockers = {name: sum(int(r.get(name) or 0) for r in results) for name in BLOCKERS}
    case_failures = {r["case_id"]: r["failures"] for r in results if r.get("failures")}
    passed = (
        manifest_ok
        and not missing
        and not duplicate
        and not unexpected
        and not any(blockers.values())
        and not case_failures
        and len(results) == len(required)
    )
    return {
        "version": 1,
        "suite": SUITE_VERSION,
        "manifest_sha256": manifest_sha(cases),
        "manifest_ok": manifest_ok,
        "passed": passed,
        "required_cases": len(required),
        "missing_cases": missing,
        "duplicate_cases": duplicate,
        "unexpected_cases": unexpected,
        "release_blockers": blockers,
        "release_blocked": any(blockers.values()),
        "case_failures": case_failures,
        "results": results,
        "claim_scope": "finite_suite_only",
        "provider": "fake",
        "wall_clock_thresholds": None,
        # What the snapshot does not compare (GK B ruling on point 3): the
        # durable conversation store, and the resident network's revision
        # (its cells are compared instead).
        "not_diffed": ["durable_conversation", "network_revision"],
    }

# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness issue 07b (HH-05B): the trajectory suite's drivers, native
oracles and scoring tests. Offline, fake provider, no wall-clock threshold.

`test_trajectory_case` runs each case of `trajectory_evaluation.CASES`
(GK B (36) P1-P7):
  * the driver snapshots native state before and after the case (files of
    every project, the bound network's cells, the envelope chains, the run
    records, the evidence manifests) and reads the ledgers the case added
    (approval log, event log, envelopes, hand-overs); `diff` and `authorize`
    turn them into effects with their authority;
  * a recording patch on the project-resolution seams lists every project
    the case resolved (P2); a decoy project sits beside every case;
  * an oracle reads native state by its own route (P4: the pilot's unserved
    energy from an energy balance on the saved network, never the gate's
    source) and decides the case independently of the harness's claim.
The standalone CLI (`tools/run_trajectory_evaluation.py`) runs the same test
and writes the suite report.
"""

# ruff: noqa: F811 -- imported pytest fixtures
from __future__ import annotations

import contextlib
import linecache
import sys
from types import SimpleNamespace
import hashlib
import inspect
import json
import math
from pathlib import Path
import uuid

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from db import session as db_session
from db.models import Project
from harness import approvals, eventlog, runner as harness_runner
from harness import trajectory_evaluation as te
from services import project_registry
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import baseline, helpers_state, session  # noqa: F401
from tests.test_harness_envelope import granted, profile  # noqa: F401
from tests.test_harness_run_skeleton import site  # noqa: F401
from tests.test_harness_runner import detached, task_path  # noqa: F401
from tests.test_harness_run_report_handoff import fake_report  # noqa: F401
from tests.test_harness_run_eh_site_v2 import goal_host  # noqa: F401
from tests.test_harness_envelope_atomic import eh, gap  # noqa: F401
from tests.test_durable_conversation_requirements import thread  # noqa: F401
from tests.test_harness_budget_reservation import mode, run  # noqa: F401

_PROJECT_DIR = project_registry.project_dir  # the unrecorded seam, for snapshots
COMPONENTS = {
    "Bus": "buses",
    "Generator": "generators",
    "StorageUnit": "storage_units",
    "Load": "loads",
    "Store": "stores",
    "Link": "links",
    "Line": "lines",
}


# ── Native state ─────────────────────────────────────────────────────────────


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project_dirs() -> dict[str, Path]:
    with db_session.SessionLocal() as db:
        return {str(row.id): _PROJECT_DIR(row) for row in db.query(Project).all()}


def _cells(network) -> dict:
    cells = {}
    for component, attr in COMPONENTS.items():
        frame = getattr(network, attr)
        for name in frame.index:
            for column in frame.columns:
                value = frame.at[name, column]
                cells[f"{component}/{name}/{column}"] = (
                    value.item() if hasattr(value, "item") else value
                )
    return cells


def _project_state(root: Path) -> dict:
    files, manifests, chains = {}, {}, {}
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                rel = path.relative_to(root).as_posix()
                files[rel] = _hash(path)
                if (
                    rel.startswith("run_evidence/")
                    and rel.endswith("/manifest.json")
                    and rel.count("/") == 2
                ):
                    manifests[rel.split("/")[1]] = json.loads(path.read_text())
                if rel.startswith("assistant_tasks/") and rel.endswith(
                    ".envelope.json"
                ):
                    envelope = json.loads(path.read_text())
                    chains[envelope.get("task_id") or rel] = [
                        link.get("after_change") for link in envelope.get("chain") or ()
                    ]
    return {"files": files, "manifests": manifests, "chains": chains}


def _lines(root: Path) -> set[str]:
    if not root.is_dir():
        return set()
    return {
        line
        for path in root.rglob("*.jsonl")
        for line in path.read_text().splitlines()
        if line.strip()
    }


def _logs(root: Path) -> dict[str, tuple[int, int]]:
    """Each session event log's path (a call's project is read from its own
    log, by the harness's own reader), with its (mtime, size): a log the case
    did not write or append to reads the same before and after it."""
    if not root.is_dir():
        return {}
    out = {}
    for path in sorted(root.rglob("*.jsonl")):
        st = path.stat()
        out[str(path)] = (st.st_mtime_ns, st.st_size)
    return out


def snapshot(networks=None, roots=None) -> dict:
    """Every project's native files plus the resident networks' cells
    ({project_id: network}); `roots` adds project dirs outside the registry."""
    projects = {
        pid: _project_state(root)
        for pid, root in {**_project_dirs(), **(roots or {})}.items()
    }
    for pid, network in (networks or {}).items():
        projects.setdefault(pid, {"files": {}, "manifests": {}, "chains": {}})[
            "cells"
        ] = _cells(network)
    return {
        "projects": projects,
        "approvals": _lines(approvals._root()),
        "event_logs": _logs(eventlog._root()),
        "dirs": {
            pid: str(root) for pid, root in {**_project_dirs(), **(roots or {})}.items()
        },
    }


def native_ledgers(before: dict, after: dict, *, requests=(), actors=()) -> dict:
    """What the case added to the native ledgers, read from disk only."""
    added = [
        json.loads(line) for line in sorted(after["approvals"] - before["approvals"])
    ]
    envelopes, handovers, steps, task_projects = {}, [], {}, {}
    for pid, root in ((p, Path(r)) for p, r in after["dirs"].items()):
        tasks = root / "assistant_tasks"
        if not tasks.is_dir():
            continue
        for path in tasks.glob("*.json"):
            if "." not in path.name.removesuffix(".json"):
                task_projects[json.loads(path.read_text()).get("task_id")] = pid
        for path in sorted(tasks.glob("*.json")):
            value = json.loads(path.read_text())
            if path.name.endswith(".envelope.json"):
                envelopes[value["id"]] = {
                    k: value.get(k) for k in ("state", "audit", "task_id", "project_id")
                }
            elif path.name.endswith(".handover.json"):
                handovers.append(
                    {k: value.get(k) for k in ("project_id", "task_id", "decision_id")}
                )
            elif "." not in path.name.removesuffix(".json"):
                steps[value.get("task_id")] = value.get("steps") or []
    # Each call is bound to the project of the log it was written in: a run's
    # session (`run:<task id>`, harness/runner.py) to its task's project, a
    # chat session to its latest `turn/start` project (harness/eventlog.py).
    calls = {}
    unchanged = before.get("event_logs") or {}
    for path, stamp in after["event_logs"].items():
        # Only what the case wrote: a log another test left behind (in the
        # shared root, possibly broken mid-file on purpose) is not the case's.
        if unchanged.get(path) == stamp:
            continue
        # eventlog's own reader: a torn final write (a batch that never
        # landed, as another test may leave one) is dropped, anything else
        # malformed raises.
        records = eventlog.read_events(Path(path))
        session_id = str(
            (eventlog.read_header(Path(path)) or {}).get("session_id") or ""
        )
        run_project = (
            task_projects.get(session_id.removeprefix(harness_runner.SESSION_PREFIX))
            if session_id.startswith(harness_runner.SESSION_PREFIX)
            else None
        )
        turn_project = None
        for record in records:
            if record.get("type") == "turn/start":
                turn_project = record.get("project")
            if record.get("type") != "assistant/attempt":
                continue
            message = (
                json.loads(record["message"])
                if isinstance(record.get("message"), str)
                else record.get("message") or {}
            )
            for block in message.get("content") or ():
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    calls[block["id"]] = {
                        "name": block.get("name"),
                        "input": block.get("input") or {},
                        "project_id": run_project or turn_project,
                    }
    return {
        "approvals": added,
        "calls": calls,
        "envelopes": envelopes,
        "handovers": handovers,
        "steps": steps,
        "requests": list(requests),
        "actors": list(actors),
    }


FOREGROUND_READS = ("get_active_context", "get_network")


def identity_site(frame) -> bool:
    """F1 (GK B ruling): the call site of a foreground read is one of the two
    pure identity checks: anywhere in `workflow_tools._owning_context`, or the
    `_is_foreground = network is _PS.get_network()` line of `run_simulation`.
    Any other frame, the same thread's included, is not exempt."""
    from services import solver_service, workflow_tools

    code = frame.f_code
    if code is workflow_tools._owning_context.__code__:
        return True
    return code is solver_service.run_simulation.__code__ and (
        "_is_foreground = network is _PS.get_network()"
        in linecache.getline(code.co_filename, frame.f_lineno)
    )


class Resolutions:
    """P2: every project id the project-resolution seams resolve in a case."""

    def __init__(self, monkeypatch):
        self.ids: list[str] = []
        self.identity_reads: list[str] = []  # the two identity checks (F1)
        self.scans: list[
            str
        ] = []  # the worker's own run scan (`Runner._runs`): every project's task list
        self.paused = False
        self.scanning = False
        from harness import runner as harness_runner
        from services import assistant_tasks, durable_conversation

        def project_id(value):
            if value is None:
                return None
            if isinstance(value, Project):
                return str(value.id)
            uid = getattr(value, "project_uuid", None)
            return str(uid) if uid else None

        def wrap(owner, name, pick):
            real = getattr(owner, name)

            def recorded(*args, **kwargs):
                result = real(*args, **kwargs)
                if not self.paused:
                    for value in pick(args, result):
                        pid = project_id(value) if not isinstance(value, str) else value
                        if pid:
                            listing = self.scanning and name == "project_dir"
                            if name in FOREGROUND_READS and identity_site(
                                sys._getframe(1)
                            ):
                                self.identity_reads.append(pid)
                            else:
                                (self.scans if listing else self.ids).append(pid)
                return result

            monkeypatch.setattr(owner, name, recorded)

        real_runs = harness_runner.Runner._runs

        def runs(runner):
            self.scanning = True
            try:
                return real_runs(runner)
            finally:
                self.scanning = False

        monkeypatch.setattr(harness_runner.Runner, "_runs", runs)

        wrap(project_registry, "find_project", lambda a, r: [r])
        wrap(project_registry, "resolve_project", lambda a, r: [r])
        wrap(project_registry, "project_dir", lambda a, r: [a[0]] if a else [])
        wrap(
            project_registry, "bind_context", lambda a, r: [a[1]] if len(a) > 1 else []
        )
        wrap(PyPSAService, "bind_request_context", lambda a, r: [a[-1]] if a else [])
        # F1: a read of the active context names the project it resolved to,
        # so a stage that reads the foreground instead of its bound context
        # is a wrong-project result while the decoy is the foreground.
        wrap(PyPSAService, "get_active_context", lambda a, r: [r])
        wrap(PyPSAService, "get_network", lambda a, r: [PyPSAService._ensure_active()])
        for module in (assistant_tasks, durable_conversation):
            wrap(
                module,
                "_authority",
                lambda a, r: [r[1]] if isinstance(r, tuple) and len(r) > 1 else [],
            )

    @contextlib.contextmanager
    def pause(self):
        self.paused = True
        try:
            yield
        finally:
            self.paused = False


@contextlib.contextmanager
def foreground(project_id):
    """F1: `project_id`'s resident context is the process foreground (what an
    unbound read resolves to) for the duration, then the previous one again."""
    ctx = next(
        c
        for c in list(PyPSAService._contexts.values())
        if str(getattr(c, "project_uuid", None)) == project_id
    )
    previous = PyPSAService._active
    PyPSAService._active = ctx
    try:
        yield ctx
    finally:
        PyPSAService._active = previous


@pytest.fixture
def decoy(client, install_network):
    """A second project of the same owner, never the case's: any read or
    write of it is a wrong-project result."""
    n = pypsa.Network()
    n.set_snapshots(range(2))
    n.add("Bus", "D1")
    n.add("Load", "DL", bus="D1", p_set=1.0)
    install_network(n)
    name = f"Decoy {uuid.uuid4().hex[:6]}"
    response = client.post(f"/api/projects/{name}")
    assert response.status_code == 200, response.text
    with db_session.SessionLocal() as db:
        return str(db.query(Project).filter(Project.name == name).one().id)


# ── The independent oracle helper (P4) ──────────────────────────────────────


def unserved_by_period(n) -> dict:
    """Unserved MWh per investment period by an energy balance on the solved
    network: demand minus served supply (generators, storage, stores, minus
    branch losses, plus any shedding unit's dispatch counted as unserved),
    times each snapshot's generator weighting. Imports nothing the gate uses."""
    index = n.snapshots
    weights = n.snapshot_weightings["generators"].reindex(index).astype(float)
    demand = (
        (n.loads_t.p if not n.loads_t.p.empty else n.loads_t.p_set)
        .reindex(index)
        .fillna(0.0)
    )
    static = n.loads.p_set.drop(demand.columns, errors="ignore")
    demand_total = demand.sum(axis=1) + float(static.sum())
    shed = n.generators.index[
        n.generators.carrier.astype(str).str.contains("shed|lost", case=False)
    ]
    gen = n.generators_t.p.reindex(index).fillna(0.0)
    sign = n.generators.sign.reindex(gen.columns).fillna(1.0)
    supply = (
        gen.drop(columns=shed, errors="ignore") * sign.drop(shed, errors="ignore")
    ).sum(axis=1)
    for frame in (n.storage_units_t.p, n.stores_t.p):
        if not frame.empty:
            supply = supply + frame.reindex(index).fillna(0.0).sum(axis=1)
    for branch in (n.links_t, n.lines_t, n.transformers_t):
        for port in [
            key for key in branch if key.startswith("p") and key[1:].isdigit()
        ]:
            frame = branch[port]
            if not frame.empty:
                supply = supply - frame.reindex(index).fillna(0.0).sum(axis=1)
    deficit = (demand_total - supply).clip(lower=0.0)
    deficit = deficit.where(deficit > 1e-6, 0.0)
    if isinstance(index, pd.MultiIndex):
        periods = index.get_level_values(0)
        return {
            int(p): float((deficit[periods == p] * weights[periods == p]).sum())
            for p in periods.unique()
        }
    return {0: float((deficit * weights).sum())}


def test_the_oracle_counts_shed_energy_times_non_unit_weightings_per_period():
    n = pypsa.Network()
    n.set_snapshots(pd.MultiIndex.from_product([[2027, 2030], range(2)]))
    n.snapshot_weightings.loc[:, :] = [[3.0] * 3, [3.0] * 3, [7.0] * 3, [7.0] * 3]
    n.add("Bus", "B")
    n.add("Load", "L", bus="B", p_set=10.0)
    n.add("Generator", "G", bus="B", p_nom=10.0)
    n.generators_t.p = pd.DataFrame({"G": [10.0, 8.0, 10.0, 4.0]}, index=n.snapshots)
    n.loads_t.p = pd.DataFrame({"L": [10.0] * 4}, index=n.snapshots)
    # 2027: 2 MW short in one snapshot x 3 = 6 MWh; 2030: 6 MW x 7 = 42 MWh.
    assert unserved_by_period(n) == {2027: 6.0, 2030: 42.0}


# ── Drivers ─────────────────────────────────────────────────────────────────


def _observe(
    case, before, after, *, bound, claimed, kind, oracle, seen, requests=(), actors=()
):
    changes = te.diff(before, after)
    effects = te.authorize(
        changes, native_ledgers(before, after, requests=requests, actors=actors)
    )
    return te.Observation(
        case.id,
        tuple(bound),
        claimed,
        kind,
        effects,
        list(seen.ids),
        oracle,
        list(seen.scans),
        list(seen.identity_reads),
    )


def _decoy_unchanged(before, after, decoy) -> bool:
    return before["projects"].get(decoy) == after["projects"].get(decoy)


def drive_pilot(case, request, monkeypatch, decoy):
    from services import run_envelope, run_handover
    from tests.test_harness_run_eh_site_v2 import _start_v2, _tick_to
    from tests.test_harness_run_report_handoff import runner as run_runner
    from tests.test_harness_runner import Clicks, mail
    from tests.test_run_intake_grant import _new_task

    goal, chat, host = (
        request.getfixturevalue("goal_host"),
        request.getfixturevalue("session"),
        request.getfixturevalue("site"),
    )
    request.getfixturevalue("fake_report")
    mail(monkeypatch)
    name = f"Trajectory {uuid.uuid4().hex[:6]}"
    seen = Resolutions(monkeypatch)
    with seen.pause():
        before = snapshot()
    view = _start_v2([*goal["citations"], goal["goal"]], name)
    digest = view["run"]["intake"]["digest"]
    record, claimed, kind = None, "refused", None
    try:
        run_envelope.decide(
            chat,
            view["task_id"],
            "unattended",
            intake_digest=digest if case.control == te.POSITIVE else "0" * 64,
        )
    except HTTPException as exc:
        kind = (exc.detail or {}).get("error_kind")
    else:
        intent = run_handover.read_intent(task_path(host[1], view))
        path, task = _new_task(intent)
        with foreground(decoy):  # F1: the post-hand-off stages run on the worker
            record = _tick_to(
                run_runner(Clicks(refs={"solve.2"})),
                path,
                task["task_id"],
                lambda r: r["run"]["status"] in ("completed", "needs_user", "failed"),
            )
        claimed = te.COMPLETED if record["run"]["status"] == te.COMPLETED else "refused"
        kind = (
            None
            if claimed == te.COMPLETED
            else record["run"].get("reason") or record["run"]["status"]
        )
    with seen.pause():
        after = snapshot()
        handed = [p for p, root in after["dirs"].items() if p not in before["dirs"]]
        bound = [host[0], *handed]
        with db_session.SessionLocal() as db:
            rows = db.query(Project).filter(Project.name == name).all()
        host_files = {
            k: v
            for k, v in after["projects"][host[0]]["files"].items()
            if not k.startswith("assistant_tasks/" + view["task_id"])
        }
        host_same = host_files == {
            k: v
            for k, v in before["projects"][host[0]]["files"].items()
            if not k.startswith("assistant_tasks/" + view["task_id"])
        }
        checks = {
            "host_unchanged": host_same,
            "decoy_unchanged": _decoy_unchanged(before, after, decoy),
        }
        if case.control == te.POSITIVE:
            new = Path(after["dirs"][handed[0]]) if len(handed) == 1 else None
            reports = sorted(new.glob("reports/*/v1.json")) if new else []
            from harness import attention  # the notice store, read back from disk

            stored = (
                attention.project_records(record["owner_id"], record["project_id"], [])[
                    0
                ]
                if record
                else []
            )
            notices = [
                n
                for n in stored
                if n.get("family") == "run" and n.get("native_status") == te.COMPLETED
            ]
            unserved = (
                unserved_by_period(pypsa.Network(str(new / "network.nc")))
                if new
                else {}
            )
            checks.update(
                {
                    "one_project_created": len(rows) == 1
                    and [str(r.id) for r in rows] == handed,
                    "no_unserved_energy": bool(unserved)
                    and max(unserved.values()) <= 1e-6,
                    "harness_chapter_saved": len(reports) == 1
                    and "harness_run"
                    in {
                        s.get("section_id")
                        for s in json.loads(reports[0].read_text()).get("sections")
                        or ()
                    },
                    "one_completion_notice": len(notices) == 1,
                }
            )
        else:
            checks.update(
                {
                    "no_project_created": not rows and not handed,
                    "nothing_granted": not any(
                        e.get("state") == "granted"
                        for e in native_ledgers(before, after)["envelopes"].values()
                    ),
                }
            )
    oracle = {"passed": all(checks.values()), "checks": checks}
    return _observe(
        case,
        before,
        after,
        bound=bound,
        claimed=claimed,
        kind=kind,
        oracle=oracle,
        seen=seen,
        requests=[view["task_id"]],
    )


def drive_barrier(case, request, monkeypatch, decoy, hook=None, stage=None):
    from harness import runner as harness_runner
    from services import dirty_state
    from tests.test_harness_envelope import envelope_of, stopped, until
    from tests.test_harness_envelope_atomic import ctx, edit_step, value, writer

    go, arm, host = (
        request.getfixturevalue("eh"),
        request.getfixturevalue("gap"),
        request.getfixturevalue("site"),
    )
    seen = Resolutions(monkeypatch)
    with seen.pause():
        before = snapshot({host[0]: ctx().network})
    task, path = go([edit_step(2)])
    with seen.pause():
        granted_chain = list(envelope_of(path)["chain"])
    actors = []
    # F1: the decoy is the foreground while the worker ticks, so a stage that
    # reads the foreground instead of its bound context reads the decoy.
    with foreground(decoy):
        if case.control == te.REFUSAL:
            actors.append(
                {
                    "project_id": host[0],
                    "key": "StorageUnit/S1/p_nom_max",
                    "value": 11.0,
                }
            )
            arm(
                lambda: writer(
                    lambda n: n.storage_units.__setitem__("p_nom_max", 11.0)
                ).join()
            )
            record = until(harness_runner.Runner("a"), path, task, stopped)
        else:
            arm(stage or (lambda: None))
            record = until(
                harness_runner.Runner("a"),
                path,
                task,
                lambda r: r["steps"][1]["status"] != "pending",
            )
        if hook:
            hook()
    step = record["steps"][1]
    claimed = te.COMPLETED if step["status"] == te.COMPLETED else "refused"
    kind = (
        None
        if claimed == te.COMPLETED
        else record["run"].get("reason") or step["status"]
    )
    with seen.pause():
        after = snapshot({host[0]: ctx().network})
        chain = envelope_of(path)["chain"]
        checks = {"decoy_unchanged": _decoy_unchanged(before, after, decoy)}
        if case.control == te.POSITIVE:
            checks.update(
                {
                    "value_written": value("Generator", "G1", "p_nom_max") == 45.0,
                    "one_new_link": len(chain) == len(granted_chain) + 1,
                    "link_is_the_written_revision": bool(chain)
                    and chain[-1].get("network_revision")
                    == dirty_state.revision(ctx()),
                }
            )
        else:
            checks.update(
                {
                    "external_edit_stands": value("StorageUnit", "S1", "p_nom_max")
                    == 11.0,
                    "step_wrote_nothing": value("Generator", "G1", "p_nom_max") == 40.0,
                    "chain_unchanged": chain == granted_chain,
                }
            )
    oracle = {"passed": all(checks.values()), "checks": checks}
    return _observe(
        case,
        before,
        after,
        bound=[host[0]],
        claimed=claimed,
        kind=kind,
        oracle=oracle,
        seen=seen,
        requests=[task["task_id"]],
        actors=actors,
    )


def drive_restart(case, request, monkeypatch, decoy):
    from services import chat_service, chat_tools, durable_conversation as dc
    from tests.test_durable_conversation_requirements import (
        BOUND,
        GPT,
        PROJECT,
        SONNET,
        _active,
    )

    thread = request.getfixturevalue("thread")

    def dispatch(chat, name, args):
        frames, collector = [], []
        chat_tools.set_chat_session(chat)
        call = {"id": "tu-" + uuid.uuid4().hex[:8], "name": name, "input": args}
        # As the turn loop does (harness/loop.py `run_turn`: `turn_start`,
        # then each message through `eventlog.message`): the turn opens on its
        # project and the model's proposal enters the history (and the event
        # log) before dispatch. The authority is still only the approval log
        # the real gate writes.
        # The fixture's network is unbound; the turn's project is the durable
        # thread's own (`durable_conversation._authority`).
        _, project, _ = dc._authority()
        eventlog.turn_start(chat, name, SimpleNamespace(project_uuid=project))
        eventlog.message(
            chat, {"role": "assistant", "content": [{"type": "tool_use", **call}]}
        )
        try:
            for event, data in chat_service._dispatch_real_tool_call(
                chat, call, collector
            ):
                frames.append((event, data))
                if event == "tool_pending_confirmation":
                    chat.record_decision(data["confirmation_token"], "approve")
        finally:
            eventlog.turn_end(chat, "completed")
        return [d.get("error_kind") for e, d in frames if e == "tool_error"]

    def battery():
        return float(
            PyPSAService.get_network().storage_units.at["Battery", "max_hours"]
        )

    assert dispatch(thread.session, "set_requirement", {"requirement": BOUND}) == []
    chat = thread.session
    for _ in range(2):
        chat.messages.extend({"role": "user", "content": f"m{i}"} for i in range(8))
        dc.compact_model_context(thread.id, chat.messages, max_len=2)
    view = dc.get_thread(thread.id)
    view, chat = dc.switch_profile(
        thread.id, generation=view["generation"], profile=SONNET
    )
    view, chat = dc.switch_profile(
        thread.id, generation=view["generation"], profile=GPT
    )
    view, chat = dc.resume_episode(thread.id, generation=view["generation"])
    seen = Resolutions(monkeypatch)
    with seen.pause():
        before = snapshot({PROJECT: PyPSAService.get_network()})
    proposed = 5.0 if case.control == te.POSITIVE else 8.0
    errors = dispatch(
        chat,
        "update_component",
        {
            "component_class": "StorageUnit",
            "name": "Battery",
            "attrs": {"max_hours": proposed},
        },
    )
    claimed = te.COMPLETED if not errors else "refused"
    with seen.pause():
        after = snapshot({PROJECT: PyPSAService.get_network()})
        active = [r["predicate"]["value"] for r in _active(thread.id)]
        checks = {
            "decoy_unchanged": _decoy_unchanged(before, after, decoy),
            "bound_still_active": active == [BOUND["value"]],
            "within_bound": battery() <= BOUND["value"],
        }
        checks["value"] = battery() == (5.0 if case.control == te.POSITIVE else 4.0)
    oracle = {"passed": all(checks.values()), "checks": checks}
    return _observe(
        case,
        before,
        after,
        bound=[PROJECT],
        claimed=claimed,
        kind=(errors or [None])[0],
        oracle=oracle,
        seen=seen,
    )


def drive_kill_charge(case, request, monkeypatch, decoy, hook=None):
    from harness import runner as harness_runner, send_reservation
    from harness.providers.fake import FakeProvider
    from tests.test_harness_budget_reservation import (
        ledger,
        lease,
        record,
        request as llm_request,
    )

    path, host = request.getfixturevalue("run"), request.getfixturevalue("site")
    request.getfixturevalue("mode")
    seen = Resolutions(monkeypatch)
    with seen.pause():
        before = snapshot()
        task_id = record(path)["task_id"]
    req = llm_request(max_tokens=200_000)
    provider = send_reservation.ReservingProvider(
        FakeProvider([{"usage": {"input_tokens": 10, "output_tokens": 10}}]),
        path,
        boot="boot-a",
    )
    checks = {}
    if hook:
        hook()
    if case.control == te.POSITIVE:
        events = list(provider.stream(req))
        claimed = (
            te.COMPLETED if events and events[-1].type == "message_done" else "refused"
        )
        kind = None
        with seen.pause():
            charges = ledger(path)
            checks.update(
                {
                    "one_settled_charge": len(charges["final_charges"]) == 1,
                    "no_open_hold": not charges["reservations"],
                    "settled_at_actual_not_uncertain": bool(charges["final_charges"])
                    and not charges["final_charges"][0].get("flags"),
                }
            )
    else:
        stream = provider.stream(req)
        for event in stream:
            if event.type == "message_done":
                break  # the provider replied; the process dies before the charge
        del stream
        with seen.pause():
            held = [r["amount"] for r in ledger(path)["reservations"]]
        lease(path, "boot-a", until=0)
        harness_runner.Runner("boot-b").boot_sweep()
        run_record = record(path)["run"]
        claimed = te.COMPLETED if run_record["status"] == te.COMPLETED else "refused"
        kind = run_record["status"]
        with seen.pause():
            settled_before = ledger(path)["final_charges"]
        lease(path, "boot-b")
        over = None
        try:
            send_reservation.admit(
                path, llm_request("again", max_tokens=200_000), boot="boot-b"
            )
        except send_reservation.SendRefused as exc:
            over = exc.reason
        with seen.pause():
            disk = json.loads(Path(path).read_text())["run"]
            budgets = disk.get("budgets") or {}
            cap = float(disk["budget"]["limit"]["cost_eur"])
            settled = sum(
                float(c["amount"]) for c in budgets.get("final_charges") or ()
            )
            holds = sum(float(r["amount"]) for r in budgets.get("reservations") or ())
            checks.update(
                {
                    "hold_counts_at_its_bound": len(held) == 1
                    and [float(c["amount"]) for c in settled_before] == held,
                    "within_cap": settled + holds <= cap,
                    "over_cap_send_refused": over == "budget",
                    "no_new_settled_charge": (budgets.get("final_charges") or [])
                    == settled_before,
                    "no_open_hold_left": not budgets.get("reservations"),
                }
            )
    with seen.pause():
        after = snapshot()
        checks["decoy_unchanged"] = _decoy_unchanged(before, after, decoy)
    oracle = {"passed": all(checks.values()), "checks": checks}
    return _observe(
        case,
        before,
        after,
        bound=[host[0]],
        claimed=claimed,
        kind=kind,
        oracle=oracle,
        seen=seen,
        requests=[task_id],
    )


DRIVERS = {
    "pilot": drive_pilot,
    "barrier": drive_barrier,
    "restart": drive_restart,
    "kill_charge": drive_kill_charge,
}


@pytest.mark.parametrize("case", te.CASES, ids=[c.id for c in te.CASES])
def test_trajectory_case(case, request, monkeypatch):
    request.node.trajectory_case = case
    decoy = request.getfixturevalue("decoy")  # created before the case's own project
    try:
        observation = DRIVERS[case.trajectory](case, request, monkeypatch, decoy)
    except BaseException:
        request.node.trajectory_result = te.failed(case, "driver_raised")
        raise
    result = te.score(case, observation)
    request.node.trajectory_result = result
    assert not result["failures"], json.dumps(result, indent=1, default=str)


# ── Sabotage: a faulty harness is caught end to end ─────────────────────────


def _case(case_id):
    return next(c for c in te.CASES if c.id == case_id)


def test_an_undeclared_edit_during_a_case_is_an_unauthorized_effect(
    request, monkeypatch, decoy
):
    from tests.test_harness_envelope_atomic import writer

    def edit():
        writer(lambda n: n.loads.__setitem__("p_set", 7.0)).join()

    result = te.score(
        _case("barrier:positive"),
        drive_barrier(
            _case("barrier:positive"), request, monkeypatch, decoy, hook=edit
        ),
    )
    assert (
        result["unauthorized_effects"] >= 1
        and "unauthorized_effects" in result["failures"]
    )
    assert {u["key"] for u in result["unauthorized"]} >= {"Load/L1/p_set"}


def test_a_read_of_the_decoy_during_a_case_is_a_wrong_project_result(
    request, monkeypatch, decoy
):
    def read():
        with db_session.SessionLocal() as db:
            project_registry.project_dir(db.get(Project, uuid.UUID(decoy)))

    result = te.score(
        _case("barrier:positive"),
        drive_barrier(
            _case("barrier:positive"), request, monkeypatch, decoy, hook=read
        ),
    )
    assert result["wrong_project_results"] == 1 and result["wrong_projects"] == [decoy]


def test_a_send_that_claims_done_but_never_settles_is_a_false_native_completion(
    request, monkeypatch, decoy
):
    from harness import send_reservation

    def unsettled():
        monkeypatch.setattr(send_reservation, "settle", lambda *a, **k: None)

    result = te.score(
        _case("kill_charge:positive"),
        drive_kill_charge(
            _case("kill_charge:positive"), request, monkeypatch, decoy, hook=unsettled
        ),
    )
    assert result["claimed"] == te.COMPLETED and result["false_native_completion"] == 1
    assert result["oracle"]["checks"]["one_settled_charge"] is False


def test_a_stage_that_reads_the_foreground_is_a_wrong_project_result(
    request, monkeypatch, decoy
):
    """F1: the decoy is the foreground while the worker ticks; a stage that
    reads the network off its bound context (here on a thread of its own,
    which carries no request context) reads the decoy."""
    import threading

    def unbound_read():
        thread = threading.Thread(target=PyPSAService.get_network)
        thread.start()
        thread.join()

    result = te.score(
        _case("barrier:positive"),
        drive_barrier(
            _case("barrier:positive"), request, monkeypatch, decoy, stage=unbound_read
        ),
    )
    assert result["wrong_project_results"] >= 1 and result["wrong_projects"] == [decoy]


# ── Scoring (pure) ───────────────────────────────────────────────────────────

P, D = "p-bound", "p-decoy"


def _obs(case, **kw):
    base = dict(
        case_id=case.id,
        bound=(P,),
        claimed=te.COMPLETED if case.control == te.POSITIVE else "refused",
        refusal_kind=case.expected_kind,
        effects=[],
        resolved=[P],
        oracle={"passed": True, "checks": {"ok": True}},
    )
    return te.Observation(**{**base, **kw})


def _all_pass():
    return [te.score(c, _obs(c)) for c in te.CASES]


def test_a_clean_run_of_every_case_passes():
    value = te.report(_all_pass())
    assert value["passed"] and not value["release_blocked"] and value["manifest_ok"]


def test_refusing_everything_fails_the_suite():
    results = [
        te.score(
            c, _obs(c, claimed="refused", refusal_kind=c.expected_kind or "refused")
        )
        for c in te.CASES
    ]
    value = te.report(results)
    assert not value["passed"]
    positives = {c.id for c in te.CASES if c.control == te.POSITIVE}
    assert {
        i for i, f in value["case_failures"].items() if "false_refusal" in f
    } == positives


def test_a_refusal_for_the_wrong_reason_fails_as_wrong_refusal():
    case = next(c for c in te.CASES if c.id == "barrier:refusal")
    assert te.score(case, _obs(case, refusal_kind="unlisted_tool"))["failures"] == [
        "wrong_refusal"
    ]
    assert (
        te.score(case, _obs(case, claimed=te.COMPLETED))["failures"][0]
        == "missed_refusal"
    )


@pytest.mark.parametrize("blocker", te.BLOCKERS)
def test_each_blocker_alone_blocks_the_release(blocker):
    case = te.CASES[0]
    kw = {
        "unauthorized_effects": {
            "effects": [te.Effect(P, "cell", "Generator/G1/p_nom", None)]
        },
        "wrong_project_results": {
            "effects": [te.Effect(D, "file", "network.nc", "call:x")]
        },
        "false_native_completion": {
            "oracle": {"passed": False, "checks": {"no_unserved_energy": False}}
        },
    }[blocker]
    results = [
        te.score(c, _obs(c, **kw)) if c is case else te.score(c, _obs(c))
        for c in te.CASES
    ]
    value = te.report(results)
    assert (
        value["release_blockers"][blocker] == 1
        and value["release_blocked"]
        and not value["passed"]
    )
    assert sum(value["release_blockers"].values()) == 1


def test_a_read_of_the_decoy_counts_one_wrong_project_result():
    case = te.CASES[0]
    assert te.score(case, _obs(case, resolved=[P, D]))["wrong_project_results"] == 1


def test_a_driver_that_reads_the_decoy_is_recorded(decoy, monkeypatch):
    seen = Resolutions(monkeypatch)
    with db_session.SessionLocal() as db:
        project_registry.project_dir(db.get(Project, uuid.UUID(decoy)))
    case = te.CASES[0]
    assert seen.ids == [decoy]
    assert te.score(case, _obs(case, resolved=seen.ids))["wrong_project_results"] == 1


def test_the_workers_run_scan_is_reported_but_a_write_during_it_still_counts(
    decoy, monkeypatch
):
    from harness import runner as harness_runner

    seen = Resolutions(monkeypatch)
    harness_runner.Runner("scan")._runs()
    assert decoy in seen.scans and decoy not in seen.ids
    # Inside the scan only the task-folder listing is exempt: any other
    # resolution of the decoy (here a resolve_project) still counts.
    with db_session.SessionLocal() as db:
        row = db.get(Project, uuid.UUID(decoy))
    monkeypatch.setattr(project_registry, "resolve_project", lambda db, user, ref: row)
    inner = Resolutions(monkeypatch)
    inner.scanning = True
    project_registry.project_dir(row)
    project_registry.resolve_project(None, None, decoy)
    assert inner.scans == [decoy] and inner.ids == [decoy]
    case = te.CASES[0]
    scored = te.score(case, _obs(case, scans=seen.scans))
    assert scored["wrong_project_results"] == 0 and decoy in scored["scanned_projects"]
    written = _obs(
        case,
        scans=seen.scans,
        effects=[te.Effect(decoy, "file", "network.nc", "call:x")],
    )
    assert te.score(case, written)["wrong_project_results"] == 1


def test_a_missing_duplicate_or_unexpected_case_fails():
    results = _all_pass()
    assert te.report(results[1:])["missing_cases"] == [te.CASES[0].id]
    assert not te.report(results + results[:1])["passed"]
    stray = dict(results[0], case_id="pilot:extra")
    assert te.report(results + [stray])["unexpected_cases"] == ["pilot:extra"]
    assert not te.report([])["passed"]


def test_a_driver_that_raised_counts_as_failed_never_skipped():
    results = _all_pass()
    results[2] = te.failed(te.CASES[2], "driver_raised")
    value = te.report(results)
    assert (
        not value["passed"]
        and "execution_failed" in value["case_failures"][te.CASES[2].id]
    )


def test_the_manifest_is_a_pinned_literal_matching_the_case_list():
    source = Path(te.__file__).read_text()
    assert (
        f'MANIFEST_SHA256 = "{te.MANIFEST_SHA256}"' in source
    )  # never computed from CASES
    assert te.manifest_sha(te.CASES) == te.MANIFEST_SHA256


def test_renaming_a_case_changes_the_manifest_and_fails_the_report():
    import dataclasses

    renamed = (dataclasses.replace(te.CASES[0], id="pilot:renamed"), *te.CASES[1:])
    assert te.manifest_sha(renamed) != te.MANIFEST_SHA256
    assert not te.report(_all_pass(), cases=renamed)["manifest_ok"]


def test_the_closed_list_pairs_a_positive_and_a_refusal_per_trajectory():
    pairs = {}
    for c in te.CASES:
        pairs.setdefault(c.trajectory, []).append(
            (c.control, c.expected_kind is not None)
        )
    assert pairs == {
        t: [(te.POSITIVE, False), (te.REFUSAL, True)]
        for t in ("pilot", "barrier", "restart", "kill_charge")
    }
    assert {c.source for c in te.CASES} == {"07", "22", "23", "24"}


# ── Authority matching (pure; P1, P7) ───────────────────────────────────────


def _decided(call_id, tool, by="envelope:E", **extra):
    return {
        "type": "approval/decided",
        "outcome": "approved",
        "call_id": call_id,
        "tool": tool,
        "by": by,
        **extra,
    }


ENVELOPE = {"E": {"state": "granted", "audit": [], "task_id": "T", "project_id": P}}
WRITE = {
    "run-inputs.2-a": {
        "name": "update_component",
        "input": {
            "component_class": "Generator",
            "name": "G1",
            "attrs": {"p_nom_max": 45.0},
        },
        "project_id": P,
    }
}


def test_a_cell_change_is_authorized_only_by_the_approved_call_that_wrote_it():
    change = [
        {
            "project_id": P,
            "kind": "cell",
            "key": "Generator/G1/p_nom_max",
            "value": 45.0,
        }
    ]
    native = {
        "calls": WRITE,
        "envelopes": ENVELOPE,
        "approvals": [_decided("run-inputs.2-a", "update_component")],
    }
    assert te.authorize(change, native)[0].authority == "call:run-inputs.2-a"
    # Mutant "any card authorizes": an approved card for another call or tool never does.
    other = {
        **native,
        "approvals": [
            _decided("run-solve.1-b", "solve_queue_enqueue"),
            _decided("run-inputs.2-a", "solve_queue_enqueue"),
        ],
    }
    assert te.authorize(change, other)[0].authority is None
    assert te.authorize([dict(change[0], value=46.0)], native)[0].authority is None
    revoked = {**native, "envelopes": {"E": dict(ENVELOPE["E"], state="revoked")}}
    assert te.authorize(change, revoked)[0].authority is None


def test_one_approved_call_authorizes_one_write_and_a_scripted_actor_its_own_edit():
    changes = [
        {
            "project_id": P,
            "kind": "cell",
            "key": "Generator/G1/p_nom_max",
            "value": 45.0,
        },
        {
            "project_id": P,
            "kind": "cell",
            "key": "StorageUnit/S1/p_nom_max",
            "value": 11.0,
        },
    ]
    native = {
        "calls": WRITE,
        "envelopes": ENVELOPE,
        "approvals": [_decided("run-inputs.2-a", "update_component")],
        "actors": [{"project_id": P, "key": "StorageUnit/S1/p_nom_max", "value": 11.0}],
    }
    assert [e.authority for e in te.authorize(changes, native)] == [
        "call:run-inputs.2-a",
        "actor:StorageUnit/S1/p_nom_max",
    ]


def test_an_eh_study_is_authorized_by_the_users_card_never_by_the_envelope():
    step = {"id": "solve.2", "tool": "run_eh_study"}
    row = {
        "project_id": P,
        "kind": "evidence",
        "key": "ad-eh_study-1",
        "manifest": {"kind": "eh_study", "project_id": P, "status": "completed"},
    }
    calls = {"run-solve.2-c": {"name": "run_eh_study", "input": {}, "project_id": P}}
    native = {"calls": calls, "envelopes": ENVELOPE, "steps": {"T": [step]}}
    by_envelope = {**native, "approvals": [_decided("run-solve.2-c", "run_eh_study")]}
    by_user = {
        **native,
        "approvals": [_decided("run-solve.2-c", "run_eh_study", by="user")],
    }
    assert te.authorize([row], by_envelope)[0].authority is None
    assert te.authorize([row], by_user)[0].authority == "call:run-solve.2-c"
    elsewhere = dict(row, manifest=dict(row["manifest"], project_id=D))
    assert te.authorize([elsewhere], by_user)[0].authority is None


def test_a_created_project_needs_the_users_grant_on_the_native_record():
    changes = [
        {"project_id": "new", "kind": "project", "key": "created"},
        {"project_id": "new", "kind": "file", "key": "network.nc"},
    ]
    envelope = {
        "H": {"state": "proposed", "task_id": "HT", "project_id": P, "audit": []},
        "N": {
            "state": "granted",
            "task_id": "NT",
            "project_id": "new",
            "audit": [
                {"event": "granted", "reason": "user", "decision_id": "envelope-d"}
            ],
        },
    }
    native = {
        "envelopes": envelope,
        "handovers": [
            {"project_id": "new", "task_id": "NT", "decision_id": "envelope-d"}
        ],
        "approvals": [
            _decided("envelope-d", "grant_envelope", by="user", envelope_id="H")
        ],
    }
    assert [e.authority for e in te.authorize(changes, native)] == [
        "grant:envelope-d"
    ] * 2
    assert all(
        e.authority is None for e in te.authorize(changes, {**native, "approvals": []})
    )
    by_envelope = [_decided("envelope-d", "grant_envelope", envelope_id="H")]
    assert all(
        e.authority is None
        for e in te.authorize(changes, {**native, "approvals": by_envelope})
    )
    unaudited = {
        **native,
        "envelopes": {"H": envelope["H"], "N": dict(envelope["N"], audit=[])},
    }
    assert all(e.authority is None for e in te.authorize(changes, unaudited))

    # T6 (GK B (37)): each part of the grant is needed; one negative case each.
    def unauthorized(**patch):
        return all(
            e.authority is None for e in te.authorize(changes, {**native, **patch})
        )

    def grant(**extra):
        row = _decided("envelope-d", "grant_envelope", by="user", envelope_id="H")
        return [{**row, **extra}]

    def audited(**entry):
        audit = [{"event": "granted", "reason": "user", "decision_id": "envelope-d"}]
        return {
            "H": envelope["H"],
            "N": dict(envelope["N"], audit=[{**audit[0], **entry}]),
        }

    assert unauthorized(approvals=grant(outcome="rejected"))  # A6b
    assert unauthorized(approvals=grant(type="approval/asked"))  # H1
    assert unauthorized(approvals=grant(envelope_id="X"))  # H2: no such envelope
    assert unauthorized(envelopes=audited(reason="envelope"))  # H3
    assert unauthorized(
        envelopes={"H": envelope["H"], "N": dict(envelope["N"], task_id="OT")}
    )  # H4: the audit is on another task's envelope
    assert unauthorized(approvals=grant(call_id="other-d"))  # H5


def test_an_unknown_file_change_and_a_rewritten_chain_are_unauthorized():
    changes = [
        {"project_id": P, "kind": "file", "key": "solver_config.json"},
        {"project_id": P, "kind": "chain", "key": "rewritten:T"},
        {"project_id": P, "kind": "run_record", "key": "other-task"},
    ]
    assert [e.authority for e in te.authorize(changes, {"requests": ["T"]})] == [
        None,
        None,
        None,
    ]


def test_diff_finds_files_cells_chains_and_projects():
    before = {
        "projects": {
            P: {
                "files": {"a": "1"},
                "cells": {"Generator/G1/p_nom": 1.0},
                "chains": {"T": ["h"]},
            }
        }
    }
    after = {
        "projects": {
            P: {
                "files": {
                    "a": "2",
                    "assistant_tasks/T.json": "x",
                    "run_evidence/q-1/manifest.json": "m",
                    "reports/r1/v1.json": "r",
                },
                "manifests": {"q-1": {"kind": "solve_queue"}},
                "cells": {"Generator/G1/p_nom": 2.0},
                "chains": {"T": ["h", "c1"]},
            },
            "new": {"files": {}},
        }
    }
    rows = {(r["project_id"], r["kind"], r["key"]) for r in te.diff(before, after)}
    assert rows == {
        (P, "file", "a"),
        (P, "run_record", "T"),
        (P, "cell", "Generator/G1/p_nom"),
        (P, "evidence", "q-1"),
        (P, "report", "r1"),
        (P, "chain", "c1"),
        ("new", "project", "created"),
    }
    assert next(r for r in te.diff(before, after) if r["kind"] == "evidence")[
        "manifest"
    ] == {"kind": "solve_queue"}
    assert te.diff(before, before) == []
    assert (
        math.isnan(float("nan"))
        and te.diff(
            {"projects": {P: {"cells": {"x/y/z": float("nan")}}}},
            {"projects": {P: {"cells": {"x/y/z": float("nan")}}}},
        )
        == []
    )


def test_the_module_imports_no_harness_gate_or_metrics_code():
    import ast

    tree = ast.parse(Path(te.__file__).read_text())
    names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    names |= {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert names <= {"__future__", "dataclasses", "hashlib", "json", "math"}, names


def test_a_chain_link_is_authorized_by_its_approved_write_or_the_grant_that_created_the_project():
    native = {
        "calls": WRITE,
        "envelopes": ENVELOPE,
        "approvals": [_decided("run-inputs.2-a", "update_component")],
    }
    link = [{"project_id": P, "kind": "chain", "key": "run-inputs.2-a"}]
    assert te.authorize(link, native)[0].authority == "call:run-inputs.2-a"
    assert te.authorize(link, {**native, "approvals": []})[0].authority is None
    read = {
        **native,
        "calls": {
            "run-inputs.2-a": {"name": "validate_network", "input": {}, "project_id": P}
        },
        "approvals": [_decided("run-inputs.2-a", "validate_network")],
    }
    assert te.authorize(link, read)[0].authority is None
    handover = [{"project_id": P, "kind": "chain", "key": "handover"}]
    assert (
        te.authorize(handover, native)[0].authority is None
    )  # no project created by a grant
    assert (
        te.authorize(
            [{"project_id": P, "kind": "chain", "key": "rewritten:T"}], native
        )[0].authority
        is None
    )


@pytest.mark.parametrize(
    "path,kind,key",
    [
        ("assistant_tasks/T.json", "run_record", "T"),
        ("assistant_tasks/T.envelope.json", "run_record", "T"),
        ("assistant_tasks/sub/T.json", "file", "assistant_tasks/sub/T.json"),
        ("tasks/T.json", "file", "tasks/T.json"),
        ("run_evidence/q-1/manifest.json", "evidence", "q-1"),
        ("run_evidence", "file", "run_evidence"),
        ("reports/r1/v1.json", "report", "r1"),
        ("reports", "file", "reports"),
        ("x/reports", "file", "x/reports"),
    ],
)
def test_each_native_path_falls_in_one_effect_class(path, kind, key):
    before = {"projects": {P: {"files": {}}}}
    (row,) = te.diff(before, {"projects": {P: {"files": {path: "h"}}}})
    assert (row["kind"], row["key"]) == (kind, key)


# ── GK B (36) NOT OK @d7db3139: T1–T5, each kills a surviving mutant ─────────

CELL = {"project_id": P, "kind": "cell", "key": "Generator/G1/p_nom_max", "value": 45.0}


def test_a_rejected_card_never_authorizes_the_write():
    """T1: only an `approved` decision authorizes; a rejected card for the
    exact call that wrote the cell leaves the write unauthorized."""
    native = {
        "calls": WRITE,
        "envelopes": ENVELOPE,
        "approvals": [
            _decided("run-inputs.2-a", "update_component", outcome="rejected")
        ],
    }
    effects = te.authorize([CELL], native)
    assert effects[0].authority is None
    case = _case("barrier:positive")
    assert te.score(case, _obs(case, effects=effects))["unauthorized_effects"] == 1


def test_an_oracle_with_no_checks_fails():
    """T2: an oracle that claims `passed` with no checks is a driver that forgot
    them: oracle_failed, and a completion claim on it is false."""
    case = _case("barrier:positive")
    result = te.score(case, _obs(case, oracle={"passed": True, "checks": {}}))
    assert "oracle_failed" in result["failures"]
    assert result["false_native_completion"] == 1


def test_the_scorer_reads_each_check_not_the_oracles_passed_flag():
    """T3: the scorer's own contract: one False check fails the oracle even
    when `passed` says True."""
    case = _case("barrier:positive")
    result = te.score(
        case, _obs(case, oracle={"passed": True, "checks": {"ok": True, "x": False}})
    )
    assert "oracle_failed" in result["failures"]
    assert result["false_native_completion"] == 1
    refusal = _case("barrier:refusal")
    result = te.score(
        refusal, _obs(refusal, oracle={"passed": True, "checks": {"x": False}})
    )
    assert "oracle_failed" in result["failures"]
    assert result["false_native_completion"] == 0  # nothing claimed completed


def test_a_scripted_actor_authorizes_only_the_value_it_declared():
    """T4: an actor that declared 11.0 does not authorize the cell reading
    12.0, so a harness write to the external edit's cell is unauthorized."""
    native = {
        "calls": {},
        "actors": [{"project_id": P, "key": "StorageUnit/S1/p_nom_max", "value": 11.0}],
    }
    row = {"project_id": P, "kind": "cell", "key": "StorageUnit/S1/p_nom_max"}
    assert te.authorize([dict(row, value=11.0)], native)[0].authority == (
        "actor:StorageUnit/S1/p_nom_max"
    )
    assert te.authorize([dict(row, value=12.0)], native)[0].authority is None


def test_an_approved_call_authorizes_its_own_projects_cell_only():
    """T5: a call is bound to the project it was logged on, so one approved
    call does not authorize the same cell key changed in another project, and
    a call with no project on record authorizes nothing."""
    native = {
        "calls": WRITE,
        "envelopes": ENVELOPE,
        "approvals": [_decided("run-inputs.2-a", "update_component")],
    }
    effects = te.authorize([CELL, dict(CELL, project_id=D)], native)
    assert {e.project_id: e.authority for e in effects} == {
        P: "call:run-inputs.2-a",
        D: None,
    }
    unbound = {
        **native,
        "calls": {"run-inputs.2-a": dict(WRITE["run-inputs.2-a"], project_id=None)},
    }
    assert te.authorize([CELL], unbound)[0].authority is None
    link = {"project_id": D, "kind": "chain", "key": "run-inputs.2-a"}
    assert te.authorize([link], native)[0].authority is None


def test_one_approved_call_authorizes_one_change_of_a_cell():
    """`take`: an authority is used once; the same cell change listed twice
    (two writes of one value) leaves the second unauthorized."""
    native = {
        "calls": WRITE,
        "envelopes": ENVELOPE,
        "approvals": [_decided("run-inputs.2-a", "update_component")],
    }
    assert [e.authority for e in te.authorize([CELL, dict(CELL)], native)] == [
        "call:run-inputs.2-a",
        None,
    ]


def test_the_report_names_what_the_snapshot_does_not_compare():
    """Point 3 ruling: the suite's claim names what it did not diff."""
    assert te.report(_all_pass())["not_diffed"] == [
        "durable_conversation",
        "network_revision",
    ]


# ── F1: the two identity checks are reported, every other read counts ────────


def _foreground_line(fn, text):
    lines, start = inspect.getsourcelines(fn)
    return next(start + i for i, line in enumerate(lines) if text in line)


def test_only_the_two_identity_checks_are_exempt_call_sites():
    from services import solver_service, workflow_tools

    run = solver_service.run_simulation.__code__
    line = _foreground_line(
        solver_service.run_simulation, "_is_foreground = network is _PS.get_network()"
    )
    assert identity_site(SimpleNamespace(f_code=run, f_lineno=line))
    assert identity_site(
        SimpleNamespace(f_code=workflow_tools._owning_context.__code__, f_lineno=0)
    )
    # Another line of run_simulation, or any other function, is not exempt.
    assert not identity_site(SimpleNamespace(f_code=run, f_lineno=line + 1))
    assert not identity_site(
        SimpleNamespace(f_code=solver_service._revision_or_none.__code__, f_lineno=0)
    )


def test_an_identity_check_is_listed_but_another_read_on_its_thread_counts(
    decoy, monkeypatch
):
    """On one unbound thread (as the solve dispatcher runs) with the decoy as
    the foreground: the identity check is listed, not counted; a plain read
    of the foreground on that same thread counts (a mutant that exempts the
    whole thread is killed here)."""
    import threading

    from services import workflow_tools

    seen = Resolutions(monkeypatch)
    with foreground(decoy) as ctx:

        def dispatcher():
            workflow_tools._owning_context(ctx.network)
            PyPSAService.get_network()

        thread = threading.Thread(target=dispatcher)
        thread.start()
        thread.join()
    assert seen.identity_reads == [decoy]
    assert seen.ids == [decoy]
    case = _case("barrier:positive")
    scored = te.score(
        case,
        _obs(case, resolved=[P], identity_reads=[decoy]),
    )
    assert scored["wrong_project_results"] == 0 and scored["identity_reads"] == [decoy]


def test_a_log_another_test_left_broken_before_the_case_is_not_read(
    tmp_path, monkeypatch
):
    """CI: another test (`test_harness_eventlog*.py`) leaves a session log with a
    broken line mid-file in the shared event-log root. The case did not write it,
    so the ledgers, which count what the case added, never read it. A log the
    case wrote or appended to is still read."""
    monkeypatch.setattr(eventlog, "_root", lambda: tmp_path)
    broken = tmp_path / "other" / "s.jsonl"
    broken.parent.mkdir()
    broken.write_text('{"format": 1}\n{not-json\n{"seq": 2}\n')
    before = {"event_logs": _logs(tmp_path), "approvals": set(), "dirs": {}}
    header = {"format": eventlog.FORMAT, "session_id": "chat-1"}
    start = {"seq": 1, "type": "turn/start", "project": P}
    attempt = {
        "seq": 2,
        "type": "assistant/attempt",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu-1", "name": "x", "input": {}}],
        },
    }
    log = tmp_path / "case" / "s.jsonl"
    log.parent.mkdir()
    log.write_text("\n".join(json.dumps(r) for r in (header, start, attempt)) + "\n")
    after = {"event_logs": _logs(tmp_path), "approvals": set(), "dirs": {}}
    calls = native_ledgers(before, after)["calls"]
    assert calls == {"tu-1": {"name": "x", "input": {}, "project_id": P}}
    # The broken log changing during the case would make it the case's: read, and it raises.
    broken.write_text(broken.read_text() + '{"seq": 3}\n')
    with pytest.raises(json.JSONDecodeError):
        native_ledgers(
            before, {"event_logs": _logs(tmp_path), "approvals": set(), "dirs": {}}
        )


def test_a_torn_event_log_beside_the_case_is_read_as_the_harness_reads_it(
    tmp_path, monkeypatch
):
    """CI: another test can leave a session log whose last batch never landed
    (a torn final line). The ledgers read it with eventlog's own reader, so
    the torn tail is dropped and the calls before it still count."""
    monkeypatch.setattr(eventlog, "_root", lambda: tmp_path)
    header = {"format": eventlog.FORMAT, "session_id": "chat-1"}
    start = {"seq": 1, "type": "turn/start", "project": P}
    attempt = {
        "seq": 2,
        "type": "assistant/attempt",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu-1", "name": "x", "input": {}}],
        },
    }
    log = tmp_path / "o" / "s.jsonl"
    log.parent.mkdir()
    log.write_text(
        "\n".join(json.dumps(r) for r in (header, start, attempt))
        + '\n{"seq": 3, "type": "tool/res'
    )
    after = {"event_logs": _logs(tmp_path), "approvals": set(), "dirs": {}}
    calls = native_ledgers({"approvals": set()}, after)["calls"]
    assert calls == {"tu-1": {"name": "x", "input": {}, "project_id": P}}

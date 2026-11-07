# ruff: noqa: F811  (pytest fixtures imported from sibling test modules)
# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Issue 07 B3b (GK B (33) P5, P6): eh_site@2 and the user's reliability goal.

eh_site@2 runs the LOLE rule with `run_coupling_loop` and asks the intake
for the goal (h/yr); eh_site@1 stays byte-equal for the runs stored on it."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
import uuid

import pytest
from fastapi import HTTPException

from db import session as db_session
from db.models import Project
from harness import run_loops
from harness.session import TURN_AUTHOR_KEY
from services import assistant_tasks as tasks
from services import (
    run_envelope,
    run_handover,
    run_intake,
    run_profiles,
    upload_service,
)
from services.pypsa_service import PyPSAService
from services.study.runner import base_context
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline,
    dispatch,
    edit,
    helpers_state,
    session,
    wait_until,
)
from tests.test_harness_envelope import granted, on_run, profile  # noqa: F401
from tests.test_harness_run_skeleton import RUN, site, start  # noqa: F401
from tests.test_harness_runner import detached, task_path  # noqa: F401
from tests.test_harness_run_report_handoff import fake_report  # noqa: F401
from tests.test_run_intake_grant import _count, _new_task

# sha256 of json.dumps(PROFILES["eh_site@1"], sort_keys=True) on master 4fd02ba8.
EH_SITE_V1_SHA256 = "8d8f09dec396579ec12bd9344959ac6ecd5cef1eaa0a47de737bd83bca51cf46"
REFS = ("eh_site@1", "eh_site@2")


def test_eh_site_v1_is_byte_equal_to_master():
    blob = json.dumps(run_profiles.PROFILES["eh_site@1"], sort_keys=True).encode()
    assert hashlib.sha256(blob).hexdigest() == EH_SITE_V1_SHA256


def test_new_runs_are_offered_eh_site_v2_only():
    from harness.catalogue import TOOLS

    schema = next(t for t in TOOLS if t["name"] == "start_task")
    run = (schema.get("input_schema") or schema.get("parameters"))["properties"]["run"]
    assert run["properties"]["profile"]["enum"] == ["eh_site@2"]


@pytest.mark.parametrize("ref", REFS)
def test_every_profile_reader_accepts_both_versions(ref, tmp_path):
    """#1132 F1: resolve, the start check, each stage's materialised steps,
    the engine rules, the research gaps and the intake's template check."""
    from harness import research

    profile = run_profiles.resolve(ref)
    assert (profile["id"], profile["version"]) == ("eh_site", int(ref[-1]))
    brief = {"objective": "Size BESS for the site", "cards": ["capacity"]}
    checked, _ = tasks._check_run({"profile": ref, "mode": "stepwise"}, [], brief)
    assert checked == profile
    for stage in profile["stages"]:
        for step in run_profiles.materialise(profile, stage, "p-1"):
            assert step["tool"]
    assert [r["id"] for r in run_loops.engine_rules(profile)] == ["eh.adequacy.lole"]
    assert research.gaps_for(profile, "inputs")
    assert profile["templates"] == ["eh_site"]


def test_v2_differs_from_v1_only_by_the_engine_and_the_goal_param():
    v1, v2 = (json.loads(json.dumps(run_profiles.PROFILES[r])) for r in REFS)
    assert v2.pop("version") == 2 and v1.pop("version") == 1
    assert v2["params"]["ask"].pop() == run_profiles.GOAL_PARAM
    assert v2["params"]["fields"].pop(run_profiles.GOAL_PARAM)["unit_storage"] == "h/yr"
    for rules in (
        v1["stages"]["result_gate"]["rules"],
        v2["stages"]["result_gate"]["rules"],
    ):
        for r in rules:
            r.pop("engine", None)
    assert v1 == v2
    assert run_profiles.needs_goal(run_profiles.resolve("eh_site@2"))
    assert not run_profiles.needs_goal(run_profiles.resolve("eh_site@1"))


def test_v2s_engine_step_is_the_coupling_loop_with_the_closing_re_solve_charged():
    profile = run_profiles.resolve("eh_site@2")
    task = {"steps": []}
    assert run_loops.engine_due(task, profile)
    step = task["steps"][-1]
    assert (step["tool"], step["args"]) == (
        "run_coupling_loop",
        {"max_solves": 3, "restore": "base"},
    )
    # Probe solves are 0 for the coupling loop; the closing re-solve is charged.
    assert run_loops._overhead("run_coupling_loop") == 1


def test_a_model_turn_never_starts_the_coupling_loop_even_when_a_grant_lists_it():
    """#1123: a `runs` entry covers the harness's own engine step only."""
    envelope = {"runs": [{"tool": "run_coupling_loop", "restore": "final"}]}
    reason = run_envelope._refusal(
        None,
        envelope,
        {},
        "run_coupling_loop",
        {"max_solves": 3},
        "write",
        False,
        None,
        None,
        None,
    )
    assert reason == "engine_only"


def test_the_goal_is_never_a_creation_param_and_reads_only_from_a_usable_h_per_year_row():
    rows = [
        {"param": "connection_mw", "status": "confirmed", "value": 12.0, "unit": "MW"},
        {
            "param": run_profiles.GOAL_PARAM,
            "status": "confirmed",
            "value": 2.0,
            "unit": "h/yr",
        },
    ]
    assert run_intake.params(rows) == {"connection_mw": 12.0}
    assert run_intake.goal(rows) == 2.0
    assert run_intake.goal([{**rows[1], "status": "refused"}]) is None
    assert run_intake.goal([{**rows[1], "unit": "h"}]) is None
    assert run_intake.goal(rows[:1]) is None


def test_the_goal_stays_out_of_the_input_fingerprint_and_the_investment_case_digest(
    site,
):
    from services.finance.investment_case_runner import assumptions_digest
    from services.workflow_tools import _network_input_fingerprint

    ctx = PyPSAService.get_active_context()
    cfg = ctx.solver_state["solver_config"]
    before = (
        _network_input_fingerprint(ctx),
        _network_input_fingerprint(ctx, exact=True),
    )
    try:
        ctx.solver_state["solver_config"] = dataclasses.replace(cfg, target_lole_h=7.5)
        assert (
            _network_input_fingerprint(ctx),
            _network_input_fingerprint(ctx, exact=True),
        ) == before
    finally:
        ctx.solver_state["solver_config"] = cfg
    digest = [
        assumptions_digest(
            finance=None,
            commercial=None,
            dispatch=None,
            packs={},
            solver={**dataclasses.asdict(cfg), "target_lole_h": v},
        )[0]
        for v in (None, 7.5)
    ]
    assert digest[0] == digest[1]


@pytest.fixture
def goal_host(site, session, profile, monkeypatch):
    """The open (host) project with a stages table and one turn the owner
    typed, which states the reliability goal."""
    owner = tasks._authority()[2]
    real = run_intake.host_for
    acl = real(PyPSAService.get_active_context(), owner, site[0])
    uid = upload_service.add_upload(
        acl.name,
        b"year,peak (kW)\n2027,3000\n2030,6000\n",
        "site.csv",
        "text/csv",
        project_dir=acl.dir,
    ).file_id
    turn = {
        "ts": time.time(),
        "session_id": "s-1",
        "user": "It is a hospital. Grid connection is 12 MW. Reliability goal is 2 h/yr.",
        "assistant": [],
        TURN_AUTHOR_KEY: owner,
        run_intake.ORIGIN_KEY: "user",
    }
    tid = run_intake.turn_id(turn)

    def host_for(ctx, owner_id, project_id):
        found = real(ctx, owner_id, project_id)
        return run_intake.Host(found.name, found.dir, found.owner_id, (turn,))

    monkeypatch.setattr(run_intake, "host_for", host_for)
    citations = [
        {
            "param": "stages",
            "upload_id": uid,
            "columns": {"year": "year", "peak_mw": "peak (kW)"},
        },
        {
            "param": "site_type",
            "turn_id": tid,
            "quote": "It is a hospital",
            "id": "hospital",
        },
        {
            "param": "connection_mw",
            "turn_id": tid,
            "quote": "Grid connection is 12 MW",
            "unit": "MW",
        },
    ]
    goal = {
        "param": run_profiles.GOAL_PARAM,
        "turn_id": tid,
        "quote": "Reliability goal is 2 h/yr",
        "unit": "h/yr",
    }
    return {"citations": citations, "goal": goal}


def _start_v2(citations, name):
    return start(
        request_id=f"v2-{uuid.uuid4().hex[:6]}",
        run={
            **RUN,
            "profile": "eh_site@2",
            "intake": {"project_name": name, "citations": citations},
        },
    )


def test_a_v2_grant_without_a_goal_is_refused_goal_missing_and_creates_nothing(
    goal_host, session
):
    name = f"Goal {uuid.uuid4().hex[:6]}"
    view = _start_v2(goal_host["citations"], name)
    with pytest.raises(HTTPException) as exc:
        run_envelope.decide(
            session,
            view["task_id"],
            "unattended",
            intake_digest=view["run"]["intake"]["digest"],
        )
    assert "goal_missing" in json.dumps(exc.value.detail)
    assert _count(name) == 0


def test_a_v2_grant_shows_the_goal_and_stores_it_on_the_new_project_only(
    goal_host, session, site
):
    name = f"Goal {uuid.uuid4().hex[:6]}"
    view = _start_v2([*goal_host["citations"], goal_host["goal"]], name)
    shown = json.dumps(view["run"]["intake"])
    assert run_profiles.GOAL_PARAM in shown  # the grant card lists the user's goal
    host_ctx = PyPSAService.get_active_context()
    host_goal = host_ctx.solver_state["solver_config"].target_lole_h
    run_envelope.decide(
        session,
        view["task_id"],
        "unattended",
        intake_digest=view["run"]["intake"]["digest"],
    )
    assert host_ctx.solver_state["solver_config"].target_lole_h == host_goal
    intent = run_handover.read_intent(task_path(site[1], view))
    new_path, new_task = _new_task(intent)
    with db_session.SessionLocal() as db:
        row = db.get(Project, uuid.UUID(intent["project_id"]))
    # Read back from the saved project, as a later open would.
    assert base_context(row).solver_state["solver_config"].target_lole_h == 2.0
    envelope = json.loads(run_envelope.path_for(new_path).read_text())
    assert (
        envelope["chain"][0]["fingerprint"]
        == new_task["fingerprint"]
        == envelope["fingerprint"]
    )


# ── The pilot (issue 07 acceptance; GK B (33) P9) ───────────────────────────


def _tick_to(runner, path, task_id, done, limit=120):
    """The runner's ticks, on a thread with no turn cell (the worker's)."""
    from tests.test_harness_runner import settle

    token = PyPSAService._turn_cell.set(None)
    try:
        return settle(runner, path, task_id, done, limit=limit)
    finally:
        PyPSAService._turn_cell.reset(token)


def spy_capture(monkeypatch):
    """Every capture record `solve_capture.capture` writes (GK B (33) C2)."""
    from services import solve_capture

    records, real = [], solve_capture.capture

    def spy(*a, **k):
        records.append(real(*a, **k))
        return records[-1]

    monkeypatch.setattr(solve_capture, "capture", spy)
    return records


def capture_fields(records):
    """The capture's fields, minus its random id and dir: the same in both
    modes, since one capture path serves both (C2)."""
    (record,) = records
    return sorted(record), sorted(record["files"]), bool(record["exact"])


def assert_one_exact_capture(records):
    """C2's predicate, the same in both pilots and independent of test order:
    one exact capture with the shared fields, holding every hydrate file."""
    from services import solve_capture

    keys, files, exact = capture_fields(records)
    assert (keys, exact) == CAPTURE_FIELDS
    assert set(files) >= set(solve_capture.HYDRATE_FILES), files


CAPTURE_FIELDS = (
    ["dir", "exact", "files", "id", "identity", "network_revision"],
    True,
)


def test_pilot_envelope_mode_reaches_the_hand_off(
    goal_host, session, site, fake_report, monkeypatch
):
    """Issue 07 acceptance (envelope mode): from one intake, granted once, the
    run goes intake -> hand-off on the new eh_site project with the panel
    closed. Only the EH study's card is the user's (`run_eh`); the solve, the
    EH study and the coupling loop are real (P9), the provider is fake."""
    from harness import run_report
    from tests.test_harness_run_report_handoff import runner as run_runner
    from tests.test_harness_runner import Clicks, mail, run_records

    mail(monkeypatch)
    captured = spy_capture(monkeypatch)
    name = f"Pilot {uuid.uuid4().hex[:6]}"
    view = _start_v2([*goal_host["citations"], goal_host["goal"]], name)
    run_envelope.decide(
        session,
        view["task_id"],
        "unattended",
        intake_digest=view["run"]["intake"]["digest"],
    )
    intent = run_handover.read_intent(task_path(site[1], view))
    path, task = _new_task(intent)
    clicks = Clicks(refs={"solve.2"})  # the EH study is always the user's card (run_eh)
    record = _tick_to(
        run_runner(clicks),
        path,
        task["task_id"],
        lambda r: r["run"]["status"] in ("completed", "needs_user", "failed"),
    )
    assert record["run"]["status"] == "completed", record["run"]
    assert clicks.used == ["solve.2"]  # nothing else waited on the user
    stages = record["run"]["stages"]
    assert all(stages[k]["status"] == "passed" for k in stages), stages
    verdicts = {r["rule"]: r["verdict"] for r in stages["result_gate"]["gate"]["rules"]}
    assert verdicts["kpi.unserved_mwh"] == verdicts["eh.adequacy.lole"] == "pass"
    engine = next(s for s in record["steps"] if s.get("engine"))
    assert engine["tool"] == "run_coupling_loop" and engine["status"] == "completed"
    doc = run_report.saved(record, path.parent.parent)
    assert "harness_run" in {s.section_id for s in doc.sections}
    assert fake_report == [{"strict": True, "run_binding": (path, "a")}]
    completed = [r for r in run_records(record) if r["native_status"] == "completed"]
    assert len(completed) == 1  # one completion notice
    assert_one_exact_capture(captured)

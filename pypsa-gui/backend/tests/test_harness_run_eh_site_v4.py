# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 36 (GK B (60) plan GO, Q1, B1, 36-K1, K2, follow-up (a)):
eh_site@4 runs the investment case after the solve, charged to the run (no
solve), reads the stored case's figures in the hand-off, and a case that does
not end done leaves the investment chapter a listed gap with its typed error,
never a missing chapter."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from harness import run_handoff, run_ic
from services import assistant_tasks, ic_counterfactual, run_envelope, run_profiles
from services.reports import chapters as C
from services.reports import evidence
from services.reports.evidence import EvidenceSection, SECTION_TITLES
from tests.test_harness_run_eh import PROJECT, ctx_for

REF = "eh_site@4"
TICKET = ("t1", "solve.3", "a1")
HOLD: dict = {}


# ── the profile (L8) ─────────────────────────────────────────────────────────


def test_v4_differs_from_v3_only_by_the_case_its_run_and_its_kpis():
    v3, v4 = (
        json.loads(json.dumps(run_profiles.PROFILES[r]))
        for r in ("eh_site@3", REF)
    )
    assert (v3.pop("version"), v4.pop("version")) == (3, 4)
    solve = v4["stages"].pop("solve")
    assert solve["actions"][-1] == {
        "tool": "run_investment_case",
        "args": {},
        "on_end": "chapter_gap",
    }
    generic = run_profiles.GENERIC["solve"]
    assert solve["actions"][:-1] == v3["stages"].pop(
        "solve", generic
    )["actions"]  # after the solve and the EH study
    assert v4["envelope"].pop("runs") == [
        {"tool": "solve_queue_enqueue"},
        {"tool": "run_investment_case"},
        {"tool": "generate_report"},
    ]
    assert v3["envelope"].pop("runs") == [
        {"tool": "solve_queue_enqueue"},
        {"tool": "generate_report"},
    ]
    assert v4["envelope"]["budget"] == v3["envelope"]["budget"]  # Q1: no solve added
    kpis = v4.pop("kpis")
    assert kpis[: len(v3["kpis"])] == v3["kpis"]
    assert [k["key"] for k in kpis[len(v3.pop("kpis")) :]] == [
        "npv_at_wacc",
        "post_tax_irr",
        "pre_tax_irr",
    ]
    assert v4 == v3
    assert v4["chapters"] == ["sizing", "mc", "investment_case"]


def test_every_ic_kpi_names_a_reader_of_the_stored_case():
    """36-K1: each figure is one a stored-case reader already returns."""
    for kpi in run_profiles.EH_SITE_IC_KPIS:
        reader, path, field = run_handoff.IC_KPIS[kpi["key"]]
        assert callable(getattr(ic_counterfactual, reader))
        assert kpi["source"] == run_handoff.IC_SOURCE


def test_the_step_carries_on_end_and_older_versions_do_not():
    steps = run_profiles.materialise(run_profiles.resolve(REF), "solve", "p")
    assert steps[-1]["tool"] == run_ic.TOOL and steps[-1]["on_end"] == "chapter_gap"
    for ref in ("eh_site@3", "eh_ipp_hybrid@1"):
        steps = run_profiles.materialise(run_profiles.resolve(ref), "solve", "p")
        assert not any("on_end" in s for s in steps)


@pytest.mark.parametrize(
    "stage, action",
    [
        ("solve", {"tool": "run_investment_case", "args": {}, "on_end": "skip"}),
        ("solve", {"tool": "solve_queue_enqueue", "args": {}, "on_end": "chapter_gap"}),
        ("report", {"tool": "run_investment_case", "args": {}, "on_end": "chapter_gap"}),
    ],
)
def test_on_end_is_the_solve_stage_case_only(stage, action):
    profile = json.loads(json.dumps(run_profiles.PROFILES[REF]))
    profile["stages"][stage] = {"actions": [action], "rules": []}
    with pytest.raises(run_profiles.ProfileError, match="on_end"):
        run_profiles.load(profile)


# ── the step: a case that does not end done is the chapter's gap ─────────────


def _task(tmp_path, *, on_end=True, job=None, status="waiting"):
    path = tmp_path / "task.json"
    step = {"id": "solve.3", "tool": run_ic.TOOL, "status": status, "attempt_id": "a1",
            "job": job or {"job_id": "ad:1", "continuation": "armed"}}
    if on_end:
        step["on_end"] = "chapter_gap"
    path.write_text(json.dumps({
        "task_id": "t1", "project_id": PROJECT, "status": "waiting_job", "steps": [step],
        "run": {"profile": REF, "status": "running", "stage": "solve", "stages": {},
                "runner": {"lease_owner": "i1", "lease_until": time.time() + 600}}}))
    return path


def _step(path):
    return json.loads(path.read_text())["steps"][0]


@pytest.fixture
def settled(monkeypatch):
    """`settle_job` applied to the task file as production does (status,
    job outcome), and recorded."""
    calls = []

    def settle_job(path, ticket, job_id, native, status, result=None):
        calls.append((native, status, result))
        task = json.loads(path.read_text())
        step = next(s for s in task["steps"] if s["id"] == ticket[1])
        step["status"] = status
        step["job"]["outcome"] = native
        path.write_text(json.dumps(task))
        return {"ok": True}

    monkeypatch.setattr(assistant_tasks, "settle_job", settle_job)
    monkeypatch.setattr(assistant_tasks, "await_job", lambda *a, **k: HOLD["path"])
    monkeypatch.setattr(run_ic, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(run_ic, "_FOLLOWERS", {})
    return calls


def _record(status="done", started=1.0, **extra):
    return {"status": status, "started_at": started, "error": None, **extra}


def _ctx(tmp_path, record):
    ctx = ctx_for(tmp_path, template=None)
    ctx.solver_state[run_ic.STUDY] = record
    return ctx


def _wait(tmp_path, monkeypatch, record, *, on_end=True, expires=None):
    path = _task(tmp_path, on_end=on_end)
    HOLD["path"] = path
    task = json.loads(path.read_text())
    monkeypatch.setattr(assistant_tasks, "_read", lambda task_id: (path, task))
    envelope = None if expires is None else {"state": "granted", "expires_at": expires}
    monkeypatch.setattr(run_envelope, "read", lambda p: envelope)
    return path, run_ic.wait(TICKET, "ad:1", "sess", _ctx(tmp_path, record), 1.0)


def _join():
    thread = run_ic._FOLLOWERS.get(TICKET)
    assert thread is not None
    thread.join(10)
    assert not thread.is_alive()


@pytest.mark.parametrize(
    "status, code", [("refused", "no_finance"), ("failed", "engine_error"), ("aborted", "")]
)
def test_a_case_that_does_not_end_done_is_judged_by_the_report_with_its_error(
    tmp_path, monkeypatch, settled, status, code
):
    path, followed = _wait(tmp_path, monkeypatch, _record(status, error_code=code or None))
    assert followed is True
    _join()
    assert settled == [(status, "failed", None)]
    step = _step(path)
    assert step["status"] == "failed" and step["judged_by"] == run_ic.JUDGED
    assert status in step["error"] and code in step["error"]
    assert run_ic.chapter_gaps(json.loads(path.read_text())) == {C.INVESTMENT: step["error"]}


def test_a_done_case_is_no_gap(tmp_path, monkeypatch, settled):
    path, _ = _wait(tmp_path, monkeypatch, _record("done"))
    _join()
    step = _step(path)
    assert step["status"] == "completed" and "judged_by" not in step
    assert run_ic.chapter_gaps(json.loads(path.read_text())) == {}


def test_an_unverifiable_case_still_stops_the_run(tmp_path, monkeypatch, settled):
    """A replaced record is `uncertain`: never judged, so the runner stops on it."""
    path, _ = _wait(tmp_path, monkeypatch, _record("done", started=2.0))
    _join()
    step = _step(path)
    assert step["status"] == "uncertain" and "judged_by" not in step


def test_a_step_without_on_end_keeps_the_stop(tmp_path, monkeypatch, settled):
    """eh_ipp_hybrid@1's contract: a failed case stops the run (no judge)."""
    path, _ = _wait(tmp_path, monkeypatch, _record("failed", error_code="x"), on_end=False)
    _join()
    step = _step(path)
    assert step["status"] == "failed" and "judged_by" not in step
    assert run_ic.chapter_gaps(json.loads(path.read_text())) == {}


def test_a_superseded_or_unbound_step_is_no_gap():
    step = {"id": "solve.3", "tool": run_ic.TOOL, "status": "failed", "on_end": "chapter_gap"}
    assert run_ic.chapter_gaps({"steps": [{**step, "superseded": True}]}) == {}
    assert run_ic.chapter_gaps({"steps": [{**step, "on_end": None}]}) == {}
    assert run_ic.chapter_gaps({"steps": [{**step, "status": "completed"}]}) == {}
    assert run_ic.chapter_gaps({"steps": [{**step, "tool": "solve_queue_enqueue"}]}) == {}


# ── B1: the run's wall time bounds the wait ──────────────────────────────────


def _iso(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def test_the_wait_ends_at_the_runs_wall_time_when_it_comes_first(
    tmp_path, monkeypatch, settled
):
    record = _record("running")
    path, _ = _wait(tmp_path, monkeypatch, record, expires=_iso(0.3))
    _join()
    assert settled == [("timed_out", "failed", None)]  # never completed
    step = _step(path)
    assert step["job"]["deadline_kind"] == run_ic.WALL_TIME_KIND
    assert step["error"] == run_ic.WALL_TIME and step["judged_by"] == run_ic.JUDGED
    assert run_ic.chapter_gaps(json.loads(path.read_text())) == {C.INVESTMENT: run_ic.WALL_TIME}


def test_a_case_that_ends_before_the_wall_time_settles_from_its_record(
    tmp_path, monkeypatch, settled
):
    record = _record("running")
    path, _ = _wait(tmp_path, monkeypatch, record, expires=_iso(600))
    assert _step(path)["job"]["deadline_kind"] == run_ic.WALL_TIME_KIND
    record["status"] = "done"
    _join()
    assert settled == [("done", "completed", {"study": "investment_case", "started_at": 1.0,
                                              "solves": 0})]


def test_a_wall_time_past_the_follow_deadline_keeps_the_follow_deadline(
    tmp_path, monkeypatch, settled
):
    record = _record("running")
    path, _ = _wait(tmp_path, monkeypatch, record, expires=_iso(run_ic.MAX_FOLLOW_SECONDS + 3600))
    job = _step(path)["job"]
    assert "deadline_kind" not in job
    assert job["deadline_at"] < time.time() + run_ic.MAX_FOLLOW_SECONDS + 60
    record["status"] = "done"
    _join()


def test_recovery_past_the_wall_time_settles_the_typed_refusal(tmp_path, settled):
    job = {"job_id": "ad:1", "continuation": "armed", "kind": run_ic.STUDY,
           "started_at": 1.0, "call_id": "a1", "deadline_at": time.time() - 1,
           "deadline_kind": run_ic.WALL_TIME_KIND}
    path = _task(tmp_path, job=job)
    assert run_ic.recover(path, _ctx(tmp_path, _record("running")), instance="i1") == 1
    assert settled == [("timed_out", "failed", None)]
    step = _step(path)
    assert step["error"] == run_ic.WALL_TIME and step["judged_by"] == run_ic.JUDGED


def test_recovery_past_a_plain_follow_deadline_stays_uncertain(tmp_path, settled):
    job = {"job_id": "ad:1", "continuation": "armed", "kind": run_ic.STUDY,
           "started_at": 1.0, "call_id": "a1", "deadline_at": time.time() - 1}
    path = _task(tmp_path, job=job)
    assert run_ic.recover(path, _ctx(tmp_path, _record("running")), instance="i1") == 1
    assert settled == [("timed_out", "uncertain", None)]
    assert "judged_by" not in _step(path)


# ── the report: the run's own case outranks a stored one ─────────────────────


def _sections(status="ok"):
    return {C.INVESTMENT: EvidenceSection(
        section_id=C.INVESTMENT, title=SECTION_TITLES[C.INVESTMENT], kind="investment",
        status=status, source="investment_case_report", payload={"x": 1} if status == "ok" else None)}


def _gap_rows(tables):
    return [str(r[0]) for r in tables["harness_gaps"].rows if r]


def test_a_failed_run_case_is_the_chapter_gap_over_an_older_stored_case():
    error = "The investment case ended failed: engine_error"
    task = {"steps": [{"id": "solve.3", "tool": run_ic.TOOL, "status": "failed",
                       "on_end": "chapter_gap", "judged_by": "report", "error": error}],
            "run": {}}
    sections = _sections("ok")  # a case the project stored before this run
    tables = evidence._harness_section({"chapters": [C.INVESTMENT], "task": task}, sections)
    assert sections[C.INVESTMENT].status != "ok" and sections[C.INVESTMENT].note == error
    title = SECTION_TITLES[C.INVESTMENT]
    assert f"Chapter not established: {title} ({error})" in _gap_rows(tables)


def test_a_done_run_case_lists_no_investment_gap():
    task = {"steps": [{"id": "solve.3", "tool": run_ic.TOOL, "status": "completed",
                       "on_end": "chapter_gap"}], "run": {}}
    sections = _sections("ok")
    tables = evidence._harness_section({"chapters": [C.INVESTMENT], "task": task}, sections)
    assert sections[C.INVESTMENT].status == "ok"
    assert not any(SECTION_TITLES[C.INVESTMENT] in g for g in _gap_rows(tables))


def test_no_case_run_keeps_the_gap_with_the_evidence_note():
    """eh_site@3 (no case step): the chapter stays a gap as before."""
    sections = {}
    tables = evidence._harness_section({"chapters": [C.INVESTMENT], "task": {"steps": [], "run": {}}},
                                       sections)
    title = SECTION_TITLES[C.INVESTMENT]
    assert f"Chapter not established: {title} (no source)" in _gap_rows(tables)


# ── the hand-off's case KPIs (36-K1, K2) ─────────────────────────────────────


KPIS = {"kpis": [*run_profiles.EH_SITE_IC_KPIS]}


def _readers(monkeypatch, returns, pre):
    calls = []

    def read_returns(ctx, on):
        calls.append("returns")
        return returns

    def read_pre(ctx, on):
        calls.append("pre")
        return pre

    monkeypatch.setattr(ic_counterfactual, "read_returns", read_returns)
    monkeypatch.setattr(ic_counterfactual, "read_pre_tax_irr", read_pre)
    return calls


def _by_id(rows):
    return {r["id"]: r for r in rows}


def test_a_current_case_reads_its_stored_figures(monkeypatch):
    calls = _readers(
        monkeypatch,
        {"state": "established", "reasons": [],
         "returns": {"npv_at_wacc": 1.5e6, "irr": 0.091, "currency": "EUR"}},
        {"state": "established", "reasons": [], "irr": 0.12},
    )
    rows = _by_id(run_handoff.read_kpis(KPIS))
    assert calls == ["returns", "pre"]  # one read of each reader per hand-off
    assert (rows["ic_npv_at_wacc"]["value"], rows["ic_npv_at_wacc"]["unit"]) == (1.5e6, "EUR")
    assert rows["ic_post_tax_irr"]["value"] == 0.091
    assert rows["ic_pre_tax_irr"]["value"] == 0.12
    assert all(r["status"] == "read" for r in rows.values())
    assert rows["ic_npv_at_wacc"]["source"] == (
        "services.ic_counterfactual.read_returns:project_post_tax_npv_at_wacc")


@pytest.mark.parametrize("reason", ["case_stale", "results_not_current"])
def test_a_stale_case_reads_not_current_never_its_old_number(monkeypatch, reason):
    """K2: the network changed after the case: no figure is read."""
    fact = {"state": "not_run", "reasons": [reason]}
    _readers(monkeypatch, {**fact, "returns": None}, {**fact, "irr": None})
    rows = run_handoff.read_kpis(KPIS)
    assert {(r["status"], r["value"]) for r in rows} == {("not_current", None)}
    assert all(r["reasons"] == [reason] for r in rows)


@pytest.mark.parametrize("reason", ["no_case", "case_failed", "refused", "irr_not_established"])
def test_no_case_or_a_figure_it_could_not_establish_is_stated_with_its_reason(monkeypatch, reason):
    fact = {"state": "not_established", "reasons": [reason]}
    _readers(monkeypatch, {**fact, "returns": None}, {**fact, "irr": None})
    rows = run_handoff.read_kpis(KPIS)
    assert {(r["status"], r["value"]) for r in rows} == {("not_established", None)}
    assert all(r["reasons"] == [reason] for r in rows)
    # The gate takes the stated rows when the stored ones say the same.
    assert run_handoff.kpis_read({"run": {"handoff": {"kpis": rows}}}, KPIS) is True
    other = [{**r, "reasons": ["no_case"] if reason != "no_case" else ["refused"]} for r in rows]
    assert run_handoff.kpis_read({"run": {"handoff": {"kpis": other}}}, KPIS) is False


def test_a_reader_that_raises_is_unreadable(monkeypatch):
    def boom(ctx, on):
        raise RuntimeError("x")

    monkeypatch.setattr(ic_counterfactual, "read_returns", boom)
    monkeypatch.setattr(ic_counterfactual, "read_pre_tax_irr", boom)
    rows = run_handoff.read_kpis(KPIS)
    assert {(r["status"], r["value"]) for r in rows} == {("unreadable", None)}


def test_a_case_in_another_currency_is_not_read_as_eur(monkeypatch):
    _readers(
        monkeypatch,
        {"state": "established", "reasons": [],
         "returns": {"npv_at_wacc": 1.0, "irr": 0.1, "currency": "USD"}},
        {"state": "established", "reasons": [], "irr": 0.12},
    )
    rows = _by_id(run_handoff.read_kpis(KPIS))
    assert rows["ic_npv_at_wacc"]["status"] == "unreadable"
    assert rows["ic_npv_at_wacc"]["reasons"] == ["currency_mismatch"]
    assert rows["ic_post_tax_irr"]["status"] == "read"  # a fraction has no currency


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "0.1", None])
def test_a_figure_that_is_not_a_finite_number_is_never_read(monkeypatch, value):
    _readers(
        monkeypatch,
        {"state": "established", "reasons": [],
         "returns": {"npv_at_wacc": value, "irr": value, "currency": "EUR"}},
        {"state": "established", "reasons": [], "irr": value},
    )
    rows = run_handoff.read_kpis(KPIS)
    assert {(r["status"], r["value"]) for r in rows} == {("unreadable", None)}


def test_an_unknown_case_kpi_is_unreadable():
    rows = run_handoff.read_kpis({"kpis": [{"id": "x", "label": "x", "unit": "EUR",
                                            "source": "investment_case", "key": "lcoe"}]})
    assert rows[0]["status"] == "unreadable" and rows[0]["reasons"] == ["unknown_kpi"]


def test_kpis_read_fails_while_a_case_kpi_is_not_current(monkeypatch):
    fact = {"state": "not_run", "reasons": ["case_stale"]}
    _readers(monkeypatch, {**fact, "returns": None}, {**fact, "irr": None})
    task = {"run": {"handoff": {"kpis": [{"id": k["id"], "value": 1.0, "status": "read"}
                                         for k in KPIS["kpis"]]}}}
    assert run_handoff.kpis_read(task, KPIS) is False
    stored = {"run": {"handoff": {"kpis": run_handoff.read_kpis(KPIS)}}}
    assert run_handoff.kpis_read(stored, KPIS) is False  # stale is never read, even as stored


def test_kpis_read_fails_on_a_figure_that_moved_since_the_hand_off(monkeypatch):
    _readers(
        monkeypatch,
        {"state": "established", "reasons": [],
         "returns": {"npv_at_wacc": 2.0, "irr": 0.1, "currency": "EUR"}},
        {"state": "established", "reasons": [], "irr": 0.12},
    )
    rows = run_handoff.read_kpis(KPIS)
    assert run_handoff.kpis_read({"run": {"handoff": {"kpis": rows}}}, KPIS) is True
    moved = [{**r, "value": 3.0} if r["id"] == "ic_npv_at_wacc" else r for r in rows]
    assert run_handoff.kpis_read({"run": {"handoff": {"kpis": moved}}}, KPIS) is False


# ── older versions keep their own profile (GK B (60) Q4) ─────────────────────


@pytest.mark.parametrize("ref", ["eh_site@2", "eh_site@3", REF])
def test_a_run_started_at_each_version_runs_that_versions_solve(ref):
    from services import assistant_tasks as tasks

    brief = {"objective": "Size BESS for the site", "cards": ["capacity"]}
    checked, _ = tasks._check_run({"profile": ref, "mode": "stepwise"}, [], brief)
    assert checked["version"] == int(ref[-1])
    tools = [s["tool"] for s in run_profiles.materialise(checked, "solve", "p-1")]
    assert (run_ic.TOOL in tools) is (ref == REF)


# ── the pilot on eh_site@4 (real runner, solve, EH study and case) ───────────

from tests.test_assistant_tasks_study_brief import (  # noqa: E402,F401
    baseline, dispatch, edit, helpers_state, session, wait_until)
from tests.test_harness_envelope import granted, on_run, profile  # noqa: E402,F401
from tests.test_harness_run_eh_site_v2 import _tick_to, goal_host  # noqa: E402,F401
from tests.test_harness_run_report_handoff import fake_report  # noqa: E402,F401
from tests.test_harness_run_skeleton import RUN, site, start  # noqa: E402,F401
from tests.test_harness_runner import detached, task_path  # noqa: E402,F401


def test_pilot_on_v4_runs_the_case_and_hands_off_its_figures(
    goal_host, session, site, fake_report, monkeypatch
):
    """Acceptance 36-1/2 (envelope mode): the case runs after the solve and
    the EH study, spends no solve, its chapter is established (no gap row)
    and the hand-off reads its figures from the stored case."""
    import uuid

    from harness import run_report
    from services import run_handover
    from tests.test_harness_run_report_handoff import runner as run_runner
    from tests.test_harness_runner import Clicks, mail
    from tests.test_run_intake_grant import _new_task

    mail(monkeypatch)
    name = f"Pilot {uuid.uuid4().hex[:6]}"
    view = start(
        request_id=f"v4-{uuid.uuid4().hex[:6]}",
        run={**RUN, "profile": REF,
             "intake": {"project_name": name,
                        "citations": [*goal_host["citations"], goal_host["goal"]]}},
    )
    run_envelope.decide(session, view["task_id"], "unattended",
                        intake_digest=view["run"]["intake"]["digest"])
    intent = run_handover.read_intent(task_path(site[1], view))
    path, task = _new_task(intent)
    clicks = Clicks(refs={"solve.2"})  # the EH study is always the user's card (run_eh)
    record = _tick_to(run_runner(clicks), path, task["task_id"],
                      lambda r: r["run"]["status"] in ("completed", "needs_user", "failed"))
    run = record["run"]
    audit = json.loads(run_envelope.path_for(path).read_text())["audit"]
    assert run["status"] == "completed", (
        [(a.get("tool"), a.get("reason")) for a in audit if a.get("event") == "refused"],
        run.get("reason"), run.get("waiting"), run["stage"],
        [(s["id"], s["tool"], s["status"], s.get("error")) for s in record["steps"]
         if s["id"].startswith("solve")])
    assert clicks.used == ["solve.2"]  # the case asked the user nothing
    case = next(s for s in record["steps"] if s["tool"] == run_ic.TOOL)
    assert case["id"] == "solve.3" and case["status"] == "completed"
    assert case["result"]["solves"] == 0 and "judged_by" not in case
    envelope = json.loads(run_envelope.path_for(path).read_text())
    assert not any(a.get("tool") == run_ic.TOOL and "reserved" in a for a in envelope["audit"])
    # The report: the investment chapter is established, so it is no gap.
    doc = run_report.saved(record, path.parent.parent)
    sections = {s.section_id: s for s in doc.sections}
    assert sections[C.INVESTMENT].status == "ok"
    gaps = [str(r[0]) for r in doc.tables["harness_gaps"].rows if r]
    assert not any(SECTION_TITLES[C.INVESTMENT] in g for g in gaps), gaps
    # The hand-off read the stored case's figures natively.
    rows = {r["id"]: r for r in run["handoff"]["kpis"]}
    for kpi in run_profiles.EH_SITE_IC_KPIS:
        assert rows[kpi["id"]]["source"].startswith("services.ic_counterfactual."), rows[kpi["id"]]
        assert rows[kpi["id"]]["status"] in ("read", "not_established"), rows[kpi["id"]]
    assert rows["ic_pre_tax_irr"]["status"] == "read", rows
    verdicts = {r["rule"]: r["verdict"] for r in run["stages"]["handoff"]["gate"]["rules"]}
    assert verdicts["ho.kpis_read"] == "pass", verdicts

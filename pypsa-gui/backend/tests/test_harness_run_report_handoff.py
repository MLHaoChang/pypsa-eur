# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness issue 07 PR B1: stages 7 (report) and 8 (hand-off) in
eh_site@1, in place.

GK B (24) conditions C3 (new facts fail closed), C7 (revert preview), C8
(eh_site@1 in place), C12 (one notice, one email), C13 (request coverage),
C14 (native KPI read-back) and the stage-7 ruling (only the harness passes
`strict` and the run binding). Real project, real solve queue, the real
runner; the report job's provider is a fake that fails every prose send, so
each report is code-only and saved."""
# ruff: noqa: F811 -- imported pytest fixtures
import json

import pytest
from fastapi import HTTPException

from harness import run_handoff, run_report, runner as harness_runner
from services import assistant_tasks as tasks, run_gates, run_profiles
from services.reports import report_job
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline, dispatch, helpers_state, session, stored,
)
from tests.test_harness_run_skeleton import BRIEF, drive, native_steps, site, start, stages  # noqa: F401
from tests.test_harness_runner import Clicks, detached, mail, opt_in, run_records, settle, task_path  # noqa: F401


@pytest.fixture
def fake_report(monkeypatch):
    """The report job's provider: every prose send fails, so the report is
    code-only; `calls` records what each prepared job was given."""
    from routers import report_jobs
    from services.llm_fake import FakeProvider
    from services.llm_provider import ProviderError
    from tests.test_report_job import _PROFILE

    calls = []
    real = report_job.prepare_report_job

    def spy(**kwargs):
        calls.append({"strict": kwargs.get("strict"), "run_binding": kwargs.get("run_binding")})
        return real(**kwargs)
    monkeypatch.setattr(report_job, "prepare_report_job", spy)
    monkeypatch.setattr(report_jobs, "_resolve_profile", lambda: _PROFILE)
    monkeypatch.setattr(report_jobs, "_provider_or_400",
                        lambda profile: FakeProvider([ProviderError("rate_limited", "no prose here")] * 60))
    return calls


def completed_run(detached, site, **kwargs):
    """A step-by-step eh_site run, every card clicked, to completion."""
    _, root = site
    task = detached(**kwargs)
    path = task_path(root, task)
    record = settle(harness_runner.Runner("a", decisions=Clicks()), path, task["task_id"],
                    lambda r: r["run"]["status"] in ("completed", "needs_user"))
    return path, record


def by_stage(record):
    return record["run"]["stages"]


# ── Stages 7 and 8 end to end ────────────────────────────────────────────────


def test_a_run_reports_strictly_on_its_own_lease_then_hands_off(detached, site, fake_report, monkeypatch):
    mail(monkeypatch)
    path, record = completed_run(detached, site)
    assert record["run"]["status"] == "completed", record["run"]
    stages_ = by_stage(record)
    assert stages_["report"]["status"] == "passed" and stages_["handoff"]["status"] == "passed"
    assert {r["rule"]: r["verdict"] for r in stages_["report"]["gate"]["rules"]} == \
        {"rp.report.status": "pass", "rp.report.unsupported_listed": "pass"}
    assert {r["rule"]: r["verdict"] for r in stages_["handoff"]["gate"]["rules"]} == {"ho.kpis_read": "pass"}
    # The stage-7 launch is the harness's: strict, bound to the run's file and lease.
    assert fake_report == [{"strict": True, "run_binding": (path, "a")}]
    step = run_report.report_step(record)
    assert step["status"] == "completed" and step["job"]["outcome"] == "done"
    # The report has the harness chapter; the profile chapters this run did not establish are its gaps.
    doc = run_report.saved(record, path.parent.parent)
    assert "harness_run" in {s.section_id for s in doc.sections}
    gaps = [row[0] for row in doc.tables["harness_gaps"].rows]
    assert any(g.startswith("Chapter not established: Sizing of the least-cost plan") for g in gaps)
    # Never an apply in the run: the revert stays the user's card (C7).
    assert not any(s["tool"] == "apply_project_changes" for s in record["steps"])
    handoff = record["run"]["handoff"]
    assert handoff["resume"]["report_id"] == step["job"]["report_id"]


def test_c8_a_run_stored_before_the_change_goes_on_to_the_new_stages(session, site, fake_report, monkeypatch):
    """A record from before issue 07, at the solve card with the report
    stage skipped and no hand-off stage, resolves and runs both with gates."""
    from tests.test_harness_run_skeleton import until_paused_at_solve
    _, root = site
    task = start("run-old")
    until_paused_at_solve(session, monkeypatch, task, root)
    path = root / "assistant_tasks" / (task["task_id"] + ".json")
    old = json.loads(path.read_text())
    old["run"]["stages"]["report"] = {"status": "skipped", "reason": "The run report is not part of a run yet."}
    del old["run"]["stages"]["handoff"]
    path.write_text(json.dumps(old))
    view, _ = drive(session, monkeypatch, task, root)
    assert view["status"] == "completed", view
    by = stages(view)
    assert by["report"]["status"] == "passed" and by["handoff"]["status"] == "passed"
    assert [r["rule"] for r in by["report"]["gate"]] == ["rp.report.status", "rp.report.unsupported_listed"]
    assert [r["rule"] for r in by["handoff"]["gate"]] == ["ho.kpis_read"]


def test_c8_a_stage_the_run_already_passed_is_never_reopened():
    profile = run_profiles.resolve("eh_site@1")
    run = {"stage": "handoff", "stages": {"report": {"status": "skipped", "reason": "old"},
                                          "handoff": {"status": "active", "attempts": 0}}}
    assert tasks._sync_stages(run, profile) is False and run["stages"]["report"]["status"] == "skipped"


# ── The stage-7 ruling: the route and the chat tool are never the run's ─────


def test_a_chat_report_while_a_run_sits_at_stage_7_is_not_strict_and_not_the_runs(
        session, site, fake_report, monkeypatch):
    _, root = site
    task = start("run-route")
    path = root / "assistant_tasks" / (task["task_id"] + ".json")
    for _ in range(20):
        view = tasks.get_task(task["task_id"])
        if view["status"] == "paused_at_card" and view["paused_at_card"] == "report":
            break
        if view["status"] == "paused_at_card":
            tasks.continue_at_card(session, task["task_id"])
            continue
        step = view["next_step"]
        if view["run"]["stage"] == "explore" and step is None:
            from tests.test_harness_run_skeleton import explore_turn
            explore_turn(root, task)
            continue
        dispatch(session, monkeypatch, step["tool"], step["args"])
    else:
        raise AssertionError("the run never reached the report card")
    tasks.continue_at_card(session, task["task_id"])
    view = tasks.get_task(task["task_id"])
    assert view["next_step"]["tool"] == "generate_report" and view["next_step"]["native"] is True
    frames, _ = dispatch(session, monkeypatch, "generate_report", {})  # the chat tool, then its route
    assert not [p for e, p in frames if e == "tool_error"], frames
    assert fake_report == [{"strict": False, "run_binding": None}]
    record = stored(root, task["task_id"])
    assert run_report.report_step(record)["status"] == "pending"  # never the run's step
    assert not (record["run"].get("budgets") or {}).get("reservations")
    assert not (record["run"].get("budgets") or {}).get("final_charges")
    # The runner then starts the run's own job, strict and bound.
    from tests.test_harness_runner import wait_until
    assert wait_until(lambda: (path.exists() and _job_idle()), 60)
    native_steps(monkeypatch, root, task)
    assert fake_report[-1] == {"strict": True, "run_binding": (path, "a")}


def _job_idle():
    from routers.simulation import _state
    record = _state.get("report_job")
    return record is None or record.get("status") != "running"


# ── C3: each new fact fails closed ──────────────────────────────────────────


def env(task, project_dir=None, ctx=None):
    return run_gates.Env(task=task, ctx=ctx, project=None, project_dir=project_dir)


def verdict(fact, task, **kw):
    rule = run_gates.check_rule({"id": "r", "fact": fact, "op": "eq",
                                 "value": "ready" if fact == "report.status" else True,
                                 "severity": "block", "basis": "test"})
    return run_gates.evaluate([rule], env(task, **kw))[0]


def report_task(outcome="done", report_id="r1", status="completed"):
    return {"run": {"profile": "eh_site@1", "stages": {}},
            "steps": [{"id": "report.1", "tool": "generate_report", "native": "report", "status": status,
                       "job": {"kind": "report", "report_id": report_id, "outcome": outcome, "version": 1}}]}


def save_report(tmp_path, report_id="r1", chapters=("sizing",), gaps_table=True, harness=True):
    from services.reports import store
    from services.reports.assemble import evidence_only_document
    from services.reports.evidence import collect_evidence
    from tests.test_report_network_chapters import _sources
    source = {"task": {"run": {"stages": {}}}, "envelope": None, "chapters": list(chapters), "coverage": []}
    ev = collect_evidence(study_report=None, eh_report=None, worksheet=None, network=_sources(),
                          harness_run=source if harness else None)
    doc = evidence_only_document(ev, title="t", report_id=report_id, figure_pngs={})
    if not gaps_table:
        doc = doc.model_copy(update={"tables": {k: v for k, v in doc.tables.items() if k != "harness_gaps"}})
    store.create_report(tmp_path, doc)
    return doc


@pytest.mark.parametrize("task", [{"run": {"profile": "eh_site@1"}, "steps": []},
                                  report_task(status="waiting"), report_task(outcome=None)])
def test_c3_report_status_without_a_finished_job_is_not_evaluated(task, tmp_path):
    assert verdict("report.status", task, project_dir=tmp_path)["verdict"] == "not_evaluated"


def test_c3_report_status_fails_on_an_unreadable_report_or_a_job_that_did_not_finish(tmp_path):
    assert verdict("report.status", report_task(), project_dir=tmp_path)["verdict"] == "not_evaluated"
    assert verdict("report.status", report_task(), project_dir=None)["verdict"] == "not_evaluated"
    for outcome in ("aborted", "failed", "lost"):
        assert verdict("report.status", report_task(outcome), project_dir=tmp_path)["verdict"] == "fail"
    save_report(tmp_path)
    assert verdict("report.status", report_task(), project_dir=tmp_path)["verdict"] == "pass"


def test_c3_report_unsupported_listed_fails_closed(tmp_path, monkeypatch):
    task = report_task()
    assert verdict("report.unsupported_listed", task, project_dir=tmp_path)["verdict"] == "not_evaluated"
    save_report(tmp_path, report_id="r1", gaps_table=False)
    assert verdict("report.unsupported_listed", task, project_dir=tmp_path)["verdict"] == "not_evaluated"
    save_report(tmp_path, report_id="r2", harness=False)
    assert verdict("report.unsupported_listed", report_task(report_id="r2"),
                   project_dir=tmp_path)["verdict"] == "not_evaluated"
    # The report lists only `sizing`; a profile asking for more chapters fails.
    save_report(tmp_path, report_id="r3", chapters=("sizing",))
    assert verdict("report.unsupported_listed", report_task(report_id="r3"),
                   project_dir=tmp_path)["verdict"] == "fail"
    profile = run_profiles.resolve("eh_site@1")
    monkeypatch.setattr(run_profiles, "resolve", lambda ref: {**profile, "chapters": ["sizing"]})
    assert verdict("report.unsupported_listed", report_task(report_id="r3"),
                   project_dir=tmp_path)["verdict"] == "pass"


def test_c3_handoff_kpis_read_fails_closed(monkeypatch):
    class Ctx:
        project_uuid = "p"
    reads = {"rows": [{"id": "total_cost", "value": 5.0, "status": "read"},
                      {"id": "emissions", "value": 1.0, "status": "read"}]}
    monkeypatch.setattr(run_gates, "_on", lambda ctx, fn, *args: fn(*args))
    monkeypatch.setattr(run_handoff, "read_kpis", lambda profile: [dict(r) for r in reads["rows"]])
    task = {"run": {"profile": "eh_site@1"}, "steps": []}
    assert verdict("handoff.kpis_read", task, ctx=Ctx())["verdict"] == "not_evaluated"  # no hand-off record
    assert verdict("handoff.kpis_read", task, ctx=None)["verdict"] == "not_evaluated"  # no context
    task["run"]["handoff"] = {"kpis": [dict(r) for r in reads["rows"]]}
    assert verdict("handoff.kpis_read", task, ctx=Ctx())["verdict"] == "pass"
    task["run"]["handoff"]["kpis"][0]["value"] = 7.0  # a number that is not the native read
    assert verdict("handoff.kpis_read", task, ctx=Ctx())["verdict"] == "fail"
    task["run"]["handoff"]["kpis"][0]["value"] = 5.0
    reads["rows"][1] = {"id": "emissions", "value": None, "status": "unreadable"}
    assert verdict("handoff.kpis_read", task, ctx=Ctx())["verdict"] == "fail"

    def boom(profile):
        raise RuntimeError("reader down")
    monkeypatch.setattr(run_handoff, "read_kpis", boom)
    assert verdict("handoff.kpis_read", task, ctx=Ctx())["verdict"] == "not_evaluated"


# ── C14: KPI values come from the native reader only ─────────────────────────


def test_c14_kpis_come_from_the_retained_results_reader(monkeypatch):
    from services import retained_results, workflow_tools
    monkeypatch.setattr(workflow_tools, "solve_freshness", lambda *a: {"results_current": True})
    captures = {"results_summary": {"cost_breakdown": {"total": 123.5}},
                "results_emissions": {"emissions": {"total_tCO2": "12"}}}

    class Adapter:
        def __init__(self, section):
            self.capture = lambda token: captures[section]
    monkeypatch.setattr(retained_results, "adapter", Adapter)
    rows = run_handoff.read_kpis(run_profiles.resolve("eh_site@1"))
    assert [(r["id"], r["value"], r["unit"], r["status"]) for r in rows] == [
        ("total_cost", 123.5, "EUR", "read"), ("emissions", None, "tCO2", "unreadable")]
    assert rows[0]["source"] == "services.retained_results.results_summary:cost_breakdown.total"
    monkeypatch.setattr(workflow_tools, "solve_freshness", lambda *a: {"results_current": False})
    assert {r["status"] for r in run_handoff.read_kpis(run_profiles.resolve("eh_site@1"))} == {"not_current"}


# ── C13: coverage from a receipt or a passing gate only ─────────────────────


def coverage_task(case_status=None, gate_passed=True):
    run = {"profile": "eh_site@1", "cases": [],
           "stages": {"result_gate": {"status": "passed" if gate_passed else "failed",
                                      "gate": {"passed": gate_passed}}}}
    if case_status:
        run["cases"].append({"name": "Bigger BESS", "status": case_status})
    return {"brief": {**BRIEF, "cases": [{"name": "Bigger BESS", "experiment": None}]}, "run": run,
            "steps": []}


@pytest.mark.parametrize("case_status, expected", [("passed", "covered"), ("failed", "partly"),
                                                   ("not_runnable", "partly"), ("running", "gap"), (None, "gap")])
def test_c13_a_case_is_covered_only_by_its_passing_gate(case_status, expected):
    rows = {r["part"]: r["status"] for r in run_handoff.coverage(coverage_task(case_status), {}, None)}
    assert rows == {"Case Bigger BESS": expected, "Stated targets": "covered"}
    rows = {r["part"]: r["status"] for r in run_handoff.coverage(coverage_task(gate_passed=False), {}, None)}
    assert rows["Stated targets"] == "gap"


def test_c13_no_receipt_or_an_unreadable_one_is_a_gap_never_covered(tmp_path, monkeypatch):
    profile = {"chapters": ["sizing"]}
    task = {**coverage_task("passed"), "steps": []}
    rows = {r["part"]: r["status"] for r in run_handoff.coverage(task, profile, tmp_path)}
    assert rows["Report"] == "gap" and rows["Chapter sizing"] == "gap"  # no report step: no receipt

    def unreadable(*a, **k):
        raise OSError("the receipt cannot be read")
    monkeypatch.setattr(run_report, "status", unreadable)
    monkeypatch.setattr(run_handoff, "_case", unreadable)
    monkeypatch.setattr(run_handoff, "_targets", unreadable)
    rows = run_handoff.coverage({**task, "steps": report_task()["steps"]}, profile, tmp_path)
    assert {r["status"] for r in rows} == {"gap"} and len(rows) == 4
    # A read that answers no known status is a gap too.
    monkeypatch.setattr(run_handoff, "_targets", lambda run: ("probably", "the model said so"))
    assert run_handoff.coverage(coverage_task(), {}, None)[-1]["status"] == "gap"


def test_c13_the_completion_notice_carries_the_gap_count_and_one_email(detached, site, fake_report, monkeypatch):
    """C12: `_notify` twice after completion is still one notice and one
    email; the notice carries only the count of uncovered parts (HH-09A)."""
    sent = mail(monkeypatch)
    _, root = site
    task = detached()
    path = task_path(root, task)
    opt_in(path, task["task_id"])
    record = settle(harness_runner.Runner("a", decisions=Clicks()), path, task["task_id"],
                    lambda r: r["run"]["status"] == "completed")
    harness_runner._notify(path)
    harness_runner._notify(path)
    completed = [r for r in run_records(record) if r["native_status"] == "completed"]
    assert len(completed) == 1 and [m["metadata"]["state"] for m in sent].count("completed") == 1
    gaps = run_handoff.gaps(json.loads(path.read_text()))
    assert gaps == 3  # the three profile chapters this run did not establish
    assert completed[0]["gaps"] == gaps and "3 parts of the request not covered" in sent[-1]["body_text"]


# ── C7: the revert preview ───────────────────────────────────────────────────


def ledger_run():
    def row(id_, component, name, attr, old, new, **kw):
        return {"id": id_, "stage": "result_gate", "target": {"component": component, "name": name, "attr": attr},
                "old": old, "new": new, "unit": "MW", "reverted": False, **kw}
    return {"ledger": [
        row("chg-1", "Generator", "G1", "p_nom_max", 10.0, 15.0),
        row("chg-2", "Generator", "G1", "p_nom_max", 15.0, 20.0),
        row("chg-3", "Settings", "Weather", "year", None, 2023, stage="inputs"),
        row("chg-4", "solver_config", "", "reserve_margin", 0.1, 0.2),
        row("chg-5", "Generator", "G1", "p_nom_max", 1.0, 2.0, case="fork-a"),
        row("chg-6", "Generator", "G1", "marginal_cost", 3.0, 4.0, reverted=True),
    ]}


def test_c7_the_preview_and_by_hand_rows_are_exactly_the_runs_own_open_rows():
    run = ledger_run()
    previewable, by_hand = run_handoff.revert_rows(run)
    open_rows = {r["id"] for r in run["ledger"] if not r["reverted"] and not r.get("case")}
    assert {r["id"] for r in previewable} | {r["id"] for r in by_hand} == open_rows
    assert not {r["id"] for r in previewable} & {r["id"] for r in by_hand}
    assert {r["id"] for r in previewable} == {"chg-1", "chg-2"} and "chg-5" not in open_rows


def test_c7_one_lc16_preview_restores_the_first_value_and_goes_stale_on_a_change(session, site):
    from services import assistant_tools
    from services.pypsa_service import PyPSAService
    PyPSAService.get_network().generators.at["G1", "p_nom_max"] = 20.0
    revert = run_handoff.revert_preview(ledger_run())
    assert revert["rows"] == ["chg-1", "chg-2"] and revert["by_hand"] == ["chg-3", "chg-4"]
    out = assistant_tools.preview_confirmation(revert["preview_id"])
    assert out["preview_id"] == revert["preview_id"]
    rows, _binding, _sets = assistant_tools.preview_card(revert["preview_id"])
    assert any(r.get("new") in (10.0, "10", "10.0") or "10" in json.dumps(r) for r in rows)
    # The project changes after the hand-off: LC-16's fingerprint check refuses the preview.
    PyPSAService.get_network().generators.at["G1", "p_nom_max"] = 25.0
    with pytest.raises(HTTPException) as exc:
        assistant_tools.preview_confirmation(revert["preview_id"])
    assert exc.value.detail["error_kind"] == "preview_stale"


def test_c7_a_preview_lc16_refuses_leaves_every_row_by_hand(monkeypatch):
    from services import assistant_tools

    def refuse(changes):
        raise HTTPException(409, {"error_kind": "project_busy"})
    monkeypatch.setattr(assistant_tools, "preview_project_changes", refuse)
    revert = run_handoff.revert_preview(ledger_run())
    assert revert == {"rows": [], "by_hand": ["chg-1", "chg-2", "chg-3", "chg-4"], "preview_id": None,
                      "error": "project_busy"}


def test_native_steps_are_never_claimed_by_a_chat_call():
    profile = run_profiles.resolve("eh_site@1")
    steps = run_profiles.materialise(profile, "report", "p") + run_profiles.materialise(profile, "handoff", "p")
    assert [s["native"] for s in steps] == ["report", "handoff"]
    assert tasks._carded(steps[0]) is True and tasks._carded(steps[1]) is False
    bad = {**run_profiles.EH_SITE, "stages": {**run_profiles.EH_SITE["stages"],
                                              "handoff": {"actions": [{"tool": "generate_report", "args": {},
                                                                       "native": "handoff"}], "rules": []}}}
    with pytest.raises(run_profiles.ProfileError, match="native"):
        run_profiles.load(bad)

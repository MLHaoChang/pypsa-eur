"""LC-17: sensitivities by conversation. One sweep card (names, base → case
rows, solves), the unsaved-baseline and name-conflict refusals, derived names,
one continuation per set of jobs with each case's status (OJ-27, OJ-28) and
the Scenarios highlight. Real project and real solve queue; no paid calls."""
# ruff: noqa: F811 -- imported pytest fixtures
import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from harness import continuation, sweep_card
from harness.providers.fake import FakeProvider
from services import assistant_tasks as tasks, chat_tools
from services.solve_queue import solve_queue
from tests.test_assistant_tasks_study_brief import session, stored, sweep_case  # noqa: F401
from tests.test_openai_comprehensive_live import setup_profile, turn
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


def refused(kind, call, *args):
    with pytest.raises(HTTPException) as failure:
        call(*args)
    assert failure.value.detail["error_kind"] == kind, failure.value.detail
    return failure.value.detail


def sweep_step(session, project_id, request_id, cases):
    """The skill's plan: one sweep step, no brief (a brief admits two cases only)."""
    args = {"baseline_project_id": project_id, "cases": cases}
    task = chat_tools.start_task(title="Run the sensitivities", request_id=request_id,
                                 steps=[{"id": "sweep", "tool": "run_sensitivity_sweep", "args": args}])
    ticket = tasks.begin_step(session, "run_sensitivity_sweep", args)
    submitted = chat_tools.run_sensitivity_sweep(project_id, cases)
    path = tasks.await_job(ticket, None, session.session_id, jobs=continuation.queue_jobs(submitted))
    assert path is not None
    for case in submitted["cases"]:
        assert chat_tools.wait_for_job(case["job"]["id"], 25)["completed"]
    return task, ticket, path, submitted


def unnamed(value=20):
    return {"changes": [{"component_class": "Generator", "names": ["G1"], "attribute": "marginal_cost", "value": value}]}


def test_case_names_derive_from_the_changes():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=1, freq="h"))
    n.add("Bus", "B1")
    n.add("Line", "L1", bus0="B1", bus1="B1", x=0.1, s_nom=300)
    n.add("Line", "L2", bus0="B1", bus1="B1", x=0.1, s_nom=300)
    n.add("Store", "Battery", bus="B1", capital_cost=100.0)
    n.add("Store", "H2 tank", bus="B1", e_nom_extendable=True)
    n.add("Generator", "Gas", bus="B1", marginal_cost=60.0)
    changes = [{"component_class": "Store", "names": ["Battery"], "attribute": "capital_cost", "value": 70.0},
               {"component_class": "Line", "names": ["L1", "L2"], "attribute": "s_nom", "value": 500}]
    assert sweep_card.case_name(changes, n) == "battery-capex-70pct-lines-500"
    assert sweep_card.case_name([{"component_class": "Generator", "names": ["Gas"], "attribute": "marginal_cost",
                                  "value": 90}], n) == "gas-mc-150pct"
    assert sweep_card.case_name([{"component_class": "Store", "names": ["H2 tank"], "attribute": "e_nom_extendable",
                                  "value": False}], n) == "h2-tank-e-nom-extendable-off"
    assert len(sweep_card.case_name(changes * 10, n)) <= 64


def test_card_shows_every_row_from_the_saved_baseline_and_the_solves_it_queues(baseline):
    _, project_id, _ = baseline
    card = sweep_card.preview({"baseline_project_id": project_id,
                               "cases": [unnamed(20), sweep_case("Named case", 40)]})
    first, second = card["cases"]
    assert first["name"] == "g1-mc-200pct" and first["derived"] and not first["existing"]
    assert second["name"] == "Named case" and not second["derived"]
    assert first["rows"] == [{"component_class": "Generator", "name": "G1", "field": "marginal_cost",
                              "label": first["rows"][0]["label"], "base": 10.0, "case": 20,
                              "unit": first["rows"][0]["unit"]}]
    assert card["solves"] == 2 and card["says"].endswith("queues 2 solves")
    assert sweep_card.preview({"baseline_project_id": project_id,
                               "cases": [unnamed(20), sweep_case("Named case", 40)]})["digest"] == card["digest"]
    refused("unknown_component", sweep_card.preview, {"baseline_project_id": project_id, "cases": [
        {"changes": [{"component_class": "Generator", "names": ["G9"], "attribute": "p_nom", "value": 1}]}]})


def test_unsaved_baseline_refuses_before_any_card_and_saves_nothing(baseline):
    ctx, project_id, path = baseline
    before = (path / "network.nc").read_bytes()
    ctx.results_unsaved = True
    detail = refused("baseline_not_saved", sweep_card.preview, {"baseline_project_id": project_id, "cases": [unnamed()]})
    assert "save_project" in detail["message"]
    assert (path / "network.nc").read_bytes() == before and ctx.results_unsaved


def test_unnamed_case_runs_under_its_card_name_and_a_conflict_asks(baseline):
    _, project_id, _ = baseline
    result = chat_tools.run_sensitivity_sweep(project_id, [unnamed(20)])
    assert result["status"] == "submitted" and result["cases"][0]["name"] == "g1-mc-200pct"
    assert chat_tools.wait_for_job(result["cases"][0]["job"]["id"], 25)["completed"]
    again = sweep_card.preview({"baseline_project_id": project_id, "cases": [unnamed(20)]})
    assert again["cases"][0]["existing"] and again["solves"] == 0  # solved and unchanged: reused
    detail = refused("sensitivity_name_conflict", sweep_card.preview,
                     {"baseline_project_id": project_id, "cases": [sweep_case("g1-mc-200pct", 30)]})
    assert "g1-mc-200pct-2" in detail["message"]


def test_confirmation_carries_the_sweep_card_and_denial_creates_nothing(baseline, monkeypatch):
    _, project_id, _ = baseline
    session = setup_profile(monkeypatch, {"run_sensitivity_sweep"})
    fake = FakeProvider([
        {"blocks": [{"type": "tool_use", "id": "sweep", "name": "run_sensitivity_sweep",
                     "input": {"baseline_project_id": project_id, "cases": [unnamed(20)]}}]},
        {"blocks": [{"type": "text", "text": "Declined."}]},
    ])
    frames = turn(session, "Run a sensitivity with G1 at 20", fake, decision="deny")
    pending = [payload for event, payload in frames if event == "tool_pending_confirmation"]
    assert pending and pending[0]["sweep_card"]["cases"][0]["name"] == "g1-mc-200pct"
    assert pending[0]["sweep_card"]["solves"] == 1
    assert not any(p["name"] == "g1-mc-200pct" for p in chat_tools.list_projects())


def test_oj27_one_case_one_job_one_continuation_and_no_second_solve(session, baseline):
    from services import project_registry, workflow_tools as wt
    _, project_id, root = baseline
    before = (root / "network.nc").read_bytes()
    task, ticket, path, submitted = sweep_step(session, project_id, "oj27", [sweep_case()])
    assert len(solve_queue.list_jobs()) == 1
    # LC-D19: the case holds exactly its change; the saved baseline is untouched.
    assert (root / "network.nc").read_bytes() == before
    with chat_tools._acting() as (db, user):
        child = wt._load_context(project_registry.resolve_project(db, user, submitted["cases"][0]["project_id"]))
        base = wt._load_context(project_registry.resolve_project(db, user, project_id))
    diff = (child.network.generators[["marginal_cost", "p_nom"]] != base.network.generators[["marginal_cost", "p_nom"]])
    assert diff.to_dict() == {"marginal_cost": {"G1": True}, "p_nom": {"G1": False}}
    continuation.settle_queue_from_record(path, ticket)
    due = tasks.due_continuations(session)
    assert len(due) == 1 and due[0]["message"].endswith("; Plan Sensitivity: completed")
    tasks.claim_continuation(session, due[0]["job_id"])
    assert tasks.due_continuations(session) == []
    # The same request again (a restart, a repeated "run it") reuses the solve.
    again = chat_tools.run_sensitivity_sweep(project_id, [sweep_case()])
    assert again["cases"][0]["reused"] and len(solve_queue.list_jobs()) == 1


def test_oj28_three_cases_one_failed_reports_every_case_once(session, baseline, monkeypatch):
    _, project_id, root = baseline
    cases = [sweep_case("Case A", 20), sweep_case("Case B", 30), sweep_case("Case C", 40)]
    task, ticket, path, submitted = sweep_step(session, project_id, "oj28", cases)
    assert len(solve_queue.list_jobs()) == 3
    failed = submitted["cases"][1]["job"]["id"]
    real = continuation._queue_job_status
    monkeypatch.setattr(continuation, "_queue_job_status", lambda job: "failed" if job["job_id"] == failed else real(job))
    monkeypatch.setattr(continuation, "_queue_job_error", lambda job: "Solver reported infeasible")
    continuation.settle_queue_from_record(path, ticket)
    due = tasks.due_continuations(session)
    assert len(due) == 1
    assert due[0]["message"].endswith("; Case A: completed; Case B: failed; Case C: completed")
    view = chat_tools.get_task(task["task_id"])
    reported = view["steps"][0]["cases"]
    assert [(c["name"], c["status"]) for c in reported] == [("Case A", "completed"), ("Case B", "failed"),
                                                            ("Case C", "completed")]
    assert reported[1]["error"] == "Solver reported infeasible" and "objective" not in reported[1]
    assert stored(root, task["task_id"])["steps"][0]["status"] == "uncertain"


def test_scenarios_panel_highlights_only_projects_the_caller_can_open(baseline):
    _, project_id, _ = baseline
    event = chat_tools.ui_open_panel("scenarios", highlight_projects=[project_id])
    assert event["highlight_projects"] == [project_id] and event["panel_id"] == "scenarios"
    with pytest.raises(HTTPException) as bad:
        chat_tools.ui_open_panel("results", highlight_projects=[project_id])
    assert bad.value.detail["field"] == "highlight_projects"
    with pytest.raises(HTTPException):
        chat_tools.ui_open_panel("scenarios", highlight_projects=["00000000-0000-0000-0000-000000000000"])


def test_a_card_whose_saved_baseline_moved_refuses_before_the_sweep(baseline):
    from services import project_registry, workflow_tools as wt
    _, project_id, path = baseline
    args = {"baseline_project_id": project_id, "cases": [unnamed(20)]}
    card = sweep_card.preview(args)
    sweep_card.require_current(args, card)
    with chat_tools._acting() as (db, user):
        saved = wt._load_context(project_registry.resolve_project(db, user, project_id))
    saved.network.generators.at["G1", "marginal_cost"] = 12.0  # saved elsewhere after the card
    saved.network.export_to_netcdf(path / "network.nc")
    refused("sweep_preview_stale", sweep_card.require_current, args, card)


@pytest.mark.parametrize("case", [
    {"changes": [{"component_class": "Generator", "names": [], "attribute": "marginal_cost", "value": 1}]},
    {"changes": [{"component_class": "Bus", "names": ["B1"], "attribute": "v_nom", "value": 1}]},
    {"changes": [{"component_class": "Generator", "names": ["G1"], "attribute": "marginal_cost"}]},
    {"changes": ["not a change"]},
    {"changes": []},
    {"new_name": 5, "changes": unnamed()["changes"]},
    "not a case",
])
def test_a_malformed_case_refuses_by_kind_at_preview_and_at_execution(baseline, case):
    _, project_id, _ = baseline
    refused("invalid_sensitivity_cases", sweep_card.preview, {"baseline_project_id": project_id, "cases": [case]})
    refused("invalid_sensitivity_cases", chat_tools.run_sensitivity_sweep, project_id, [case])


def test_several_names_of_any_class_name_the_class():
    n = pypsa.Network()
    n.add("Bus", "B1")
    n.add("Generator", ["G1", "G2"], bus="B1", marginal_cost=10.0)
    assert sweep_card.case_name([{"component_class": "Generator", "names": ["G1", "G2"],
                                  "attribute": "marginal_cost", "value": 20}], n) == "generators-mc-200pct"
    assert sweep_card._PLURAL.get("ShuntImpedance", sweep_card._slug("ShuntImpedance") + "s") == "shuntimpedances"

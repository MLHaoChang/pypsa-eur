"""LC-06 S10, LC-D19: two independently solved experiment cases and their signed deltas.

The seam is the copilot's experiment tools: ``start_experiment`` and
``prepare_experiment`` (each case's only change is its override path,
``Generator.G1.marginal_cost``), ``advance_experiment`` and the approved call
it arms (a real queued HiGHS solve per case), ``refresh_experiment``,
``compare_experiments`` and ``export_experiment_comparison``, whose download is
read back. ``export_run_evidence`` gives each case's own recorded objective.

The baseline is the LC-D19 single-case network (one bus, G1 20 MW, L1 5 MW)
with each of its two snapshots weighted 3 h, so a weighting bug cannot pass:
case low = 20 EUR/MWh · 5 MW · (3 + 3) h = 600 EUR, case high = 30 · 5 · 6
= 900 EUR, Δ = +300 EUR, +50 %. A unit weighting would give 200/300/100.
Tolerance: ``rel=1e-9``; the LP optimum is a sum of six products the solver
returns to its own precision, and the mutations move it by 100 EUR or more.
"""
# ruff: noqa: F811 -- shared public harness fixtures
import pandas as pd
import pypsa
import pytest

from harness import experiment_execution as engine
from harness.session import ChatSession
from services import chat_tools, project_registry
from services.pypsa_service import PyPSAService
from services.solve_queue import solve_queue
from tests.test_experiment_comparison import completed_case
from tests.test_experiment_case_advancement import tool
from tests.test_workflow_tools import helpers_state  # noqa: F401
from tests.test_answer_assurance import checked  # noqa: F401

W, LOAD = 3.0, 5.0
REL = 1e-9


def hand(cost):
    return cost * LOAD * 2 * W


@pytest.fixture
def weighted(client, session_ctx, install_network):
    """``test_workflow_tools.baseline`` with each snapshot weighted 3 h."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=2, freq="h"))
    n.snapshot_weightings.loc[:, :] = W
    n.add("Bus", "B1", v_nom=110)
    n.add("Generator", "G1", bus="B1", p_nom=20, marginal_cost=10)
    n.add("Load", "L1", bus="B1", p_set=LOAD)
    install_network(n)
    response = client.post("/api/projects/Tool Baseline")
    assert response.status_code == 200, response.text
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    try:
        with chat_tools._acting() as (db, user):
            project = project_registry.resolve_project(db, user, "Tool Baseline")
            path = project_registry.project_dir(project)
        yield ctx, str(project.id), path
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def _recorded(client, result, project):
    """The objective the case's own solve recorded: its run evidence, exported from
    the case project (where the run lives) and downloaded."""
    session = ChatSession()
    chat_tools.set_chat_session(session)
    engine.invoke(session, "activate_project", {"project_id": result["case_project_id"]},
                  chat_tools.activate_project)
    try:
        exported = chat_tools.export_run_evidence(run_id=result["run_id"])
        response = client.get(chat_tools.get_file_delivery(exported["file_id"])["download_url"])
    finally:
        engine.invoke(session, "activate_project", {"project_id": project}, chat_tools.activate_project)
    assert response.status_code == 200, response.text
    return response.json()


def test_two_weighted_cases_compare_their_own_solved_objectives(weighted, client):
    _ctx, project, root = weighted
    before = (root / "network.nc").read_bytes()
    low, low_result = completed_case(20, "low")
    high, high_result = completed_case(30, "high")

    # Each case's objective is the one its own solve recorded.
    for result, cost in ((low_result, 20), (high_result, 30)):
        assert result["objective"] == pytest.approx(hand(cost), rel=REL)
        evidence = _recorded(client, result, project)
        assert evidence["run_id"] == result["run_id"]
        assert evidence["metrics"]["objective"] == {"value": pytest.approx(hand(cost), rel=REL), "unit": "EUR"}
    assert low_result["run_id"] != high_result["run_id"]
    assert low_result["job_id"] != high_result["job_id"]

    report = tool("compare_experiments", experiment_id=high, baseline_experiment_id=low)
    assert report["comparable"] and report["status"] == "completed"
    obj = report["deltas"]["objective"]
    assert obj["case"]["value"] == pytest.approx(hand(30), rel=REL)               # 900
    assert obj["baseline_case"]["value"] == pytest.approx(hand(20), rel=REL)      # 600
    assert obj["delta"] == {"value": pytest.approx(hand(30) - hand(20), rel=REL), "unit": "EUR"}
    assert obj["delta_pct"] == pytest.approx(50.0, rel=REL)
    # Mutations: a unit weighting gives 300 - 200 = 100; the baseline's objective read
    # for both cases gives 0. Both miss 300 by 100 EUR or more.
    assert abs(obj["delta"]["value"] - 100.0) >= 100.0 and obj["delta"]["value"] > 0
    assert report["case"]["job_id"] == high_result["job_id"]
    assert report["baseline_case"]["job_id"] == low_result["job_id"]
    assert report["project_id"] == project and report["inputs_differ"]

    # The export downloads exactly the inspected report.
    exported = tool("export_experiment_comparison", experiment_id=high, baseline_experiment_id=low,
                    comparison_sha256=report["comparison_sha256"])
    download = client.get(chat_tools.get_file_delivery(exported["file_id"])["download_url"])
    assert download.status_code == 200, download.text
    assert download.json() == report
    assert download.json()["deltas"]["objective"]["delta"]["value"] == pytest.approx(300.0, rel=REL)

    # Two solves, no other work, and the baseline untouched.
    assert len(solve_queue.list_jobs()) == 2
    assert (root / "network.nc").read_bytes() == before


def test_the_signed_delta_flips_with_the_order(weighted):
    low, _ = completed_case(20, "low")
    high, _ = completed_case(30, "high")
    report = tool("compare_experiments", experiment_id=low, baseline_experiment_id=high)
    obj = report["deltas"]["objective"]
    assert obj["delta"]["value"] == pytest.approx(hand(20) - hand(30), rel=REL)   # -300
    assert obj["delta_pct"] == pytest.approx(-100.0 / 3.0, rel=1e-6)              # -300/900, rounded by the report

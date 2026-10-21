"""Real authenticated harness and solved fixtures; only model output is scripted."""
# ruff: noqa: F811 -- shared pytest fixtures.
import os

import pandas as pd
import pytest

from harness.pilot_evaluation import CASES, GOLDEN_CASES, UNSAFE, LiveBudget, score
from services import chat_service, llm_config, workflow_tools
from tests.assurance_acceptance.support import Harness, parse_sse
from tests.test_assurance_metrics import NumericalProvider
from tests.test_workflow_tools import baseline  # noqa: F401


@pytest.fixture
def evaluation_project(baseline, request):
    case = request.node.callspec.params["case"]
    ctx = baseline[0]
    n = ctx.network
    n.generators.at["G1", "capital_cost"] = 3
    if case.profile == "bus-price/1":
        n.add("Generator", "G2", bus="B1", p_nom=20, marginal_cost=30)
        n.loads_t.p_set = pd.DataFrame({"L1": [5, 25]}, index=n.snapshots)
        n.snapshot_weightings.loc[:, "generators"] = [1, 3]
    elif case.profile.endswith("-emissions/1"):
        n.add("Carrier", "gas", co2_emissions=0.25)
        n.generators.at["G1", "carrier"] = "gas"
        n.generators.at["G1", "efficiency"] = 0.5
        n.loads_t.p_set = pd.DataFrame({"L1": [5, 7]}, index=n.snapshots)
        n.snapshot_weightings.loc[:, "generators"] = [2, 1]
        if case.profile == "network-emissions/1":
            n.add("Carrier", "clean", co2_emissions=0)
            n.add("Generator", "G2", bus="B1", p_nom=20, marginal_cost=50, carrier="clean")
            n.add("GlobalConstraint", "co2", type="primary_energy", carrier_attribute="co2_emissions",
                  sense="<=", constant=6)
    elif case.profile == "curtailment/1":
        n.generators_t.p_max_pu = pd.DataFrame({"G1": [1.0, 0.5]}, index=n.snapshots)
        n.loads_t.p_set = pd.DataFrame({"L1": [5, 7]}, index=n.snapshots)
        n.snapshot_weightings.loc[:, "generators"] = [2, 1]
    elif case.profile.startswith("lost-"):
        n.loads_t.p_set = pd.DataFrame({"L1": [5, 7]}, index=n.snapshots)
        n.snapshot_weightings.loc[:, "generators"] = [2, 3]
        n.snapshot_weightings.loc[:, "objective"] = [1, 2]
    elif case.profile.endswith("-loading/1"):
        n.set_snapshots(pd.date_range("2026-01-01", periods=3, freq="h"))
        n.loads_t.p_set = pd.DataFrame({"L1": [0, 0, 0]}, index=n.snapshots)
        n.add("Bus", "B2", v_nom=110)
        n.add("Load", "L2", bus="B2", p_set=[4, 9, 6])
        n.add("Generator", "G2", bus="B2", p_nom=20, marginal_cost=100)
        n.add("Line", "L12", bus0="B1", bus1="B2", s_nom=8, x=0.1, r=0.01)
        n.lines_t.s_max_pu = pd.DataFrame({"L12": [1, 1, 0.5]}, index=n.snapshots)
        n.snapshot_weightings.loc[:, "generators"] = [2, 1, 3]
    elif case.profile.startswith("carrier-") and case.fault != "battery":
        n.set_snapshots(pd.date_range("2026-01-01", periods=3, freq="h"))
        n.generators.at["G1", "carrier"] = "wind"
        n.add("Generator", "G2", bus="B1", p_nom=10, marginal_cost=5, carrier="wind")
        n.generators_t.p_max_pu = pd.DataFrame({"G2": [0.5, 0.5, 0.2]}, index=n.snapshots)
        n.loads_t.p_set = pd.DataFrame({"L1": [6, 8, 4]}, index=n.snapshots)
        n.snapshot_weightings.loc[:, "generators"] = [2, 1, 3]
    elif case.profile.startswith("storage-") or case.fault == "battery":
        n.set_snapshots(pd.date_range("2026-01-01", periods=3, freq="h"))
        n.loads_t.p_set = pd.DataFrame({"L1": [0, 2, 2]}, index=n.snapshots)
        n.generators_t.p_max_pu = pd.DataFrame({"G1": [1, 0, 0]}, index=n.snapshots)
        n.snapshot_weightings.loc[:, "stores"] = [2, 1, 1]
        n.snapshot_weightings.loc[:, "generators"] = [3, 4, 5]
        n.snapshot_weightings.loc[:, "objective"] = [1, 3, 1]
        n.add("StorageUnit", "Battery", bus="B1", p_nom=10, max_hours=2, carrier="battery",
              efficiency_store=0.8, efficiency_dispatch=0.5,
              state_of_charge_initial=0, cyclic_state_of_charge=False, capital_cost=3)
        n.storage_units_t.marginal_cost = pd.DataFrame({"Battery": [99, 2, 4]}, index=n.snapshots)
        n.storage_units_t.marginal_cost_storage = pd.DataFrame({"Battery": [0.5, 1, 7]}, index=n.snapshots)
    else:
        n.loads_t.p_set = pd.DataFrame({"L1": [5, 7]}, index=n.snapshots)
        n.snapshot_weightings.loc[:, "generators"] = [2, 1]
        n.snapshot_weightings.loc[:, "objective"] = [2, 1]
        if case.profile.startswith("network-"):
            n.generators.at["G1", "p_max_pu"] = 0.25
            n.add("Generator", "G2", bus="B1", p_nom=10, marginal_cost=20, capital_cost=5)
    assert n.optimize(solver_name="highs", include_objective_constant=False) == ("ok", "optimal")
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    if case.profile == "bus-price/1":
        ctx.solver_state["last_solve_settings"] = {"voll_eur_per_mwh": 1000.0}
    if case.profile.startswith("lost-"):
        # The VoLL capture a solve with slacks writes; hand-set, as n.optimize adds none.
        ctx.solver_state["last_lost_load"] = {
            "lost_load_t": pd.DataFrame({"L1": [0.0, 5.0]}, index=n.snapshots),
            "voll_eur_per_mwh": 1000.0,
        }
    return ctx


@pytest.fixture
def pilot_harness(client, monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "pilot")
    from harness.calculations import PROFILES

    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", ",".join(PROFILES))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_EFFECTS", "disabled")
    monkeypatch.setenv("PYPSA_GUI_ANSWER_REVIEW_MODE", "disabled")
    harness = Harness(client, monkeypatch)
    yield harness
    for sid in harness.session_ids:
        chat_service.drop_session(sid)


def run_case(case, ctx, harness, provider, node, mode):
    options = {"message": case.question}
    if case.fault not in ("golden", "basis"):
        scope = {"version": 1, "profile": case.profile, "project_id": ctx.project_uuid,
                 "entity_id": case.entity,
                 "retain": False, "metrics": list(case.expected) + list(case.unavailable)}
        if "cost" in case.profile:
            scope["cost_basis"] = {"currency": "EUR", "price_year": 2026,
                                   "annual_fixed_cost_fraction": 0.5 if case.profile.startswith("storage-") else 1}
        options["checked_scope"] = scope
    response = harness.request(provider, **options)
    assert response.status_code == 200
    frames = parse_sse(response.text)
    approvals = [data for event, data in frames if event == "answer_approved"]
    answer = {}
    if len(approvals) == 1:
        stored = harness.client.get("/api/chat/answers/" + approvals[0]["answer_id"])
        assert stored.status_code == 200
        answer = stored.json()
    result = score(case, frames, answer, project_id=ctx.project_uuid)
    result["mode"] = mode
    node.pilot_result = result
    assert not result["failures"], result["failures"]
    return result


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_pilot_case(case, evaluation_project, pilot_harness, request):
    n = evaluation_project.network
    storage = case.profile.startswith("storage-")
    if case.fault == "battery":
        su = n.storage_units_t
        su.p_store.iloc[1, 0], su.p_dispatch.iloc[1, 0], su.p.iloc[1, 0] = 5, 4, -1
    if case.fault == "stale":
        n.loads.at["L1", "p_set"] = 6
    elif case.fault == "missing" and case.profile == "bus-price/1":
        n.buses_t.marginal_price.loc[:, "B1"] = float("nan")
    elif case.fault == "missing" and case.profile.startswith("lost-"):
        evaluation_project.solver_state["last_lost_load"]["lost_load_t"].iloc[1, 0] = float("nan")
    elif case.fault == "missing" and case.profile.endswith("-loading/1"):
        n.lines_t.p0.loc[:, "L12"] = float("nan")
    elif case.fault == "missing":
        table = n.storage_units_t.p_dispatch if storage else n.generators_t.p
        table.loc[:, "Battery" if storage else "G1"] = float("nan")

    def corrupt(draft):
        if case.fault == "value":
            draft["blocks"][0]["value"] = 999
        elif case.fault == "unit":
            draft["blocks"][0]["unit"] = "invented_unit"
        elif case.fault == "formula":
            draft["blocks"][0]["formula_id"] = "invented/1"
        elif case.fault == "source":
            draft["blocks"][0]["source"]["evidence_id"] = "another-project-evidence"
        elif case.fault == "omit":
            draft["blocks"].pop()
        elif case.fault == "narrative":
            draft["narrative"] = UNSAFE

    provider = NumericalProvider({metric: value[0] for metric, value in case.expected.items()}, mutate=corrupt)
    # Invalid drafts still need plausible correct base values before mutation.
    if case.fault in ("value", "unit", "formula", "source", "omit", "narrative"):
        golden = next(c for c in GOLDEN_CASES if c.profile == case.profile)
        provider.values = {metric: value[0] for metric, value in golden.expected.items()}
    result = run_case(case, evaluation_project, pilot_harness, provider, request.node, "deterministic")
    if case.clarification:
        assert not provider.requests
    assert not result["unchecked_publication"]


@pytest.fixture(scope="session")
def live_meter():
    return LiveBudget()


@pytest.mark.skipif(os.environ.get("PYPSA_GUI_PILOT_EVALUATION_LIVE") != "1", reason="Explicit pilot-evaluation live opt-in required")
@pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda case: case.id)
def test_live_pilot_case(case, evaluation_project, pilot_harness, request, live_meter):
    from harness.providers.pilot_evaluation import bounded_provider
    key = os.environ.get("PYPSA_GUI_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    assert key, "Live credential unavailable"
    model = os.environ.get("PYPSA_GUI_PILOT_EVALUATION_MODEL", "gpt-6.1-sol")
    profile = llm_config.LLMProfile(id="pilot-evaluation", label="Disposable pilot evaluation",
        preset="openai", wire="openai", base_url=None, model=model, tools=False, vision=False,
        auth="bearer", fallback_model=None, max_output_tokens=LiveBudget.PER_REQUEST_OUTPUT)
    llm_config.save_profiles([profile], profile.id)
    with bounded_provider(key, live_meter) as provider:
        run_case(case, evaluation_project, pilot_harness, provider, request.node, "live")

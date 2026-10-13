"""Stress the real solved/public harness with independent weighted fixtures."""
# ruff: noqa: F811 -- shared pytest fixtures.
import pandas as pd
import pytest

from harness.reliability_cases import CASES, SCENARIOS, expected_values
from services import workflow_tools
from tests.test_assurance_metrics import NumericalProvider
from tests.test_pilot_evaluation import pilot_harness, run_case  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401


@pytest.fixture
def stress_project(baseline, request):
    case = request.node.callspec.params["case"]
    scenario = next(s for s in SCENARIOS if s.id == case.id.split(":")[1])
    ctx = baseline[0]
    n = ctx.network
    n.generators.at["G1", "capital_cost"] = scenario.fixed_price
    a, b = scenario.loads
    if case.profile.startswith("storage-"):
        n.set_snapshots(pd.date_range("2026-01-01", periods=3, freq="h"))
        n.loads_t.p_set = pd.DataFrame({"L1": [0, a, b]}, index=n.snapshots)
        n.generators_t.p_max_pu = pd.DataFrame({"G1": [1, 0, 0]}, index=n.snapshots)
        n.snapshot_weightings.loc[:, "stores"] = [scenario.charge_hours, 1, 1]
        n.snapshot_weightings.loc[:, "generators"] = [3, 4, 5]
        n.snapshot_weightings.loc[:, "objective"] = [1, *scenario.objective_weights]
        n.add("StorageUnit", "Battery", bus="B1", p_nom=20, max_hours=2,
              efficiency_store=scenario.storage_efficiencies[0],
              efficiency_dispatch=scenario.storage_efficiencies[1],
              state_of_charge_initial=0, cyclic_state_of_charge=False,
              capital_cost=scenario.fixed_price)
        n.storage_units_t.marginal_cost = pd.DataFrame({"Battery": [99, *scenario.prices]}, index=n.snapshots)
        n.storage_units_t.marginal_cost_storage = pd.DataFrame({"Battery": [0.5, 1, 7]}, index=n.snapshots)
    else:
        n.loads_t.p_set = pd.DataFrame({"L1": [a, b]}, index=n.snapshots)
        n.snapshot_weightings.loc[:, "generators"] = scenario.energy_weights
        n.snapshot_weightings.loc[:, "objective"] = scenario.objective_weights
        n.generators_t.marginal_cost = pd.DataFrame({"G1": scenario.prices}, index=n.snapshots)
        if case.profile.startswith("network-"):
            n.generators.at["G1", "p_max_pu"] = 0.2
            n.add("Generator", "G2", bus="B1", p_nom=10, marginal_cost=20, capital_cost=5)
    assert n.optimize(solver_name="highs", include_objective_constant=False) == ("ok", "optimal")
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    return ctx, scenario


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_stress_case(case, stress_project, pilot_harness, request):
    ctx, scenario = stress_project
    values = expected_values(scenario, case.profile)

    def corrupt(draft):
        if case.fault == "value":
            # Change even a legitimate zero. The repair is deliberately corrupt
            # as well; correctness is about published output, not first drafts.
            for block in draft["blocks"]:
                block["value"] += 1

    provider = NumericalProvider({metric: v[0] for metric, v in values.items()}, mutate=corrupt)
    run_case(case, ctx, pilot_harness, provider, request.node, "deterministic")

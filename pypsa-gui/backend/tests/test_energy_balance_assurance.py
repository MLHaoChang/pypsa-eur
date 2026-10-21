"""energy-balance/1 (checked-results S7): the Energy flow tab's totals.

Each fixture is checked against hand values and against the tab's own
`energy_flows` payload on the same solve, so a definition gap shows as a
failure, not a silent difference.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import pandas as pd
import pytest

from services import workflow_tools
from services.results.energy_flows import energy_flows
from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_workflow_tools import baseline  # noqa: F401

PROFILE = "energy-balance/1"


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", PROFILE)


def solve(ctx):
    n = ctx.network
    assert n.optimize(solver_name="highs", include_objective_constant=False) == ("ok", "optimal")
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    return ctx


def ask(checked, ctx, values):
    scope = {"version": 1, "profile": PROFILE, "project_id": ctx.project_uuid,
             "entity_id": "network", "retain": False}
    return checked.run(NumericalProvider(values), message="Check the energy balance.", checked_scope=scope)


def answered(checked, frames):
    record = approval(frames)
    response = checked.client.get("/api/chat/answers/" + record["answer_id"])
    assert response.status_code == 200, response.text
    return {b["metric"]: b["value"] for b in response.json()["blocks"]}


def profile_facts(ctx):
    """The profile's own rebuild, to compare with the tab before publishing it."""
    from harness import calculations

    scope = {"version": 1, "profile": PROFILE, "entity_id": "network"}
    facts, _ = calculations.scoped_calculate(calculations.scoped_source(ctx, scope), scope)
    return {f["metric"]: f["value"] for f in facts}


def tab(n):
    """The tab's totals: generation, load rows, Export + Losses rows, storage change."""
    p = energy_flows(n)
    assert p["status"] == "ok", p.get("flags")
    loads = {str(x) for x in n.loads.index}
    rows = p["rows"]
    return {
        "generation_energy": p["conservation"]["generation_mwh"],
        "served_energy": sum(r["mwh"] for r in rows if r["destination"] in loads),
        "storage_change": p["conservation"]["storage_delta_mwh"],
        "export_losses_and_conversion": sum(r["mwh"] for r in rows if r["destination"] not in loads),
        "unserved_product": sum(p["unserved_product_mwh"].values()),
    }


@pytest.fixture
def shortfall(baseline):
    """A 3 MW process unit cannot make the 5 MW delivery: 2 MW shortfall each hour."""
    ctx = baseline[0]
    n = ctx.network
    n.loads.at["L1", "p_set"] = 0
    n.add("Bus", "prod")
    n.add("Carrier", ["product_shortfall"])
    n.add("Link", "unit", bus0="B1", bus1="prod", p_nom=3, efficiency=1.0)
    n.add("Generator", "__lost_prod", bus="prod", carrier="product_shortfall", p_nom=100,
          marginal_cost=1e4, process_role="shortfall")
    n.add("Load", "delivery", bus="prod", p_set=5.0)
    return ctx


def test_mixed_carriers_are_split_beside_the_totals(shortfall, checked):
    shortfall.network.buses.at["prod", "carrier"] = "NH3"
    ctx = solve(shortfall)
    values = {"generation_energy": 6, "served_energy": 6, "unserved_energy": 4,
              "storage_change": 0, "export_losses_and_conversion": 0}
    text = public_text(ask(checked, ctx, values))
    assert ("These totals add MWh of different carriers. Generation: AC 6; served: NH3 6; "
            "unserved: NH3 4.") in text


def test_a_shortfall_comes_off_served_as_it_comes_off_generation(shortfall, checked):
    ctx = solve(shortfall)
    # G1 makes 3 MW for two unit-weight hours; the delivery is served 6 of 10 MWh.
    values = {"generation_energy": 6, "served_energy": 6, "unserved_energy": 4,
              "storage_change": 0, "export_losses_and_conversion": 0}
    frames = ask(checked, ctx, values)
    assert answered(checked, frames) == pytest.approx(values)
    expected = tab(ctx.network)
    assert expected["unserved_product"] == pytest.approx(4)
    for metric in ("generation_energy", "served_energy", "storage_change", "export_losses_and_conversion"):
        assert values[metric] == pytest.approx(expected[metric], abs=1e-6)
    text = public_text(frames)
    assert "split by source and route is its own attribution" in text
    assert "derived (generation minus served minus storage change), not separately measured" in text


@pytest.fixture
def exporting(baseline):
    """Cheap PV sells through a lossy tie to a grid that also buys."""
    ctx = baseline[0]
    n = ctx.network
    n.generators.at["G1", "marginal_cost"] = 0
    n.generators_t.p_max_pu = pd.DataFrame({"G1": [1.0, 0.0]}, index=n.snapshots)
    n.add("Bus", "grid")
    n.add("Carrier", ["grid"])
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=100, p_min_pu=-1,
          marginal_cost=20)
    n.add("Link", "tie", bus0="grid", bus1="B1", p_nom=6, p_min_pu=-1, efficiency=0.98)
    return ctx


def test_export_and_losses_equal_the_tabs_export_and_losses_rows(exporting, checked):
    ctx = solve(exporting)
    expected = tab(ctx.network)
    assert expected["export_losses_and_conversion"] > 0
    values = profile_facts(ctx)
    # The rebuild equals the tab's totals; only the summation order differs (~1e-15).
    for metric in ("generation_energy", "served_energy", "storage_change", "export_losses_and_conversion"):
        assert values[metric] == pytest.approx(expected[metric], abs=1e-9)
    assert values["unserved_energy"] == 0
    assert answered(checked, ask(checked, ctx, values)) == values


@pytest.fixture
def battery(baseline):
    """A battery shifts cheap first-hour energy into the second, at store weights 2 and 1."""
    ctx = baseline[0]
    n = ctx.network
    n.generators_t.marginal_cost = pd.DataFrame({"G1": [1, 100]}, index=n.snapshots)
    n.generators_t.p_max_pu = pd.DataFrame({"G1": [1.0, 0.0]}, index=n.snapshots)
    n.loads_t.p_set = pd.DataFrame({"L1": [0, 4]}, index=n.snapshots)
    n.add("StorageUnit", "Battery", bus="B1", p_nom=10, max_hours=2, efficiency_store=0.9,
          efficiency_dispatch=0.9, state_of_charge_initial=0, cyclic_state_of_charge=False)
    n.snapshot_weightings.loc[:, "stores"] = [2, 1]
    return ctx


def test_storage_change_converts_state_as_the_tab_does(battery, checked):
    ctx = solve(battery)
    expected = tab(ctx.network)
    values = profile_facts(ctx)
    assert expected["storage_change"] != 0
    for metric in ("generation_energy", "served_energy", "storage_change", "export_losses_and_conversion"):
        assert values[metric] == pytest.approx(expected[metric], abs=1e-9)
    assert answered(checked, ask(checked, ctx, values)) == values


def test_a_bus_that_does_not_balance_gives_no_figure(battery, checked):
    ctx = solve(battery)
    ctx.network.generators_t.p.iloc[0, 0] += 1  # one MW appears from nowhere
    frames = ask(checked, ctx, {})
    assert {s["reason"] for s in approval(frames)["receipt"]["result_slots"]} == {"balance_not_established"}


def test_a_commodity_bus_is_refused_as_the_tab_refuses_it(shortfall, checked):
    shortfall.network.buses.at["prod", "unit"] = "t/h"
    ctx = solve(shortfall)
    frames = ask(checked, ctx, {})
    assert {s["reason"] for s in approval(frames)["receipt"]["result_slots"]} == {"non_energy_bus"}


def test_storage_inflow_is_refused_as_the_tab_refuses_it(baseline, checked):
    # Inflow fills the state without touching a bus: a derived remainder would read -8 MWh.
    ctx = baseline[0]
    ctx.network.add("StorageUnit", "Hydro", bus="B1", p_nom=10, max_hours=4, inflow=4,
                    state_of_charge_initial=0, cyclic_state_of_charge=False)
    ctx = solve(ctx)
    assert energy_flows(ctx.network)["status"] != "ok"
    frames = ask(checked, ctx, {})
    assert {s["reason"] for s in approval(frames)["receipt"]["result_slots"]} == {"unsupported_storage_inflow"}


def test_a_state_off_its_own_dispatch_gives_no_figure(battery, checked):
    ctx = solve(battery)
    soc = ctx.network.storage_units_t.state_of_charge
    soc.iloc[:, 0] += 1  # each state 1 MWh above what its dispatch makes
    frames = ask(checked, ctx, {})
    assert {s["reason"] for s in approval(frames)["receipt"]["result_slots"]} == {"balance_not_established"}


def test_no_carrier_split_beside_withheld_totals(shortfall):
    from harness import calculations, energy_balance_calculations as balance

    shortfall.network.buses.at["prod", "carrier"] = "NH3"
    ctx = solve(shortfall)
    scope = {"version": 1, "profile": PROFILE, "entity_id": "network"}
    raw = calculations.scoped_source(ctx, scope)
    assert any("different carriers" in line for line in balance.notes(raw, scope, str))
    raw["w_store"] = [0.0] * len(raw["w_store"])  # a gate calculate applies, past the bus balance
    assert balance.calculate(raw, scope)[0] == []
    assert not any("different carriers" in line for line in balance.notes(raw, scope, str))

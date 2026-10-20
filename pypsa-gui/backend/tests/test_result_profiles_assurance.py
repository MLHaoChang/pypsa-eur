"""bus-price/1, generator/network-emissions/1 and curtailment/1 (checked-results S5).

Expected values are worked by hand from small LPs; the shadow price is the
LP's own dual on a binding cap, checked against the hand marginal.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import pandas as pd
import pytest

from harness import assurance_scope
from services import workflow_tools
from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_workflow_tools import baseline  # noqa: F401

PROFILES = ("bus-price/1", "generator-emissions/1", "network-emissions/1", "curtailment/1")


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", ",".join(PROFILES))


def solve(ctx, voll=None):
    n = ctx.network
    assert n.optimize(solver_name="highs", include_objective_constant=False) == ("ok", "optimal")
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    if voll is not None:
        ctx.solver_state["last_solve_settings"] = {"voll_eur_per_mwh": voll}
    return ctx


def ask(checked, ctx, profile, entity, values):
    scope = {"version": 1, "profile": profile, "project_id": ctx.project_uuid,
             "entity_id": entity, "retain": False}
    return checked.run(NumericalProvider(values), message="Check results.", checked_scope=scope)


def answered(checked, frames):
    record = approval(frames)
    response = checked.client.get("/api/chat/answers/" + record["answer_id"])
    assert response.status_code == 200, response.text
    return {b["metric"]: b["value"] for b in response.json()["blocks"]}


def slots(frames):
    return {s["metric"]: s for s in approval(frames)["receipt"]["result_slots"]}


# ── prices ────────────────────────────────────────────────────────────────


@pytest.fixture
def priced(baseline):
    """G1 (20 MW at 10) then G2 (at 30): prices 10 and 30 at generator weights 1 and 3."""
    ctx = baseline[0]
    n = ctx.network
    n.add("Generator", "G2", bus="B1", p_nom=20, marginal_cost=30)
    n.loads_t.p_set = pd.DataFrame({"L1": [5, 25]}, index=n.snapshots)
    n.snapshot_weightings.loc[:, "generators"] = [1, 3]
    return ctx


# mean (1*10 + 3*30)/4 = 25; min 10; max 30; no hour at VoLL 1000.
PRICE = {"mean_price": 25, "min_price": 10, "max_price": 30, "hours_at_voll": 0}


def test_bus_price_is_time_weighted_and_observed(priced, checked):
    ctx = solve(priced, voll=1000)
    assert list(ctx.network.buses_t.marginal_price.B1) == pytest.approx([10, 30])
    frames = ask(checked, ctx, "bus-price/1", "B1", PRICE)
    assert answered(checked, frames) == pytest.approx(PRICE)
    text = public_text(frames)
    assert "Bus B1" in text and "time-weighted with generator snapshot weights" in text


def test_hours_at_voll_use_the_solves_voll_and_need_one(priced, checked):
    ctx = solve(priced, voll=30)
    assert answered(checked, ask(checked, ctx, "bus-price/1", "B1", {**PRICE, "hours_at_voll": 3})) == (
        pytest.approx({**PRICE, "hours_at_voll": 3}))
    ctx.solver_state["last_solve_settings"] = None
    frames = ask(checked, ctx, "bus-price/1", "B1", {k: v for k, v in PRICE.items() if k != "hours_at_voll"})
    assert slots(frames)["hours_at_voll"]["reason"] == "voll_missing_or_zero"


# ── emissions ─────────────────────────────────────────────────────────────


@pytest.fixture
def gas(baseline):
    """G1 burns gas (0.25 tCO2/MWh_th) at efficiency 0.5 for loads 5 and 7 at weights 2 and 1."""
    ctx = baseline[0]
    n = ctx.network
    n.add("Carrier", "gas", co2_emissions=0.25)
    n.generators.at["G1", "carrier"] = "gas"
    n.generators.at["G1", "efficiency"] = 0.5
    n.loads_t.p_set = pd.DataFrame({"L1": [5, 7]}, index=n.snapshots)
    n.snapshot_weightings.loc[:, "generators"] = [2, 1]
    return ctx


def test_generator_emissions_use_dispatch_over_efficiency(gas, checked):
    ctx = solve(gas)
    # (2*5 + 1*7) / 0.5 * 0.25 = 8.5 tCO2.
    frames = ask(checked, ctx, "generator-emissions/1", "G1", {"generator_emissions": 8.5})
    assert answered(checked, frames) == pytest.approx({"generator_emissions": 8.5})


def test_network_without_a_cap_has_no_shadow_price(gas, checked):
    ctx = solve(gas)
    frames = ask(checked, ctx, "network-emissions/1", "network", {"total_emissions": 8.5})
    assert answered(checked, frames) == pytest.approx({"total_emissions": 8.5})
    assert slots(frames)["co2_cap_shadow_price"]["reason"] == "no_co2_cap"


def test_a_binding_cap_reports_the_dual_as_a_positive_price(gas, checked):
    n = gas.network
    n.add("Generator", "G2", bus="B1", p_nom=20, marginal_cost=50, carrier="clean")
    n.add("Carrier", "clean", co2_emissions=0)
    n.add("GlobalConstraint", "co2", type="primary_energy", carrier_attribute="co2_emissions",
          sense="<=", constant=6)
    ctx = solve(gas)
    # The cap weights emissions by generator weights (2, 1); cost uses objective
    # weights (1, 1). Displacing gas by clean (+40 EUR/MWh_el) saves 0.5 t x 2 = 1 t
    # per MWh in the first hour, 0.5 t in the second: the marginal ton costs 40 EUR.
    assert ctx.network.global_constraints.at["co2", "mu"] == pytest.approx(-40)
    values = {"total_emissions": 6, "co2_cap_shadow_price": 40}
    assert answered(checked, ask(checked, ctx, "network-emissions/1", "network", values)) == (
        pytest.approx(values))


def test_network_emissions_refuse_when_storage_carries_co2(gas, checked):
    gas.network.add("StorageUnit", "Tank", bus="B1", p_nom=0, carrier="gas")
    ctx = solve(gas)
    frames = ask(checked, ctx, "network-emissions/1", "network", {})
    assert {s["reason"] for s in approval(frames)["receipt"]["result_slots"]} == {"emissions_outside_scope"}
    frames = ask(checked, ctx, "generator-emissions/1", "G1", {"generator_emissions": 8.5})
    assert "This generator only; 1 Links or stores elsewhere carry CO2." in public_text(frames)


# ── curtailment ───────────────────────────────────────────────────────────


@pytest.fixture
def windy(baseline):
    """G1 (20 MW) available 1.0 then 0.5 for loads 5 and 7 at weights 2 and 1."""
    ctx = baseline[0]
    n = ctx.network
    n.generators_t.p_max_pu = pd.DataFrame({"G1": [1.0, 0.5]}, index=n.snapshots)
    n.loads_t.p_set = pd.DataFrame({"L1": [5, 7]}, index=n.snapshots)
    n.snapshot_weightings.loc[:, "generators"] = [2, 1]
    return ctx


# available 2*20 + 1*10 = 50; curtailed 2*15 + 1*3 = 33; share 66 %.
WIND = {"available_energy": 50, "curtailed_energy": 33, "curtailment_share": 66}


@pytest.mark.parametrize("entity", ["G1", "network"])
def test_curtailment_of_a_variable_generator(windy, checked, entity):
    ctx = solve(windy)
    assert answered(checked, ask(checked, ctx, "curtailment/1", entity, WIND)) == pytest.approx(WIND)


def test_a_dispatchable_unit_is_not_curtailed(windy, checked):
    windy.network.add("Generator", "G2", bus="B1", p_nom=5, marginal_cost=50)
    ctx = solve(windy)
    frames = ask(checked, ctx, "curtailment/1", "G2", {})
    assert {s["reason"] for s in approval(frames)["receipt"]["result_slots"]} == {"not_variable"}


def test_dispatch_above_availability_names_the_generator(windy, checked):
    ctx = solve(windy)
    ctx.network.generators_t.p.iloc[1, 0] = 11  # 1 MW above the 10 MW available
    frames = ask(checked, ctx, "curtailment/1", "network", {})
    assert {s["reason"] for s in approval(frames)["receipt"]["result_slots"]} == {"invalid_variable_generator"}
    assert "Unavailable for: G1." in public_text(frames)


# ── shared ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("profile,entity", [("bus-price/1", "B1"), ("generator-emissions/1", "G1"),
                                            ("curtailment/1", "G1")])
def test_every_profile_refuses_after_an_ac_power_flow(windy, checked, profile, entity):
    ctx = solve(windy, voll=1000)
    ctx.solver_state["ac_pf_results"] = {}
    frames = ask(checked, ctx, profile, entity, {})
    assert {s["reason"] for s in approval(frames)["receipt"]["result_slots"]} == {"unverified_solve"}
    ctx.solver_state["ac_pf_results"] = None


def test_grammar():
    def profile(message):
        scope = assurance_scope.from_message(message, "p", {})
        return scope and (scope["profile"], scope["entity_id"])

    assert profile("Show the price for bus B1") == ("bus-price/1", "B1")
    assert profile("Show the emissions for generator G1") == ("generator-emissions/1", "G1")
    assert profile("Show the emissions for network") == ("network-emissions/1", "network")
    assert profile("Show the curtailment for network") == ("curtailment/1", "network")
    assert profile("Show the price for network") is None

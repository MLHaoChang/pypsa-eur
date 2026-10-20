"""lost-load/1 through the real solve path and the checked stream (checked-results S4).

The VoLL slacks exist only during the solve, so these tests solve through
POST /api/simulation/run (as `test_solver_run_api.py` does) and read the
capture that solve wrote. Expected values are worked by hand.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import pandas as pd
import pytest

from harness import assurance_scope
from services.solver_service import SolverConfig
from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_workflow_tools import baseline  # noqa: F401

PROFILE = "lost-load/1"
# G1 (20 MW, 10 EUR/MWh) cannot meet L1's 25 MW in the second hour: 5 MW shed.
# Generator weights 2, 3: energy 3*5 = 15 MWh, hours 3 h; objective weights 1, 2:
# cost 2*5*1000 = 10000 EUR at VoLL 1000; peak 5 MW.
SHED = {"lost_load_energy": 15, "peak_shed": 5, "shed_hours": 3, "lost_load_cost": 10000}


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", PROFILE)


def solve(client, session_state, voll=1000.0):
    """One real solve through the API worker; returns the session solver state."""
    state = session_state(client)
    state["solver_config"] = SolverConfig(voll=voll)
    response = client.post("/api/simulation/run")
    assert response.status_code == 200, response.text
    state["thread"].join(timeout=90)
    assert state["status"] == "completed", state.get("condition")
    return state


@pytest.fixture
def short(baseline):
    ctx = baseline[0]
    n = ctx.network
    n.loads_t.p_set = pd.DataFrame({"L1": [5, 25]}, index=n.snapshots)
    n.snapshot_weightings.loc[:, "generators"] = [2, 3]
    n.snapshot_weightings.loc[:, "objective"] = [1, 2]
    return ctx


def scope(ctx, entity="L1", **options):
    return {"version": 1, "profile": PROFILE, "project_id": ctx.project_uuid,
            "entity_id": entity, "retain": False, **options}


def ask(checked, ctx, values, entity="L1"):
    return checked.run(NumericalProvider(values), message="Check lost load.",
                       checked_scope=scope(ctx, entity))


def answered(checked, frames):
    record = approval(frames)
    response = checked.client.get("/api/chat/answers/" + record["answer_id"])
    assert response.status_code == 200, response.text
    return {b["metric"]: b["value"] for b in response.json()["blocks"]}


def reasons(frames):
    receipt = approval(frames).get("receipt") or {}
    assert receipt.get("request_complete") is False
    return {s["reason"] for s in receipt["result_slots"]}


def test_lost_load_matches_the_hand_values_from_a_real_solve(short, checked, client, session_state):
    state = solve(client, session_state)
    assert list(state["last_lost_load"]["lost_load_t"]["L1"]) == pytest.approx([0, 5])
    frames = ask(checked, short, SHED)
    assert answered(checked, frames) == pytest.approx(SHED)
    text = public_text(frames)
    assert "Load L1" in text and "demand response is excluded" in text
    assert "not checked against demand" in text


def test_network_entity_sums_the_electrical_loads(short, checked, client, session_state):
    solve(client, session_state)
    frames = ask(checked, short, SHED, entity="network")
    assert answered(checked, frames) == pytest.approx(SHED)
    assert "1 Loads with a VoLL slack; shed hours count the 1 electrical Loads only" in public_text(frames)


def test_several_carriers_show_each_carriers_energy_beside_the_total(short, checked, client, session_state):
    n = short.network
    n.add("Bus", "H2", carrier="H2")
    n.add("Load", "Electrolyser", bus="H2", carrier="H2", p_set=[0, 4])
    state = solve(client, session_state)
    assert list(state["last_lost_load"]["lost_load_t"]["Electrolyser"]) == pytest.approx([0, 4])
    # AC 3*5 = 15 MWh, H2 3*4 = 12 MWh; total 27 MWh; summed peak 9 MW at hour two.
    values = {"lost_load_energy": 27, "peak_shed": 9, "shed_hours": 3, "lost_load_cost": 18000}
    frames = ask(checked, short, values, entity="network")
    assert answered(checked, frames) == pytest.approx(values)
    text = public_text(frames)
    assert "Energy by carrier: AC 15 MWh, H2 12 MWh" in text
    assert "Peak shed is the peak of the summed shed, all carriers." in text


def test_a_later_solve_replaces_the_capture_it_is_checked_against(short, checked, client, session_state):
    solve(client, session_state)
    # Re-solve with more demand: the capture and both fingerprints move together.
    short.network.loads_t.p_set = pd.DataFrame({"L1": [5, 27]}, index=short.network.snapshots)
    solve(client, session_state)
    values = {"lost_load_energy": 21, "peak_shed": 7, "shed_hours": 3, "lost_load_cost": 14000}
    assert answered(checked, ask(checked, short, values)) == pytest.approx(values)


def test_a_solve_without_voll_leaves_no_capture_to_check(short, checked, client, session_state):
    solve(client, session_state)
    short.network.loads_t.p_set = pd.DataFrame({"L1": [5, 15]}, index=short.network.snapshots)
    state = solve(client, session_state, voll=0.0)
    assert state["last_lost_load"] is None
    assert reasons(ask(checked, short, {})) == {"no_lost_load_capture"}


def test_an_input_edit_after_the_solve_is_unverified(short, checked, client, session_state):
    solve(client, session_state)
    short.network.generators.at["G1", "marginal_cost"] = 11
    assert reasons(ask(checked, short, {})) == {"unverified_solve"}


def test_missing_shed_or_voll_is_unavailable_never_partial(short, checked, client, session_state):
    state = solve(client, session_state)
    state["last_lost_load"]["voll_eur_per_mwh"] = None
    frames = ask(checked, short, {k: v for k, v in SHED.items() if k != "lost_load_cost"})
    receipt = approval(frames)["receipt"]
    cost = next(s for s in receipt["result_slots"] if s["metric"] == "lost_load_cost")
    assert cost["reason"] == "voll_missing_or_zero"
    state["last_lost_load"]["lost_load_t"].iloc[0, 0] = float("nan")
    assert reasons(ask(checked, short, {})) == {"invalid_or_missing_shed_data"}


def test_an_unknown_load_is_refused_by_name(short, checked, client, session_state):
    solve(client, session_state)
    assert reasons(ask(checked, short, {}, entity="Ghost")) == {"unknown_load"}


def test_a_bus_keyed_legacy_capture_is_refused(short, checked, client, session_state):
    state = solve(client, session_state)
    state["last_lost_load"]["lost_load_t"].columns = ["B1"]
    assert reasons(ask(checked, short, {}, entity="B1")) == {"legacy_lost_load_capture"}


def test_a_network_without_electrical_loads_is_refused_not_zero(short, checked, client, session_state,
                                                                monkeypatch):
    solve(client, session_state)
    monkeypatch.setattr("services.adequacy.metrics.electrical_columns", lambda n, cols: [])
    assert reasons(ask(checked, short, {}, entity="network")) == {"no_electrical_loads"}


def test_scope_and_grammar():
    assert assurance_scope.from_message("Show the lost load for load L1", "p", {})["entity_id"] == "L1"
    assert assurance_scope.from_message("Show lost load for network", "p", {})["entity_id"] == "network"
    with pytest.raises(ValueError, match="unsupported_lost_load_cost_scope"):
        assurance_scope.validate({"version": 1, "profile": PROFILE, "project_id": "p", "entity_id": "L1",
                                  "cost_basis": {"currency": "EUR", "price_year": 2026,
                                                 "annual_fixed_cost_fraction": 1}})

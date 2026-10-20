"""carrier-energy/1 through the authenticated checked stream (checked-results S2).

Expected values are worked by hand from the fixtures, never read back from
the profile under test.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import pandas as pd
import pytest

from harness import assurance_scope, rollout
from services import workflow_tools
from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_workflow_tools import baseline  # noqa: F401

PROFILE = "carrier-energy/1"
METRICS = ("carrier_generator_energy", "carrier_peak_generator_output",
           "carrier_storage_discharged_energy", "carrier_storage_charged_energy")


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", PROFILE)


def _solve(ctx):
    assert ctx.network.optimize(solver_name="highs") == ("ok", "optimal")
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(ctx.network, ctx.solver_state["solver_config"])
    return ctx


@pytest.fixture
def wind(baseline):
    """Two wind generators serve the only load: their sum is the load, however split."""
    ctx = baseline[0]
    n = ctx.network
    n.set_snapshots(pd.date_range("2026-01-01", periods=3, freq="h"))
    n.generators.at["G1", "carrier"] = "wind"
    n.add("Generator", "G2", bus="B1", p_nom=10, marginal_cost=5, carrier="wind")
    n.generators_t.p_max_pu = pd.DataFrame({"G2": [0.5, 0.5, 0.2]}, index=n.snapshots)
    n.loads_t.p_set = pd.DataFrame({"L1": [6, 8, 4]}, index=n.snapshots)
    n.snapshot_weightings.loc[:, "generators"] = [2, 1, 3]
    return ctx


# Energy 2*6 + 1*8 + 3*4 = 32 MWh; peak max(6, 8, 4) = 8 MW; no StorageUnits.
WIND = {"carrier_generator_energy": 32, "carrier_peak_generator_output": 8,
        "carrier_storage_discharged_energy": 0, "carrier_storage_charged_energy": 0}


@pytest.fixture
def battery(baseline):
    """The storage profile's fixture, with the battery on its own carrier."""
    ctx = baseline[0]
    n = ctx.network
    n.set_snapshots(pd.date_range("2026-01-01", periods=3, freq="h"))
    n.loads_t.p_set = pd.DataFrame({"L1": [0, 2, 2]}, index=n.snapshots)
    n.generators_t.p_max_pu = pd.DataFrame({"G1": [1, 0, 0]}, index=n.snapshots)
    n.snapshot_weightings.loc[:, "stores"] = [2, 1, 1]
    n.snapshot_weightings.loc[:, "generators"] = [3, 4, 5]
    n.add("StorageUnit", "Battery", bus="B1", p_nom=10, max_hours=2, carrier="battery",
          efficiency_store=0.8, efficiency_dispatch=0.5, state_of_charge_initial=0,
          cyclic_state_of_charge=False)
    _solve(ctx)
    assert list(ctx.network.storage_units_t.p_store.Battery) == pytest.approx([5, 0, 0])
    assert list(ctx.network.storage_units_t.p_dispatch.Battery) == pytest.approx([0, 2, 2])
    return ctx


# Store weights 2, 1, 1: charge 2*5 = 10 MWh; discharge 1*2 + 1*2 = 4 MWh.
BATTERY = {"carrier_generator_energy": 0, "carrier_peak_generator_output": 0,
           "carrier_storage_discharged_energy": 4, "carrier_storage_charged_energy": 10}


def scope(ctx, carrier, **options):
    return {"version": 1, "profile": PROFILE, "project_id": ctx.project_uuid,
            "entity_id": carrier, "retain": False, **options}


def run(checked, ctx, carrier, values, **options):
    frames = checked.run(NumericalProvider(values), message="Check carrier results.",
                         checked_scope=scope(ctx, carrier, **options))
    record = approval(frames)
    response = checked.client.get("/api/chat/answers/" + record["answer_id"])
    assert response.status_code == 200, response.text
    return frames, response.json()


def slots(answer):
    return {s["metric"]: s for s in answer["receipt"]["result_slots"]}


def test_wind_carrier_energy_matches_the_hand_sum(wind, checked):
    frames, answer = run(checked, _solve(wind), "wind", WIND)
    assert answer["receipt"]["request_complete"]
    assert {b["metric"]: b["value"] for b in answer["blocks"]} == pytest.approx(WIND)
    text = public_text(frames)
    assert "Carrier wind" in text
    assert "2 generators and 0 StorageUnits of this carrier" in text
    assert "0 Links and 0 Stores of this carrier excluded" in text
    assert "Link outputs and Stores are excluded" in text
    assert "weights differ" not in text


def test_whole_message_grammar_selects_the_carrier_profile(wind, checked):
    ctx = _solve(wind)
    frames = checked.run(NumericalProvider(WIND), message="Give me the key results for carrier wind")
    assert approval(frames)["verdict"] == "passed"
    assert assurance_scope.from_message("Show key results for carrier wind; from a to b", "p", {})[
        "profile"] == PROFILE
    assert ctx.project_uuid


def test_battery_carrier_reads_split_storage_power_and_an_empty_generator_set(battery, checked):
    frames, answer = run(checked, battery, "battery", BATTERY)
    assert answer["receipt"]["request_complete"], answer["receipt"]["result_slots"]
    assert {b["metric"]: b["value"] for b in answer["blocks"]} == pytest.approx(BATTERY)
    text = public_text(frames)
    assert "0 generators and 1 StorageUnits of this carrier" in text
    assert "Storage energy uses store weights; generator energy uses generator weights" in text
    # Store weights 2, 1, 1 differ from generator weights 3, 4, 5.
    assert "Store and generator weights differ in this window" in text


def test_simultaneous_charge_and_discharge_count_both_not_the_net(battery, checked):
    su = battery.network.storage_units_t
    # Snapshot 1 (1 h): 8 + (0.8*5 - 4/0.5) = 4 MWh, the solved state; net p = -1.
    su.p_store.iloc[1, 0], su.p_dispatch.iloc[1, 0], su.p.iloc[1, 0] = 5, 4, -1
    # charge 2*5 + 1*5 = 15; discharge 1*4 + 1*2 = 6. The net would give 11 and 2.
    values = {**BATTERY, "carrier_storage_discharged_energy": 6, "carrier_storage_charged_energy": 15}
    _, answer = run(checked, battery, "battery", values)
    assert {b["metric"]: b["value"] for b in answer["blocks"]} == pytest.approx(values)


def test_a_carrier_link_is_disclosed_and_not_counted(wind, checked):
    n = wind.network
    # One gas generator, too dear to run, and one gas Link (a CHP-like path).
    n.add("Generator", "G3", bus="B1", p_nom=5, marginal_cost=50, carrier="gas")
    n.add("Bus", "Heat")
    n.add("Link", "CHP", bus0="B1", bus1="Heat", p_nom=0, carrier="gas")
    zero = dict.fromkeys(METRICS, 0)
    frames, answer = run(checked, _solve(wind), "gas", zero)
    assert answer["receipt"]["request_complete"]
    assert {b["metric"]: b["value"] for b in answer["blocks"]} == pytest.approx(zero)
    assert "1 generators and 0 StorageUnits of this carrier; 1 Links and 0 Stores of this carrier excluded" in (
        public_text(frames))


def test_a_refused_storage_unit_leaves_the_generator_metrics_answered(wind, checked):
    n = wind.network
    n.add("StorageUnit", "Pumped", bus="B1", p_nom=1, max_hours=2, carrier="wind",
          cyclic_state_of_charge=True, marginal_cost=1000)
    frames, answer = run(checked, _solve(wind), "wind", {m: WIND[m] for m in METRICS[:2]})
    got = slots(answer)
    assert got["carrier_generator_energy"]["disposition"] == "answered"
    assert got["carrier_storage_charged_energy"]["disposition"] == "unavailable"
    assert got["carrier_storage_charged_energy"]["reason"] == "invalid_or_missing_storage_trajectory"


@pytest.mark.parametrize("case,reason", [
    ("links_only", "carrier_outside_scope"),
    ("ambiguous", "ambiguous_carrier"),
    ("unknown", "unknown_carrier"),
])
def test_unsupported_carriers_are_refused_by_name(wind, checked, case, reason):
    n = wind.network
    carrier = "wind"
    if case == "links_only":
        n.add("Bus", "H2")
        n.add("Link", "Pipe", bus0="B1", bus1="H2", p_nom=0, carrier="hydrogen")
        carrier = "hydrogen"
    elif case == "ambiguous":
        n.add("Generator", "G3", bus="B1", p_nom=1, marginal_cost=50, carrier="Wind")
    else:
        carrier = "solar"
    ctx = _solve(wind)
    frames = checked.run(NumericalProvider({}), message="Check carrier results.",
                         checked_scope=scope(ctx, carrier))
    record = approval(frames)
    receipt = record.get("receipt") or {}
    assert receipt.get("request_complete") is False
    assert {s["disposition"] for s in receipt["result_slots"]} == {"unavailable"}
    assert {s["reason"] for s in receipt["result_slots"]} == {reason}


def test_one_invalid_generator_makes_the_carrier_metric_unavailable(wind, checked):
    ctx = _solve(wind)
    ctx.network.generators_t.p.loc[:, "G1"] = float("nan")
    frames, answer = run(checked, ctx, "wind", {m: WIND[m] for m in METRICS[2:]})
    got = slots(answer)
    assert got["carrier_generator_energy"]["disposition"] == "unavailable"
    assert got["carrier_peak_generator_output"]["disposition"] == "unavailable"
    assert "partial_value" not in got["carrier_generator_energy"]
    assert not answer["receipt"]["request_complete"]


def test_new_profile_is_off_unless_listed(monkeypatch):
    monkeypatch.delenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES")
    assert not rollout.profile_enabled(PROFILE)
    assert rollout.profile_enabled("generator-dispatch/1")
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", "generator-dispatch/1," + PROFILE)
    assert rollout.profile_enabled(PROFILE)


def test_carrier_scope_refuses_a_cost_basis():
    with pytest.raises(ValueError, match="unsupported_carrier_cost_scope"):
        assurance_scope.validate({"version": 1, "profile": PROFILE, "project_id": "p",
                                  "entity_id": "wind", "cost_basis": {
                                      "currency": "EUR", "price_year": 2026,
                                      "annual_fixed_cost_fraction": 1}})

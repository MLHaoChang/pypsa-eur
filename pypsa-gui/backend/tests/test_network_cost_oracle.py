"""network-linear-cost/1 against a hand-worked two-generator LP (assistant-assurance 10, R2).

The older oracles (`test_harness_reliability.py`) run on one generator, where the
network population and the generator coincide. Here two generators split the load,
so a network sum differs from either unit and the network peak, max over t of the
summed dispatch, differs from the sum of the unit peaks.

Hand LP: one bus, three snapshots, generator weights 1, 0.5, 2 and objective weights
2, 1, 0.5. G1 (10 MW, 5 EUR/MWh, capital 3 EUR/MW) is cheaper than G2 (25 MW,
8 EUR/MWh, capital 1 EUR/MW); G1's availability is 1, 1, 0.4. Load 4, 12, 25 MW.
Merit order: G1 = 4, 10, 4 (0.4 x 10 at t3); G2 = 0, 2, 21.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import pandas as pd
import pypsa
import pytest

from services import assurance_records as records
from tests.test_result_profiles_assurance import solve
from tests.test_workflow_tools import baseline  # noqa: F401

# dispatch: 1 x (4 + 0) + 0.5 x (10 + 2) + 2 x (4 + 21) = 4 + 6 + 50 = 60 MWh
# expenditure: G1 5 x (2 x 4 + 1 x 10 + 0.5 x 4) = 100; G2 8 x (0 + 1 x 2 + 0.5 x 21) = 100; 200 EUR
# peak: max(4, 12, 25) = 25 MW (the unit peaks 10 + 21 = 31 MW would be wrong)
# fixed: 10 x 3 + 25 x 1 = 55 EUR/a; allocated at the declared fraction 0.5: 27.5 EUR;
# total 27.5 + 200 = 227.5 EUR
HAND = {
    "dispatch_energy": 60.0,
    "marginal_expenditure": 200.0,
    "peak_dispatch": 25.0,
    "annual_fixed_cost": 55.0,
    "allocated_fixed_cost": 27.5,
    "total_cost": 227.5,
}


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))


@pytest.fixture
def two_units(baseline):
    ctx = baseline[0]
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=3, freq="h"))
    n.snapshot_weightings["generators"] = [1.0, 0.5, 2.0]
    n.snapshot_weightings["objective"] = [2.0, 1.0, 0.5]
    n.add("Bus", "B1")
    n.add(
        "Generator",
        "G1",
        bus="B1",
        p_nom=10,
        marginal_cost=5,
        capital_cost=3,
        p_max_pu=[1.0, 1.0, 0.4],
    )
    n.add("Generator", "G2", bus="B1", p_nom=25, marginal_cost=8, capital_cost=1)
    n.add("Load", "L1", bus="B1", p_set=[4.0, 12.0, 25.0])
    ctx.network = n
    return solve(ctx)


def _facts(ctx, **overrides):
    scope = {
        "version": 1,
        "profile": "network-linear-cost/1",
        "project_id": ctx.project_uuid,
        "entity_id": "network",
        "retain": False,
        "cost_basis": {
            "currency": "EUR",
            "price_year": 2026,
            "annual_fixed_cost_fraction": 0.5,
        },
        **overrides,
    }
    record = records.capture_scoped(records.actor_id(), scope)
    return {f["metric"]: f["value"] for f in record["facts"]}, {
        s["metric"]: s["reason"]
        for s in record["slots"]
        if s["disposition"] == "unavailable"
    }


def test_the_network_population_equals_the_hand_lp(two_units):
    n = two_units.network
    assert list(n.generators_t.p["G1"]) == [4.0, 10.0, 4.0]
    assert list(n.generators_t.p["G2"]) == [0.0, 2.0, 21.0]
    values, reasons = _facts(two_units)
    assert reasons == {}
    assert values == HAND


def test_a_network_peak_is_not_the_sum_of_unit_peaks(two_units):
    values, _ = _facts(two_units)
    unit_peaks = max(two_units.network.generators_t.p["G1"]) + max(
        two_units.network.generators_t.p["G2"]
    )
    assert unit_peaks == 31.0 and values["peak_dispatch"] == 25.0


def test_without_a_basis_the_fixed_costs_are_withheld(two_units):
    values, reasons = _facts(two_units, cost_basis=None)
    # Expenditure needs the basis too on a cost profile: only the physical figures remain.
    assert values == {"dispatch_energy": 60.0, "peak_dispatch": 25.0}
    assert (
        reasons["annual_fixed_cost"]
        == reasons["allocated_fixed_cost"]
        == "explicit_cost_basis_required"
    )

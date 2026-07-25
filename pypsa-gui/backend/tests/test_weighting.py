"""
Energy-vs-cost weighting basis: energy/MWh quantities must use
snapshot_weightings.generators (PyPSA's energy basis); cost uses .objective.
Locks in the migration (economics dispatch GWh, losses years scaling, etc.).
"""
from __future__ import annotations

import pandas as pd
import pypsa

from tests.conftest import build_network
from routers.compare import _build_snapshot_weights


def test_economics_dispatch_gwh_uses_generators_basis(client, install_network):
    # generators=2, objective=5 → the two bases differ 2.5×, so the test can
    # tell which column the economics endpoint weights energy by.
    live = install_network(build_network(solve=True, gens_weight=2.0, obj_weight=5.0))

    p_gas = live.generators_t.p["gas"]
    expected_generators = float((p_gas * live.snapshot_weightings["generators"]).sum()) / 1000.0
    expected_objective = float((p_gas * live.snapshot_weightings["objective"]).sum()) / 1000.0
    # Sanity: the two bases really do differ for this fixture.
    assert abs(expected_generators - expected_objective) > 1e-6

    resp = client.get("/api/results/economics_by_carrier")
    assert resp.status_code == 200
    gas = resp.json()["by_carrier"]["gas"]["dispatch_gwh"]["total"]

    # Economics GWh must match the GENERATORS-weighted energy (not objective).
    assert abs(gas - expected_generators) < 1e-3, (gas, expected_generators)
    assert abs(gas - expected_objective) > 1e-3, "economics energy is still on the objective basis"


def test_build_snapshot_weights_flat_has_no_years_factor():
    n = build_network(gens_weight=3.0, obj_weight=7.0)
    w_gen = _build_snapshot_weights(n, "generators")
    w_obj = _build_snapshot_weights(n)  # default "objective"
    assert list(w_gen) == [3.0] * len(n.snapshots)
    assert list(w_obj) == [7.0] * len(n.snapshots)


def test_build_snapshot_weights_falls_back_to_objective_when_generators_missing():
    n = build_network(obj_weight=4.0)
    # Simulate an older netcdf without a generators column.
    n.snapshot_weightings.drop(columns=["generators"], inplace=True)
    w = _build_snapshot_weights(n, "generators")
    assert list(w) == [4.0] * len(n.snapshots)  # fell back to objective


def test_build_snapshot_weights_applies_investment_period_years():
    n = pypsa.Network()
    idx = pd.MultiIndex.from_product(
        [[2030, 2040], pd.date_range("2025-01-01", periods=2, freq="h")],
        names=["period", "timestep"],
    )
    n.set_snapshots(idx)
    n.investment_periods = [2030, 2040]
    n.investment_period_weightings["years"] = [5.0, 10.0]
    # generators column defaults to 1.0 → weight == years per period row.
    w = _build_snapshot_weights(n, "generators")
    assert list(w) == [5.0, 5.0, 10.0, 10.0]

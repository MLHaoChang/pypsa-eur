"""The P4 integration fixture is a year, solves quickly and costs the owner's
assets with an overnight cost (IC P4 plan WP4.0)."""
from __future__ import annotations

import time

import pytest

from tests.fixtures.investment_case.edge_hourly_year import (
    DISCOUNT_RATE, build_edge_hourly_year,
)


def test_the_template_is_one_year_of_hours():
    n = build_edge_hourly_year()
    assert len(n.snapshots) == 8760
    assert float(n.snapshot_weightings.objective.sum()) == pytest.approx(8760.0)
    for comp, name in (("generators", "pv"), ("storage_units", "bess")):
        df = getattr(n, comp)
        assert df.at[name, "overnight_cost"] > 0 and df.at[name, "discount_rate"] == DISCOUNT_RATE


@pytest.mark.live_solve
def test_it_solves_within_the_budget_and_exports_some_pv():
    n = build_edge_hourly_year()
    t0 = time.perf_counter()
    status, cond = n.optimize(solver_name="highs")
    elapsed = time.perf_counter() - t0
    assert status == "ok", cond
    assert elapsed < 30.0, f"{elapsed:.1f} s"
    assert float(n.generators_t.p["pv"].sum()) > 50_000.0     # MWh a year
    # The grid supply's cost is exactly the site's import × its price (the P4
    # commodity cross-check; WP4.0 review B1): the sink is uncosted.
    opex = n.statistics.opex(groupby=False)
    supply = float(opex.loc[("Generator", "grid_supply")])
    w = n.snapshot_weightings.objective
    imp = float((n.links_t.p0["import"] * w).sum())
    assert supply == pytest.approx(60.0 * imp, rel=1e-9)
    # The derived capex annuity (PyPSA 1.x from overnight cost, rate, lifetime)
    # for PV: 700k × annuity(7 %, 30) × 40 MW (review R6).
    capex = n.statistics.capex(groupby=False)
    ann = DISCOUNT_RATE / (1 - (1 + DISCOUNT_RATE) ** -30)
    assert float(capex.loc[("Generator", "pv")]) == pytest.approx(700_000.0 * ann * 40.0, rel=1e-6)

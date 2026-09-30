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
    assert float(n.links_t.p0["export"].sum()) > 0.0          # some export happens
    assert float(n.generators_t.p["pv"].sum()) > 50_000.0     # MWh a year

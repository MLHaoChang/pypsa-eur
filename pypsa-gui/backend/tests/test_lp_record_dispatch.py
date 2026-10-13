"""
A public "record a solve" helper (IC engine ask 5, owner 2026-10-13).

`lp_bindings.record_dispatch_as_solve(n, commercial)` leaves on a network
whose dispatch is already written (`links_t.p0`) what a successful engine
solve of that dispatch leaves: the PoC prices materialised, undone and
COMMITTED (`ic_poc_links`, `ic_energy_price`), and the demand and capacity
records read the way the solve's readers read the solved dispatch (interval
means, ratchet and tier rule values, fixed-PoC capacity charges). Test fakes
then need no `lp_bindings` internals (the study's
`tests/study_s4_support.record_engine_solve` wrote them by hand).

It returns the priced `marginal_cost` of each priced Link while the prices
were applied (what a fake writes as the bus price). Energy tiers are an LP
allocation it cannot read from a dispatch: refused, typed. A dispatch not
written for a metered Link is refused too.

Written red first: the helper did not exist.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pytest

from models.commercial import Tariff
from services.commercial import lp_bindings as L
from tests.fixtures.investment_case import edge_15min as F


def _tariff(*items) -> Tariff:
    return Tariff.model_validate({"id": "t", "name": "t", "jurisdiction": "US",
                                  "valid_from": "2030-01-01", "items": list(items)})


ENERGY = {"id": "energy", "kind": "energy", "unit": "per_kwh", "measured_on": "import",
          "periods": [{"name": "all", "rate": 0.05}]}
DEMAND = {"id": "demand", "kind": "demand", "unit": "per_kw_month", "measured_on": "import",
          "periods": [{"name": "all", "rate": 15.0}],
          "ratchet": {"lookback_months": 11, "share": 0.8}}
PEAK_CAP = {"id": "lp", "kind": "capacity", "unit": "per_kw_year", "measured_on": "peak_import",
            "periods": [{"name": "all", "rate": 400.0}]}
CONTRACTED = {"id": "cap", "kind": "capacity", "unit": "per_kw_year",
              "periods": [{"name": "all", "rate": 40.0}]}
TIERED = {"id": "tiered", "kind": "energy", "unit": "per_kwh", "measured_on": "import",
          "periods": [{"name": "all", "rate": 0.0}],
          "tiers": [{"threshold": 0, "rate": 0.10}, {"threshold": 100_000, "rate": 0.30}]}


def _commercial(*items):
    return {"poc_link": "import", "import_tariff": _tariff(*items).model_dump(mode="json")}


def _site():
    n = F.build_edge_15min()
    n.generators.loc["grid_supply", "marginal_cost"] = 50.0
    return n


def _solve(n, commercial):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    PyPSAService.set_network(n)
    status, condition = run_simulation(SolverConfig(commercial=commercial), n,
                                       PyPSAService.get_lock(), threading.Event(),
                                       queue.SimpleQueue(), state_update=lambda **kw: None)
    assert status in ("ok", "optimal"), (status, condition)


def _approx(got, want, path=""):
    if isinstance(want, dict):
        assert set(got) == set(want), path
        for k in want:
            _approx(got[k], want[k], f"{path}.{k}")
    elif isinstance(want, (list, tuple)):
        assert len(got) == len(want), path
        for i, (g, w) in enumerate(zip(got, want)):
            _approx(g, w, f"{path}[{i}]")
    elif isinstance(want, float):
        assert got == pytest.approx(want, rel=1e-9, abs=1e-9), path
    else:
        assert got == want, path


@pytest.mark.live_solve
def test_the_record_equals_what_the_solve_of_that_dispatch_commits():
    items = (ENERGY, DEMAND, PEAK_CAP, CONTRACTED)
    solved = _site()
    _solve(solved, _commercial(*items))
    fake = _site()
    fake.links_t.p0 = solved.links_t.p0.copy()
    fake.links_t.p1 = solved.links_t.p1.copy()
    mc_before = fake.links_t.marginal_cost.copy()

    priced = L.record_dispatch_as_solve(fake, _commercial(*items))

    for key in (L.META_DEMAND, L.META_DEMAND_INFO, L.META_CAPACITY, L.META_LINKS):
        assert key in solved.meta, key
        _approx(fake.meta[key], solved.meta[key], key)
    pd.testing.assert_frame_equal(fake.links_t[L.ENERGY_PRICE_ATTR],
                                  solved.links_t[L.ENERGY_PRICE_ATTR])
    # The prices were undone: the Link costs what it cost before.
    pd.testing.assert_frame_equal(fake.links_t.marginal_cost, mc_before)
    base = float(fake.links.at["import", "marginal_cost"])
    pd.testing.assert_series_equal(
        priced["import"], base + solved.links_t[L.ENERGY_PRICE_ATTR]["import"],
        check_names=False)
    for attr in (L.DEMAND_SPEC_ATTR, L.CAPACITY_SPEC_ATTR):
        assert not hasattr(fake, attr)


def _two_month_site():
    old = F.START
    F.START = "2030-01-28 00:00"                     # a week across January and February
    try:
        n = F.build_edge_15min()
    finally:
        F.START = old
    n.generators.loc["grid_supply", "marginal_cost"] = 50.0
    return n


@pytest.mark.live_solve
def test_binding_ratchets_and_demand_tiers_record_the_rule_value():
    """Two months, a demand-tier item with a months-mode ratchet seeded from
    July's history (it binds: billed above the peak) and a lookback item."""
    tiered = {"id": "a", "kind": "demand", "unit": "per_kw_month", "measured_on": "import",
              "periods": [{"name": "all", "rate": 0.0}],
              "tiers": [{"threshold": 0, "rate": 5.0}, {"threshold": 30_000, "rate": 20.0}],
              "ratchet": {"months": [1, 7], "share": 0.8}}
    lookback = {"id": "b", "kind": "demand", "unit": "per_kw_month", "measured_on": "import",
                "periods": [{"name": "all", "rate": 12.0}],
                "ratchet": {"lookback_months": 1, "share": 0.9}}
    commercial = {**_commercial(tiered, lookback, PEAK_CAP),
                  "meter_history_peaks_kw": {"2030-07": 9e4}}
    solved = _two_month_site()
    _solve(solved, commercial)
    peaks = solved.meta[L.META_DEMAND]
    assert len({v["month"] for v in peaks.values()}) == 2
    assert any(v["billed_mw"] > v["peak_mw"] + 1.0 for v in peaks.values())   # a ratchet binds
    fake = _two_month_site()
    fake.links_t.p0 = solved.links_t.p0.copy()
    L.record_dispatch_as_solve(fake, commercial)
    for key in (L.META_DEMAND, L.META_DEMAND_INFO, L.META_CAPACITY):
        _approx(fake.meta[key], solved.meta[key], key)


def test_energy_tiers_are_refused_typed():
    n = _site()
    n.links_t.p0 = pd.DataFrame({"import": 1.0}, index=n.snapshots)
    with pytest.raises(L.CommercialBindingError, match="record_dispatch_tiers_need_lp"):
        L.record_dispatch_as_solve(n, _commercial(TIERED))
    assert L.META_LINKS not in n.meta and not hasattr(n, L.TIER_SPEC_ATTR)


def test_an_unwritten_dispatch_is_refused_typed():
    n = _site()
    with pytest.raises(L.CommercialBindingError, match="record_dispatch_missing_flow"):
        L.record_dispatch_as_solve(n, _commercial(ENERGY, DEMAND))
    assert L.META_LINKS not in n.meta and not hasattr(n, L.DEMAND_SPEC_ATTR)
    assert "import" not in n.links_t.marginal_cost.columns

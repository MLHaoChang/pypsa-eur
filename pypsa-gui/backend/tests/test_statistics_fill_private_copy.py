"""
B6 (study-harness issue 31): no report path fills periodized cost defaults
on the LIVE network.

The fill scales `capital_cost` / `fom_cost` to the modelled horizon and fills
blank `discount_rate` / `lifetime` for the length of the block. Run in place
on `PyPSAService.get_network()` it is visible to every reader that takes no
lock — the input fingerprint behind `run_envelope.settle` and the chat
freshness checks among them — which then hashes the transient values.

The probe wraps `fill_periodized_cost_defaults`: each time it fills a
network, it reads the live network's static cost columns, exactly as a
concurrent unlocked reader landing in that window would, and records whether
the live network itself was the fill's target.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.solver import periodized_costs as PC
from services.solver_service import SolverConfig, with_periodized_cost_defaults

COLUMNS = ("capital_cost", "fom_cost", "discount_rate", "lifetime")
COMPONENTS = ("generators", "links")


def _build() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1095.0  # nyears = 0.5: a scaled FOM is visibly different
    for carrier in ("AC", "gas", "H2"):
        n.add("Carrier", carrier)
    n.add("Bus", "el", carrier="AC")
    n.add("Bus", "h2", carrier="H2")
    n.add("Load", "load", bus="el", p_set=100.0)
    n.add("Load", "h2load", bus="h2", p_set=20.0)
    # Typed annual capital_cost + FOM: scaled by the fill.
    n.add("Generator", "gas", bus="el", carrier="gas", p_nom_extendable=True,
          capital_cost=1000.0, fom_cost=200.0, marginal_cost=10.0)
    # Overnight-priced with a blank discount rate and lifetime: filled by the fill.
    n.add("Generator", "pv", bus="el", carrier="AC", p_nom_extendable=True,
          overnight_cost=5000.0, discount_rate=np.nan, lifetime=np.inf,
          p_max_pu=0.3, marginal_cost=0.0)
    n.add("Link", "electrolyser", bus0="el", bus1="h2", carrier="H2", p_nom_extendable=True,
          efficiency=0.7, capital_cost=500.0, fom_cost=50.0)
    return n


def _static(n) -> dict:
    out = {}
    for comp in COMPONENTS:
        df = getattr(n, comp)
        for col in COLUMNS:
            if col in df.columns:
                out[(comp, col)] = df[col].copy()
    return out


def _assert_static_equal(before: dict, after: dict) -> None:
    assert before.keys() == after.keys()
    for key, series in before.items():
        pd.testing.assert_series_equal(series, after[key], check_names=False, obj=str(key))


@pytest.fixture(scope="module")
def solved() -> pypsa.Network:
    n = _build()
    cfg = SolverConfig()
    # Solve the way `run_simulation` does: inside the in-place fill, reverted after.
    with with_periodized_cost_defaults(n, cfg):
        n.optimize(solver_name="highs")
    return n


@pytest.fixture
def probe(monkeypatch, solved):
    """Record every fill target and what an unlocked reader of `solved` saw
    while that fill was in force."""
    baseline = _static(solved)
    seen: list[dict] = []
    real = PC.fill_periodized_cost_defaults

    def spy(n, cfg, **kwargs):
        revert = real(n, cfg, **kwargs)
        seen.append({"live_target": n is solved, "live_static": _static(solved)})
        return revert

    monkeypatch.setattr(PC, "fill_periodized_cost_defaults", spy)
    return baseline, seen


def _cost_breakdown(n, cfg):
    from services.results.cost_breakdown import compute_cost_breakdown
    return compute_cost_breakdown(n, cfg)


def _statistics(n, cfg):
    from services.results.statistics import compute_statistics
    return compute_statistics(n, cfg)


def _objective_decomposition(n, cfg):
    from services.results.objective_decomposition import compute_objective_decomposition
    return compute_objective_decomposition(n, _cost_breakdown(n, cfg), cfg)


def _lcoh(n, cfg):
    from services.results.lcoh import compute_lcoh
    result_df = pd.DataFrame({"electrolyser": [1.0] * len(n.snapshots)}, index=n.snapshots)
    return compute_lcoh(n, cfg, result_df=result_df)


def _horizon_cost(n, cfg):
    from services.cost_totals import horizon_system_cost
    return horizon_system_cost(n, cfg)


def _asset_costs(n, cfg):
    return PC.periodized_capital_costs(n, cfg)


def _value_flow_assets(n, cfg):
    from services.commercial.participants import MeterSides
    from services.results import value_flows as VF
    # Every bus on the grid side: the classification is not under test.
    sides = MeterSides(site=set(), grid=set(n.buses.index), bypass=set(), meter_links=set())
    return VF._asset_costs(n, cfg, None, sides)


ENTRY_POINTS = {
    "statistics": _statistics,
    "cost_breakdown": _cost_breakdown,
    "objective_decomposition": _objective_decomposition,
    "lcoh": _lcoh,
    "horizon_system_cost": _horizon_cost,
    "periodized_capital_costs": _asset_costs,
    "value_flows_asset_costs": _value_flow_assets,
}


@pytest.mark.parametrize("name", sorted(ENTRY_POINTS))
def test_report_path_never_fills_the_live_network(name, solved, probe):
    baseline, seen = probe
    ENTRY_POINTS[name](solved, SolverConfig())

    assert seen, f"{name} ran no periodized-cost fill; the probe observed nothing"
    for event in seen:
        assert not event["live_target"], f"{name} filled the live network in place"
        _assert_static_equal(baseline, event["live_static"])
    _assert_static_equal(baseline, _static(solved))


def test_view_yields_the_filled_values_on_a_private_copy(solved):
    """The view carries what the in-place fill would, on its own frames, and
    shares the time series rather than copying them."""
    cfg = SolverConfig()
    baseline = _static(solved)
    with with_periodized_cost_defaults(solved, cfg):
        expected = _static(solved)
    _assert_static_equal(baseline, _static(solved))

    with PC.periodized_cost_view(solved, cfg) as view:
        assert view is not solved
        _assert_static_equal(expected, _static(view))
        _assert_static_equal(baseline, _static(solved))
        assert view.generators_t.p is solved.generators_t.p
        assert PC.fom_is_scaled(view)
    assert not PC.fom_is_scaled(solved)


def test_view_inside_an_in_place_fill_does_not_scale_twice(solved):
    """`solver_service` reads the horizon cost while the solve's own fill is
    in force: the view inherits that scaling instead of applying it again."""
    cfg = SolverConfig()
    with with_periodized_cost_defaults(solved, cfg):
        filled = _static(solved)
        with PC.periodized_cost_view(solved, cfg) as view:
            _assert_static_equal(filled, _static(view))

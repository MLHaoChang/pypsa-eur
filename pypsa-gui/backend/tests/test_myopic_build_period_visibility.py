"""
After a myopic run the user must be able to SEE which period decided each
asset's capacity.

The Capacity Expansion view groups strictly by `build_year > 0`. A myopic run
freezes assets that carry the default `build_year = 0`, and
`_freeze_period_capacities` deliberately does not touch that column (it drives
PyPSA's activity mask — writing the freeze period into it would change which
periods the asset is active in and when it retires). So the "Capacity expansion
by period" section came back EMPTY for exactly the run where the user most
needs to see that everything was decided in period 1 and nothing could be added
afterwards.

The fix routes the freeze period through the existing `vintage_results` channel
the view already reads, so no frontend change is needed.
"""
from __future__ import annotations

import pathlib
import tempfile

import pandas as pd
import pypsa
import pytest

from services.solver_service import (
    SolverConfig,
    _apply_modelling_assumptions,
    _run_myopic_foresight,
)

PERIODS = [2030, 2035, 2040]


def _network(growth=(1.0, 1.3, 1.6)) -> pypsa.Network:
    n = pypsa.Network()
    idx = pd.MultiIndex.from_product(
        [PERIODS, pd.date_range("2030-01-01", periods=3, freq="h")],
        names=["period", "timestep"],
    )
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.investment_periods = PERIODS
    n.investment_period_weightings["years"] = 5.0
    n.investment_period_weightings["objective"] = 5.0
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    n.add("Load", "L", bus="B", p_set=pd.Series(
        [100.0 * dict(zip(PERIODS, growth))[p] for p, _ in n.snapshots],
        index=n.snapshots))
    n.add("Generator", "GAS", bus="B", carrier="gas", p_nom_extendable=True,
          capital_cost=120.0, marginal_cost=10.0, p_nom_max=10_000.0)
    n.add("Generator", "VOLL", bus="B", carrier="gas", p_nom=5_000.0,
          marginal_cost=5_000.0)
    return n


def _cfg() -> SolverConfig:
    return SolverConfig(solve_strategy="myopic", multi_investment_periods=True,
                        investment_periods=PERIODS, voll=0.0)


def _solve(n: pypsa.Network, cfg: SolverConfig) -> list:
    """Run the myopic loop and return its undo list (as run_simulation does)."""
    tmp = pathlib.Path(tempfile.mktemp(suffix=".log"))
    tmp.touch()
    try:
        status, condition, undo = _run_myopic_foresight(
            n, cfg, lambda m: None, merged_solver_options={}, extra_fn=None,
            tmp_log=tmp, stop_event=None, iteration_undo=[])
        assert (status, condition) == ("ok", "optimal"), f"{status}/{condition}"
        return undo
    finally:
        tmp.unlink(missing_ok=True)


def _revert(n: pypsa.Network, undo: list) -> None:
    """
    Undo the capacity freezes, the way `run_simulation`'s finally block does.
    Without this a second solve sees a fleet that is already non-extendable and
    has nothing left to decide — which is a different scenario entirely.
    """
    for action in reversed(undo):
        _, attr_name, col, idx, original = action
        df = getattr(n, attr_name, None)
        if df is None:
            continue
        valid = [i for i in idx if i in df.index]
        if valid:
            df.loc[valid, col] = original.loc[valid]


def _entry(n, cls="Generator", name="GAS"):
    return ((n.meta or {}).get("vintage_results", {}).get(cls, {})).get(name)


def test_the_deciding_period_is_recorded_for_a_frozen_asset():
    n = _network()
    _solve(n, _cfg())

    entry = _entry(n)
    assert entry is not None, (
        "no vintage_results entry — the Capacity Expansion per-period chart "
        "would render empty for this run"
    )
    assert entry["periods"], "entry has no periods"
    assert [p["build_year"] for p in entry["periods"]] == [2030], (
        "capacity was decided by the FIRST period's LP, so that is the period "
        "the chart must attribute it to"
    )
    # The frontend accumulates p_nom_opt onto initial_capacity, so this field
    # is the period's OWN contribution, not the cumulative total.
    assert entry["periods"][0]["p_nom_opt"] == pytest.approx(
        float(n.generators.at["GAS", "p_nom_opt"]), rel=1e-6)
    assert entry["initial_capacity"] == pytest.approx(0.0)
    assert entry["capacity_field"] == "p_nom"


def test_build_year_itself_is_left_alone():
    """
    The column drives PyPSA's activity mask. Writing the freeze period into it
    would silently change which periods the asset is active in and when it
    retires — a model change made to populate a chart.
    """
    n = _network()
    _solve(n, _cfg())
    assert float(n.generators.at["GAS", "build_year"]) == 0.0


def test_a_second_run_replaces_rather_than_accumulates():
    """
    `apply_vintage_bounds` resets vintage_results at solve start but returns
    early when there are no per-period bounds — the case this feature serves.
    Without an explicit clear, build periods would pile up across solves.
    """
    n = _network()
    undo = _solve(n, _cfg())
    first = _entry(n)
    assert first is not None
    _revert(n, undo)          # what run_simulation's finally block does
    _solve(n, _cfg())
    second = _entry(n)
    assert second is not None, "the re-solve produced no build record at all"
    assert len(second["periods"]) == 1, (
        f"entries accumulated across runs: {second['periods']}"
    )
    assert second["periods"][0]["build_year"] == first["periods"][0]["build_year"]


def test_an_asset_that_was_never_expanded_gets_no_row():
    """A zero delta would draw a meaningless zero-height bar."""
    n = _network()
    # Plenty of firm capacity already — the LP has no reason to build.
    n.generators.loc["GAS", "p_nom"] = 5_000.0
    n.generators.loc["GAS", "p_nom_extendable"] = True
    _solve(n, _cfg())
    entry = _entry(n)
    assert entry is None or not entry["periods"], (
        f"expected no build row for an unexpanded asset, got {entry}"
    )


def test_real_vintages_keep_vintage_service_breakdown():
    """
    With per-period bounds configured, `vintage_service` owns the breakdown for
    the PARENT asset. The myopic recorder must not write competing entries
    under the transient vintage row names, which would double-count capacity in
    the chart.
    """
    n = _network()
    n.meta["vintage_bounds"] = {
        "Generator": {"GAS": {str(p): {"p_nom_min": 0.0, "p_nom_max": 5000.0}
                              for p in PERIODS}}
    }
    cfg = _cfg()
    restore, _captured = _apply_modelling_assumptions(n, cfg, lambda m: None)
    try:
        _solve(n, cfg)
        root = (n.meta or {}).get("vintage_results", {}).get("Generator", {})
        stray = [k for k in root if "@" in k]
        assert not stray, f"transient vintage rows got their own entries: {stray}"
    finally:
        restore()

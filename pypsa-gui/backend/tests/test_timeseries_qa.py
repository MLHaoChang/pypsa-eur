"""
Data-quality checks on uploaded profiles.

Every case here SOLVES. That is the point: a profile of 8760 zeros, a demand
column in kW, a decimal-place typo — none of them makes PyPSA fail, so nothing
downstream of the LP will ever mention them, and the user gets a confident
wrong answer. These are warnings by the module's severity policy and must
never block a run.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

from services import timeseries_qa as Q
from services.solver_service import SolverConfig
from services.validation_service import has_errors, validate_for_run


def _network(periods: int = 48) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=periods, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=100.0)
    n.add("Generator", "gas", bus="B1", p_nom=200.0, marginal_cost=50.0)
    return n


def _codes(issues) -> set[str]:
    return {i.code for i in issues}


def _for(issues, code: str, name: str):
    return next((i for i in issues if i.code == code and i.name == name), None)


def _profile(n, frame_attr: str, attribute: str, name: str, values) -> None:
    frame = getattr(getattr(n, frame_attr), attribute)
    frame[name] = pd.Series(np.asarray(values, dtype=float), index=n.snapshots)


# ── All-zero ───────────────────────────────────────────────────────────────


def test_all_zero_availability_is_flagged(install_network=None):
    n = _network()
    n.add("Generator", "solar", bus="B1", p_nom_extendable=True,
          capital_cost=1.0)
    _profile(n, "generators_t", "p_max_pu", "solar", np.zeros(48))
    issue = _for(Q.check_timeseries_quality(n), "timeseries_all_zero", "solar")
    assert issue is not None
    assert issue.severity == "warning"
    assert "can never dispatch" in issue.message


def test_all_zero_demand_is_flagged():
    n = _network()
    _profile(n, "loads_t", "p_set", "L1", np.zeros(48))
    issue = _for(Q.check_timeseries_quality(n), "timeseries_all_zero", "L1")
    assert issue is not None
    assert "no demand" in issue.message


def test_a_zero_series_is_not_also_reported_as_frozen():
    """Two warnings for one defect is noise, and noise is how warnings die."""
    n = _network()
    _profile(n, "loads_t", "p_set", "L1", np.zeros(48))
    codes = _codes(Q.check_timeseries_quality(n))
    assert "timeseries_all_zero" in codes
    assert "timeseries_frozen" not in codes


# ── Frozen ─────────────────────────────────────────────────────────────────


def test_a_flat_profile_is_flagged_as_frozen():
    n = _network()
    _profile(n, "loads_t", "p_set", "L1", np.full(48, 100.0))
    issue = _for(Q.check_timeseries_quality(n), "timeseries_frozen", "L1")
    assert issue is not None
    assert "static p_set instead" in issue.message


def test_a_varying_profile_is_not_frozen():
    n = _network()
    _profile(n, "loads_t", "p_set", "L1", 100 + 10 * np.sin(np.arange(48)))
    assert "timeseries_frozen" not in _codes(Q.check_timeseries_quality(n))


def test_a_short_horizon_is_never_called_frozen():
    """A 4-snapshot test network is legitimately flat; that is not evidence."""
    n = _network(periods=4)
    _profile(n, "loads_t", "p_set", "L1", np.full(4, 100.0))
    assert "timeseries_frozen" not in _codes(Q.check_timeseries_quality(n))
    assert "timeseries_all_zero" not in _codes(Q.check_timeseries_quality(n))


# ── Negatives ──────────────────────────────────────────────────────────────


def test_negative_availability_is_flagged():
    n = _network()
    n.add("Generator", "wind", bus="B1", p_nom=10.0)
    values = np.full(48, 0.5)
    values[3] = -0.2
    _profile(n, "generators_t", "p_max_pu", "wind", values)
    issue = _for(Q.check_timeseries_quality(n), "timeseries_negative", "wind")
    assert issue is not None
    assert "cannot be below zero" in issue.message


def test_negative_demand_is_named_an_injection_not_an_error():
    """Negative load is legal in PyPSA. The message must not claim otherwise."""
    values = np.full(48, 100.0)
    values[5] = -40.0
    n = _network()
    _profile(n, "loads_t", "p_set", "L1", values)
    issue = _for(Q.check_timeseries_quality(n), "timeseries_negative", "L1")
    assert issue is not None
    assert "INJECTION" in issue.message
    assert "legal in PyPSA" in issue.message


# ── Spikes ─────────────────────────────────────────────────────────────────


def test_a_decimal_point_typo_is_flagged():
    n = _network()
    values = np.full(48, 100.0)
    values[10] = 100_000.0          # a stray thousands separator
    _profile(n, "loads_t", "p_set", "L1", values)
    issue = _for(Q.check_timeseries_quality(n), "timeseries_spike", "L1")
    assert issue is not None
    assert "sizes the fleet" in issue.message


def test_an_ordinary_peaky_profile_is_not_a_spike():
    """Real demand peaks at 2-3x its median. The check must ignore that."""
    n = _network()
    values = 100 + 120 * (np.arange(48) % 24 > 16)
    _profile(n, "loads_t", "p_set", "L1", values)
    assert "timeseries_spike" not in _codes(Q.check_timeseries_quality(n))


# ── Unit scale ─────────────────────────────────────────────────────────────


def test_a_load_in_kilowatts_among_megawatts_is_flagged():
    n = _network()
    n.add("Load", "L2", bus="B1", p_set=120.0)
    n.add("Load", "L3", bus="B1", p_set=90.0)
    # 110 MW of real demand, pasted as 110 000 kW. The peer median is 110 MW,
    # so this lands on EXACTLY the 1000x boundary — which is where a strict
    # comparison would have a blind spot precisely on the real case.
    n.add("Load", "kw_load", bus="B1", p_set=110_000.0)
    issue = _for(Q.check_timeseries_quality(n),
                 "timeseries_scale_outlier", "kw_load")
    assert issue is not None
    assert "unit error" in issue.message
    assert "1000x" in issue.message.replace(",", "")


def test_a_load_in_megawatts_among_gigawatt_peers_is_flagged():
    n = _network()
    for name in ("L2", "L3"):
        n.add("Load", name, bus="B1", p_set=200_000.0)
    n.loads.loc["L1", "p_set"] = 150.0
    issue = _for(Q.check_timeseries_quality(n),
                 "timeseries_scale_outlier", "L1")
    assert issue is not None


def test_a_wide_but_plausible_spread_is_not_a_unit_error():
    """A 0.5 MW load beside a 200 MW one is a modelling choice, not kW."""
    n = _network()
    n.add("Load", "small", bus="B1", p_set=0.5)
    n.add("Load", "big", bus="B1", p_set=200.0)
    assert "timeseries_scale_outlier" not in _codes(Q.check_timeseries_quality(n))


def test_the_scale_check_needs_a_population_to_compare_against():
    """With one or two loads there is no 'the others' to be an outlier from."""
    n = _network()
    n.add("Load", "L2", bus="B1", p_set=90_000.0)
    assert "timeseries_scale_outlier" not in _codes(Q.check_timeseries_quality(n))


def test_the_scale_check_reads_the_profile_not_the_static_value():
    """
    An uploaded profile overrides p_set, so judging the static value would
    miss exactly the case the check exists for.
    """
    n = _network()
    for name in ("L2", "L3"):
        n.add("Load", name, bus="B1", p_set=100.0)
    _profile(n, "loads_t", "p_set", "L1", np.full(48, 120_000.0))
    assert _for(Q.check_timeseries_quality(n),
                "timeseries_scale_outlier", "L1") is not None


# ── Integration with the run gate ──────────────────────────────────────────


def test_findings_reach_preflight():
    n = _network()
    _profile(n, "loads_t", "p_set", "L1", np.zeros(48))
    codes = _codes(validate_for_run(n, SolverConfig()))
    assert "timeseries_all_zero" in codes


def test_findings_never_block_a_run():
    """
    Severity policy: error = PyPSA will fail. None of these does — the solve
    succeeds and lies. Promoting one to error would refuse a legal network.
    """
    n = _network()
    _profile(n, "loads_t", "p_set", "L1", np.zeros(48))
    _profile(n, "generators_t", "p_max_pu", "gas", np.full(48, 1.0))
    issues = Q.check_timeseries_quality(n)
    assert issues, "the fixture must actually trip a check"
    assert all(i.severity == "warning" for i in issues)
    assert not has_errors(issues)


def test_a_clean_network_produces_no_findings():
    """The false-positive gate: ordinary data must stay silent."""
    n = _network()
    _profile(n, "loads_t", "p_set", "L1", 100 + 20 * np.sin(np.arange(48) / 3))
    n.add("Generator", "wind", bus="B1", p_nom=50.0)
    _profile(n, "generators_t", "p_max_pu", "wind",
             0.35 + 0.25 * np.cos(np.arange(48) / 5))
    assert Q.check_timeseries_quality(n) == []


def test_an_empty_network_is_silent():
    n = pypsa.Network()
    assert Q.check_timeseries_quality(n) == []


def test_an_all_nan_column_is_left_to_the_nonfinite_check():
    """Two owners for one defect means two messages and no fix."""
    n = _network()
    _profile(n, "loads_t", "p_set", "L1", np.full(48, np.nan))
    assert Q.check_timeseries_quality(n) == []


def test_the_solve_that_these_warnings_describe_actually_succeeds():
    """
    The premise of the whole module, asserted rather than assumed: a zeroed
    availability profile optimises cleanly, which is why nothing downstream
    would ever mention it.
    """
    n = _network(periods=6)
    n.add("Generator", "solar", bus="B1", p_nom=500.0, marginal_cost=0.0)
    _profile(n, "generators_t", "p_max_pu", "solar", np.zeros(6))
    n.optimize(solver_name="highs")
    assert n.generators_t.p["solar"].sum() == pytest.approx(0.0)
    assert n.generators_t.p["gas"].sum() > 0

"""
Phase 12f — a non-finite value in an LP bound whose default is FINITE.

linopy does not clamp a bound it cannot read: it MASKS THAT CONSTRAINT ROW OUT
OF THE PROBLEM. Measured on PyPSA 1.3.0 / HiGHS, and the numbers below are the
reason this phase exists:

    p_max_pu = [0.5, NaN, 1.0], p_nom = 100, load 500  ->  [50, 500, 100]
    p_min_pu = [0,   NaN, 0.9], dear unit, cheap slack ->  [0, -900, 90]
    STATIC p_max_pu = NaN, no dynamic column           ->  [500, 500, 500]

The set is exactly the five bounds whose class default is finite. `ramp_limit_*`
is NOT one of them and must never be added: its class default IS NaN and PyPSA
masks the row on purpose (`no_up_limit = limit_up.isnull() & ...`), so a null
ramp limit is the documented way to say "this unit has no ramp limit".
Including it would block every network in this repository — the golden fixture
alone carries eight non-finite `ramp_limit_*` cells and zero on the five.

Every ★ names the broken variant it must fail against, and each was applied and
demonstrated RED before this file was allowed to go green.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

import routers.network as N
import services.validation_service as V
from services.pypsa_service import PyPSAService


def _net(load=500.0, snapshots=3):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=snapshots, freq="h"))
    n.add("Bus", "b")
    n.add("Load", "l", bus="b", p_set=load)
    n.add("Generator", "slack", bus="b", p_nom=5000.0, marginal_cost=999.0)
    return n


# ── K1: clearing a bound writes what PyPSA's own None coercion writes ──────

def test_K1a_clearing_p_max_pu_writes_one_not_nan():
    """★ K1a. `_bulk` mapped a cleared numeric to NaN unless the column ended
    `_max`. For `p_max_pu` that is not "no bound" — PyPSA drops the row, so a
    100 MW unit dispatched 500 MW.

    The fixture's generator carries NO profile: a static write is inert when a
    dynamic column exists (PyPSA prefers `_t`) and `_bulk` does not clear the
    dynamic column, so a profiled asset would pass this for the wrong reason.

    Bite (verified): restore the `float("nan")` fallthrough — `p_max_pu` reads
    NaN and the dispatch is 500.0.
    """
    n = _net()
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0, p_max_pu=0.4)
    assert "g" not in getattr(n.generators_t, "p_max_pu", pd.DataFrame()).columns
    PyPSAService.set_network(n)

    N.bulk_update({"component_class": "Generator", "names": ["g"],
                   "updates": {"p_max_pu": None}})
    live = PyPSAService.get_network()
    assert live.generators.at["g", "p_max_pu"] == 1.0

    live.optimize(solver_name="highs")
    disp = live.generators_t.p["g"].to_list()
    assert all(x <= 100.0 + 1e-6 for x in disp), disp


def test_K1b_storage_p_min_pu_clears_to_minus_one():
    """★ K1b. The mapping is keyed by (component, column), not column alone:
    `StorageUnit.p_min_pu` defaults to **−1.0** (the unit may charge), where a
    Generator's is 0.0. One constant for every bound would silently forbid
    charging. Bite (verified): return 0.0 for every `p_min_pu`."""
    n = _net()
    n.add("StorageUnit", "s", bus="b", p_nom=50.0, max_hours=4.0, p_min_pu=-0.5)
    PyPSAService.set_network(n)
    N.bulk_update({"component_class": "StorageUnit", "names": ["s"],
                   "updates": {"p_min_pu": None}})
    assert PyPSAService.get_network().storage_units.at["s", "p_min_pu"] == -1.0


def test_K1c_clearing_a_ramp_limit_still_writes_nan():
    """★ K1c. The other side of the rule, and the one that keeps this phase
    from blocking every network: `ramp_limit_up`'s class default IS NaN, which
    is PyPSA's documented "no ramp limit"
    (`pypsa/optimization/constraints.py`: `no_up_limit = limit_up.isnull() &
    limit_start.isnull()`). Clearing it must still write NaN, and the network
    must still solve.

    Bite (verified): make `_finite_bound_default` a HARD-CODED mapping and add
    `ramp_limit_*` to the list — it then writes 1.0 and invents a limit the
    user just cleared.

    The first bite tried — adding `ramp_limit_*` to the list alone — did NOT
    bite, and is recorded rather than counted. It cannot: the shipped helper
    reads the value from `n.components[...].defaults`, and `ramp_limit_up`'s
    default IS NaN, so even a wrong membership list writes NaN there. That is
    the implementation being robust, but it means the first bite was testing
    the list where the hazard is the *value source*. The bite above targets
    that source.
    """
    n = _net(load=50.0)
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0,
          ramp_limit_up=0.1, ramp_limit_down=0.1)
    PyPSAService.set_network(n)
    N.bulk_update({"component_class": "Generator", "names": ["g"],
                   "updates": {"ramp_limit_up": None}})
    live = PyPSAService.get_network()
    assert np.isnan(live.generators.at["g", "ramp_limit_up"])
    status, _cond = live.optimize(solver_name="highs")
    assert status == "ok"


def test_K1d_a_non_bound_column_still_clears_to_missing():
    """★ K1d. The rule must not widen. `discount_rate` is deliberately left
    NaN by `tests/golden/fixture.py` — PyPSA's consistency check wants one only
    on assets carrying `overnight_cost`, and the app fills it transiently
    around the solve — so clearing it must still mean *missing*, not a
    fabricated 0.0. Bite (verified): apply the finite-default branch to every
    numeric column."""
    n = _net()
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0,
          discount_rate=0.07)
    PyPSAService.set_network(n)
    N.bulk_update({"component_class": "Generator", "names": ["g"],
                   "updates": {"discount_rate": None}})
    assert np.isnan(PyPSAService.get_network().generators.at["g", "discount_rate"])


def test_K1e_the_existing_max_to_inf_rule_is_untouched():
    """The pre-12f behaviour this change sits beside: a cleared `*_max` is
    `inf`, which is PyPSA's own "no bound" for those."""
    n = _net()
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0,
          p_nom_max=200.0)
    PyPSAService.set_network(n)
    N.bulk_update({"component_class": "Generator", "names": ["g"],
                   "updates": {"p_nom_max": None}})
    assert PyPSAService.get_network().generators.at["g", "p_nom_max"] == float("inf")


# ── K4: preflight names what is already there ─────────────────────────────

def test_K4a_a_nan_static_bound_is_an_error_naming_the_asset():
    """★ K4a. A NaN STATIC `p_max_pu` with no dynamic column dispatches five
    times nameplate in EVERY hour — the worst shape, because nothing about the
    network looks unusual. Bite (verified): scan the dynamic frame only."""
    n = _net()
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0)
    n.generators.at["g", "p_max_pu"] = float("nan")
    issues = V._check_nonfinite_bounds(n)
    assert [i.code for i in issues] == ["nonfinite_bound"], issues
    assert issues[0].severity == "error"
    assert issues[0].name == "g"
    assert "p_max_pu" in issues[0].message


def test_K4b_a_partial_dynamic_column_is_a_coverage_error():
    """★ K4b. The commoner shape, and it must not read as an accusation: a
    user uploads a representative week and extends the horizon. The message
    says how much is covered. Bite (verified): scan the static frame only."""
    n = _net()
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0)
    n.generators_t.p_max_pu["g"] = [0.5, np.nan, 1.0]
    issues = V._check_nonfinite_bounds(n)
    assert [i.code for i in issues] == ["nonfinite_bound_partial_coverage"], issues
    assert issues[0].severity == "error"
    assert "covers 2 of 3 snapshots" in issues[0].message


@pytest.mark.parametrize("attr", ["p_min_pu", "s_max_pu", "e_max_pu", "e_min_pu"])
def test_K4b2_the_other_four_bounds_are_covered_too(attr):
    """The set is five, not one. `s_max_pu` matters twice over: it masks the
    `-lower` row as well as the `-upper`."""
    n = _net()
    if attr in ("p_min_pu",):
        n.add("Generator", "g", bus="b", p_nom=100.0)
        n.generators.at["g", attr] = float("nan")
        who = "Generator"
    elif attr == "s_max_pu":
        n.add("Bus", "b2")
        n.add("Line", "g", bus0="b", bus1="b2", s_nom=100.0, x=0.1)
        n.lines.at["g", attr] = float("nan")
        who = "Line"
    else:
        n.add("Store", "g", bus="b", e_nom=100.0)
        n.stores.at["g", attr] = float("nan")
        who = "Store"
    issues = V._check_nonfinite_bounds(n)
    assert [i.code for i in issues] == ["nonfinite_bound"], (attr, issues)
    assert issues[0].component_class == who


def test_K4c_preflight_is_silent_on_the_golden_network():
    """★ K4c — the regression that five rejected designs would have broken.

    The golden fixture carries **eight** non-finite static cells, every one a
    `ramp_limit_up`/`down` on gas, solar, diesel_backup and electrolyzer, and
    **zero** on the five. An earlier version of this phase errored on any
    non-finite bound; it would have stopped the golden network — and, measured
    across the suite, 68 of 68 solving networks — from solving at all.

    Bite (verified): add `ramp_limit_up`/`down` to `FINITE_DEFAULT_BOUNDS` —
    eight errors on a network that solves today.
    """
    from tests.golden import fixture as gf
    g = gf.build_golden_network()

    ramp_cells = sum(
        int((~np.isfinite(g.static(c)[a].to_numpy(dtype=float))).sum())
        for c in ("Generator", "Link")
        for a in ("ramp_limit_up", "ramp_limit_down")
        if a in g.static(c).columns
    )
    assert ramp_cells == 8, ramp_cells          # the fixture really is this shape
    assert V._nonfinite_bound_hits(g) == []
    assert V._check_nonfinite_bounds(g) == []


def test_K4d_a_nan_ramp_limit_is_never_an_error():
    """★ K4d. Stated as its own test rather than left implicit in K4c, because
    it is the single rule that keeps this check shippable. Bite (verified):
    same as K4c's."""
    n = _net(load=50.0)
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0)
    n.generators.at["g", "ramp_limit_up"] = float("nan")
    n.generators_t.ramp_limit_down = pd.DataFrame(
        {"g": [0.1, np.nan, 0.1]}, index=n.snapshots)
    assert V._check_nonfinite_bounds(n) == []


def test_K4e_a_clean_network_is_silent():
    """The anti-vacuity floor: the check must be able to say nothing."""
    n = _net(load=50.0)
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0)
    n.generators_t.p_max_pu["g"] = [0.5, 0.6, 0.7]
    assert V._check_nonfinite_bounds(n) == []

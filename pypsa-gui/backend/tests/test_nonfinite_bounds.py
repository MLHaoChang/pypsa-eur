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


# ── K2: a time series with a non-finite cell is refused at the write ───────

def _idx(n):
    return [str(x) for x in n.snapshots]


def test_K2a_a_null_in_a_put_body_is_422_naming_the_column_and_row():
    """★ K2a. JSON `null` becomes NaN the moment pandas builds the frame, and
    the route wrote it straight into the store. Bite (verified): drop the
    validator call — the write succeeds and the LP is left unbounded there."""
    from fastapi import HTTPException
    n = _net(load=50.0)
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0)
    PyPSAService.set_network(n)
    with pytest.raises(HTTPException) as e:
        N.set_timeseries("generators", "p_max_pu",
                         {"index": _idx(n), "columns": ["g"],
                          "data": [[0.5], [None], [1.0]]})
    assert e.value.status_code == 422
    detail = str(e.value.detail)
    assert "p_max_pu" in detail and "'g'" in detail
    assert "2030-01-01" in detail          # it names WHERE, not just that


def test_K2b_infinity_is_refused_too():
    """★ K2b. `json.loads` accepts the bare `Infinity` literal, so a client can
    send one; `inf` masks a row exactly as NaN does. Bite (verified): test
    `isnan` instead of `isfinite` — the infinity sails through."""
    from fastapi import HTTPException
    n = _net(load=50.0)
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0)
    PyPSAService.set_network(n)
    with pytest.raises(HTTPException) as e:
        N.set_timeseries("generators", "p_max_pu",
                         {"index": _idx(n), "columns": ["g"],
                          "data": [[0.5], [float("inf")], [1.0]]})
    assert e.value.status_code == 422


def test_K2c_the_guard_is_not_a_wall_a_finite_series_still_writes():
    """★ K2c. The anti-vacuity floor — a refusal that refuses everything is
    not a guard. Bite (verified): raise unconditionally."""
    n = _net(load=50.0)
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0)
    PyPSAService.set_network(n)
    N.set_timeseries("generators", "p_max_pu",
                     {"index": _idx(n), "columns": ["g"],
                      "data": [[0.5], [0.6], [0.7]]})
    assert PyPSAService.get_network().generators_t.p_max_pu["g"].to_list() \
        == [0.5, 0.6, 0.7]


def test_K2d_the_profile_upload_path_refuses_before_user_ts_is_written():
    """★ K2d. The profile routes are a SECOND write path — a CSV's blank cell
    is a NaN by the time `read_csv` returns — and what they write lands in
    `_user_ts`, which survives project reload and is re-injected on every
    solve. So a rejected upload must leave nothing behind.

    Bite (verified): guard `set_timeseries` only; this path stays green and a
    NaN reaches `_user_ts`.
    """
    from fastapi import HTTPException
    n = _net(load=50.0)
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0)
    PyPSAService.set_network(n)
    live = PyPSAService.get_network()
    before = dict(N._user_ts)
    bad = pd.DataFrame({"g": [0.5, np.nan, 0.7]}, index=live.snapshots)
    with pytest.raises(HTTPException) as e:
        N._apply_profile_upload(live, "generators", "p_max_pu", "Generator", bad)
    assert e.value.status_code == 422
    assert dict(N._user_ts) == before, "a refused upload wrote to _user_ts"

    ok = pd.DataFrame({"g": [0.5, 0.6, 0.7]}, index=live.snapshots)
    out = N._apply_profile_upload(live, "generators", "p_max_pu", "Generator", ok)
    assert out["matched"] == ["g"]


def test_K2e_every_dynamic_column_is_checked_not_only_the_bounds():
    """★ K2e. `upload_load_profile` writes `p_set`, which is not a bound at all
    — and a non-finite demand hour masks that snapshot's nodal balance. The
    validator therefore checks every column it is given, not a bound list.
    Bite (verified): restrict the validator to `FINITE_DEFAULT_BOUNDS`."""
    from fastapi import HTTPException
    n = _net(load=50.0)
    PyPSAService.set_network(n)
    with pytest.raises(HTTPException) as e:
        N.set_timeseries("loads", "p_set",
                         {"index": _idx(n), "columns": ["l"],
                          "data": [[50.0], [None], [50.0]]})
    assert e.value.status_code == 422


# ── K6: both loops refuse up front rather than burning their budget ────────

def _margin_ready_network():
    """A network the margin loop would otherwise accept, so that the refusal
    under test is the one this phase adds.

    EVERY generator carries outage data, the slack included: without that the
    pre-existing `reserve_margin_unpriceable_assets` error fires first and the
    test passes for the wrong reason — which is exactly what the first version
    of this fixture did.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.add("Bus", "b")
    n.add("Load", "l", bus="b", p_set=50.0)
    n.add("Carrier", "gas")
    for name, p_nom, cost in (("g", 100.0, 10.0), ("slack", 5000.0, 999.0)):
        n.add("Generator", name, bus="b", p_nom=p_nom, marginal_cost=cost,
              carrier="gas", outage_rate_value=0.05,
              outage_rate_basis="EFORd", mttr_hours=24.0)
    n.generators_t.p_max_pu["g"] = [1.0, np.nan, 1.0]
    return n


@pytest.mark.parametrize("which", ["coupling", "margin"])
def test_K6a_a_loop_refuses_a_nan_bound_up_front(which):
    """★ K6a. Every iterate of either loop calls `run_simulation`, which now
    refuses a non-finite LP bound — so without an up-front check the loop
    spends its whole budget failing identically and ends `budget_exhausted`,
    advising "Raise max_solves" (coupling) or the margin equivalent. That
    advice can never work: no number of solves fixes a NaN.

    `_margin_out_of_reach` cannot save this — it relabels `validation_failed`
    only when the MARGIN is the cause, which it is not here.

    Bite (verified): drop the up-front check from the loop under test; the
    other loop stays green, which is why this is parametrised.
    """
    import routers.results as R
    from fastapi import HTTPException
    from routers.simulation import _state

    n = _margin_ready_network()
    PyPSAService.set_network(n)
    _state["solver_config"] = _cfg_with_margin()
    # Isolate the mesh: if a previous case's loop actually STARTED (which is
    # what the bite for the other loop causes), the study it holds would make
    # this one refuse 409 "a study is running" and the test would pass for the
    # wrong reason. Each case starts with an empty mesh and leaves one.
    for _k in ("coupling_loop", "margin_loop", "mc", "frontier", "fmea_sweep"):
        _state.pop(_k, None)

    try:
        with pytest.raises(HTTPException) as e:
            if which == "coupling":
                R.post_coupling_loop(body=R.CouplingLoopRequest(target_lole_h=1.0))
            else:
                R.post_margin_loop(body=R.MarginLoopRequest(target_lole_h=1.0))
        assert e.value.status_code == 422
        assert "p_max_pu" in str(e.value.detail)
    finally:
        # In a `finally`, because the whole point of the bite is that the call
        # does NOT raise — it starts a real study instead. Cleaning up only on
        # the happy path leaks that study into the next parametrised case,
        # which then refuses 409 and fails for the wrong reason.
        for _k in ("coupling_loop", "margin_loop", "mc", "frontier",
                   "fmea_sweep"):
            rec = _state.pop(_k, None)
            th = (rec or {}).get("thread") if isinstance(rec, dict) else None
            if th is not None:
                ev = rec.get("stop_event")
                if ev is not None:
                    ev.set()
                th.join(timeout=30)


def _cfg_with_margin():
    from services.solver_service import SolverConfig
    return SolverConfig(solver_name="highs", voll=3000.0, reserve_margin=0.1)

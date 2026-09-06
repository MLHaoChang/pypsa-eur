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


# ── R: the shipped-code review of 12f, eight findings, each pinned ─────────
#
# The review found the walk falling back to the deprecated call on every
# invocation, a static NaN flagged under a finite profile that shadows it, the
# write boundary refusing NaN where PyPSA's own default IS NaN, a literal
# `NaN`/`Infinity` reaching `_bulk` and the create schemas past the `null`
# branch, a duplicated column label turning the 422 into a 500, and the two
# raising checkpoints reporting a user refusal as a crash. And that the wiring
# — the one line in `_check_lopf` and the three solver checkpoints — had no
# test at all: deleting all four left the file green.


def test_R1_a_static_nan_under_a_finite_profile_is_inert_and_not_flagged():
    """★ R1. PyPSA reads `_t` before the static cell, so a static NaN under a
    finite dynamic column is never seen by the LP (measured: `[0.5, 0.6, 0.7]`
    dispatched, not 500). Every project in which pre-12f `_bulk` cleared a
    profiled asset's bound carries exactly this shape, and it must still
    solve. Bite (verified): drop the `dyn_names` skip in the static branch."""
    n = _net(load=50.0)
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0)
    n.generators.at["g", "p_max_pu"] = np.nan
    n.generators_t.p_max_pu["g"] = [0.5, 0.6, 0.7]
    assert V._nonfinite_bound_hits(n) == []
    assert V._check_nonfinite_bounds(n) == []
    # and the profile itself is still judged: a NaN hour in it is the K4b case
    n.generators_t.p_max_pu["g"] = [0.5, np.nan, 0.7]
    assert [i.code for i in V._check_nonfinite_bounds(n)] \
        == ["nonfinite_bound_partial_coverage"]


def test_R2_a_ghost_column_is_not_an_asset():
    """★ R2. `set_timeseries` never filters columns to the component index, so
    a column named for no asset can exist. PyPSA warns and ignores it — the
    solve is `optimal` and unchanged — so a NaN there masks nothing, and the
    "View" jump for a named asset that does not exist goes nowhere. Bite
    (verified): drop the `static_names` skip in the dynamic branch."""
    n = _net(load=50.0)
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0)
    n.generators_t.p_max_pu["ghost"] = [0.5, np.nan, 0.7]
    assert V._nonfinite_bound_hits(n) == []


def test_R3_the_walk_uses_the_supported_api_and_never_goes_quiet():
    """★ R3. The first walk was `[n.components[c] for c in n.components]`,
    which iterates the `Components` OBJECTS (unhashable) — it raised
    `TypeError` on every call and the deprecated `iterate_components` fallback
    ran instead, with its DeprecatedWarning, the opposite of what the commit
    claimed. On a PyPSA that removes the fallback the nested `except` then
    returned `[]`: the check would have vanished silently. Two bites
    (verified): restore the object-iterating walk — the warning is emitted;
    restore the `return hits` — the bogus object yields `[]` instead of
    raising."""
    import warnings
    n = _net(load=50.0)
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0)
    n.generators.at["g", "p_max_pu"] = np.nan
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        hits = V._nonfinite_bound_hits(n)
    assert hits == [("Generator", "p_max_pu", "g", 1, 1)]
    assert not [x for x in w if "iterate_components" in str(x.message)], \
        [str(x.message) for x in w]

    class _NotANetwork:
        snapshots = None

    with pytest.raises(Exception):
        V._nonfinite_bound_hits(_NotANetwork())


@pytest.mark.parametrize("component,attribute,factory", [
    ("generators", "ramp_limit_up",
     lambda n: n.add("Generator", "g", bus="b", p_nom=100.0)),
    ("generators", "p_set",
     lambda n: n.add("Generator", "g", bus="b", p_nom=100.0)),
    ("storage_units", "state_of_charge_set",
     lambda n: n.add("StorageUnit", "g", bus="b", p_nom=100.0, max_hours=4.0)),
])
def test_R4_a_nan_where_pypsa_s_own_default_is_nan_is_accepted(
        component, attribute, factory):
    """★ R4. The write boundary follows the SAME rule as the preflight: NaN is
    corrupt only where the class default is finite. For these three the
    default IS NaN and it means "not fixed at this hour" — a `p_set` of
    `[NaN, 20, NaN]` fixes dispatch at hour two only, measured `optimal` with
    dispatch `[50, 20, 50]`. The first validator refused every column and so
    made all three unwritable. Bite (verified): drop the
    `_attribute_default_is_finite` gate — every case reads 422."""
    n = _net(load=50.0)
    factory(n)
    PyPSAService.set_network(n)
    N.set_timeseries(component, attribute,
                     {"index": _idx(n), "columns": ["g"],
                      "data": [[None], [20.0], [None]]})
    got = getattr(PyPSAService.get_network(), f"{component}_t")[attribute]["g"]
    assert np.isnan(got.iloc[0]) and got.iloc[1] == 20.0


def test_R4b_load_p_set_is_still_refused_its_default_is_zero():
    """R4's other edge, so the gate cannot be satisfied by passing everything:
    `Load.p_set` defaults to 0.0, a NaN demand hour masks the nodal balance,
    and K2e's refusal must survive the narrowing."""
    assert N._attribute_default_is_finite("loads", "p_set") is True
    assert N._attribute_default_is_finite("Load", "p_set") is True
    assert N._attribute_default_is_finite("generators", "p_set") is False
    assert N._attribute_default_is_finite("generators", "ramp_limit_up") is False
    # unknown attribute or component: refuse, as before
    assert N._attribute_default_is_finite("generators", "no_such_attr") is True
    assert N._attribute_default_is_finite("no_such_component", "p_set") is True


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "Infinity", "nan"])
def test_R5_a_non_finite_literal_in_bulk_is_refused_not_written(value):
    """★ R5. `json.loads` accepts the bare `NaN` and `Infinity` literals and
    `float()` accepts the strings, so a non-finite value reached one of the
    five past the `null` branch and was WRITTEN (measured: `p_max_pu = inf`).
    The preflight caught it at solve, which is the 200-then-refused UX this
    phase removed for `PUT /timeseries`. Bite (verified): drop the `isfinite`
    check after `float(value)`."""
    from fastapi import HTTPException
    n = _net()
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0)
    PyPSAService.set_network(n)
    with pytest.raises(HTTPException) as e:
        N.bulk_update({"component_class": "Generator", "names": ["g"],
                       "updates": {"p_max_pu": value}})
    assert e.value.status_code == 422
    assert PyPSAService.get_network().generators.at["g", "p_max_pu"] == 1.0


def test_R5b_the_literal_rule_does_not_widen_past_the_five():
    """`discount_rate` may legitimately be NaN (K1d), and it must stay
    writable as such through the literal path too."""
    n = _net()
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0,
          discount_rate=0.07)
    PyPSAService.set_network(n)
    N.bulk_update({"component_class": "Generator", "names": ["g"],
                   "updates": {"discount_rate": float("nan")}})
    assert np.isnan(PyPSAService.get_network().generators.at["g", "discount_rate"])


@pytest.mark.parametrize("schema,field", [
    ("GeneratorCreate", "p_max_pu"), ("GeneratorCreate", "p_min_pu"),
    ("LinkCreate", "p_max_pu"), ("StoreCreate", "e_min_pu"),
])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), "inf"])
def test_R6_the_create_schemas_refuse_a_non_finite_bound(schema, field, value):
    """★ R6. `POST`/`PUT` on a component go through the pydantic schema, whose
    plain `float` accepted `NaN`/`Infinity` and wrote `inf` straight into the
    static frame (measured). `allow_inf_nan=False` makes that a 422 at the
    boundary. Bite (verified): drop the `Field(allow_inf_nan=False)`."""
    import pydantic
    from models import schemas as S
    cls = getattr(S, schema)
    base = {"name": "x", "bus": "b"}
    if schema == "LinkCreate":
        base = {"name": "x", "bus0": "b", "bus1": "c"}
    with pytest.raises(pydantic.ValidationError):
        cls.model_validate({**base, field: value})
    # and the finite value still validates
    assert getattr(cls.model_validate({**base, field: 0.5}), field) == 0.5


def test_R6b_the_schema_refusal_reaches_the_client_as_a_422_not_a_500(client):
    """★ R6b. Found live, not by the review: the schema refused `Infinity`
    correctly and the client still saw a **500**, because pydantic echoes the
    offending `input` in its error list and starlette's JSON encoder refuses
    to write `inf` (`Out of range float values are not JSON compliant`) while
    building the 422. The app now owns the `RequestValidationError` handler
    and renders a non-finite input as its repr. Bite (verified): remove the
    handler — the TestClient surfaces the encoder's ValueError."""
    import json as _json
    # raw body: httpx encodes `json=` with allow_nan=False and would refuse
    # to SEND the literal, so the app would never see it. `json.dumps` with
    # its default emits the bare `Infinity` the browser's JSON.stringify never
    # does but a scripted client can.
    r = client.post("/api/network/generators",
                    content=_json.dumps({"name": "g_inf", "bus": "b",
                                         "p_nom": 10.0,
                                         "p_max_pu": float("inf")}),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 422, (r.status_code, r.text[:200])
    body = r.json()
    assert isinstance(body.get("detail"), list) and body["detail"]
    assert any("p_max_pu" in str(e.get("loc")) for e in body["detail"]), body
    assert any(e.get("input") == "inf" for e in body["detail"]), body


def test_R7_a_duplicated_column_label_is_a_422_not_a_500():
    """★ R7. `df[col]` on a duplicated label is a DataFrame, and the row
    lookup then raised IndexError — a 500 where the user is owed a 422. Only
    the JSON PUT can send one (`read_csv` mangles duplicates). Bite
    (verified): drop the `is_unique` guard — IndexError escapes."""
    from fastapi import HTTPException
    n = _net(load=50.0)
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0)
    PyPSAService.set_network(n)
    with pytest.raises(HTTPException) as e:
        N.set_timeseries("generators", "p_max_pu",
                         {"index": _idx(n), "columns": ["g", "g"],
                          "data": [[0.5, 0.5], [None, 0.6], [1.0, 1.0]]})
    assert e.value.status_code == 422
    assert "duplicate" in str(e.value.detail)


def test_R8_the_preflight_wiring_reaches_validate_for_run():
    """★ R8. Every K4 case called the helper directly, so the one line that
    puts it in `_check_lopf` had no witness. Bite (verified): delete
    `out += _check_nonfinite_bounds(n)` — the code is absent here while every
    K4 case stays green."""
    from services.solver_service import SolverConfig
    n = _net(load=50.0)
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0)
    n.generators_t.p_max_pu["g"] = [0.5, np.nan, 1.0]
    codes = [i.code for i in V.validate_for_run(n, SolverConfig())]
    assert "nonfinite_bound_partial_coverage" in codes, codes
    assert V.has_errors(V.validate_for_run(n, SolverConfig()))


def _two_period_network_with_flat_profile():
    """MultiIndex snapshots with a FLAT `_t` frame: finite at preflight,
    all-NaN after the pre-LP reindex — the fixture plan v6's amendment 9
    named as the one that separates the check points."""
    H = 3
    n = pypsa.Network()
    sns = pd.MultiIndex.from_product(
        [[2030, 2035], pd.date_range("2030-01-01", periods=H, freq="h")],
        names=["period", "timestep"])
    n.set_snapshots(sns)
    n.investment_periods = [2030, 2035]
    n.add("Carrier", "gas")
    n.add("Bus", "b")
    n.add("Load", "l", bus="b", p_set=50.0)
    for name, p_nom, cost in (("g", 100.0, 10.0), ("slack", 5000.0, 999.0)):
        n.add("Generator", name, bus="b", carrier="gas", p_nom=p_nom,
              marginal_cost=cost, build_year=2000, lifetime=100)
    flat = pd.DataFrame({"g": [0.5, 0.6, 0.7]},
                        index=pd.date_range("2030-01-01", periods=H, freq="h"))
    n.generators_t["p_max_pu"] = flat
    return n


def _run(n, cfg, *, foreground=True):
    """Drive `run_simulation` on ``n``. With ``foreground=False`` a different
    network is the active one, which is the background-project-solve path:
    `_reapply_user_ts_to_network` is skipped there."""
    import queue
    import threading
    from services.solver_service import run_simulation
    PyPSAService.set_network(n if foreground else _net(load=50.0))
    with N._user_ts_lock:
        saved = dict(N._user_ts)
        N._user_ts.clear()
    lq: queue.SimpleQueue = queue.SimpleQueue()
    try:
        status, cond = run_simulation(cfg, n, PyPSAService.get_lock(),
                                      threading.Event(), lq)
    finally:
        with N._user_ts_lock:
            N._user_ts.clear()
            N._user_ts.update(saved)
    lines = []
    while True:
        try:
            lines.append(str(lq.get_nowait()))
        except Exception:                                     # noqa: BLE001
            break
    return status, cond, lines


def test_R9_run_simulation_refuses_the_nan_the_reindex_manufactures():
    """★ R9. The gate commit cca46ac added exists for: a flat `_t` frame
    against MultiIndex snapshots is finite when `validate_for_run` looks and
    all-NaN after `_normalise_dynamic_indexes`. Nothing exercised
    `run_simulation` end to end.

    Measured while writing this: on the FOREGROUND network the fixture never
    reaches the checkpoint, because `_reapply_user_ts_to_network` — which
    runs there even with `_user_ts` empty — broadcasts the flat frame over
    the periods correctly. The NaN reaches the LP on a network the reapply
    skips: a background project solve (this fixture) or an adequacy
    transient.

    Two bites, because the first attempt did not bite: deleting the first
    checkpoint alone left this green — the SECOND checkpoint is a backstop
    and refused the same NaN after the modelling step, reporting identically.
    So this asserts what the first checkpoint is FOR: the refusal happens
    before modelling assumptions are applied, so there is nothing to unwind.
    Bites (verified): delete the first checkpoint — the refusal is now
    reported "after modelling assumptions"; delete both — the solve proceeds
    against the masked bound and returns `optimal`."""
    from services.solver_service import SolverConfig
    n = _two_period_network_with_flat_profile()
    assert V._check_nonfinite_bounds(n) == []          # finite at preflight
    cfg = SolverConfig(multi_investment_periods=True)
    status, cond, lines = _run(n, cfg, foreground=False)
    assert (status, cond) == ("error", "validation_failed"), (status, cond)
    assert any("[VALIDATION] ERROR: Generator 'g'" in ln for ln in lines), lines
    assert not [ln for ln in lines if ln.startswith("TRACEBACK")]
    # refused BEFORE the modelling step, by the first checkpoint
    assert any("Validation failed: 1 error(s). Aborting." in ln for ln in lines), lines
    assert not [ln for ln in lines if "after modelling assumptions" in ln], lines


def test_R9b_a_refusal_after_modelling_is_reported_as_one_not_as_a_crash(
        monkeypatch):
    """★ R9b. The second and myopic checkpoints RAISE (the modelling
    transforms must be unwound), and the first version raised a bare
    `ValueError`: the generic handler dumped a `TRACEBACK:` block and set
    `condition` to the whole sentence. No legal input reaches the second
    checkpoint with a NaN the first did not already catch — measured: the
    cfg-only period promotion broadcasts the profile correctly — so this
    fixture makes the check fire on its SECOND call, and asserts the
    handler's contract: `validation_failed` exactly, the asset named the way
    preflight names it, no traceback, and the network restored. Bite
    (verified): raise `ValueError` again — the traceback is back and the
    condition is the sentence."""
    import services.solver_service as SS
    from services.solver_service import SolverConfig
    real = SS._check_nonfinite_bounds
    calls = {"n": 0}

    def fire_on_second(network):
        calls["n"] += 1
        if calls["n"] == 2:
            return [V._err("nonfinite_bound", "Generator", "g",
                           "Generator 'g': 'p_max_pu' is not a finite number")]
        return real(network)

    monkeypatch.setattr(SS, "_check_nonfinite_bounds", fire_on_second)
    n = _net(load=50.0)
    n.add("Carrier", "gas")
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=100.0,
          marginal_cost=10.0)
    n_gens_before = len(n.generators)
    status, cond, lines = _run(n, SolverConfig())
    assert calls["n"] >= 2, calls
    assert (status, cond) == ("error", "validation_failed"), (status, cond)
    assert any("[VALIDATION] ERROR: Generator 'g'" in ln for ln in lines), lines
    assert not [ln for ln in lines if ln.startswith("TRACEBACK")], lines
    assert len(PyPSAService.get_network().generators) == n_gens_before


# ── R10: the fixes reviewed in turn — coverage is judged against the horizon ─


def test_R10a_a_partial_index_frame_is_a_coverage_error():
    """★ R10a. Found by the review of the fix round. `set_timeseries` writes
    the body's own index, and the scanner judged only the cells the frame
    held — so a 2-of-3-snapshot PUT passed preflight while PyPSA reads NaN
    for the missing hour (measured dispatch `[50, 60, 500]`). Inside
    `run_simulation` the normalise step reindexes first and the checkpoint
    catches it; the Validate panel and both loops' up-front guards judge the
    raw network and were silent. Bite (verified): judge `frame[name]` as it
    stands instead of reindexed onto the snapshots."""
    n = _net(load=500.0)
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0)
    n.generators_t["p_max_pu"] = pd.DataFrame({"g": [0.5, 0.6]},
                                              index=n.snapshots[:2])
    issues = V._check_nonfinite_bounds(n)
    assert [i.code for i in issues] == ["nonfinite_bound_partial_coverage"], issues
    assert "covers 2 of 3 snapshots" in issues[0].message


def test_R10b_a_zero_row_frame_does_not_hide_a_static_nan():
    """★ R10b. The static skip R1 added made a static NaN silent under a
    dynamic column with NO rows — PyPSA reads that as NaN in every hour
    (measured dispatch `[500, 500, 500]`), the pre-12f `_bulk`-cleared shape
    with an emptied profile. Judged against the horizon, the empty column
    is "not finite in 3 of 3 snapshots". Bite (verified): as R10a."""
    n = _net(load=500.0)
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0)
    n.generators.at["g", "p_max_pu"] = np.nan
    n.generators_t["p_max_pu"] = pd.DataFrame({"g": []},
                                              index=pd.DatetimeIndex([]))
    issues = V._check_nonfinite_bounds(n)
    assert [i.code for i in issues] == ["nonfinite_bound"], issues
    assert "3 of 3 snapshots" in issues[0].message


def test_R10c_a_flat_frame_on_a_multi_period_network_is_read_as_the_reapply_reads_it():
    """★ R10c. PyPSA's own `as_dense` reads a flat frame against MultiIndex
    snapshots as all-NaN, but the foreground solve runs the reapply first,
    which broadcasts it by timestep — so a flat frame that covers the
    timesteps is in force and clean, and one missing a timestep is short
    by that hour in EVERY period. Judging by plain `reindex` would refuse
    at preflight the very network the solve would have run. Bite
    (verified): replace the timestep broadcast with `reindex(snaps)` — the
    clean case reads "0 of 6"."""
    n = _two_period_network_with_flat_profile()          # covers all 3 stamps
    assert V._check_nonfinite_bounds(n) == []
    short = pd.DataFrame({"g": [0.5, 0.6]},
                         index=pd.date_range("2030-01-01", periods=2, freq="h"))
    n.generators_t["p_max_pu"] = short
    issues = V._check_nonfinite_bounds(n)
    assert [i.code for i in issues] == ["nonfinite_bound_partial_coverage"], issues
    assert "covers 4 of 6 snapshots" in issues[0].message


def test_R10d_a_multi_index_frame_on_a_flat_network_is_not_in_force():
    """R10d. The reapply DROPS a MultiIndex frame on a flat network, so it
    shadows nothing: a static NaN beneath it is reported, and its own cells
    are not judged."""
    n = _net(load=500.0)
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0)
    n.generators.at["g", "p_max_pu"] = np.nan
    mi = pd.MultiIndex.from_product([[2030], n.snapshots],
                                    names=["period", "timestep"])
    n.generators_t["p_max_pu"] = pd.DataFrame({"g": [0.5, np.nan, 0.7]}, index=mi)
    hits = V._nonfinite_bound_hits(n)
    assert hits == [("Generator", "p_max_pu", "g", 1, 1)], hits


def test_R10e_a_ghost_column_on_an_asset_less_component_is_still_a_ghost():
    """★ R10e. R2's guard read `if static_names and ...`, so an EMPTY static
    index disabled it and a ghost column on a component with no assets was
    named as one. Bite (verified): restore the truthiness test."""
    n = _net(load=50.0)
    assert len(n.stores) == 0
    n.stores_t["e_max_pu"] = pd.DataFrame({"ghost": [1.0, np.nan, 1.0]},
                                          index=n.snapshots)
    assert V._nonfinite_bound_hits(n) == []

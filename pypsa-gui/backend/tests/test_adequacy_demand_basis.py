"""
Phase 12c-0 — one demand basis for every adequacy engine (the fifteenth
finding; plan 2026-09-03-fmea-phase12c-portfolio-elcc-v3.md §1 as amended
by v3.1 A1/A2).

``load_scalers`` are applied to ``loads_t.p_set`` in place inside
``_apply_modelling_assumptions`` and reverted after the LP, so the LP, the
ENS cap and the reserve-margin constraint saw scaled demand while
``snapshot_inputs``, ``fleet_and_residual`` and the route-side
``reserve_margin_facts`` — and therefore both certifying loops — read the raw
series. Measured before the fix (v3 review probe): route-side peaks 179.93 in
both periods against the wrapper's stashed 224.92 in 2035 under a 1.25
scaler; the post-solve snapshot equal to the pre-solve raw residual.

Every ★ names its bite; D1 is a regression ANCHOR (its "bite" — multiplying by
1.0 — is exact in IEEE-754 and cannot bite; recorded as such by the review).
"""
from __future__ import annotations

import hashlib
import threading
import time

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.adequacy import demand as D
from services.adequacy.copt import fleet_and_residual
from services.adequacy.mc import snapshot_inputs
from services.solver_service import (
    SolverConfig,
    _apply_modelling_assumptions,
    reserve_margin_facts,
)

H = 24


def _h(a) -> str:
    return hashlib.sha256(
        np.ascontiguousarray(a, dtype=np.float64).tobytes()).hexdigest()


def two_period_network(*, static_load: bool = False) -> pypsa.Network:
    """The v3 review's fixture: two 24 h periods, one series load (or one
    static load), a firm base unit, a wind farm with a profile, an
    extendable peaker with outage data."""
    n = pypsa.Network()
    sns = pd.MultiIndex.from_product(
        [[2030, 2035], pd.date_range("2030-01-01", periods=H, freq="h")],
        names=["period", "timestep"])
    n.set_snapshots(sns)
    n.investment_periods = [2030, 2035]
    n.add("Carrier", "gas")
    n.add("Carrier", "wind")
    n.add("Bus", "b", carrier="AC")
    if static_load:
        n.add("Load", "l", bus="b", p_set=150.0)
    else:
        prof = 120.0 + 60.0 * np.sin(np.linspace(0, 2 * np.pi, 2 * H)) ** 2
        n.add("Load", "l", bus="b", p_set=prof)
    n.add("Generator", "base", bus="b", carrier="gas", p_nom=200.0,
          marginal_cost=10.0, build_year=2000, lifetime=100,
          outage_rate_value=0.05, outage_rate_basis="FOR", mttr_hours=24.0)
    n.add("Generator", "wind", bus="b", carrier="wind", p_nom=100.0,
          marginal_cost=0.0, build_year=2000, lifetime=100)
    wp = np.clip(0.5 + 0.5 * np.cos(np.linspace(0, 4 * np.pi, 2 * H)), 0, 1)
    n.generators_t.p_max_pu["wind"] = wp
    n.add("Generator", "peaker", bus="b", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=500.0, capital_cost=5e6,
          marginal_cost=500.0, build_year=2000, lifetime=100,
          outage_rate_value=0.05, outage_rate_basis="FOR", mttr_hours=24.0)
    return n


def _per_period(arr, n):
    lvl = np.asarray(n.snapshots.get_level_values(0))
    return {p: np.asarray(arr, dtype=float)[lvl == p] for p in (2030, 2035)}


# ── D1: regression anchor, pinned on 52d4244 (the pre-change code) ──────

D1_MC_RESIDUAL = "2f328808042a94140c1238749e2bccc8041edf435f8444bde4d6a714130d3e06"
D1_COPT_RESIDUAL = D1_MC_RESIDUAL      # one construction, one hash
D1_STASH_DEMAND = {
    "2030": "11f8b4476be30b970982db4b29a5a2b9db2f38c2984f33581801f6b046e7f28d",
    "2035": "c6d7c3746ff863381064a997e379b987db18b3ed3da3e45bec626a07295e317a",
}


def test_D1_without_scalers_every_engine_is_bit_identical_to_before():
    """D1 (anchor). With no scaler configured the MC residual, the COPT
    residual and the margin stash demand hash to the values pinned on the
    code before Phase 12c-0. Not a bitten ★: ×1.0 is exact, so the only
    variant that could move these is a reordered sum over three or more
    loads, which this fixture does not have."""
    n = two_period_network()
    cfg = SolverConfig(multi_investment_periods=True, reserve_margin=0.15)
    inp = snapshot_inputs(n, cfg=cfg)
    _u, res, _w = fleet_and_residual(n, cfg=cfg)
    facts = reserve_margin_facts(n, cfg)
    assert _h(inp.residual) == D1_MC_RESIDUAL
    assert _h(res.to_numpy(dtype=float)) == D1_COPT_RESIDUAL
    for P, per in facts["stash"]["periods"].items():
        assert _h(per["demand_mw"].to_numpy(dtype=float)) == D1_STASH_DEMAND[P], P
    # …and the no-scaler path hands back the frame ITSELF, no copy.
    assert D.lp_demand_frame(n, cfg) is n.loads_t.p_set
    assert D.load_scale_factors(n, cfg) == []


# ── D2: a scaler moves the numbers in its period only, on all three ─────

def test_D2_a_scaler_moves_every_engine_in_its_period_only():
    """★ D2. `load_scalers = {"2035": 1.25}` on a SERIES load: the MC
    residual, the COPT residual and the margin `peak_mw` are ×1.25 in 2035
    and unchanged in 2030, on all three surfaces — the fifteenth finding
    closed.

    Bites (verified): apply the factor to every period; leave the COPT on
    the raw frame.
    """
    n = two_period_network()
    raw = SolverConfig(multi_investment_periods=True, reserve_margin=0.15)
    cfg = SolverConfig(multi_investment_periods=True, reserve_margin=0.15,
                       load_scalers={"2035": 1.25})
    inp0, inp1 = snapshot_inputs(n, cfg=raw), snapshot_inputs(n, cfg=cfg)
    _u, res0, _w = fleet_and_residual(n, cfg=raw)
    _u, res1, _w = fleet_and_residual(n, cfg=cfg)
    f0, f1 = reserve_margin_facts(n, raw), reserve_margin_facts(n, cfg)
    load = n.loads_t.p_set["l"].to_numpy(dtype=float)
    # residual = demand − must-take; the must-take leg is unchanged, so the
    # DEMAND ratio is what must read 1.25 / 1.0.
    mt = load - inp0.residual
    for label, r0, r1 in (("mc", inp0.residual, inp1.residual),
                          ("copt", res0.to_numpy(dtype=float), res1.to_numpy(dtype=float))):
        d0, d1 = _per_period(r0 + mt, n), _per_period(r1 + mt, n)
        assert np.allclose(d1[2035], 1.25 * d0[2035]), label
        assert np.array_equal(d1[2030], d0[2030]), label
    p0 = {P: v["peak_mw"] for P, v in f0["stash"]["periods"].items()}
    p1 = {P: v["peak_mw"] for P, v in f1["stash"]["periods"].items()}
    assert p1["2035"] == pytest.approx(1.25 * p0["2035"])
    assert p1["2030"] == p0["2030"]
    assert D.load_scale_factors(n, cfg) == [(2035, "l", "electrical", 1.25)]


# ── D3: the wrapper path and the route path agree ───────────────────────

def test_D3_the_in_place_path_and_the_route_path_stash_the_same_demand():
    """★ D3. Inside the solve the transforms are applied IN PLACE and the
    wrapper calls the facts with `demand_scaled_in_place=True`; from a
    route the facts scale a copy through the helper. The two must stash
    the same `demand_mw`, and the in-place path must NOT be scaled twice.

    Bite (verified): run the helper inside the wrapper too → ×1.25² in 2035
    (measured 281.15 against 224.92).
    """
    n = two_period_network()
    cfg = SolverConfig(multi_investment_periods=True, reserve_margin=0.15,
                       load_scalers={"2035": 1.25})
    raw_p_set = n.loads_t.p_set.copy(deep=True)
    route = reserve_margin_facts(n, cfg)
    restore, _captured = _apply_modelling_assumptions(n, cfg, lambda m: None)
    try:
        wrapper = reserve_margin_facts(n, cfg, demand_scaled_in_place=True)
    finally:
        restore()
    assert n.loads_t.p_set.equals(raw_p_set)          # reverted bit-identical
    for P in ("2030", "2035"):
        a = route["stash"]["periods"][P]["demand_mw"].to_numpy(dtype=float)
        b = wrapper["stash"]["periods"][P]["demand_mw"].to_numpy(dtype=float)
        assert np.array_equal(a, b), P
    assert wrapper["stash"]["periods"]["2035"]["peak_mw"] == pytest.approx(
        1.25 * float(raw_p_set["l"].loc[2035].max()))


def test_D3b_a_real_solve_stashes_the_LP_basis_once_and_the_loops_read_it():
    """★ D3b — the wrapper path, through a real solve. The wrapper passes
    `demand_scaled_in_place=True`; the stashed 2035 peak is 1.25× the raw
    peak (once, not 1.25²), `p_set` is restored bit-identical, and the
    post-solve snapshot the loops take — with the cfg — is on the same
    basis: its demand is 1.25× raw in 2035, where before this phase it
    equalled the pre-solve raw residual (the fifteenth finding, measured).

    Bite (verified): drop `demand_scaled_in_place=True` from the wrapper's
    call → the helper scales the in-place-scaled frame → 1.25² × raw.
    """
    import queue

    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation

    n = two_period_network()
    raw_p_set = n.loads_t.p_set.copy(deep=True)
    raw_peak = float(raw_p_set["l"].loc[2035].max())
    cfg = SolverConfig(multi_investment_periods=True, reserve_margin=0.15,
                       load_scalers={"2035": 1.25})
    PyPSAService.set_network(n)
    sink: dict = {}
    status, cond = run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(),
        queue.SimpleQueue(), state_update=lambda **kw: sink.update(kw))
    assert status in ("ok", "optimal"), (status, cond)
    peaks = {r["period"]: r["peak_mw"]
             for r in (sink.get("last_reserve_margin") or {}).get("by_period", [])}
    assert peaks["2035"] == pytest.approx(1.25 * raw_peak)
    assert peaks["2030"] == pytest.approx(float(raw_p_set["l"].loc[2030].max()))
    assert n.loads_t.p_set.equals(raw_p_set)
    # the loops' snapshot, taken as they now take it
    inp = snapshot_inputs(n, keep_zero_capacity=True, cfg=cfg)
    mt = raw_p_set["l"].to_numpy(dtype=float) - snapshot_inputs(n, keep_zero_capacity=True).residual
    d = _per_period(inp.residual + mt, n)
    assert np.allclose(d[2035], 1.25 * _per_period(raw_p_set["l"].to_numpy(dtype=float), n)[2035])


# ── D4: the LP never scales a static load, and neither do the engines ───

def test_D4_a_static_load_is_untouched_on_every_surface():
    """★ D4 (v3 review, finding 1 — the blocker). The LP scales
    `loads_t.p_set` COLUMNS only; a static `loads.p_set` is left alone. The
    helper must reproduce that, not improve on it: scaling the static value
    would put every engine at 187.5 while the LP, the ENS cap and the margin
    constraint stay at 150 — the very mismatch this phase removes,
    manufactured on every static-load network.

    Bite (verified): scale the static value in `fleet_and_residual` /
    `reserve_margin_facts` when a factor is configured.
    """
    n = two_period_network(static_load=True)
    cfg = SolverConfig(multi_investment_periods=True, reserve_margin=0.15,
                       load_scalers={"2035": 1.25})
    assert D.load_scale_factors(n, cfg) == []           # nothing to scale
    inp = snapshot_inputs(n, cfg=cfg)
    load = np.full(2 * H, 150.0)
    mt = load - snapshot_inputs(n).residual
    assert np.allclose(inp.residual + mt, 150.0)
    facts = reserve_margin_facts(n, cfg)
    assert {P: v["peak_mw"] for P, v in facts["stash"]["periods"].items()} \
        == {"2030": 150.0, "2035": 150.0}
    # and the LP itself leaves it alone
    restore, _c = _apply_modelling_assumptions(n, cfg, lambda m: None)
    try:
        assert float(n.loads.at["l", "p_set"]) == 150.0
    finally:
        restore()


# ── the helper IS the LP's rule ─────────────────────────────────────────

@pytest.mark.parametrize("mip", [True, False])
def test_the_helper_and_the_LP_apply_the_same_factors(mip):
    """★ The gate is the LP's: with `multi_investment_periods=False` the LP
    scales nothing on a MultiIndex network (v3 review, finding 7 — the
    router's inline copy had no such gate and scaled it anyway). Whatever the
    LP does in place, the helper does on a copy, column for column.

    Bite (verified): drop the `multi_investment_periods` test from the
    helper's gate.
    """
    n = two_period_network()
    cfg = SolverConfig(multi_investment_periods=mip, reserve_margin=0.15,
                       load_scalers={"2035": 1.25})
    helper = D.lp_demand_frame(n, cfg).copy(deep=True)
    restore, _c = _apply_modelling_assumptions(n, cfg, lambda m: None)
    try:
        in_place = n.loads_t.p_set.copy(deep=True)
    finally:
        restore()
    pd.testing.assert_frame_equal(helper, in_place)
    scaled = not np.array_equal(helper["l"].loc[2035].to_numpy(),
                                two_period_network().loads_t.p_set["l"].loc[2035].to_numpy())
    assert scaled is mip


def test_non_finite_and_unparseable_factors_are_identity_like_the_LP():
    """The LP treats `inf`, `nan` and junk as identity (`math.isfinite`); the
    router's copy used `f == f`, which let `inf` through. One rule now."""
    n = two_period_network()
    for bad in (float("inf"), float("nan"), "abc", None):
        cfg = SolverConfig(multi_investment_periods=True,
                           load_scalers={"2035": bad})
        assert D.load_scale_factors(n, cfg) == [], bad


def test_per_carrier_overrides_the_legacy_global_and_falls_back_to_it():
    n = two_period_network()
    n.loads.at["l", "carrier"] = "AC"
    cfg = SolverConfig(multi_investment_periods=True,
                       load_scalers={"2035": 1.25, "2030": 1.1},
                       load_scalers_by_carrier={"electrical": {"2035": 1.5}})
    assert D.load_scale_factors(n, cfg) == [
        (2030, "l", "electrical", 1.1), (2035, "l", "electrical", 1.5)]


# ── D5: /copt reads under the mutation lock ─────────────────────────────

def test_D5_copt_route_waits_for_the_mutation_lock():
    """★ D5 (v3 review, finding 8). A solve holds the mutation lock and
    scales the load frame in place for its duration; `/copt` used to read
    bare and could see the half-transformed network — a transient
    inconsistency before, a double scaling after this phase. It now takes
    the same lock `/mc` does.

    Bite (verified): drop the `with PyPSAService.get_lock():` around
    `fleet_and_residual` in `get_copt`.
    """
    import routers.results as R
    from services.pypsa_service import PyPSAService
    from tests.test_adequacy_copt import _network

    PyPSAService.set_network(_network())
    lock = PyPSAService.get_lock()
    done = threading.Event()
    result: dict = {}

    def call():
        result["out"] = R.get_copt()
        done.set()

    with lock:
        t = threading.Thread(target=call, daemon=True)
        t.start()
        time.sleep(0.3)
        assert not done.is_set(), "get_copt read the network while a solve held the lock"
    assert done.wait(10.0)
    assert result["out"]["engine"] == "copt"

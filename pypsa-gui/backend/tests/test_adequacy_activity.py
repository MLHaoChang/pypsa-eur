"""
Phase 12d — the engines honour activity and vintages.

Plan: docs/superpowers/plans/2026-09-03-fmea-phase12d-engine-activity-v1.md
(§4 hand values, §5 tests as amended by the v1 review). Every ★ was run
against its named broken variant and failed; restores by hash.

F1 (``activity_network``): two periods 2030 / 2035 of 24 h, flat load 80 MW,
two 50 MW base units at q = 0.1, and ``new`` — 40 MW at q = 0.2 with
``build_year=2035``. By hand: 2030 LOLP = 1 − 0.81 = 0.19, EUE/h = 6.2;
2035 LOLP = 0.01 + 2·0.09·0.2 = 0.046, EUE/h = 1.56. Per period LOLE 4.56 h
and 1.104 h. The blind reading was the 2035 numbers in both periods.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import replace

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.adequacy import activity as A
from services.adequacy import copt as C
from services.adequacy import elcc as E
from services.adequacy import mc as M
from services.adequacy import portfolio as P
from services.adequacy.copt import must_take_generators
from services.solver_service import SolverConfig

H = 24
LOLE_2030 = 0.19 * H          # 4.56
LOLE_2035 = 0.046 * H         # 1.104
EUE_2030 = 6.2 * H            # 148.8
EUE_2035 = 1.56 * H           # 37.44


def _sha(a) -> str:
    return hashlib.sha256(np.ascontiguousarray(
        np.asarray(a, dtype=np.float64)).tobytes()).hexdigest()


def activity_network(*, new_build_year: int = 2035, store: bool = False,
                     flat: bool = False, new_active: bool = True,
                     new_profile=None) -> pypsa.Network:
    n = pypsa.Network()
    if flat:
        n.set_snapshots(pd.date_range("2030-01-01", periods=H, freq="h"))
    else:
        n.set_snapshots(pd.MultiIndex.from_product(
            [[2030, 2035], pd.date_range("2030-01-01", periods=H, freq="h")],
            names=["period", "timestep"]))
    n.add("Carrier", "gas")
    n.add("Carrier", "battery")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=80.0)
    for nm in ("base_a", "base_b"):
        n.add("Generator", nm, bus="b", carrier="gas", p_nom=50.0,
              build_year=2000, lifetime=100, outage_rate_value=0.1,
              outage_rate_basis="FOR", mttr_hours=24.0)
    # `new` LAST: E3's bit-identity with a fleet that lacks it needs the
    # positional substreams of the units before it unchanged.
    n.add("Generator", "new", bus="b", carrier="gas", p_nom=40.0,
          build_year=new_build_year, lifetime=100, active=new_active,
          outage_rate_value=0.2, outage_rate_basis="FOR", mttr_hours=24.0)
    if new_profile is not None:
        n.generators_t.p_max_pu["new"] = np.asarray(new_profile, dtype=float)
    if store:
        n.add("StorageUnit", "batt", bus="b", carrier="battery", p_nom=30.0,
              max_hours=2.0, build_year=2035, lifetime=100)
    return n


def _screen(n):
    units, res, w = C.fleet_and_residual(n)
    return C.screening_analysis(units, res, weights=w, voll=1000.0)


# ── E1: the mask IS PyPSA's, and the vintage rule matches it on live rows ──

def test_E1_active_mask_is_pypsas_and_the_vintage_rule_matches_it_on_live_rows():
    """★ E1. `active_mask` returns exactly `get_active_assets(P)` per period
    and `static.active` on a flat axis; `capacity_by_period`'s vintage rule
    applied to a hand-built breakdown equals PyPSA's mask applied to LIVE
    rows with the same build years and lifetimes. Bite (verified): `<` for
    `<=` on the vintage's build year — a vintage is then inactive in the
    period it was built for."""
    n = activity_network()
    for P in (2030, 2035):
        pd.testing.assert_series_equal(
            A.active_mask(n, "generators", P),
            n.components.generators.get_active_assets(P))
    assert A.active_mask(n, "generators", 2030).to_dict() == {
        "base_a": True, "base_b": True, "new": False}
    flat = activity_network(flat=True, new_active=False)
    pd.testing.assert_series_equal(
        A.active_mask(flat, "generators", "ALL"),
        flat.components.generators.get_active_assets())
    assert not A.active_mask(flat, "generators", "ALL")["new"]

    # the vintage rule vs PyPSA on live rows: a 2030 vintage with a 5-year
    # life and a 2035 one, beside a parent that carries the breakdown
    n = activity_network()
    n.add("Generator", "v2030", bus="b", carrier="gas", p_nom=35.0,
          build_year=2030, lifetime=5)
    n.add("Generator", "v2035", bus="b", carrier="gas", p_nom=35.0,
          build_year=2035, lifetime=5)
    n.add("Generator", "wind", bus="b", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, build_year=2030, lifetime=100)
    n.generators.loc["wind", "p_nom_opt"] = 70.0
    n.meta["vintage_results"] = {"Generator": {"wind": {
        "initial_capacity": 0.0, "capacity_field": "p_nom",
        "periods": [{"build_year": 2030, "lifetime": 5.0, "p_nom_opt": 35.0},
                    {"build_year": 2035, "lifetime": 5.0, "p_nom_opt": 35.0}]}}}
    ctx = A.ActivityContext(n, "generators", A.period_blocks(n.snapshots))
    got = ctx.capacity_by_period("wind", n.generators.loc["wind"])
    want = [35.0 * float(A.active_mask(n, "generators", P)["v2030"])
            + 35.0 * float(A.active_mask(n, "generators", P)["v2035"])
            for P in (2030, 2035)]
    assert got == want == [35.0, 35.0]


# ── E2: the COPT per period, at the hand values ─────────────────────────

def test_E2_the_copt_evaluates_each_period_with_the_fleet_active_in_it():
    """★ E2. On F1 the screening COPT reads 4.56 h / 148.8 MWh in 2030 (two
    units) and 1.104 h / 37.44 MWh in 2035 (three), total 5.664 h; `new`'s
    criticality row is nonzero and `base_a`'s ΔEUE is the SUM of its two
    per-block counterfactuals (76.8 + 23.04, recomputed from the two tables).
    Bite (verified): the single-table path (drop the per-block loop) — both
    periods read the 2035 numbers."""
    n = activity_network()
    an = _screen(n)
    by = an["metrics"]["by_period"]
    assert by[2030]["lole_hours"] == pytest.approx(LOLE_2030)
    assert by[2030]["eue_mwh"] == pytest.approx(EUE_2030)
    assert by[2035]["lole_hours"] == pytest.approx(LOLE_2035)
    assert by[2035]["eue_mwh"] == pytest.approx(EUE_2035)
    assert an["metrics"]["lole_hours"] == pytest.approx(LOLE_2030 + LOLE_2035)
    assert an["metrics"]["lolp_max"] == pytest.approx(0.19)
    rows = {r["name"]: r for r in an["rows"]}
    assert rows["new"]["delta_eue_mwh"] == pytest.approx(27.84)
    # the two per-block counterfactuals, computed on the two tables directly
    units, res, w = C.fleet_and_residual(n)
    base = [u for u in units if u.name != "new"]
    new = next(u for u in units if u.name == "new")
    per_block = []
    for start, end, fleet in ((0, H, base), (H, 2 * H, base + [replace(new, capacity_series=None)])):
        r = C.attribute_criticality(
            fleet, C.build_copt(fleet), res.iloc[start:end],
            weights=w.iloc[start:end], voll=1000.0)
        per_block.append({x["name"]: x["delta_eue_mwh"] for x in r})
    assert rows["base_a"]["delta_eue_mwh"] == pytest.approx(
        per_block[0]["base_a"] + per_block[1]["base_a"])
    assert per_block[0]["base_a"] == pytest.approx(76.8)
    assert per_block[1]["base_a"] == pytest.approx(23.04)
    # merged rows keep the contract shape and the € follow the ΔEUE
    fm = rows["base_a"]["failure_mode"]
    assert fm["criticality_eur_per_year"] == pytest.approx(rows["base_a"]["delta_eue_mwh"] * 1000.0)
    assert set(an["dist"]) == {2030, 2035} and set(an["residual"]) == {2030, 2035}
    assert an["fidelity_note"] is None


def test_E2b_the_single_block_path_is_literally_the_old_call():
    """Anchor. With no capacity series the per-block machinery is not
    entered: the return shape is the 12c-pre one (`dist` a table, `residual`
    a Series) and the numbers are the closed form."""
    n = activity_network(new_build_year=2000)
    an = _screen(n)
    assert isinstance(an["dist"], C.CapacityDistribution)
    assert isinstance(an["residual"], pd.Series)
    assert an["metrics"]["by_period"][2030]["lole_hours"] == pytest.approx(LOLE_2035)
    assert an["metrics"]["by_period"][2035]["lole_hours"] == pytest.approx(LOLE_2035)


# ── E3: the sampler, bit-identical to exclusion in the inactive period ───

def test_E3_the_mc_masks_the_unit_bit_for_bit_and_lands_on_the_hand_values():
    """★ E3. In 2030 the F1 sampler is bit-identical to `exclude={new}` and
    to a fleet WITHOUT `new` (positional substreams: `new` is last; a fixed
    sample count: `cov_target=_NEVER_CONVERGE`, single batch) while 2035
    differs; at 2000 draws each period's hand value lies inside its own CI.
    Bite (verified): the sampler ignoring `capacity_series` (scalar cap) —
    the 2030 block then contains `new`."""
    n = activity_network()
    inp = M.snapshot_inputs(n)
    assert [u.name for u in inp.units] == ["base_a", "base_b", "new"]
    new = inp.units[2]
    assert new.capacity_mw == 40.0 and new.capacity_series is not None
    assert (new.capacity_series[:H] == 0.0).all() and (new.capacity_series[H:] == 40.0).all()

    kw = dict(draws=64, seed=0)
    full = M._simulate_blocks(inp, **kw)
    excl = M._simulate_blocks(inp, exclude=frozenset({2}), **kw)
    without = M._simulate_blocks(M.snapshot_inputs(activity_network(new_build_year=2100)), **kw)
    for other in (excl, without):
        assert np.array_equal(full[2030][0], other[2030][0])
        assert np.array_equal(full[2030][1], other[2030][1])
    assert not np.array_equal(full[2035][0], excl[2035][0])
    # a fleet whose `new` is NEVER active has the unit at 0 MW → cap_max 0 →
    # not in the fleet at all (membership by cap_max, plan §1)
    assert [u.name for u in M.snapshot_inputs(activity_network(new_build_year=2100)).units] \
        == ["base_a", "base_b"]

    mc = M.mc_adequacy(inp, draws=2000, seed=0, cov_target=0.05)
    lo, hi = mc["by_period"][2030]["lole_ci"]
    assert lo <= LOLE_2030 <= hi, mc["by_period"][2030]
    lo, hi = mc["by_period"][2035]["lole_ci"]
    assert lo <= LOLE_2035 <= hi, mc["by_period"][2035]


# ── E4: a must-take farm built later; the portfolio agrees with the margin ─

E4_ELCC_MW = 83.203125     # draws 32 seed 1 AND draws 128 seed 0, on the code


def test_E4_a_farm_built_in_2035_is_netted_nowhere_in_2030_and_the_block_is_ok():
    """★ E4 (rewrites 12c's B12). `two_period_network(wind_build_year=2035)`:
    the 2030 residual equals the demand (nothing netted), the preserved
    profile is zero over 2030; solved under a 30 % margin the block is `ok`,
    the 2030 row `no_contribution`, the 2035 row `ok` at the pinned credit
    (v1 review, finding 2: at 15 % every 2035 hour sheds iff `base` is down,
    wind or no wind, and the credit is 0.0). Bite (verified): net the
    must-take without its capacity series — the 2030 residual carries wind
    and the block refuses with `activity_mismatch`, 12c's outcome."""
    from tests.test_adequacy_demand_basis import two_period_network
    from tests.test_adequacy_portfolio import _block_for, _solved
    n = two_period_network(wind_build_year=2035)
    cfg = SolverConfig(multi_investment_periods=True, reserve_margin=0.3)
    inp = M.snapshot_inputs(n, vre_assets=must_take_generators(n), cfg=cfg)
    demand = n.loads_t.p_set["l"].to_numpy(dtype=float)
    assert np.array_equal(inp.residual[:H], demand[:H])
    assert (inp.vre_profiles["wind"][:H] == 0.0).all()
    assert inp.vre_profiles["wind"][H:].max() == pytest.approx(100.0)
    assert np.array_equal(inp.residual[H:], demand[H:] - inp.vre_profiles["wind"][H:])

    sink = _solved(n, reserve_margin=0.3, multi_investment_periods=True)
    block, pop = _block_for(n, sink, cfg=cfg, draws=32, seed=1)
    assert block["status"] == "ok", block["reason"]
    assert pop["members"][0].capacity_by_period == (("2030", 0.0), ("2035", 100.0))
    rows = {r["period"]: r for r in block["periods"]}
    assert rows["2030"]["status"] == "no_contribution"
    assert rows["2030"]["credit_gross_mw"] == 0.0
    assert rows["2035"]["status"] == "ok"
    assert rows["2035"]["elcc_mw"] == pytest.approx(E4_ELCC_MW)
    assert rows["2035"]["nameplate_mw"] == pytest.approx(100.0)


def test_E4b_a_margin_row_the_snapshot_does_not_know_is_still_a_mismatch():
    """★ E4b — the tripwire. With both sides masking by activity the only
    way the margin credits a generator the engines lack is a row the
    snapshot does not know; a hand-added one refuses `activity_mismatch`
    naming it. Bite (verified): drop the credited-minus-snapshot check."""
    import copy
    from tests.test_adequacy_demand_basis import two_period_network
    from tests.test_adequacy_portfolio import _solved
    n = two_period_network(wind_build_year=2035)
    cfg = SolverConfig(multi_investment_periods=True, reserve_margin=0.3)
    sink = _solved(n, reserve_margin=0.3, multi_investment_periods=True)
    payload = copy.deepcopy(sink["last_reserve_margin"])
    payload["assets"].append({"period": "2030", "name": "ghost", "kind": "generator",
                              "capacity_mw": 10.0, "derate": 1.0, "extendable": False})
    inp = M.snapshot_inputs(n, vre_assets=must_take_generators(n), cfg=cfg)
    pop = P.portfolio_population(n, inp)
    block = P.portfolio_block(inp, pop, margin_payload=payload,
                              snapshot_fingerprint=P.network_fingerprint(n),
                              seed=1, draws=32, cov_target=1.0,
                              baseline=None, baseline_key=None)
    assert block["status"] == "activity_mismatch", block
    assert "ghost" in block["reason"] and "2030" in block["reason"]


# ── E5: a store built later is not dispatched before it exists ──────────

def test_E5_a_store_built_in_2035_is_not_dispatched_in_2030():
    """★ E5. F1 plus a 30 MW / 2 h store with `build_year=2035`: the 2030
    block's per-draw (lole, eue) equal the no-store run's bit for bit and
    the 2035 block's differ. Bite (verified): `_simulate_blocks` ignoring the
    store's capacity series."""
    with_store = M.snapshot_inputs(activity_network(store=True))
    no_store = M.snapshot_inputs(activity_network())
    s = with_store.storage[0]
    assert s.p_nom_mw == 30.0 and s.e_nom_mwh == 60.0
    assert (s.capacity_series[:H] == 0.0).all() and (s.capacity_series[H:] == 30.0).all()
    a = M._simulate_blocks(with_store, draws=64, seed=3)
    b = M._simulate_blocks(no_store, draws=64, seed=3)
    assert np.array_equal(a[2030][0], b[2030][0]) and np.array_equal(a[2030][1], b[2030][1])
    assert not np.array_equal(a[2035][1], b[2035][1])
    assert a[2035][1].sum() < b[2035][1].sum()      # the store helps in 2035


# ── E6: the vintage breakdown, on a real solve ──────────────────────────

def _vintage(*, lifetime: float, alternating: bool = True, min_2040: float | None = None):
    from services.vintage_service import set_bounds_for_asset
    from tests.test_adequacy_reserve_margin import _vintage_network
    n = _vintage_network(alternating=alternating)
    n.generators.loc["wind", "lifetime"] = float(lifetime)
    if min_2040 is not None:
        set_bounds_for_asset(n, "Generator", "wind",
                             {"2030": {"p_nom_min": 0.0, "p_nom_max": 100.0},
                              "2040": {"p_nom_min": float(min_2040), "p_nom_max": 100.0}})
    return n


def _ctx(n):
    return A.ActivityContext(n, "generators", A.period_blocks(n.snapshots))


def test_E6_a_vintage_built_per_period_is_scored_per_period():
    """★ E6 (F2, alternating profile, wind `lifetime=10`). The LP builds 70 MW
    in EACH vintage (the 2030 one expires before 2040); the parent reads
    `p_nom_opt = 140` while the engines score 70 in each period: residual
    150 − 70·profile per period, `wind` a member at 70 with (70, 70), the
    block `ok` with the by-parent capacity check passing per period and the
    margin's credit 35 MW (derate 0.5) in each. Bite (verified): the parent's
    aggregate in every period (`solved_capacity`) — the 2030 residual reads
    150 − 140 and the block is `capacity_basis_mismatch`."""
    from tests.test_adequacy_portfolio import _solved
    n = _vintage(lifetime=10)
    sink = _solved(n, reserve_margin=0.5, multi_investment_periods=True)
    assert n.generators.at["wind", "p_nom_opt"] == pytest.approx(140.0)
    bd = n.meta["vintage_results"]["Generator"]["wind"]
    assert [(p["build_year"], p["p_nom_opt"], p["lifetime"]) for p in bd["periods"]] \
        == [(2030, 70.0, 10.0), (2040, 70.0, 10.0)]
    assert _ctx(n).capacity_by_period("wind", n.generators.loc["wind"]) == [70.0, 70.0]
    cfg = SolverConfig(multi_investment_periods=True, reserve_margin=0.5)
    inp = M.snapshot_inputs(n, vre_assets=must_take_generators(n), cfg=cfg)
    assert np.array_equal(inp.residual, np.array([80.0, 150.0] * 4))
    pop = P.portfolio_population(n, inp)
    assert pop["members"] == [P.Member("vre", "wind", 70.0, (("2030", 70.0), ("2040", 70.0)))]
    block = P.portfolio_block(inp, pop, margin_payload=sink["last_reserve_margin"],
                              snapshot_fingerprint=P.network_fingerprint(n),
                              seed=1, draws=32, cov_target=1.0,
                              baseline=None, baseline_key=None)
    assert block["status"] == "ok", block["reason"]
    assert [r["credit_gross_mw"] for r in block["periods"]] == [pytest.approx(35.0)] * 2


def test_E6b_a_partially_built_vintage_scores_its_size_in_each_period():
    """★ E6b (F3 alternating: `lifetime=100`, a 20 MW floor on the 2040
    vintage). Breakdown 70 / 20, parent 90: the engines give (70, 90), the
    series is not constant, the 2040 residual nets 90·profile and the
    disclosure lists `wind` as `partial` in 2030. Bite (verified): a vintage
    active ONLY in its build period (`==` for the lifetime rule) — 2040
    reads 20."""
    from tests.test_adequacy_portfolio import _solved
    n = _vintage(lifetime=100, min_2040=20.0)
    _solved(n, reserve_margin=0.5, multi_investment_periods=True)
    assert n.generators.at["wind", "p_nom_opt"] == pytest.approx(90.0)
    ctx = _ctx(n)
    assert ctx.capacity_by_period("wind", n.generators.loc["wind"]) == [70.0, 90.0]
    cap, series = ctx.capacity_series("wind", n.generators.loc["wind"])
    assert cap == 90.0 and series is not None
    assert (series[:4] == 70.0).all() and (series[4:] == 90.0).all()
    cfg = SolverConfig(multi_investment_periods=True, reserve_margin=0.5)
    inp = M.snapshot_inputs(n, vre_assets=must_take_generators(n), cfg=cfg)
    assert np.array_equal(inp.residual, np.array([80.0, 150.0, 80.0, 150.0, 60.0, 150.0, 60.0, 150.0]))
    units = [C.CoptUnit("wind", 90.0, 0.0, capacity_series=series)]
    summary = A.activity_summary(units, (), inp.periods)
    assert summary["by_period"]["2030"] == {"inactive": [], "partial": ["wind"]}
    assert summary["by_period"]["2040"] == {"inactive": [], "partial": []}
    assert "later vintage" in summary["note"]


def test_E6c_a_breakdown_without_lifetime_falls_back_to_the_parents():
    """★ E6c. A breakdown entry without `lifetime` (one written before this
    phase, or a myopic entry) expires with the PARENT's finite lifetime, and
    never with an infinite one. Bite (verified): fall back to `inf`
    unconditionally."""
    def _net(parent_lifetime):
        n = activity_network()
        n.add("Generator", "wind", bus="b", carrier="gas", p_nom=0.0,
              p_nom_extendable=True, build_year=2030, lifetime=parent_lifetime)
        n.generators.loc["wind", "p_nom_opt"] = 70.0
        n.meta["vintage_results"] = {"Generator": {"wind": {
            "initial_capacity": 0.0, "capacity_field": "p_nom",
            "periods": [{"build_year": 2030, "p_nom_opt": 35.0},
                        {"build_year": 2035, "p_nom_opt": 35.0}]}}}
        return n
    n = _net(5.0)
    assert _ctx(n).capacity_by_period("wind", n.generators.loc["wind"]) == [35.0, 35.0]
    n = _net(100.0)
    assert _ctx(n).capacity_by_period("wind", n.generators.loc["wind"]) == [35.0, 70.0]


def test_E6d_a_stale_breakdown_is_ignored():
    """★ E6d. After the F2 solve, editing the parent's `p_nom_opt` breaks the
    identity `initial + Σ opt == p_nom_opt`; the breakdown is then ignored
    and the plain rule applies — (90, 0), the parent (build 2030, lifetime
    10) being inactive in 2040 by PyPSA's own mask. Bite (verified): drop the
    consistency test — the stale 70/70 is used against a row that says 90."""
    from tests.test_adequacy_portfolio import _solved
    n = _vintage(lifetime=10)
    _solved(n, reserve_margin=0.5, multi_investment_periods=True)
    n.generators.loc["wind", "p_nom_opt"] = 90.0
    assert A.vintage_breakdown(n, "generators", "wind", n.generators.loc["wind"]) is None
    assert _ctx(n).capacity_by_period("wind", n.generators.loc["wind"]) == [90.0, 0.0]


def test_E6e_a_myopic_freeze_entry_is_a_breakdown():
    """★ E6e (v1 review, finding 3). The myopic strategy's `source:
    "myopic_freeze"` entry — one period, `p_nom_opt = delta` — is read as a
    breakdown: the delta exists from its period onward for the parent's
    lifetime and not before. Bite (verified): skip entries that carry a
    `source`."""
    n = activity_network()
    n.add("Generator", "peaker", bus="b", carrier="gas", p_nom=20.0,
          p_nom_extendable=True, build_year=2000, lifetime=100)
    n.generators.loc["peaker", "p_nom_opt"] = 50.0
    n.meta["vintage_results"] = {"Generator": {"peaker": {
        "capacity_field": "p_nom", "initial_capacity": 20.0,
        "source": "myopic_freeze",
        "periods": [{"build_year": 2035, "p_nom_opt": 30.0}]}}}
    assert _ctx(n).capacity_by_period("peaker", n.generators.loc["peaker"]) == [20.0, 50.0]


def test_E6f_the_vintage_breakdown_is_cleared_when_the_bounds_are_gone():
    """★ E6f (v1 review, finding 6). A re-solve after the bounds are gone
    must not keep the old per-vintage breakdown beside a parent the LP now
    sizes directly. The delete route already drops the entry with the
    bounds (`delete_bounds_for_asset`); the bucket wiped WHOLESALE — a
    project file edited, a bucket reset — reached `apply_vintage_bounds`'s
    early return before its clear and kept the entry. Bite (verified): the
    clear behind the early returns."""
    from services.vintage_service import apply_vintage_bounds
    from tests.test_adequacy_portfolio import _solved
    n = _vintage(lifetime=10)
    _solved(n, reserve_margin=0.5, multi_investment_periods=True)
    assert "wind" in n.meta["vintage_results"]["Generator"]
    n.meta["vintage_results"]["Generator"]["keep"] = {"source": "myopic_freeze",
                                                       "initial_capacity": 0.0, "periods": []}
    n.meta["vintage_bounds"] = {}
    assert apply_vintage_bounds(n, [], lambda _m: None) == 0
    assert n.meta["vintage_results"] == {"Generator": {"keep": {
        "source": "myopic_freeze", "initial_capacity": 0.0, "periods": []}}}


# ── E7: the static `active` column on a flat network ────────────────────

def test_E7_an_inactive_generator_on_a_flat_network_is_in_no_engine():
    """★ E7. `active=False` on a single-period network: not in the fleet,
    not a candidate, not in the margin's stash — and the LP would not
    dispatch it either. Bite (verified): `active_mask` all-True for the
    "ALL" label."""
    from services.solver_service import reserve_margin_facts
    n = activity_network(flat=True, new_active=False)
    units, _res, _w = C.fleet_and_residual(n)
    assert [u.name for u in units] == ["base_a", "base_b"]
    assert {r["name"] for r in E.elcc_candidates(n)} == {"base_a", "base_b"}
    facts = reserve_margin_facts(n, SolverConfig(reserve_margin=0.1))
    assert "new" not in {r["name"] for r in facts["stash"]["assets"]}
    on = activity_network(flat=True, new_active=True)
    assert [u.name for u in C.fleet_and_residual(on)[0]] == ["base_a", "base_b", "new"]


# ── E8: the hashes the loops and the ELCC baseline rest on ──────────────

def test_E8_the_loop_hash_and_the_baseline_key_cover_the_capacity_series():
    """★ E8 / E8b. Two snapshots differing only in one unit's capacity
    series — and, separately, one store's — hash differently under the
    loops' `snapshot_hash` and under `elcc.baseline_key`. Bite (verified):
    hash without the series bytes."""
    from services.adequacy.coupling import snapshot_hash
    inp = M.snapshot_inputs(activity_network(store=True))
    u = inp.units[2]
    other_u = replace(u, capacity_series=np.where(u.capacity_series > 0, 0.0, 40.0))
    alt_units = replace(inp, units=(inp.units[0], inp.units[1], other_u))
    s = inp.storage[0]
    other_s = replace(s, capacity_series=np.where(s.capacity_series > 0, 0.0, 30.0))
    alt_stores = replace(inp, storage=(other_s,))
    kw = dict(draws=8, seed=0, cov_target=0.05, max_draws=M.MAX_DRAWS, batch=250)
    for alt in (alt_units, alt_stores):
        assert snapshot_hash(alt) != snapshot_hash(inp)
        assert E.baseline_key(alt, **kw) != E.baseline_key(inp, **kw)
    assert snapshot_hash(replace(inp)) == snapshot_hash(inp)


# ── E9: the bracket top is the best ACTIVE hour ─────────────────────────

def test_E9_the_nameplate_is_the_best_active_hour():
    """★ E9. `new` on F1 is a candidate at 40 MW (not a horizon mean, not 0);
    a profiled unit active in 2035 only brackets at `max_{h∈2035}(profile)
    × cap` even when its 2030 hours are higher. Bite (verified): the mean
    of the series."""
    n = activity_network()
    assert {r["name"]: r["nameplate_mw"] for r in E.elcc_candidates(n)}["new"] == 40.0
    prof = np.concatenate([np.full(H, 1.0), np.full(H, 0.6)])
    n = activity_network(new_profile=prof)
    new = next(u for u in M.snapshot_inputs(n).units if u.name == "new")
    assert new.profile is not None and new.capacity_series is not None
    assert E.unit_nameplate_mw(new) == pytest.approx(0.6 * 40.0)
    st = M.snapshot_inputs(activity_network(store=True)).storage[0]
    assert E.elcc_candidates(activity_network(store=True))[-1] == {
        "kind": "storage_unit", "name": "batt", "nameplate_mw": 30.0}
    assert st.p_nom_mw == 30.0


# ── E10: the fingerprint covers what now decides the capacity ───────────

@pytest.mark.parametrize("edit", ["active", "vintage_lifetime", "store_build_year"])
def test_E10_the_fingerprint_covers_activity_and_the_breakdown(edit):
    """★ E10. Flipping a generator's `active`, changing a persisted vintage
    lifetime, or a store's build year changes the fingerprint. Bite
    (verified): drop the fields."""
    n = activity_network(store=True)
    n.meta["vintage_results"] = {"Generator": {"new": {
        "initial_capacity": 0.0, "periods": [{"build_year": 2035, "p_nom_opt": 40.0, "lifetime": 10.0}]}}}
    before = P.network_fingerprint(n)
    if edit == "active":
        n.generators.loc["base_a", "active"] = False
    elif edit == "vintage_lifetime":
        n.meta["vintage_results"]["Generator"]["new"]["periods"][0]["lifetime"] = 20.0
    else:
        n.storage_units.loc["batt", "build_year"] = 2030
    assert P.network_fingerprint(n) != before


# ── E11: the routes disclose it ─────────────────────────────────────────

def test_E11_the_copt_route_discloses_which_units_are_masked_where():
    """E11. `/results/copt` on F1 carries `activity.by_period["2030"]
    .inactive == ["new"]` and the sentence; on a single-period network the
    note is None and nothing is listed."""
    import routers.results as R
    from services.pypsa_service import PyPSAService
    PyPSAService.set_network(activity_network())
    out = R.get_copt()
    assert out["activity"]["by_period"] == {
        "2030": {"inactive": ["new"], "partial": []},
        "2035": {"inactive": [], "partial": []}}
    assert "build year and lifetime" in out["activity"]["note"]
    assert out["metrics"]["by_period"]["2030"]["lole_hours"] == pytest.approx(LOLE_2030) \
        if "2030" in out["metrics"]["by_period"] else \
        out["metrics"]["by_period"][2030]["lole_hours"] == pytest.approx(LOLE_2030)
    PyPSAService.set_network(activity_network(flat=True))
    out = R.get_copt()
    assert out["activity"] == {"by_period": {"ALL": {"inactive": [], "partial": []}}, "note": None}


# ── E12: the margin's mask IS the engines' ──────────────────────────────

def test_E12_the_reserve_margin_delegates_its_activity_mask(monkeypatch):
    """★ E12. With `activity.active_mask` patched to all-False the margin
    credits nothing: its `_active` calls the engines' function. Bite
    (verified): the margin keeping its own copy of the six lines."""
    from services.solver_service import reserve_margin_facts
    n = activity_network(flat=True)
    monkeypatch.setattr(
        A, "active_mask",
        lambda n_, comp, P: pd.Series(False, index=getattr(n_, comp).index, dtype=bool))
    facts = reserve_margin_facts(n, SolverConfig(reserve_margin=0.1))
    assert facts["stash"]["assets"] == []
    assert all(per.get("firm_fixed_mw", 0.0) == 0.0 for per in facts["stash"]["periods"].values())

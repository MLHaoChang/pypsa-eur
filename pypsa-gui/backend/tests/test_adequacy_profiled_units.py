"""
Phase 12c-pre — a generator with BOTH an availability series and outage data
(plan docs/superpowers/plans/2026-09-02-fmea-phase12c-pre-profile-outage-unit-v2.md,
v2.1 amendments).

The defect (12a, `2963fc8`): such a unit entered both engines as a flat
two-state unit at nameplate, the series discarded. Now the series rides on
the unit (`CoptUnit.profile`): the sequential MC samples its outages ON the
series (UP = the series' value that hour, DOWN = 0) and the COPT mixes the
unit exactly per hour over its outage states — `2^k` vectorised evaluations
of the without-unit table, exact by the law of total probability. Netting
the unit at its EXPECTED output — v1's proposal — was measured to
understate LOLE 3× and the unit's criticality 14× (LOLP is convex in the
shortfall); A3′ and A7 pin that the mixture, not the netting, shipped.

Every ★ test was run against a named broken variant and failed (recorded in
the plan). M1/M2 pin membership and the scalar sampling path by hash,
computed on `1bce9da` before any engine edit.
"""
from __future__ import annotations

import hashlib
import math
import pathlib
import time

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.adequacy import copt as C
from services.adequacy import elcc as E
from services.adequacy import mc as M
from tests.benchmark_data import rbts_load, rts79_load

BENCH = pathlib.Path(__file__).parent / "benchmark_data"


def _units_csv(path: pathlib.Path) -> list[C.CoptUnit]:
    df = pd.read_csv(path, comment="#")
    return [C.CoptUnit(str(r["name"]), float(r["capacity_mw"]),
                       float(r["forced_outage_rate"]), "FOR",
                       float(r["mttr_hours"]), path.name)
            for _, r in df.iterrows()]


def _rts79_minus_one_400() -> list[C.CoptUnit]:
    """RTS-79 minus one 400 MW unit — the A3′/A7 fixture (plan v2 review,
    finding 6: the full fleet gives 0.59 h, not the 3.97 h the plan quotes)."""
    return [u for u in _units_csv(BENCH / "rts79_units.csv") if u.name != "U400-2"]


def _rts_load() -> np.ndarray:
    return np.asarray(rts79_load.build_hourly_load(2850.0), dtype=np.float64)


def _series(arr) -> tuple[pd.Series, pd.Series]:
    idx = pd.RangeIndex(len(arr))
    return pd.Series(np.asarray(arr, dtype=np.float64), index=idx), pd.Series(1.0, index=idx)


def _hand_mixture_lole(dist: C.CapacityDistribution, load, a, q: float) -> float:
    """Σ_h [q·(1 − S(r_h)) + (1−q)·(1 − S(r_h − a_h))] with the SCALAR
    survival function — independent of `mixture_hourly`."""
    return float(sum(q * (1.0 - dist.survival(float(r)))
                     + (1.0 - q) * (1.0 - dist.survival(float(r - ah)))
                     for r, ah in zip(load, a)))


# ── the membership fixture (M1, A4′, A8) ─────────────────────────────────

def _m1_network() -> pypsa.Network:
    """A must-take farm, a thermal with a static 0.9, a thermal with an
    all-ones column, a farm with a varying series and typed outage data, and
    a hydro with a CONSTANT 0.8 series on a library rate."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=8, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    for c in ("wind", "gas", "hydro"):
        n.add("Carrier", c)
    n.add("Bus", "b", carrier="AC", country="AA")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "wind_mt", bus="b", carrier="wind", p_nom=100.0,
          marginal_cost=0.0)
    n.add("Generator", "gas_static", bus="b", carrier="gas", p_nom=80.0,
          marginal_cost=10.0, p_max_pu=0.9, outage_rate_value=0.10,
          outage_rate_basis="EFORd", mttr_hours=24.0)
    n.add("Generator", "gas_ones", bus="b", carrier="gas", p_nom=50.0,
          marginal_cost=12.0, outage_rate_value=0.06,
          outage_rate_basis="EFORd", mttr_hours=30.0)
    n.add("Generator", "wind_for", bus="b", carrier="wind", p_nom=100.0,
          marginal_cost=0.0, outage_rate_value=0.10,
          outage_rate_basis="EFORd", mttr_hours=24.0)
    n.add("Generator", "hydro_const", bus="b", carrier="hydro", p_nom=60.0,
          marginal_cost=0.0)
    prof = np.tile([0.05, 0.15, 0.35, 0.45], 2)
    n.generators_t.p_max_pu["wind_mt"] = prof
    n.generators_t.p_max_pu["gas_ones"] = np.ones(8)
    n.generators_t.p_max_pu["wind_for"] = prof
    n.generators_t.p_max_pu["hydro_const"] = np.full(8, 0.8)
    return n


def _row_hash(u: C.CoptUnit) -> str:
    prof = u.profile
    ph = None if prof is None else hashlib.sha256(
        np.ascontiguousarray(np.asarray(prof, np.float64)).tobytes()).hexdigest()[:16]
    return hashlib.sha256(
        repr((u.name, float(u.capacity_mw), float(u.q), ph)).encode()).hexdigest()[:16]


# Computed on 1bce9da (before any engine edit) by scratchpad/m1m2_pins.py.
M1_UNCHANGED = {"gas_static": "4c49f507694900a2", "gas_ones": "afe1cb4666dd076f"}
M1_RESIDUAL = "5f4edc3d7480ab0e"
M1_OLD_WIND_FOR = "827dc271a04917a1"       # profile None — must NOT hold now
M1_OLD_HYDRO_CONST = "24ad1c55c5c08a04"    # profile None — must NOT hold now


def test_M1_membership_pin_and_A4_which_rows_carry_a_profile():
    """★ M1 + A4′: the rows that must not change did not (hash), the residual
    is untouched (only must-take is netted), the varying farm and the
    constant-0.8 hydro carry their series (v2.1 C1: any INFORMATIVE series,
    constant included), and the static-0.9 thermal and the all-ones thermal
    carry none.

    Bites (verified): fold the static column in → gas_static's hash changes;
    attach only VARYING series → hydro_const's hash equals the old one.
    """
    units, residual, _w = C.fleet_and_residual(_m1_network())
    by = {u.name: u for u in units}
    for name, h in M1_UNCHANGED.items():
        assert _row_hash(by[name]) == h, (name, _row_hash(by[name]))
        assert by[name].profile is None
    res_hash = hashlib.sha256(
        np.ascontiguousarray(residual.to_numpy(np.float64)).tobytes()).hexdigest()[:16]
    assert res_hash == M1_RESIDUAL
    assert _row_hash(by["wind_for"]) != M1_OLD_WIND_FOR
    assert _row_hash(by["hydro_const"]) != M1_OLD_HYDRO_CONST
    np.testing.assert_array_equal(by["wind_for"].profile, np.tile([0.05, 0.15, 0.35, 0.45], 2))
    np.testing.assert_array_equal(by["hydro_const"].profile, np.full(8, 0.8))
    assert "wind_mt" not in by                    # still must-take


def test_a_NaN_hour_is_availability_zero_at_attachment():
    """Phase 12b rule 1, restated for the engines: a NaN hour nets as 0."""
    n = _m1_network()
    n.generators_t.p_max_pu.loc[n.snapshots[2], "wind_for"] = np.nan
    units, _r, _w = C.fleet_and_residual(n)
    prof = {u.name: u for u in units}["wind_for"].profile
    assert prof[2] == 0.0 and np.isfinite(prof).all()


def test_series_is_informative_is_not_identically_one():
    assert not C.series_is_informative(np.ones(5))
    assert not C.series_is_informative([1.0, np.nan, 1.0 + 1e-12])
    assert not C.series_is_informative([np.nan, np.nan])
    assert C.series_is_informative(np.full(5, 0.8))
    assert C.series_is_informative([1.0, 0.5, 1.0])
    assert C.series_is_informative([1.0, np.nan, 0.999999])


# ── A1: the MC uses the profile ──────────────────────────────────────────

def test_A1_the_MC_samples_outages_ON_the_series():
    """★ A1: one 100 MW unit, q = 0.5, profile [1,0,1,0], residual 60 MW —
    hours 1 and 3 are short with probability 1 (the series is 0 there, up or
    down); hours 0 and 2 with ≈ 0.5.

    Bite (verified): ignore the profile → hours 1, 3 short with ≈ 0.5.
    """
    u = C.CoptUnit("p", 100.0, 0.5, "FOR", 2.0, "x",
                   profile=np.array([1.0, 0.0, 1.0, 0.0]))
    cap = M.sample_capacity([u], 4, 4000, seed=7)          # (draws, H)
    assert cap.shape == (4000, 4)
    assert (cap[:, 1] == 0.0).all() and (cap[:, 3] == 0.0).all()
    short0 = float((cap[:, 0] < 60.0).mean())
    short2 = float((cap[:, 2] < 60.0).mean())
    assert abs(short0 - 0.5) < 0.05 and abs(short2 - 0.5) < 0.05, (short0, short2)
    # UP is the series' value, DOWN is zero — never anything in between.
    assert set(np.unique(cap[:, 0]).tolist()) <= {0.0, 100.0}


def test_the_MC_rejects_a_profile_of_the_wrong_length():
    u = C.CoptUnit("p", 100.0, 0.1, "FOR", 24.0, "x", profile=np.ones(3))
    with pytest.raises(ValueError, match="profile has shape"):
        M.sample_capacity([u], 4, 8, seed=1)


# ── M2: the scalar path is byte-identical ────────────────────────────────

M2_SHA256 = "aa4b3c0f25c70b6fc0bb094a071c96c28704c9c9a149e1d6a9143c148cdf2394"


def test_M2_scalar_sampling_path_is_byte_identical():
    """★ M2 (v1's A2, specified): `sample_capacity` on the RBTS fleet,
    H = 8736, draws = 64, seed = 20260828 hashes to the value computed on
    1bce9da under numpy 2.4.6. Stream stability across numpy MAJORS is NEP 19
    best-effort, so the test skips — naming the version — rather than fail
    for a reason that is not the engine's.

    Bite (verified): accumulate in float64 instead of float32 — the returned
    bytes change. (Forcing every unit through the (H, 1) path with a profile
    of ones is NOT a broken variant: it is byte-identical, which is exactly
    the broadcast claim, confirmed by that attempt.)
    """
    if int(np.__version__.split(".")[0]) != 2:
        pytest.skip(f"M2 pinned under numpy 2.x; running {np.__version__}")
    units = _units_csv(BENCH / "rbts_units.csv")
    cap = M.sample_capacity(units, 8736, 64, 20260828)
    assert hashlib.sha256(np.ascontiguousarray(cap).tobytes()).hexdigest() == M2_SHA256


# ── A12 / A13: the vectorised evaluators and their cost ──────────────────

def test_A12_vectorised_survival_and_shortfall_equal_the_scalar_pair():
    """★ A12: on the RBTS table, `survival_vec` / `expected_shortfall_vec`
    equal the scalar methods to 1e-12 on grid points, the 1e-12 edge,
    negatives, zero and beyond-table loads; and `hourly_adequacy` (now on the
    vectorised pair) equals the old scalar `map` on the RBTS residual.

    Bite (verified): drop the `− 1e-12` from the grid rule → the grid points
    disagree.
    """
    dist = C.build_copt(_units_csv(BENCH / "rbts_units.csv"), 1.0)
    n = len(dist.probs)
    xs = np.concatenate([
        # …and it must be checked where the grid point CARRIES mass: on RBTS
        # 200 = one 40 MW unit down (P ≈ 0.02); at 1 or 40 the state is empty
        # and a wrong index reads an identical value.
        [-5.0, 0.0, 1e-9, 0.5, 1.0, 1.0 - 1e-13, 1.0 + 5e-13, 2.0, 40.0, 40.5,
         200.0 + 5e-13, 220.0 + 5e-13, 240.0 + 5e-13],
        np.arange(0, n + 3, dtype=float),          # every grid point and past the table
        [n - 1e-13, n + 0.5, 1e9],
    ])
    s_vec = dist.survival_vec(xs)
    es_vec = dist.expected_shortfall_vec(xs)
    for x, sv, ev in zip(xs, s_vec, es_vec):
        assert abs(sv - dist.survival(float(x))) < 1e-12, x
        assert abs(ev - dist.expected_shortfall(float(x))) < 1e-12 * max(1.0, abs(ev)), x
    load = np.asarray(rbts_load.build_hourly_load(185.0), dtype=np.float64)
    res, w = _series(load)
    got = C.hourly_adequacy(dist, res, weights=w)
    old_lole = float(sum(1.0 - dist.survival(float(x)) for x in load))
    old_eue = float(sum(dist.expected_shortfall(float(x)) for x in load))
    assert got["lole_hours"] == pytest.approx(old_lole, rel=1e-12)
    assert got["eue_mwh"] == pytest.approx(old_eue, rel=1e-12)


def test_A13_the_256_state_mixture_on_a_300_unit_table_is_under_a_second():
    """★ A13 (cost pin): LOLE + EUE over 2^8 states, H = 8760, on a 300-unit
    table — measured 89 ms vectorised, 51 s through the scalar map (plan v2
    review, finding 5). The gate is 1 s.
    """
    rng = np.random.default_rng(12)
    units = [C.CoptUnit(f"u{i}", float(rng.integers(5, 41)), 0.05, "FOR", 50.0, "x")
             for i in range(300)]
    dist = C.build_copt(units, 1.0)
    H = 8760
    hrs = np.arange(H)
    mixed = [C.CoptUnit(f"p{i}", 150.0, 0.05, "FOR", 40.0, "x",
                        profile=np.clip(0.55 + 0.45 * np.cos(2 * np.pi * hrs / H + i), 0, 1))
             for i in range(8)]
    residual = 0.6 * dist.mean() + 0.35 * dist.mean() * np.cos(2 * np.pi * hrs / H)
    t0 = time.perf_counter()
    lolp, eue = C.mixture_hourly(dist, residual, mixed)
    dt = time.perf_counter() - t0
    assert lolp.shape == (H,) and eue.shape == (H,)
    assert dt < 1.0, f"{dt:.2f}s"


# ── A3′ / A7: the COPT mixes exactly; continuity at the constant boundary ─

def test_A3_the_COPT_mixes_exactly_and_is_neither_netting_nor_today():
    """★ A3′: RTS-79 minus one 400 MW unit plus a 500 MW q = 0.05 unit on a
    mild profile (0.95 + 0.05·cos): the engine's LOLE equals the
    hand-computed mixture with the SCALAR survival function to 1e-12, and is
    3.97 h — not v1's netting (1.28 h) and not today's flat two-state
    (3.88 h).

    Bite (verified): net the unit at expected output → 1.28 h.
    """
    thermal = _rts79_minus_one_400()
    load = _rts_load()
    H = len(load)
    prof = 0.95 + 0.05 * np.cos(2 * np.pi * np.arange(H) / H)
    unit = C.CoptUnit("u", 500.0, 0.05, "FOR", 40.0, "x", profile=prof)
    res, w = _series(load)
    got = C.screening_analysis(thermal + [unit], res, weights=w, voll=0.0)
    table = C.build_copt(thermal, 1.0)
    hand = _hand_mixture_lole(table, load, prof * 500.0, 0.05)
    assert abs(got["metrics"]["lole_hours"] - hand) < 1e-12
    assert got["metrics"]["lole_hours"] == pytest.approx(3.9746, abs=5e-4)
    netting = C.hourly_adequacy(table, res - 0.95 * prof * 500.0, weights=w)["lole_hours"]
    today = C.hourly_adequacy(
        C.build_copt(thermal + [C.CoptUnit("u", 500.0, 0.05, "FOR", 40.0, "x")], 1.0),
        res, weights=w)["lole_hours"]
    assert netting == pytest.approx(1.28, abs=0.01)
    assert today == pytest.approx(3.88, abs=0.01)
    assert [u.name for u in got["split"].mixed] == ["u"]
    assert got["fidelity_note"] and "u" in got["fidelity_note"]


def _rts_network(level_series: np.ndarray) -> pypsa.Network:
    """RTS-79 minus one 400 MW unit as a network, plus the 500 MW q = 0.05
    unit carrying `level_series` as its p_max_pu column."""
    thermal = _rts79_minus_one_400()
    load = _rts_load()
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=len(load), freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "coal")
    n.add("Bus", "b", carrier="AC", country="AA")
    n.add("Load", "l", bus="b")
    n.loads_t.p_set["l"] = load
    for u in thermal:
        n.add("Generator", u.name, bus="b", carrier="coal", p_nom=u.capacity_mw,
              marginal_cost=10.0, outage_rate_value=u.q, outage_rate_basis="FOR",
              mttr_hours=u.mttr_hours)
    n.add("Generator", "u", bus="b", carrier="coal", p_nom=500.0,
          marginal_cost=5.0, outage_rate_value=0.05, outage_rate_basis="FOR",
          mttr_hours=40.0)
    n.generators_t.p_max_pu["u"] = level_series
    return n


def test_A7_attribution_is_continuous_at_the_constant_boundary_and_exact():
    """★ A7 (retargeted to level 0.5, plan v2.1 C1): the 500 MW unit on a
    CONSTANT 0.5 series and on 0.5 with one hour at 0.5 − 1e-8 — the same
    physical unit either side of Phase 12b's "varying" threshold — give LOLE
    within 0.1 % and ΔEUE within 0.5 % of each other, both through
    `fleet_and_residual`; and the varying row equals
    `EUE(mixture) − EUE(s_i ≡ 1)` computed with `mixture_hourly` directly.

    v2 as written left the constant series two-state AT CAP (level ignored):
    measured 3.877 h against 11.770 h one hour away — a 3× cliff (plan v2
    review, finding 1).

    Bite (verified): attach only VARYING series → LOLE 3.877 vs 11.770.
    """
    H = len(_rts_load())
    const = np.full(H, 0.5)
    varying = const.copy()
    varying[H // 2] = 0.5 - 1e-8
    out = {}
    for tag, series in (("const", const), ("varying", varying)):
        units, residual, w = C.fleet_and_residual(_rts_network(series))
        an = C.screening_analysis(units, residual, weights=w, voll=0.0)
        rows = {r["name"]: r["delta_eue_mwh"] for r in an["rows"]}
        out[tag] = (an["metrics"]["lole_hours"], rows["u"], an)
    lc, dc, _ = out["const"]
    lv, dv, an_v = out["varying"]
    assert abs(lv - lc) / lc < 1e-3, (lc, lv)
    assert abs(dv - dc) / dc < 5e-3, (dc, dv)
    assert lc == pytest.approx(11.77, abs=0.01)         # not 3.877 (at cap)
    # The varying row IS EUE(mixture) − EUE(s ≡ 1):
    split = an_v["split"]
    r = an_v["residual"].to_numpy(dtype=np.float64)
    _l, e_mix = C.mixture_hourly(an_v["dist"], r, split.mixed)
    _l, e_up = C.mixture_hourly(an_v["dist"], r, split.mixed, fixed_up=frozenset({0}))
    assert dv == pytest.approx(float(e_mix.sum() - e_up.sum()), rel=1e-9)


# ── A5′: expectation, pooled over per-draw means ─────────────────────────

def test_A5_sampled_availability_matches_its_expectation_pooled_and_per_hour():
    """★ A5′ (v2.1 C3): q = 0.05, D = 2000, H = 8760, seed pinned. The mean
    over hours of `sampled − (1−q)·a_h`, with its standard error from the D
    per-draw horizon means (hours within a draw are autocorrelated over
    ≈ MTTR, so the hours-independent SE is 6.6× too small and fails a
    correct engine 3 seeds in 4 — plan v2 review, finding 2), is within 3σ;
    and no hour exceeds its Bonferroni bound at α = 0.01/H (z = 4.87).

    Bite (verified): apply the profile without the state (+5 MW every hour)
    → every hour fails its bound.
    """
    D, H, q, cap = 2000, 8760, 0.05, 100.0
    hrs = np.arange(H)
    prof = np.clip(0.55 + 0.45 * np.cos(2 * np.pi * hrs / H), 0, 1)
    a = prof * cap
    u = C.CoptUnit("p", cap, q, "FOR", 24.0, "x", profile=prof)
    s = M.sample_capacity([u], H, D, seed=20260902).astype(np.float64)   # (D, H)
    diff = s - (1.0 - q) * a[None, :]
    per_draw = diff.mean(axis=1)
    se = float(per_draw.std(ddof=1) / math.sqrt(D))
    z = float(per_draw.mean() / se)
    assert abs(z) < 3.0, z
    se_h = a * math.sqrt(q * (1.0 - q) / D)
    diff_h = np.abs(diff.mean(axis=0))
    pos = a > 0
    assert (diff_h[pos] <= 4.87 * se_h[pos]).all(), float((diff_h[pos] / se_h[pos]).max())
    assert (diff_h[~pos] == 0.0).all()


# ── the cap, the split, and the refusal ──────────────────────────────────

def test_split_fleet_mixes_the_largest_and_nets_the_rest_and_says_so():
    """Beyond `K_EXACT` the smallest-mean profiled units are netted at
    expected output (plan v2.1 C4: per-hour convolution rejected on the 99 s
    case), their rows carry `note`, and the sentence names them."""
    H = 24
    hrs = np.arange(H)
    load = np.full(H, 300.0)
    table = [C.CoptUnit(f"t{i}", 100.0, 0.05, "FOR", 50.0, "x") for i in range(3)]
    prof = [C.CoptUnit(f"p{i}", 50.0, 0.1, "FOR", 24.0, "x",
                       profile=np.clip(0.9 - 0.1 * i + 0.05 * np.sin(hrs + i), 0, 1))
            for i in range(4)]
    res, w = _series(load)
    an = C.screening_analysis(table + prof, res, weights=w, voll=0.0, k_exact=2)
    split = an["split"]
    assert [u.name for u in split.mixed] == ["p0", "p1"]
    assert [u.name for u in split.netted] == ["p2", "p3"]
    # Hand: table over t*, residual − Σ_netted (1−q)·a, mixture over p0, p1.
    dist = C.build_copt(table, 1.0)
    netted = sum(0.9 * u.profile * 50.0 for u in split.netted)
    lolp, eue = C.mixture_hourly(dist, load - netted, split.mixed)
    assert an["metrics"]["lole_hours"] == pytest.approx(float(lolp.sum()), rel=1e-12)
    assert an["metrics"]["eue_mwh"] == pytest.approx(float(eue.sum()), rel=1e-12)
    notes = {r["name"]: r.get("note") for r in an["rows"]}
    assert notes["p2"] and notes["p3"] and "understates" in notes["p2"]
    assert notes["p0"] is None and notes["t0"] is None
    assert "2 more beyond the exact cap of 2 (p2, p3)" in an["fidelity_note"]
    # Every row is non-negative and the netted rows are still attributed.
    assert all(r["delta_eue_mwh"] >= 0 for r in an["rows"])


def test_build_copt_refuses_a_profiled_unit():
    """The silent flattening that WAS the defect is now impossible: a
    profiled unit cannot be convolved at nameplate by accident."""
    u = C.CoptUnit("p", 50.0, 0.1, "FOR", 24.0, "x", profile=np.ones(4))
    with pytest.raises(ValueError, match="split the fleet"):
        C.build_copt([u], 1.0)


def test_an_unprofiled_fleet_is_unchanged_through_screening_analysis():
    """With no profiled unit the split is trivial and the numbers are the
    plain table's (the anchors pin the values; this pins the plumbing)."""
    units = _units_csv(BENCH / "rbts_units.csv")
    res, w = _series(rbts_load.build_hourly_load(185.0))
    an = C.screening_analysis(units, res, weights=w, voll=0.0)
    dist = C.build_copt(units, 1.0)
    plain = C.hourly_adequacy(dist, res, weights=w)
    assert an["metrics"]["lole_hours"] == plain["lole_hours"]
    assert an["fidelity_note"] is None
    assert not an["split"].mixed and not an["split"].netted
    assert not any("note" in r for r in an["rows"])


# ── A8: ELCC nameplate ───────────────────────────────────────────────────

def test_A8_elcc_nameplate_is_the_best_hour_and_zero_peak_is_excluded():
    """A8 (pin): a profiled unit's bracket top is `max_h(a_{i,h})` — never a
    (1−q)-derated figure — in `_resolve` and in the candidates; a zero-peak
    profile is not a candidate (as the vre branch already excludes it)."""
    u = C.CoptUnit("p", 200.0, 0.1, "FOR", 24.0, "x",
                   profile=np.array([0.2, 0.6, 0.4]))
    assert E.unit_nameplate_mw(u) == pytest.approx(120.0)
    assert E.unit_nameplate_mw(C.CoptUnit("t", 200.0, 0.1)) == 200.0
    n = _m1_network()
    n.add("Generator", "wind_zero", bus="b", carrier="wind", p_nom=100.0,
          marginal_cost=0.0, outage_rate_value=0.10,
          outage_rate_basis="EFORd", mttr_hours=24.0)
    n.generators_t.p_max_pu["wind_zero"] = np.zeros(8)
    cands = {r["name"]: r for r in E.elcc_candidates(n)}
    assert cands["wind_for"]["nameplate_mw"] == pytest.approx(45.0)
    assert cands["hydro_const"]["nameplate_mw"] == pytest.approx(48.0)
    assert cands["gas_static"]["nameplate_mw"] == pytest.approx(80.0)   # static NOT applied
    assert "wind_zero" not in cands
    inputs = M.snapshot_inputs(n)
    _i, excl, _s, nameplate = E._resolve(inputs, "generator", "wind_for")
    assert nameplate == pytest.approx(45.0)
    assert [inputs.units[i].name for i in excl] == ["wind_for"]


# ── A6′: the margin credits the same expectation ─────────────────────────

def test_A6_the_margin_derate_is_the_window_mean_of_the_same_expectation():
    """A6′ (pin, not a bite): the reserve margin's `derate` for the varying
    farm equals `mean((1−q)·profile over the gross window)` — the margin is
    NOT refactored; its series IS the expectation of the mixture's per-hour
    availability."""
    from services.solver_service import SolverConfig, reserve_margin_facts
    from services.adequacy.window import peak_window

    n = _m1_network()
    facts = reserve_margin_facts(n, SolverConfig(reserve_margin=0.1))
    assert facts is not None
    rows = [a for a in facts["stash"]["assets"] if a["name"] == "wind_for"]
    assert rows, facts["stash"]["assets"]
    win = peak_window(n.loads_t.p_set.sum(axis=1) if not n.loads_t.p_set.empty
                      else pd.Series(100.0, index=n.snapshots))
    expect = 0.9 * float(n.generators_t.p_max_pu["wind_for"].reindex(win).mean())
    assert rows[0]["derate"] == pytest.approx(expect)


# ── the route ────────────────────────────────────────────────────────────

def test_the_copt_route_discloses_profile_units_and_counts_must_take_from_the_walk():
    """★ A11′'s unit half: on 12a's two-farm fixture, `GET /results/copt`
    equals the mixture computed independently here, names the profiled farm,
    counts the must-take farm from the walk, and carries the sentence."""
    import routers.results as R
    from services.pypsa_service import PyPSAService
    from tests.test_adequacy_occurrence import _two_farm_network

    n = _two_farm_network()
    PyPSAService.set_network(n)
    out = R.get_copt()
    prof = [0.05, 0.15, 0.35, 0.45] * 2

    def surv(x):                      # gas1 alone: 80 MW, q = 0.10
        return 1.0 if x <= 0 else (0.9 if x <= 80.0 else 0.0)
    expected = sum(0.1 * (1.0 - surv(100.0 - 100.0 * p))
                   + 0.9 * (1.0 - surv(100.0 - 200.0 * p)) for p in prof)
    assert out["metrics"]["lole_hours"] == pytest.approx(expected, abs=1e-9)
    assert out["fleet"]["profile_units"] == ["wind_with_for"]
    assert out["fleet"]["netted_beyond_cap"] == []
    assert out["fleet"]["k_exact"] == C.K_EXACT
    assert out["fleet"]["must_take"] == 1
    assert out["fleet"]["units"] == 2
    assert "wind_with_for" in out["fidelity_note"]
    assert {r["name"] for r in out["per_mode"]} == {"gas1", "wind_with_for"}
    assert not any("note" in r for r in out["per_mode"])
    assert out["fidelity"] == "analytic_convolution"   # the enum is untouched

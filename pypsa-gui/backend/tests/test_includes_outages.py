"""
Phase 12h — the static capacity factor is applied, and "it already includes
outages" is a flag the asset carries.

Plan: `docs/superpowers/plans/2026-09-06-fmea-phase12h-static-cf-includes-outages-v6.md`
(five adversarial reviews, 61 findings, five blockers; v5 accepted with seven
changes, applied in v6).

The finding it adjudicates is 12c-pre's fourteenth: a generator carrying a
STATIC `p_max_pu < 1` and outage data was read differently by the engines and
by the reserve margin, in two directions at once. Measured on one fixture
(168 h, load 100 MW flat; `nuc` 100 MW static 0.8, q = 0.05 EFORd MTTR 100 h;
`gas` 25 MW q = 0.05):

    how the unit is read                      LOLE      EUE     derate
    today  (engines ignore the static CF)     8.40 h   640.5    0.76
    CF is an availability: CF *and* outages  16.38 h   800.1    0.76
    CF already includes outages: rate zeroed  8.40 h   168.0    0.80

Neither surface can know which reading applies, because the static column
carries both meanings in the wild — a typed capacity factor on a farm, and
PyPSA-Eur's `nuclear_p_max_pu.csv`, a historical table that already contains
forced outages. So the reading becomes DATA: a per-asset bool
`p_max_pu_includes_outages` that zeroes the rate at
`occurrence.resolve_outage_params`, the ONE place from which every consumer
— both engines, the margin's derate, the net-load window, the worksheet, the
disclosures — reads `q`.

Every ★ below names the broken variant it must fail against.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pypsa
import pytest

import routers.network as N
import services.validation_service as V
from services.adequacy import copt as C
from services.adequacy import mc as M
from services.adequacy import occurrence as O
from services.pypsa_service import PyPSAService

FLAG = O.FLAG_COL

#: The three COPT readings of the LIVE suite's fixture (§0 with `gas` at
#: 50 MW / marginal cost 20). `smoke/qa_e2e.py::suite_S31` imports these, so
#: the live rows and the unit pin below can never disagree.
S31_EUE_PLAIN = 600.6        # the CF and the outage rate, both applied
S31_EUE_FLAG = 168.0         # the CF alone, the rate zeroed by the flag
S31_EUE_NAMEPLATE = 441.0    # before 12h: the static CF ignored entirely


# ── fixtures ──────────────────────────────────────────────────────────────

def s0_network(*, flag: bool = False, hours: int = 168) -> pypsa.Network:
    """The §0 fixture, whose three rows are pinned by H3b."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=hours, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "nuclear")
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC", country="AA")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "nuc", bus="b", carrier="nuclear", p_nom=100.0,
          marginal_cost=5.0, p_max_pu=0.8, outage_rate_value=0.05,
          outage_rate_basis="EFORd", mttr_hours=100.0)
    n.add("Generator", "gas", bus="b", carrier="gas", p_nom=25.0,
          marginal_cost=20.0, outage_rate_value=0.05,
          outage_rate_basis="EFORd", mttr_hours=100.0)
    O.normalise_flag_column(n)
    if flag:
        n.generators.at["nuc", FLAG] = True
    return n


def nine_unit_network(*, cap: float = 300.0, load: float = 600.0,
                      flag_g0: bool = True) -> pypsa.Network:
    """Nine units with informative VARYING series (the [0.05, 0.15, 0.35,
    0.45] tile, rolled one hour per unit) — nine profiled units against
    `K_EXACT = 8`, which is what makes the deterministic bucket observable:
    without it the flagged unit burns the ninth slot and displaces a real one
    into the netted approximation.

    The load is part of the pin, and BOTH values bite — measured on this
    fixture (`cap = 300`), shipped against the bucket removed:

        load 300: 0.036157 h against 0.051173 h
        load 600: 25.862225 h against 30.747938 h

    600 is the pinned one because its margin is the wider of the two. (An
    earlier docstring carried the plan's numbers, which were measured on a
    differently-sized prototype and are false of this fixture — shipped-code
    review, finding 7.)"""
    n = pypsa.Network()
    H = 168
    n.set_snapshots(pd.date_range("2030-01-01", periods=H, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "wind")
    n.add("Bus", "b", carrier="AC", country="AA")
    n.add("Load", "l", bus="b", p_set=load)
    tile = np.tile([0.05, 0.15, 0.35, 0.45], H // 4)
    cols = {}
    for i in range(9):
        g = f"g{i}"
        n.add("Generator", g, bus="b", carrier="wind", p_nom=cap,
              marginal_cost=0.0, outage_rate_value=0.05,
              outage_rate_basis="EFORd", mttr_hours=100.0)
        cols[g] = np.roll(tile, i)
    n.generators_t.p_max_pu = pd.DataFrame(cols, index=n.snapshots)
    O.normalise_flag_column(n)
    if flag_g0:
        n.generators.at["g0", FLAG] = True
    return n


def _screen(n):
    units, residual, w = C.fleet_and_residual(n)
    return units, C.screening_analysis(units, residual, weights=w, voll=0.0)


# ── H1a — the flag, at the one place the rate is resolved ─────────────────

def test_H1a_the_flag_zeroes_the_rate_only_where_there_is_something_to_fold():
    """★ H1a. `resolve_outage_params` is the ONE place every consumer reads
    `q` from, so zeroing there resolves both engines and the margin at once.
    It fires only for a unit that HAS a sub-1 availability — there is nothing
    for outages to be "already included in" otherwise — and never for a row
    with no outage data at all.

    Bite (verified): ignore the flag; drop the sub-1 guard; apply it to a
    `source == "missing"` row.
    """
    n = s0_network(flag=True)
    n.add("Generator", "firm", bus="b", carrier="nuclear", p_nom=10.0,
          outage_rate_value=0.07, outage_rate_basis="FOR", mttr_hours=50.0)
    n.add("Generator", "musttake", bus="b",
          carrier="unknown_carrier", p_nom=10.0, p_max_pu=0.5)
    O.normalise_flag_column(n)
    n.generators.loc[["firm", "musttake"], FLAG] = True

    params = O.resolve_outage_params(n, "generators")
    assert params.at["nuc", "rate"] == 0.0
    assert bool(params.at["nuc", "outages_in_availability"]) is True
    # Availability 1: the flag has nothing to act on, the rate stands.
    assert params.at["firm", "rate"] == pytest.approx(0.07)
    assert bool(params.at["firm", "outages_in_availability"]) is False
    # No outage data at all: untouched, still `missing`.
    assert params.at["musttake", "source"] == "missing"
    # And the unflagged sibling keeps its rate.
    assert params.at["gas", "rate"] == pytest.approx(0.05)


@pytest.mark.parametrize("raw,expect", [
    (True, True), (np.True_, True), (1, True), (1.0, True), (0.5, True),
    ("true", True), ("TRUE", True), ("1", True), ("yes", True),
    (False, False), (np.False_, False), (0, False), (0.0, False),
    ("false", False), ("False", False), ("", False), ("no", False),
    (None, False), (float("nan"), False), (float("inf"), False),
])
def test_H1a_flag_is_set_is_the_only_reader_and_is_total(raw, expect):
    """★ `bool(nan)` is True, and a bool column that has been through an
    `object` phase can hold a string. One reader, total over everything a
    frame can hold. Bite (verified): use `bool(v)` — NaN and "False" both
    read as SET."""
    assert O.flag_is_set(raw) is expect


def test_H1a_the_normaliser_creates_the_column_as_bool():
    """★ A first `n.add` on a frame lacking the column creates it as
    `object`, and an `object` column of PURE bools is the one shape netCDF
    refuses — so the next project save is a 500. Bite (verified): drop the
    `.astype(bool)`."""
    n = pypsa.Network()
    n.add("Bus", "b")
    n.add("Generator", "g", bus="b", p_nom=1.0)
    assert FLAG not in n.generators.columns
    O.normalise_flag_column(n)
    assert n.generators[FLAG].dtype == bool
    assert bool(n.generators.at["g", FLAG]) is False
    # And it fixes an already-broken column rather than only creating one.
    n.generators[FLAG] = pd.Series(["true", ], index=n.generators.index,
                                   dtype=object)
    O.normalise_flag_column(n)
    assert n.generators[FLAG].dtype == bool
    assert bool(n.generators.at["g", FLAG]) is True


def test_H1a_the_normaliser_repairs_a_float_column():
    """★ The normaliser's float leg, on its own. This does NOT bite the
    import helper — it never calls it (shipped-code review, finding 5); the
    helper's own bite is `test_H1b_an_older_saves_float_column_loads_as_bool`
    below."""
    n = s0_network()
    n.generators[FLAG] = pd.Series([1.0, 0.0], index=n.generators.index,
                                   dtype="float64")
    O.normalise_flag_column(n)
    assert n.generators[FLAG].dtype == bool
    assert list(n.generators[FLAG]) == [True, False]


def test_H1b_an_older_saves_float_column_loads_as_bool(tmp_path):
    """★ H1b's load-side leg, through `PyPSAService.import_network_from_netcdf`.

    A clean `bool` column round-trips unaided, so the fixture that bites the
    IMPORT helper is a float one — an older save, or an externally produced
    netCDF. Writing it needs `n.export_to_netcdf` directly: the export helper
    would normalise it on the way out and there would be nothing to repair.

    Bite (verified): drop the normalise from the import helper — the column
    loads back `float64` and every later write keeps it that way.
    """
    n = s0_network()
    n.generators[FLAG] = pd.Series([1.0, 0.0], index=n.generators.index,
                                   dtype="float64")
    path = tmp_path / "older_save.nc"
    n.export_to_netcdf(str(path))

    back = pypsa.Network()
    PyPSAService.import_network_from_netcdf(back, path)
    assert back.generators[FLAG].dtype == bool, back.generators[FLAG].dtype
    assert bool(back.generators.at["nuc", FLAG]) is True
    assert bool(back.generators.at["gas", FLAG]) is False


# ── H2a — the reserve margin moves with it ────────────────────────────────

def test_H2a_the_margin_derate_reads_080_with_the_flag_and_076_without():
    """★ H2a. The margin's derate is `(1 - q) x avail`, and it reads `q`
    from the same frame — so the flag moves it with nothing else changed.
    0.8 x 0.95 = 0.76 unflagged; 0.80 flagged.

    Bite (verified): as H1a — with the flag ignored both read 0.76.
    """
    from services.solver_service import SolverConfig, reserve_margin_facts

    def derate(flag: bool) -> float:
        n = s0_network(flag=flag)
        facts = reserve_margin_facts(n, SolverConfig(reserve_margin=0.1))
        row = next(r for r in facts["stash"]["assets"] if r["name"] == "nuc")
        return row["derate"]

    assert derate(False) == pytest.approx(0.76, abs=1e-9)
    assert derate(True) == pytest.approx(0.80, abs=1e-9)


# ── H2b — a rate-zero PROFILED unit leaves the mixture, and nothing else ──

def test_H2b_a_rate_zero_profiled_unit_gets_its_own_bucket():
    """★ H2b. `split_fleet` gated on `profile is None` alone, so a flagged
    unit carrying a varying series stayed in the `2^k` mixture: it burned a
    `K_EXACT` slot for a unit with ONE state and displaced a real unit into
    the netted approximation. The bucket is exact — equal to raising
    `k_exact` so the rate-zero unit is mixed at q = 0 — and costs no slot.

    Bite (verified): drop the bucket. On this fixture the answer moves from
    25.862 h to 30.748 h, and `g8` is netted where nothing should be.
    """
    n = nine_unit_network()
    units, out = _screen(n)
    split = C.split_fleet(units)

    assert [u.name for u in split.deterministic] == ["g0"]
    assert len(split.mixed) == 8
    assert split.netted == ()

    assert out["metrics"]["lole_hours"] == pytest.approx(25.862224776, abs=1e-6)
    assert out["metrics"]["eue_mwh"] == pytest.approx(2093.304391, abs=1e-4)

    # The exact reference: mix ALL nine (g0 at q = 0) with no bucket at all.
    residual = C.fleet_and_residual(n)[1]
    weights = C.fleet_and_residual(n)[2]
    dist = C.build_copt([u for u in units if u.profile is None], delta_mw=1.0)
    ref = C.hourly_adequacy(dist, residual, weights=weights,
                            mixed=tuple(u for u in units if u.profile is not None))
    assert out["metrics"]["lole_hours"] == pytest.approx(ref["lole_hours"], abs=1e-9)
    assert out["metrics"]["eue_mwh"] == pytest.approx(ref["eue_mwh"], abs=1e-9)


def test_H2b_the_unit_stays_in_units_and_keeps_its_FMECA_row():
    """★ H2b, the half the v2 review measured: netting the unit OUT of
    `units` removes it from the portfolio population, from `elcc_candidates`
    and from the FMECA rows while the margin keeps its row — and shifts every
    downstream unit's CRN substream, because `sample_capacity` spawns child
    streams BY POSITION. So it stays in `units` and only the split changes.

    Bites (verified): net it out of `units`; drop `deterministic=()` from
    `attribute_criticality` (the row vanishes).
    """
    from services.adequacy import elcc as E

    n = nine_unit_network()
    units, out = _screen(n)
    assert "g0" in [u.name for u in units]
    assert "g0" in {r["name"] for r in E.elcc_candidates(n)}

    row = next(r for r in out["rows"] if r["name"] == "g0")
    assert row["delta_eue_mwh"] == pytest.approx(0.0)
    assert "note" in row and "already include" in row["note"]
    # Every unit still has exactly one row.
    assert len(out["rows"]) == len(units)


def test_H2b_the_bucket_survives_the_multi_period_merge():
    """★ H2b. The 12d per-block merge rebuilds `FleetSplit` from name sets, so
    a fourth bucket that the merge does not rebuild vanishes on every
    multi-period network. Rebuilt as ONE FIELD, not as a merge-only extra —
    a merge-only field is empty on every single-block network, which is the
    ordinary case (v4 review).

    Bite (verified): rebuild the merge without `deterministic`.
    """
    n = nine_unit_network()
    # Give one unit a build year so `screening_analysis` takes the per-block
    # path; the flagged unit is untouched by it.
    idx = pd.MultiIndex.from_arrays(
        [np.where(np.arange(168) < 84, 2030, 2035),
         n.snapshots],
        names=["period", "timestep"])
    n.snapshots = idx
    n.investment_periods = [2030, 2035]
    n.generators.loc["g5", "build_year"] = 2035

    units, out = _screen(n)
    assert any(u.capacity_series is not None for u in units), \
        "fixture must take the per-block path"
    assert [u.name for u in out["split"].deterministic] == ["g0"]


# ── H2c — a flagged CONSTANT series, the case the v6 narrowing creates ────

def test_H2c_a_flagged_constant_series_unit_lands_in_the_bucket():
    """★ H2c. v6 folds the STATIC cell only; a constant SERIES is left alone
    (that is 12i). So a flagged constant-series unit keeps its profile, has
    rate 0, and must land in `deterministic` — where it reads exactly what a
    fold would have given it, and never worse: the bucket nets in float and
    costs no `K_EXACT` slot.

    Bite (verified): leave it in the mixture.
    """
    n = s0_network(flag=True)
    n.generators_t.p_max_pu = pd.DataFrame(
        {"nuc": np.full(len(n.snapshots), 0.8)}, index=n.snapshots)
    units, out = _screen(n)

    nuc = next(u for u in units if u.name == "nuc")
    assert nuc.profile is not None and nuc.q == 0.0
    # The column supersedes the static cell, so there is no fold.
    assert nuc.folded_constant is None
    assert nuc.capacity_mw == pytest.approx(100.0)
    assert [u.name for u in out["split"].deterministic] == ["nuc"]
    assert out["metrics"]["eue_mwh"] == pytest.approx(168.0, abs=1e-6)


# ── H3 — the engines apply a static CF ────────────────────────────────────

def test_H3b_the_three_rows_of_the_premise_table():
    """★ H3b. The §0 table, by hand. Unflagged: the CF and the rate are both
    applied (16.38 h / 800.1 MWh). Flagged: the CF alone (8.40 h / 168.0).

    Bite (verified): drop the capacity scaling — the unflagged row reads the
    nameplate answer, 8.40 h / 640.5 MWh, to the digit.
    """
    _u, plain = _screen(s0_network(flag=False))
    assert plain["metrics"]["lole_hours"] == pytest.approx(16.38, abs=1e-6)
    assert plain["metrics"]["eue_mwh"] == pytest.approx(800.1, abs=1e-4)

    _u, flagged = _screen(s0_network(flag=True))
    assert flagged["metrics"]["lole_hours"] == pytest.approx(8.40, abs=1e-6)
    assert flagged["metrics"]["eue_mwh"] == pytest.approx(168.0, abs=1e-6)


def test_H3a_a_static_CF_equals_a_scaled_capacity_equals_a_constant_series():
    """★ H3a. Three routes to the same physics. The third leg is a
    cross-route ORACLE against a path v6 deliberately leaves alone (a
    constant series is not folded here; that is 12i), not a claim about it.

    On grid the three agree to 1e-9. OFF grid (cf = 0.833 on a 1 MW grid) the
    scaled unit's capacity is apportioned across two grid states, so EUE
    agrees within 5e-3 relative and NO LOLE equality is asserted — that is
    the table's existing discretisation, the same one every non-integer
    nameplate has always had.

    Bite (verified): drop the capacity scaling — the static route reads the
    nameplate answer.
    """
    def by_static(cf):
        n = s0_network()
        n.generators.at["nuc", "p_max_pu"] = cf
        return _screen(n)[1]["metrics"]

    def by_capacity(cf):
        n = s0_network()
        n.generators.at["nuc", "p_max_pu"] = 1.0
        n.generators.at["nuc", "p_nom"] = 100.0 * cf
        return _screen(n)[1]["metrics"]

    def by_series(cf):
        n = s0_network()
        n.generators.at["nuc", "p_max_pu"] = 1.0
        n.generators_t.p_max_pu = pd.DataFrame(
            {"nuc": np.full(len(n.snapshots), cf)}, index=n.snapshots)
        return _screen(n)[1]["metrics"]

    on_grid = [by_static(0.8), by_capacity(0.8), by_series(0.8)]
    for key in ("lole_hours", "eue_mwh"):
        assert on_grid[1][key] == pytest.approx(on_grid[0][key], abs=1e-9), key
        assert on_grid[2][key] == pytest.approx(on_grid[0][key], abs=1e-9), key

    off = [by_static(0.833), by_capacity(0.833), by_series(0.833)]
    assert off[1]["eue_mwh"] == pytest.approx(off[0]["eue_mwh"], abs=1e-9)
    assert off[0]["eue_mwh"] == pytest.approx(off[2]["eue_mwh"], rel=5e-3)


def test_H3a_the_MC_is_bit_identical_between_the_two_routes():
    """★ H3a's MC leg: the sampler has no discretisation term at all, so the
    scaled and the nameplate-with-series routes must agree exactly under one
    seed."""
    n_static = s0_network()
    n_cap = s0_network()
    n_cap.generators.at["nuc", "p_max_pu"] = 1.0
    n_cap.generators.at["nuc", "p_nom"] = 80.0

    a = M.mc_adequacy(M.snapshot_inputs(n_static), draws=8, seed=11)
    b = M.mc_adequacy(M.snapshot_inputs(n_cap), draws=8, seed=11)
    assert a["lole_hours"] == b["lole_hours"]
    assert a["eue_mwh"] == b["eue_mwh"]


def test_H3c_the_fold_scales_the_per_period_capacity_series_too():
    """★ H3c. 12d gives an asset with a build year a per-period capacity
    SERIES in MW, and `activity.block_capacity` reads the block's constant
    value from it. Scale only the scalar and every block capacity is left
    unscaled.

    Bite (verified): scale `capacity_mw` alone — the series reads 100, not 80.
    """
    n = pypsa.Network()
    idx = pd.MultiIndex.from_product(
        [[2030, 2035], pd.date_range("2030-01-01", periods=2, freq="h")],
        names=["period", "timestep"])
    n.set_snapshots(idx)
    n.investment_periods = [2030, 2035]
    n.add("Carrier", "nuclear")
    n.add("Bus", "b", carrier="AC", country="AA")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "nuc", bus="b", carrier="nuclear", p_nom=100.0,
          p_max_pu=0.8, build_year=2035, lifetime=30,
          outage_rate_value=0.05, outage_rate_basis="EFORd", mttr_hours=100.0)

    unit = next(u for u in C.fleet_and_residual(n)[0] if u.name == "nuc")
    assert unit.capacity_mw == pytest.approx(80.0)
    assert unit.folded_constant == pytest.approx(0.8)
    assert list(unit.capacity_series) == pytest.approx([0.0, 0.0, 80.0, 80.0])


def test_H3d_the_must_take_branch_applies_the_static_exactly_once():
    """★ H3d. `fleet_and_residual`'s must-take branch already applies the
    static column itself, so a fold placed before the branch SQUARES it for
    every must-take farm.

    Bite (verified): apply the fold before the `source != "missing"` branch —
    the netted output drops from 20 MW to 8 MW and LOLE goes 1.2 -> 24.0.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "solar")
    n.add("Bus", "b", carrier="AC", country="AA")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "pv", bus="b", carrier="solar", p_nom=50.0, p_max_pu=0.4)

    units, residual, _w = C.fleet_and_residual(n)
    assert units == []                       # no occurrence data: must-take
    assert list(residual.values) == pytest.approx([80.0] * 4)   # 100 - 0.4x50


def test_H3e_an_all_ones_column_supersedes_the_static_and_blocks_the_fold():
    """★ H3e. The gate is the PRESENCE of a `p_max_pu` column, not whether
    the column is informative. PyPSA lets any column supersede the static
    cell — measured, a static 0.8 beside an all-ones column dispatches at
    1.0 — and `reserve_margin_facts` reads the column the same way, so a fold
    here would invent a 20 % derate that neither the LP nor the margin
    applies. An all-ones column is exactly the shape two shipped fixtures
    carry.

    Bite (verified): gate on `series_is_informative` — the capacity reads 80
    and the engines disagree with the LP by 20 %.
    """
    from services.solver_service import SolverConfig, reserve_margin_facts

    n = s0_network()
    n.generators_t.p_max_pu = pd.DataFrame(
        {"nuc": np.ones(len(n.snapshots))}, index=n.snapshots)

    unit = next(u for u in C.fleet_and_residual(n)[0] if u.name == "nuc")
    assert unit.capacity_mw == pytest.approx(100.0)
    assert unit.folded_constant is None
    assert unit.profile is None              # all-ones is not informative

    facts = reserve_margin_facts(n, SolverConfig(reserve_margin=0.1))
    row = next(r for r in facts["stash"]["assets"] if r["name"] == "nuc")
    assert row["derate"] == pytest.approx(0.95, abs=1e-9)


def test_S31_the_live_suites_fixture_and_its_three_hand_values():
    """★ The COPT rows S31 reads over HTTP, pinned HERE so the live suite
    compares against numbers this suite owns.

    S31's fixture is §0 with `gas` at 50 MW / marginal cost 20, not 25 MW: at
    25 the derated firm capacity is 99.75 MW against a 110 MW requirement and
    preflight refuses `reserve_margin_unreachable` before a solve can persist
    the margin stash `GET /results/reserve_margin` serves.

    Bite (verified): drop the capacity scaling — S31.2 reads the 441.0
    nameplate row.
    """
    def fixture(flag: bool) -> pypsa.Network:
        n = s0_network(flag=flag)
        n.generators.at["gas", "p_nom"] = 50.0
        n.generators.at["gas", "marginal_cost"] = 20.0
        return n

    plain = _screen(fixture(False))[1]["metrics"]
    assert plain["lole_hours"] == pytest.approx(16.38, abs=1e-6)
    assert plain["eue_mwh"] == pytest.approx(S31_EUE_PLAIN, abs=1e-4)

    flagged = _screen(fixture(True))[1]["metrics"]
    assert flagged["lole_hours"] == pytest.approx(8.40, abs=1e-6)
    assert flagged["eue_mwh"] == pytest.approx(S31_EUE_FLAG, abs=1e-6)

    # And the row 12h replaced: the same fleet read at nameplate.
    from dataclasses import replace
    units, residual, w = C.fleet_and_residual(fixture(False))
    units = [replace(u, capacity_mw=100.0, folded_constant=None)
             if u.name == "nuc" else u for u in units]
    before = C.screening_analysis(units, residual, weights=w,
                                  voll=0.0)["metrics"]
    assert before["eue_mwh"] == pytest.approx(S31_EUE_NAMEPLATE, abs=1e-4)


# ── shipped-code review: three defects the plan's own rules imply ────────

def test_R1_a_flagged_unit_behind_an_all_ones_column_keeps_its_rate():
    """★ Shipped-code review, finding 1 (SERIOUS). H2's third condition says
    the flag acts only where the availability is SUB-1, because zeroing the
    rate of an availability-1 unit does not "have no effect" — it makes the
    unit perfectly firm, the maximal effect.

    `_availability_is_sub_one` gated on the column being INFORMATIVE and then
    fell back to the static cell, while the fold gates on the column's
    PRESENCE. The two disagreed on exactly one population — a static below 1
    beside an all-ones column, the shape two shipped fixtures carry — and
    there the flag zeroed a live 5 % rate.

    Measured on the §0 fixture with an all-ones column and the flag set:
    LOLE 8.40 h -> 0.00, EUE 640.5 -> 0.0, margin derate 0.95 -> 1.00. (On
    the S31 fixture, whose `gas` is 50 MW, the same reading is 441.0 -> 0.0.)

    640.5 MWh is exactly the §0 table's "today" row, and it should be: an
    all-ones column supersedes the static cell, so this unit IS nameplate 100
    at q = 0.05 on every surface.

    Bite (verified): restore the fallback — every assertion below moves.
    """
    from services.solver_service import SolverConfig, reserve_margin_facts

    def read(flag: bool):
        n = s0_network(flag=flag)
        n.generators_t.p_max_pu = pd.DataFrame(
            {"nuc": np.ones(len(n.snapshots))}, index=n.snapshots)
        rate = float(O.resolve_outage_params(n, "generators").at["nuc", "rate"])
        m = _screen(n)[1]["metrics"]
        row = next(r for r in reserve_margin_facts(
            n, SolverConfig(reserve_margin=0.1))["stash"]["assets"]
            if r["name"] == "nuc")
        codes = {i.code for i in V.__dict__["_check_profiled_occurrence_units"](n)}
        return rate, m, row["derate"], codes

    plain = read(False)
    flagged = read(True)
    # The column supersedes the static cell everywhere, so the flag has
    # nothing to act on and the two readings are IDENTICAL.
    assert flagged[0] == pytest.approx(0.05)
    assert flagged[1]["lole_hours"] == pytest.approx(plain[1]["lole_hours"])
    assert flagged[1]["eue_mwh"] == pytest.approx(plain[1]["eue_mwh"])
    assert flagged[1]["eue_mwh"] == pytest.approx(640.5, abs=1e-4)
    assert flagged[2] == pytest.approx(0.95, abs=1e-9)
    # And preflight says the flag was ignored rather than claiming a fold.
    assert "outages_folded_into_availability_ignored" in flagged[3], flagged[3]
    assert "outages_folded_into_availability" not in flagged[3], flagged[3]


@pytest.mark.parametrize("static", [1.5, 2.0, 100.0])
def test_R2_a_static_above_one_is_not_folded_so_the_margin_still_agrees(static):
    """★ Shipped-code review, finding 2 (SERIOUS). `reserve_margin_facts`
    clamps `avail_static` to [0, 1]. Folding an unclamped 1.5 credited the
    engines 142.5 MW firm where the margin credited 95.0 — a 50 % divergence,
    and the very class of defect 12h exists to close, re-created on a new
    input. Before 12h the two AGREED here, both at nameplate.

    Declining to fold IS the clamp to 1, so the agreement is restored exactly.

    Bite (verified): fold any finite `cf != 1` — the engine capacity reads
    `100 x static` while the margin stays at 95.
    """
    from services.solver_service import SolverConfig, reserve_margin_facts

    n = s0_network()
    n.generators.at["nuc", "p_max_pu"] = static
    unit = next(u for u in C.fleet_and_residual(n)[0] if u.name == "nuc")
    assert unit.capacity_mw == pytest.approx(100.0)
    assert unit.folded_constant is None

    row = next(r for r in reserve_margin_facts(
        n, SolverConfig(reserve_margin=0.1))["stash"]["assets"]
        if r["name"] == "nuc")
    assert unit.capacity_mw * (1.0 - 0.05) == pytest.approx(
        row["derate"] * 100.0, abs=1e-9)


def test_R3_a_negative_static_does_not_fold_and_copt_still_serves():
    """★ Shipped-code review, finding 3 (SERIOUS). The schema accepts a
    negative `p_max_pu` — it checks finiteness, not range — and folding it
    gave a NEGATIVE `capacity_mw`, which `_shift_deterministic` cannot index:
    `GET /results/copt` became a 500 on a network that returned 200 before
    12h.

    Bite (verified): fold any finite `cf != 1` — `ValueError: operands could
    not be broadcast together with shapes (0,) (52,) (0,)`.
    """
    import routers.results as R

    n = s0_network()
    n.generators.at["nuc", "p_max_pu"] = -0.5
    unit = next(u for u in C.fleet_and_residual(n)[0] if u.name == "nuc")
    assert unit.capacity_mw == pytest.approx(100.0)
    assert unit.folded_constant is None

    _install(n)
    out = R.get_copt()                      # a 500 before the fix
    assert out["metrics"]["eue_mwh"] >= 0.0


def test_R3_a_static_of_ZERO_still_folds_to_nothing():
    """★ The other end of the new gate, kept deliberately: `p_max_pu = 0` is
    the ordinary "this unit is off for this study" idiom, and folding it to
    0 MW is the honest reading. Measured to reach the COPT without raising.
    """
    n = s0_network()
    n.generators.at["nuc", "p_max_pu"] = 0.0
    unit = next(u for u in C.fleet_and_residual(n)[0] if u.name == "nuc")
    assert unit.capacity_mw == pytest.approx(0.0)
    assert unit.folded_constant == pytest.approx(0.0)
    assert _screen(n)[1]["metrics"]["eue_mwh"] > 0.0


def test_R6_preflight_skips_the_second_walk_when_no_flag_is_set():
    """★ Shipped-code review, finding 6 (MINOR, performance). The
    `_ignored`-from-the-membership-walk loop was gated on the COLUMN
    existing, and the normaliser creates that column on essentially every
    network — so a second full membership walk ran on every preflight and
    every solve. Measured on 300 generators x 8760 snapshots with no flag
    set: 423 ms without it against 963 ms with it.

    Bite (verified): gate on `flags is not None` — the walk runs anyway. This
    test pins the OBSERVABLE half (the walk is skipped, so a must-take
    flagged unit is still named when a flag IS set) rather than a timing.
    """
    quiet = s0_network()
    assert FLAG in quiet.generators.columns          # the column always exists
    assert not any(O.flag_is_set(v) for v in quiet.generators[FLAG])
    codes = {i.code for i in V.__dict__["_check_profiled_occurrence_units"](quiet)}
    assert "outages_folded_into_availability_ignored" not in codes

    # One flag set anywhere re-enables it, so the guard cannot hide a unit.
    loud = s0_network()
    loud.add("Generator", "farm", bus="b", carrier="unknown_carrier",
             p_nom=10.0, p_max_pu=0.5)
    O.normalise_flag_column(loud)
    loud.generators.at["farm", FLAG] = True
    msg = _codes(V.__dict__["_check_profiled_occurrence_units"](loud),
                 "outages_folded_into_availability_ignored")
    assert "farm" in msg, msg


# ── H4 — preflight tells the truth again ─────────────────────────────────

def _codes(issues, code):
    return " ".join(i.message for i in issues if i.code == code)


def test_H4a_the_retired_code_is_gone_everywhere():
    """★ H4a. `static_p_max_pu_not_applied` said "the COPT and the sequential
    MC do NOT apply it". That sentence is false after H3, so the code is
    retired rather than reworded. Bite (verified): keep it."""
    from services.solver_service import SolverConfig

    for n in (s0_network(), s0_network(flag=True), nine_unit_network()):
        issues = V.validate_for_run(n, SolverConfig())
        assert not [i for i in issues
                    if i.code == "static_p_max_pu_not_applied"], issues


def test_H4a_the_fold_population_is_told_both_are_applied_and_named_the_flag():
    """★ H4a. `availability_may_include_outages` fires on exactly the units
    the fold touches — occurrence unit, live rate, flag clear, static below 1
    and NO column — so its sentence is true of every unit in it.

    Bite (verified): fire it beside a column too — the message would claim a
    derate that nothing applies.
    """
    from services.solver_service import SolverConfig

    issues = V.validate_for_run(s0_network(), SolverConfig())
    msg = _codes(issues, "availability_may_include_outages")
    assert "nuc" in msg and "gas" not in msg.split(":")[1].split(".")[0], msg
    assert "p_max_pu_includes_outages" in msg, msg
    assert all(i.severity == "warning" for i in issues
               if i.code == "availability_may_include_outages")


def test_H4b_a_flagged_unit_gets_one_sentence_not_two():
    """★ H4b. A flagged asset-typed unit has q = 0, so it LEAVES
    `profile_and_outage_modelled` (whose population keeps its `q > 0` gate)
    and enters `outages_folded_into_availability`. One code per unit.

    Bite (verified): emit the folded code without the q gate on the other —
    the unit is named twice, with two different stories.
    """
    n = s0_network(flag=True)
    n.generators_t.p_max_pu = pd.DataFrame(
        {"nuc": np.tile([0.6, 0.9], len(n.snapshots) // 2)}, index=n.snapshots)
    issues = V.__dict__["_check_profiled_occurrence_units"](n)
    named = [i.code for i in issues if "nuc" in i.message]
    assert named == ["outages_folded_into_availability"], named


@pytest.mark.parametrize("shape", ["availability_one", "no_outage_data"])
def test_H4c_the_ignored_variant_is_reachable_at_both_routes(shape):
    """★ H4c. The flag set with nothing to act on. The second route matters:
    a unit with NO outage data never reaches `occurrence_units`, so its
    sentence has to come from the membership walk itself.

    Bite (verified): walk only `occurrence_units` — the must-take case goes
    silent and the user is never told the flag they set does nothing.
    """
    n = s0_network()
    if shape == "availability_one":
        n.generators.at["nuc", "p_max_pu"] = 1.0
        n.generators.at["nuc", FLAG] = True
        who = "nuc"
    else:
        n.add("Generator", "farm", bus="b", carrier="unknown_carrier",
              p_nom=10.0, p_max_pu=0.5)
        O.normalise_flag_column(n)
        n.generators.at["farm", FLAG] = True
        who = "farm"
    msg = _codes(V.__dict__["_check_profiled_occurrence_units"](n),
                 "outages_folded_into_availability_ignored")
    assert who in msg, msg


def test_H4c_an_unflagged_constant_series_unit_is_still_named():
    """★ H4c. `profile_and_outage_modelled`'s population is UNCHANGED —
    asset-typed, an INFORMATIVE series (constant or varying), q > 0 — because
    v6 does not fold a constant series and such a unit is still mixed exactly
    per hour. A draft that narrowed it to varying series would have left an
    unflagged constant-series unit named by NOTHING.

    Bite (verified): narrow the population to varying series.
    """
    n = s0_network()
    n.generators_t.p_max_pu = pd.DataFrame(
        {"nuc": np.full(len(n.snapshots), 0.4)}, index=n.snapshots)
    issues = V.__dict__["_check_profiled_occurrence_units"](n)
    assert "nuc" in _codes(issues, "profile_and_outage_modelled")


def test_H4c_the_modelled_message_names_the_flag_not_a_false_remedy():
    """★ H4c. The old remedy was "remove the outage rate", which a
    carrier-default unit cannot do. It names the flag instead."""
    n = s0_network()
    n.generators_t.p_max_pu = pd.DataFrame(
        {"nuc": np.tile([0.6, 0.9], len(n.snapshots) // 2)}, index=n.snapshots)
    msg = _codes(V.__dict__["_check_profiled_occurrence_units"](n),
                 "profile_and_outage_modelled")
    assert "p_max_pu_includes_outages" in msg, msg
    assert "Remove the outage rate" not in msg, msg


# ── H4d — the payloads say it too ────────────────────────────────────────

def test_H4d_copt_carries_folded_and_deterministic_units():
    """★ H4d. Driven through `routers.results.get_copt` — the SHIPPED payload,
    not a re-implementation of its comprehension. The first version of this
    test rebuilt the list inside the test and asserted on its own output, so
    deleting both keys from the route left it green (shipped-code review,
    finding 4).

    A folded unit is in NO existing list (it has no profile), and a
    deterministic unit LEAVES `profile_units`, which is `mixed + netted`.

    Bites (verified): drop either key from `get_copt`.
    """
    import routers.results as R

    _install(s0_network(flag=True))
    out = R.get_copt()
    fleet = out["fleet"]
    assert fleet["folded_units"] == [
        {"name": "nuc", "folded_constant": 0.8, "source": "static"}]
    # Static-folded but not deterministic: the fold and the bucket are
    # different mechanisms, and this unit has no profile to be netted.
    assert fleet["deterministic_units"] == []

    # A profiled rate-zero unit is the other half.
    _install(nine_unit_network())
    fleet9 = R.get_copt()["fleet"]
    assert fleet9["deterministic_units"] == ["g0"]
    assert "g0" not in fleet9["profile_units"]
    assert fleet9["folded_units"] == []


def test_H4d_copt_lists_survive_the_multi_period_path():
    """★ H4d, the multi-block leg: `screening_analysis` rebuilds the split
    per period block, and the payload reads the TOP-LEVEL `units` list for
    `folded_units` and the MERGED split for `deterministic_units`."""
    import routers.results as R

    n = nine_unit_network()
    n.snapshots = pd.MultiIndex.from_arrays(
        [np.where(np.arange(168) < 84, 2030, 2035), n.snapshots],
        names=["period", "timestep"])
    n.investment_periods = [2030, 2035]
    n.generators.loc["g5", "build_year"] = 2035
    _install(n)

    fleet = R.get_copt()["fleet"]
    assert fleet["deterministic_units"] == ["g0"]
    assert "g0" not in fleet["profile_units"]


def test_H4d_mc_lists_are_disjoint_and_profile_units_stays_true(
        client, install_network):
    """★ H4d. Driven through `POST /api/results/mc` — the SHIPPED payload.
    `/mc` never calls `split_fleet` and builds `profile_units` as "every unit
    with a profile", so a deterministic unit would stay in a list whose
    documented meaning ("outages were sampled on the availability series") is
    false of a q = 0 unit. The two lists are built separately there and must
    be DISJOINT.

    Bite (verified): leave a deterministic unit in `profile_units`.
    """
    import time as _t

    install_network(nine_unit_network())
    r = client.post("/api/results/mc", json={"draws": 4, "seed": 7})
    assert r.status_code == 200, r.text

    deadline = _t.time() + 300.0
    body = None
    while _t.time() < deadline:
        body = client.get("/api/results/mc").json()
        if body.get("status") in ("done", "failed"):
            break
        _t.sleep(0.05)
    assert body and body["status"] == "done", body

    res = body["result"]
    assert res["deterministic_units"] == ["g0"]
    assert "g0" not in res["profile_units"]
    assert not set(res["profile_units"]) & set(res["deterministic_units"])
    assert len(res["profile_units"]) + len(res["deterministic_units"]) == 9
    assert res["folded_units"] == []


def test_H4d_reserve_margin_does_not_list_a_rate_zero_unit_as_carrier_default():
    """★ H4d. A rate-zero unit's derate uses NO class average — it is the
    availability alone — so listing it under `carrier_default` makes
    preflight's "derates N assets using carrier class averages" false of it.
    Pinned on a LIBRARY-rate fixture, because on §0 both units carry typed
    rates and the list is empty either way.

    Bite (verified): drop the rate skip.
    """
    from services.solver_service import SolverConfig, reserve_margin_facts

    def carrier_default(flag: bool) -> list[str]:
        n = pypsa.Network()
        n.set_snapshots(pd.date_range("2030-01-01", periods=8, freq="h"))
        n.snapshot_weightings.loc[:, :] = 1.0
        n.add("Carrier", "coal")
        n.add("Bus", "b", carrier="AC", country="AA")
        n.add("Load", "l", bus="b", p_set=100.0)
        n.add("Generator", "lib", bus="b", carrier="coal", p_nom=100.0,
              p_max_pu=0.8)          # NO typed rate: the carrier library fills it
        O.normalise_flag_column(n)
        if flag:
            n.generators.at["lib", FLAG] = True
        return reserve_margin_facts(
            n, SolverConfig(reserve_margin=0.1))["carrier_default"]

    assert carrier_default(False) == ["lib"]
    assert carrier_default(True) == []


# ── H6 — the MC snapshot hash moves with the flag ────────────────────────

def test_H6_snapshot_hash_differs_across_a_flag_flip():
    """★ H6. The flag changes NOTHING else about a unit — same capacity, same
    availability series — so without a `q` term the hash collides while the
    MC result moves, and the certifying loops' plateau reuse hands back the
    wrong metrics.

    Bite (verified): drop the `q` term — the two hashes are equal.
    """
    from dataclasses import replace

    from services.adequacy.coupling import snapshot_hash

    a = M.snapshot_inputs(s0_network(flag=False))
    b = M.snapshot_inputs(s0_network(flag=True))
    assert snapshot_hash(a) != snapshot_hash(b)
    assert snapshot_hash(replace(a)) == snapshot_hash(a)


# ── H1b — the data model, every path the reviews broke ───────────────────

def _install(n):
    PyPSAService.set_network(n, allow_during_study=True)


def test_H1b_bulk_creates_the_column_on_a_frame_that_never_had_one():
    """★ H1b. This route is the one that must flag an IMPORT, whose frame has
    no such column at all — without the create-if-absent the unknown-column
    check refuses `has no column(s)` and there is no way to set the flag on
    an imported network.

    The column is dropped from the LIVE network, AFTER `set_network`: that
    boundary normalises too, so dropping it before the install would put the
    column back and the bite would not bite (found by running the mutation).

    Bite (verified): drop the `_bulk` normalise — HTTPException 400.
    """
    _install(s0_network())
    live = PyPSAService.get_network()
    del live.generators[FLAG]
    assert FLAG not in live.generators.columns

    N.bulk_update({"component_class": "Generator", "names": ["nuc"],
                   "updates": {FLAG: True}})
    got = PyPSAService.get_network().generators
    assert got[FLAG].dtype == bool
    assert bool(got.at["nuc", FLAG]) is True
    assert bool(got.at["gas", FLAG]) is False


def test_H1b_bulk_normalises_a_solve_shaped_column_BEFORE_the_dispatch():
    """★ H1b, the dtype half. After a solve the column is `object` (the slack
    rows are added on a frame lacking it and removed again), and `_bulk`'s
    dtype dispatch reads `df[col].dtype`: on an `object` column the bool
    branch is skipped and the STRING branch runs, storing `'True'`. That
    exports fine and `flag_is_set` reads it as set — only the dtype separates
    the two, and only until the next solve breaks the save.

    So the normalise must run BEFORE the dispatch, not after.

    Bite (verified): drop the `_bulk` normalise — the cell reads the string
    `'True'` in an `object` column.
    """
    _install(s0_network())
    live = PyPSAService.get_network()
    live.generators[FLAG] = live.generators[FLAG].astype(object)
    assert live.generators[FLAG].dtype == object

    N.bulk_update({"component_class": "Generator", "names": ["nuc"],
                   "updates": {FLAG: True}})
    got = PyPSAService.get_network().generators
    assert got[FLAG].dtype == bool, got[FLAG].dtype
    # The bite stores the STRING 'True' in an object column. Both halves are
    # asserted, because either alone is passed by the broken variant.
    assert not isinstance(got.at["nuc", FLAG], str), repr(got.at["nuc", FLAG])
    assert bool(got.at["nuc", FLAG]) is True


@pytest.mark.parametrize("sent,expect", [
    (False, False), ("false", False), ("True", True), (True, True), (None, False),
])
def test_H1b_bulk_writes_every_accepted_shape_as_a_bool(sent, expect):
    """★ H1b. The bite is on the DTYPE, not the value."""
    _install(s0_network(flag=True))
    N.bulk_update({"component_class": "Generator", "names": ["nuc"],
                   "updates": {FLAG: sent}})
    got = PyPSAService.get_network().generators
    assert got[FLAG].dtype == bool
    assert bool(got.at["nuc", FLAG]) is expect


@pytest.mark.parametrize("cls,comp,name,col,expect", [
    ("Generator", "generators", "nuc", "committable", False),
    ("Generator", "generators", "nuc", "p_nom_extendable", False),
])
def test_H1b_a_null_bool_clears_to_the_class_default(cls, comp, name, col, expect):
    """★ H1b. The bulk editor sends `null` for a blank cell, and each of
    these was a reachable 500: `df.loc[...] = None` upcasts the column to
    `object`, which netCDF refuses on the next save.

    Bite (verified): restore `bool(value) if value is not None else value`.
    """
    _install(s0_network())
    N.bulk_update({"component_class": cls, "names": [name],
                   "updates": {col: None}})
    got = getattr(PyPSAService.get_network(), comp)
    assert got[col].dtype == bool
    assert bool(got.at[name, col]) is expect


def test_H1b_link_cyclic_delay_clears_to_TRUE_from_the_metadata():
    """★ H1b. TWO of PyPSA's bool inputs default to True, not one: `active`
    on every class and `Link.cyclic_delay`, which is bulk-editable. An
    implementer working from a hand-written "bools clear to False" list would
    have silently flipped every selected link.

    Bite (verified): hard-code False — `cyclic_delay` reads False where PyPSA
    says True.
    """
    n = s0_network()
    n.add("Bus", "b2", carrier="AC")
    n.add("Link", "lk", bus0="b", bus1="b2", p_nom=10.0)
    if "cyclic_delay" not in n.links.columns:
        pytest.skip("this PyPSA has no Link.cyclic_delay")
    n.links.at["lk", "cyclic_delay"] = False
    _install(n)

    N.bulk_update({"component_class": "Link", "names": ["lk"],
                   "updates": {"cyclic_delay": None}})
    assert bool(PyPSAService.get_network().links.at["lk", "cyclic_delay"]) is True


def test_H1b_a_null_active_is_422_and_says_why():
    """★ H1b. `active` defaults to True, so clearing it would ACTIVATE every
    selected asset — behind a confirm toast that reads "Set active = (unset)
    on 200 generator(s)?". Refused, not written. 422 is the shape this route
    already uses for a value it could write but refuses on what the write
    would MEAN; 400 is its wrong-type answer.

    Bite (verified): let it fall through to the metadata default — 200, and
    every selected asset is silently activated.
    """
    from fastapi import HTTPException

    n = s0_network()
    n.generators.at["nuc", "active"] = False
    _install(n)

    with pytest.raises(HTTPException) as exc:
        N.bulk_update({"component_class": "Generator", "names": ["nuc"],
                       "updates": {"active": None}})
    assert exc.value.status_code == 422
    assert "true" in exc.value.detail and "false" in exc.value.detail
    assert bool(PyPSAService.get_network().generators.at["nuc", "active"]) is False


def test_H1b_an_explicit_null_PUT_is_200_and_reads_false():
    """★ H1b. A scripted PUT or a chat tool can send an explicit `null`. The
    schema's `None -> False` validator keeps that a 200 and a bool.

    Bite (verified): drop the validator — 422 on a value that means "the
    default".
    """
    from models.schemas import GeneratorCreate

    g = GeneratorCreate(name="x", bus="b", p_max_pu_includes_outages=None)
    assert g.p_max_pu_includes_outages is False
    assert GeneratorCreate(
        name="x", bus="b", p_max_pu_includes_outages=True
    ).p_max_pu_includes_outages is True


def test_H1b_a_solve_shaped_column_still_exports(tmp_path):
    """★ H1b, the defect the export helper exists for: the solve adds its
    VOLL/DSR slack rows on a frame lacking the column and removes them again,
    leaving an `object` column of PURE bools — the one shape netCDF refuses.
    Reproduced directly, then exported through the helper.

    Bite (verified): call `n.export_to_netcdf` instead of the helper —
    `unsupported dtype for netCDF4 variable: bool`.
    """
    n = s0_network(flag=True)
    n.generators[FLAG] = n.generators[FLAG].astype(object)
    assert n.generators[FLAG].dtype == object
    assert all(isinstance(v, (bool, np.bool_)) for v in n.generators[FLAG])

    with pytest.raises(Exception):
        n.export_to_netcdf(str(tmp_path / "raw.nc"))

    path = tmp_path / "ok.nc"
    PyPSAService.export_network_to_netcdf(n, path)
    back = pypsa.Network()
    PyPSAService.import_network_from_netcdf(back, path)
    assert back.generators[FLAG].dtype == bool
    assert bool(back.generators.at["nuc", FLAG]) is True

"""
Phase 12e Part B — `/copt` costs less, and every number it reports is the
same one (plan v4 §2, §4).

Two independent changes, and they need two independent claims:

* **Binning the counterfactual evaluations is EXACT.** `expected_shortfall_vec`
  is a pure grid lookup and the cells depend only on the residual and the
  mixed units, so binning them once and evaluating each counterfactual as a
  dot product over the grid is the same arithmetic. Pinned at rel 1e-8
  against the direct path (measured worst 6.2e-13 across the fixtures below).
* **Deleting the `deconvolve` call CHANGES numbers**, and what is pinned is
  the DIRECTION, not a band. `deconvolve` accepts any table whose mass lands
  in `0.999 ≤ total ≤ 1.001`; that guard bounds the table's MASS, and no
  bound on the ΔEUE error follows from it — the measurements refute any such
  reading (1.68e-5 of mass error moved ΔEUE by 1.04e-4 on one fleet, while
  7.2e-4 of mass error moved it by only 3.8e-5 on another). So F2e asserts
  that the shipped rows equal the REBUILD's to 1e-12 on a fixture where the
  two routes provably disagree, and asserts that disagreement (`> 1e-6`) so
  the test cannot go vacuous. No claim of bit-identity anywhere.

The reference for the first claim is therefore the shipped mixture path with
the `deconvolve` call ALREADY REMOVED (`_direct_reference` below) — comparing
against a reference that still calls it would fold the second change's error
into the first change's tolerance.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.adequacy import copt as C

H = 1500
IDX = pd.RangeIndex(H)
W = pd.Series(np.ones(H), index=IDX)


def _direct_reference(units, dist, residual_load, *, weights, mixed=(), netted=()):
    """ΔEUE per unit by the SHIPPED mixture path, with the deconvolve call
    already removed — `attribute_criticality` before Phase 12e's binning."""
    mixed = tuple(mixed)
    netted = tuple(netted)
    r = residual_load.to_numpy(dtype=np.float64)
    w = weights.reindex(residual_load.index).fillna(0.0).to_numpy(dtype=np.float64)

    def _eue(d, res, fixed_up=frozenset()) -> float:
        _l, e = C.mixture_hourly(d, res, mixed, fixed_up=fixed_up)
        return float((e * w).sum())

    base = _eue(dist, r)
    out: dict[str, float] = {}
    for u in units:
        without = C.build_copt([v for v in units if v.name != u.name],
                               delta_mw=dist.delta_mw)
        out[u.name] = max(base - _eue(C._shift_deterministic(without, u.capacity_mw), r), 0.0)
    for i, u in enumerate(mixed):
        out[u.name] = max(base - _eue(dist, r, fixed_up=frozenset({i})), 0.0)
    for u in netted:
        out[u.name] = max(
            base - _eue(dist, r - float(u.q) * C._availability_mw(u, r.shape[0])), 0.0)
    return out


def _fleet(n, *, k=0, cap=100.0, q=0.05, seed=7, spread=True):
    rng = np.random.default_rng(seed)
    return [C.CoptUnit(f"g{i}", cap + (i % 5 if spread else 0), q, mttr_hours=24.0,
                       profile=(np.clip(rng.random(H), 0.05, 1.0) if i < k else None))
            for i in range(n)]


def _split_and_table(units, *, k_exact=C.K_EXACT, delta=1.0, residual=None):
    split = C.split_fleet(units, k_exact=k_exact)
    res = residual
    if split.netted:
        res = res - pd.Series(C.netted_expectation(split.netted, H), index=res.index)
    return split, res, C.build_copt(list(split.table), delta_mw=delta)


# ── F2: binning is exact, ON THE BINNED PATH ────────────────────────────

F2_CASES = {
    # off the Δ grid: `ceil(x − 1e-12) == floor(x)` on integers, so an
    # on-grid residual makes the `floor` bite vacuous (plan v4 §4).
    "off_grid": dict(n=30, k=4, level=1650.4999687, delta=1.0, k_exact=C.K_EXACT),
    "delta_2_5": dict(n=20, k=3, level=1100.3, delta=2.5, k_exact=C.K_EXACT),
    "mixed_and_netted": dict(n=30, k=6, level=1650.4999687, delta=1.0, k_exact=2),
    "empty_table": dict(n=3, k=3, level=300.7, delta=1.0, k_exact=C.K_EXACT),
    "fold": dict(n=4, k=2, level=5000.0, delta=1.0, k_exact=C.K_EXACT),
    # Every other case weights the hours equally, which makes `w` a constant
    # factor that cancels out of the comparison — so nothing here pinned the
    # per-hour weight entering the cells at all (shipped-code review, finding
    # 9). This case gives the hours a non-uniform, non-degenerate weighting,
    # including exact zeros: a zero-weight hour must contribute nothing to
    # either side, and the two paths must still agree row for row.
    "weighted": dict(n=30, k=4, level=1650.4999687, delta=1.0,
                     k_exact=C.K_EXACT, weights="ramp_with_zeros"),
}


def _weights(kind):
    """The weight vector a case asks for. `None` is the module's uniform `W`."""
    if kind is None:
        return W
    if kind == "ramp_with_zeros":
        v = np.linspace(0.25, 3.5, H)
        v[::37] = 0.0                    # zero-weight hours, spread through
        return pd.Series(v, index=IDX)
    raise AssertionError(f"unknown weights kind {kind!r}")


@pytest.mark.parametrize("case", sorted(F2_CASES))
def test_F2_the_binned_path_equals_the_direct_one(case):
    """★ F2. Every criticality row from the binned evaluator equals the
    direct mixture path's, BY NAME, to rel 1e-8 — on an off-grid residual, a
    Δ ≠ 1 table, a fleet with netted units, an empty table (all units mixed)
    and a residual far beyond the table (the fold).

    The binned implementation is called DIRECTLY, not through the switch: the
    switch sends small fixtures to the direct path, and comparing the direct
    path against itself is what made an earlier version of this test — and
    both its bites — vacuous (plan v4 §0 finding 4).

    Bites (verified): bin with `floor` instead of the grid rule; drop the
    beyond-table fold in `_eue_binned`.
    """
    cfg = F2_CASES[case]
    wts = _weights(cfg.get("weights"))
    units = _fleet(cfg["n"], k=cfg["k"])
    res = pd.Series(np.full(H, cfg["level"]) + 40 * np.sin(np.linspace(0, 9, H)),
                    index=IDX)
    split, res, dist = _split_and_table(
        units, k_exact=cfg["k_exact"], delta=cfg["delta"], residual=res)
    ref = _direct_reference(list(split.table), dist, res, weights=wts,
                            mixed=split.mixed, netted=split.netted)

    r = res.to_numpy(dtype=np.float64)
    w = wts.reindex(res.index).fillna(0.0).to_numpy(dtype=np.float64)
    cells = C._eue_cells(r, split.mixed, w, dist.delta_mw)
    base = C._eue_binned(dist, cells)
    got: dict[str, float] = {}
    for u in split.table:
        without = C.build_copt([v for v in split.table if v.name != u.name],
                               delta_mw=dist.delta_mw)
        got[u.name] = max(base - C._eue_binned(
            C._shift_deterministic(without, u.capacity_mw), cells), 0.0)
    for i, u in enumerate(split.mixed):
        got[u.name] = max(base - C._eue_binned(dist, C._eue_cells(
            r, split.mixed, w, dist.delta_mw, fixed_up=frozenset({i}))), 0.0)

    assert set(got) <= set(ref)
    for name, want in ref.items():
        if name not in got:                      # netted rows keep the direct path
            continue
        if want == 0.0:
            assert got[name] == pytest.approx(0.0, abs=1e-9), name
        else:
            assert abs(got[name] - want) / abs(want) < 1e-8, (
                name, got[name], want)


def test_F2b_the_fold_is_what_makes_a_beyond_table_cell_count():
    """★ F2b. With the residual far beyond the table, every cell clips to the
    table's last index — and that index is where all the shortfall lives. The
    hand value is `Σ_h w_h · q · cap` per unit (the unit is the only thing
    that can be down).

    The binned evaluator is called DIRECTLY. Routing this through
    `attribute_criticality` is what made an earlier version vacuous: the
    fixture has no mixed units, so `len(mixed) >= BIN_MIN_MIXED` is false, the
    switch sends it to the direct path, and `_eue_binned`'s fold — the thing
    named in the bite — never runs (shipped-code review, finding F2b).

    Bite (verified): drop the beyond-table fold in `_eue_binned` — every ΔEUE
    reads 0.0.
    """
    units = [C.CoptUnit(f"g{i}", 60.0, 0.1, mttr_hours=24.0) for i in range(4)]
    res = pd.Series(np.full(H, 5000.0), index=IDX)
    dist = C.build_copt(units, delta_mw=1.0)
    r = res.to_numpy(dtype=np.float64)
    w = W.to_numpy(dtype=np.float64)

    cells = C._eue_cells(r, (), w, dist.delta_mw)
    base = C._eue_binned(dist, cells)
    for u in units:
        without = C.build_copt([v for v in units if v.name != u.name],
                               delta_mw=dist.delta_mw)
        delta = max(base - C._eue_binned(
            C._shift_deterministic(without, u.capacity_mw), cells), 0.0)
        assert delta == pytest.approx(H * u.q * u.capacity_mw, rel=1e-9), u.name

    # …and the shipped route (here: the direct path) agrees with it, so the
    # hand value pins both sides of the switch on this fixture.
    rows = {x["name"]: x["delta_eue_mwh"]
            for x in C.attribute_criticality(units, dist, res, weights=W, voll=0.0)}
    for u in units:
        assert rows[u.name] == pytest.approx(H * u.q * u.capacity_mw, rel=1e-9)


# ── F2c: the operation count, machine-independently ─────────────────────

def test_F2h_a_negative_availability_still_bins_exactly():
    """★ F2h (shipped-code review, finding 7). `_eue_cells` sized its grid from
    `r.max()`, which is only the largest `x` the mixture can produce when every
    availability is non-negative. Nothing enforces that: `_availability_mw`
    multiplies a profile by a capacity series and clamps neither, so one signed
    hour makes some state's `x` exceed the residual, those cells clip into the
    top bin, and when the TABLE OUTRUNS THE RESIDUAL `_eue_binned` reads them
    at the wrong grid index instead of folding them.

    The fixture is that shape deliberately: 10 × 100 MW against a 300 MW
    residual, so the table (n = 1010) is far longer than the residual grid and
    the mis-binning is visible rather than absorbed by the fold. Measured 70%
    relative error before the fix. Bite (verified): size `n_max` from
    `r.max()` alone.
    """
    n_h = 8
    r = np.full(n_h, 300.0)
    w = np.ones(n_h)
    prof = np.full(n_h, 0.5)
    prof[3] = -2.0                       # one hour of negative availability
    mixed = (C.CoptUnit("m0", 100.0, 0.2, mttr_hours=24.0, profile=prof),
             C.CoptUnit("m1", 100.0, 0.2, mttr_hours=24.0))
    table = [C.CoptUnit(f"t{i}", 100.0, 0.1, mttr_hours=24.0) for i in range(10)]
    dist = C.build_copt(table, delta_mw=1.0)
    assert len(dist.probs) - 1 > n_h * 100, "table must outrun the residual grid"

    _l, e = C.mixture_hourly(dist, r, mixed)
    direct = float((e * w).sum())
    binned = C._eue_binned(dist, C._eue_cells(r, mixed, w, dist.delta_mw))
    assert direct > 0.0
    assert abs(binned - direct) / direct < 1e-9, (binned, direct)


def test_F2i_a_netted_row_is_right_even_though_only_its_base_is_binned():
    """★ F2i (shipped-code review, finding 8). A netted unit's counterfactual
    shifts the RESIDUAL, so it changes every cell and keeps the direct path
    even when the call is binned. That makes its ΔEUE asymmetric —
    `base_binned − perfect_direct`, one side from each evaluator — and nothing
    pinned it: F2 compares only the rows it recomputes and skips the netted
    ones outright.

    Here the whole payload, netted rows included, is checked against a
    fully-direct reference on a fixture that provably takes the binned path.
    Bite (verified): make the binning approximate — e.g. bin with `floor`
    instead of the grid rule — and the netted rows move, because the
    approximation lands on their base with nothing to cancel it.
    """
    units = _fleet(30, k=6)
    res = pd.Series(np.full(H, 1650.4999687) + 40 * np.sin(np.linspace(0, 9, H)),
                    index=IDX)
    split, res, dist = _split_and_table(units, k_exact=2, residual=res)
    assert split.netted, "fixture must have netted units"
    assert len(split.mixed) >= C.BIN_MIN_MIXED, "fixture must take the binned path"

    ref = _direct_reference(list(split.table), dist, res, weights=W,
                            mixed=split.mixed, netted=split.netted)
    got = {r["name"]: r["delta_eue_mwh"]
           for r in C.attribute_criticality(
               list(split.table), dist, res, weights=W, voll=0.0,
               mixed=split.mixed, netted=split.netted)}

    netted_names = [u.name for u in split.netted]
    assert set(netted_names) <= set(got)
    assert all(ref[nm] > 0.0 for nm in netted_names), \
        "fixture must give every netted row a non-zero ΔEUE to compare"
    # The netted rows FIRST, so the bite names one of them rather than
    # tripping on a table row and leaving this claim untested.
    for name in netted_names:
        assert abs(got[name] - ref[name]) / ref[name] < 1e-8, \
            (name, got[name], ref[name])
    for name, want in ref.items():
        if want == 0.0:
            assert got[name] == pytest.approx(0.0, abs=1e-9), name
        else:
            assert abs(got[name] - want) / abs(want) < 1e-8, (name, got[name], want)


def test_F2c_the_attribution_makes_no_deconvolve_calls_and_one_mixture_pass_per_netted_unit(
        monkeypatch):
    """★ F2c. The claim, counted rather than timed: on a `k ≥ 2` fixture (so
    the switch takes the BINNED path — named, because the count depends on it)
    the attribution calls `deconvolve` zero times, and `mixture_hourly` once
    per netted unit and no more. Wall time is printed, never asserted — a
    timing gate cannot fail on a fast machine and cannot pass on a slow one.
    Bite (verified): restore the per-unit mixture loop."""
    calls = {"deconvolve": 0, "mixture": 0}
    real_mix = C.mixture_hourly
    monkeypatch.setattr(C, "deconvolve",
                        lambda *a, **k: calls.__setitem__("deconvolve", calls["deconvolve"] + 1))
    def counting_mix(*a, **k):
        calls["mixture"] += 1
        return real_mix(*a, **k)
    monkeypatch.setattr(C, "mixture_hourly", counting_mix)

    units = _fleet(40, k=6)
    res = pd.Series(np.full(H, 2200.5) + 30 * np.sin(np.linspace(0, 9, H)), index=IDX)
    split, res, dist = _split_and_table(units, k_exact=4, residual=res)
    assert len(split.mixed) >= C.BIN_MIN_MIXED, "fixture must take the binned path"
    assert split.netted, "fixture must have netted units for the count to be non-zero"
    C.attribute_criticality(list(split.table), dist, res, weights=W, voll=0.0,
                            mixed=split.mixed, netted=split.netted)
    assert calls["deconvolve"] == 0
    assert calls["mixture"] == len(split.netted)


def test_F2c2_a_fleet_below_the_switch_uses_the_direct_path_and_still_makes_no_deconvolve_call(
        monkeypatch):
    """The other side of F2c: below `BIN_MIN_MIXED` the direct path runs (one
    `mixture_hourly` per counterfactual plus the base), and `deconvolve` is
    still never called — the deletion is unconditional, the binning is not."""
    calls = {"deconvolve": 0, "mixture": 0}
    real_mix = C.mixture_hourly
    monkeypatch.setattr(C, "deconvolve",
                        lambda *a, **k: calls.__setitem__("deconvolve", calls["deconvolve"] + 1))
    def counting_mix(*a, **k):
        calls["mixture"] += 1
        return real_mix(*a, **k)
    monkeypatch.setattr(C, "mixture_hourly", counting_mix)
    units = _fleet(12, k=1)
    res = pd.Series(np.full(H, 660.5), index=IDX)
    split, res, dist = _split_and_table(units, residual=res)
    assert len(split.mixed) < C.BIN_MIN_MIXED
    C.attribute_criticality(list(split.table), dist, res, weights=W, voll=0.0,
                            mixed=split.mixed, netted=split.netted)
    assert calls["deconvolve"] == 0
    assert calls["mixture"] == 1 + len(split.table) + len(split.mixed)


def test_F2d_the_switch_picks_the_path_it_claims_to(monkeypatch):
    """★ F2d. `BIN_MIN_MIXED` is the whole switch, and the test OBSERVES which
    path ran rather than restating the fixture: the binned path calls
    `mixture_hourly` once per netted unit only, the direct path once per
    counterfactual plus the base. A fleet below the threshold takes the
    direct path, one above it the binned path, and both give the same
    numbers.

    Constant-free by design — the crossover between the two paths is not a
    fixed ratio (the direct path carries a large per-call overhead that a
    `2^k·H` term does not model), so a machine-measured constant would drift.
    Bite (verified): invert the comparison — the call counts swap.
    """
    assert C.BIN_MIN_MIXED == 2
    for k, want_binned in ((1, False), (3, True)):
        units = _fleet(15, k=k)
        res = pd.Series(np.full(H, 830.25), index=IDX)
        split, res, dist = _split_and_table(units, residual=res)
        assert not split.netted, "fixture must have no netted units"

        calls = {"n": 0}
        real_mix = C.mixture_hourly

        def counting(*a, _real=real_mix, **kw):
            calls["n"] += 1
            return _real(*a, **kw)

        monkeypatch.setattr(C, "mixture_hourly", counting)
        got = {r["name"]: r["delta_eue_mwh"] for r in C.attribute_criticality(
            list(split.table), dist, res, weights=W, voll=0.0, mixed=split.mixed)}
        monkeypatch.setattr(C, "mixture_hourly", real_mix)

        direct_calls = 1 + len(split.table) + len(split.mixed)
        if want_binned:
            assert calls["n"] == 0, (k, calls["n"])          # netted-only, none here
        else:
            assert calls["n"] == direct_calls, (k, calls["n"])

        ref = _direct_reference(list(split.table), dist, res, weights=W,
                                mixed=split.mixed)
        for name, want in ref.items():
            if want == 0.0:
                assert got[name] == pytest.approx(0.0, abs=1e-9), name
            else:
                assert abs(got[name] - want) / abs(want) < 1e-8, name


# ── F2e: what deleting the deconvolve call costs ────────────────────────

def test_F2e_the_attribution_now_follows_the_rebuild_and_not_the_deconvolution():
    """★ F2e (rewritten after the shipped-code review). `attribute_criticality`
    no longer calls `deconvolve`, and on a fleet where the two routes DISAGREE
    the shipped numbers must be the rebuild's, exactly.

    This pins a DIRECTION, not a bound. The earlier version asserted that the
    shipped rows sat within `1e-3` of the deconvolved ones, justified by
    `deconvolve`'s mass guard (`0.999 ≤ total ≤ 1.001`) — but that guard bounds
    the table's MASS, and no bound on ΔEUE follows from it; worse, on the
    fixture it used the two routes agreed to 8e-15, so the assertion could not
    fail and neither could its bite.

    The fixture here is chosen to discriminate: 45 × 100 MW at q = 0.05 with
    the residual at full nameplate deconvolves to a table carrying 7.2e-4 of
    surplus mass, and that moves ΔEUE by 3.8e-5 relative — 10 orders of
    magnitude above the rebuild's own float noise. Bite (verified): restore
    the deconvolve-first path in `attribute_criticality`; the shipped rows
    then follow the deconvolution and the `1e-12` assertion fails.
    """
    units = _fleet(45, k=0, q=0.05, spread=False)
    res = pd.Series(np.full(H, 4500.0), index=IDX)
    dist = C.build_copt(units, delta_mw=1.0)
    r = res.to_numpy(dtype=np.float64)
    w = W.to_numpy(dtype=np.float64)

    def eue(d):
        _l, e = C.mixture_hourly(d, r, ())
        return float((e * w).sum())

    base = eue(dist)
    shipped = {x["name"]: x["delta_eue_mwh"]
               for x in C.attribute_criticality(units, dist, res, weights=W, voll=0.0)}

    n_deconv = 0
    worst_shipped_vs_rebuild = 0.0
    worst_deconv_vs_rebuild = 0.0
    for u in units:
        without = C.build_copt([v for v in units if v.name != u.name],
                               delta_mw=dist.delta_mw)
        rebuild = max(base - eue(C._shift_deterministic(without, u.capacity_mw)), 0.0)
        assert rebuild > 0.0, u.name
        worst_shipped_vs_rebuild = max(
            worst_shipped_vs_rebuild, abs(shipped[u.name] - rebuild) / rebuild)
        try:
            g = C.deconvolve(dist, capacity_mw=u.capacity_mw, q=u.q)
        except ValueError:
            continue
        n_deconv += 1
        # the mass the guard admitted…
        assert 0.999 <= float(g.probs.sum()) <= 1.001
        dec = max(base - eue(C._shift_deterministic(g, u.capacity_mw)), 0.0)
        worst_deconv_vs_rebuild = max(
            worst_deconv_vs_rebuild, abs(dec - rebuild) / rebuild)

    assert n_deconv > 0, "fixture must be one where the deconvolution succeeds"
    # …and the two routes really do disagree here, so the assertion below is
    # not vacuous.
    assert worst_deconv_vs_rebuild > 1e-6, worst_deconv_vs_rebuild
    # The shipped attribution is the rebuild's, to float noise.
    assert worst_shipped_vs_rebuild < 1e-12, worst_shipped_vs_rebuild


def test_F2f_the_sort_key_breaks_exact_ties_by_name():
    """★ F2f. The row order `attribute_criticality` EMITS is deterministic for
    exactly-tied ΔEUE: the name breaks the tie, so a payload built twice
    orders identically whatever the fleet order was.

    The tie is exact by construction, not by luck: with `q = 0` every unit is
    perfectly available, so removing one and convolving back a perfect unit of
    the same size reproduces the table and every ΔEUE is exactly 0.0. The
    fleet is built in an order that is NOT alphabetical, so the bite is
    visible. This is the only thing the key fixes — two distinct units within
    the binning error can still swap, and after the deconvolve deletion two
    identical units may differ at ~1e-12 and are then not tied at all (module
    docstring). Bite (verified): sort on `-delta_eue_mwh` alone — the
    construction order survives.
    """
    names = ["zulu", "alpha", "mike", "bravo", "yankee"]
    units = [C.CoptUnit(n, 100.0, 0.0, mttr_hours=24.0) for n in names]
    res = pd.Series(np.full(H, 260.0), index=IDX)
    dist = C.build_copt(units, delta_mw=1.0)
    rows = C.attribute_criticality(units, dist, res, weights=W, voll=0.0)
    assert all(r["delta_eue_mwh"] == 0.0 for r in rows), \
        [r["delta_eue_mwh"] for r in rows]
    assert [r["name"] for r in rows] == sorted(names)


def test_F2g_the_block_merge_sort_breaks_exact_ties_by_name_too():
    """★ F2g. The 12d per-block path merges criticality rows by name and sorts
    them again, so the tie-break has to be on THAT sort too — the merged order
    is what `/copt` serves on every multi-period network. `q = 0` makes every
    ΔEUE exactly 0.0, so the ties are exact.

    The blocks hold DIFFERENT units, and that is the whole fixture. An earlier
    version gave both blocks the same fleet, which made the test vacuous: each
    block's rows come out of `attribute_criticality` already name-sorted, so
    the merge dict was filled alphabetically and `sorted` — being stable —
    returned alphabetical order whether or not the merge key had a tie-break
    (shipped-code review, finding F2g). Here `mike`/`zulu` carry capacity only
    in 2030 and `alpha`/`bravo` only in 2035, so the dict fills as
    [mike, zulu, alpha, bravo] and only the merge sort's own name key can
    recover alphabetical order.

    Bite (verified): sort the merged rows on `-delta_eue_mwh` alone.
    """
    n_h = 6
    idx = pd.MultiIndex.from_product(
        [[2030, 2035], pd.RangeIndex(n_h)], names=["period", "t"])
    first = np.concatenate([np.full(n_h, 100.0), np.zeros(n_h)])
    second = np.concatenate([np.zeros(n_h), np.full(n_h, 100.0)])
    units = [C.CoptUnit(nm, 100.0, 0.0, mttr_hours=24.0, capacity_series=ser)
             for nm, ser in (("mike", first), ("zulu", first),
                             ("alpha", second), ("bravo", second))]
    res = pd.Series(np.full(2 * n_h, 260.0), index=idx)
    w = pd.Series(np.ones(2 * n_h), index=idx)
    an = C.screening_analysis(units, res, weights=w, voll=0.0)

    assert set(an["dist"]) == {2030, 2035}, "fixture must take the per-block path"
    names = [r["name"] for r in an["rows"]]
    assert sorted(names) == ["alpha", "bravo", "mike", "zulu"], names
    assert all(r["delta_eue_mwh"] == 0.0 for r in an["rows"]), \
        [(r["name"], r["delta_eue_mwh"]) for r in an["rows"]]
    # The merge dict cannot have been filled alphabetically: 2030 contributes
    # only mike/zulu and 2035 only alpha/bravo. Without the merge sort's name
    # key the stable sort would hand back [mike, zulu, alpha, bravo].
    assert names == ["alpha", "bravo", "mike", "zulu"], names

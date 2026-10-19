"""Calibration / shaping ops over a generated per-unit profile (RP-06).

Spec: .scratch/renewable-profiles/spec.md R5 and issue 06; gatekeeper
conditions 2026-10-19:

1. an unreachable FLH/CF target is a refusal naming the reachable maximum,
   never a clipped series that silently misses it;
2. the op chain, each op's parameters, and the parent's and result's
   FLH/CF land in the meta with ``calibrated: true``;
3. ``peak_shift`` wraps at the year boundary and runs on the weather year,
   before alignment — length and FLH unchanged;
4. apply stays the only writer (the route test pins that side).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.profile_gen.engine import generate_profile
from services.profile_gen.shape import (
    InvalidShapeOpError,
    UnreachableTargetError,
    apply_chain,
    profile_stats,
)

NOW = "2026-10-18T00:00:00+00:00"


def _series(technology: str = "wind", year: int = 2023) -> pd.Series:
    res = generate_profile(points=[("site", 8.0, 50.0)], technology=technology,
                           source="synthetic", year=year, now=NOW)
    return res.series["site"]


def _spearman(a: pd.Series, b: pd.Series) -> float:
    return float(pd.Series(a.to_numpy()).rank().corr(
        pd.Series(b.to_numpy()).rank()))


# ── invariants every op keeps ───────────────────────────────────────────────

OPS = [
    {"op": "target_flh", "hours": 2000},
    {"op": "target_cf", "cf": 0.2},
    # above the solar night share (~50 % zeros), which named quantiles refuse
    {"op": "quantile_map", "quantiles": {"0.6": 0.05, "0.75": 0.25, "0.9": 0.7}},
    # 0.7: above the solar night share, so the low tail is not all zeros
    {"op": "tail_damp", "side": "low", "beyond_q": 0.7, "factor": 0.5},
    {"op": "tail_damp", "side": "high", "beyond_q": 0.9, "factor": 0.5,
     "keep_flh": False},
    {"op": "peak_shift", "hours": 3},
]


@pytest.mark.parametrize("op", OPS, ids=lambda o: o["op"] + "-" + str(len(o)))
@pytest.mark.parametrize("technology", ["wind", "solar"])
def test_every_op_keeps_range_length_and_index(op, technology):
    s = _series(technology)
    out, _ = apply_chain(s, [op])
    assert len(out) == len(s)
    assert out.index.equals(s.index)
    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0
    assert np.isfinite(out.to_numpy()).all()


@pytest.mark.parametrize("op", OPS, ids=lambda o: o["op"] + "-" + str(len(o)))
def test_every_op_is_deterministic(op):
    s = _series()
    a, _ = apply_chain(s, [op])
    b, _ = apply_chain(s, [op])
    assert a.to_numpy().tobytes() == b.to_numpy().tobytes()


# ── FLH / CF targets ────────────────────────────────────────────────────────


def test_target_flh_hits_the_target_within_half_a_percent():
    s = _series()
    target = 1.3 * profile_stats(s)["flh"]
    out, meta = apply_chain(s, [{"op": "target_flh", "hours": target}])
    flh = profile_stats(out)["flh"]
    assert abs(flh - target) / target < 0.005
    # Clip-at-1 is in play: plain scaling would overshoot the cap.
    assert float(out.max()) == 1.0


def test_target_flh_keeps_the_rank_order():
    s = _series()
    out, _ = apply_chain(s, [{"op": "target_flh", "hours": 1500}])
    assert _spearman(s, out) > 0.999


def test_an_unreachable_flh_target_refuses_with_the_reachable_maximum():
    s = _series("solar")  # zero at night: FLH can never exceed the daylight hours
    daylight = int((s > 0).sum())
    with pytest.raises(UnreachableTargetError) as err:
        apply_chain(s, [{"op": "target_flh", "hours": daylight + 100}])
    assert str(daylight) in str(err.value)
    assert err.value.reachable_max == pytest.approx(daylight, abs=1)


def test_target_cf_is_target_flh_over_the_year():
    s = _series()
    a, _ = apply_chain(s, [{"op": "target_cf", "cf": 0.25}])
    b, _ = apply_chain(s, [{"op": "target_flh", "hours": 0.25 * 8760}])
    np.testing.assert_allclose(a.to_numpy(), b.to_numpy(), atol=1e-12)


# ── quantile mapping ────────────────────────────────────────────────────────


def test_quantile_map_hits_named_quantiles_and_keeps_timing():
    s = _series()
    target = {"0.1": 0.05, "0.5": 0.3, "0.9": 0.75}
    out, _ = apply_chain(s, [{"op": "quantile_map", "quantiles": target}])
    for q, v in target.items():
        assert float(out.quantile(float(q))) == pytest.approx(v, abs=2e-3)
    assert _spearman(s, out) > 0.999


def test_quantile_map_onto_an_observed_series_matches_its_duration_curve():
    s = _series()
    rng = np.random.default_rng(7)
    observed = np.clip(rng.beta(1.2, 3.0, size=8760), 0, 1)
    out, _ = apply_chain(s, [{"op": "quantile_map",
                              "observed": observed.tolist()}])
    for q in (0.1, 0.25, 0.5, 0.75, 0.9):
        assert float(out.quantile(q)) == pytest.approx(
            float(np.quantile(observed, q)), abs=2e-3)
    assert _spearman(s, out) > 0.999


def test_quantile_map_keeps_solar_nights_at_zero():
    s = _series("solar")
    out, _ = apply_chain(s, [{"op": "quantile_map",
                              "quantiles": {"0.9": 0.6}}])
    assert (out[s == 0] == 0).all()


def test_quantile_map_refuses_decreasing_targets():
    with pytest.raises(InvalidShapeOpError, match="non-decreasing"):
        apply_chain(_series(), [{"op": "quantile_map",
                                 "quantiles": {"0.1": 0.5, "0.9": 0.2}}])


# ── tail damping ────────────────────────────────────────────────────────────


def test_low_tail_damp_cuts_the_low_hours_and_keeps_flh():
    s = _series()
    before = profile_stats(s)
    out, _ = apply_chain(s, [{"op": "tail_damp", "side": "low",
                              "beyond_q": 0.2, "factor": 0.5}])
    after = profile_stats(out)
    assert after["p10"] < before["p10"]
    assert abs(after["flh"] - before["flh"]) / before["flh"] < 0.005
    assert _spearman(s, out) > 0.999


def test_high_tail_damp_cuts_the_top_and_can_keep_flh():
    s = _series()
    before = profile_stats(s)
    out, _ = apply_chain(s, [{"op": "tail_damp", "side": "high",
                              "beyond_q": 0.9, "factor": 0.5}])
    after = profile_stats(out)
    assert float(out.quantile(0.99)) < float(s.quantile(0.99))
    assert abs(after["flh"] - before["flh"]) / before["flh"] < 0.005


def test_tail_damp_refuses_a_factor_outside_zero_one():
    with pytest.raises(InvalidShapeOpError, match="factor"):
        apply_chain(_series(), [{"op": "tail_damp", "side": "low",
                                 "beyond_q": 0.2, "factor": 1.5}])


# ── peak shift ──────────────────────────────────────────────────────────────


def test_peak_shift_moves_the_timing_and_wraps_at_the_year_boundary():
    s = _series("solar")
    out, meta = apply_chain(s, [{"op": "peak_shift", "hours": 2}])
    assert float(out.iloc[2]) == float(s.iloc[0])
    assert float(out.iloc[0]) == float(s.iloc[-2])  # wrapped
    assert profile_stats(out)["flh"] == pytest.approx(profile_stats(s)["flh"])
    assert meta["ops"][0]["boundary"] == "wrap"


def test_shaping_refuses_a_coarse_profile():
    res = generate_profile(points=[("site", 8.0, 50.0)], technology="wind",
                           source="synthetic", year=2023, resolution="daily",
                           now=NOW)
    with pytest.raises(InvalidShapeOpError, match="hourly"):
        apply_chain(res.series["site"], [{"op": "peak_shift", "hours": 1}])


# ── chain + provenance ──────────────────────────────────────────────────────


def test_the_chain_meta_carries_ops_parent_and_result_stats():
    s = _series()
    ops = [{"op": "tail_damp", "side": "low", "beyond_q": 0.2, "factor": 0.5},
           {"op": "target_flh", "hours": 2200}]
    out, meta = apply_chain(s, ops)
    assert meta["calibrated"] is True
    assert [o["op"] for o in meta["ops"]] == ["tail_damp", "target_flh"]
    assert meta["ops"][1]["hours"] == 2200
    assert meta["parent_stats"]["flh"] == pytest.approx(profile_stats(s)["flh"])
    assert meta["result_stats"]["flh"] == pytest.approx(2200, rel=0.005)
    assert set(meta["result_stats"]) >= {"flh", "cf", "p10", "p50", "p90",
                                          "peak", "peak_hour"}


def test_an_unknown_op_or_an_empty_chain_is_refused():
    with pytest.raises(InvalidShapeOpError, match="unknown"):
        apply_chain(_series(), [{"op": "make_it_windier"}])
    with pytest.raises(InvalidShapeOpError, match="at least one"):
        apply_chain(_series(), [])


# ── refusals instead of silent coercions (review 2026-10-19) ───────────────


def test_a_target_exactly_at_the_reachable_maximum_is_met():
    s = _series("solar")
    daylight = int((s > 0).sum())
    out, _ = apply_chain(s, [{"op": "target_flh", "hours": daylight}])
    assert profile_stats(out)["flh"] == pytest.approx(daylight)


def test_keep_flh_must_be_a_boolean():
    with pytest.raises(InvalidShapeOpError, match="keep_flh"):
        apply_chain(_series(), [{"op": "tail_damp", "side": "high",
                                 "beyond_q": 0.9, "factor": 0.5,
                                 "keep_flh": "false"}])


def test_observed_values_outside_per_unit_are_refused():
    with pytest.raises(InvalidShapeOpError, match="per-unit"):
        apply_chain(_series(), [{"op": "quantile_map",
                                 "observed": [0.1, 0.5, 12.0]}])
    with pytest.raises(InvalidShapeOpError, match="per-unit"):
        apply_chain(_series(), [{"op": "quantile_map",
                                 "observed": [0.1, float("nan"), 0.3]}])


def test_damping_an_all_zero_low_tail_is_refused():
    with pytest.raises(InvalidShapeOpError, match="all zero"):
        apply_chain(_series("solar"), [{"op": "tail_damp", "side": "low",
                                        "beyond_q": 0.2, "factor": 0.5}])


def test_a_named_quantile_inside_the_solar_nights_is_refused():
    with pytest.raises(InvalidShapeOpError, match="stay zero"):
        apply_chain(_series("solar"), [{"op": "quantile_map",
                                        "quantiles": {"0.2": 0.1}}])


def test_a_profile_with_holes_is_refused():
    s = _series()
    s.iloc[10] = float("nan")
    with pytest.raises(InvalidShapeOpError, match="non-finite"):
        apply_chain(s, [{"op": "target_cf", "cf": 0.2}])


# ── seeded property loop: the invariants over years, sites, technologies ───


@pytest.mark.parametrize("year", [2019, 2020, 2023])  # 2020: leap
@pytest.mark.parametrize("site", [(8.0, 50.0), (-3.7, 40.4), (24.9, 60.2)])
@pytest.mark.parametrize("technology", ["wind", "solar"])
def test_targets_and_rank_order_hold_across_years_and_sites(year, site,
                                                            technology):
    res = generate_profile(points=[("p", *site)], technology=technology,
                           source="synthetic", year=year, now=NOW)
    s = res.series["p"]
    base = profile_stats(s)["flh"]
    for factor in (0.6, 1.2):
        target = base * factor
        out, _ = apply_chain(s, [{"op": "target_flh", "hours": target}])
        assert abs(profile_stats(out)["flh"] - target) / target < 0.005
        assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0
        assert _spearman(s, out) > 0.999
    out, _ = apply_chain(s, [{"op": "tail_damp", "side": "high",
                              "beyond_q": 0.9, "factor": 0.5}])
    assert abs(profile_stats(out)["flh"] - base) / base < 0.005
    assert len(out) == len(s)

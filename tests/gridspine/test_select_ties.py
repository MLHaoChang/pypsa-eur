"""Task 9: break the floor tie by spreading, not by calendar position.

The 8760 h run found 96 hours tied at the inertia floor (7780 MW·s) and the
chronological tie-break handed back the five EARLIEST — one January week.
"Deterministic" stays non-negotiable ("Determinism matters more than the
choice itself"); "chronologically clustered" was never the requirement.

Rule: rank as before. If the hours tied at the k-th boundary value outnumber
the slots left for them, fill those slots from the tied set by a farthest-
point pass over hour-of-year, seeded at the EARLIEST tied hour: each pick is
the tied hour farthest from every hour already picked (ties in distance go to
the earlier hour). Closed form, no RNG. A single remaining slot therefore
still goes to the earliest tied hour, so every increment-2 expectation holds.
Strictly-better hours are never displaced; a tie that fits its slots is
untouched.
"""
import numpy as np
import pandas as pd
import pytest

from gridspine.ranking.select import CRITERIA, RANKED_COLUMNS, select_snapshots, validate_selection

FLOOR_HOURS = list(range(10, 30))   # 20 hours tied at the inertia floor


def _metrics(n=40):
    """Five ranked columns; only inertia_excl_equiv_mws carries a tie."""
    hours = np.arange(n)
    df = pd.DataFrame(index=pd.Index(hours, dtype="int64", name="hour"))
    inertia = 200.0 + 10.0 * hours
    inertia[FLOOR_HOURS] = 100.0
    df["inertia_excl_equiv_mws"] = inertia
    df["inertia_mws"] = inertia + 50000.0
    df["ibr_share"] = np.linspace(0.05, 0.60, n)          # max at the last hour, no ties
    df["load_mw"] = 3000.0 + 37.0 * hours                  # max at the last hour, no ties
    df["import_mw"] = np.where(hours % 2 == 0, 0.0, hours)  # max at odd late hours, no ties
    df["n1_severity_ac"] = 0.01 * hours                    # no ties
    assert set(RANKED_COLUMNS) <= set(df.columns)
    return df


def _inertia_hours(sel):
    return [int(h) for h, r in zip(sel["hour"], sel["reasons"]) if "min_inertia_excl_equiv_mws" in r]


def test_a_wide_tie_is_spread_over_the_tied_hours_not_the_first_k():
    sel = validate_selection(select_snapshots(_metrics(), k=5), _metrics())
    picked = _inertia_hours(sel)
    assert len(picked) == 5
    assert set(picked) <= set(FLOOR_HOURS)
    assert picked != FLOOR_HOURS[:5], "chronological [:k] — the January-week failure"
    assert min(picked) == FLOOR_HOURS[0], "seeded at the earliest tied hour"
    assert max(picked) == FLOOR_HOURS[-1], "farthest point from the seed is the last tied hour"
    gaps = np.diff(sorted(picked))
    assert gaps.min() >= 3, picked


def test_spreading_is_deterministic():
    a = select_snapshots(_metrics(), k=5)
    b = select_snapshots(_metrics().sample(frac=1.0, random_state=7).sort_index(), k=5)
    pd.testing.assert_frame_equal(a, b)


def test_a_single_slot_still_goes_to_the_earliest_tied_hour():
    """k=3 on the floor: three slots, twenty tied — spread. But with strictly
    better hours filling all but one slot, the one slot is the seed, i.e. the
    earliest, which is the increment-2 rule."""
    m = _metrics()
    m.loc[[3, 4], "inertia_excl_equiv_mws"] = [50.0, 60.0]   # two strictly thinner hours
    sel = select_snapshots(m, k=3)
    picked = _inertia_hours(sel)
    assert sorted(picked) == [3, 4, FLOOR_HOURS[0]]


def test_a_tie_that_fits_its_slots_is_untouched():
    m = _metrics()
    m.loc[FLOOR_HOURS[2:], "inertia_excl_equiv_mws"] = 150.0   # only hours 10, 11 stay at the floor
    sel = select_snapshots(m, k=2)
    assert sorted(_inertia_hours(sel)) == [10, 11]


def test_strictly_better_hours_are_never_displaced_by_spreading():
    m = _metrics()
    m.loc[[5], "inertia_excl_equiv_mws"] = 50.0
    sel = select_snapshots(m, k=4)
    picked = _inertia_hours(sel)
    assert 5 in picked and len(picked) == 4
    assert set(picked) - {5} <= set(FLOOR_HOURS)


def test_spreading_applies_in_the_max_direction_too():
    m = _metrics()
    m["load_mw"] = 3000.0
    m.loc[list(range(20, 40)), "load_mw"] = 9000.0   # twenty hours tied at the peak
    sel = select_snapshots(m, k=4)
    picked = [int(h) for h, r in zip(sel["hour"], sel["reasons"]) if "max_load_mw" in r]
    assert len(picked) == 4 and min(picked) == 20 and max(picked) == 39
    assert np.diff(sorted(picked)).min() >= 3


def test_no_tie_selection_is_the_plain_top_k():
    m = _metrics()
    m["inertia_excl_equiv_mws"] = 200.0 + 10.0 * np.arange(len(m))   # remove the tie
    sel = select_snapshots(m, k=3)
    assert sorted(_inertia_hours(sel)) == [0, 1, 2]
    load = [int(h) for h, r in zip(sel["hour"], sel["reasons"]) if "max_load_mw" in r]
    assert sorted(load) == [37, 38, 39]


def test_reasons_vocabulary_and_order_are_unchanged():
    sel = select_snapshots(_metrics(), k=5)
    for reasons in sel["reasons"]:
        assert reasons == [c for c in CRITERIA if c in reasons]


def test_farthest_point_hand_check():
    """Tied hours 10..29, five slots: seed 10; farthest from {10} is 29; then
    the hour farthest from both — 19 and 20 tie at distance 9, the earlier
    wins: 19; then 24 or 25 (distance 5 from 19/29), the earlier: 24 — wait,
    14 and 15 are distance 4 from 10/19; 24 is distance 5 from 19 and 29, so
    24; then 14 (distance 4 from 10 and 19) beats 15 (4 from 19... 5 from 10).
    Pinned as computed so a change in the rule is a visible change."""
    sel = select_snapshots(_metrics(), k=5)
    assert sorted(_inertia_hours(sel)) == [10, 14, 19, 24, 29]

"""``critical_feeders.compose``: the pocket hours added to an MC result
(walkthrough 2026-11-17 issue 02; GK A F2–F4)."""

from __future__ import annotations

import numpy as np
import pytest

from services.adequacy.critical_feeders import Pocket, bounded_verdict, compose


def _res(lole, ci, eue, by_period=None):
    return {
        "lole_hours": lole,
        "lole_ci": list(ci),
        "eue_mwh": eue,
        "eue_ci": [eue - 1.0, eue + 1.0],
        "by_period": by_period or {},
    }


def test_a_pocket_alone_is_its_unavailability_times_its_loaded_hours():
    w = np.full(8760, 1.0)
    load = np.full(8760, 1.5)
    out = compose(
        _res(0.0, (0.0, 0.0), 0.0),
        [Pocket(["h"], ["f"], 0.01, load)],
        weights=w,
        periods=(("2030", 0, 8760),),
    )
    assert out["lole_hours"] == pytest.approx(87.6)
    assert out["lole_ci"] == pytest.approx([87.6, 87.6])
    assert out["eue_mwh"] == pytest.approx(131.4)  # 1.5 MW × 87.6 h
    assert out["eue_ci"] == pytest.approx([130.4, 132.4])


def test_hours_without_pocket_load_cannot_be_lost():
    w = np.ones(10)
    load = np.array([1.0] * 5 + [0.0] * 5)
    out = compose(
        _res(0.0, (0.0, 0.0), 0.0),
        [Pocket(["h"], ["f"], 0.1, load)],
        weights=w,
        periods=(),
    )
    assert out["lole_hours"] == pytest.approx(0.5)


def test_the_hub_and_the_pocket_compose_as_independent_events():
    w = np.ones(100)
    out = compose(
        _res(10.0, (8.0, 12.0), 50.0),
        [Pocket(["h"], ["f"], 0.2, np.ones(100))],
        weights=w,
        periods=(),
    )
    # 10 + 20 − 10·20/100 = 28; the CI shifts by the same deterministic term.
    assert out["lole_hours"] == pytest.approx(28.0)
    assert out["lole_ci"] == pytest.approx([8 + 20 - 1.6, 12 + 20 - 2.4])
    assert out["critical_pocket_eue_overlap_bound_mwh"] == pytest.approx(10.0)


def test_two_pockets_cut_off_in_the_same_hour_count_once():
    w = np.ones(100)
    p = [
        Pocket(["a"], ["fa"], 0.1, np.ones(100)),
        Pocket(["b"], ["fb"], 0.1, np.ones(100)),
    ]
    out = compose(_res(0.0, (0.0, 0.0), 0.0), p, weights=w, periods=())
    assert out["lole_hours"] == pytest.approx(100 * (1 - 0.9 * 0.9))


def test_each_period_composes_on_its_own_weights():
    # Two periods of 4 snapshots, weighted 10 and 20 h each.
    w = np.array([10.0] * 4 + [20.0] * 4)
    by = {
        "2030": {"lole_hours": 4.0, "lole_ci": [4.0, 4.0], "eue_mwh": 1.0},
        "2040": {"lole_hours": 0.0, "lole_ci": [0.0, 0.0], "eue_mwh": 0.0},
    }
    out = compose(
        _res(4.0, (4.0, 4.0), 1.0, by),
        [Pocket(["h"], ["f"], 0.5, np.ones(8))],
        weights=w,
        periods=(("2030", 0, 4), ("2040", 4, 8)),
    )
    # 2030: 4 + 20 − 4·20/40 = 22; 2040: 0 + 40 = 40.
    assert out["by_period"]["2030"]["lole_hours"] == pytest.approx(22.0)
    assert out["by_period"]["2040"]["lole_hours"] == pytest.approx(40.0)
    assert out["lole_hours"] == pytest.approx(62.0)


def test_no_pocket_leaves_the_result_as_it_was():
    res = _res(1.0, (0.5, 1.5), 2.0)
    assert compose(res, [], weights=np.ones(3), periods=()) is res


def test_a_bounded_pocket_is_not_composed():
    out = compose(
        _res(1.0, (0.5, 1.5), 2.0),
        [Pocket(["h"], ["f"], 0.5, np.ones(10), bound=True)],
        weights=np.ones(10),
        periods=(),
    )
    assert out["lole_hours"] == 1.0 and out["lole_ci"] == [0.5, 1.5]


@pytest.mark.parametrize(
    "hi, u, want",
    [
        (1.0, 0.1, "pass"),  # 1 + 0.1 × 10 = 2 ≤ 2
        (1.0, 0.2, "inconclusive"),  # 1 + 2 = 3 > 2
    ],
)
def test_the_bound_plus_the_ci_upper_bound_decides_a_pass(hi, u, want):
    rows = [
        {
            "name": "f",
            "model": "unsampled",
            "chained": False,
            "upper_bound": True,
            "buses": ["h"],
        }
    ]
    v, note = bounded_verdict(
        "pass",
        None,
        hi=hi,
        pockets=[Pocket(["h"], ["f"], u, np.ones(10), bound=True)],
        rows=rows,
        weights=np.ones(10),
        target_h=2.0,
        years=1.0,
    )
    assert v == want
    assert (note is None) == (want == "pass")


def test_a_bound_never_turns_a_verdict_into_a_pass_or_a_fail():
    p = [Pocket(["h"], ["f"], 0.9, np.ones(10), bound=True)]
    for v in ("fail", "inconclusive", None):
        assert bounded_verdict(
            v,
            "n",
            hi=0.0,
            pockets=p,
            rows=[],
            weights=np.ones(10),
            target_h=2.0,
            years=1.0,
        ) == (v, "n")

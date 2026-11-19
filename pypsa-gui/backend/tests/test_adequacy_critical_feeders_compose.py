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
    v, note, held = bounded_verdict(
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
    assert (held is None) == (want == "pass")
    if held:
        assert held["pockets"] == [
            {"buses": ["h"], "feeders": ["f"], "bound_h_per_year": pytest.approx(2.0)}
        ]
        assert "issue" not in note


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
        ) == (v, "n", None)


def test_a_single_period_reports_the_same_figure_as_the_total():
    by = {"2030": {"lole_hours": 1.0, "lole_ci": [1.0, 1.0], "eue_mwh": 2.0}}
    out = compose(
        _res(1.0, (1.0, 1.0), 2.0, by),
        [Pocket(["h"], ["f"], 0.5, np.ones(4))],
        weights=np.ones(4),
        periods=(("2030", 0, 4),),
    )
    # 1 + 2 − 1·2/4 = 2.5, in the period and in the total.
    assert out["lole_hours"] == pytest.approx(2.5)
    assert out["by_period"]["2030"]["lole_hours"] == pytest.approx(2.5)
    assert out["by_period"]["2030"]["eue_mwh"] == pytest.approx(4.0)


def test_periods_the_mc_did_not_report_compose_on_the_whole_horizon():
    by = {"2030": {"lole_hours": 0.0, "lole_ci": [0.0, 0.0], "eue_mwh": 0.0}}
    out = compose(
        _res(0.0, (0.0, 0.0), 0.0, by),
        [Pocket(["h"], ["f"], 0.5, np.ones(8))],
        weights=np.ones(8),
        periods=(("2030", 0, 4), ("2040", 4, 8)),
    )
    assert out["lole_hours"] == pytest.approx(4.0)
    assert out["by_period"] == by  # left as reported, not half-composed


def test_no_periods_given_is_the_whole_horizon():
    out = compose(
        _res(0.0, (0.0, 0.0), 0.0),
        [Pocket(["h"], ["f"], 0.5, np.ones(4))],
        weights=np.ones(4),
        periods=None,
    )
    assert out["lole_hours"] == pytest.approx(2.0)


def test_the_hub_eue_is_kept_and_a_missing_one_reads_as_zero():
    pocket = [Pocket(["h"], ["f"], 0.2, np.full(10, 3.0))]
    kept = compose(_res(1.0, (1.0, 1.0), 5.0), pocket, weights=np.ones(10), periods=())
    assert kept["eue_mwh"] == pytest.approx(5.0 + 0.2 * 10 * 3.0)
    missing = _res(1.0, (1.0, 1.0), 0.0)
    missing["eue_mwh"] = None
    missing.pop("eue_ci")
    out = compose(missing, pocket, weights=np.ones(10), periods=())
    assert out["eue_mwh"] == pytest.approx(6.0)
    assert out["critical_pocket_eue_overlap_bound_mwh"] == 0.0
    assert "eue_ci" not in out


def test_zero_weights_lose_no_hours_and_do_not_divide_by_zero():
    out = compose(
        _res(0.0, (0.0, 0.0), 1.0),
        [Pocket(["h"], ["f"], 0.5, np.ones(4))],
        weights=np.zeros(4),
        periods=(),
    )
    assert out["lole_hours"] == 0.0
    assert out["critical_pocket_eue_overlap_bound_mwh"] == 0.0


def _bounded(u=0.5, hours=4):
    return [Pocket(["h"], ["f"], u, np.ones(hours), bound=True)]


_BOUND_ROW = [
    {
        "name": "f",
        "model": "unsampled",
        "chained": False,
        "upper_bound": True,
        "buses": ["h"],
    }
]


def test_a_bound_exactly_at_the_goal_lets_the_pass_stand():
    # 0 + 0.5 × 4 = 2.0 exactly: at the goal is within it.
    v, _, held = bounded_verdict(
        "pass",
        None,
        hi=0.0,
        pockets=_bounded(),
        rows=_BOUND_ROW,
        weights=np.ones(4),
        target_h=2.0,
        years=1.0,
    )
    assert (v, held) == ("pass", None)


def test_without_a_goal_a_bound_decides_nothing():
    assert bounded_verdict(
        "pass",
        "n",
        hi=0.0,
        pockets=_bounded(u=0.9),
        rows=_BOUND_ROW,
        weights=np.ones(4),
        target_h=None,
        years=1.0,
    ) == ("pass", "n", None)


@pytest.mark.parametrize("years, per_year", [(2.0, 1.0), (0.0, 2.0)])
def test_the_bound_and_the_goal_are_stated_per_year(years, per_year):
    # 0.5 × 4 h = 2 h over the horizon; a goal of 1 h over it.
    v, note, held = bounded_verdict(
        "pass",
        None,
        hi=0.0,
        pockets=_bounded(),
        rows=_BOUND_ROW,
        weights=np.ones(4),
        target_h=1.0,
        years=years,
    )
    assert v == "inconclusive"
    assert held["pockets"][0]["bound_h_per_year"] == pytest.approx(per_year)
    assert held["target_h_per_year"] == pytest.approx(per_year / 2.0)


def test_a_chained_pocket_alone_withholds_the_pass_and_names_the_feeder():
    rows = [
        {
            "name": "g",
            "model": "unsampled",
            "chained": True,
            "upper_bound": False,
            "buses": ["h"],
        }
    ]
    v, note, held = bounded_verdict(
        "pass",
        None,
        hi=0.0,
        pockets=[],
        rows=rows,
        weights=np.ones(4),
        target_h=2.0,
        years=1.0,
    )
    assert v == "inconclusive"
    assert held == {"pockets": [], "chained": ["g"], "target_h_per_year": 2.0}
    assert note.startswith("g feeds a must-stay-on bus") and "straight from" in note

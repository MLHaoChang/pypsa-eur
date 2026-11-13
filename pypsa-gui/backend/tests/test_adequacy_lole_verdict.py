# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Adequacy loops 04: the one LOLE-vs-goal rule (`lole_verdict`)."""

from __future__ import annotations

import math

import pytest

from services.adequacy.lole_verdict import event_hours, lole_verdict

CAP = 2000


@pytest.mark.parametrize(
    "lole,ci,target,event,verdict",
    [
        (2.5, (2.0, 3.0), 2.0, 0.73, "miss"),
        # confident arm: CI upper at the goal is a pass (<=), just above is not
        (1.46, (1.0, 2.0), 2.0, 0.73, "pass"),
        (1.46, (1.0, 2.0001), 2.0, 0.73, "not_confident"),
        # one-hour arm: lole + event exactly at the goal passes (<=)
        (1.27, (0.5, 2.5), 2.0, 0.73, "pass"),
        (1.28, (0.5, 2.5), 2.0, 0.73, "not_confident"),
        # the issue's eh_h2_hub case: 1.46 + 0.73 = 2.19 > 2
        (1.46, (0.4, 2.5), 2.0, 0.73, "not_confident"),
        # the estimate at the goal is not above it
        (2.0, (1.0, 3.0), 2.0, 0.0, "pass"),
        (2.0, (1.0, 3.0), 2.0, 0.73, "not_confident"),
        (0.0, (0.0, 0.0), 2.0, 0.73, "pass"),
        # no event size known: only the confident arm can pass
        (0.5, (0.0, 2.5), 2.0, None, "not_confident"),
    ],
)
def test_the_verdict_table(lole, ci, target, event, verdict):
    assert lole_verdict(lole, ci, target, event, 500, CAP)["verdict"] == verdict


def test_settle_draws_is_where_the_upper_bound_reaches_the_goal():
    v = lole_verdict(1.46, (0.4, 2.52), 2.0, 0.73, 500, CAP)
    # 500 * ((2.52 - 1.46) / (2.0 - 1.46))**2 = 1926.4 -> 1927
    assert v["settle_draws"] == 1927 and v["settle_reason"] is None
    assert v["confident"] is False and v["event_h"] == 0.73


@pytest.mark.parametrize(
    "lole,ci,why",
    [
        (1.46, (0.4, 3.0), "beyond the engine cap of 2000 draws"),
        (2.0, (1.0, 3.0), "the estimate equals the goal"),
        (1.0, (1.0, 1.0), None),  # confident: settled, no draws needed
        (1.5, (1.0, math.nan), "the confidence interval is degenerate"),
        (1.5, (1.0, 1.5), None),  # confident
    ],
)
def test_settle_draws_is_null_with_a_reason(lole, ci, why):
    v = lole_verdict(lole, ci, 2.0, 0.73, 500, CAP)
    assert v["settle_draws"] is None
    assert v["settle_reason"] == why


def test_a_pass_or_a_miss_carries_no_settle_count():
    for lole in (0.1, 3.0):
        v = lole_verdict(lole, (0.0, 3.5), 2.0, 0.73, 500, CAP)
        assert v["verdict"] in ("pass", "miss") and v["settle_draws"] is None


def test_event_hours_is_the_heaviest_weight_over_the_draws_that_ran():
    assert event_hours([1.0, 365.0, 0.0, 30.0], 500) == pytest.approx(0.73)
    assert event_hours([0.0, -1.0], 500) is None
    assert event_hours([3.0], 0) is None


def _net(gen_w=365.0, obj_w=1.0):
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(range(6))
    n.snapshot_weightings["generators"] = gen_w
    n.snapshot_weightings["objective"] = obj_w
    n.snapshot_weightings.loc[n.snapshots[0], "generators"] = 2.0 * gen_w
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=50.0)
    for i in range(3):
        n.add("Generator", f"g{i}", bus="b", p_nom=30.0)
    n.generators["outage_rate_value"] = 0.2
    n.generators["mttr_hours"] = 12.0
    return n


def test_event_h_uses_the_generator_weights_and_the_draws_that_ran():
    """D1: an adaptive run that stops early, and generator weightings that
    differ from the objective's: event_h is the heaviest GENERATOR weight
    over the draws that produced the estimate."""
    from services.adequacy.mc import mc_adequacy, snapshot_inputs

    m = mc_adequacy(
        snapshot_inputs(_net()), draws=100, seed=1, max_draws=2000, cov_target=0.5
    )
    n = m["n_samples"]
    assert n < 2000  # stopped early
    assert m["event_h"] == pytest.approx(730.0 / n)


# ── the words a not-confident loop ends on (D4/D5) ─────────────────────────

def _nc_mc(settle_draws=1200, reason=None, event_h=0.73):
    return {"lole_hours": 1.46, "lole_ci": [0.2, 3.5],
            "goal": {"verdict": "not_confident", "confident": False,
                     "event_h": event_h, "settle_draws": settle_draws,
                     "settle_reason": reason}}


def test_the_not_confident_copy_names_the_settle_and_the_network_left():
    from services.adequacy.lole_verdict import not_confident_copy
    text = not_confident_copy(_nc_mc(), 2.0, "ε = 0.035‱")
    assert text.startswith("Not confident")
    assert "one sampled shortfall hour at the heaviest snapshot (0.73 h)" in text
    assert "~1200 draws if the mean and spread hold" in text
    assert "not a pass" in text
    assert "your own plan, not that one" in text


def test_the_not_confident_copy_says_when_draws_alone_cannot_settle_it():
    from services.adequacy.lole_verdict import not_confident_copy
    text = not_confident_copy(
        _nc_mc(None, "beyond the engine cap of 2000 draws", None), 2.0, "x")
    assert "beyond the engine cap of 2000 draws" in text
    assert "did not report" in text


def test_goal_fields_prefer_the_nearest_iterate():
    from services.adequacy.lole_verdict import goal_fields
    near = {"mc": _nc_mc()}
    final = {"mc": {"goal": {"event_h": 0.1, "settle_draws": None,
                             "settle_reason": None}}}
    assert goal_fields(near, final) == {"event_h": 0.73, "settle_draws": 1200,
                                        "settle_reason": None}
    assert goal_fields(None, final)["event_h"] == 0.1
    assert goal_fields(None, None) == {"event_h": None, "settle_draws": None,
                                       "settle_reason": None}

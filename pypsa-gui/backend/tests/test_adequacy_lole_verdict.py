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
        (1.46, (0.4, 3.0), "beyond the limit of 2000 draws"),
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
    assert event_hours([1.0, 365.0, 0.0, 30.0], 500, []) == pytest.approx(0.73)
    assert event_hours([0.0, -1.0], 500, []) is None
    for n in (0, None, -1):
        assert event_hours([3.0], n, [9.0]) is None


def test_event_hours_is_the_heaviest_draw_when_it_outweighs_a_snapshot():
    """E2, the hub-like case: one sampled outage shorts several snapshots, so
    the heaviest draw (208.6 h) outweighs the heaviest snapshot (52.14 h).
    The goal is moved to 1.8 h so the case reads not_confident; the shipped
    hub at 2.0 h stays met (1.46 + 0.417 <= 2), as D7 measured.
    BROKEN VARIANT (bite): w_max alone gives 0.104 h and a pass."""
    draws = [0.0] * 496 + [208.6, 208.6, 208.6, 104.2]
    e = event_hours([52.14] * 168, 500, draws)
    assert e == pytest.approx(208.6 / 500)
    lole = sum(draws) / 500  # 1.46
    v = lole_verdict(lole, (0.08, 2.84), 1.8, e, 500, CAP)
    assert v["verdict"] == "not_confident"
    assert (
        lole_verdict(lole, (0.08, 2.84), 1.8, 52.14 / 500, 500, CAP)["verdict"]
        == "pass"
    )


def test_a_one_draw_estimate_is_not_a_pass_eh_datacenter_shape():
    """E2/E7, eh_datacenter as shipped: 1.147 h/yr over 500 draws rests on ONE
    draw (573.5 weighted h), CI [0, 3.40], heaviest snapshot 52.14 h. One more
    such draw gives 2.29 > 2.
    BROKEN VARIANT (bite): w_max alone gives 0.104 h and a false pass."""
    draws = [0.0] * 499 + [573.5]
    lole = sum(draws) / 500
    e = event_hours([52.14] * 168, 500, draws)
    assert e == pytest.approx(lole)
    v = lole_verdict(lole, (0.0, 3.3955), 2.0, e, 500, CAP)
    assert v["verdict"] == "not_confident"
    assert v["settle_draws"] is None
    assert v["settle_reason"] == "beyond the limit of 2000 draws"


def test_event_hours_keeps_the_snapshot_floor_when_nothing_was_sampled():
    """E2, nothing sampled: 0 h with an interval straddling a goal at or below
    one shortfall hour at the heaviest snapshot is not a pass.
    BROKEN VARIANT (bite): the heaviest draw alone gives no event size here,
    caught by the value assertion."""
    e = event_hours([52.14] * 168, 500, [0.0] * 500)
    assert e == pytest.approx(52.14 / 500)
    v = lole_verdict(0.0, (0.0, 0.2), 0.1, e, 500, CAP)
    assert v["verdict"] == "not_confident"


def test_an_infinite_upper_bound_is_degenerate_not_a_crash():
    v = lole_verdict(1.5, (1.0, math.inf), 2.0, 0.73, 500, CAP)
    assert v["verdict"] == "not_confident" and v["confident"] is False
    assert v["settle_draws"] is None
    assert v["settle_reason"] == "the confidence interval is degenerate"


def test_settle_draws_at_the_cap_is_still_a_count():
    # need = 500 * ((3.0 - 1.0) / (2.0 - 1.0))**2 = 2000 == CAP: not beyond it
    v = lole_verdict(1.0, (0.0, 3.0), 2.0, 1.5, 500, CAP)
    assert v["verdict"] == "not_confident"
    assert v["settle_draws"] == CAP and v["settle_reason"] is None


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


def test_event_h_uses_the_generator_weights_and_the_draws_that_ran(monkeypatch):
    """D1/E1: an adaptive run that stops early, and generator weightings that
    differ from the objective's: event_h is max(heaviest GENERATOR weight,
    heaviest draw) over the draws that produced the estimate, and the draw
    array is exactly the one the mean is taken over."""
    import numpy as np

    from services.adequacy import lole_verdict as lv
    from services.adequacy.mc import mc_adequacy, snapshot_inputs

    seen = {}
    real = lv.event_hours

    def spy(weights, n_samples, draws):
        seen.update(
            weights=np.asarray(weights).copy(),
            n=n_samples,
            draws=np.asarray(draws).copy(),
        )
        return real(weights, n_samples, draws)

    monkeypatch.setattr(lv, "event_hours", spy)
    m = mc_adequacy(
        snapshot_inputs(_net()), draws=100, seed=1, max_draws=2000, cov_target=0.5
    )
    n = m["n_samples"]
    assert n < 2000  # stopped early
    assert seen["n"] == n and len(seen["draws"]) == n
    assert float(np.mean(seen["draws"])) == pytest.approx(m["lole_hours"])
    assert seen["weights"].max() == pytest.approx(730.0)  # generator, not objective
    assert m["event_h"] == pytest.approx(max(730.0, seen["draws"].max()) / n)


# ── the words a not-confident loop ends on (D4/D5) ─────────────────────────


def _nc_mc(settle_draws=1200, reason=None, event_h=0.73):
    return {
        "lole_hours": 1.46,
        "lole_ci": [0.2, 3.5],
        "goal": {
            "verdict": "not_confident",
            "confident": False,
            "event_h": event_h,
            "settle_draws": settle_draws,
            "settle_reason": reason,
        },
    }


def test_the_not_confident_copy_names_the_settle_and_the_network_left():
    from services.adequacy.lole_verdict import not_confident_copy

    text = not_confident_copy(_nc_mc(), 2.0, "ε = 0.035‱")
    assert text.startswith("Not confident")
    assert (
        "one more sampled draw as heavy as the heaviest seen would put it above" in text
    )
    assert "About 1200 draws would settle it, if the estimate and its range" in text
    assert "not a pass" in text
    assert "the network you have now is your own plan, not that one" in text


def test_the_not_confident_copy_says_when_draws_alone_cannot_settle_it():
    from services.adequacy.lole_verdict import not_confident_copy

    text = not_confident_copy(
        _nc_mc(None, "beyond the limit of 2000 draws", None), 2.0, "x"
    )
    assert "beyond the limit of 2000 draws" in text
    assert "did not report" in text


def test_goal_fields_prefer_the_nearest_iterate():
    from services.adequacy.lole_verdict import goal_fields

    near = {"mc": _nc_mc()}
    final = {
        "mc": {"goal": {"event_h": 0.1, "settle_draws": None, "settle_reason": None}}
    }
    assert goal_fields(near, final) == {
        "event_h": 0.73,
        "settle_draws": 1200,
        "settle_reason": None,
    }
    assert goal_fields(None, final)["event_h"] == 0.1
    assert goal_fields(None, None) == {
        "event_h": None,
        "settle_draws": None,
        "settle_reason": None,
    }

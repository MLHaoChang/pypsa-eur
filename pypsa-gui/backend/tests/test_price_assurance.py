"""bus-price/1's validity guards (harness/price_calculations.py `calculate`), one hand case each.

Each case drives `calculate` on a hand-written raw capture, so the guard is the
only thing that decides the outcome. Expected values are literals worked by
hand in the comment beside them (S1); none is computed through the profile.
"""

import math

import pytest

from harness import price_calculations as price

INDEX = ["2026-01-01T00:00:00", "2026-01-01T01:00:00", "2026-01-01T02:00:00"]
METRICS = ("mean_price", "min_price", "max_price", "hours_at_voll")


def _raw(price_values, weight, voll=1000.0, valid_solve=True):
    return {
        "version": 1,
        "profile": price.PROFILE,
        "entity_id": "B1",
        "index": INDEX,
        "weight": weight,
        "price": price_values,
        "voll": voll,
        "valid_solve": valid_solve,
    }


def _run(raw):
    facts, slots = price.calculate(raw, {"profile": price.PROFILE, "entity_id": "B1"})
    values = {f["metric"]: f["value"] for f in facts}
    reasons = {
        s["metric"]: s["reason"] for s in slots if s["disposition"] == "unavailable"
    }
    return values, reasons


def _refused(reason):
    return {m: reason for m in METRICS}


@pytest.mark.parametrize(
    "price_values, weight",
    [
        ([10.0, math.nan, 30.0], [1.0, 1.0, 1.0]),
        ([10.0, None, 30.0], [1.0, 1.0, 1.0]),
        ([10.0, 20.0, 30.0], [1.0, math.nan, 1.0]),
        ([10.0, 20.0, 30.0], [1.0, math.inf, 1.0]),
    ],
    ids=["nan-price", "missing-price", "nan-weight", "inf-weight"],
)
def test_a_non_finite_price_or_weight_refuses_every_metric(price_values, weight):
    values, reasons = _run(_raw(price_values, weight))
    assert values == {}
    assert reasons == _refused("invalid_or_missing_price_data")


def test_one_zero_weight_among_positive_weights_is_still_answered():
    """`w >= 0`, not `w > 0`: a zero-weight snapshot is a valid capture."""
    values, reasons = _run(_raw([10.0, 20.0, 30.0], [1.0, 0.0, 2.0], voll=30.0))
    assert reasons == {}
    assert values == {
        # (1*10 + 0*20 + 2*30) / (1 + 0 + 2) = 70 / 3
        "mean_price": 23.333333333333332,
        # min and max read every snapshot of the window, weighted or not.
        "min_price": 10.0,
        "max_price": 30.0,
        # only 30 >= 30*(1 - 1e-6) = 29.99997, at weight 2.
        "hours_at_voll": 2.0,
    }


def test_a_negative_weight_refuses_every_metric():
    # The weights still sum to 1 - 0.5 + 2 = 2.5 > 0, so only the sign guard refuses.
    values, reasons = _run(_raw([10.0, 20.0, 30.0], [1.0, -0.5, 2.0]))
    assert values == {}
    assert reasons == _refused("invalid_or_missing_price_data")


def test_all_zero_weights_refuse_every_metric():
    """The mean's denominator would be 0; `sum(weight) > 0` refuses it."""
    values, reasons = _run(_raw([10.0, 20.0, 30.0], [0.0, 0.0, 0.0]))
    assert values == {}
    assert reasons == _refused("invalid_or_missing_price_data")


@pytest.mark.parametrize("voll", [None, 0.0, -5.0])
def test_hours_at_voll_need_a_positive_voll(voll):
    values, reasons = _run(_raw([10.0, 20.0, 30.0], [1.0, 0.5, 2.0], voll=voll))
    assert reasons == {"hours_at_voll": "voll_missing_or_zero"}
    assert values == {
        # (1*10 + 0.5*20 + 2*30) / 3.5 = 80 / 3.5
        "mean_price": 22.857142857142858,
        "min_price": 10.0,
        "max_price": 30.0,
    }


def test_a_price_exactly_at_the_voll_threshold_counts_and_one_ulp_below_does_not():
    # 1000 * (1 - 1e-6) is the float 999.999 (worked: 1000 * 0.999999).
    at = 999.999
    below = math.nextafter(at, -math.inf)
    values, reasons = _run(_raw([at, below, 1000.0], [0.5, 4.0, 2.0], voll=1000.0))
    assert reasons == {}
    # 999.999 at weight 0.5 and 1000 at weight 2 count; the one below, at weight 4, does not.
    assert values["hours_at_voll"] == 2.5


def test_an_unverified_solve_names_unverified_solve_for_every_metric():
    """Even the VoLL-less hours read unverified_solve, not voll_missing_or_zero."""
    values, reasons = _run(
        _raw([10.0, 20.0, 30.0], [1.0, 0.5, 2.0], voll=None, valid_solve=False)
    )
    assert values == {}
    assert reasons == _refused("unverified_solve")


def test_a_capture_of_another_bus_or_profile_is_refused():
    with pytest.raises(ValueError, match="price_source_identity"):
        price.calculate(
            _raw([10.0, 20.0, 30.0], [1.0, 1.0, 1.0]),
            {"profile": price.PROFILE, "entity_id": "B2"},
        )
    with pytest.raises(ValueError, match="price_source_identity"):
        price.calculate(
            {**_raw([10.0, 20.0, 30.0], [1.0, 1.0, 1.0]), "version": 2},
            {"profile": price.PROFILE, "entity_id": "B1"},
        )
    with pytest.raises(ValueError, match="price_source_identity"):
        price.calculate(
            {**_raw([10.0, 20.0, 30.0], [1.0, 1.0, 1.0]), "profile": "curtailment/1"},
            {"profile": price.PROFILE, "entity_id": "B1"},
        )

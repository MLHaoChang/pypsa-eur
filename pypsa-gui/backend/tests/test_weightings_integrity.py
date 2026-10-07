"""
Weightings: the right period, and a number that means something.

Two defects on the same surface.

QA-N3 — `set_investment_periods` sorts and de-duplicates `periods`, then
assigns `objective_weightings` / `years_weightings` POSITIONALLY against the
sorted index. Submit `periods=[2040, 2030]` and the weights land on the wrong
rows, silently. A list of the wrong length either broadcast or surfaced a raw
pandas error as a 500.

QA-N6 — every weighting entry point parsed with a bare `float(...)`, so
`-5`, `inf` and `nan` all passed. A negative weight inverts the sign of that
period's contribution to the objective; a non-finite one poisons every
downstream sum and surfaces as `NaN` costs with nothing to point at.

The handlers are called directly rather than over HTTP: JSON cannot carry
`inf` or `nan`, and those are two of the values under test.
"""
from __future__ import annotations

import math

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from models.schemas import InvestmentPeriods
from routers.network_time_axis import (
    set_investment_periods,
    update_investment_period_weightings,
    update_snapshot_weightings,
)


def _flat_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "B")
    n.add("Load", "L", bus="B", p_set=50.0)
    return n


@pytest.fixture
def flat(install_network):
    return install_network(_flat_network())


@pytest.fixture
def multi(install_network):
    live = install_network(_flat_network())
    set_investment_periods(InvestmentPeriods(periods=[2030, 2040]))
    return live


# ── QA-N3: weights follow their period, not their position ────────────────

def test_weights_land_on_the_period_they_were_submitted_with(flat):
    # Descending on purpose — ascending input cannot distinguish the two.
    set_investment_periods(InvestmentPeriods(
        periods=[2040, 2030],
        objective_weightings=[0.5, 1.0],
        years_weightings=[15.0, 5.0],
    ))
    ipw = flat.investment_period_weightings
    assert ipw.at[2040, "objective"] == pytest.approx(0.5)
    assert ipw.at[2030, "objective"] == pytest.approx(1.0)
    assert ipw.at[2040, "years"] == pytest.approx(15.0)
    assert ipw.at[2030, "years"] == pytest.approx(5.0)


def test_ascending_submission_is_unchanged(flat):
    # The control: the common case must keep behaving exactly as before.
    set_investment_periods(InvestmentPeriods(
        periods=[2030, 2040],
        objective_weightings=[1.0, 0.5],
        years_weightings=[5.0, 15.0],
    ))
    ipw = flat.investment_period_weightings
    assert ipw.at[2030, "objective"] == pytest.approx(1.0)
    assert ipw.at[2040, "objective"] == pytest.approx(0.5)


def test_weightings_length_mismatch_is_refused(flat):
    with pytest.raises(HTTPException) as exc:
        set_investment_periods(InvestmentPeriods(
            periods=[2030, 2040, 2050], objective_weightings=[1.0, 0.5],
        ))
    assert exc.value.status_code == 400
    # The message names both counts — otherwise the caller has to guess which
    # of the two lists it got wrong.
    assert "2" in str(exc.value.detail) and "3" in str(exc.value.detail)


def test_a_refused_weighting_list_does_not_half_apply(flat):
    with pytest.raises(HTTPException):
        set_investment_periods(InvestmentPeriods(
            periods=[2030, 2040], objective_weightings=[1.0, float("nan")],
        ))
    # The periods themselves must not have been rebuilt either: a refusal is
    # a refusal, not a partial apply the user has to undo.
    assert flat.investment_periods.empty
    assert not isinstance(flat.snapshots, pd.MultiIndex)


# ── QA-N6: a weighting is a finite, non-negative multiplier ────────────────

@pytest.mark.parametrize("bad", [-5.0, float("inf"), float("nan")])
def test_period_weighting_rejects_bad_values_per_period(multi, bad):
    with pytest.raises(HTTPException) as exc:
        update_investment_period_weightings({"updates": {2030: {"years": bad}}})
    assert exc.value.status_code == 400
    assert multi.investment_period_weightings.at[2030, "years"] == pytest.approx(1.0)


@pytest.mark.parametrize("key", ["all_years", "all_objective"])
@pytest.mark.parametrize("bad", [-5.0, float("inf"), float("nan")])
def test_period_weighting_rejects_bad_values_broadcast(multi, key, bad):
    with pytest.raises(HTTPException) as exc:
        update_investment_period_weightings({key: bad})
    assert exc.value.status_code == 400
    col = "years" if key == "all_years" else "objective"
    assert multi.investment_period_weightings[col].eq(1.0).all()


@pytest.mark.parametrize("bad", [-5.0, float("inf"), float("nan")])
def test_snapshot_weighting_rejects_bad_broadcast(flat, bad):
    with pytest.raises(HTTPException) as exc:
        update_snapshot_weightings({"all": bad})
    assert exc.value.status_code == 400
    assert flat.snapshot_weightings["objective"].eq(1.0).all()


@pytest.mark.parametrize("bad", [-5.0, float("inf"), float("nan")])
def test_snapshot_weighting_rejects_bad_per_row(flat, bad):
    key = flat.snapshots[0].isoformat()
    with pytest.raises(HTTPException) as exc:
        update_snapshot_weightings({"updates": {key: {"objective": bad}}})
    assert exc.value.status_code == 400
    assert flat.snapshot_weightings["objective"].eq(1.0).all()


def test_zero_is_still_a_legal_weight(flat):
    # The boundary the guard must not over-reach on: a zero-weight snapshot is
    # how a user excludes an hour without deleting it.
    key = flat.snapshots[0].isoformat()
    update_snapshot_weightings({"updates": {key: {"objective": 0.0}}})
    assert flat.snapshot_weightings.at[flat.snapshots[0], "objective"] == 0.0
    assert math.isfinite(flat.snapshot_weightings["objective"].sum())

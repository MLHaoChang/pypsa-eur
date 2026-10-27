"""The flicker screen (grid-to-process issue 17, PR 2)."""
import math

import pandas as pd
import pytest

from gridspine.schema.contracts import ContractError
from gridspine.static import flicker as fl

#: The demo planning levels the tests judge against (IEC TR 61000-3-7 HV-EHV, assumed).
LIMITS = fl.Limits(pst=0.8, plt=0.6)


def _units(*rows):
    cols = ["unit", "poc", "step_mva", "changes_per_min"]
    return pd.DataFrame([dict(zip(cols, r)) for r in rows], columns=cols)


def _one(step, rate, sk=1000.0, limits=LIMITS):
    return fl.flicker_table(_units(("H1", "PoC", step, rate)), {"PoC": sk}, limits)


# --- the analytic method -----------------------------------------------------

def test_pst_by_hand():
    # d 2 %, 2 changes/min: t_f = 2.3 * 2^3.2 s, 20 changes in 10 min.
    expected = (20 * 2.3 * 2.0 ** 3.2 / 600.0) ** (1 / 3.2)
    row = _one(20.0, 2.0).units.iloc[0]
    assert row["d_pct"] == pytest.approx(2.0, rel=1e-12)
    assert row["pst"] == pytest.approx(expected, rel=1e-12)
    assert row["pst"] == pytest.approx(0.8963, abs=1e-4)
    assert row["plt"] == row["pst"]                         # a steady pattern over 2 h


def test_doubling_the_fault_level_halves_pst():
    one = _one(20.0, 2.0, sk=1000.0).pocs.iloc[0]["pst"]
    two = _one(20.0, 2.0, sk=2000.0).pocs.iloc[0]["pst"]
    assert abs(two - one / 2) < 1e-9


def test_one_on_off_cycle_is_two_voltage_changes():
    assert fl.changes_per_min(3) == 6.0
    cycles = _one(20.0, fl.changes_per_min(1.0)).units.iloc[0]["pst"]
    assert cycles == pytest.approx(fl.pst(2.0, 2.0), rel=1e-12)
    assert cycles == pytest.approx(fl.pst(2.0, 1.0) * 2 ** (1 / 3.2), rel=1e-12)


def test_units_sum_by_the_cubic_law():
    st = fl.flicker_table(_units(("H1", "PoC", 20.0, 2.0), ("H2", "PoC", 10.0, 4.0)), {"PoC": 1000.0}, LIMITS)
    p1, p2 = st.units["pst"]
    assert st.pocs.iloc[0]["pst"] == pytest.approx((p1 ** 3 + p2 ** 3) ** (1 / 3), rel=1e-12)
    assert st.pocs.iloc[0]["law"] == "alpha 3"


# --- acceptance --------------------------------------------------------------

def test_a_burst_heater_over_the_limit_fails():
    row = _one(20.0, 2.0).units.iloc[0]
    assert row["verdict"] == "fail" and "Pst above 0.8" in row["note"] and "Plt above 0.6" in row["note"]


@pytest.mark.parametrize("step, rate", [(10.0, 2.0), (20.0, 0.5)])
def test_a_smaller_step_or_a_lower_rate_passes(step, rate):
    st = _one(step, rate)
    assert st.units.iloc[0]["verdict"] == "pass" and st.pocs.iloc[0]["verdict"] == "pass"


@pytest.mark.parametrize("step, rate", [(0.0, 30.0), (20.0, 0.0)])
def test_a_steady_load_passes_with_no_flicker(step, rate):
    row = _one(step, rate).units.iloc[0]
    assert row["pst"] == 0.0 and row["verdict"] == "pass"


@pytest.mark.parametrize("step, rate, missing", [(None, 2.0, "Largest power step"),
                                                  (20.0, math.nan, "Changes per minute")])
def test_a_unit_without_its_inputs_is_no_input_and_blocks_a_pass(step, rate, missing):
    st = fl.flicker_table(_units(("H1", "PoC", step, rate), ("H2", "PoC", 1.0, 0.1)), {"PoC": 1000.0}, LIMITS)
    row = st.units.iloc[0]
    assert row["verdict"] == "unknown" and row["note"] == f"no input: {missing} not set"
    poc = st.pocs.iloc[0]
    assert poc["verdict"] == "unknown" and poc["no_input_units"] == ["H1"]
    assert "no input for H1: it blocks a pass" in poc["note"]


def test_a_failing_unit_still_fails_beside_one_without_input():
    st = fl.flicker_table(_units(("H1", "PoC", None, 2.0), ("H2", "PoC", 20.0, 2.0)), {"PoC": 1000.0}, LIMITS)
    assert st.pocs.iloc[0]["verdict"] == "fail"


# --- the analytic method's range ----------------------------------------------

def test_changes_one_second_apart_are_in_range_and_faster_is_unknown():
    assert _one(1.0, 60.0).units.iloc[0]["verdict"] == "pass"
    st = _one(1.0, 60.5)
    row = st.units.iloc[0]
    assert row["verdict"] == "unknown" and row["pst"] is None and "flicker-meter study" in row["note"]
    assert st.pocs.iloc[0]["verdict"] == "unknown" and st.pocs.iloc[0]["outside_range_units"] == ["H1"]


def test_a_step_above_the_range_is_unknown_never_pass():
    edge = _one(100.0, 0.001).units.iloc[0]                                      # d 10 %, in range
    assert edge["d_pct"] == pytest.approx(10.0) and edge["pst"] == pytest.approx(fl.pst(10.0, 0.001))
    assert edge["verdict"] == "pass"
    row = _one(100.5, 0.001).units.iloc[0]
    assert row["verdict"] == "unknown" and "above the analytic method's 10 %" in row["note"]


def test_a_steady_load_is_not_bound_by_the_range():
    for step, rate in ((0.0, 120.0), (200.0, 0.0)):
        row = _one(step, rate).units.iloc[0]
        assert row["verdict"] == "pass" and row["pst"] == 0.0, (step, rate)


# --- limits and inputs ---------------------------------------------------------

def test_no_limit_is_unknown():
    row = _one(20.0, 2.0, limits=fl.Limits()).units.iloc[0]
    assert row["verdict"] == "unknown" and "sets no flicker limit" in row["note"]


def test_one_limit_alone_judges():
    assert _one(15.0, 2.0, limits=fl.Limits(pst=0.8)).units.iloc[0]["verdict"] == "pass"
    assert _one(15.0, 2.0, limits=fl.Limits(plt=0.6)).units.iloc[0]["verdict"] == "fail"


def test_no_fault_level_is_unknown():
    st = _one(20.0, 2.0, sk=math.nan)
    assert st.units.iloc[0]["verdict"] == st.pocs.iloc[0]["verdict"] == "unknown"
    assert st.pocs.iloc[0]["note"] == fl.NO_SK


def test_a_negative_input_is_refused():
    with pytest.raises(ContractError, match="Largest power step must be 0 or more"):
        _one(-1.0, 2.0)


def test_limits_from_profile():
    lim, cited = fl.limits_from_profile({"pst_limit": {"value": 0.8, "clause": "c1", "source": "assumed"},
                                         "plt_limit": {"value": 0.6, "clause": "c2", "source": "code"}})
    assert lim == fl.Limits(pst=0.8, plt=0.6)
    assert [c["key"] for c in cited] == ["pst_limit", "plt_limit"]
    assert fl.limits_from_profile({}) == (fl.Limits(), [])


def test_a_poc_with_no_unit_to_compute_shows_no_pst():
    poc = fl.flicker_table(_units(("H1", "PoC", None, 2.0)), {"PoC": 1000.0}, LIMITS).pocs.iloc[0]
    assert poc["pst"] is None and poc["verdict"] == "unknown" and poc["note"] == "no unit with a Pst: no input for H1"


def test_the_ledger_states_the_screens_assumptions():
    text = " ".join(fl.LEDGER)
    for phrase in ("X/R is conservative", "c factor", "F = 1", "Plt equals Pst", "alpha 3", "alpha 1",
                   "on/off cycle is two changes", "flicker-meter study", "margin is overstated"):
        assert phrase in text, phrase

"""The motor-start voltage-change screen (grid-to-process issue 18): a fixture PoC at 150 MVA and one process
unit with a 2000 kW motor, every value checked against a hand calculation."""
import math

import pandas as pd
import pytest

from gridspine.schema.contracts import ContractError
from gridspine.static import motor_start as ms

LIMIT = 3.0
POC = {"PoC": ms.Source(sk_mva=150.0)}
# Hand values. S_rated = 2000 kW / 0.85 = 2.352941 MVA; S_sc = 150 / 1.1 = 136.3636 MVA.
# DOL: S_start = 6 * 2.352941 = 14.117647 MVA, sin phi = sqrt(1 - 0.30^2) = 0.953939,
#      Q_start = 13.467372 Mvar, dU = 13.467372 / 136.3636 * 100 = 9.876073 %  (> 3 %: fail).
# VFD: S_start = 2.352941 MVA, sin phi = sqrt(1 - 0.95^2) = 0.312250,
#      Q_start = 0.734706 Mvar, dU = 0.538784 %  (<= 3 %: pass).
DOL_DU = 9.876073
VFD_DU = 0.538784


def _units(*rows):
    cols = ["unit", "poc", "motor_kw", "start_method", "i_start_pu"]
    return pd.DataFrame([dict(zip(cols, r)) for r in rows], columns=cols)


def _one(method, kw=2000.0, k=None, sources=POC, q_gfm=None, limit=LIMIT):
    return ms.motor_start_table(_units(("Mill", "PoC", kw, method, k)), sources,
                                None if q_gfm is None else {"PoC": q_gfm}, limit).iloc[0]


def test_a_dol_start_fails_by_hand():
    row = _one("dol")
    assert row["s_start_mva"] == pytest.approx(14.117647, rel=1e-6)
    assert row["s_sc_mva"] == pytest.approx(136.363636, rel=1e-6)
    assert row["du_pct"] == pytest.approx(DOL_DU, rel=1e-6)
    assert row["verdict"] == "fail" and row["margin_pct"] == pytest.approx(LIMIT - DOL_DU, rel=1e-6)


def test_a_vfd_start_passes_by_hand():
    row = _one("vfd")
    assert row["du_pct"] == pytest.approx(VFD_DU, rel=1e-6)
    assert row["verdict"] == "pass"


def test_a_soft_start_by_hand():
    # S_start = 3 * 2.352941 = 7.058824 MVA, sin phi = sqrt(1 - 0.35^2) = 0.936750, dU = 4.849057 %.
    assert _one("soft")["du_pct"] == pytest.approx(4.849057, rel=1e-6)


def test_the_units_own_start_multiple_overrides_the_method():
    # DOL at 7 x rated: 7 / 6 of the DOL hand value.
    assert _one("dol", k=7.0)["du_pct"] == pytest.approx(DOL_DU * 7 / 6, rel=1e-6)


def test_q_gfm_zero_and_positive_by_hand():
    zero = _one("dol", q_gfm=0.0)
    assert zero["du_pct"] == pytest.approx(DOL_DU, rel=1e-6) and "Q_gfm 0" not in zero["note"]
    # (13.467372 - 5) / 136.3636 * 100 = 6.209406 %.
    five = _one("dol", q_gfm=5.0)
    assert five["q_gfm_mvar"] == 5.0
    assert five["du_pct"] == pytest.approx(6.209406, rel=1e-6)


def test_q_gfm_not_stated_is_zero_and_says_so():
    row = _one("dol")
    assert row["q_gfm_mvar"] == 0.0 and row["du_pct"] == pytest.approx(DOL_DU, rel=1e-6)
    assert "Q_gfm 0 (conservative)" in row["note"]


def test_q_gfm_above_the_start_draw_gives_zero_not_a_rise():
    assert _one("vfd", q_gfm=5.0)["du_pct"] == 0.0


def test_the_minimum_case_is_used_when_the_poc_has_one():
    # S_k''min 150 MVA / c_min 1.0 (above 1 kV): dU = 13.467372 / 150 * 100 = 8.978248 %.
    row = _one("dol", sources={"PoC": ms.Source(sk_mva=400.0, sk_min_mva=150.0, vn_kv=110.0)})
    assert row["sk_case"] == "min" and row["c"] == 1.0 and row["sk_mva"] == 150.0
    assert row["du_pct"] == pytest.approx(8.978248, rel=1e-6)
    assert "minimum case" in row["note"] and "entered" not in row["note"]


def test_an_unknown_voltage_takes_the_smaller_s_sc():
    row = _one("dol", sources={"PoC": ms.Source(sk_min_mva=150.0)})
    assert row["c"] == 1.0


def test_without_a_minimum_case_the_entered_sk_is_used_and_noted():
    row = _one("dol")
    assert row["sk_case"] == "entered" and row["c"] == 1.1
    assert "the PoC has no minimum short-circuit case: the entered S_k'' 150 MVA / c_max 1.1 is used" in row["note"]


@pytest.mark.parametrize("kw, method, missing", [
    (None, None, "Motor rating and Start method"), (2000.0, None, "Start method"), (None, "dol", "Motor rating"),
    (float("nan"), "", "Motor rating and Start method")])
def test_a_unit_without_motor_fields_is_unknown_never_pass(kw, method, missing):
    row = _one(method, kw=kw)
    assert row["verdict"] == "unknown" and row["du_pct"] is None
    assert row["note"] == f"no input: {missing} not set"


def test_no_short_circuit_level_is_unknown():
    row = _one("dol", sources={})
    assert row["verdict"] == "unknown" and row["note"] == ms.NO_SK


def test_no_limit_is_unknown_with_the_value_shown():
    row = _one("dol", limit=None)
    assert row["verdict"] == "unknown" and row["du_pct"] == pytest.approx(DOL_DU, rel=1e-6)


@pytest.mark.parametrize("kw, method, k, match", [
    (-1.0, "dol", None, "Motor rating must be 0 or more"), (2000.0, "star-delta", None, "Start method"),
    (2000.0, "dol", -2.0, "Start current multiple must be 0 or more")])
def test_malformed_inputs_are_refused(kw, method, k, match):
    with pytest.raises(ContractError, match=match):
        _one(method, kw=kw, k=k)


def test_duplicate_units_are_refused():
    with pytest.raises(ContractError, match="duplicate"):
        ms.motor_start_table(_units(("M", "PoC", 1.0, "dol", None), ("M", "PoC", 1.0, "dol", None)), POC, None, 3.0)


def test_the_limit_reads_rvc_limit_pct_and_a_demo_value_says_planning_level():
    value, clause = ms.limit_from_profile({"rvc_limit_pct": {"value": 3.0, "clause": "3 % used", "source": "assumed"}})
    assert value == 3.0 and clause == f"3 % used; {ms.PLANNING_LEVEL}"
    assert "planning level, not an emission limit allocated to this installation" in clause
    value, clause = ms.limit_from_profile({"rvc_limit_pct": {"value": 5.0, "clause": "Code 4.2", "source": "code"}})
    assert (value, clause) == (5.0, "Code 4.2")
    assert ms.limit_from_profile({}) == (None, None)


def test_the_start_methods_match_the_gui():
    assert ms.START_METHODS == tuple(ms.START_MULTIPLE) == tuple(ms.START_PF)
    assert math.isclose(ms.START_MULTIPLE["dol"], 6.0)

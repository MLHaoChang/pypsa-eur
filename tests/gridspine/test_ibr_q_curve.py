"""A per-unit reactive capability curve (DC 15, G9): ``q_capability_unit``.

Each IBR unit's inverter circle, sqrt(S^2 - P^2) at P = p_pu x P_n, is
compared on both sides against the curve's band at the curve's breakpoints
within 0-1 pu and at rated P. The fixture's BESS1 has P_n 20 MW; at S 21 MVA
it has 6.40 Mvar (0.320 pu) at rated P and 18.47 Mvar (0.923 pu) at half
load. A unit without a rating is reported as unknown, never passed. The
numbers are generic test values.
"""
import math

import pytest

from gridspine.static.campus_reactive import IBR_CURVE_LABEL, ibr_q_capability, ibr_q_curve
from gridspine.templates.grid_codes import load_grid_code, validate_profile
from tests.gridspine.test_ibr_q_capability import campus

BASE = load_grid_code("generic_assumed", raw=True)


def with_curve(points, **extra):
    return validate_profile("p", {**BASE, **extra, "q_capability_unit": {
        "points": points, "clause": "generic test clause", "source": "code"}})


#: Fails at half load (0.95 pu needed, 0.923 available), passes at rated P.
PART_LOAD = [[0.0, -0.5, 0.5], [0.5, -0.95, 0.95], [1.0, -0.3, 0.3]]


def rows(df, unit):
    return {r["p_pu"]: r for r in df.to_dict("records") if r["unit"] == unit}


def test_a_unit_can_fail_the_curve_at_part_load_and_pass_at_rated_p():
    got = rows(ibr_q_curve(campus(bess_s=21.0), with_curve(PART_LOAD)), "BESS1")
    assert sorted(got) == [0.0, 0.5, 1.0]
    half, rated = got[0.5], got[1.0]
    assert half["p_mw"] == pytest.approx(10.0)
    assert half["available_mvar"] == pytest.approx(math.sqrt(21.0 ** 2 - 10.0 ** 2))
    assert half["q_max_req_mvar"] == pytest.approx(19.0) and half["q_min_req_mvar"] == pytest.approx(-19.0)
    assert half["available_mvar"] == pytest.approx(18.466, abs=1e-3)
    assert half["status"] == "fail" and half["shortfall_mvar"] == pytest.approx(19.0 - 18.466, abs=1e-3)
    assert "deliver" in half["detail"] and "absorb" in half["detail"]          # both sides fall short
    assert rated["status"] == "pass" and rated["shortfall_mvar"] == 0.0
    assert rated["available_mvar"] == pytest.approx(math.sqrt(21.0 ** 2 - 20.0 ** 2))
    assert got[0.0]["status"] == "pass"
    assert {r["status"] for r in rows(ibr_q_curve(campus(bess_s=21.0), with_curve(PART_LOAD)), "PV1").values()} \
        == {"pass"}


def test_both_sides_of_the_band_are_checked():
    # Delivering 0.1 pu at rated P is within reach; absorbing 0.4 pu (8 Mvar) is not.
    got = rows(ibr_q_curve(campus(bess_s=21.0), with_curve([[0.0, -0.1, 0.5], [1.0, -0.4, 0.1]])), "BESS1")
    assert got[1.0]["status"] == "fail" and "deliver" not in got[1.0]["detail"]
    assert got[1.0]["shortfall_mvar"] == pytest.approx(8.0 - math.sqrt(21.0 ** 2 - 20.0 ** 2))
    assert "absorb" in got[1.0]["detail"]


def test_at_part_load_each_side_has_its_own_shortfall():
    # At 0.5 pu BESS1 has 18.466 Mvar either way: delivering 1.0 pu (20 Mvar) is 1.53 short,
    # absorbing 0.95 pu (19 Mvar) is 0.53 short; the row's shortfall is the larger.
    got = rows(ibr_q_curve(campus(bess_s=21.0), with_curve([[0.0, -0.5, 0.5], [0.5, -0.95, 1.0],
                                                             [1.0, -0.3, 0.3]])), "BESS1")
    half = got[0.5]
    assert half["status"] == "fail" and half["shortfall_mvar"] == pytest.approx(20.0 - 18.466, abs=1e-3)
    assert "1.53 Mvar short to deliver and 0.53 Mvar short to absorb" in half["detail"]
    assert got[1.0]["status"] == "pass"


def test_only_breakpoints_within_0_to_1_pu_and_rated_p_inside_the_curve_are_checked():
    got = rows(ibr_q_curve(campus(), with_curve([[0.2, -0.1, 0.1], [0.8, -0.2, 0.2], [1.2, -0.3, 0.3]])), "PV1")
    assert sorted(got) == [0.2, 0.8, 1.0]
    got = rows(ibr_q_curve(campus(), with_curve([[0.2, -0.1, 0.1], [0.8, -0.2, 0.2]])), "PV1")
    assert sorted(got) == [0.2, 0.8, 1.0]                              # rated P is never skipped
    assert got[1.0]["status"] == "reported" and "does not state rated P" in got[1.0]["detail"]


def test_a_curve_ending_below_rated_p_reports_rated_p_never_passes_it():
    got = rows(ibr_q_curve(campus(), with_curve([[0.0, -0.1, 0.1], [0.9, -0.2, 0.2]])), "BESS1")
    assert sorted(got) == [0.0, 0.9, 1.0]
    assert got[0.9]["status"] == "pass"
    assert got[1.0]["status"] == "reported" and math.isnan(got[1.0]["shortfall_mvar"])
    assert "does not state rated P" in got[1.0]["detail"]


def test_points_below_zero_pu_are_outside_the_first_cut():
    got = rows(ibr_q_curve(campus(), with_curve([[-1.0, -0.9, 0.9], [0.0, -0.1, 0.1], [1.0, -0.2, 0.2]])), "BESS1")
    assert sorted(got) == [0.0, 1.0]


@pytest.mark.parametrize("missing", [float("nan"), None])
def test_a_unit_without_a_rating_is_unknown_never_passed(missing):
    c = campus()
    c.units["s_mva"] = c.units["s_mva"].astype(object)      # so a None stays None
    c.units.loc["PV1", "s_mva"] = missing
    df = ibr_q_curve(c, with_curve(PART_LOAD))
    pv = [r for r in df.to_dict("records") if r["unit"] == "PV1"]
    assert len(pv) == 1 and pv[0]["status"] == "reported" and "unknown" in pv[0]["detail"]
    assert math.isnan(pv[0]["available_mvar"])
    assert {r["status"] for r in df.to_dict("records") if r["unit"] == "BESS1"} <= {"pass", "fail"}


def test_no_curve_no_table_and_q_range_ibr_stays_its_own_table():
    assert ibr_q_curve(campus(), validate_profile("p", BASE)) is None
    p = with_curve(PART_LOAD, q_range_ibr={"value": 0.3287, "reference": "P_n", "clause": "generic",
                                           "source": "code"})
    curve, single = ibr_q_curve(campus(), p), ibr_q_capability(campus(), p)
    assert "p_pu" in curve.columns and "TOTAL" not in set(curve["unit"])
    assert "TOTAL" in set(single["unit"])
    assert (curve["basis"] == IBR_CURVE_LABEL).all() and not (single["basis"] == IBR_CURVE_LABEL).any()
    assert set(curve["clause"]) == {"generic test clause"}

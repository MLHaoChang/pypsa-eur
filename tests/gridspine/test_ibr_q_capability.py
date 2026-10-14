"""IEEE 2800's reactive capability of the on-site IBR (E2E demo plan
2026-10-14 §7.3; gatekeeper's conditions §7.4).

A capability at rated active power, screened at the unit terminals:
required = q_range_ibr x P_n, available = sqrt(S^2 - P_n^2). Closed forms:
S = P_n has no headroom (the whole requirement is the shortfall); S =
1.053 P_n (above P_n sqrt(1 + 0.3287^2)) passes. A unit without a rating is
reported, never passed. A profile without q_range_ibr adds no table and no
connection-table row; the connection table gains a reported ``q_ibr`` row
only for a profile that has it.
"""
import math

import pytest

from gridspine.ingest.campus import build_campus
from gridspine.static.campus_reactive import IBR_LABEL, ibr_q_capability
from gridspine.static.connection import Facility, assess_connection
from gridspine.static.contingency_set import branch_contingencies
from gridspine.templates.grid_codes import load_grid_code
from tests.gridspine.test_campus import campus_spec, t
from tests.gridspine.test_connection import _net

US = load_grid_code("us_ieee2800_nerc")
EU = load_grid_code("eu_rfg_dcc_ce")
Q = 0.3287


def campus(bess_s=22.0, pv_s=16.5):
    s = campus_spec()
    s["campus"]["units"]["BESS1"]["s_mva"] = t(bess_s)
    s["campus"]["units"]["PV1"]["s_mva"] = t(pv_s)
    return build_campus(s)


def by_unit(df):
    return {r["unit"]: r for r in df.to_dict("records")}


def test_the_fixture_passes_and_the_total_compares_the_sums():
    got = by_unit(ibr_q_capability(campus(), US))
    assert set(got) == {"BESS1", "PV1", "TOTAL"}                      # gensets and loads are not IBR
    b = got["BESS1"]
    assert b["required_mvar"] == pytest.approx(Q * 20.0)
    assert b["available_mvar"] == pytest.approx(math.sqrt(22.0 ** 2 - 20.0 ** 2))
    assert b["status"] == "pass" and b["shortfall_mvar"] == 0.0
    tot = got["TOTAL"]
    assert tot["required_mvar"] == pytest.approx(Q * 35.0)
    assert tot["available_mvar"] == pytest.approx(b["available_mvar"] + got["PV1"]["available_mvar"])
    assert tot["status"] == "pass" and tot["source"] == "code" and "IEEE 2800" in tot["clause"]


def test_an_inverter_rated_at_p_n_has_the_whole_requirement_as_shortfall():
    got = by_unit(ibr_q_capability(campus(bess_s=20.0), US))
    b = got["BESS1"]
    assert b["available_mvar"] == 0.0 and b["shortfall_mvar"] == pytest.approx(Q * 20.0)
    assert b["status"] == "fail" and got["TOTAL"]["status"] == "fail"
    assert "1.053" in b["detail"] and "21.05" in b["detail"]        # S >= P_n sqrt(1 + 0.3287^2)
    assert "STATCOM" in b["detail"]


def test_an_inverter_at_1_053_p_n_passes():
    got = by_unit(ibr_q_capability(campus(bess_s=1.053 * 20.0), US))
    assert got["BESS1"]["status"] == "pass"


def test_a_unit_without_a_rating_is_reported_never_passed():
    c = campus()
    c.units.loc["PV1", "s_mva"] = float("nan")
    got = by_unit(ibr_q_capability(c, US))
    assert got["PV1"]["status"] == "reported" and "rating not given" in got["PV1"]["detail"]
    assert got["TOTAL"]["status"] == "reported"


def test_a_profile_without_the_capability_adds_nothing():
    assert ibr_q_capability(campus(), EU) is None
    assert "unit terminals" in IBR_LABEL


def test_the_connection_table_reports_q_ibr_only_for_a_profile_that_has_it():
    net = _net()
    fac = Facility(bus="BUS_02", load_mw=40.0, onsite_mw=10.0)
    us = assess_connection(net, fac, branch_contingencies(net), US)
    row = us[us["check"] == "q_ibr"].iloc[0]
    assert row["status"] == "reported" and row["source"] == "code"
    assert "not checked here" in row["detail"] and "campus study" in row["detail"]
    for profile in (EU, load_grid_code("generic_assumed")):
        out = assess_connection(net, fac, branch_contingencies(net), profile)
        assert "q_ibr" not in set(out["check"])

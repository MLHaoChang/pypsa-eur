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


@pytest.mark.parametrize("missing", [float("nan"), None])
def test_a_unit_without_a_rating_is_reported_never_passed(missing):
    c = campus()
    c.units["s_mva"] = c.units["s_mva"].astype(object)      # so a None stays None
    c.units.loc["PV1", "s_mva"] = missing
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


def test_the_total_is_the_plant_comparison_and_names_a_unit_short_of_its_own_share():
    """Review probe: BESS 20 MW at S = 30 MVA has 22.36 Mvar, PV 15 MW at S =
    15 MVA none; the plant needs 11.50 Mvar and has it. IEEE 2800 states the
    requirement for the plant, so the TOTAL passes; the PV row still fails."""
    got = by_unit(ibr_q_capability(campus(bess_s=30.0, pv_s=15.0), US))
    assert got["PV1"]["status"] == "fail" and got["PV1"]["shortfall_mvar"] == pytest.approx(Q * 15.0)
    assert got["BESS1"]["status"] == "pass"
    tot = got["TOTAL"]
    assert tot["status"] == "pass" and tot["shortfall_mvar"] == 0.0
    assert tot["available_mvar"] == pytest.approx(math.sqrt(30.0 ** 2 - 20.0 ** 2))
    assert tot["required_mvar"] == pytest.approx(Q * 35.0)
    assert "plant meets" in tot["detail"] and "PV1 alone falls 4.93 Mvar short" in tot["detail"]


def test_with_no_rated_unit_the_total_is_unknown_not_zero():
    c = campus()
    c.units.loc[["BESS1", "PV1"], "s_mva"] = float("nan")
    tot = by_unit(ibr_q_capability(c, US))["TOTAL"]
    assert tot["status"] == "reported"
    for col in ("required_mvar", "available_mvar", "shortfall_mvar", "p_n_mw"):
        assert math.isnan(tot[col]), col


def test_the_total_counts_only_rated_units_in_p_n():
    c = campus()
    c.units.loc["PV1", "s_mva"] = float("nan")
    tot = by_unit(ibr_q_capability(c, US))["TOTAL"]
    assert tot["p_n_mw"] == pytest.approx(20.0)
    assert tot["required_mvar"] == pytest.approx(Q * tot["p_n_mw"])


def test_the_table_itself_says_it_is_screened_at_the_unit_terminals(tmp_path):
    import pandas as pd

    df = ibr_q_capability(campus(), US)
    assert "basis" in df.columns and (df["basis"] == IBR_LABEL).all()
    df.to_csv(tmp_path / "t.csv", index=False)
    back = pd.read_csv(tmp_path / "t.csv")
    assert back["basis"].str.startswith("screening at unit terminals").all()

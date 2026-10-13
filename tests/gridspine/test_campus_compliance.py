"""The PCC compliance report of a campus study (C5).

One row per check, each with:
* the status **as is**;
* the status **with the recommended measures**;
* the worst (period, hour), the value against the limit, the clause, and
  the ``code``/``assumed`` tag.

Statuses are ``pass``, ``fail``, ``not_rated`` (no rating to judge
against), and ``not_rechecked`` (a measure whose effect the study did not
re-solve).
"""
import pandas as pd
import pytest

from gridspine.static.campus_compliance import CHECKS, campus_compliance
from gridspine.templates.grid_codes import list_grid_codes, load_grid_code

PROFILE = load_grid_code("eu_rfg_dcc_ce")


def inputs(**over):
    bus = pd.DataFrame([
        {"period": 2030, "hour": 1, "case": "intact", "bus": "PCC", "vm_pu": 1.00},
        {"period": 2030, "hour": 1, "case": "intact", "bus": "MV1", "vm_pu": 0.97},
        {"period": 2030, "hour": 2, "case": "intact", "bus": "PCC", "vm_pu": 1.00},
        {"period": 2030, "hour": 2, "case": "intact", "bus": "MV1", "vm_pu": 0.95},
        {"period": 2030, "hour": 2, "case": "N-1:TR1", "bus": "MV1", "vm_pu": 0.89},
    ])
    trafo = pd.DataFrame([
        {"period": 2030, "hour": 1, "case": "intact", "trafo": "TR1", "s_mva": 30.0, "loading_pct": 75.0},
        {"period": 2030, "hour": 2, "case": "intact", "trafo": "TR1", "s_mva": 36.0, "loading_pct": 90.0},
        {"period": 2030, "hour": 2, "case": "N-1:TR1", "trafo": "TR2", "s_mva": 70.0, "loading_pct": 175.0},
    ])
    reactive = pd.DataFrame([
        {"period": 2030, "hour": 1, "converged": True, "q0_mvar": 10.0, "q_final_mvar": 10.0,
         "q_inverters_mvar": 0.0, "q_comp_mvar": 0.0, "compliant_without": True},
        {"period": 2030, "hour": 2, "converged": True, "q0_mvar": 25.0, "q_final_mvar": 19.2,
         "q_inverters_mvar": 3.0, "q_comp_mvar": 2.8, "compliant_without": False},
    ])
    sizing = pd.DataFrame([{"group": "TR1+TR2", "units": 2, "unit_rating_mva": 40.0, "required_unit_mva": 84.0,
                            "recommended_unit_mva": 100.0, "adequate": False, "worst_period": 2030,
                            "worst_hour": 2, "n_minus_1": True}])
    comp = pd.DataFrame([
        {"direction": "capacitive", "required_mvar": 2.8, "recommended_mvar": 4.0, "worst_period": 2030, "worst_hour": 2},
        {"direction": "inductive", "required_mvar": 0.0, "recommended_mvar": 0.0, "worst_period": None, "worst_hour": None},
    ])
    sc = pd.DataFrame([
        {"period": 2030, "bus": "PCC", "vn_kv": 110.0, "ikss_max_ka": 15.0, "ip_max_ka": 38.0, "rated_ka": 31.5,
         "adequate": True, "detail": "ok"},
        {"period": 2030, "bus": "MV1", "vn_kv": 20.0, "ikss_max_ka": 12.0, "ip_max_ka": 30.0, "rated_ka": float("nan"),
         "adequate": None, "detail": "no switchgear rating given"},
    ])
    req = {"q_limit_mvar": 19.2, "clause": "DCC Art. 15(1)(a): ...", "source": "code"}
    base = dict(bus=bus, trafo=trafo, reactive=reactive, sizing=sizing, compensation=comp, short_circuit=sc,
                requirement=req, profile=PROFILE, pcc_bus="PCC", bus_kv={"PCC": 110.0, "MV1": 20.0})
    base.update(over)
    return base


def report(**over):
    return campus_compliance(**inputs(**over)).set_index("check")


def test_every_check_is_reported_once():
    r = report()
    assert list(r.index) == list(CHECKS)


def test_the_pcc_reactive_band_fails_as_is_and_passes_with_the_compensation():
    row = report().loc["pcc_reactive"]
    assert row["status_as_is"] == "fail" and row["status_with_measures"] == "pass"
    assert row["value"] == 25.0 and row["limit"] == 19.2 and row["unit"] == "Mvar"
    assert (row["worst_period"], row["worst_hour"]) == (2030, 2)
    assert row["source"] == "code" and row["clause"].startswith("DCC Art. 15(1)(a)")
    assert "4" in row["detail"] and "capacitive" in row["detail"]


def test_a_correction_that_leaves_the_pcc_outside_the_band_fails_with_measures_too():
    reactive = inputs()["reactive"].assign(q_final_mvar=[10.0, 23.0])
    row = report(reactive=reactive).loc["pcc_reactive"]
    assert row["status_as_is"] == "fail" and row["status_with_measures"] == "fail"


def test_a_campus_inside_the_band_passes_both_ways():
    reactive = inputs()["reactive"].assign(q0_mvar=[10.0, 12.0], compliant_without=[True, True])
    row = report(reactive=reactive).loc["pcc_reactive"]
    assert row["status_as_is"] == row["status_with_measures"] == "pass"


def test_pcc_voltage_is_held_to_the_code_band_for_its_voltage():
    row = report().loc["pcc_voltage"]
    assert row["status_as_is"] == "pass" and row["source"] == "code"
    assert "DCC Annex II" in row["clause"]
    bus = inputs()["bus"]
    bus.loc[bus["bus"] == "PCC", "vm_pu"] = 1.13                  # above 1.118 at 110 kV
    row = report(bus=bus).loc["pcc_voltage"]
    assert row["status_as_is"] == "fail" and row["value"] == 1.13 and row["limit"] == 1.118


def test_voltages_inside_the_campus_are_a_design_limit_and_the_worst_case_is_found():
    row = report().loc["campus_voltage"]
    assert row["status_as_is"] == "fail" and row["value"] == 0.89      # MV1 under N-1
    assert row["source"] == "assumed" and "design" in row["detail"]
    assert row["status_with_measures"] == "not_rechecked"


def test_an_internal_bus_at_a_code_voltage_is_still_held_to_a_design_limit():
    bus = pd.DataFrame([
        {"period": 2030, "hour": 1, "case": "intact", "bus": "PCC", "vm_pu": 1.0},
        {"period": 2030, "hour": 1, "case": "intact", "bus": "HV2", "vm_pu": 1.0},
    ])
    row = report(bus=bus, bus_kv={"PCC": 110.0, "HV2": 132.0}).loc["campus_voltage"]
    assert row["source"] == "assumed" and "DCC Annex II" in row["clause"]


def test_transformers_fail_as_is_and_pass_at_the_recommended_rating():
    row = report().loc["transformer_loading"]
    assert row["status_as_is"] == "fail" and row["status_with_measures"] == "pass"
    assert row["value"] == 175.0 and row["limit"] == 100.0
    assert "100" in row["detail"] and "TR1+TR2" in row["detail"]


def test_switchgear_with_a_rating_is_judged_and_without_one_is_not_rated():
    row = report().loc["switchgear"]
    assert row["status_as_is"] == "not_rated"                         # MV1 has no rating
    sc = inputs()["short_circuit"].assign(rated_ka=[31.5, 25.0], adequate=[True, True])
    assert report(short_circuit=sc).loc["switchgear", "status_as_is"] == "pass"
    sc = sc.assign(adequate=[True, False], detail=["ok", "Ik'' 12.00 kA above the rated 25 kA"])
    row = report(short_circuit=sc).loc["switchgear"]
    assert row["status_as_is"] == "fail" and "MV1" in row["detail"]
    # the same judgement read back from CSV (strings) gives the same answer
    assert report(short_circuit=sc.assign(adequate=["True", "False"])).loc["switchgear", "status_as_is"] == "fail"


def test_the_profiles_a_study_can_choose_from_are_listed_with_titles():
    codes = list_grid_codes()
    assert "eu_rfg_dcc_ce" in codes and codes["eu_rfg_dcc_ce"].startswith("EU RfG")


def _mv_bus(vm):
    return pd.DataFrame([
        {"period": 2030, "hour": 1, "case": "intact", "bus": "PCC", "vm_pu": 1.0},
        {"period": 2030, "hour": 1, "case": "intact", "bus": "MV1", "vm_pu": vm},
    ])


def test_a_profile_campus_voltage_band_replaces_the_code_bands_inside_the_campus():
    # 0.97 pu at 20 kV: inside the +/-10 % band of eu_rfg_dcc_ce ...
    assert report(bus=_mv_bus(0.97)).loc["campus_voltage", "status_as_is"] == "pass"
    # ... and 0.94 pu fails the generic profile's 0.95-1.05 design band, though it is inside 0.90-1.10
    generic = load_grid_code("generic_assumed")
    row = report(bus=_mv_bus(0.94), profile=generic).loc["campus_voltage"]
    assert row["status_as_is"] == "fail" and row["value"] == 0.94 and row["limit"] == 0.95
    assert row["source"] == "assumed" and "generic placeholder" in row["clause"]
    assert "0.95-1.05" in row["detail"]
    # 1.06 fails on the high side
    row = report(bus=_mv_bus(1.06), profile=generic).loc["campus_voltage"]
    assert row["status_as_is"] == "fail" and row["limit"] == 1.05
    # the same 0.97 pu passes under it
    assert report(bus=_mv_bus(0.97), profile=generic).loc["campus_voltage", "status_as_is"] == "pass"


def test_the_pcc_voltage_still_uses_the_voltage_bands_when_campus_voltage_is_given():
    generic = load_grid_code("generic_assumed")
    bus = _mv_bus(1.0)
    bus.loc[bus["bus"] == "PCC", "vm_pu"] = 0.92          # outside 0.95, inside the 0.90 PCC band
    row = report(bus=bus, profile=generic).loc["pcc_voltage"]
    assert row["status_as_is"] == "pass" and row["limit"] == 0.90


def test_without_campus_voltage_the_code_bands_apply_as_before():
    assert "campus_voltage" not in PROFILE
    row = report(bus=_mv_bus(0.94)).loc["campus_voltage"]
    assert row["status_as_is"] == "pass" and row["source"] == "assumed" and "design" in row["detail"]


def test_the_campus_band_carries_its_own_tag_not_a_fixed_one():
    profile = load_grid_code("generic_assumed")
    profile["campus_voltage"]["source"] = "code"
    assert report(bus=_mv_bus(0.94), profile=profile).loc["campus_voltage", "source"] == "code"


def test_every_row_names_the_case_of_its_worst_reading():
    """The worst reading's case beside its (period, hour), read from the
    inputs above by hand: MV1 at 0.89 pu and TR2 at 175 % are both in the
    outage of TR1 at hour 2; the PCC's 1.00 pu first appears intact at hour 1.
    The reactive exchange is one figure per hour and the fault level one per
    period, so neither has a case."""
    r = report()
    assert (r.at["campus_voltage", "worst_hour"], r.at["campus_voltage", "worst_case"]) == (2, "N-1:TR1")
    assert (r.at["transformer_loading", "worst_hour"], r.at["transformer_loading", "worst_case"]) == (2, "N-1:TR1")
    assert (r.at["pcc_voltage", "worst_hour"], r.at["pcc_voltage", "worst_case"]) == (1, "intact")
    assert r.at["pcc_reactive", "worst_case"] is None and r.at["switchgear", "worst_case"] is None
    assert list(campus_compliance(**inputs()).columns) == [
        "check", "status_as_is", "status_with_measures", "value", "limit", "unit", "worst_period", "worst_hour",
        "clause", "source", "detail", "worst_case", "limit_min", "limit_max"]   # the new columns last


def test_a_pass_that_relies_on_the_solver_tolerance_says_so():
    """The browser run: 13.157 Mvar against a 13.147 Mvar band is 0.010 Mvar
    over, inside the 0.011 Mvar the band is judged with (the solver's)."""
    reactive = inputs()["reactive"].assign(q0_mvar=[10.0, 13.157], q_final_mvar=[10.0, 13.157],
                                           compliant_without=[True, True])
    req = {**inputs()["requirement"], "q_limit_mvar": 13.147}
    row = report(reactive=reactive, requirement=req).loc["pcc_reactive"]
    assert row["status_as_is"] == row["status_with_measures"] == "pass"
    assert row["detail"].endswith(
        "; passes within the solver tolerance of 0.011 Mvar: 13.157 Mvar against the 13.147 Mvar band")


@pytest.mark.parametrize("q_final, status", [(13.0, "pass"), (13.2, "fail")])
def test_a_pass_inside_the_band_or_a_fail_says_nothing_of_the_tolerance(q_final, status):
    reactive = inputs()["reactive"].assign(q0_mvar=[10.0, 13.5], q_final_mvar=[10.0, q_final],
                                           compliant_without=[True, False])
    req = {**inputs()["requirement"], "q_limit_mvar": 13.147}
    row = report(reactive=reactive, requirement=req).loc["pcc_reactive"]
    assert (row["status_as_is"], row["status_with_measures"]) == ("fail", status)
    assert "tolerance" not in row["detail"]


def test_a_voltage_row_carries_its_whole_band():
    """The limit is the band edge the worst reading is judged against; the
    band itself is carried too (EU profile: 0.90-1.118 pu at 110 kV,
    0.90-1.10 pu below, restated). Rows without a band carry none."""
    r = report()
    assert (r.at["pcc_voltage", "limit_min"], r.at["pcc_voltage", "limit_max"]) == (0.90, 1.118)
    assert (r.at["campus_voltage", "limit_min"], r.at["campus_voltage", "limit_max"]) == (0.90, 1.10)
    for check in ("pcc_reactive", "transformer_loading", "switchgear"):
        assert pd.isna(r.at[check, "limit_min"]) and pd.isna(r.at[check, "limit_max"])


@pytest.mark.parametrize("q0, ok_as_is, q_final, q_said", [
    (13.5, False, 13.157, "13.157"),      # only the corrected exchange is over the band: the with-measures pass
    (13.155, True, 13.0, "13.155"),       # only the as-is exchange is over it: the as-is pass
])
def test_the_tolerance_is_said_for_whichever_pass_relies_on_it(q0, ok_as_is, q_final, q_said):
    reactive = inputs()["reactive"].assign(q0_mvar=[10.0, q0], q_final_mvar=[10.0, q_final],
                                           compliant_without=[True, ok_as_is])
    req = {**inputs()["requirement"], "q_limit_mvar": 13.147}
    row = report(reactive=reactive, requirement=req).loc["pcc_reactive"]
    assert row["status_with_measures"] == "pass" and row["status_as_is"] == ("pass" if ok_as_is else "fail")
    assert row["detail"].endswith(f"tolerance of 0.011 Mvar: {q_said} Mvar against the 13.147 Mvar band")

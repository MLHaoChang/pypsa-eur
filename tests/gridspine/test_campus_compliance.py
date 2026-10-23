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


TIE_LABEL = "applies with the tie closed (grid-connected operation), not to islanded operation"


def _tie_inputs(tie_pct, inner_pct, inner_adequate=True):
    trafo = pd.DataFrame([
        {"period": 2030, "hour": 1, "case": "intact", "trafo": "TIE", "s_mva": 1.3, "loading_pct": tie_pct},
        {"period": 2030, "hour": 1, "case": "intact", "trafo": "INNER", "s_mva": 4.3, "loading_pct": inner_pct},
    ])
    sizing = pd.DataFrame([{"group": "INNER", "units": 1, "unit_rating_mva": 2.5, "required_unit_mva": 5.2,
                            "recommended_unit_mva": 6.3, "adequate": inner_adequate, "worst_period": 2030,
                            "worst_hour": 1, "n_minus_1": False}])
    req = {**inputs()["requirement"], "tie_open": True}
    return dict(trafo=trafo, sizing=sizing, requirement=req, held={"TIE"})


def test_an_internal_overload_behind_an_open_tie_is_not_labelled_as_a_tie_closed_check():
    row = report(**_tie_inputs(tie_pct=5.0, inner_pct=173.0, inner_adequate=False)).loc["transformer_loading"]
    assert row["status_as_is"] == "fail" and row["status_with_measures"] == "pass"   # INNER is resized
    assert TIE_LABEL not in row["clause"]
    assert "INNER: 1 x 6.3 MVA recommended" in row["detail"] and "not sized from islanded hours" in row["detail"]


def test_an_overloaded_open_tie_fails_with_measures_too():
    row = report(**_tie_inputs(tie_pct=120.0, inner_pct=50.0)).loc["transformer_loading"]
    assert row["status_as_is"] == "fail" and row["status_with_measures"] == "fail"   # nothing resizes the tie
    assert TIE_LABEL in row["clause"] and "no measure resizes it" in row["detail"]


# --------------------------------------------------------------------------
# P-dependent Q band and asymmetric power factor at the PCC (DC 10a)
# --------------------------------------------------------------------------
# Signs: pcc p_mw > 0 is import (res_ext_grid); Q > 0 is the grid supplying
# reactive power into the site, a lagging power factor. Generic test values.

P_REF = 100.0


def _pcc(p1=50.0, p2=80.0):
    return pd.DataFrame([
        {"period": 2030, "hour": 1, "case": "intact", "converged": True, "p_mw": p1, "q_mvar": 0.0},
        {"period": 2030, "hour": 2, "case": "intact", "converged": True, "p_mw": p2, "q_mvar": 0.0},
        {"period": 2030, "hour": 2, "case": "N-1:TR1", "converged": True, "p_mw": 999.0, "q_mvar": 0.0},
    ])


def _lim(**kw):
    return {"clause": "generic test clause", "source": "code", **kw}


def _with(**limits):
    return {**PROFILE, **limits}


def sagc(profile=None, pcc=None, p_ref=P_REF, tie_open=False, **over):
    req = {**inputs()["requirement"], **({"p_ref_mw": p_ref} if p_ref is not None else {}),
           **({"tie_open": True} if tie_open else {})}
    return report(profile=profile or PROFILE, pcc=_pcc() if pcc is None else pcc, requirement=req, **over)


BAND = _lim(points=[[0.0, -0.1, 0.1], [1.0, -0.2, 0.2]])     # +-18 Mvar at 0.8 pu


@pytest.mark.parametrize("profile, pcc, p_ref, why", [
    (PROFILE, None, P_REF, "no limit set"),
    (_with(q_capability_pq=BAND, pf_min_lagging=_lim(value=0.9)), None, None, "p_ref"),
    (_with(q_capability_pq=BAND, pf_min_lagging=_lim(value=0.9)), pd.DataFrame(), P_REF, "no PCC active power"),
])
def test_an_unset_limit_or_unknown_base_is_unknown_never_pass_or_fail(profile, pcc, p_ref, why):
    r = sagc(profile=profile, pcc=pcc, p_ref=p_ref)
    for check in ("pcc_q_band", "pcc_power_factor"):
        row = r.loc[check]
        assert row["status_as_is"] == row["status_with_measures"] == "unknown", check
        assert why in row["detail"] and pd.isna(row["value"])


def test_the_existing_call_without_pcc_still_reports_every_check():
    r = report()
    assert list(r.index) == list(CHECKS)
    assert set(r.loc[["pcc_q_band", "pcc_power_factor"], "status_as_is"]) == {"unknown"}


def test_the_q_band_is_interpolated_at_the_intact_pcc_p():
    row = sagc(profile=_with(q_capability_pq=BAND)).loc["pcc_q_band"]
    # hour 2: P 80 MW = 0.8 pu, band +-0.18 pu = +-18 Mvar; Q 25 as is, 19.2 with measures
    assert row["status_as_is"] == "fail" and row["status_with_measures"] == "fail"
    assert row["value"] == 25.0 and row["limit"] == pytest.approx(18.0) and row["unit"] == "Mvar"
    assert (row["limit_min"], row["limit_max"]) == (pytest.approx(-18.0), pytest.approx(18.0))
    assert (row["worst_period"], row["worst_hour"]) == (2030, 2) and row["clause"] == "generic test clause"
    wide = _lim(points=[[0.0, -0.1, 0.1], [1.0, -0.3, 0.3]])  # +-26 Mvar at 0.8 pu
    assert sagc(profile=_with(q_capability_pq=wide)).loc["pcc_q_band", "status_as_is"] == "pass"


def test_a_p_outside_the_band_fails_naming_the_hour_and_p():
    narrow = _lim(points=[[0.0, -0.3, 0.3], [0.6, -0.3, 0.3]])
    row = sagc(profile=_with(q_capability_pq=narrow)).loc["pcc_q_band"]
    assert row["status_as_is"] == row["status_with_measures"] == "fail"
    assert "80.00 MW" in row["detail"] and "(2030, 2)" in row["detail"] and "outside" in row["detail"]


def test_a_p_outside_the_band_still_reports_the_worst_q_excursion_inside_it():
    # hour 1: P 50 MW inside the P range, Q 10 Mvar against +-5 Mvar; hour 2: P 80 MW outside it
    narrow = _lim(points=[[0.0, -0.05, 0.05], [0.6, -0.05, 0.05]])
    row = sagc(profile=_with(q_capability_pq=narrow)).loc["pcc_q_band"]
    assert row["status_as_is"] == "fail" and "(2030, 2)" in row["detail"]
    assert "worst inside the P range: Q 10.00 Mvar at (2030, 1), band -5.00 to 5.00 Mvar" in row["detail"]
    wide = _lim(points=[[0.0, -0.3, 0.3], [0.6, -0.3, 0.3]])
    assert "worst inside" not in sagc(profile=_with(q_capability_pq=wide)).loc["pcc_q_band", "detail"]


def test_an_exporting_hour_reads_the_band_below_zero_p():
    band = _lim(points=[[-1.0, -0.1, 0.1], [0.0, -0.3, 0.3], [1.0, -0.3, 0.3]])
    row = sagc(profile=_with(q_capability_pq=band), pcc=_pcc(p1=-50.0, p2=-80.0)).loc["pcc_q_band"]
    assert row["status_as_is"] == "fail" and row["limit"] == pytest.approx(14.0)   # -0.8 pu: +-0.14 pu


def test_a_lagging_hour_is_held_to_pf_min_lagging():
    # hour 2: P 80, Q +25 (grid supplies Q, lagging): pf 0.9545
    ok = sagc(profile=_with(pf_min_lagging=_lim(value=0.95), pf_min_leading=_lim(value=0.99))).loc["pcc_power_factor"]
    assert ok["status_as_is"] == "pass" and ok["value"] == pytest.approx(80 / (80 ** 2 + 25 ** 2) ** 0.5)
    assert ok["limit"] == 0.95 and "lagging" in ok["detail"]
    bad = sagc(profile=_with(pf_min_lagging=_lim(value=0.96))).loc["pcc_power_factor"]
    assert bad["status_as_is"] == "fail" and (bad["worst_period"], bad["worst_hour"]) == (2030, 2)
    assert bad["status_with_measures"] == "pass"           # Q 19.2 with measures: pf 0.972
    assert "no leading limit set" in bad["detail"]


def test_a_leading_hour_is_held_to_pf_min_leading_only():
    reactive = inputs()["reactive"].assign(q0_mvar=[-10.0, -25.0], q_final_mvar=[-10.0, -25.0])
    lagging_only = sagc(profile=_with(pf_min_lagging=_lim(value=0.99)), reactive=reactive).loc["pcc_power_factor"]
    # no hour on a side with a limit: nothing was judged, so unknown, never pass
    assert lagging_only["status_as_is"] == lagging_only["status_with_measures"] == "unknown"
    assert pd.isna(lagging_only["value"]) and "no leading limit set" in lagging_only["detail"]
    row = sagc(profile=_with(pf_min_lagging=_lim(value=0.8), pf_min_leading=_lim(value=0.96)),
               reactive=reactive).loc["pcc_power_factor"]
    assert row["status_as_is"] == "fail" and row["limit"] == 0.96 and "leading" in row["detail"]


def test_hours_near_zero_p_are_left_out_of_the_power_factor():
    profile = _with(pf_min_lagging=_lim(value=0.95))
    # hour 1: |P| 5 MW < 10 % of 100 MW with Q 10: pf 0.45, left out; hour 2 still checked
    row = sagc(profile=profile, pcc=_pcc(p1=5.0, p2=200.0)).loc["pcc_power_factor"]
    assert row["status_as_is"] == "pass" and row["worst_hour"] == 2
    assert "1 hour(s) with |P| below 10% of p_ref (10.00 MW) left out" in row["detail"]
    none = sagc(profile=profile, pcc=_pcc(p1=5.0, p2=-9.0)).loc["pcc_power_factor"]
    assert none["status_as_is"] == "unknown" and "2 hour(s)" in none["detail"]


def test_the_new_pcc_rows_carry_the_open_tie_clause():
    r = sagc(profile=_with(q_capability_pq=BAND), tie_open=True)
    assert TIE_LABEL in r.loc["pcc_q_band", "clause"]
    assert r.loc["pcc_power_factor", "clause"] == ""                   # unknown: no clause to qualify

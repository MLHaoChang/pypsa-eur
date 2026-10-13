"""
The SAM "Single Owner" oracle fixtures (IC P4 WP4.0): committed, pinned to
the generator and the profile, well-formed, and mapped to P4 units by the one
reviewed mapping (`sam_case.py`). The parity tests land in the WPs that can
reach each quantity (plan, "Fixtures and oracles").
"""
from __future__ import annotations

import hashlib

import pytest

from tests.fixtures.investment_case.sam import sam_case as S


@pytest.mark.parametrize("name", S.CASES)
def test_each_case_is_pinned_to_the_committed_generator_and_profile(name):
    prov = S.load_case(name)["provenance"]
    assert prov["pysam_version"] == "7.1.1.post1"
    assert prov["script_sha"] == hashlib.sha256(
        (S.HERE / "generate_sam_cases.py").read_bytes()).hexdigest()
    assert prov["profile_sha"] == hashlib.sha256((S.HERE / "gen_profile.csv").read_bytes()).hexdigest()


@pytest.mark.parametrize("name", S.CASES)
def test_each_case_has_every_compared_array_over_the_whole_axis(name):
    d = S.load_case(name)
    years = int(d["sam_inputs"]["FinancialParameters"]["analysis_period"])
    arrays = d["outputs"]["arrays"]
    for k in ("cf_energy_net", "cf_ppa_price", "cf_om_capacity_expense", "cf_om_fixed_expense",
              "cf_ebitda", "cf_cash_for_ds", "cf_feddepr_total", "cf_stadepr_total", "cf_fedtax",
              "cf_statax", "cf_debt_payment_interest", "cf_debt_payment_principal",
              "cf_debt_balance", "cf_reserve_debtservice", "cf_project_return_aftertax",
              "cf_project_return_pretax", "cf_itc_total", "cf_net_salvage_value"):
        assert len(arrays[k]) == years + 1, k
    assert set(d["outputs"]["scalars"]) >= {"project_return_aftertax_irr", "size_of_debt",
                                             "min_dscr", "ppa_price", "lcoe_nom"}


def test_the_profile_is_one_year_of_nonnegative_generation():
    p = S.load_profile()
    assert len(p) == 8760 and min(p) >= 0.0 and max(p) <= 100_000.0


@pytest.mark.parametrize("name", S.CASES)
def test_the_mapping_reads_every_case(name):
    p = S.sam_params(name)
    e = S.sam_expected(name)
    assert p.analysis_years == 25
    assert abs(p.nominal_discount_rate - e["scalars"]["equity_discount_rate"]) < 1e-12
    # Year-1 revenue = energy × price (no TOD factors, no degradation in year 1).
    rev1 = e["arrays"]["cf_energy_net"][1] * e["arrays"]["cf_ppa_price"][1]
    assert rev1 == pytest.approx(p.energy_year1_mwh * (e["scalars"]["ppa_price_per_mwh"]
                                                       if p.ppa_solve else p.ppa_price_per_mwh),
                                 rel=1e-9)
    # Year-1 O&M in COD-year money; the additive escalation in year 2.
    om = [c + f for c, f in zip(e["arrays"]["cf_om_capacity_expense"],
                                e["arrays"]["cf_om_fixed_expense"])]
    assert om[1] == pytest.approx(p.opex_year1, rel=1e-12)
    assert om[2] / om[1] == pytest.approx(1 + p.opex_escalation, rel=1e-12)


def test_the_mapping_refuses_what_p4_cannot_express(monkeypatch):
    real = S.load_case

    def patched(name):
        d = real(name)
        d["sam_inputs"]["SystemCosts"]["om_fixed_escal"] = 0.0      # unequal O&M escalations
        return d

    monkeypatch.setattr(S, "load_case", patched)
    with pytest.raises(S.SamMappingError, match="unequal O&M"):
        S.sam_params("s1")


def test_the_no_debt_dscr_sentinel_is_none_and_the_units_convert():
    e1, e2 = S.sam_expected("s1"), S.sam_expected("s2")
    assert e1["scalars"]["min_dscr"] is None and e2["scalars"]["min_dscr"] == pytest.approx(1.3)
    assert S.sam_expected("s1b")["scalars"]["ppa_price_per_mwh"] == pytest.approx(144.6559168, rel=1e-8)
    assert 0.0 < e2["scalars"]["equity_irr_post_tax"] < 1.0
    assert S.sam_params("s2").debt["option"] == "dscr" and S.sam_params("s1").debt == {}
    assert S.sam_params("s3").debt["grace_years"] == 1
    p3 = S.sam_params("s3")
    assert p3.itc_basis_reduction == {"federal": True, "state": False}
    assert S.sam_expected("s3f")["deviations"][0]["name"] == "gearing_with_fee"


def test_the_committed_profile_is_what_the_generator_writes():
    """The profile pin (WP4.0 review R7): `gen_profile()` — stdlib only, no
    PySAM — reproduces `gen_profile.csv` byte for byte."""
    from tests.fixtures.investment_case.sam import generate_sam_cases as G
    text = "hour,gen_kw\n" + "".join(f"{h},{v!r}\n" for h, v in enumerate(G.gen_profile()))
    assert text == (S.HERE / "gen_profile.csv").read_text()
    # The profile SAM ran on is the committed one (the export's `gen` sha).
    prof = S.load_profile()
    for name in S.CASES:
        gen = S.load_case(name)["sam_inputs"]["SystemOutput"]["gen"]
        assert gen == {"sha256_16": G._sha(prof), "len": 8760}, name


def test_itc_qualifying_classes_come_from_the_depr_itc_flags(monkeypatch):
    """SAM's `depr_itc_fed_<class>` flags set both the ITC base and the classes
    whose basis the ITC reduces (WP4.0 review B2)."""
    p3 = S.sam_params("s3")
    assert p3.itc_qualifying_classes == ["macrs_5"] and p3.itc_base_share == pytest.approx(1.0)
    assert S.sam_expected("s3")["scalars"]["itc_total"] == pytest.approx(
        p3.itc_federal_percent * p3.itc_base_share * p3.installed_cost, rel=1e-12)
    real = S.load_case

    def patched(name):
        d = real(name)
        dep = d["sam_inputs"]["Depreciation"]
        dep.update(depr_alloc_macrs_5_percent=70.0, depr_alloc_sl_20_percent=30.0,
                   depr_itc_fed_macrs_5=1.0, depr_itc_fed_sl_20=0.0)
        return d

    monkeypatch.setattr(S, "load_case", patched)
    p = S.sam_params("s3")
    assert p.itc_qualifying_classes == ["macrs_5"] and p.itc_base_share == pytest.approx(0.7)
    layers = {tl.name: tl for tl in S.sam_tax_layers("s3")}
    reduces = {c.name: c.itc_reduces for c in layers["federal"].depreciation}
    assert reduces == {"macrs_5": True, "sl_20": False}


def test_energy_arrays_convert_to_mwh_and_money_arrays_do_not():
    """`cf_energy_value` is money (WP4.0 review B3); every other `cf_energy_*`
    is kWh → MWh."""
    raw = S.load_case("s1")["outputs"]["arrays"]
    e = S.sam_expected("s1")["arrays"]
    energy = [k for k in raw if k.startswith("cf_energy_") and k != "cf_energy_value"]
    assert "cf_energy_net" in energy
    for k in energy:
        assert e[k] == pytest.approx([x / 1000.0 for x in raw[k]], rel=1e-15), k
    assert e["cf_energy_value"] == raw["cf_energy_value"]
    p = S.sam_params("s1")
    assert e["cf_energy_value"][1] == pytest.approx(
        e["cf_energy_net"][1] * p.ppa_price_per_mwh, rel=1e-12)
    assert e["cf_ebitda"] == raw["cf_ebitda"]


@pytest.mark.parametrize("grp,key,value,match", [
    ("PaymentIncentives", "cbi_fed_amount", 1.0e6, "CBI/IBI/PBI"),
    ("PaymentIncentives", "ibi_sta_percent", 10.0, "CBI/IBI/PBI"),
    ("PaymentIncentives", "pbi_fed_amount", [0.01], "CBI/IBI/PBI"),
    ("Revenue", "dispatch_tod_factors", [1.2] + [1.0] * 8, "TOD"),
    ("TaxCreditIncentives", "itc_fed_percent_maxvalue", [5.0e6], "finite ITC cap"),
    ("SystemCosts", "om_fuel_cost", [3.0], "om_fuel_cost"),
    ("SystemCosts", "system_use_recapitalization", 1.0, "system_use_recapitalization"),
    ("GridLimits", "grid_curtailment_price", [5.0], "grid_curtailment_price"),
    ("LandLease", "om_land_lease", [100.0], "om_land_lease"),
    ("BatterySystem", "en_batt", 1.0, "battery"),
    ("SystemCosts", "om_production", [2.0], "om_production"),
])
def test_the_mapping_refuses_each_unmodelled_sam_input(monkeypatch, grp, key, value, match):
    """Every SAM input P4 does not model is refused unless neutral (WP4.0
    review B4) — never silently mapped to nothing."""
    real = S.load_case

    def patched(name):
        d = real(name)
        d["sam_inputs"].setdefault(grp, {})[key] = value
        return d

    monkeypatch.setattr(S, "load_case", patched)
    with pytest.raises(S.SamMappingError, match=match):
        S.sam_params("s1")


def test_s1b_target_year_irr_and_the_dscr_array_without_debt():
    """R3: S1b's price solves the year-20 IRR (11 %), not the 25-year one; R9:
    DSCR is None in years without debt service, never SAM's 0.0."""
    s = S.sam_expected("s1b")["scalars"]
    assert s["target_year"] == 20 and s["equity_irr_at_target_year"] == pytest.approx(0.11, abs=1e-6)
    assert s["equity_irr_post_tax"] > 0.11
    assert all(x is None for x in S.sam_expected("s1")["arrays"]["cf_pretax_dscr"])
    d2 = S.sam_expected("s2")["arrays"]["cf_pretax_dscr"]
    assert d2[0] is None and d2[1] == pytest.approx(1.3)


def test_sams_sl15_table_is_refused_not_mapped_to_half_year(monkeypatch):
    """SAM's SL-15 is IRS Table A-8 rounded, not 1/15 with the half-year
    convention (WP4.3a review B6)."""
    real = S.load_case

    def patched(name):
        d = real(name)
        d["sam_inputs"]["Depreciation"].update(depr_alloc_sl_20_percent=70.0,
                                               depr_alloc_sl_15_percent=30.0)
        return d

    monkeypatch.setattr(S, "load_case", patched)
    with pytest.raises(S.SamMappingError, match="SL-15"):
        S.sam_tax_layers("s1")

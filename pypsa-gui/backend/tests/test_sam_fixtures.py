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

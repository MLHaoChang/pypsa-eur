"""
The P4 amendments to the finance contracts (IC P4 plan WP4.0): backward
compatible, no fabricated defaults (P0 gate finding 5, plan C12), the case
dates and choices the engine needs, the drill-down provenance and the
finance-side stream kinds (the ledger's `ValueStreamKind` unchanged).
"""
from __future__ import annotations

import typing
from datetime import date

import pytest
from pydantic import ValidationError

from models.commercial import ValueStreamKind
from models.finance import (
    ESCALATION_CLASSES, CashflowLine, DebtTranche, FinanceInputs, Provenance, SolvePpa,
    TaxEquityStructure,
)


def test_a_p3_era_finance_payload_still_validates():
    old = {"financial_close": "2030-01-01", "capex_phasing": [0.5, 0.5],
           "escalation": {"opex": 0.02}, "degradation_by_asset": {"pv": 0.005},
           "debt": [{"kind": "term_loan", "gearing": 0.6, "rate": 0.05, "tenor_years": 15}]}
    fin = FinanceInputs.model_validate(old)
    assert fin.analysis_years is None and fin.tax_losses is None and fin.annualise is False
    assert fin.debt[0].gearing_base == "capex"


def test_fees_dsra_and_grace_have_no_default():
    t = DebtTranche(kind="term_loan", gearing=0.6, rate=0.05, tenor_years=15)
    assert (t.upfront_fee, t.commitment_fee, t.dsra_months, t.grace_years) == (None,) * 4
    assert TaxEquityStructure(kind="partnership_flip", te_share_pre_flip=0.99,
                              te_share_post_flip=0.05, target_flip_irr=0.08, flip_year_cap=10,
                              cash_share_pre_flip=0.3, cash_share_post_flip=0.05,
                              dro_cap=0.0).itc_recapture_years is None


def test_a_per_year_rate_list_is_bounded_and_not_empty():
    assert DebtTranche(kind="term_loan", amount=1e6, rate=[0.05, 0.06], tenor_years=2).rate == [0.05, 0.06]
    with pytest.raises(ValidationError):
        DebtTranche(kind="term_loan", amount=1e6, rate=[0.05, -0.01], tenor_years=2)
    with pytest.raises(ValidationError):
        DebtTranche(kind="term_loan", amount=1e6, rate=[], tenor_years=2)


def test_escalation_takes_only_the_six_classes():
    assert set(ESCALATION_CLASSES) == {"opex", "fuel", "tariff", "ppa", "export", "capex"}
    with pytest.raises(ValidationError, match="escalation classes"):
        FinanceInputs(financial_close=date(2030, 1, 1), escalation={"om": 0.02})


def test_degradation_is_a_rate_or_a_list_of_rates():
    fin = FinanceInputs(financial_close=date(2030, 1, 1),
                        degradation_by_asset={"pv": 0.005, "bess": [0.02, 0.02, 0.015]})
    assert fin.degradation_by_asset["bess"] == [0.02, 0.02, 0.015]
    for bad in (1.0, -0.1, []):
        with pytest.raises(ValidationError, match="degradation"):
            FinanceInputs(financial_close=date(2030, 1, 1), degradation_by_asset={"pv": bad})


def test_the_case_dates_and_choices():
    fin = FinanceInputs(financial_close=date(2030, 1, 1), analysis_years=25,
                        acquisition_date=date(2029, 6, 1), construction_start=date(2029, 3, 1),
                        tax_losses="carryforward", financing_fee_tax="amortised",
                        hebesatz_pct=400.0, state_rate=0.0, pwa_met=True,
                        small_business_163j=False, reserves_rate=0.0175,
                        solve_ppa={"target_irr": 0.11, "target_year": 20})
    assert fin.state_rate == 0.0 and isinstance(fin.solve_ppa, SolvePpa)
    with pytest.raises(ValidationError):
        FinanceInputs(financial_close=date(2030, 1, 1), analysis_years=0)
    with pytest.raises(ValidationError):
        FinanceInputs(financial_close=date(2030, 1, 1), tax_losses="none")


def test_provenance_carries_the_ledger_drill_down():
    p = Provenance(source="bill", mode="pf", source_id="energy", contract_id="ppa1", period="_")
    assert p.model_dump()["source_id"] == "energy"


def test_finance_streams_extend_the_ledger_kinds_without_changing_them():
    line = CashflowLine(year=2031, participant="owner", counterparty="tax_authority",
                        value_stream="corporate_tax", amount=-1.0,
                        provenance=Provenance(source="tax", mode="pf"))
    assert line.value_stream == "corporate_tax"
    CashflowLine(year=2031, participant="owner", counterparty="market", value_stream="energy_export",
                 amount=1.0, provenance=Provenance(source="export_price", mode="pf"))
    # The ledger enum is the P3 one: corporate tax never collides with the levy stream.
    kinds = set(typing.get_args(ValueStreamKind))
    assert "corporate_tax" not in kinds and "tax" in kinds and "incentive" in kinds

"""
The investment-case workbook (IC P4 WP4.6d): an Excel round trip (every
number read back equal, every None an explicit `not_established` cell) and the
formula-injection guard (a user-named value stays text).
"""
from __future__ import annotations

import io

import pytest

from models.finance import (
    IC_REPORT_SECTIONS, CashflowLine, GatesBlock, IcSectionState, InvestmentCaseReport, Provenance,
)
from services.finance.export_xlsx import NOT_ESTABLISHED, build_workbook

openpyxl = pytest.importorskip("openpyxl")


def _report():
    sections = {n: IcSectionState(status="skipped", note="P5+") for n in IC_REPORT_SECTIONS}
    sections["project"] = IcSectionState(status="ok", payload={
        "equity_post_tax_irr": 0.1234, "lifecycle_npv": None, "counterfactual": "bill+commodity",
        "flags": ["contract_ends:p1:2040", "=cmd|' /C calc'!A0"],
        "by_year": [{"year": 2031, "ebitda": 1000.5, "tax": None},
                    {"year": 2032, "ebitda": 1010.25, "tax": 12.5}]})
    sections["debt"] = IcSectionState(status="not_established", payload={
        "reasons": {"debt": ["input_missing:upfront_fee:0:term_loan"]}})
    lines = [CashflowLine(year=2031, participant="owner", counterparty="+SUM(A1:A9)",
                          value_stream="ppa_settlement", asset="@evil", amount=-1234.5678,
                          provenance=Provenance(source="contract", mode="pf", contract_id="-ppa1")),
             CashflowLine(year=2032, participant="owner", counterparty="grid",
                          value_stream="corporate_tax", amount=0.0,
                          provenance=Provenance(source="finance", mode="pf"))]
    return InvestmentCaseReport(
        case_id="case-1", assumptions_hash="abcdef0123456789", packs={"us_federal": "77ad0d6516c8d403"},
        project_irr_post_tax=0.0987, npv_at_wacc=None, min_dscr=1.3,
        gates=GatesBlock(wacc_vs_discount_rate_consistent=None),
        sections=sections, completeness={n: sections[n].status for n in IC_REPORT_SECTIONS},
        cashflow_lines=lines)


def _values(ws):
    return [[c.value for c in row] for row in ws.iter_rows()]


def test_round_trip_numbers_equal_and_none_is_explicit():
    wb = openpyxl.load_workbook(io.BytesIO(build_workbook(_report(), project="p")))
    assert wb.sheetnames[:2] == ["About", "Summary"] and "CashflowLines" in wb.sheetnames
    assert set(IC_REPORT_SECTIONS) <= set(wb.sheetnames)
    summary = dict((r[0], r[1]) for r in _values(wb["Summary"])[1:])
    assert summary["project_irr_post_tax"] == 0.0987 and summary["min_dscr"] == 1.3
    assert summary["npv_at_wacc"] == NOT_ESTABLISHED and summary["project_irr_pre_tax"] == NOT_ESTABLISHED
    proj = _values(wb["project"])
    flat = {r[0]: r[1] for r in proj if r and r[0]}
    assert flat["Status"] == "ok" and flat["equity_post_tax_irr"] == 0.1234
    assert flat["lifecycle_npv"] == NOT_ESTABLISHED
    table = proj[proj.index(["by_year"] + [None] * (len(proj[0]) - 1)) + 2:]
    assert [r[:3] for r in table[:2]] == [[1000.5, NOT_ESTABLISHED, 2031], [1010.25, 12.5, 2032]]
    cf = _values(wb["CashflowLines"])
    assert cf[1][6] == -1234.5678 and cf[2][6] == 0.0
    about = {r[0]: r[1] for r in _values(wb["About"])}
    assert about["Assumptions hash"] == "abcdef0123456789" and about["Pack us_federal"] == "77ad0d6516c8d403"
    assert about["WACC vs discount rate consistent"] == NOT_ESTABLISHED
    assert about["Section debt"] == "not_established"
    flags = [r[1] for r in _values(wb["About"]) if r[0] == "Flag"]
    assert "input_missing:upfront_fee:0:term_loan" in flags and "contract_ends:p1:2040" in flags


def test_formula_injection_stays_text():
    wb = openpyxl.load_workbook(io.BytesIO(build_workbook(_report())))
    cf = wb["CashflowLines"]
    for ref, want in (("C2", "+SUM(A1:A9)"), ("F2", "@evil"), ("M2", "-ppa1")):
        assert cf[ref].value == want and cf[ref].data_type == "s", ref
    flags = [row for row in wb["About"].iter_rows() if row[0].value == "Flag"]
    evil = [row[1] for row in flags if str(row[1].value).startswith("=")]
    assert evil and all(c.data_type == "s" for c in evil)

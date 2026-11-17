# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Business case 17: each P&L row that is not established carries a reason naming the
missing input for this case (a line, a linked output, an IC figure), read from the
engine's own reason codes."""

from __future__ import annotations

import json
import types
from pathlib import Path

import numpy as np

from models.finance import DebtTranche, Incentive, TerminalValueRule
from services.business_case.linked_outputs import NotEvaluated
from services.finance import business_case as bcm
from services.finance.business_case import PnL, build_business_case, pnl, row_reasons
from services.finance.engine import run_case
from tests.test_business_case_engine import ESC, PACK, _capex, _ic, _inputs, _line, _run

PNL_ROWS = (
    Path(__file__).resolve().parents[2]
    / "frontend/src/pages/results/investment/businessCasePnlRows.json"
)


def _reasons(r, outputs=None, **kw):
    p = pnl(r, **kw)
    got = row_reasons(r, p, outputs)
    # A reason for exactly the rows that read None, never for an established one.
    assert set(got) == {k for k, v in p.rows.items() if v is None}
    return got


def test_the_dependency_table_names_every_row_the_p_and_l_serves():
    assert sorted(bcm._ROW_INPUTS) == json.loads(PNL_ROWS.read_text())
    _, r = _run(_inputs(_line()))
    assert sorted(pnl(r, receivable_days=30.0).rows) == sorted(bcm._ROW_INPUTS)


def test_an_established_case_serves_no_reason():
    _, r = _run(_inputs(_line()))
    assert _reasons(r, receivable_days=30.0) == {}


def test_a_linked_output_not_evaluated_names_the_line_and_the_output():
    ln = _line(name="nh3", quantity=None, quantity_source="linked:served_product.nh3@1")
    out = {"nh3": NotEvaluated("served_product.nh3", "stale")}
    _, r = _run(_inputs(ln), outputs=out)
    got = _reasons(r, out, receivable_days=30.0)
    want = (
        "operating_not_established: line_not_established:nh3, "
        "linked_output_not_evaluated:served_product.nh3:stale"
    )
    for row in ("revenue", "ebitda", "ebitda_margin", "revenue_net_of_pass_through",
                "ebit", "net_income", "project_pre_tax", "credits",
                "working_capital_change", "equity_after_working_capital"):
        assert got[row] == want, row
    # The tax waits on the operating section, and names the same line and output.
    assert got["tax"].startswith("tax_not_established: ")
    assert got["tax"].endswith(
        "operating_not_established, line_not_established:nh3, "
        "linked_output_not_evaluated:served_product.nh3:stale"
    )
    # Without the outputs map the line alone is named.
    assert row_reasons(r, pnl(r))["revenue"] == (
        "operating_not_established: line_not_established:nh3"
    )


def test_a_missing_capex_rate_names_the_asset():
    esc = {k: v for k, v in ESC.items() if k != "capex"}
    _, r = _run(_inputs(_capex(money_year=2028)), _ic(escalation=esc))
    got = _reasons(r, receivable_days=30.0)
    assert got["capex"].startswith("capex_not_established: ")
    assert "overnight_cost_missing:plant" in got["capex"]
    assert got["project_pre_tax"] == got["capex"]


def test_a_missing_terminal_value_names_it():
    _, r = _run(_inputs(_line()), _ic(terminal_value=TerminalValueRule(method="fixed")))
    got = _reasons(r, receivable_days=30.0)
    assert got["terminal"] == "terminal_not_established: terminal_value_missing"
    assert got["ebit"] == got["net_income"] == got["terminal"]


def test_a_book_value_terminal_without_a_tax_basis_names_the_tax_inputs():
    bc = build_business_case(
        _ic(terminal_value=TerminalValueRule(method="book_value")), _inputs(_line()), {}
    )
    r = run_case(bc.case, None)
    got = _reasons(r, receivable_days=30.0)
    assert got["terminal"] == (
        "terminal_not_established: book_value_needs_tax_basis, tax_not_established, "
        "tax_pack_missing"
    )


def test_a_missing_tax_pack_names_it_on_every_tax_row():
    bc = build_business_case(_ic(), _inputs(_line()), {})
    r = run_case(bc.case, None)
    got = _reasons(r, receivable_days=30.0)
    for row in ("depreciation", "tax", "tax_equity", "ebit", "net_income",
                "project_post_tax", "equity_post_tax", "project_after_working_capital"):
        assert got[row] == "tax_not_established: tax_pack_missing", row


def test_a_debt_input_missing_names_the_tranche_input():
    t = DebtTranche(kind="term_loan", rate=0.05, tenor_years=4, amount=100.0, upfront_fee=None,
                    dsra_months=0, grace_years=0)
    _, r = _run(_inputs(_line()), _ic(debt=[t]))
    got = _reasons(r, receivable_days=30.0)
    assert got["equity_pre_tax"] == (
        "debt_not_established: input_missing:upfront_fee:0:term_loan"
    )
    assert got["equity_post_tax"] == got["equity_pre_tax"]
    # The tax waits on the debt: its reason names the debt input too.
    assert got["project_post_tax"] == got["tax"] == (
        "tax_not_established: debt_not_established, "
        "input_missing:upfront_fee:0:term_loan"
    )


def test_an_unresolved_grant_names_the_incentives_section():
    _, r = _run(_inputs(_line()), _ic(incentives=[Incentive(kind="grant")]))
    got = _reasons(r, receivable_days=30.0)
    assert got["credits"].startswith("incentives_not_established")
    assert got["credits"] == "incentives_not_established: " + ", ".join(
        r.reasons["incentives"]
    )


def test_no_receivable_days_names_that_input():
    _, r = _run(_inputs(_line()))
    got = _reasons(r)
    assert got == {
        "working_capital_change": "input_missing:receivable_days",
        "project_after_working_capital": "input_missing:receivable_days",
        "equity_after_working_capital": "input_missing:receivable_days",
    }


def test_an_overflowed_input_reads_not_finite_and_the_row_itself_none():
    _, r = _run(_inputs(_line()))
    p = pnl(r, receivable_days=30.0)
    rows = dict(p.rows, ebitda=None, ebit=None)
    p2 = PnL(years=p.years, streams=p.streams, rows=rows, not_finite=("ebitda",))
    got = row_reasons(r, p2)
    assert got == {"ebit": "not_finite:ebitda"}


def test_a_section_the_engine_holds_established_reads_a_bare_reason():
    # A row None while its section reads ok (no code to name) never invents a cause.
    _, r = _run(_inputs(_line()))
    p = pnl(r, receivable_days=30.0)
    rows = dict(p.rows, tax=None, capex=None)
    got = row_reasons(r, PnL(years=p.years, streams=p.streams, rows=rows))
    assert got == {"tax": "not_established", "capex": "not_established"}
    fake = types.SimpleNamespace(op=r.op, sections={}, reasons={})
    assert row_reasons(fake, PnL(p.years, p.streams, {"tax": None})) == {
        "tax": "tax_not_established"
    }
    assert row_reasons(fake, PnL(p.years, p.streams, {"x": None, "y": np.zeros(1)})) == {
        "x": "not_established"
    }

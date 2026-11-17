# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Business case 17: each P&L row that is not established carries every input it lacks
for this case (a line, its linked output, a section's missing input), read from the
engine's own reason codes. GK B plan conditions C1 (the dependency table against the
real engine), C2 (the union, in a fixed order), C3 (the code manifest) and C5 (rows are
all-or-nothing)."""

from __future__ import annotations

import functools
import json
import types
from pathlib import Path

import numpy as np
import pytest

from models.finance import DebtTranche, Incentive, TerminalValueRule
from services.business_case.linked_outputs import NotEvaluated
from services.finance import business_case as bcm
from services.finance.business_case import PnL, build_business_case, pnl, row_reasons
from services.finance.engine import run_case
from tests.test_business_case_engine import ESC, PACK, _capex, _ic, _inputs, _line

_INV = Path(__file__).resolve().parents[2] / "frontend/src/pages/results/investment"
PNL_ROWS = _INV / "businessCasePnlRows.json"
#: The code families (the part before the first ":") row_reasons can emit; a vitest
#: gives each its words (C3).
CODES = _INV / "businessCaseRowReasonCodes.json"

_LINKED = _line(name="nh3", quantity=None, quantity_source="linked:served_product.nh3@1")
_STALE = {"nh3": NotEvaluated("served_product.nh3", "stale")}
_TRANCHE = dict(kind="term_loan", rate=0.05, tenor_years=4, amount=100.0,
                upfront_fee=0.0, dsra_months=0, grace_years=0)

#: One knockout per leaf: (case args, the (section, code) it must name).
KNOCK = {
    "op": (dict(lines=[_LINKED], outputs=_STALE), ("operating", "line_not_established:nh3")),
    "term": (dict(ic=dict(terminal_value=TerminalValueRule(method="fixed"))),
             ("terminal", "terminal_value_missing")),
    "tax": (dict(pack=None), ("tax", "tax_pack_missing")),
    "capex": (dict(lines=[_line(), _capex(money_year=2028)],
                   ic=dict(escalation={k: v for k, v in ESC.items() if k != "capex"})),
              ("capex", "overnight_cost_missing:plant")),
    "debt": (dict(ic=dict(debt=[DebtTranche(**{**_TRANCHE, "upfront_fee": None})])),
             ("debt", "input_missing:upfront_fee:0:term_loan")),
    "inc": (dict(ic=dict(incentives=[Incentive(kind="grant")])), ("incentives", None)),
    "rd": (dict(rd=None), ("inputs", "input_missing:receivable_days")),
}


def _case(lines=None, outputs=None, ic=None, pack=PACK, rd=30.0):
    ic = dict(ic or {})
    ic.setdefault("debt", [DebtTranche(**_TRANCHE)])
    bc = build_business_case(_ic(**ic), _inputs(*(lines or [_line()])), outputs or {})
    r = run_case(bc.case, pack)
    p = pnl(r, receivable_days=rd)
    got = row_reasons(r, p, outputs, receivable_days=rd)
    # A reason for exactly the rows that read None, never for an established one.
    assert set(got) == {k for k, v in p.rows.items() if v is None}
    return r, p, got


@functools.cache
def _knocked(name):
    return _case(**KNOCK[name][0])


def _knock(name):
    want = KNOCK[name][1]
    r, p, got = _knocked(name)
    if want[1] is None:  # the incentives' own first code
        want = (want[0], r.reasons["incentives"][0])
    return p, got, {"section": want[0], "code": want[1]}


def test_the_dependency_table_names_every_row_the_p_and_l_serves():
    assert sorted(bcm._ROW_INPUTS) == json.loads(PNL_ROWS.read_text())
    _, p, got = _case()
    assert sorted(p.rows) == sorted(bcm._ROW_INPUTS)
    assert got == {}  # an established case (with debt) serves no reason


#: C1 soundness: each leaf and each edge of `_ROW_INPUTS` that a real input can knock
#: out alone. A table edit that drops one fails here. Edges no input reaches alone are
#: equivalent and listed in the PR (stage_capex, replacement, grant are never None;
#: a tax knockout takes depreciation and tax together).
EDGES = [
    ("revenue", "op"), ("opex", "op"), ("ebitda", "op"), ("terminal", "term"),
    ("depreciation", "tax"), ("tax", "tax"), ("tax_equity", "tax"), ("capex", "capex"),
    ("revenue_net_of_pass_through", "op"), ("ebitda_net_of_pass_through", "op"),
    ("ebitda_margin", "op"),
    ("ebit", "op"), ("ebit", "term"), ("ebit", "tax"),
    ("net_income", "op"), ("net_income", "term"), ("net_income", "tax"),
    ("credits", "op"), ("credits", "term"), ("credits", "capex"), ("credits", "inc"),
    ("project_pre_tax", "op"), ("project_pre_tax", "term"), ("project_pre_tax", "capex"),
    ("project_post_tax", "op"), ("project_post_tax", "tax"), ("project_post_tax", "inc"),
    ("equity_pre_tax", "op"), ("equity_pre_tax", "debt"),
    ("equity_post_tax", "op"), ("equity_post_tax", "tax"), ("equity_post_tax", "inc"),
    ("equity_post_tax", "debt"),
    ("working_capital_change", "op"), ("working_capital_change", "rd"),
    ("project_after_working_capital", "rd"), ("project_after_working_capital", "tax"),
    ("equity_after_working_capital", "rd"), ("equity_after_working_capital", "debt"),
    ("equity_after_working_capital", "tax"),
]


@pytest.mark.parametrize("row,knock", EDGES)
def test_each_knockout_names_its_input_on_every_row_that_reads_it(row, knock):
    p, got, want = _knock(knock)
    assert p.rows[row] is None
    assert want in got[row]
    # Completeness: no row the engine leaves None falls to the bare reason.
    assert all({"section": None, "code": "not_established"} not in v for v in got.values())


def test_a_linked_output_not_evaluated_follows_its_line():
    _, _, got = _case(lines=[_LINKED], outputs=_STALE)
    assert got["revenue"] == [
        {"section": "operating", "code": "line_not_established:nh3"},
        {"section": "operating",
         "code": "linked_output_not_evaluated:served_product.nh3:stale"},
    ]
    # The tax waits on the operating section: the item is the operating input itself.
    assert got["revenue"][0] in got["tax"]
    assert {"section": "tax", "code": "operating_not_established"} not in got["tax"]


def test_a_linked_line_without_the_outputs_map_names_the_line_alone():
    r, p, _ = _knocked("op")
    assert row_reasons(r, p)["revenue"] == [
        {"section": "operating", "code": "line_not_established:nh3"}
    ]


def test_sections_that_wait_on_each_other_end_instead_of_looping():
    r, p, _ = _case()
    fake = types.SimpleNamespace(
        op=r.op,
        sections={"tax": "not_established", "debt": "not_established"},
        reasons={"tax": ["debt_not_established"], "debt": ["tax_not_established", "x"]},
    )
    assert row_reasons(fake, PnL(p.years, p.streams, {"tax": None})) == {
        "tax": [{"section": "debt", "code": "tax_not_established"},
                {"section": "debt", "code": "x"}]
    }


def test_c2_two_missing_inputs_are_both_named_in_table_order():
    _, _, got = _case(pack=None, rd=None)
    assert got["project_after_working_capital"] == [
        {"section": "inputs", "code": "input_missing:receivable_days"},
        {"section": "tax", "code": "tax_pack_missing"},
    ]
    assert got["equity_after_working_capital"] == got["project_after_working_capital"]


def test_the_tax_waiting_on_the_debt_names_the_debt_input():
    _, _, got = _case(**KNOCK["debt"][0])
    assert got["tax"] == [
        {"section": "debt", "code": "input_missing:upfront_fee:0:term_loan"}
    ]
    assert "project_post_tax" in got  # it reads the tax


def test_a_book_value_terminal_without_a_tax_basis_names_the_tax_inputs():
    _, _, got = _case(ic=dict(terminal_value=TerminalValueRule(method="book_value")),
                      pack=None)
    assert got["terminal"][:2] == [
        {"section": "terminal", "code": "book_value_needs_tax_basis"},
        {"section": "tax", "code": "tax_pack_missing"},
    ]


def test_an_overflowed_input_reads_not_finite_and_the_row_itself_no_entry():
    _, p, _ = _case()
    rows = dict(p.rows, ebitda=None, ebit=None)
    got = row_reasons(_case()[0], PnL(p.years, p.streams, rows, ("ebitda",)))
    assert got == {"ebit": [{"section": None, "code": "not_finite:ebitda"}]}


def test_a_row_whose_section_reads_ok_gets_the_bare_reason_never_an_invented_cause():
    r, p, _ = _case()
    rows = dict(p.rows, tax=None, capex=None, x=None)
    bare = [{"section": None, "code": "not_established"}]
    assert row_reasons(r, PnL(p.years, p.streams, rows)) == {
        "tax": bare, "capex": bare, "x": bare
    }
    fake = types.SimpleNamespace(op=r.op, sections={}, reasons={})
    assert row_reasons(fake, PnL(p.years, p.streams, {"tax": None})) == {
        "tax": [{"section": "tax", "code": "tax_not_established"}]
    }


def test_c5_rows_are_all_or_nothing_and_a_ratio_s_none_year_is_no_row_reason():
    for name in KNOCK:
        p, _, _ = _knock(name)
        for k, v in p.rows.items():
            if k == "ebitda_margin":
                assert v is None or isinstance(v, list)
            elif k == "debt_established":
                assert isinstance(v, bool)
            else:
                assert v is None or (isinstance(v, np.ndarray) and v.dtype.kind == "f"), k
    r, p, _ = _case()
    rows = dict(p.rows, ebitda_margin=[0.5, None])
    assert row_reasons(r, PnL(p.years, p.streams, rows)) == {}


def test_c3_every_code_emitted_is_in_the_manifest_the_view_words():
    families = set(json.loads(CODES.read_text()))
    seen = set()
    for name in KNOCK:
        _, got, _ = _knock(name)
        seen |= {i["code"].split(":")[0] for v in got.values() for i in v}
    seen |= {"not_finite", "not_established", "linked_output_not_evaluated",
             "book_value_needs_tax_basis"}
    assert seen <= families, seen - families


#: Words that sit near "reason" in the engine but are not codes (section names, "ok").
_NOT_CODES = {"capex", "counterfactual", "debt", "incentives", "operating", "ok", "tax",
              "terminal", "not_established"}


def test_c3_the_manifest_holds_every_reason_code_the_engine_writes():
    """A scan of the engine's reason literals: a new code without words fails here."""
    import re

    src = Path(__file__).resolve().parents[1] / "services/finance"
    found = {"terminal_needs_operating", "upfront_only_from_capital_cost"}
    for f in ("cashflow", "engine", "debt", "incentives", "replacements", "case"):
        for line in (src / f"{f}.py").read_text().splitlines():
            if "reason" in line and "flags" not in line:
                found |= {m.group(1) for m in re.finditer(r'f?"([a-z][a-z0-9_]+)(?=[:"])', line)}
    families = set(json.loads(CODES.read_text()))
    assert found - _NOT_CODES <= families, (found - _NOT_CODES) - families

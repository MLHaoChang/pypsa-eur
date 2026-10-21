"""
Revenue by stream per year (IPP renewables + BESS ticket 05, G6): the operating cash lines
grouped into rows, a financial PPA shown as its two legs (PPA sales = price × share × gen;
its reference leg joins market sales). Every year the rows sum to the operating cash; nothing
falls into "Other" without being named.
"""
from __future__ import annotations

import numpy as np
import pytest

import tests.test_finance_two_leg as TL
from services.finance.case import CONTRACT_CLASS, TemplateLine
from services.finance.cashflow import build_operating
from services.finance.revenue_streams import ROWS, revenue_by_stream
from services.finance.timeline import build_timeline
from tests.test_operating_pin import CASES, _cfd


def _op(case):
    return build_operating(case, build_timeline(case))


def _rows(out) -> dict[str, np.ndarray]:
    return {r["id"]: np.array(r["values"], dtype=float) for r in out["rows"]}


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_rows_sum_to_the_operating_cash_every_year(name):
    op = _op(CASES[name]())
    out = revenue_by_stream(op)
    rows = _rows(out)
    assert list(rows) == list(ROWS)
    np.testing.assert_allclose(sum(rows.values()), op.revenue - op.costs, rtol=1e-12, atol=1e-9)
    np.testing.assert_allclose(out["total"]["values"], op.revenue - op.costs)
    assert out["other_keys"] == []
    assert out["years"] == [int(y) for y in op.tl.years]


def test_a_financial_ppa_shows_as_ppa_sales_and_market_sales():
    """PPA sales = P × q at the PPA's indexation (2 %); market = ref × (export − q), export flat."""
    op = _op(TL._case(TL._financial()))
    rows = _rows(revenue_by_stream(op))
    q, k = TL.SHARE * TL.GEN, np.arange(op.tl.n)
    on = op.tl.operating_mask()
    np.testing.assert_allclose(rows["ppa"][on], (TL.P * q * 1.02 ** (k - 1))[on])
    np.testing.assert_allclose(rows["merchant"][on], TL.REF * (TL.EXPORT - q))
    # The two rows are the PPA line and the export line, re-cut (no money moves).
    np.testing.assert_allclose(rows["ppa"] + rows["merchant"],
                               op.lines["contract:c1:ppa_energy"]
                               + op.lines["export_price:export_price"])


def test_a_physical_ppa_and_a_cfd_keep_their_lines_whole():
    """No difference line to re-cut on a physical PPA; a CfD's difference stays one row."""
    op = _op(TL._case(TL._physical()))
    rows = _rows(revenue_by_stream(op))
    np.testing.assert_array_equal(rows["ppa"], op.lines["contract:c1:ppa_energy"])
    np.testing.assert_array_equal(rows["merchant"], op.lines["export_price:export_price"])
    op = _op(TL._case(_cfd()))
    rows = _rows(revenue_by_stream(op))
    np.testing.assert_array_equal(rows["cfd"], op.lines["contract:c:cfd_difference"])
    np.testing.assert_array_equal(rows["merchant"], op.lines["export_price:export_price"])
    assert not rows["ppa"].any()


def test_a_line_with_no_row_is_named_in_other():
    lease = TemplateLine("contract:l:lease_payment", "lease", -1000.0, CONTRACT_CLASS,
                         indexation=0.0, contract_id="l")
    op = _op(TL._case(TL._physical() + (lease,)))
    out = revenue_by_stream(op)
    assert out["other_keys"] == ["contract:l:lease_payment"]
    np.testing.assert_array_equal(_rows(out)["other"], op.lines["contract:l:lease_payment"])


def test_replacements_are_shown_as_a_cost_below_the_total():
    op = _op(TL._case(TL._physical()))
    out = revenue_by_stream(op)
    np.testing.assert_array_equal(out["replacements"]["values"], -op.replacement)


def test_no_operating_cash_no_rows():
    none_line = TemplateLine("bill:x", "network_energy", None, "tariff")
    assert revenue_by_stream(_op(TL._case(TL._physical() + (none_line,)))) is None


@pytest.mark.parametrize("stream,amount", [("certificates", 500.0), ("certificates", -500.0),
                                           ("tax", -300.0)])
def test_levies_and_certificates_are_not_grid_imports(stream, amount):
    """GK: a certificate (bought or sold) or a levy is no grid import; it is named in Other,
    and the rows still sum to the operating cash."""
    ln = TemplateLine(f"bill:{stream}", stream, amount, "tariff")
    op = _op(TL._case(TL._physical() + (ln,)))
    out = revenue_by_stream(op)
    rows = _rows(out)
    assert out["other_keys"] == [f"bill:{stream}"]
    assert not rows["grid_imports"].any()
    np.testing.assert_allclose(sum(rows.values()), op.revenue - op.costs, rtol=1e-12, atol=1e-9)

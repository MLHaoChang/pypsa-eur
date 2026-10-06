"""
U2 WP9 TARGET — `engine_adapter.case_workbook`: IC's finance workbook
(`export_xlsx.build_workbook` over `assemble_finance_sections`) with the
guided sheets appended — `Assumptions` (the ledger with `engine_path` and the
tested range), `Provenance` (pack version and hash, compiled digest, export
series ref, engines, basis, currency year) and the live-formula
`Cash flows (formulas)` view over the engine's cash series (owner decision 9).

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md WP2
(port of `test_proforma_xlsx.py`), WP9. `pending("WP9")` until stage 2 (it
also needs WP7's `option_case`). The two evaluator tests of the source file
stay where they are: `tests/xlsx_formula_eval.py` is unchanged.
"""
from __future__ import annotations

import io

import openpyxl
import pytest

from tests.u2_targets import pending
from tests.xlsx_formula_eval import FormulaEvaluator

pytestmark = pending("WP9", "case_workbook over build_workbook (needs WP7's option_case)")
SHEET = "Cash flows (formulas)"


def _A():
    from services.study import engine_adapter as A

    return A


def _bundle_and_ledger(option="bess_2h"):
    from tests import test_engine_adapter_case_golden as G

    n, cfg, compiled, ledger = G._solved_ic(option)
    return G._bundle(option), ledger


@pytest.fixture(scope="module")
def book():
    bundle, ledger = _bundle_and_ledger()
    blob = _A().case_workbook(bundle, ledger)
    assert isinstance(blob, bytes) and blob[:2] == b"PK"
    return bundle, ledger, openpyxl.load_workbook(io.BytesIO(blob))


def _find(ws, label):
    for row in ws.iter_rows():
        for cell in row:
            if cell.value == label:
                return cell
    raise AssertionError(f"{label!r} not on {ws.title}")


def test_the_engines_sheets_come_first_and_the_guided_sheets_are_appended(book):
    _b, _l, wb = book
    assert wb.sheetnames[-3:] == [SHEET, "Assumptions", "Provenance"]
    assert len(wb.sheetnames) > 3


def test_the_npv_cell_is_cf0_plus_npv_of_cf1_to_cfn_and_evaluates_to_the_case(book):
    bundle, _l, wb = book
    ws = wb[SHEET]
    npv_label = _find(ws, "NPV")
    formula = ws.cell(npv_label.row, npv_label.column + 1).value
    first = _find(ws, "Year").row + 1
    last = first + bundle.view.horizon_years
    net = _find(ws, "Net cash flow").column_letter
    assert formula == f"={net}{first}+NPV($B$1,{net}{first + 1}:{net}{last})"
    got = FormulaEvaluator(wb).value(SHEET, ws.cell(npv_label.row, npv_label.column + 1).coordinate)
    assert got == pytest.approx(bundle.view.kpis.npv, abs=1.0)


def test_every_year_row_evaluates_to_the_engines_cash(book):
    bundle, _l, wb = book
    ws = wb[SHEET]
    ev = FormulaEvaluator(wb)
    first = _find(ws, "Year").row + 1
    net = _find(ws, "Net cash flow").column_letter
    for y in bundle.view.years:
        assert ev.value(SHEET, f"{net}{first + y.year}") == pytest.approx(y.net_cash_flow, abs=1e-6)


def test_assumptions_are_the_ledger_with_engine_paths_and_provenance_is_complete(book):
    from services.study import packs

    bundle, ledger, wb = book
    ws = wb["Assumptions"]
    header = [c.value for c in ws[1]]
    assert "engine_path" in header
    assert [c.value for c in ws["A"][1:] if c.value] == [r.key for r in ledger.rows]
    prov = {r[0].value: r[1].value for r in wb["Provenance"].iter_rows() if r[0].value}
    assert prov["library_version"] == ledger.ledger_version
    assert prov["ledger_hash"] == packs.ledger_hash(ledger)
    assert prov["compiled_digest"] == bundle.compiled.digest
    assert {"tariff_engine", "finance_engine"} <= set(prov["engines"].split(", "))


def test_a_ledger_text_that_looks_like_a_formula_stays_text():
    bundle, ledger = _bundle_and_ledger()
    rows = [r.model_copy(update={"source": '=HYPERLINK("http://x","y")'})
            if r.key == "discount_rate" else r for r in ledger.rows]
    wb = openpyxl.load_workbook(io.BytesIO(_A().case_workbook(
        bundle, ledger.model_copy(update={"rows": rows}))))
    assert _find(wb["Assumptions"], '=HYPERLINK("http://x","y")').data_type == "s"


def test_a_not_established_case_writes_no_npv():
    from tests import test_engine_adapter_case_golden as G

    bundle = G._bundle("bess_2h", bills={"option": None})
    wb = openpyxl.load_workbook(io.BytesIO(_A().case_workbook(bundle, G._ledger())))
    with pytest.raises(AssertionError):
        _find(wb[SHEET], "NPV")

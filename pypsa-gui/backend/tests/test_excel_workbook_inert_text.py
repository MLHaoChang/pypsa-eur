"""GK B E7: the Excel input workbook's cell writer (gridspine) and the app's
`services/study/workbook_view.text` keep the same text inert, on one case table."""

from __future__ import annotations

import io

import openpyxl
import pytest

from gridspine.ingest import excel_layout as L


@pytest.mark.parametrize("text", L.INERT_TEXT_CASES)
def test_the_gridspine_writer_and_workbook_view_keep_the_same_text_inert(text):
    from services.study.workbook_view import text as wv_text

    wb = openpyxl.Workbook()
    ours = L.put(wb.active, 1, 1, text)
    theirs = wv_text(wb.active, 2, 1, text)
    assert ours.value == theirs.value
    buf = io.BytesIO()
    wb.save(buf)
    back = openpyxl.load_workbook(io.BytesIO(buf.getvalue()))
    for row in (1, 2):
        cell = back.active.cell(row, 1)
        assert cell.data_type != "f", (row, text)
        assert cell.value == (ours.value or None)

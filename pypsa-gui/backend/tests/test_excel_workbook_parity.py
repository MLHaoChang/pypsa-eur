"""The Excel input workbook reader against today's app (IMPORTS-1, GK B E6 and E7).

E6: for a valid workbook in an older layout, `gridspine.ingest.excel_import.read_excel`
gives the same network as today's /import/excel: the same frames and the same
`_network_input_fingerprint`, after the route's own post-steps.
E7: the gridspine cell writer and `workbook_view.text` keep the same text inert.
"""
from __future__ import annotations

import io
import pathlib
import copy

import openpyxl
import pandas as pd
import pypsa
import pytest

from gridspine.ingest import excel_layout as L
from gridspine.ingest.excel_import import read_excel

MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _legacy_workbook() -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet, rows in {
        "buses": [["name", "v_nom", "carrier"], ["B1", 110.0, "AC"], ["B2", 110.0, "AC"]],
        "generators": [["name", "bus", "p_nom", "marginal_cost", "eh_flag"], ["G1", "B1", 5.0, 12.5, True],
                       ["'=cmd", "B2", 1.0, None, None]],
        "loads": [["name", "bus", "p_set"], ["L1", "B2", 4.0]],
        "lines": [["name", "bus0", "bus1", "x", "s_nom"], ["l1", "B1", "B2", 0.1, 50.0]],
    }.items():
        ws = wb.create_sheet(sheet)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _native_workbook() -> bytes:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.add("Bus", ["B1", "B2"], v_nom=110.0)
    n.add("Generator", "G1", bus="B1", p_nom=5.0, p_max_pu=[0.2, 0.5, 0.9])
    n.add("Load", "L1", bus="B2", p_set=[1.0, 2.0, 3.0])
    n.add("Line", "l1", bus0="B1", bus1="B2", x=0.1, s_nom=50.0)
    buf = io.BytesIO()
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "n.xlsx"
        n.export_to_excel(str(path))
        buf.write(path.read_bytes())
    return buf.getvalue()


def _post_steps(n) -> None:
    """What /import/excel does after parsing, so both sides are compared as opened."""
    from services.adequacy.eh_columns import normalise_eh_columns
    from services.network_crud import rederive_imported_parts

    rederive_imported_parts(n)
    normalise_eh_columns(n)


@pytest.mark.parametrize("make", [_legacy_workbook, _native_workbook], ids=["legacy-sheets", "pypsa-native"])
def test_an_older_layout_reads_as_today_s_import(client, session_ctx, tmp_path, make):
    from services.workflow_tools import _network_input_fingerprint

    data = make()
    r = client.post("/api/io/import/excel", files={"file": ("x.xlsx", data, MIME)})
    assert r.status_code == 200, r.text
    ctx = session_ctx(client)
    today = ctx.network

    path = tmp_path / "x.xlsx"
    path.write_bytes(data)
    res = read_excel(path)
    assert res.report.unmapped == []
    ours = res.network
    _post_steps(ours)

    for _, _, ln in L.SHEETS:
        a, b = getattr(today, ln), getattr(ours, ln)
        pd.testing.assert_frame_equal(a.sort_index(axis=1), b.sort_index(axis=1), obj=ln)
        for attr, frame in getattr(today, f"{ln}_t").items():
            pd.testing.assert_frame_equal(frame, getattr(ours, f"{ln}_t")[attr], obj=f"{ln}_t.{attr}")
    pd.testing.assert_index_equal(today.snapshots, ours.snapshots)
    theirs_fp = _network_input_fingerprint(ctx)
    other = copy.copy(ctx)          # the same project state, our network
    other.network = ours
    ours_fp = _network_input_fingerprint(other)
    assert ours_fp == theirs_fp


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

"""
PowerFactory DGS import, step 3, the Excel form (owner decision Q6, GK A2
X4-X7): one sheet per class, row 1 the typed headers, read through the ASCII
grammar.

Fixture licence: every workbook here is built in the test from the hand
fixtures of ``test_dgs_import`` and ``test_dgs_v7``; no third-party file.
"""

import datetime as dt
import re

import openpyxl
import pandas as pd
import pytest

from gridspine.ingest import excel_layout as L
from gridspine.ingest import import_result as ir
from gridspine.ingest.dgs_import import read_dgs
from gridspine.ingest.dgs_parse import parse_dgs_text
from gridspine.ingest.dgs_xlsx import is_dgs_workbook, parse_dgs_xlsx
from gridspine.ingest.import_result import ImportFormatError
from gridspine.schema.contracts import ContractError
from tests.gridspine.test_dgs_import import dgs_text
from tests.gridspine.test_dgs_v7 import V7
from tests.gridspine.test_excel_workbook import _network, _write


def _typed(field, letter):
    """A field as the cell a person would type: numbers as numbers."""
    if field == "":
        return None
    if letter == "i":
        return int(field)
    if letter in ("r", "d"):
        return float(field.replace(",", "."))
    return field


def workbook(text, path, *, keep_text=False):
    """The DGS text as a workbook: one sheet per class header, typed cells."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    ws = letters = None
    for line in text.splitlines():
        if not line.strip() or line.startswith("*"):
            continue
        fields = line.strip().split(";")
        if line.startswith("$$"):
            ws = wb.create_sheet(fields[0][2:])
            ws.append(fields[1:])
            letters = [h[h.index("(") + 1] for h in fields[1:]]
            continue
        ws.append(
            [f or None for f in fields]
            if keep_text
            else [_typed(f, t) for f, t in zip(fields, letters)]
        )
    wb.save(path)
    return path


def one_sheet(tmp_path, rows, *, general="7.0", name="ElmTerm", prep=None):
    wb = openpyxl.Workbook()
    g = wb.active
    g.title = "General"
    g.append(["FID(a:40)", "Descr(a:40)", "Val(a:40)"])
    g.append(["1", "Version", general])
    ws = wb.create_sheet(name)
    for row in rows:
        ws.append(row)
    if prep:
        prep(wb, ws)
    path = tmp_path / "one.xlsx"
    wb.save(path)
    return path


HEAD = ["FID(a:40)", "OP(a:1)", "loc_name(a:40)", "uknom(r)", "nlnum(i)"]


def _no_location(items):
    return sorted(
        re.sub(r" \((line \d+|sheet \w+, row \d+)\)", "", str(x)) for x in items
    )


# --- X7: the hand fixture as a workbook -------------------------------------


@pytest.mark.parametrize("keep_text", [False, True], ids=["typed", "text"])
def test_the_hand_fixture_as_a_workbook_maps_like_the_dgs_file(tmp_path, keep_text):
    text = dgs_text()
    (tmp_path / "hand.dgs").write_text(text, encoding="utf-8")
    a = read_dgs([tmp_path / "hand.dgs"], {})
    b = read_dgs([workbook(text, tmp_path / "hand.xlsx", keep_text=keep_text)], {})
    for comp in (
        "buses",
        "lines",
        "transformers",
        "loads",
        "generators",
        "shunt_impedances",
    ):
        x, y = getattr(a.network, comp), getattr(b.network, comp)
        pd.testing.assert_frame_equal(x.sort_index(), y.sort_index())
    for attr in ("p_set", "q_set"):
        pd.testing.assert_frame_equal(a.network.loads_t[attr], b.network.loads_t[attr])
    assert a.report.counts == b.report.counts
    assert a.report.warnings == b.report.warnings
    assert _no_location(a.report.unmapped) == _no_location(b.report.unmapped)
    assert _no_location(a.report.assumed) == _no_location(b.report.assumed)
    # locations name the sheet and row, not a line
    assert any("(sheet ElmRelay, row 2)" in u.source_item for u in b.report.unmapped)
    assert a.sidecars.keys() == b.sidecars.keys()


def test_the_7_0_fixture_as_a_workbook(tmp_path):
    t = parse_dgs_xlsx(workbook(V7, tmp_path / "v7.xlsx"))
    assert t.version == "7.0"
    assert t.tables["ElmLne"].loc["731", "dline"] == 12.5
    assert t.tables["ElmLne"].loc["731", "typ_id"] == "830"
    assert t.dropped["ElmLne"] == ["GPScoords:MATRIX"]


def test_a_refusal_names_the_sheet_and_row(tmp_path):
    path = workbook(
        V7.replace("731;C;L1;902;830;", "731;C;L1;902;899;"), tmp_path / "v.xlsx"
    )
    with pytest.raises(ImportFormatError) as e:
        read_dgs([path], {})
    assert "v.xlsx, sheet ElmLne, row 2: ElmLne.typ_id points at ID 899" in str(e.value)


def test_an_op_other_than_create_names_the_sheet_and_row(tmp_path):
    path = workbook(V7.replace("740;C;Load", "740;U;Load"), tmp_path / "v.xlsx")
    with pytest.raises(ContractError, match=r"sheet ElmLod, row 2: ElmLod: OP 'U'"):
        parse_dgs_xlsx(path)


# --- X4: cells ---------------------------------------------------------------


def test_numbers_become_their_text(tmp_path):
    path = one_sheet(tmp_path, [HEAD, ["2", "C", "T", 1e-05, 3], ["3", "C", "U", 5, 2]])
    t = parse_dgs_xlsx(path).tables["ElmTerm"]
    assert t.loc["2", "uknom"] == 1e-05 and t.loc["2", "nlnum"] == 3
    assert t.loc["3", "uknom"] == 5.0


def test_an_integral_real_keeps_its_point():
    """
    A real cell holding 5.0 is written "5.0", never "5", so an integer column
    holding it is refused as the ASCII "5.0" would be. (Excel saves a typed 5
    as "5", which openpyxl reads as the integer 5; only a cell saved as "5.0"
    reads as a real, so the cell is stubbed here.)
    """
    from types import SimpleNamespace

    from gridspine.ingest.dgs_xlsx import _text

    def cell(v):
        return SimpleNamespace(value=v, data_type="n")

    assert _text(cell(5.0), None) == "5.0"
    assert _text(cell(5), None) == "5"
    assert _text(cell(-0.0), None) == "-0.0"
    with pytest.raises(ContractError, match="'5.0' is not an integer"):
        parse_dgs_text(
            "$$General;FID(a:40);Descr(a:40);Val(a:40)\n1;Version;7.0\n"
            f"$$ElmTerm;FID(a:40);OP(a:1);nlnum(i)\n2;C;{_text(cell(5.0), None)}\n"
        )


def test_a_formula_is_refused_by_cell(tmp_path):
    path = one_sheet(tmp_path, [HEAD, ["2", "C", "T", "=11*3", 1]])
    with pytest.raises(
        ContractError, match=r"sheet ElmTerm, row 2, column D: a formula \(=11\*3\)"
    ):
        parse_dgs_xlsx(path)


@pytest.mark.parametrize(
    "value, words",
    [
        (True, "a true/false value"),
        (dt.datetime(2026, 1, 1), "a date or time"),
        (dt.date(2026, 1, 1), "a date or time"),
        (dt.time(12, 0), "a date or time"),
        ("a;b", "holds a ';'"),
        ("a\nb", "line break"),
    ],
)
def test_cells_dgs_cannot_hold_are_refused_by_cell(tmp_path, value, words):
    path = one_sheet(tmp_path, [HEAD, ["2", "C", value, 33.0, 1]])
    with pytest.raises(ContractError, match=r"sheet ElmTerm, row 2, column C: ") as e:
        parse_dgs_xlsx(path)
    assert words in str(e.value)


def test_an_error_cell_is_refused_by_cell(tmp_path):
    def prep(wb, ws):
        ws["D2"].value = "#N/A"
        ws["D2"].data_type = "e"

    path = one_sheet(tmp_path, [HEAD, ["2", "C", "T", 1.0, 1]], prep=prep)
    with pytest.raises(
        ContractError, match=r"row 2, column D: an error value \(#N/A\)"
    ):
        parse_dgs_xlsx(path)


@pytest.mark.parametrize("first", ["*2", "$$ElmLne"])
def test_a_first_cell_that_would_read_as_a_comment_or_header_is_refused(
    tmp_path, first
):
    path = one_sheet(tmp_path, [HEAD, [first, "C", "T", 1.0, 1]])
    with pytest.raises(ContractError, match=r"row 2, column A: .* comment or a header"):
        parse_dgs_xlsx(path)


def test_empty_rows_and_columns(tmp_path):
    """Trailing and wholly empty rows go; a partly empty row keeps its empty cells."""
    path = one_sheet(
        tmp_path,
        [
            HEAD + [None, None],
            ["2", "C", "T", 33.0, 1, None, None],
            [None] * 5,
            ["3", "C", None, None, 2],
            [None] * 3,
            [None] * 7,
        ],
    )
    t = parse_dgs_xlsx(path)
    df = t.tables["ElmTerm"]
    assert list(df.index) == ["2", "3"]
    assert df.loc["3", "loc_name"] is None and pd.isna(df.loc["3", "uknom"])
    assert t.where(df.loc["3", "_line"]) == "sheet ElmTerm, row 4"


def test_a_value_past_the_last_header_is_refused(tmp_path):
    path = one_sheet(tmp_path, [HEAD, ["2", "C", "T", 33.0, 1, None, "x"]])
    with pytest.raises(
        ContractError, match=r"row 2, column G: 'x' is past the last header"
    ):
        parse_dgs_xlsx(path)


def test_an_empty_header_cell_between_headers_is_refused(tmp_path):
    path = one_sheet(
        tmp_path, [["FID(a:40)", "OP(a:1)", None, "uknom(r)"], ["2", "C", None, 3.0]]
    )
    with pytest.raises(
        ContractError, match=r"sheet ElmTerm, row 1, column C: an empty header"
    ):
        parse_dgs_xlsx(path)


def test_a_sheet_that_is_not_a_class_table_is_refused(tmp_path):
    path = one_sheet(
        tmp_path, [["Notes about this export"], ["ignore me"]], name="Notes"
    )
    with pytest.raises(
        ContractError, match=r"sheet Notes, row 1: Notes: header column"
    ):
        parse_dgs_xlsx(path)


def test_an_empty_sheet_is_ignored_and_a_hidden_sheet_is_read(tmp_path):
    def prep(wb, ws):
        wb.create_sheet("Empty")
        ws.sheet_state = "hidden"

    t = parse_dgs_xlsx(one_sheet(tmp_path, [HEAD, ["2", "C", "T", 33.0, 1]], prep=prep))
    assert "Empty" not in t.tables
    assert t.tables["ElmTerm"].loc["2", "uknom"] == 33.0


def test_the_ascii_and_workbook_grammars_agree_on_a_5_0_row(tmp_path):
    path = one_sheet(tmp_path, [["ID(a:40)", "uknom(r)"], ["2", 1.5]], general="5.0")
    wb = openpyxl.load_workbook(path)
    wb["General"]["A1"] = "ID(a:40)"
    wb.save(path)
    t = parse_dgs_xlsx(path)
    u = parse_dgs_text(
        "$$General;ID(a:40);Descr(a:40);Val(a:40)\n1;Version;5.0\n$$ElmTerm;ID(a:40);uknom(r)\n2;1.5\n"
    )
    pd.testing.assert_frame_equal(
        t.tables["ElmTerm"].drop(columns="_line"),
        u.tables["ElmTerm"].drop(columns="_line"),
    )


# --- X5: the shared safe path ------------------------------------------------


def test_too_many_sheets_is_refused_by_the_shared_limit(tmp_path):
    def prep(wb, ws):
        for i in range(L.LIMITS["sheets"]):
            wb.create_sheet(f"S{i}")

    path = one_sheet(tmp_path, [HEAD], prep=prep)
    with pytest.raises(
        ImportFormatError, match=r"one.xlsx: The workbook is too large: sheets"
    ):
        read_dgs([path], {})


def test_a_declared_size_past_the_limit_is_refused_before_reading(
    tmp_path, monkeypatch
):
    monkeypatch.setitem(L.LIMITS, "rows", 2)
    path = one_sheet(
        tmp_path, [HEAD, ["2", "C", "T", 33.0, 1], ["3", "C", "U", 33.0, 1]]
    )
    with pytest.raises(ImportFormatError, match="rows 3 exceeds 2"):
        read_dgs([path], {})


def test_a_file_that_is_not_a_workbook_is_refused(tmp_path):
    path = tmp_path / "x.xlsx"
    path.write_text("$$General;ID(a:40)\n", encoding="utf-8")
    with pytest.raises(
        ImportFormatError, match=r"x.xlsx: The file is not an Excel workbook"
    ):
        read_dgs([path], {})
    assert not is_dgs_workbook(path)


# --- X6: which importer takes a .xlsx ---------------------------------------


def test_a_dgs_workbook_goes_to_dgs(tmp_path):
    path = workbook(V7, tmp_path / "v7.xlsx")
    assert ir.for_file(path).format_id == "dgs"
    assert not L.is_input_workbook(path)


def test_the_apps_workbook_goes_to_excel(tmp_path):
    path = _write(_network(False), tmp_path)
    assert not is_dgs_workbook(path)
    assert ir.for_file(path).format_id == "excel"


def test_a_workbook_both_claim_is_refused_naming_both(tmp_path):
    path = _write(_network(False), tmp_path)
    wb = openpyxl.load_workbook(path)
    g = wb.create_sheet("General")
    g.append(["FID(a:40)", "Descr(a:40)", "Val(a:40)"])
    wb.save(path)
    with pytest.raises(ImportFormatError) as e:
        ir.for_file(path)
    msg = str(e.value)
    assert "more than one format" in msg
    assert "PowerFactory export (DGS)" in msg and "Excel input workbook" in msg


def test_a_workbook_neither_claims_is_refused_naming_the_candidates(tmp_path):
    path = one_sheet(tmp_path, [HEAD], name="Data")
    wb = openpyxl.load_workbook(path)
    del wb["General"]
    wb.save(path)
    with pytest.raises(
        ImportFormatError, match="not a file any of these importers recognise"
    ):
        ir.for_file(path)


@pytest.mark.parametrize(
    "header",
    [
        ["ID(a:40)", "Descr(a:40)"],  # no Val
        ["Descr(a:40)", "Val(a:40)"],  # no key
        ["FID", "Descr", "Val"],  # not typed
    ],
)
def test_a_general_sheet_without_typed_dgs_headers_is_not_dgs(tmp_path, header):
    wb = openpyxl.Workbook()
    wb.active.title = "General"
    wb.active.append(header)
    path = tmp_path / "g.xlsx"
    wb.save(path)
    assert not is_dgs_workbook(path)


def test_one_extension_one_importer_needs_no_sniff(tmp_path):
    path = tmp_path / "a.dgs"
    path.write_text("not even DGS", encoding="utf-8")
    assert ir.for_file(path).format_id == "dgs"
    with pytest.raises(ImportFormatError, match=r"a.csv: no importer reads .csv"):
        ir.for_file(tmp_path / "a.csv")


def test_every_declared_sniff_resolves():
    """A typo in a sniff would make its importer silently unreachable."""
    import importlib

    for spec in ir.DECLARED:
        if spec.sniff:
            module, _, func = spec.sniff.partition(":")
            assert callable(getattr(importlib.import_module(module), func)), spec.sniff


def test_a_row_of_spaces_is_an_empty_row(tmp_path):
    path = one_sheet(tmp_path, [HEAD, ["2", "C", "T", 33.0, 1], ["  ", " "]])
    assert list(parse_dgs_xlsx(path).tables["ElmTerm"].index) == ["2"]


# --- mutant survivors: limits, paths, sniffs ---------------------------------


def _ws(rows, cols):
    from types import SimpleNamespace

    return SimpleNamespace(max_row=rows, max_column=cols)


def test_the_declared_size_is_checked_per_limit(monkeypatch):
    from gridspine.ingest import xlsx_safe as X

    monkeypatch.setitem(L.LIMITS, "rows", 10)
    monkeypatch.setitem(L.LIMITS, "columns", 10)
    monkeypatch.setitem(L.LIMITS, "cells", 50)
    X.declared_within_limits(_ws(10, 5))  # at each limit: accepted
    X.declared_within_limits(_ws(5, 10))
    X.declared_within_limits(_ws(None, None))  # an empty sheet declares nothing
    for ws, limit, value in (
        (_ws(11, 1), "rows", 11),
        (_ws(1, 11), "columns", 11),
        (_ws(6, 9), "cells", 54),
    ):
        with pytest.raises(L.ImportLimitError) as e:
            X.declared_within_limits(ws)
        assert (e.value.limit, e.value.value) == (limit, value)
    X.declared_within_limits(_ws(10, 5))  # rows * cols == 50: accepted


def test_streamed_rows_are_capped_per_limit(monkeypatch):
    from gridspine.ingest import xlsx_safe as X

    monkeypatch.setitem(L.LIMITS, "rows", 2)
    monkeypatch.setitem(L.LIMITS, "columns", 3)
    assert list(X.capped([(1, 2, 3), (4, 5, 6)])) == [(1, (1, 2, 3)), (2, (4, 5, 6))]
    with pytest.raises(L.ImportLimitError) as e:
        list(X.capped([(1,), (2,), (3,)]))
    assert e.value.limit == "rows"
    with pytest.raises(L.ImportLimitError) as e:
        list(X.capped([(1, 2, 3, 4)]))
    assert e.value.limit == "columns"


def test_the_cell_count_runs_across_sheets(tmp_path, monkeypatch):
    path = one_sheet(tmp_path, [HEAD, ["2", "C", "T", 33.0, 1]])  # 6 + 10 cells
    monkeypatch.setitem(L.LIMITS, "cells", 16)
    assert parse_dgs_xlsx(path).tables["ElmTerm"].loc["2", "uknom"] == 33.0
    monkeypatch.setitem(L.LIMITS, "cells", 15)
    with pytest.raises(L.ImportLimitError, match="cells 16 exceeds 15"):
        parse_dgs_xlsx(path)


def test_exactly_the_sheet_limit_is_accepted(tmp_path, monkeypatch):
    path = one_sheet(tmp_path, [HEAD, ["2", "C", "T", 33.0, 1]])
    monkeypatch.setitem(L.LIMITS, "sheets", 2)
    assert parse_dgs_xlsx(path).version == "7.0"
    monkeypatch.setitem(L.LIMITS, "sheets", 1)
    with pytest.raises(L.ImportLimitError, match="sheets 2 exceeds 1"):
        parse_dgs_xlsx(path)


def test_a_zip_that_is_not_a_workbook_is_refused(tmp_path):
    import zipfile

    path = tmp_path / "z.xlsx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("hello.txt", "not a workbook")
    with pytest.raises(
        ImportFormatError, match="z.xlsx: The file is not a readable Excel workbook"
    ):
        read_dgs([path], {})
    assert not is_dgs_workbook(path) and not L.is_input_workbook(path)


def test_a_path_may_be_text(tmp_path):
    path = workbook(V7, tmp_path / "v7.xlsx")
    assert parse_dgs_xlsx(str(path)).version == "7.0"
    assert ir.for_file(str(path)).format_id == "dgs"


def test_a_short_row_is_padded_to_the_header(tmp_path):
    path = one_sheet(tmp_path, [HEAD, ["2", "C"]])
    df = parse_dgs_xlsx(path).tables["ElmTerm"]
    assert df.loc["2", "loc_name"] is None and pd.isna(df.loc["2", "uknom"])


def test_an_empty_header_row_over_data_is_refused(tmp_path):
    path = one_sheet(tmp_path, [[None, None], ["2", "C", "T", 33.0, 1]])
    with pytest.raises(ContractError, match=r"sheet ElmTerm, row 1: row 1 is empty"):
        parse_dgs_xlsx(path)


def test_a_carriage_return_is_refused(tmp_path):
    path = one_sheet(tmp_path, [HEAD, ["2", "C", "a\rb", 33.0, 1]])
    with pytest.raises(ContractError, match="line break"):
        parse_dgs_xlsx(path)


def test_a_general_sheet_with_a_number_in_its_header_is_still_read(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.title = "General"
    wb.active.append(["FID(a:40)", 5, "Descr(a:40)", "Val(a:40)"])
    path = tmp_path / "g.xlsx"
    wb.save(path)
    assert is_dgs_workbook(path)


def test_a_general_sheet_past_the_limits_is_not_dgs(tmp_path, monkeypatch):
    path = one_sheet(tmp_path, [HEAD])
    monkeypatch.setitem(L.LIMITS, "rows", 1)
    assert not is_dgs_workbook(path)


@pytest.mark.parametrize(
    "sheets", [["About"], ["Horizon and finance"], ["About", "Nodes"]]
)
def test_the_input_workbook_needs_both_marker_sheets(tmp_path, sheets):
    wb = openpyxl.Workbook()
    wb.active.title = sheets[0]
    for s in sheets[1:]:
        wb.create_sheet(s)
    path = tmp_path / "w.xlsx"
    wb.save(path)
    assert not L.is_input_workbook(path)
    wb.create_sheet("Horizon and finance" if "About" in sheets else "About")
    wb.save(path)
    assert L.is_input_workbook(path)

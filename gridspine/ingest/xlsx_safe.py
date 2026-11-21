"""
Opening an uploaded .xlsx safely, shared by the workbook readers (the Excel
input workbook, the DGS workbook): one path, so no reader is looser than
another (GK B E2, GK A2 X5).

- The zip is checked before openpyxl sees it: member count and inflated size
  against `excel_layout.LIMITS`.
- A sheet's declared size is checked before any cell is read (a small file
  can declare A1:XFD1048576), and rows are counted as they stream.
- `open_workbook` opens read-only, without external links, and keeps
  formulas as formulas (`data_only=False`): a reader sees a formula cell as
  one and never takes its cached value, or its absence, for data.
- openpyxl hardens its XML parser only when defusedxml is importable;
  ``pypsa-gui/backend/tests/test_packaging_requirements.py`` pins that it is.

No pypsa here: a sniff can open a workbook without loading the engine.
"""

from __future__ import annotations

import logging
import pathlib
import zipfile

from gridspine.ingest import excel_layout as L

logger = logging.getLogger(__name__)


def check_zip(path: pathlib.Path) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        OSError,
        NotImplementedError,
        ValueError,
        EOFError,
    ) as exc:
        # A damaged header can claim a zip version or feature Python does not support.
        raise L.ImportFormatError("The file is not an Excel workbook (.xlsx).") from exc
    if len(infos) > L.LIMITS["zip_members"]:
        raise L.ImportLimitError("zip_members", len(infos))
    total = sum(i.file_size for i in infos)
    if total > L.LIMITS["uncompressed_bytes"]:
        raise L.ImportLimitError("uncompressed_bytes", total)


def declared_within_limits(ws) -> int:
    """
    Refuse a sheet whose declared size passes the limits before any cell is read
    (E2): a small file can declare A1:XFD1048576.
    """
    rows, cols = ws.max_row or 0, ws.max_column or 0
    if rows > L.LIMITS["rows"]:
        raise L.ImportLimitError("rows", rows)
    if cols > L.LIMITS["columns"]:
        raise L.ImportLimitError("columns", cols)
    if rows * cols > L.LIMITS["cells"]:
        raise L.ImportLimitError("cells", rows * cols)
    return rows * cols


def capped(rows):
    """Each (row number, row), refusing past the row and column limits."""
    for r, row in enumerate(rows, start=1):
        if r > L.LIMITS["rows"]:
            raise L.ImportLimitError("rows", r)
        if len(row) > L.LIMITS["columns"]:
            raise L.ImportLimitError("columns", len(row))
        yield r, row


def open_workbook(path: pathlib.Path):
    """
    The workbook at `path`, read-only, formulas kept, no external links;
    refused as `excel_layout.ImportFormatError` when it is not a readable
    .xlsx or passes a limit. The caller closes it.
    """
    import openpyxl

    check_zip(path)
    try:
        wb = openpyxl.load_workbook(
            path, read_only=True, data_only=False, keep_links=False
        )
    except Exception as exc:  # noqa: BLE001 - any parser failure is one refusal
        logger.warning("Workbook %s refused as unreadable", path.name, exc_info=True)
        raise L.ImportFormatError("The file is not a readable Excel workbook.") from exc
    if len(wb.sheetnames) > L.LIMITS["sheets"]:
        wb.close()
        raise L.ImportLimitError("sheets", len(wb.sheetnames))
    return wb


#: Sheet names only the app's Excel workbooks use: the input workbook's own
#: sheets (``excel_layout``) and the older export's ``network`` sheet.
EXCEL_SHEETS = frozenset(
    {L.ABOUT, L.HORIZON, L.TIME_SERIES, "network"} | {sheet for _, sheet, _ in L.SHEETS}
)


def is_excel_input_workbook(path) -> bool:
    """
    Whether the workbook at ``path`` has a sheet only the app's Excel
    workbooks use (``EXCEL_SHEETS``), for the import registry to route a
    ``.xlsx``. Sheet names only, no cell is read; a file that cannot be
    opened safely is not one.
    """
    try:
        wb = open_workbook(pathlib.Path(path))
    except L.ImportFormatError:
        return False
    try:
        return bool(EXCEL_SHEETS & set(wb.sheetnames))
    finally:
        wb.close()

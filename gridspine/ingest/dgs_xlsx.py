"""
PowerFactory DGS reader, Excel form (owner decision Q6): a ``.xlsx`` workbook
with one sheet per class, read into the same ``DgsTables`` as the ASCII file.

Layout (third-party-documented only: no PowerFactory .xlsx export was
available, and the DGS specification is behind a login; untested against a
real export):

- each sheet is one class, named as the class (``ElmTerm``); the ``General``
  sheet carries ``Descr``/``Val`` and the version, as in the ASCII file;
- row 1 holds the typed headers, one per cell, exactly as the ASCII header
  writes them (``FID(a:40)``, ``OP(a:1)``, ``uknom(r)``,
  ``vector:SIZEROW(i)``...); every following row is one object.

Each sheet becomes the ASCII text it stands for (``$$Class;header;...`` then
one ``;``-joined line per row), and ``dgs_parse.parse_dgs_text`` reads it:
one grammar, so 5.x and 7.x, pointers, vectors and every refusal are the
ASCII reader's. Refusals name "sheet <class>, row <n>" instead of a line.

Cells, each handled explicitly (GK A2 X4):

- a number becomes its text as Python writes it: an integer ``5``, a real
  ``5.0`` or ``1e-05``, so an integer column holding ``5.0`` is refused as the
  ASCII ``5.0`` would be;
- text is taken as it stands; a ``;`` or a line break in it is refused
  (it would split the row), and so is a row whose first cell starts with
  ``*`` or ``$$`` (it would read as a comment or a header);
- a formula is refused naming its cell: the workbook is opened with formulas
  kept, so its cached value, or a missing one, is never read as data. So are
  a boolean, a date or time, and an error value (``#N/A``);
- an empty cell is an empty field. Wholly empty rows are skipped, and empty
  cells past the header's last column are ignored; a cell with a value
  there is refused. A row with some empty cells keeps them;
- a header cell must be text; an empty one between two headers is refused;
- a wholly empty sheet is ignored. Every other sheet must be a DGS class
  table: one whose row 1 is not typed headers is refused, naming the sheet
  (the reader has no way to tell notes from a damaged class);
- hidden sheets are read like any other.

Opening goes through ``xlsx_safe`` (zip and size limits, read-only, no
external links), the same path as the Excel input workbook.
"""

from __future__ import annotations

import datetime as _dt
import math
from pathlib import Path

from openpyxl.utils import get_column_letter

from gridspine.ingest import excel_layout as L
from gridspine.ingest import xlsx_safe as X
from gridspine.ingest.dgs_parse import DgsTables, parse_dgs_text
from gridspine.schema.contracts import ContractError

GENERAL = "General"


def parse_dgs_xlsx(path) -> DgsTables:
    """Parse the DGS workbook at ``path``; refusals name the file and cell."""
    path = Path(path)
    wb = X.open_workbook(path)
    try:
        text, locations = _as_text(wb, path.name)
    finally:
        wb.close()
    return parse_dgs_text(text, source=path.name, locations=locations)


def is_dgs_workbook(path) -> bool:
    """
    Whether the workbook at ``path`` looks like a DGS export: a ``General``
    sheet whose row 1 is typed headers with an ``ID`` or ``FID`` column and
    ``Descr`` and ``Val``. Used by the import registry to route a ``.xlsx``;
    a file that cannot be opened safely is not one.
    """
    try:
        wb = X.open_workbook(Path(path))
    except L.ImportFormatError:
        return False
    try:
        if GENERAL not in wb.sheetnames:
            return False
        ws = wb[GENERAL]
        X.declared_within_limits(ws)
        first = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
        names = {
            str(v).split("(")[0].strip()
            for v in first
            if isinstance(v, str) and v.strip().endswith(")")
        }
        return {"Descr", "Val"} <= names and bool(names & {"ID", "FID"})
    except L.ImportFormatError:
        return False
    finally:
        wb.close()


def _as_text(wb, source: str) -> tuple[str, dict]:
    lines: list[str] = []
    locations: dict = {}
    cells = 0
    for ws in wb.worksheets:
        name = ws.title

        def fail(r, c, msg, name=name):
            at = f"sheet {name}, row {r}" + (
                f", column {get_column_letter(c)}" if c else ""
            )
            raise ContractError(f"DGS {source}, {at}: {msg}")

        X.declared_within_limits(ws)
        ws.reset_dimensions()
        rows = []
        for r, row in X.capped(ws.iter_rows()):
            cells += len(row)
            if cells > L.LIMITS["cells"]:
                raise L.ImportLimitError("cells", cells)
            rows.append(
                [
                    _text(cell, lambda m, r=r, c=c: fail(r, c, m))
                    for c, cell in enumerate(row, start=1)
                ]
            )
        while rows and not any(rows[-1]):
            rows.pop()
        if not rows:
            continue  # a wholly empty sheet
        if ";" in name:
            fail(1, 0, "a ';' in the sheet name; the sheet name is the class")
        head = list(rows[0])
        while head and head[-1] == "":
            head.pop()
        if not head:
            fail(1, 0, "row 1 is empty; it must hold the class's typed headers")
        for c, h in enumerate(head, start=1):
            if h == "":
                fail(1, c, "an empty header cell between two headers")
        width = len(head)
        lines.append(f"$${name};" + ";".join(head))
        locations[len(lines)] = f"sheet {name}, row 1"
        for r, fields in enumerate(rows[1:], start=2):
            if not any(fields):
                continue  # a wholly empty row
            for c, v in enumerate(fields[width:], start=width + 1):
                if v != "":
                    fail(
                        r,
                        c,
                        f"{v!r} is past the last header (column "
                        f"{get_column_letter(width)})",
                    )
            fields = (fields + [""] * width)[:width]
            if fields[0].startswith(("*", "$$")):
                fail(r, 1, f"{fields[0]!r} would read as a comment or a header")
            lines.append(";".join(fields))
            locations[len(lines)] = f"sheet {name}, row {r}"
    return "\n".join(lines) + "\n", locations


def _text(cell, fail) -> str:
    """One cell as the ASCII field it stands for."""
    kind = getattr(cell, "data_type", None)
    v = getattr(cell, "value", None)
    if kind == "f":
        fail(f"a formula ({v}); the DGS reader takes values only")
    if kind == "e":
        fail(f"an error value ({v})")
    if v is None:
        return ""
    if isinstance(v, bool):
        fail(f"a true/false value ({v}); DGS has no boolean type")
    if isinstance(v, (_dt.date, _dt.time, _dt.timedelta)):  # datetime is a date
        fail(f"a date or time ({v}); DGS has no date type")
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            fail(f"{v!r} is not finite")
        return repr(v)
    if isinstance(v, str):
        if ";" in v or "\n" in v or "\r" in v:
            fail(f"{v!r} holds a ';' or a line break, which would split the row")
        return v
    fail(f"a {type(v).__name__} value, which DGS cannot hold")
    raise AssertionError  # pragma: no cover - fail raises

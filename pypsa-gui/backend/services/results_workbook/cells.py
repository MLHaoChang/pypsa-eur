"""
Cell writing for the results workbook: every text cell goes through
`workbook_view.text` (formula-looking text stays text, control characters are
replaced), an unknown value is an explicit `not_established` cell, never an
empty one or a 0 (ADR-0001).
"""
from __future__ import annotations

import math
from typing import Any

from openpyxl.styles import Font

from services.finance.export_xlsx import NOT_ESTABLISHED
from services.study import workbook_view as WV

__all__ = ["BOLD", "NOT_ESTABLISHED", "formula", "heading", "put", "width"]

BOLD = Font(bold=True)


def put(ws, row: int, col: int, value: Any):
    """One value cell: None or a non-finite number → `not_established`."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return WV.text(ws, row, col, NOT_ESTABLISHED)
    if isinstance(value, (bool, int, float)):
        cell = ws.cell(row=row, column=col)
        cell.value = value
        return cell
    return WV.text(ws, row, col, str(value))


def formula(ws, row: int, col: int, expr: str):
    """A formula this module composed from cell addresses (never user text)."""
    return WV.formula(ws, row, col, expr)


def heading(ws, row: int, values: list[Any], col: int = 1) -> None:
    for j, v in enumerate(values, start=col):
        WV.text(ws, row, j, v).font = BOLD


def width(ws, widths: dict[str, float]) -> None:
    for letter, w in widths.items():
        ws.column_dimensions[letter].width = w

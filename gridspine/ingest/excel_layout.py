"""
The Excel input workbook's layout, shared by its writer and its reader (IMPORTS-1).

No pypsa here (the engine-cage ruling of 2026-11-17: pypsa is imported only in
`gridspine/ingest/*_import.py`), and no app code: the plain names come in from
the caller as `Column`s keyed by model name, so the expert catalogue can replace
the strings without a format change.

Every component sheet has four header rows:

1. plain names (visible, bold);
2. units (visible; a plain name never carries its unit);
3. the model name of each column (hidden row; RAW-NAMES-1);
4. the factor from the stored value to the shown value (hidden row).

Data starts on row 5. The reader trusts none of these rows (GK B E1): a model
name must be an input attribute of the class or on the caller's allow-list, and
a factor must be the one the unit table gives for the unit shown above it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from gridspine.ingest.import_result import ImportFormatError as _ContractFormatError

FORMAT_VERSION = 1

ROW_LABEL, ROW_UNIT, ROW_KEY, ROW_SCALE = 1, 2, 3, 4
FIRST_DATA_ROW = 5

#: Model class → (sheet name, the n.<list_name> table). Order is the workbook's sheet order.
SHEETS: tuple[tuple[str, str, str], ...] = (
    ("Carrier", "Carriers", "carriers"),
    ("Bus", "Nodes", "buses"),
    ("Line", "Lines", "lines"),
    ("Transformer", "Transformers", "transformers"),
    ("Generator", "Generators", "generators"),
    ("StorageUnit", "Storage", "storage_units"),
    ("Store", "Stores", "stores"),
    ("Load", "Loads", "loads"),
    ("Link", "Links", "links"),
    ("ShuntImpedance", "Shunts", "shunt_impedances"),
    ("GlobalConstraint", "System limits", "global_constraints"),
)
SHEET_OF = {cls: sheet for cls, sheet, _ in SHEETS}
CLASS_OF_SHEET = {sheet: cls for cls, sheet, _ in SHEETS}
LIST_NAME = {cls: ln for cls, _, ln in SHEETS}

ABOUT, TIME_SERIES, HORIZON, LISTS = (
    "About",
    "Time series",
    "Horizon and finance",
    "Lists",
)
#: Hidden: uploaded profiles whose time steps differ from the project's (kept for the round trip).
UPLOADED = "Uploaded profiles"
UP_ROW_LIST, UP_ROW_ATTR, UP_ROW_ASSET, UP_ROW_META, UP_FIRST_DATA_ROW = 1, 2, 3, 4, 5

#: What the workbook does not carry (GK B F3); the About sheet lists it too.
NOT_CARRIED = (
    "Map and single-line layout",
    "Site scene and campus drawings",
    "Results, reports and studies",
    "The assumptions ledger and change history",
    "Custom line and transformer types (standard types are kept by name)",
)

# Time series sheet: the header rows above each series column.
TS_ROW_TYPE, TS_ROW_ASSET, TS_ROW_LABEL, TS_ROW_UNIT = 1, 2, 3, 4
TS_ROW_CLASS, TS_ROW_ATTR, TS_ROW_SCALE, TS_ROW_USER = 5, 6, 7, 8
TS_FIRST_DATA_ROW = 9
#: The time columns of the Time series sheet, by model name.
SNAPSHOT_COLUMNS = (
    ("period", "Investment period", "year"),
    ("snapshot", "Time", ""),
    ("objective", "Objective weighting", "h"),
    ("stores", "Storage weighting", "h"),
    ("generators", "Generator weighting", "h"),
)

# Horizon and finance sheet: setting rows, then the period weighting table.
HORIZON_SETTINGS = (
    ("discount_rate", "Discount rate", "%", 100.0),
    ("multi_investment_periods", "Plan over several investment periods", "", 1.0),
    ("investment_periods", "Investment periods (comma separated)", "year", 1.0),
    ("snapshot_kind", "Time column holds", "", 1.0),
)
PERIOD_COLUMNS = (
    ("period", "Investment period", "year"),
    ("objective", "Objective weighting", ""),
    ("years", "Years", "years"),
)
SNAPSHOT_KINDS = ("dates", "numbers", "text")

#: Limits on an uploaded workbook (GK B E2). Each refusal names its limit.
LIMITS = {
    "uncompressed_bytes": 512 * 1024 * 1024,
    "zip_members": 400,
    "sheets": 40,
    "rows": 1_048_576,
    "columns": 16_384,
    "cells": 60_000_000,
}


class ImportFormatError(_ContractFormatError):
    """The file is not a workbook this reader can open; the message is plain language."""


class ImportLimitError(ImportFormatError):
    """The workbook passes one of `LIMITS`."""

    def __init__(self, limit: str, value: int):
        self.limit = limit
        self.value = value
        super().__init__(
            f"The workbook is too large: {limit.replace('_', ' ')} {value:,} "
            f"exceeds {LIMITS[limit]:,}."
        )


@dataclass(frozen=True)
class Column:
    """One column as a person reads it. `scale`: shown = stored × scale."""

    key: str
    label: str
    unit: str = ""
    scale: float = 1.0
    kind: str = "number"  # number | int | bool | enum | text | bus | carrier
    options: tuple[str, ...] = ()
    visible: bool = True


# ── units ────────────────────────────────────────────────────────────────────

_PER_KILO = re.compile(r"^EUR/kWh?(/yr)?$")


def scale_for_unit(unit: str) -> float:
    """
    The one factor a shown unit allows (the catalogue's unit table): money per kW or
    kWh is stored per MW or MWh, a percentage as a fraction, everything else as shown.
    """
    if _PER_KILO.match(unit or ""):
        return 1e-3
    if unit == "%":
        return 100.0
    return 1.0


def scale_ok(unit: str, scale: Any) -> bool:
    """A hidden factor is trusted only when it is the unit table's factor for the shown unit."""
    if isinstance(scale, bool) or not isinstance(scale, int | float):
        return False
    if not math.isfinite(scale) or scale <= 0:
        return False
    return math.isclose(float(scale), scale_for_unit(unit), rel_tol=1e-12)


def rescale(value: float, factor: float) -> float:
    """
    `value × factor` as a decimal shift, so a value shown per kW and read back per MW
    is the stored float again (the round trip compares exactly).
    """
    from decimal import Decimal

    if factor == 1.0:
        return value
    return float(Decimal(repr(float(value))) * Decimal(repr(float(factor))))


# ── cells ────────────────────────────────────────────────────────────────────

#: A text cell starting with one of these could be read as a formula by a spreadsheet.
FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")
_ILLEGAL = re.compile(r"[\000-\010]|[\013-\014]|[\016-\037]")


def safe_text(value: str) -> str:
    """A string an .xlsx cell can hold: control characters openpyxl refuses become U+FFFD."""
    return _ILLEGAL.sub("�", value)


def put(ws, row: int, col: int, value: Any):
    """
    Write one cell that is never a formula. Text goes in as a string cell, whatever it
    starts with, so a name like `=HYPERLINK(...)` stays text in Excel (E7).
    """
    cell = ws.cell(row=row, column=col)
    if isinstance(value, str):
        value = safe_text(value)
        cell.value = value
        if value.startswith(FORMULA_LEADS):
            cell.data_type = "s"
    else:
        cell.value = value
    return cell


def humanise(key: str) -> str:
    """A label for a column with no catalogue entry (shown only in a hidden column)."""
    return key.replace("_", " ").strip().capitalize() or key


#: Text that must stay text in a cell, whoever writes it (GK B E7): this module's
#: `put` and the app's `services/study/workbook_view.text` are both tested on it.
INERT_TEXT_CASES = (
    "=1+1",
    '=HYPERLINK("http://x","y")',
    "+1+cmd",
    "-2+3+cmd",
    "@SUM(A1)",
    "\t=1",
    "\r=1",
    "=",
    "-",
    "-5",
    "+3.2",
    "plain",
    "",
    "a\x01b",
)

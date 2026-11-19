"""
Read the Excel input workbook (IMPORTS-1) into a network, its sidecars and a report.

`read_excel(paths, options)` returns an `ImportResult`. It never drops a row
silently: a row, column, sheet or cell it cannot use becomes a `report.unmapped`
entry with the reason; a blank it fills with the model default becomes a
`report.assumed` entry.

It reads the input workbook `excel_export.write_workbook` writes (About + Horizon
and finance), and the older layouts today's /import/excel reads: PyPSA's own
export (a `network` sheet) and raw-named component sheets (`buses`, `generators`, …).

Every workbook is untrusted, the hidden rows included (GK B E1–E3): a model name
must be an input attribute of its class or on the caller's allow-list, a factor
must match its unit, a formula is never evaluated, and the size of the file is
capped (`excel_layout.LIMITS`).
"""

from __future__ import annotations

import json
import logging
import math
import pathlib
import re
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd
import pypsa
from openpyxl.utils import get_column_letter

from gridspine.ingest import excel_layout as L
from gridspine.ingest.import_result import Assumed, ImportReport, ImportResult, Unmapped

logger = logging.getLogger(__name__)

FORMAT_ID = "excel"


_MAX_JSON_CELL = 1_000_000
_BOOL_TEXT = {"true": True, "false": False}
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?\Z")


# ── reading the file safely (E2) ─────────────────────────────────────────────


def _check_zip(path: pathlib.Path) -> None:
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


def _declared_within_limits(ws) -> None:
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


def _capped(rows):
    """Each (row number, row), refusing past the row and column limits."""
    for r, row in enumerate(rows, start=1):
        if r > L.LIMITS["rows"]:
            raise L.ImportLimitError("rows", r)
        if len(row) > L.LIMITS["columns"]:
            raise L.ImportLimitError("columns", len(row))
        yield r, row


class _Sheets:
    """Each sheet's cell values (cached values; formulas never evaluated), counted against the limits."""

    def __init__(self, path: pathlib.Path):
        import openpyxl

        _check_zip(path)
        try:
            formulas = openpyxl.load_workbook(path, read_only=True, data_only=False)
            values = openpyxl.load_workbook(path, read_only=True, data_only=True)
        except L.ImportFormatError:
            raise
        except Exception as exc:  # noqa: BLE001 - any parser failure is one refusal
            logger.warning("Excel workbook refused as unreadable", exc_info=True)
            raise L.ImportFormatError(
                "The file is not a readable Excel workbook."
            ) from exc
        try:
            if len(values.sheetnames) > L.LIMITS["sheets"]:
                raise L.ImportLimitError("sheets", len(values.sheetnames))
            self.names = list(values.sheetnames)
            self.hidden = {
                ws.title for ws in values.worksheets if ws.sheet_state != "visible"
            }
            self.rows: dict[str, list[tuple]] = {}
            self.formula_cells: dict[str, set[tuple[int, int]]] = {}
            self.reported: set[tuple[str, int, int]] = set()
            for name in self.names:
                _declared_within_limits(values[name])
                values[name].reset_dimensions()
                formulas[name].reset_dimensions()
            cells = scanned = 0
            for name in self.names:
                f_cells: set[tuple[int, int]] = set()
                for r, row in _capped(formulas[name].iter_rows()):
                    scanned += len(row)
                    if scanned > L.LIMITS["cells"]:
                        raise L.ImportLimitError("cells", scanned)
                    for c, cell in enumerate(row, start=1):
                        if getattr(cell, "data_type", None) == "f":
                            f_cells.add((r, c))
                self.formula_cells[name] = f_cells
                rows = []
                for r, row in _capped(values[name].iter_rows(values_only=True)):
                    cells += len(row)
                    if cells > L.LIMITS["cells"]:
                        raise L.ImportLimitError("cells", cells)
                    rows.append(tuple(row))
                while rows and all(v is None for v in rows[-1]):
                    rows.pop()
                self.rows[name] = rows
        except L.ImportFormatError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Excel workbook refused as unreadable", exc_info=True)
            raise L.ImportFormatError(
                "The file is not a readable Excel workbook."
            ) from exc
        finally:
            formulas.close()
            values.close()

    def cell(self, sheet: str, r: int, c: int) -> Any:
        rows = self.rows.get(sheet, [])
        if r - 1 < len(rows) and c - 1 < len(rows[r - 1]):
            return rows[r - 1][c - 1]
        return None

    def uncached_formula(self, sheet: str, r: int, c: int) -> bool:
        """
        Whether the cell is a formula Excel saved no value for; such a cell is
        reported where it is read, and `unread_formulas` lists the rest.
        """
        hit = (r, c) in self.formula_cells.get(sheet, ()) and self.cell(
            sheet, r, c
        ) is None
        if hit:
            self.reported.add((sheet, r, c))
        return hit

    def unread_formulas(self) -> list[tuple[str, int, int]]:
        return sorted(
            (sheet, r, c)
            for sheet, cells in self.formula_cells.items()
            for r, c in cells
            if self.cell(sheet, r, c) is None and (sheet, r, c) not in self.reported
        )


# ── values (E3) ──────────────────────────────────────────────────────────────


class _Bad(ValueError):
    """One cell the reader cannot use; the message is the report's reason."""


def _attr_type(defaults: pd.DataFrame, key: str) -> type | None:
    if key not in defaults.index:
        return None
    typ = defaults.at[key, "typ"]
    return typ if typ in (bool, int, float, str) else str


def _allowed_inf(defaults: pd.DataFrame, key: str) -> float | None:
    """
    The one infinity a column accepts: its model default when that is infinite
    (+inf for p_nom_max and the like), else none.
    """
    d = defaults["default"].get(key)
    return d if isinstance(d, float) and math.isinf(d) else None


def _number(v: Any, *, inf: float | None = None) -> float:
    if isinstance(v, bool):
        raise _Bad("a TRUE/FALSE value in a number column")
    if isinstance(v, int | float):
        f = float(v)
    elif isinstance(v, str):
        try:
            if "_" in v:
                raise ValueError(v)
            f = float(v.strip())
        except ValueError:
            raise _Bad(f"'{v[:40]}' is not a number") from None
    else:
        raise _Bad(f"'{str(v)[:40]}' is not a number")
    if math.isnan(f):
        raise _Bad("not a number (NaN)")
    if math.isinf(f) and f != inf:
        raise _Bad(
            "infinity is not allowed here"
            if inf is None
            else f"only {inf} is allowed here"
        )
    return f


def _weighting(v: Any) -> float:
    f = _number(v)
    if f < 0:
        raise _Bad("a weighting cannot be negative")
    return f


def _convert(
    v: Any, typ: type | None, *, scale: float = 1.0, inf: float | None = None
) -> Any:
    """One cell as the model stores it, or `_Bad` with the reason."""
    if typ is bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str) and v.strip().lower() in _BOOL_TEXT:
            return _BOOL_TEXT[v.strip().lower()]
        raise _Bad(f"'{str(v)[:40]}' is not TRUE or FALSE")
    if typ is float:
        f = _number(v, inf=inf)
        return f if math.isinf(f) else L.rescale(f, 1.0 / scale)
    if typ is int:
        f = _number(v)
        if not f.is_integer():
            raise _Bad(f"{f} is not a whole number")
        return int(f)
    if typ is str:
        if isinstance(v, bool):
            raise _Bad("a TRUE/FALSE value in a text column")
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return str(v)
    # A custom column (no model type): kept as the cell holds it. An .xlsx cell
    # cannot hold NaN, and a typed "nan" stays text.
    return v


# ── the input workbook ───────────────────────────────────────────────────────


class _Reader:
    def __init__(
        self, sheets: _Sheets, report: ImportReport, options: Mapping[str, Any]
    ):
        self.s = sheets
        self.report = report
        custom = options.get("custom_columns") or {}
        self.custom = {cls: set(cols) for cls, cols in custom.items()}
        # The catalogue's unit per class and column: the shown unit must be it (E1),
        # so a unit and its factor changed together cannot rescale a value.
        self.units = {
            cls: {c.key: c.unit for c in cols}
            for cls, cols in (options.get("columns") or {}).items()
        }
        self.n = pypsa.Network()
        self.uploaded: dict = {}
        self._step_keys: dict[int, str] = {}
        self.exact: dict[tuple[str, str, str, str], tuple[float, float, int]] = {}
        self._exact_seen: set[tuple[str, str, str, str]] = set()

    # the hidden Exact values sheet (GK B ask 1, X2-X5): untrusted like every sheet
    def load_exact(self) -> None:
        sheet = L.EXACT
        if sheet not in self.s.rows:
            return  # deleted in Excel, or nothing needed it: the cells are rescaled
        if sheet not in self.s.hidden:
            self.unmapped(
                sheet,
                f"'{sheet}' is a reserved name for the workbook's hidden sheet; "
                "this visible sheet was not read",
            )
            return
        rows = self.s.rows[sheet]
        formulas = self.s.formula_cells.get(sheet, set())
        width = len(L.EXACT_COLUMNS)
        duplicates: set[tuple[str, str, str, str]] = set()
        for r in range(2, len(rows) + 1):
            cells = [self.s.cell(sheet, r, c) for c in range(1, width + 1)]
            if all(v is None for v in cells):
                continue
            item = f"{sheet} row {r}"
            if any((r, c) in formulas for c in range(1, width + 1)):
                self.unmapped(item, "a formula in the exact values; ignored")
                self.s.reported.update((sheet, r, c) for c in range(1, width + 1))
                continue
            where, name, param, step, shown, stored = cells
            name = "" if name is None else name
            step = "" if step is None else step
            fs, ft = L.exact_text(shown), L.exact_text(stored)
            if (
                not all(isinstance(x, str) for x in (where, name, param, step))
                or fs is None
                or ft is None
            ):
                self.unmapped(item, "not a readable exact value; ignored")
                continue
            key = (where, name, param, step)
            if key in self.exact or key in duplicates:
                self.unmapped(
                    item, "a second exact value for the same cell; both ignored"
                )
                duplicates.add(key)
                self.exact.pop(key, None)
                continue
            self.exact[key] = (fs, ft, r)

    def exact_value(self, key, v: Any, scale: float) -> float | None:
        """
        The stored float for a scaled cell left as the writer wrote it, else None
        (the visible value is rescaled). The entry counts only when the cell still
        equals the float written and the stored float still shows as that float.
        """
        entry = self.exact.get(key)
        if entry is None:
            return None
        self._exact_seen.add(key)
        shown, stored, r = entry
        if isinstance(v, bool) or not isinstance(v, int | float) or float(v) != shown:
            return None  # edited in Excel: the visible value wins
        if scale == 1.0 or L.rescale(stored, scale) != shown:
            self.unmapped(
                f"{L.EXACT} row {r}",
                "the exact value does not show as its cell; ignored",
            )
            return None
        return stored

    def unused_exact(self) -> None:
        for key, (_, _, r) in self.exact.items():
            if key not in self._exact_seen:
                self.unmapped(
                    f"{L.EXACT} row {r}", "no cell matches this exact value; ignored"
                )

    def unmapped(self, item: str, reason: str) -> None:
        self.report.unmapped.append(Unmapped(item, reason))

    def _unit_ok(self, shown, cls, key, unit, scale, *, row: int) -> bool:
        expected = self.units.get(cls, {}).get(key)
        if expected is not None and str(unit) != expected:
            self.unmapped(
                shown, f"the unit '{unit}' is not this parameter's unit '{expected}'"
            )
            return False
        if not L.scale_ok(str(unit), scale):
            self.unmapped(
                shown,
                f"the hidden unit factor (row {row}) does not match the unit '{unit}'",
            )
            return False
        return True

    def assumed(self, asset: str, parameter: str, value: Any, why: str) -> None:
        """
        A blank the model's default fills. An infinite default is written as None
        with "no limit" in the reason, so the report stays JSON-finite.
        """
        if isinstance(value, float) and math.isinf(value):
            value, why = None, f"{why} (no limit)"
        elif hasattr(value, "item"):
            value = value.item()
        self.report.assumed.append(Assumed(asset, parameter, value, why))

    # columns: the hidden rows checked (E1)
    def _columns(
        self, sheet: str, cls: str, defaults: pd.DataFrame
    ) -> list[tuple[int, str, str, float]]:
        rows = self.s.rows[sheet]
        width = max((len(r) for r in rows), default=0)
        allowed = set(
            defaults.index[
                defaults["static"]
                & defaults["status"].astype(str).str.startswith("Input")
            ]
        )
        allowed |= self.custom.get(cls, set())
        out: list[tuple[int, str, str, float]] = []
        seen: set[str] = set()
        for c in range(1, width + 1):
            label = self.s.cell(sheet, L.ROW_LABEL, c)
            key = self.s.cell(sheet, L.ROW_KEY, c)
            unit = self.s.cell(sheet, L.ROW_UNIT, c) or ""
            scale = self.s.cell(sheet, L.ROW_SCALE, c)
            shown = f"{sheet} › {label or key or f'column {c}'}"
            has_data = any(
                len(r) >= c and r[c - 1] is not None
                for r in rows[L.FIRST_DATA_ROW - 1 :]
            )
            if key is None:
                if has_data or label is not None:
                    self.unmapped(
                        shown,
                        "the hidden model-name row (row 3) is empty for this column",
                    )
                continue
            key = str(key)
            if key not in allowed and not (
                key == L.SIZE_UNIT_KEY and cls in L.SIZE_BUS
            ):
                self.unmapped(shown, f"'{key[:60]}' is not a parameter of {sheet}")
                continue
            if not self._unit_ok(shown, cls, key, unit, scale, row=L.ROW_SCALE):
                continue
            if key in seen:
                self.unmapped(shown, "a second column for the same parameter")
                continue
            seen.add(key)
            out.append((c, key, str(label or key), L.scale_for_unit(str(unit))))
        if "name" not in seen:
            self.unmapped(sheet, "no Name column; the sheet's rows were not read")
            return []
        return out

    def component(self, sheet: str, cls: str) -> None:
        list_name = L.LIST_NAME[cls]
        defaults = self.n.components[list_name].defaults
        cols = self._columns(sheet, cls, defaults)
        if not cols:
            return
        name_col = next(c for c, k, _, _ in cols if k == "name")
        records: dict[str, dict[str, Any]] = {}
        size_units: dict[str, str] = {}
        for r in range(L.FIRST_DATA_ROW, len(self.s.rows[sheet]) + 1):
            row = self.s.rows[sheet][r - 1]
            if all(v is None for v in row):
                continue
            name = self.s.cell(sheet, r, name_col)
            if name is None:
                self.unmapped(f"{sheet} row {r}", "the row has values but no name")
                continue
            try:
                name = _convert(name, str, scale=1.0)
            except _Bad as exc:
                self.unmapped(f"{sheet} row {r}", f"name: {exc}")
                continue
            if name in records:
                self.unmapped(
                    f"{sheet} row {r} ({name})", "a second row with the same name"
                )
                continue
            values: dict[str, Any] = {}
            for c, key, label, scale in cols:
                if key == "name":
                    continue
                if self.s.uncached_formula(sheet, r, c):
                    self.unmapped(
                        f"{sheet} › {name} › {label}",
                        "a formula with no saved value; "
                        "open and save the file in Excel, or type the value",
                    )
                    continue
                v = self.s.cell(sheet, r, c)
                if v is None:
                    continue
                if key == L.SIZE_UNIT_KEY:
                    size_units[name] = str(v)
                    continue
                try:
                    exact = self.exact_value((sheet, name, key, ""), v, scale)
                    values[key] = (
                        exact
                        if exact is not None
                        else _convert(
                            v,
                            _attr_type(defaults, key),
                            scale=scale,
                            inf=_allowed_inf(defaults, key),
                        )
                    )
                except _Bad as exc:
                    self.unmapped(
                        f"{sheet} › {name} › {label}",
                        f"{exc}; the model default is kept",
                    )
            records[name] = values
        self._add(sheet, cls, records, cols, defaults)
        self._check_size_units(sheet, cls, size_units)

    def _add(self, sheet, cls, records, cols, defaults) -> None:
        if not records:
            self.report.counts[sheet] = 0
            return
        names = list(records)
        model_keys = [k for _, k, _, _ in cols if k in defaults.index and k != "name"]
        # Columns every row fills go in with the add, so PyPSA checks them (a bus that
        # does not exist warns); the rest are set row by row below.
        full = {
            k: [records[nm][k] for nm in names]
            for k in model_keys
            if all(k in records[nm] for nm in names)
        }
        try:
            self.n.add(cls, names, **full)
        except Exception as exc:  # noqa: BLE001 - one bad row refuses the batch; find it
            added = []
            for name in names:
                try:
                    self.n.add(cls, name, **{k: records[name][k] for k in full})
                    added.append(name)
                except Exception as row_exc:  # noqa: BLE001
                    self.unmapped(
                        f"{sheet} › {name}", f"the model refused the row: {row_exc}"
                    )
            logger.info("batch add of %s fell back to rows: %s", cls, exc)
            names = added
        table = getattr(self.n, L.LIST_NAME[cls])
        for _, key, label, _ in cols:
            if key in ("name", L.SIZE_UNIT_KEY) or key in full:
                continue
            have = {nm: records[nm][key] for nm in names if key in records[nm]}
            if have:
                if key not in table.columns:
                    table[key] = pd.Series(
                        dtype=object
                        if not all(
                            isinstance(v, int | float) and not isinstance(v, bool)
                            for v in have.values()
                        )
                        else float
                    )
                for nm, v in have.items():
                    table.at[nm, key] = v
            blanks = [nm for nm in names if key not in records[nm]]
            if blanks and key in defaults.index:
                d = defaults.at[key, "default"]
                if not (
                    d is None or (isinstance(d, float) and math.isnan(d)) or d == ""
                ):
                    who = (
                        blanks[0]
                        if len(blanks) == 1
                        else f"{len(blanks)} {sheet.lower()}"
                    )
                    self.assumed(who, label, d, "left empty: the model default")
        self.report.counts[sheet] = len(names)

    def _check_size_units(self, sheet, cls, size_units) -> None:
        if not size_units:
            return
        col = L.SIZE_BUS[cls]
        table = getattr(self.n, L.LIST_NAME[cls])
        for name, unit in size_units.items():
            if name not in table.index:
                continue  # the model refused the row; already reported
            bus = table.at[name, col]
            bus_unit = self.n.buses.at[bus, "unit"] if bus in self.n.buses.index else ""
            expected = L.size_unit(cls, bus_unit)
            if unit != expected:
                self.report.warnings.append(
                    f"{sheet} › {name}: size unit '{unit[:20]}' but its node counts in '{expected}'; "
                    f"the size is read in {expected}."
                )

    # time series and horizon
    def horizon(self) -> dict[str, Any]:
        sheet = L.HORIZON
        if sheet not in self.s.rows:
            self.unmapped(
                sheet,
                "the sheet is missing; one time step and no investment periods are assumed",
            )
            return {}
        rows = self.s.rows[sheet]
        settings = {
            k: (label, unit, scale) for k, label, unit, scale in L.HORIZON_SETTINGS
        }
        solver: dict[str, Any] = {}
        period_header = None
        seen: set[str] = set()
        for r, row in enumerate(rows, start=1):
            key = self.s.cell(sheet, r, 4)
            if key in settings:
                label, unit, scale = settings[key]
                v = self.s.cell(sheet, r, 2)
                shown = f"{sheet} › {label}"
                if key in seen:
                    self.unmapped(
                        f"{sheet} row {r}", f"a second row for the setting '{label}'"
                    )
                    continue
                seen.add(key)
                if v is None:
                    if key == "investment_periods":
                        solver[key] = []
                    continue
                try:
                    if key == "discount_rate":
                        if (self.s.cell(sheet, r, 3) or "") != unit:
                            raise _Bad(f"the unit must be '{unit}'")
                        exact = self.exact_value((sheet, "", key, ""), v, scale)
                        solver[key] = (
                            exact
                            if exact is not None
                            else _convert(v, float, scale=scale)
                        )
                    elif key == "multi_investment_periods":
                        solver[key] = _convert(v, bool, scale=1.0)
                    elif key == "investment_periods":
                        parts = [
                            p for p in str(v).replace(";", ",").split(",") if p.strip()
                        ]
                        solver[key] = [_convert(p, int, scale=1.0) for p in parts]
                    elif key == "snapshot_kind":
                        if v not in L.SNAPSHOT_KINDS:
                            raise _Bad(
                                f"'{str(v)[:20]}' is not one of {', '.join(L.SNAPSHOT_KINDS)}"
                            )
                        solver[key] = v
                except _Bad as exc:
                    self.unmapped(shown, str(exc))
            elif [self.s.cell(sheet, r, j) for j in (1, 2, 3)] == [
                k for k, _, _ in L.PERIOD_COLUMNS
            ]:
                period_header = r
            elif period_header is None and key not in (None, "key"):
                self.unmapped(
                    f"{sheet} row {r}",
                    f"'{str(key)[:40]}' is not a setting this workbook uses",
                )
        if period_header is not None:
            periods: dict[int, tuple[float, float]] = {}
            for r in range(period_header + 1, len(rows) + 1):
                p, obj, yrs = (self.s.cell(sheet, r, j) for j in (1, 2, 3))
                if p is None and obj is None and yrs is None:
                    continue
                try:
                    period = _convert(p, int, scale=1.0)
                    if period in periods:
                        raise _Bad(f"a second row for period {period}")
                    periods[period] = (_weighting(obj), _weighting(yrs))
                except _Bad as exc:
                    self.unmapped(
                        f"{sheet} row {r}", f"investment period weighting: {exc}"
                    )
            self._periods = periods
        return solver

    def time_series(self, kind: str) -> None:
        sheet = L.TIME_SERIES
        if sheet not in self.s.rows:
            self.unmapped(sheet, "the sheet is missing; one time step is assumed")
            return
        rows = self.s.rows[sheet]
        width = max((len(r) for r in rows), default=0)
        time_cols: dict[str, int] = {}
        series_cols: list[tuple[int, str, str, str, float, Any]] = []
        seen_series: set[tuple[Any, str, str]] = set()
        for c in range(1, width + 1):
            cls, attr = (
                self.s.cell(sheet, L.TS_ROW_CLASS, c),
                self.s.cell(sheet, L.TS_ROW_ATTR, c),
            )
            label = self.s.cell(sheet, L.TS_ROW_LABEL, c)
            asset = self.s.cell(sheet, L.TS_ROW_ASSET, c)
            shown = f"{sheet} › {asset or ''} {label or attr or f'column {c}'}".replace(
                "  ", " "
            )
            if cls == "snapshots" and attr in {k for k, _, _ in L.SNAPSHOT_COLUMNS}:
                if attr in time_cols:
                    self.unmapped(shown, "a second column for the same time step data")
                else:
                    time_cols[attr] = c
                continue
            if cls is None and attr is None:
                if label is not None or asset is not None:
                    self.unmapped(
                        shown,
                        "the hidden model rows (5 and 6) are empty for this column",
                    )
                continue
            if cls not in L.LIST_NAME:
                self.unmapped(shown, f"'{str(cls)[:40]}' is not an asset type")
                continue
            defaults = self.n.components[L.LIST_NAME[cls]].defaults
            if (
                attr not in defaults.index
                or not defaults.at[attr, "varying"]
                or not str(defaults.at[attr, "status"]).startswith("Input")
            ):
                self.unmapped(
                    shown,
                    f"'{str(attr)[:40]}' is not a parameter that can change over time",
                )
                continue
            unit = self.s.cell(sheet, L.TS_ROW_UNIT, c) or ""
            scale = self.s.cell(sheet, L.TS_ROW_SCALE, c)
            if not self._unit_ok(shown, cls, attr, unit, scale, row=L.TS_ROW_SCALE):
                continue
            if asset is None:
                self.unmapped(shown, "no asset name in row 2")
                continue
            if (cls, str(attr), str(asset)) in seen_series:
                self.unmapped(shown, "a second column for the same profile")
                continue
            seen_series.add((cls, str(attr), str(asset)))
            series_cols.append(
                (
                    c,
                    cls,
                    str(attr),
                    str(asset),
                    L.scale_for_unit(str(unit)),
                    self.s.cell(sheet, L.TS_ROW_USER, c),
                )
            )
        self._series_cols = series_cols
        if "snapshot" not in time_cols:
            self.unmapped(sheet, "no Time column; one time step is assumed")
            return
        steps, periods, keep_rows = [], [], []
        for r in range(L.TS_FIRST_DATA_ROW, len(rows) + 1):
            v = self.s.cell(sheet, r, time_cols["snapshot"])
            if v is None:
                if any(x is not None for x in rows[r - 1]):
                    self.unmapped(f"{sheet} row {r}", "the row has values but no time")
                continue
            try:
                step = _snapshot(v, kind)
                period = (
                    _convert(
                        self.s.cell(sheet, r, time_cols["period"]),
                        int,
                        scale=1.0,
                    )
                    if "period" in time_cols
                    else None
                )
            except (_Bad, ValueError) as exc:
                self.unmapped(f"{sheet} row {r}", f"time step: {exc}")
                continue
            steps.append(step)
            periods.append(period)
            keep_rows.append(r)
            self._step_keys[r] = L.step_key(period, step)
        if not keep_rows:
            return
        if "period" in time_cols:
            index = pd.MultiIndex.from_arrays(
                [periods, steps], names=["period", "timestep"]
            )
        else:
            index = pd.Index(steps, name="snapshot")
        if index.duplicated().any():
            self.unmapped(
                sheet, "the same time step appears twice; the time series were not read"
            )
            return
        self.n.set_snapshots(index)
        for key, label, _ in L.SNAPSHOT_COLUMNS[2:]:
            if key not in time_cols:
                self.assumed(
                    "every time step", label, 1.0, "no column: the model default"
                )
                continue
            vals = []
            for r in keep_rows:
                try:
                    vals.append(_weighting(self.s.cell(sheet, r, time_cols[key])))
                except _Bad as exc:
                    self.unmapped(f"{sheet} row {r} › weighting", f"{exc}; 1 is used")
                    vals.append(1.0)
            self.n.snapshot_weightings[key] = vals
        self._keep_rows = keep_rows

    def series(self) -> None:
        rows = getattr(self, "_keep_rows", None)
        sheet = L.TIME_SERIES
        for c, cls, attr, asset, scale, user in getattr(self, "_series_cols", ()):
            table = getattr(self.n, L.LIST_NAME[cls])
            shown = f"{sheet} › {asset} › {attr}"
            if asset not in table.index:
                self.unmapped(
                    shown, f"no {L.SHEET_OF[cls]} row is named '{asset[:40]}'"
                )
                continue
            if rows is None:
                self.unmapped(shown, "no time steps were read")
                continue
            vals, blanks = [], 0
            static = table.at[asset, attr]
            try:
                for r in rows:
                    if self.s.uncached_formula(sheet, r, c):
                        raise _Bad(f"row {r} is a formula with no saved value")
                    v = self.s.cell(sheet, r, c)
                    if v is None:
                        blanks += 1
                        vals.append(static)
                        continue
                    exact = self.exact_value(
                        (sheet, f"{cls}:{asset}", attr, self._step_keys[r]), v, scale
                    )
                    vals.append(
                        exact if exact is not None else _convert(v, float, scale=scale)
                    )
            except _Bad as exc:
                self.unmapped(shown, f"{exc}; the column was not read")
                continue
            if blanks:
                self.assumed(
                    asset,
                    attr,
                    static,
                    f"{blanks} empty time step(s): the asset's fixed value",
                )
            getattr(self.n, f"{L.LIST_NAME[cls]}_t")[attr][asset] = pd.Series(
                vals, index=self.n.snapshots
            )
            if user is not None:
                self._flag_uploaded(shown, L.LIST_NAME[cls], attr, asset, user)

    def _flag_uploaded(self, shown, list_name, attr, asset, flag) -> None:
        extra = _json_dict(flag)
        if extra is None:
            self.report.warnings.append(
                f"{shown}: the hidden upload mark is unreadable; "
                "the series is kept but not marked as uploaded."
            )
            return
        s = getattr(self.n, f"{list_name}_t")[attr][asset]
        entry = {
            "index": L.snapshot_index_json(self.n.snapshots),
            "values": [None if not math.isfinite(v) else v for v in s.tolist()],
            **_index_name(extra),
        }
        self.uploaded.setdefault(list_name, {}).setdefault(attr, {})[asset] = entry

    def uploaded_sheet(self) -> None:
        sheet = L.UPLOADED
        if sheet not in self.s.rows:
            return
        rows = self.s.rows[sheet]
        width = max((len(r) for r in rows), default=0)
        for j in range(1, width + 1, 2):
            list_name = self.s.cell(sheet, L.UP_ROW_LIST, j)
            attr = self.s.cell(sheet, L.UP_ROW_ATTR, j)
            asset = self.s.cell(sheet, L.UP_ROW_ASSET, j)
            extra = _json_dict(self.s.cell(sheet, L.UP_ROW_META, j))
            shown = f"{sheet} › {asset} › {attr}"
            if (
                list_name not in {ln for _, _, ln in L.SHEETS}
                or attr is None
                or asset is None
                or extra is None
            ):
                self.unmapped(shown, "an uploaded profile with an unreadable header")
                continue
            index, values = [], []
            try:
                for r in range(L.UP_FIRST_DATA_ROW, len(rows) + 1):
                    i, v = self.s.cell(sheet, r, j), self.s.cell(sheet, r, j + 1)
                    if i is None:
                        continue
                    if isinstance(i, str) and i.startswith("["):
                        i = _json_list(i)
                    index.append(i)
                    values.append(None if v is None else _number(v))
            except _Bad as exc:
                self.unmapped(shown, f"uploaded profile: {exc}")
                continue
            self.uploaded.setdefault(list_name, {}).setdefault(str(attr), {})[
                str(asset)
            ] = {"index": index, "values": values, **_index_name(extra)}

    def meta(self) -> None:
        if self.s.cell(L.LISTS, 1, 4) != "$meta":
            return
        meta = _json_dict(self.s.cell(L.LISTS, 2, 4))
        if meta is None:
            self.report.warnings.append(
                "The provenance of uploaded profiles is unreadable and was not kept."
            )
            return
        self.uploaded["$meta"] = meta

    def periods(self, solver: Mapping[str, Any]) -> None:
        periods = getattr(self, "_periods", None)
        if not periods:
            return
        if not isinstance(self.n.snapshots, pd.MultiIndex):
            self.report.warnings.append(
                "Investment period weightings were given, but the time series has "
                "no Investment period column; the weightings were not used."
            )
            return
        have = set(self.n.investment_periods)
        for p, (obj, yrs) in periods.items():
            if p not in have:
                self.unmapped(
                    f"{L.HORIZON} › period {p}", "no time step belongs to this period"
                )
                continue
            self.n.investment_period_weightings.loc[p, "objective"] = obj
            self.n.investment_period_weightings.loc[p, "years"] = yrs


def _no_constant(name: str):
    raise ValueError(f"{name} is not JSON-ready")


def _json_dict(v: Any) -> dict | None:
    if not isinstance(v, str) or len(v) > _MAX_JSON_CELL:
        return None
    try:
        out = json.loads(v, parse_constant=_no_constant)
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def _json_list(v: str) -> list:
    try:
        out = json.loads(v, parse_constant=_no_constant) if len(v) < 200 else None
    except ValueError:
        out = None
    if not isinstance(out, list):
        raise _Bad(f"'{v[:40]}' is not a time step")
    return out


def _index_name(extra: Mapping) -> dict:
    name = extra.get("index_name")
    return {"index_name": name} if isinstance(name, str) else {}


def _snapshot(v: Any, kind: str) -> Any:
    if kind == "dates":
        if hasattr(v, "isoformat") and not isinstance(v, str):
            if getattr(v, "tzinfo", None) is not None:
                raise _Bad(f"'{v}' carries a time zone; give local time without one")
            return pd.Timestamp(v)
        if not isinstance(v, str) or not _ISO_DATE.match(v.strip()):
            raise _Bad(
                f"'{str(v)[:40]}' is not a date as YYYY-MM-DD or YYYY-MM-DD hh:mm[:ss], "
                "without a time zone"
            )
        return pd.Timestamp(v.strip())
    if kind == "numbers":
        f = _number(v)
        return int(f) if f.is_integer() else f
    return _convert(v, str, scale=1.0)


def _read_input_workbook(
    sheets: _Sheets, report: ImportReport, options: Mapping[str, Any]
) -> tuple[pypsa.Network, dict]:
    rd = _Reader(sheets, report, options)
    rd.load_exact()
    solver = rd.horizon()
    kind = solver.pop("snapshot_kind", "dates")
    known = {L.ABOUT, L.TIME_SERIES, L.HORIZON, L.LISTS, L.UPLOADED, L.EXACT} | set(
        L.CLASS_OF_SHEET
    )
    for name in sheets.names:
        if name not in known:
            rd.unmapped(name, "not a sheet this workbook uses; its rows were not read")
    rd.time_series(kind)
    for _, sheet, _ in L.SHEETS:
        if sheet in sheets.rows:
            rd.component(sheet, L.CLASS_OF_SHEET[sheet])
    rd.series()
    rd.periods(solver)
    rd.uploaded_sheet()
    rd.meta()
    rd.unused_exact()
    for sheet, r, c in sheets.unread_formulas():
        rd.unmapped(
            f"{sheet} › cell {get_column_letter(c)}{r}",
            "a formula with no saved value; open and save the file in Excel, or type the value",
        )
    report.counts[L.TIME_SERIES] = sum(
        len(getattr(rd.n, f"{ln}_t")[a].columns)
        for _, _, ln in L.SHEETS
        for a in getattr(rd.n, f"{ln}_t", {}).keys()
    )
    sidecars: dict[str, Any] = {
        L.SIDECAR_USER_TS: rd.uploaded,
        L.SIDECAR_SOLVER: solver,
    }
    return rd.n, sidecars


# ── the older layouts today's /import/excel reads (S1b, GK B E6) ─────────────

#: Raw-named component sheets: one header row of model names, then one row per asset.
_LEGACY_SHEETS = {
    "buses": "Bus",
    "generators": "Generator",
    "lines": "Line",
    "links": "Link",
    "loads": "Load",
    "storage_units": "StorageUnit",
    "stores": "Store",
    "transformers": "Transformer",
}


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def unescape_formula(value: Any) -> Any:
    """Undo the `'` today's /export/excel puts before a text that would read as a formula."""
    if (
        isinstance(value, str)
        and value.startswith("'")
        and value[1:].startswith(L.FORMULA_LEADS)
        and not _is_number(value[1:])
    ):
        return value[1:]
    return value


def _static_inputs(n: pypsa.Network, cls: str, custom: Mapping[str, set]) -> set[str]:
    """The columns a file may set on `cls` (E1): its static input attributes and the caller's allow-list."""
    d = n.components[L.LIST_NAME[cls]].defaults
    inputs = d.index[d["static"] & d["status"].astype(str).str.startswith("Input")]
    return set(inputs) | set(custom.get(cls, ()))


def _report_formulas(sheets: _Sheets, report: ImportReport) -> None:
    for sheet, r, c in sheets.unread_formulas():
        report.unmapped.append(
            Unmapped(
                f"{sheet} › cell {get_column_letter(c)}{r}",
                "a formula with no saved value; open and save the file in Excel, or type the value",
            )
        )


_NOT_INPUT = "not an input of this kind of asset; the column was not read"


def _check_native_cells(
    n: pypsa.Network, sheets: _Sheets, report: ImportReport, custom: Mapping[str, set]
) -> None:
    """
    E1 and E3 on PyPSA's own layout, checked on the cells as written: PyPSA's
    reader coerces them (TRUE to 1.0, "inf" to inf, "nan" to the default).

    A column that is not a static input of its class or on the allow-list is
    reported; a model output goes back to its default, anything else is dropped.
    An asset with a cell the model cannot take is reported and removed, never
    half-read. A time-series sheet for an attribute that is not a varying input is
    reported and not read; an asset's series with a bad cell is reported and dropped.
    """
    for cls, _sheet, ln in L.SHEETS:
        table = getattr(n, ln)
        rows = sheets.rows.get(ln)
        defaults = n.components[ln].defaults
        if rows and not table.empty:
            allowed = _static_inputs(n, cls, custom)
            headers = list(rows[0])
            for h in headers[1:]:
                if h is None or h in allowed:
                    continue
                report.unmapped.append(
                    Unmapped(f"{ln} › column {str(h)[:40]}", _NOT_INPUT)
                )
                if isinstance(h, str) and h in defaults.index:
                    table[h] = defaults.at[h, "default"]
                elif h in table.columns:
                    table.drop(columns=h, inplace=True)
            checked = [
                (c, h)
                for c, h in enumerate(headers)
                if c > 0 and isinstance(h, str) and h in allowed and h in defaults.index
            ]
            refused: list[str] = []
            for row in rows[1:]:
                try:
                    name = unescape_formula(_convert(row[0] if row else None, str))
                except _Bad:
                    continue  # PyPSA did not read it as an asset either
                if name not in table.index:
                    continue
                for c, h in checked:
                    if c >= len(row) or row[c] is None:
                        continue
                    try:
                        _convert(
                            unescape_formula(row[c]),
                            _attr_type(defaults, h),
                            inf=_allowed_inf(defaults, h),
                        )
                    except _Bad as exc:
                        report.unmapped.append(
                            Unmapped(f"{ln} › {name}", f"{h}: {exc}")
                        )
                        refused.append(name)
                        break
            if refused:
                n.remove(cls, refused)
        series = getattr(n, f"{ln}_t")
        for sheet in sheets.names:
            if not sheet.startswith(f"{ln}-"):
                continue
            attr = sheet[len(ln) + 1 :]
            varying_input = (
                attr in defaults.index
                and bool(defaults.at[attr, "varying"])
                and str(defaults.at[attr, "status"]).startswith("Input")
            )
            if not varying_input:
                report.unmapped.append(
                    Unmapped(
                        sheet,
                        "not an input time series of this kind of asset; "
                        "the sheet was not read",
                    )
                )
                if attr in series:
                    series[attr] = series[attr].iloc[:, :0]
                continue
            if _attr_type(defaults, attr) is not float or attr not in series:
                continue
            rows = sheets.rows.get(sheet) or []
            inf = _allowed_inf(defaults, attr)
            for c, asset in enumerate(rows[0] if rows else []):
                if c == 0 or asset is None:
                    continue
                asset = str(asset)
                for row in rows[1:]:
                    if c >= len(row) or row[c] is None:
                        continue
                    try:
                        _number(row[c], inf=inf)
                    except _Bad as exc:
                        report.unmapped.append(
                            Unmapped(f"{sheet} › {asset}", f"{attr}: {exc}")
                        )
                        series[attr] = series[attr].drop(columns=asset, errors="ignore")
                        break


def _read_native(
    path: pathlib.Path, sheets: _Sheets, report: ImportReport, custom: Mapping[str, set]
) -> pypsa.Network:
    """
    PyPSA's own Excel export, read by PyPSA as today's /import/excel does (cached
    values, the `'` undone). A column that is not a model input of its class and
    not on the caller's allow-list is dropped and reported (E1).
    """
    import openpyxl

    n = pypsa.Network()
    with tempfile.TemporaryDirectory() as directory:
        copy = pathlib.Path(directory) / "network.xlsx"
        wb = openpyxl.load_workbook(path, data_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str):
                        new = unescape_formula(cell.value)
                        if new != cell.value:
                            cell.value = new
                            cell.data_type = "s"
        wb.save(copy)
        wb.close()
        try:
            n.import_from_excel(str(copy), engine="openpyxl")
        except Exception as exc:  # noqa: BLE001 - PyPSA's reader failing is one refusal
            logger.warning("PyPSA Excel layout refused", exc_info=True)
            raise L.ImportFormatError(
                "The workbook has a 'network' sheet but PyPSA could not read it."
            ) from exc
    _check_native_cells(n, sheets, report, custom)
    for _cls, sheet, ln in L.SHEETS:
        table = getattr(n, ln)
        if table.empty:
            continue
        report.counts[sheet] = len(table)
    _report_formulas(sheets, report)
    return n


def _read_legacy_sheets(
    sheets: _Sheets, report: ImportReport, custom: Mapping[str, set]
) -> pypsa.Network:
    """Raw-named component sheets: header row, then one row per asset."""
    n = pypsa.Network()
    for sheet in sheets.names:
        cls = _LEGACY_SHEETS.get(sheet)
        if cls is None:
            report.unmapped.append(
                Unmapped(sheet, "not a component sheet; its rows were not read")
            )
            continue
        rows = sheets.rows[sheet]
        if not rows:
            continue
        defaults = n.components[L.LIST_NAME[cls]].defaults
        allowed = _static_inputs(n, cls, custom)
        headers = list(rows[0])
        name_col = headers.index("name") if "name" in headers else 0
        columns: dict[int, str] = {}
        for c, h in enumerate(headers):
            if c == name_col or h is None:
                continue
            if not isinstance(h, str) or h not in allowed:
                report.unmapped.append(
                    Unmapped(f"{sheet} › column {str(h)[:40]}", _NOT_INPUT)
                )
                continue
            columns[c] = h
        added = 0
        for r, row in enumerate(rows[1:], start=2):
            if all(v is None for v in row):
                continue
            raw = row[name_col] if name_col < len(row) else None
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                report.unmapped.append(
                    Unmapped(f"{sheet} row {r}", "the row has values but no name")
                )
                continue
            try:
                name = unescape_formula(_convert(raw, str))
            except _Bad as exc:
                report.unmapped.append(Unmapped(f"{sheet} row {r}", f"name: {exc}"))
                continue
            if name in getattr(n, L.LIST_NAME[cls]).index:
                # PyPSA skips a second add with a warning only: report it instead.
                report.unmapped.append(
                    Unmapped(
                        f"{sheet} row {r} ({name})", "a second row with the same name"
                    )
                )
                continue
            values: dict[str, Any] = {}
            bad = None
            for c, key in columns.items():
                v = row[c] if c < len(row) else None
                if v is None:
                    continue
                try:
                    values[key] = _convert(
                        unescape_formula(v),
                        _attr_type(defaults, key),
                        inf=_allowed_inf(defaults, key),
                    )
                except _Bad as exc:
                    bad = f"{key}: {exc}"
                    break
            if bad is not None:
                report.unmapped.append(Unmapped(f"{sheet} › {name}", bad))
                continue
            try:
                n.add(cls, name, **values)
                added += 1
            except Exception as exc:  # noqa: BLE001 - the model's refusal is the reason
                report.unmapped.append(
                    Unmapped(f"{sheet} › {name}", f"the model refused the row: {exc}")
                )
        report.counts[L.SHEET_OF[cls]] = report.counts.get(L.SHEET_OF[cls], 0) + added
    _report_formulas(sheets, report)
    return n


# ── entry point ──────────────────────────────────────────────────────────────


def read_excel(
    paths: Sequence[str | pathlib.Path] | str | pathlib.Path,
    options: Mapping[str, Any] | None = None,
) -> ImportResult:
    """
    Read one .xlsx file.

    `options["custom_columns"]`: per model class, the non-PyPSA columns the caller
    accepts (investment parts, eh_* tags); any other column the file names is
    reported, never set. `options["columns"]`: the catalogue the writer was given
    (`excel_layout.Column`s per class); a column whose shown unit is not the
    catalogue's is reported. Without it, only the unit/factor pair is checked.
    """
    options = options or {}
    if isinstance(paths, str | pathlib.Path):
        paths = [paths]
    paths = [pathlib.Path(p) for p in paths]
    if len(paths) != 1:
        raise L.ImportFormatError("Import one Excel workbook at a time.")
    path = paths[0]
    report = ImportReport(source_format=FORMAT_ID, source_files=[path.name])
    sheets = _Sheets(path)
    custom = {
        cls: set(cols) for cls, cols in (options.get("custom_columns") or {}).items()
    }
    if L.ABOUT in sheets.names and L.HORIZON in sheets.names:
        n, sidecars = _read_input_workbook(sheets, report, options)
    elif "network" in sheets.names:
        n, sidecars = _read_native(path, sheets, report, custom), {}
    elif set(sheets.names) & set(_LEGACY_SHEETS):
        n, sidecars = _read_legacy_sheets(sheets, report, custom), {}
    else:
        raise L.ImportFormatError(
            f"{path.name} has none of the sheets an Excel import reads. Download the "
            "template from the Import dialog and fill it in."
        )
    return ImportResult(network=n, sidecars=sidecars, report=report)

"""Read the Excel input workbook (IMPORTS-1) into a network, its sidecars and a report.

`read_excel(paths, options)` returns an `ImportResult`. It never drops a row
silently: a row, column, sheet or cell it cannot use becomes a `report.unmapped`
entry with the reason; a blank it fills with the model default becomes a
`report.assumed` entry.

Three layouts are read:

- the input workbook `excel_export.write_workbook` writes (About + Horizon and finance);
- PyPSA's own Excel export (a `network` sheet), as today's /import/excel reads it;
- the older raw-named sheets (`buses`, `generators`, …), as today's /import/excel
  reads them, except that a row that fails is reported instead of swallowed.

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
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import pypsa

from gridspine.ingest import excel_layout as L
from gridspine.ingest.excel_export import SIZE_BUS, SIZE_UNIT_KEY, snapshot_index_json

logger = logging.getLogger(__name__)

FORMAT_ID = "excel_workbook"


# ── the import contract (IMPORTS-1). The core session lands it as
# gridspine/ingest/import_result.py; until then this module carries the same shape.

@dataclass
class ImportReport:
    source_format: str
    source_files: list[str]
    counts: dict[str, int] = field(default_factory=dict)
    unmapped: list[tuple[str, str]] = field(default_factory=list)
    assumed: list[tuple[str, str, Any, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ImportResult:
    network: pypsa.Network
    sidecars: dict[str, Any]
    report: ImportReport


_LEGACY_SHEETS = {
    "buses": "Bus", "generators": "Generator", "lines": "Line", "links": "Link",
    "loads": "Load", "storage_units": "StorageUnit", "stores": "Store", "transformers": "Transformer",
}
_MAX_JSON_CELL = 1_000_000
_BOOL_TEXT = {"true": True, "false": False}


# ── reading the file safely (E2) ─────────────────────────────────────────────

def _check_zip(path: pathlib.Path) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, NotImplementedError, ValueError, EOFError) as exc:
        # A damaged header can claim a zip version or feature Python does not support.
        raise L.ImportFormatError("The file is not an Excel workbook (.xlsx).") from exc
    if len(infos) > L.LIMITS["zip_members"]:
        raise L.ImportLimitError("zip_members", len(infos))
    total = sum(i.file_size for i in infos)
    if total > L.LIMITS["uncompressed_bytes"]:
        raise L.ImportLimitError("uncompressed_bytes", total)


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
            raise L.ImportFormatError("The file is not a readable Excel workbook.") from exc
        try:
            if len(values.sheetnames) > L.LIMITS["sheets"]:
                raise L.ImportLimitError("sheets", len(values.sheetnames))
            self.names = list(values.sheetnames)
            self.hidden = {ws.title for ws in values.worksheets if ws.sheet_state != "visible"}
            self.rows: dict[str, list[tuple]] = {}
            self.formula_cells: dict[str, set[tuple[int, int]]] = {}
            cells = 0
            for name in self.names:
                f_cells: set[tuple[int, int]] = set()
                for r, row in enumerate(formulas[name].iter_rows(), start=1):
                    for c, cell in enumerate(row, start=1):
                        if getattr(cell, "data_type", None) == "f":
                            f_cells.add((r, c))
                self.formula_cells[name] = f_cells
                rows = []
                for r, row in enumerate(values[name].iter_rows(values_only=True), start=1):
                    if r > L.LIMITS["rows"]:
                        raise L.ImportLimitError("rows", r)
                    if len(row) > L.LIMITS["columns"]:
                        raise L.ImportLimitError("columns", len(row))
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
            raise L.ImportFormatError("The file is not a readable Excel workbook.") from exc
        finally:
            formulas.close()
            values.close()

    def cell(self, sheet: str, r: int, c: int) -> Any:
        rows = self.rows.get(sheet, [])
        if r - 1 < len(rows) and c - 1 < len(rows[r - 1]):
            return rows[r - 1][c - 1]
        return None

    def uncached_formula(self, sheet: str, r: int, c: int) -> bool:
        return (r, c) in self.formula_cells.get(sheet, ()) and self.cell(sheet, r, c) is None


# ── values (E3) ──────────────────────────────────────────────────────────────

class _Bad(ValueError):
    pass


def _attr_type(defaults: pd.DataFrame, key: str) -> type | None:
    if key not in defaults.index:
        return None
    typ = defaults.at[key, "typ"]
    return typ if typ in (bool, int, float, str) else str


def _inf_ok(defaults: pd.DataFrame, key: str) -> bool:
    if key not in defaults.index:
        return False
    d = defaults.at[key, "default"]
    return isinstance(d, float) and math.isinf(d)


def _number(v: Any, *, inf_ok: bool) -> float:
    if isinstance(v, bool):
        raise _Bad("a TRUE/FALSE value in a number column")
    if isinstance(v, int | float):
        f = float(v)
    elif isinstance(v, str):
        try:
            f = float(v.strip())
        except ValueError:
            raise _Bad(f"'{v[:40]}' is not a number") from None
    else:
        raise _Bad(f"'{str(v)[:40]}' is not a number")
    if math.isnan(f):
        raise _Bad("not a number (NaN)")
    if math.isinf(f) and not inf_ok:
        raise _Bad("infinity is not allowed here")
    return f


def _convert(v: Any, typ: type | None, *, scale: float, inf_ok: bool) -> Any:
    """One cell as the model stores it, or `_Bad` with the reason."""
    if typ is bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str) and v.strip().lower() in _BOOL_TEXT:
            return _BOOL_TEXT[v.strip().lower()]
        raise _Bad(f"'{str(v)[:40]}' is not TRUE or FALSE")
    if typ is float:
        f = _number(v, inf_ok=inf_ok)
        return f if math.isinf(f) else L.rescale(f, 1.0 / scale)
    if typ is int:
        f = _number(v, inf_ok=False)
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
    # A custom column (no model type): kept as the cell holds it, NaN refused.
    if isinstance(v, float) and math.isnan(v):
        raise _Bad("not a number (NaN)")
    return v


# ── the input workbook ───────────────────────────────────────────────────────

class _Reader:
    def __init__(self, sheets: _Sheets, report: ImportReport, custom: Mapping[str, Sequence[str]]):
        self.s = sheets
        self.report = report
        self.custom = {cls: set(cols) for cls, cols in custom.items()}
        self.n = pypsa.Network()
        self.uploaded: dict = {}

    def unmapped(self, item: str, reason: str) -> None:
        self.report.unmapped.append((item, reason))

    # columns: the hidden rows checked (E1)
    def _columns(self, sheet: str, cls: str, defaults: pd.DataFrame) -> list[tuple[int, str, str, float]]:
        rows = self.s.rows[sheet]
        width = max((len(r) for r in rows), default=0)
        allowed = set(defaults.index[defaults["static"] & defaults["status"].astype(str).str.startswith("Input")])
        allowed |= self.custom.get(cls, set())
        out: list[tuple[int, str, str, float]] = []
        seen: set[str] = set()
        for c in range(1, width + 1):
            label = self.s.cell(sheet, L.ROW_LABEL, c)
            key = self.s.cell(sheet, L.ROW_KEY, c)
            unit = self.s.cell(sheet, L.ROW_UNIT, c) or ""
            scale = self.s.cell(sheet, L.ROW_SCALE, c)
            shown = f"{sheet} › {label or key or f'column {c}'}"
            has_data = any(len(r) >= c and r[c - 1] is not None for r in rows[L.FIRST_DATA_ROW - 1:])
            if key is None:
                if has_data or label is not None:
                    self.unmapped(shown, "the hidden model-name row (row 3) is empty for this column")
                continue
            key = str(key)
            if key != SIZE_UNIT_KEY and key not in allowed:
                self.unmapped(shown, f"'{key[:60]}' is not a parameter of {sheet}")
                continue
            if not L.scale_ok(str(unit), scale):
                self.unmapped(shown, f"the hidden unit factor (row 4) does not match the unit '{unit}'")
                continue
            if key in seen:
                self.unmapped(shown, "a second column for the same parameter")
                continue
            seen.add(key)
            out.append((c, key, str(label or key), float(scale)))
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
                name = _convert(name, str, scale=1.0, inf_ok=False)
            except _Bad as exc:
                self.unmapped(f"{sheet} row {r}", f"name: {exc}")
                continue
            if name in records:
                self.unmapped(f"{sheet} row {r} ({name})", "a second row with the same name")
                continue
            values: dict[str, Any] = {}
            for c, key, label, scale in cols:
                if key == "name":
                    continue
                if self.s.uncached_formula(sheet, r, c):
                    self.unmapped(f"{sheet} › {name} › {label}", "a formula with no saved value; "
                                  "open and save the file in Excel, or type the value")
                    continue
                v = self.s.cell(sheet, r, c)
                if v is None:
                    continue
                if key == SIZE_UNIT_KEY:
                    size_units[name] = str(v)
                    continue
                try:
                    values[key] = _convert(v, _attr_type(defaults, key), scale=scale,
                                           inf_ok=_inf_ok(defaults, key))
                except _Bad as exc:
                    self.unmapped(f"{sheet} › {name} › {label}", f"{exc}; the model default is kept")
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
        full = {k: [records[nm][k] for nm in names] for k in model_keys
                if all(k in records[nm] for nm in names)}
        try:
            self.n.add(cls, names, **full)
        except Exception as exc:  # noqa: BLE001 - one bad row refuses the batch; find it
            added = []
            for name in names:
                try:
                    self.n.add(cls, name, **{k: records[name][k] for k in full})
                    added.append(name)
                except Exception as row_exc:  # noqa: BLE001
                    self.unmapped(f"{sheet} › {name}", f"the model refused the row: {row_exc}")
            logger.info("batch add of %s fell back to rows: %s", cls, exc)
            names = added
        table = getattr(self.n, L.LIST_NAME[cls])
        for _, key, label, _ in cols:
            if key in ("name", SIZE_UNIT_KEY) or key in full:
                continue
            have = {nm: records[nm][key] for nm in names if key in records[nm]}
            if have:
                if key not in table.columns:
                    table[key] = pd.Series(dtype=object if not all(
                        isinstance(v, int | float) and not isinstance(v, bool) for v in have.values()) else float)
                for nm, v in have.items():
                    table.at[nm, key] = v
            blanks = [nm for nm in names if key not in records[nm]]
            if blanks and key in defaults.index:
                d = defaults.at[key, "default"]
                if not (d is None or (isinstance(d, float) and math.isnan(d)) or d == ""):
                    who = blanks[0] if len(blanks) == 1 else f"{len(blanks)} {sheet.lower()}"
                    self.report.assumed.append((who, label, d, "left empty: the model default"))
        self.report.counts[sheet] = len(names)

    def _check_size_units(self, sheet, cls, size_units) -> None:
        col = SIZE_BUS.get(cls)
        if not size_units or col is None:
            return
        table = getattr(self.n, L.LIST_NAME[cls])
        for name, unit in size_units.items():
            if name not in table.index:
                continue
            bus = table.at[name, col]
            bus_unit = self.n.buses.at[bus, "unit"] if bus in self.n.buses.index else ""
            expected = "t/h" if bus_unit == "t" else "MW"
            if unit != expected:
                self.report.warnings.append(
                    f"{sheet} › {name}: size unit '{unit[:20]}' but its node counts in '{expected}'; "
                    f"the size is read in {expected}.")

    # time series and horizon
    def horizon(self) -> dict[str, Any]:
        sheet = L.HORIZON
        if sheet not in self.s.rows:
            self.unmapped(sheet, "the sheet is missing; one time step and no investment periods are assumed")
            return {}
        rows = self.s.rows[sheet]
        settings = {k: (label, unit, scale) for k, label, unit, scale in L.HORIZON_SETTINGS}
        solver: dict[str, Any] = {}
        period_header = None
        for r, row in enumerate(rows, start=1):
            key = self.s.cell(sheet, r, 4)
            if key in settings:
                label, unit, scale = settings[key]
                v = self.s.cell(sheet, r, 2)
                shown = f"{sheet} › {label}"
                if v is None:
                    if key == "investment_periods":
                        solver[key] = []
                    continue
                try:
                    if key == "discount_rate":
                        if (self.s.cell(sheet, r, 3) or "") != unit:
                            raise _Bad(f"the unit must be '{unit}'")
                        solver[key] = _convert(v, float, scale=scale, inf_ok=False)
                    elif key == "multi_investment_periods":
                        solver[key] = _convert(v, bool, scale=1.0, inf_ok=False)
                    elif key == "investment_periods":
                        parts = [p for p in str(v).replace(";", ",").split(",") if p.strip()]
                        solver[key] = [_convert(p, int, scale=1.0, inf_ok=False) for p in parts]
                    elif key == "snapshot_kind":
                        if v not in L.SNAPSHOT_KINDS:
                            raise _Bad(f"'{str(v)[:20]}' is not one of {', '.join(L.SNAPSHOT_KINDS)}")
                        solver[key] = v
                except _Bad as exc:
                    self.unmapped(shown, str(exc))
            elif [self.s.cell(sheet, r, j) for j in (1, 2, 3)] == [k for k, _, _ in L.PERIOD_COLUMNS]:
                period_header = r
        if period_header is not None:
            periods: dict[int, tuple[float, float]] = {}
            for r in range(period_header + 1, len(rows) + 1):
                p, obj, yrs = (self.s.cell(sheet, r, j) for j in (1, 2, 3))
                if p is None and obj is None and yrs is None:
                    continue
                try:
                    periods[_convert(p, int, scale=1.0, inf_ok=False)] = (
                        _number(obj, inf_ok=False), _number(yrs, inf_ok=False))
                except _Bad as exc:
                    self.unmapped(f"{sheet} row {r}", f"investment period weighting: {exc}")
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
        for c in range(1, width + 1):
            cls, attr = self.s.cell(sheet, L.TS_ROW_CLASS, c), self.s.cell(sheet, L.TS_ROW_ATTR, c)
            label = self.s.cell(sheet, L.TS_ROW_LABEL, c)
            asset = self.s.cell(sheet, L.TS_ROW_ASSET, c)
            shown = f"{sheet} › {asset or ''} {label or attr or f'column {c}'}".replace("  ", " ")
            if cls == "snapshots" and attr in {k for k, _, _ in L.SNAPSHOT_COLUMNS}:
                time_cols[attr] = c
                continue
            if cls is None and attr is None:
                if label is not None or asset is not None:
                    self.unmapped(shown, "the hidden model rows (5 and 6) are empty for this column")
                continue
            if cls not in L.LIST_NAME:
                self.unmapped(shown, f"'{str(cls)[:40]}' is not an asset type")
                continue
            defaults = self.n.components[L.LIST_NAME[cls]].defaults
            if attr not in defaults.index or not defaults.at[attr, "varying"] \
                    or not str(defaults.at[attr, "status"]).startswith("Input"):
                self.unmapped(shown, f"'{str(attr)[:40]}' is not a parameter that can change over time")
                continue
            unit = self.s.cell(sheet, L.TS_ROW_UNIT, c) or ""
            scale = self.s.cell(sheet, L.TS_ROW_SCALE, c)
            if not L.scale_ok(str(unit), scale):
                self.unmapped(shown, f"the hidden unit factor (row 7) does not match the unit '{unit}'")
                continue
            if asset is None:
                self.unmapped(shown, "no asset name in row 2")
                continue
            series_cols.append((c, cls, str(attr), str(asset),
                                float(scale), self.s.cell(sheet, L.TS_ROW_USER, c)))
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
                period = (_convert(self.s.cell(sheet, r, time_cols["period"]), int, scale=1.0, inf_ok=False)
                          if "period" in time_cols else None)
            except (_Bad, ValueError) as exc:
                self.unmapped(f"{sheet} row {r}", f"time step: {exc}")
                continue
            steps.append(step)
            periods.append(period)
            keep_rows.append(r)
        if not keep_rows:
            return
        if "period" in time_cols:
            index = pd.MultiIndex.from_arrays([periods, steps], names=["period", "timestep"])
        else:
            index = pd.Index(steps, name="snapshot")
        if index.duplicated().any():
            self.unmapped(sheet, "the same time step appears twice; the time series were not read")
            return
        self.n.set_snapshots(index)
        for key in ("objective", "stores", "generators"):
            if key not in time_cols:
                continue
            vals = []
            for r in keep_rows:
                try:
                    vals.append(_number(self.s.cell(sheet, r, time_cols[key]), inf_ok=False))
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
                self.unmapped(shown, f"no {L.SHEET_OF[cls]} row is named '{asset[:40]}'")
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
                    vals.append(_convert(v, float, scale=scale, inf_ok=False))
            except _Bad as exc:
                self.unmapped(shown, f"{exc}; the column was not read")
                continue
            if blanks:
                self.report.assumed.append((asset, attr, static,
                                            f"{blanks} empty time step(s): the asset's fixed value"))
            getattr(self.n, f"{L.LIST_NAME[cls]}_t")[attr][asset] = pd.Series(vals, index=self.n.snapshots)
            if user is not None:
                self._flag_uploaded(shown, L.LIST_NAME[cls], attr, asset, user)

    def _flag_uploaded(self, shown, list_name, attr, asset, flag) -> None:
        extra = _json_dict(flag)
        if extra is None:
            self.report.warnings.append(f"{shown}: the hidden upload mark is unreadable; "
                                        "the series is kept but not marked as uploaded.")
            return
        s = getattr(self.n, f"{list_name}_t")[attr][asset]
        entry = {"index": snapshot_index_json(self.n.snapshots),
                 "values": [None if not math.isfinite(v) else v for v in s.tolist()], **_index_name(extra)}
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
            if list_name not in {ln for _, _, ln in L.SHEETS} or attr is None or asset is None or extra is None:
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
                    values.append(None if v is None else _number(v, inf_ok=False))
            except _Bad as exc:
                self.unmapped(shown, f"uploaded profile: {exc}")
                continue
            self.uploaded.setdefault(list_name, {}).setdefault(str(attr), {})[str(asset)] = {
                "index": index, "values": values, **_index_name(extra)}

    def meta(self) -> None:
        if L.LISTS not in self.s.rows or self.s.cell(L.LISTS, 1, 4) != "$meta":
            return
        meta = _json_dict(self.s.cell(L.LISTS, 2, 4))
        if meta is None:
            self.report.warnings.append("The provenance of uploaded profiles is unreadable and was not kept.")
            return
        self.uploaded["$meta"] = meta

    def periods(self, solver: Mapping[str, Any]) -> None:
        periods = getattr(self, "_periods", None)
        if not periods:
            return
        if not isinstance(self.n.snapshots, pd.MultiIndex):
            self.report.warnings.append("Investment period weightings were given, but the time series has "
                                        "no Investment period column; the weightings were not used.")
            return
        have = set(self.n.investment_periods)
        for p, (obj, yrs) in periods.items():
            if p not in have:
                self.unmapped(f"{L.HORIZON} › period {p}", "no time step belongs to this period")
                continue
            self.n.investment_period_weightings.loc[p, "objective"] = obj
            self.n.investment_period_weightings.loc[p, "years"] = yrs


def _json_dict(v: Any) -> dict | None:
    if not isinstance(v, str) or len(v) > _MAX_JSON_CELL:
        return None
    try:
        out = json.loads(v)
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def _json_list(v: str) -> list:
    try:
        out = json.loads(v) if len(v) < 200 else None
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
            return pd.Timestamp(v)
        if not isinstance(v, str):
            raise _Bad(f"'{v}' is not a date")
        return pd.Timestamp(v.strip())
    if kind == "numbers":
        f = _number(v, inf_ok=False)
        return int(f) if f.is_integer() else f
    return _convert(v, str, scale=1.0, inf_ok=False)


def _read_input_workbook(sheets: _Sheets, report: ImportReport, custom) -> tuple[pypsa.Network, dict]:
    rd = _Reader(sheets, report, custom)
    solver = rd.horizon()
    kind = solver.pop("snapshot_kind", "dates")
    known = {L.ABOUT, L.TIME_SERIES, L.HORIZON, L.LISTS, L.UPLOADED} | set(L.CLASS_OF_SHEET)
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
    report.counts[L.TIME_SERIES] = sum(len(getattr(rd.n, f"{ln}_t")[a].columns)
                                       for _, _, ln in L.SHEETS
                                       for a in getattr(rd.n, f"{ln}_t", {}).keys())
    sidecars: dict[str, Any] = {"user_timeseries": rd.uploaded, "solver_config": solver}
    return rd.n, sidecars


# ── the older layouts (E6) ───────────────────────────────────────────────────

def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def unescape_formula(value: Any) -> Any:
    """Undo the `'` today's /export/excel puts before a text that would read as a formula."""
    if isinstance(value, str) and value.startswith("'") and value[1:].startswith(L.FORMULA_LEADS) \
            and not _is_number(value[1:]):
        return value[1:]
    return value


def _read_native(path: pathlib.Path, report: ImportReport) -> pypsa.Network:
    """PyPSA's own Excel export, as today's /import/excel reads it (cached values, `'` undone)."""
    import openpyxl

    n = pypsa.Network()
    with tempfile.TemporaryDirectory() as directory:
        copy = pathlib.Path(directory) / "network.xlsx"
        wb = openpyxl.load_workbook(path, data_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    if cell.data_type in ("s", "f") and isinstance(cell.value, str):
                        new = unescape_formula(cell.value)
                        if new != cell.value or cell.data_type == "f":
                            cell.value = new
                            cell.data_type = "s"
        wb.save(copy)
        n.import_from_excel(str(copy), engine="openpyxl")
    for cls, sheet, list_name in L.SHEETS:
        if not getattr(n, list_name).empty:
            report.counts[sheet] = len(getattr(n, list_name))
    return n


def _read_legacy_sheets(sheets: _Sheets, report: ImportReport) -> pypsa.Network:
    """Raw-named component sheets: header row, then one row per asset."""
    n = pypsa.Network()
    for sheet in sheets.names:
        cls = _LEGACY_SHEETS.get(sheet)
        if cls is None:
            report.unmapped.append((sheet, "not a component sheet; its rows were not read"))
            continue
        rows = sheets.rows[sheet]
        if not rows:
            continue
        headers = list(rows[0])
        added = 0
        for r, row in enumerate(rows[1:], start=2):
            if all(v is None for v in row):
                continue
            values = dict(zip(headers, map(unescape_formula, row)))
            name = values.pop("name", values.pop(headers[0], None))
            if not name:
                report.unmapped.append((f"{sheet} row {r}", "the row has values but no name"))
                continue
            if str(name) in getattr(n, L.LIST_NAME[cls]).index:
                # PyPSA skips a second add with a warning only: report it instead.
                report.unmapped.append((f"{sheet} row {r} ({name})", "a second row with the same name"))
                continue
            try:
                n.add(cls, str(name), **{k: v for k, v in values.items() if v is not None})
                added += 1
            except Exception as exc:  # noqa: BLE001 - the model's refusal is the reason
                report.unmapped.append((f"{sheet} › {name}", f"the model refused the row: {exc}"))
        report.counts[L.SHEET_OF[cls]] = added
    return n


# ── entry point ──────────────────────────────────────────────────────────────

def read_excel(paths: Sequence[str | pathlib.Path] | str | pathlib.Path,
               options: Mapping[str, Any] | None = None) -> ImportResult:
    """Read one .xlsx file. `options["custom_columns"]`: per model class, the
    non-PyPSA columns the caller accepts (investment parts, eh_* tags); any other
    column the file names is reported, never set."""
    options = options or {}
    if isinstance(paths, str | pathlib.Path):
        paths = [paths]
    paths = [pathlib.Path(p) for p in paths]
    if len(paths) != 1:
        raise L.ImportFormatError("Import one Excel workbook at a time.")
    path = paths[0]
    report = ImportReport(source_format=FORMAT_ID, source_files=[path.name])
    sheets = _Sheets(path)
    if L.ABOUT in sheets.names and L.HORIZON in sheets.names:
        n, sidecars = _read_input_workbook(sheets, report, options.get("custom_columns") or {})
    elif "network" in sheets.names:
        try:
            n = _read_native(path, report)
        except Exception as exc:  # noqa: BLE001 - PyPSA's reader failing is one refusal
            logger.warning("PyPSA Excel layout refused", exc_info=True)
            raise L.ImportFormatError("The workbook has a 'network' sheet but PyPSA could not read it.") from exc
        sidecars = {}
    else:
        n = _read_legacy_sheets(sheets, report)
        sidecars = {}
    return ImportResult(network=n, sidecars=sidecars, report=report)

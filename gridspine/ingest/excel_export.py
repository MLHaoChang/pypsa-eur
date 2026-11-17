"""
Write a network and its sidecars as the Excel input workbook (IMPORTS-1).

`write_workbook(n, columns=..., sidecars=...)` returns the .xlsx bytes. The same
call with `template=True` writes the blank template: every catalogue column, no
rows, the validation lists drawn from the network handed in.

No pypsa import (engine-cage ruling): the network is read through its tables
only. `excel_import.read_excel` reads what this writes; the round trip is the
contract (export, import, the same network and sidecars).
"""

from __future__ import annotations

import io
import json
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pandas as pd

from gridspine.ingest import excel_layout as L

SIZE_UNIT_KEY = "~size_unit"
#: Sidecar keys: the project files they become (the import contract).
SIDECAR_USER_TS, SIDECAR_SOLVER = "user_ts.json", "solver_config.json"
#: The column naming the bus a class's size is counted on (a Link's input side).
SIZE_BUS = {
    "Generator": "bus",
    "StorageUnit": "bus",
    "Store": "bus",
    "Load": "bus",
    "Link": "bus0",
}
_VALIDATION_ROWS = 2000


def _defaults(n, list_name: str) -> pd.DataFrame:
    return n.components[list_name].defaults


def _input_attrs(n, list_name: str) -> pd.DataFrame:
    d = _defaults(n, list_name)
    return d[d["status"].astype(str).str.startswith("Input")]


def _is_default(series: pd.Series, default: Any) -> bool:
    if series.empty:
        return True
    try:
        if default is None or (isinstance(default, float) and math.isnan(default)):
            return bool(series.isna().all())
        return bool((series == default).all())
    except (TypeError, ValueError):
        return False


def _cell_value(v: Any, scale: float) -> Any:
    """
    A stored value as the cell shows it. NaN stays empty; inf is written as text so
    Excel keeps it (the reader accepts it only where the engine does, E3).
    """
    if v is None:
        return None
    if (
        isinstance(v, bool)
        or hasattr(v, "dtype")
        and str(getattr(v, "dtype", "")) == "bool"
    ):
        return bool(v)
    if isinstance(v, int | float) and not isinstance(v, bool):
        f = float(v)
        if math.isnan(f):
            return None
        if math.isinf(f):
            return "inf" if f > 0 else "-inf"
        if scale != 1.0:
            return L.rescale(f, scale)
        return v.item() if hasattr(v, "item") else v
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return str(v)


def _sheet_columns(
    n, cls: str, catalogue: Sequence[L.Column], *, template: bool, custom: Sequence[str]
) -> list[L.Column]:
    """
    The catalogue's columns (visible), then every other column the table holds
    that is not at its default, and every custom column (hidden, kept for the round trip).
    """
    list_name = L.LIST_NAME[cls]
    table = getattr(n, list_name)
    attrs = _input_attrs(n, list_name)
    out: list[L.Column] = []
    seen = {"name"}
    out.append(
        next(
            (c for c in catalogue if c.key == "name"),
            L.Column("name", "Name", kind="text"),
        )
    )
    for c in catalogue:
        if c.key in seen:
            continue
        if c.key not in attrs.index and c.key not in custom:
            continue  # an output, or a column this table cannot hold (yet)
        out.append(c)
        seen.add(c.key)
    if template:
        return out
    for key in table.columns:
        if key in seen:
            continue
        if key in attrs.index:
            if _is_default(table[key], attrs.at[key, "default"]):
                continue
        elif key not in custom:
            continue
        out.append(L.Column(key, L.humanise(key), visible=False, kind="text"))
        seen.add(key)
    return out


def _write_header(ws, cols: Sequence[L.Column]) -> None:
    from openpyxl.styles import Font

    bold, italic = Font(bold=True), Font(italic=True, color="666666")
    for j, c in enumerate(cols, start=1):
        L.put(ws, L.ROW_LABEL, j, c.label).font = bold
        L.put(ws, L.ROW_UNIT, j, c.unit or None).font = italic
        L.put(ws, L.ROW_KEY, j, c.key)
        L.put(ws, L.ROW_SCALE, j, float(c.scale))
        ws.column_dimensions[_letter(j)].width = max(10, min(36, len(c.label) + 2))
        if not c.visible:
            ws.column_dimensions[_letter(j)].hidden = True
    ws.row_dimensions[L.ROW_KEY].hidden = True
    ws.row_dimensions[L.ROW_SCALE].hidden = True
    ws.freeze_panes = ws.cell(row=L.FIRST_DATA_ROW, column=2)


def _letter(j: int) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(j)


def _size_units(n, cls: str, names, size_unit_of) -> list[str] | None:
    col = SIZE_BUS.get(cls)
    if col is None:
        return None
    table = getattr(n, L.LIST_NAME[cls])
    units = []
    for name in names:
        unit = size_unit_of(cls, name) if size_unit_of else None
        if unit is None:
            bus = table.at[name, col]
            bus_unit = (
                n.buses.at[bus, "unit"]
                if bus in n.buses.index and "unit" in n.buses
                else ""
            )
            unit = "t/h" if bus_unit == "t" else "MW"
        units.append(unit)
    return units if any(u != "MW" for u in units) else None


def _write_component(
    wb, n, cls: str, catalogue, *, template: bool, custom, lists, size_unit_of
) -> int:
    table = getattr(n, L.LIST_NAME[cls])
    if not template and table.empty and cls not in ("Bus", "Generator", "Load", "Line"):
        return 0
    cols = _sheet_columns(n, cls, catalogue, template=template, custom=custom)
    names = [] if template else list(table.index)
    sizes = None if template else _size_units(n, cls, names, size_unit_of)
    if sizes:
        cols.insert(1, L.Column(SIZE_UNIT_KEY, "Size unit", kind="text"))
    ws = wb.create_sheet(L.SHEET_OF[cls])
    _write_header(ws, cols)
    for i, name in enumerate(names):
        r = L.FIRST_DATA_ROW + i
        for j, c in enumerate(cols, start=1):
            if c.key == "name":
                v = str(name)
            elif c.key == SIZE_UNIT_KEY:
                v = sizes[i]
            elif c.key in table.columns:
                v = _cell_value(table.at[name, c.key], c.scale)
            else:
                v = None
            L.put(ws, r, j, v)
    _validate(ws, cols, lists)
    return len(names)


def _validate(ws, cols, lists: Mapping[str, str]) -> None:
    """Drop-down lists: TRUE/FALSE, enum options, and nodes and carriers from the Lists sheet."""
    from openpyxl.worksheet.datavalidation import DataValidation

    last = L.FIRST_DATA_ROW + max(_VALIDATION_ROWS, ws.max_row)
    for j, c in enumerate(cols, start=1):
        if c.kind == "bool":
            formula = '"TRUE,FALSE"'
        elif c.kind in ("bus", "carrier") and lists.get(c.kind):
            formula = lists[c.kind]
        elif (
            c.kind == "enum"
            and c.options
            and len(",".join(c.options)) < 250
            and not any(("," in o or '"' in o) for o in c.options)
        ):
            formula = '"' + ",".join(c.options) + '"'
        else:
            continue
        dv = DataValidation(
            type="list",
            formula1=formula,
            allow_blank=True,
            showErrorMessage=c.kind != "carrier",
        )
        dv.error = f"Pick a value from the list for {c.label}."
        dv.add(f"{_letter(j)}{L.FIRST_DATA_ROW}:{_letter(j)}{last}")
        ws.add_data_validation(dv)


def _write_lists(wb, n) -> dict[str, str]:
    ws = wb.create_sheet(L.LISTS)
    ws.sheet_state = "hidden"
    out = {}
    for j, (kind, label, values) in enumerate(
        (
            ("bus", "Nodes", list(n.buses.index)),
            (
                "carrier",
                "Carriers",
                sorted(
                    set(n.carriers.index)
                    | set(n.buses.get("carrier", pd.Series()).dropna())
                ),
            ),
        ),
        start=1,
    ):
        L.put(ws, 1, j, label)
        for i, v in enumerate(values, start=2):
            L.put(ws, i, j, str(v))
        if values:
            col = _letter(j)
            out[kind] = f"'{L.LISTS}'!${col}$2:${col}${len(values) + 1}"
    return out


# ── time series ──────────────────────────────────────────────────────────────


def _snapshot_kind(index) -> str:
    level = index.get_level_values(-1) if isinstance(index, pd.MultiIndex) else index
    if isinstance(level, pd.DatetimeIndex):
        return "dates"
    if pd.api.types.is_numeric_dtype(level):
        return "numbers"
    return "text"


def _write_time_series(
    wb, n, catalogue: Mapping[str, Sequence[L.Column]], user_ts: Mapping
) -> int:
    from openpyxl.styles import Font

    ws = wb.create_sheet(L.TIME_SERIES)
    multi = isinstance(n.snapshots, pd.MultiIndex)
    time_cols = [c for c in L.SNAPSHOT_COLUMNS if multi or c[0] != "period"]
    weights = n.snapshot_weightings
    series: list[tuple[str, str, str, L.Column, pd.Series]] = []
    for cls, sheet, list_name in L.SHEETS:
        dyn = getattr(n, f"{list_name}_t", None)
        if dyn is None:
            continue
        by_key = {c.key: c for c in catalogue.get(cls, ())}
        for attr in sorted(dyn.keys()):
            frame = dyn[attr]
            if frame.empty:
                continue
            col = by_key.get(attr, L.Column(attr, L.humanise(attr)))
            for name in frame.columns:
                series.append((cls, sheet, str(name), col, frame[name]))
    uploaded, elsewhere = _split_uploaded(n, user_ts, series)
    bold = Font(bold=True)
    j = 0
    for j, (key, label, unit) in enumerate(time_cols, start=1):
        L.put(ws, L.TS_ROW_LABEL, j, label).font = bold
        L.put(ws, L.TS_ROW_UNIT, j, unit or None)
        L.put(ws, L.TS_ROW_CLASS, j, "snapshots")
        L.put(ws, L.TS_ROW_ATTR, j, key)
        L.put(ws, L.TS_ROW_SCALE, j, 1.0)
    first_series = j + 1
    for k, (cls, sheet, name, col, _) in enumerate(series):
        jj = first_series + k
        L.put(ws, L.TS_ROW_TYPE, jj, sheet)
        L.put(ws, L.TS_ROW_ASSET, jj, name)
        L.put(ws, L.TS_ROW_LABEL, jj, col.label).font = bold
        L.put(ws, L.TS_ROW_UNIT, jj, col.unit or None)
        L.put(ws, L.TS_ROW_CLASS, jj, cls)
        L.put(ws, L.TS_ROW_ATTR, jj, col.key)
        L.put(ws, L.TS_ROW_SCALE, jj, float(col.scale))
        L.put(ws, L.TS_ROW_USER, jj, uploaded.get((L.LIST_NAME[cls], col.key, name)))
    for r in (L.TS_ROW_CLASS, L.TS_ROW_ATTR, L.TS_ROW_SCALE, L.TS_ROW_USER):
        ws.row_dimensions[r].hidden = True
    for i, snap in enumerate(n.snapshots):
        r = L.TS_FIRST_DATA_ROW + i
        period, step = snap if multi else (None, snap)
        values = {"period": period, "snapshot": step}
        for key in ("objective", "stores", "generators"):
            values[key] = weights.iat[i, weights.columns.get_loc(key)]
        for jj, (key, _, _) in enumerate(time_cols, start=1):
            v = values[key]
            L.put(
                ws,
                r,
                jj,
                v.isoformat() if isinstance(v, pd.Timestamp) else _cell_value(v, 1.0),
            )
        for k, (_, _, _, col, s) in enumerate(series):
            L.put(ws, r, first_series + k, _cell_value(s.iat[i], col.scale))
    ws.freeze_panes = ws.cell(row=L.TS_FIRST_DATA_ROW, column=first_series)
    if elsewhere:
        _write_uploaded(wb, elsewhere)
    return len(series)


def snapshot_index_json(snapshots) -> list:
    """The snapshots as `user_ts.json` writes an index: ISO strings, or [period, ISO] pairs."""

    def iso(t):
        return t.isoformat() if hasattr(t, "isoformat") else t

    if isinstance(snapshots, pd.MultiIndex):
        return [[int(p), iso(t)] for p, t in snapshots]
    return [iso(t) for t in snapshots]


def _json_values(s: pd.Series) -> list:
    return [
        None if isinstance(v, float) and not math.isfinite(v) else v for v in s.tolist()
    ]


def _split_uploaded(n, user_ts: Mapping, series) -> tuple[dict, list]:
    """
    Which uploaded profiles (`user_ts.json`, keyed like the app's user_ts.json:
    list name, attribute, asset) are exactly a Time series column: those are flagged
    in the hidden row, so an edit in Excel edits both. The rest keep their own time
    steps in the hidden Uploaded profiles sheet.
    """
    index = snapshot_index_json(n.snapshots)
    on_sheet = {(L.LIST_NAME[cls], col.key, name): s for cls, _, name, col, s in series}
    flagged: dict = {}
    elsewhere: list = []
    for list_name, by_attr in user_ts.items():
        if list_name.startswith("$") or not isinstance(by_attr, Mapping):
            continue
        for attr, by_asset in by_attr.items():
            for name, entry in (by_asset or {}).items():
                key = (list_name, attr, name)
                extra = {k: v for k, v in entry.items() if k not in ("index", "values")}
                s = on_sheet.get(key)
                if (
                    s is not None
                    and entry.get("index") == index
                    and entry.get("values") == _json_values(s)
                ):
                    flagged[key] = json.dumps(extra, sort_keys=True)
                else:
                    elsewhere.append((key, entry))
    return flagged, elsewhere


def _write_uploaded(wb, elsewhere: list) -> None:
    ws = wb.create_sheet(L.UPLOADED)
    ws.sheet_state = "hidden"
    for k, ((list_name, attr, name), entry) in enumerate(elsewhere):
        j = 2 * k + 1
        extra = {key: v for key, v in entry.items() if key not in ("index", "values")}
        L.put(ws, L.UP_ROW_LIST, j, list_name)
        L.put(ws, L.UP_ROW_ATTR, j, attr)
        L.put(ws, L.UP_ROW_ASSET, j, name)
        L.put(ws, L.UP_ROW_META, j, json.dumps(extra, sort_keys=True))
        for i, (idx, v) in enumerate(
            zip(entry.get("index") or (), entry.get("values") or ())
        ):
            r = L.UP_FIRST_DATA_ROW + i
            L.put(ws, r, j, json.dumps(idx) if isinstance(idx, list) else idx)
            L.put(ws, r, j + 1, _cell_value(v, 1.0))


# ── horizon and finance ──────────────────────────────────────────────────────


def _write_horizon(wb, n, solver: Mapping, template: bool) -> None:
    from openpyxl.styles import Font

    ws = wb.create_sheet(L.HORIZON)
    bold = Font(bold=True)
    for j, label in enumerate(("Setting", "Value", "Unit"), start=1):
        L.put(ws, 1, j, label).font = bold
    L.put(ws, 1, 4, "key")
    values = {
        "discount_rate": solver.get("discount_rate"),
        "multi_investment_periods": bool(solver.get("multi_investment_periods", False)),
        "investment_periods": ", ".join(
            str(int(p)) for p in solver.get("investment_periods") or ()
        ),
        "snapshot_kind": "dates" if template else _snapshot_kind(n.snapshots),
    }
    for i, (key, label, unit, scale) in enumerate(L.HORIZON_SETTINGS, start=2):
        L.put(ws, i, 1, label)
        v = values[key]
        L.put(
            ws,
            i,
            2,
            v * scale if isinstance(v, int | float) and not isinstance(v, bool) else v,
        )
        L.put(ws, i, 3, unit or None)
        L.put(ws, i, 4, key)
    start = len(L.HORIZON_SETTINGS) + 3
    for j, (key, label, unit) in enumerate(L.PERIOD_COLUMNS, start=1):
        L.put(ws, start, j, label).font = bold
        L.put(ws, start + 1, j, unit or None)
        L.put(ws, start + 2, j, key)
    ws.row_dimensions[start + 2].hidden = True
    ws.column_dimensions["D"].hidden = True
    ws.column_dimensions["A"].width = 40
    pw = n.investment_period_weightings
    for i, period in enumerate(pw.index):
        r = start + 3 + i
        L.put(ws, r, 1, int(period))
        L.put(ws, r, 2, _cell_value(pw.at[period, "objective"], 1.0))
        L.put(ws, r, 3, _cell_value(pw.at[period, "years"], 1.0))


# ── about ────────────────────────────────────────────────────────────────────

_SHEET_HELP = {
    "Carriers": "Energy carriers and their emission factors.",
    "Nodes": "The points assets connect to (buses).",
    "Lines": "AC lines between two nodes.",
    "Transformers": "Transformers between two nodes.",
    "Generators": "Power plants, wind and solar parks, and other sources.",
    "Storage": "Batteries and pumped hydro with a fixed hours-of-storage ratio.",
    "Stores": "Energy stores sized separately from their charger.",
    "Loads": "Demand at a node.",
    "Links": "Converters, DC links and process units between nodes.",
    "Shunts": "Shunt impedances at a node.",
    "System limits": "Limits on the whole system, such as an emissions cap.",
    L.TIME_SERIES: "One column per asset and parameter that changes over time; one row per time step.",
    L.HORIZON: "Investment periods, their weightings and the discount rate.",
}


def _write_about(
    ws, n, sheets: list[str], counts: Mapping[str, int], template: bool
) -> None:
    from openpyxl.styles import Font

    rows: list[tuple[Any, ...]] = [
        ("PyPSA Studio input workbook",),
        ("Blank template" if template else f"Project: {n.name or 'Untitled'}",),
        (),
        ("How to use it",),
        (
            "Fill or edit the sheets below in Excel, then import the file as a new project. "
            "The import shows what it read, what it could not read and what it assumed, before anything is created.",
        ),
        (
            "Row 1 holds the column names and row 2 their units. Rows 3 and 4 are hidden: "
            "they tie each column to the model. Leave them as they are.",
        ),
        (
            "Leave a cell empty to use the model's default. Columns that are hidden hold values the project "
            "already had; keep them to keep the project unchanged.",
        ),
        (),
        ("Sheet", "What it holds", "Rows"),
    ]
    rows += [(s, _SHEET_HELP.get(s, ""), counts.get(s)) for s in sheets]
    rows += [(), ("Not in this workbook",)] + [(f"• {item}",) for item in L.NOT_CARRIED]
    rows += [(), ("Workbook format", L.FORMAT_VERSION)]
    bold = Font(bold=True)
    for i, row in enumerate(rows, start=1):
        for j, v in enumerate(row, start=1):
            cell = L.put(ws, i, j, v)
            if (
                j == 1
                and len(row) == 1
                and v in ("How to use it", "Not in this workbook")
                or v == "Sheet"
            ):
                cell.font = bold
    ws["A1"].font = Font(bold=True, size=14)
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 80


# ── entry point ──────────────────────────────────────────────────────────────


def write_workbook(
    n,
    *,
    columns: Mapping[str, Sequence[L.Column]],
    sidecars: Mapping[str, Any] | None = None,
    template: bool = False,
    custom_columns: Mapping[str, Sequence[str]] | None = None,
    size_unit_of: Callable[[str, str], str | None] | None = None,
) -> bytes:
    """
    The workbook as bytes.

    `columns`: per model class, the catalogue's columns in order (plain names, units,
    factors). `custom_columns`: per class, the non-PyPSA columns the project may hold
    (investment parts, eh_* tags); the reader accepts exactly these. `sidecars`:
    `user_ts.json` (the uploaded series, as the project file holds them) and
    `solver_config.json` (discount rate and investment periods only). `size_unit_of(cls, name)`: the asset's
    size unit when the caller knows it better than the bus table (a tonne bus, E4).
    """
    import openpyxl

    sidecars = sidecars or {}
    custom_columns = custom_columns or {}
    wb = openpyxl.Workbook()
    about = wb.active
    about.title = L.ABOUT
    lists = _write_lists(wb, n)
    counts: dict[str, int] = {}
    sheets: list[str] = []
    for cls, sheet, _ in L.SHEETS:
        before = len(wb.sheetnames)
        rows = _write_component(
            wb,
            n,
            cls,
            columns.get(cls, ()),
            template=template,
            custom=tuple(custom_columns.get(cls, ())),
            lists=lists,
            size_unit_of=size_unit_of,
        )
        if len(wb.sheetnames) > before:
            sheets.append(sheet)
            counts[sheet] = rows
    if template:
        ws = wb.create_sheet(L.TIME_SERIES)
        for j, (key, label, unit) in enumerate(L.SNAPSHOT_COLUMNS[1:], start=1):
            L.put(ws, L.TS_ROW_LABEL, j, label)
            L.put(ws, L.TS_ROW_UNIT, j, unit or None)
            L.put(ws, L.TS_ROW_CLASS, j, "snapshots")
            L.put(ws, L.TS_ROW_ATTR, j, key)
            L.put(ws, L.TS_ROW_SCALE, j, 1.0)
        for r in (L.TS_ROW_CLASS, L.TS_ROW_ATTR, L.TS_ROW_SCALE, L.TS_ROW_USER):
            ws.row_dimensions[r].hidden = True
        counts[L.TIME_SERIES] = 0
    else:
        counts[L.TIME_SERIES] = _write_time_series(
            wb, n, columns, sidecars.get(SIDECAR_USER_TS) or {}
        )
    sheets.append(L.TIME_SERIES)
    _write_horizon(wb, n, sidecars.get(SIDECAR_SOLVER) or {}, template)
    sheets.append(L.HORIZON)
    meta = (sidecars.get(SIDECAR_USER_TS) or {}).get("$meta")
    if meta:
        # Provenance of uploaded series: kept for the round trip, in the hidden Lists sheet.
        L.put(wb[L.LISTS], 1, 4, "$meta")
        L.put(wb[L.LISTS], 2, 4, json.dumps(meta, sort_keys=True, default=str))
    wb.move_sheet(L.LISTS, offset=len(wb.sheetnames))
    _write_about(about, n, sheets, counts, template)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

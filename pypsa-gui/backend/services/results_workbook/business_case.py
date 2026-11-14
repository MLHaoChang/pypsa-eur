# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""The results workbook's "Business case" sheet (business case 08a; spec §7; RW-KPI-1;
GK B 08a C6, C7).

Written beside the investment-case sheets from the stored business-case report only: the
export never runs the business case. Nothing stored is one `not_established` row with
"run the business case". A stale report is labelled stale with what changed and its
figures are not shown, so it never reads as current. A KPI or break-even that is not
established is the `not_established` cell with its reason, never 0."""

from __future__ import annotations

from typing import Any

from services.results_workbook.cells import NOT_ESTABLISHED, heading, put, width

SHEET = "Business case"
RUN_THE_BUSINESS_CASE = "run the business case (Results, Investment tab)"


def business_case_text(report: dict | None, state: dict | None) -> str:
    """The About row and the sheet's status line."""
    if not report:
        return (
            f"{NOT_ESTABLISHED}: no business case has been run; {RUN_THE_BUSINESS_CASE}"
        )
    st = state or {}
    if st.get("stale") is not False:
        changed = (
            ", ".join(st.get("changed") or []) or st.get("reason") or "not readable now"
        )
        return (
            f"stale (changed since the run: {changed}): the figures describe an earlier "
            f"run and are not shown; {RUN_THE_BUSINESS_CASE} again"
        )
    return f"current, {report.get('basis', {}).get('label', '')}"


def _kpi_cells(k: dict) -> tuple[Any, str]:
    if k.get("status") == "ok":
        return k.get("value"), "ok"
    why = k.get("reason") or "not_established"
    if k.get("status") == "n/a":
        return "n/a", why
    return None, why  # `put` writes not_established


def _assumed_rows(ws, r: int, assumed: list[dict] | None) -> int:
    """Every default the case relies on (business case 14): value, unit, source and year,
    one row each. None is said, never left blank. Returns the last row written."""
    heading(ws, r, ["Assumed default", "Value", "Unit", "Source and year"])
    if assumed is None:  # a report stored before the list existed: unknown, never "none"
        r += 1
        put(ws, r, 1, None)
        put(ws, r, 4, RUN_THE_BUSINESS_CASE + " again to list them")
        return r
    if not assumed:
        r += 1
        put(ws, r, 1, "none: every value is the user's")
        return r
    for a in assumed:
        r += 1
        put(ws, r, 1, f"{a.get('line')} ({a.get('column')})")
        put(ws, r, 2, a.get("value"))
        put(ws, r, 3, a.get("unit") or "")
        put(ws, r, 4, f"{a.get('source')}, {a.get('source_year')} (assumed)")
    return r


def write_business_case_sheet(wb, report: dict | None, state: dict | None) -> str:
    ws = wb.create_sheet(SHEET)
    heading(ws, 1, ["Item", "Value"])
    put(ws, 2, 1, "Business case")
    put(ws, 2, 2, business_case_text(report, state))
    width(ws, {"A": 34, "B": 22, "C": 22, "D": 40})
    if not report or (state or {}).get("stale") is not False:
        return ws.title
    rows = [
        ("Basis", report["basis"]["label"]),
        ("Currency", report.get("currency")),
        ("Business case hash", report.get("business_case_hash")),
        ("Investment case key", report.get("ic_assumptions_hash")),
    ]
    r = 3
    for k, v in rows:
        put(ws, r, 1, k)
        put(ws, r, 2, v)
        r += 1
    r += 1
    heading(ws, r, ["KPI", "Value", "Status or reason", "Unit"])
    for name, k in report.get("kpis", {}).items():
        r += 1
        value, why = _kpi_cells(k)
        put(ws, r, 1, name)
        put(ws, r, 2, value)
        put(ws, r, 3, why)
        put(ws, r, 4, k.get("unit") or "")
    be = report.get("break_even") or {}
    r += 2
    heading(ws, r, ["Break-even", "Value", "Status or reason"])
    ok = be.get("status") == "ok"
    for label, key in (
        ("Revenue line", "line"),
        ("Price at NPV 0", "price"),
        ("Price scale at NPV 0", "price_scale"),
    ):
        r += 1
        put(ws, r, 1, label)
        put(ws, r, 2, be.get(key) if (ok or key == "line") else None)
        put(ws, r, 3, "ok" if ok else (be.get("status") or NOT_ESTABLISHED))
    r = _assumed_rows(ws, r + 2, report.get("assumed"))
    pnl = report.get("pnl") or {}
    years = pnl.get("years") or []
    for title, table in (
        ("P&L by year", pnl.get("rows") or {}),
        ("Operating ledger by stream", pnl.get("streams") or {}),
    ):
        r += 2
        heading(ws, r, [title, *years])
        for name, series in table.items():
            r += 1
            put(ws, r, 1, name)
            for j, _y in enumerate(years):
                v = series[j] if isinstance(series, list) and j < len(series) else None
                put(ws, r, 2 + j, v if isinstance(v, (int, float)) else None)
    return ws.title

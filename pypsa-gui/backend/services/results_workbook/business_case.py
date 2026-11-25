# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""The results workbook's "Business case" sheet (business case 08a; spec §7; RW-KPI-1;
GK B 08a C6, C7).

Written beside the investment-case sheets from the stored business-case report only: the
export never runs the business case. Nothing stored is one "not established" row with
"run the business case". A stale report is labelled stale with what changed and its
figures are not shown, so it never reads as current. A KPI or break-even that is not
established is the "not established" cell with its reason, never 0. KPI names, reasons
and empty cells are in words (walk 06, RAW-NAMES-1, RW-KPI-1; GK B2 B3)."""

from __future__ import annotations

import math
from typing import Any

from services.results_workbook.cells import heading, put, width
from services.run_actions import _display_name

SHEET = "Business case"
RUN_THE_BUSINESS_CASE = "run the business case (Results, Investment tab)"


#: The lease-up row of "P&L by year" (BC 15); written only when a year is below 100 %.
LEASE_UP_ROW = "Volume factor (lease-up), %"
#: The edit card's label and option words for a typed line's volume class (BC 18).
VOLUME_CLASS_LABEL = "Volume class"
VOLUME_CLASS_WORDS = {
    "fixed": "Fixed (never scales)",
    "volume": "Follows volume (scales with lease-up)",
}

#: An empty figure on this sheet, as the app writes it (walk 06); never 0.
NOT_ESTABLISHED_WORDS = "not established"

#: Each `kpis()` key as the Investment tab names it (a test pins the keys to `kpis()`).
KPI_WORDS = {
    "ebitda_margin_gross": "EBITDA margin (gross)",
    "ebitda_margin_net": "EBITDA margin (net of pass-through)",
    "revenue_per_capacity_gross": "Revenue per capacity (gross)",
    "revenue_per_capacity_net": "Revenue per capacity (net of pass-through)",
    "project_npv": "Project NPV",
    "project_irr": "Project IRR",
    "equity_npv": "Equity NPV",
    "equity_irr": "Equity IRR",
    "payback_years": "Payback",
    "min_dscr": "Minimum DSCR",
    "avg_dscr": "Average DSCR",
}

#: Reason codes whose words are not their spelled-out code.
_REASON_WORDS = {
    "not_established": NOT_ESTABLISHED_WORDS,
    "capacity_not_stated": "the capacity is not stated",
    "not_requested": "not run",
}

#: Abbreviations a spelled-out code keeps in capitals.
_CAPS = {
    "wacc": "WACC",
    "irr": "IRR",
    "npv": "NPV",
    "dscr": "DSCR",
    "ppa": "PPA",
    "ebitda": "EBITDA",
    "capex": "capex",
    "opex": "opex",
}


def _code_words(code: str) -> str:
    """One code in words: `degradation_missing:genset_1` → "degradation missing for
    Genset 1"; the subject after the colon is an asset or item, read as its name."""
    name, _, subject = code.strip().partition(":")
    words = _REASON_WORDS.get(name) or " ".join(
        _CAPS.get(w, w) for w in name.split("_") if w
    )
    return f"{words} for {_display_name(subject)}" if subject else words


def reason_words(code: str | None) -> str:
    """A KPI or break-even reason in words, never a code: `operating_not_established:
    degradation_missing:genset_1, input_missing:tax_losses` → "operating not established:
    degradation missing for Genset 1, input missing for Tax losses"."""
    if not code:
        return NOT_ESTABLISHED_WORDS
    head, sep, items = code.partition(": ")
    out = _code_words(head)
    if sep:
        out += ": " + ", ".join(_code_words(i) for i in items.split(", ") if i)
    return out


def _put(ws, row: int, col: int, value: Any):
    """`put`, with an empty or non-finite figure written in words."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        value = NOT_ESTABLISHED_WORDS
    return put(ws, row, col, value)


def business_case_text(report: dict | None, state: dict | None) -> str:
    """The About row and the sheet's status line."""
    if not report:
        return (
            f"{NOT_ESTABLISHED_WORDS}: no business case has been run; "
            f"{RUN_THE_BUSINESS_CASE}"
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
    why = reason_words(k.get("reason"))
    if k.get("status") == "n/a":
        return "n/a", why
    return None, why  # `_put` writes "not established"


def _assumed_rows(ws, r: int, assumed: list[dict] | None) -> int:
    """Every default the case relies on (business case 14): value, unit, source and year,
    one row each. None is said, never left blank. Returns the last row written."""
    heading(ws, r, ["Assumed default", "Value", "Unit", "Source and year"])
    if (
        assumed is None
    ):  # a report stored before the list existed: unknown, never "none"
        r += 1
        _put(ws, r, 1, None)
        _put(ws, r, 4, RUN_THE_BUSINESS_CASE + " again to list them")
        return r
    if not assumed:
        r += 1
        _put(ws, r, 1, "none: every value is the user's")
        return r
    for a in assumed:
        r += 1
        _put(ws, r, 1, f"{a.get('line')} ({a.get('column')})")
        _put(ws, r, 2, a.get("value"))
        _put(ws, r, 3, a.get("unit") or "")
        _put(ws, r, 4, f"{a.get('source')}, {a.get('source_year')} (assumed)")
    return r


def write_business_case_sheet(wb, report: dict | None, state: dict | None) -> str:
    ws = wb.create_sheet(SHEET)
    heading(ws, 1, ["Item", "Value"])
    _put(ws, 2, 1, "Business case")
    _put(ws, 2, 2, business_case_text(report, state))
    width(ws, {"A": 34, "B": 22, "C": 22, "D": 40})
    if not report or (state or {}).get("stale") is not False:
        return ws.title
    rows = [
        ("Basis", report["basis"]["label"]),
        ("Currency", report.get("currency")),
        ("Business case hash", report.get("business_case_hash")),
        ("Investment case key", report.get("ic_assumptions_hash")),
    ]
    pnl = report.get("pnl") or {}
    if pnl.get("volume_factor_note"):
        rows.append(("Volume factor (lease-up)", pnl["volume_factor_note"]))
    if pnl.get("volume_factor_ignored"):
        rows.append(
            (
                "Volume factor years ignored",
                "outside the operating years: "
                + ", ".join(str(y) for y in pnl["volume_factor_ignored"]),
            )
        )
    for name, cls in (pnl.get("volume_class") or {}).items():
        # BC 18: each typed line's stated class, in the edit card's words (UX 30).
        rows.append((f"{VOLUME_CLASS_LABEL}: {name}", VOLUME_CLASS_WORDS.get(cls, cls)))
    r = 3
    for k, v in rows:
        _put(ws, r, 1, k)
        _put(ws, r, 2, v)
        r += 1
    r += 1
    heading(ws, r, ["KPI", "Value", "Status or reason", "Unit"])
    for name, k in report.get("kpis", {}).items():
        r += 1
        value, why = _kpi_cells(k)
        _put(ws, r, 1, KPI_WORDS.get(name) or _display_name(name))
        _put(ws, r, 2, value)
        _put(ws, r, 3, why)
        _put(ws, r, 4, k.get("unit") or "")
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
        _put(ws, r, 1, label)
        _put(ws, r, 2, be.get(key) if (ok or key == "line") else None)
        _put(ws, r, 3, "ok" if ok else reason_words(be.get("status")))
    r = _assumed_rows(ws, r + 2, report.get("assumed"))
    years = pnl.get("years") or []
    for title, table in (
        ("P&L by year", pnl.get("rows") or {}),
        ("Operating ledger by stream", pnl.get("streams") or {}),
    ):
        r += 2
        heading(ws, r, [title, *years])
        lease = pnl.get("volume_factor")
        if title == "P&L by year" and lease:
            # BC 15: the lease-up the run applied, a percent per year (100 = full volume).
            r += 1
            _put(ws, r, 1, LEASE_UP_ROW)
            for j, y in enumerate(years):
                _put(ws, r, 2 + j, 100.0 * lease.get(str(y), 1.0))
        for name, series in table.items():
            r += 1
            _put(ws, r, 1, name)
            for j, _y in enumerate(years):
                v = series[j] if isinstance(series, list) and j < len(series) else None
                _put(ws, r, 2 + j, v if isinstance(v, (int, float)) else None)
    return ws.title

"""
The pro forma as a workbook (guided investment study MVP-1, phase S5).

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S5;
review v1 S9). openpyxl, as `services/asset_results/export.py` already uses.

Four sheets:

* ``Cash flows`` — one row per year (0 .. horizon). The money columns are the
  case's values; the net cash flow, discount factor, discounted and
  cumulative discounted cash flow are FORMULAS, so a reader can audit or
  change them. The NPV is pinned as ``=CF0 + NPV(rate, CF1:CFn)``: Excel's
  ``NPV`` discounts its first value one period, so the year-0 cash flow is
  added outside it (a range starting at CF0 would understate the NPV by the
  factor 1 + r). Market revenue at duals is a column of its own, labelled
  excluded, and no formula reads it.
* ``KPIs`` — the case's KPIs; the NPV cell refers to the cash-flow sheet's.
  A null KPI is an empty cell with its reason beside it, never a zero. The
  ``cell`` column labels each KPI live (the NPV) or STATIC (the IRR, the
  paybacks, the LCOS: the model's values at the case's rate, which do not
  move with the rate cell; gate S5 [N5], S7).
* ``Assumptions`` — the ledger, one row per key.
* ``Provenance`` — engines, basis, currency year, fidelity, library version,
  ledger and model hashes, the honesty notes and the upfront-cost gap.

Text is written as text: a string that starts with ``=`` (a ledger source a
user typed, say) is stored as a string cell, never as a formula.
"""
from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Any

from models.study import AssumptionsLedger, InvestmentCase

__all__ = ["write_proforma_xlsx"]

RATE_CELL = "$B$1"
_CF_COLUMNS = (
    ("Year", "year"),
    ("CAPEX", "capex"),
    ("Replacements", "replacements"),
    ("Fixed O&M", "opex_fixed"),
    ("Variable O&M", "opex_variable"),
    ("Bill (baseline)", "bill_baseline"),
    ("Bill (option)", "bill_option"),
    ("Savings", "savings"),
    ("Salvage", "salvage"),
    ("Net cash flow", None),
    ("Discount factor", None),
    ("Discounted cash flow", None),
    ("Cumulative discounted", None),
    ("Market revenue at duals (excluded)", "market_revenue_at_duals"),
)
_HEADER_ROW = 4
_COL = {label: j for j, (label, _f) in enumerate(_CF_COLUMNS, start=1)}


def _text(ws, row: int, col: int, value: Any):
    """A value that is never a formula (labels, ledger text, provenance)."""
    cell = ws.cell(row=row, column=col)
    cell.value = value
    if isinstance(value, str) and value.startswith("="):
        cell.data_type = "s"
    return cell


def _formula(ws, row: int, col: int, formula: str):
    cell = ws.cell(row=row, column=col)
    cell.value = formula
    return cell


def _basis_label(case: InvestmentCase) -> str:
    b = case.basis
    terms = b.terms.value if hasattr(b.terms, "value") else str(b.terms)
    tax = "pre-tax" if b.tax == "pre" else "post-tax"
    subsidy = "without subsidy" if b.subsidy == "excl" else "with subsidy"
    return f"{terms}, {tax}, {subsidy}"


def _cash_flows(ws, case: InvestmentCase, title: str = "Cash flows") -> int | None:
    """The cash-flow sheet; returns the NPV row (None when there is no cash flow)."""
    ws.title = title
    _text(ws, 1, 1, "Discount rate (real)")
    ws.cell(1, 2).value = case.discount_rate
    _text(ws, 1, 3, "Currency year")
    ws.cell(1, 4).value = case.currency_year
    _text(ws, 2, 1, "Basis")
    _text(ws, 2, 2, _basis_label(case))
    _text(ws, 2, 3, "Status")
    _text(ws, 2, 4, case.status)
    for j, (label, _field) in enumerate(_CF_COLUMNS, start=1):
        _text(ws, _HEADER_ROW, j, label)
    if not case.years:
        return None
    letter = {label: ws.cell(_HEADER_ROW, j).column_letter
              for j, (label, _f) in enumerate(_CF_COLUMNS, start=1)}
    first = _HEADER_ROW + 1
    for i, y in enumerate(case.years):
        r = first + i
        for j, (label, field) in enumerate(_CF_COLUMNS, start=1):
            if field is None:
                continue
            v = getattr(y, field)
            if v is not None:  # a null figure stays an empty cell (listed below)
                ws.cell(r, j).value = v
        L = letter
        _formula(ws, r, _COL["Net cash flow"], (f"={L['Savings']}{r}+{L['Salvage']}{r}-{L['CAPEX']}{r}"
                             f"-{L['Replacements']}{r}-{L['Fixed O&M']}{r}"
                             f"-{L['Variable O&M']}{r}"))
        _formula(ws, r, _COL["Discount factor"], f"=1/(1+{RATE_CELL})^{L['Year']}{r}")
        _formula(ws, r, _COL["Discounted cash flow"], f"={L['Net cash flow']}{r}*{L['Discount factor']}{r}")
        cum = L["Cumulative discounted"]
        _formula(ws, r, _COL["Cumulative discounted"], (f"={L['Discounted cash flow']}{r}" if i == 0
                             else f"={cum}{r - 1}+{L['Discounted cash flow']}{r}"))
    last = first + len(case.years) - 1
    net = letter["Net cash flow"]
    npv_row = last + 2
    _text(ws, npv_row, 1, "NPV")
    # Excel's NPV discounts its FIRST value one period: CF0 stays outside.
    _formula(ws, npv_row, 2, f"={net}{first}+NPV({RATE_CELL},{net}{first + 1}:{net}{last})")
    _text(ws, npv_row + 1, 1, "Check: sum of discounted cash flows")
    _formula(ws, npv_row + 1, 2, f"=SUM({letter['Discounted cash flow']}{first}:"
                                 f"{letter['Discounted cash flow']}{last})")
    _text(ws, npv_row + 2, 1, "Market revenue at duals")
    _text(ws, npv_row + 2, 2, "reported, not in the net cash flow (the bill prices that energy)")
    unavailable = sorted({f"{k}: {v}" for y in case.years for k, v in y.unavailable.items()})
    for k, note in enumerate(unavailable):
        _text(ws, npv_row + 4 + k, 1, "Not available" if k == 0 else None)
        _text(ws, npv_row + 4 + k, 2, note)
    return npv_row


# Gate S5 [N5] (carried to S7): the IRR, the paybacks and the LCOS are the
# model's values at the case's rate; they are written as STATIC cells and
# labelled so. Only the NPV cell is a live formula (it reads the cash-flow
# sheet, which reads the rate cell).
LIVE = "live formula: follows the rate cell and the cash flows"
STATIC = ("static value from the model at the case's rate: it does not move when "
          "the rate cell or a cash flow is edited")


def _kpis(ws, case: InvestmentCase, npv_row: int | None, title: str = "KPIs",
          cash_flows_title: str = "Cash flows") -> None:
    ws.title = title
    for j, h in enumerate(("kpi", "value", "unit", "not available because", "cell"), start=1):
        _text(ws, 1, j, h)
    rows: list[tuple[str, Any, str, str | None]] = [
        ("status", case.status, "", None),
        ("option_id", case.option_id, "", None),
        ("horizon_years", case.horizon_years, "years", None),
        ("discount_rate", case.discount_rate, "per unit (real)", None),
    ]
    k = case.kpis
    if k is not None:
        for key, unit in _KPI_UNITS.items():
            rows.append((key, getattr(k, key), unit, k.unavailable.get(key)))
    r = 2
    for key, value, unit, why in rows:
        _text(ws, r, 1, key)
        live = key == "npv" and npv_row is not None
        if live:
            _formula(ws, r, 2, f"='{cash_flows_title}'!B{npv_row}")
        elif isinstance(value, str):
            _text(ws, r, 2, value)
        elif value is not None:
            ws.cell(r, 2).value = value
        _text(ws, r, 3, unit)
        _text(ws, r, 4, why)
        if k is not None and key in _KPI_UNITS:
            _text(ws, r, 5, LIVE if live else STATIC)
        r += 1
    if case.value_streams:
        r += 1
        for j, h in enumerate(("value stream (annual saving by bill component)",
                               "value", "share", "engine"), start=1):
            _text(ws, r, j, h)
        for s in case.value_streams:
            r += 1
            _text(ws, r, 1, s.key or s.label)
            ws.cell(r, 2).value = s.annual_value
            ws.cell(r, 3).value = s.share
            _text(ws, r, 4, s.engine)
    mr = case.market_revenue_at_duals
    if mr is not None:
        r += 2
        _text(ws, r, 1, "market_revenue_at_duals (annual, excluded from the cash flow)")
        ws.cell(r, 2).value = mr.annual_value
        _text(ws, r, 3, "EUR")
        _text(ws, r, 4, mr.engine)


_KPI_UNITS = {"npv": "EUR", "irr": "per unit", "payback_simple": "years",
              "payback_discounted": "years", "lcoe": "EUR/MWh", "lcos": "EUR/MWh",
              "lcoh": "EUR/MWh", "dscr_min": "ratio", "capex_total": "EUR",
              "salvage_eur": "EUR"}

_LEDGER_COLUMNS = ("key", "label", "value", "unit", "currency_year", "basis",
                   "provenance", "status", "sensitivity_flag", "range_low",
                   "range_high", "source", "source_year", "source_url")


def _assumptions(ws, ledger: AssumptionsLedger) -> None:
    ws.title = "Assumptions"
    for j, h in enumerate(_LEDGER_COLUMNS, start=1):
        _text(ws, 1, j, h)
    for i, row in enumerate(ledger.rows, start=2):
        values = {
            "key": row.key, "label": row.label, "value": row.value, "unit": row.unit,
            "currency_year": row.currency_year,
            "basis": row.basis.value if hasattr(row.basis, "value") else row.basis,
            "provenance": row.provenance, "status": row.status,
            "sensitivity_flag": bool(row.sensitivity_flag),
            "range_low": row.range.low if row.range else None,
            "range_high": row.range.high if row.range else None,
            "source": row.source, "source_year": row.source_year,
            "source_url": row.source_url,
        }
        for j, col in enumerate(_LEDGER_COLUMNS, start=1):
            _text(ws, i, j, values[col])


def _provenance(ws, case: InvestmentCase, ledger: AssumptionsLedger) -> None:
    ws.title = "Provenance"
    p = case.provenance
    fidelity = case.fidelity.value if case.fidelity is not None else None
    rows: list[tuple[str, Any]] = [
        ("study_id", case.study_id), ("option_id", case.option_id),
        ("case_id", case.case_id), ("status", case.status),
        ("engine", case.engine),
        ("engines", ", ".join(p.engines) if p else None),
        ("basis", _basis_label(case)),
        ("perspective", case.perspective.value),
        ("currency_year", case.currency_year),
        ("fidelity", fidelity),
        ("library_version", (p.library_version if p else None) or ledger.ledger_version),
        ("ledger_hash", p.ledger_hash if p else None),
        ("model_hash", p.model_hash if p else None),
        ("project_ref", p.project_ref if p else None),
        ("tariff_id", p.tariff_id if p else None),
        ("discount_rate", case.discount_rate),
        ("horizon_years", case.horizon_years),
        ("salvage_basis", case.salvage_basis),
        ("generated_at", datetime.now(tz=UTC).isoformat(timespec="seconds")),
    ]
    for gap in case.upfront_gaps:
        rows += [
            (f"upfront_{gap.asset}_ledger_eur", gap.ledger_upfront_eur),
            (f"upfront_{gap.asset}_back_calculated_single_lifetime_eur",
             gap.back_calculated_upfront_eur),
            (f"upfront_{gap.asset}_gap_eur", gap.gap_eur),
        ]
    for note in case.honesty_notes:
        rows.append(("honesty_note", note))
    for i, (key, value) in enumerate(rows, start=1):
        _text(ws, i, 1, key)
        _text(ws, i, 2, value)


def write_proforma_xlsx(case: InvestmentCase, ledger: AssumptionsLedger) -> bytes:
    """The case and its ledger as an .xlsx workbook (bytes)."""
    import openpyxl

    wb = openpyxl.Workbook()
    npv_row = _cash_flows(wb.active, case)
    _kpis(wb.create_sheet(), case, npv_row)
    _assumptions(wb.create_sheet(), ledger)
    _provenance(wb.create_sheet(), case, ledger)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

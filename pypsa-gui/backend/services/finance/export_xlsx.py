"""
The investment-case workbook (IC P4 plan WP4.6d; the `asset_results` pattern).

`build_workbook(report, *, project=None) -> bytes` writes:
- `About` — the case, the assumptions hash, every pack with its hash, the CFADS
  definition, the WACC gate, the counterfactual's provenance, the completeness
  of each section and every flag the sections carry;
- `Summary` — the headline figures;
- one sheet per report section (`IC_REPORT_SECTIONS`), its status, note and
  payload (scalars as rows; lists of records as tables);
- `CashflowLines` — every `CashflowLine` with its provenance.

An unknown value (`None`) is written as an explicit `not_established` cell,
never an empty one or a 0 (ADR-0001). A text value that a spreadsheet would
read as a formula (starting with = + - @, a tab or a carriage return — a
user-named asset, party or contract) is stored as TEXT, never a formula
(formula injection guard).
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from typing import Any

from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from models.finance import (
    IC_EXPORT_KEYS, IC_REPORT_SECTIONS, InvestmentCaseReport, export_investment_case,
)
from services.finance.debt import CFADS_DEFINITION

NOT_ESTABLISHED = "not_established"
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")



def _put(ws, row: int, col: int, value: Any) -> None:
    """One cell: None → `not_established`; a formula-looking string → text."""
    cell = ws.cell(row=row, column=col)
    if value is None:
        cell.value = NOT_ESTABLISHED
        return
    if isinstance(value, bool):
        cell.value = value
        return
    if isinstance(value, (int, float)):
        cell.value = value
        return
    if not isinstance(value, str):
        value = json.dumps(value, default=str, sort_keys=True)
    # C0 control characters are illegal in xlsx cells (openpyxl raises): strip
    # them rather than fail the export (WP4.6b review B4).
    value = ILLEGAL_CHARACTERS_RE.sub("", value)
    cell.value = value
    if value.startswith(_FORMULA_LEADS):
        cell.data_type = "s"                 # never a formula


def _rows(ws, rows: list[list[Any]], start: int = 1) -> int:
    r = start
    for row in rows:
        for c, v in enumerate(row, start=1):
            _put(ws, r, c, v)
        r += 1
    return r


def _flatten(prefix: str, obj: Any, out: list[list[Any]], tables: list[tuple[str, list]]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out, tables)
    elif isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        tables.append((prefix, obj))
    elif isinstance(obj, list):
        out.append([prefix, *obj] if obj else [prefix, "(empty)"])
    else:
        out.append([prefix, obj])


def _flags(report: InvestmentCaseReport) -> list[str]:
    found: set[str] = set()
    for st in report.sections.values():
        payload = st.payload or {}
        for key in ("flags", "reasons"):
            v = payload.get(key)
            if isinstance(v, list):
                found.update(str(x) for x in v)
            elif isinstance(v, dict):
                for xs in v.values():
                    if isinstance(xs, list):
                        found.update(str(x) for x in xs)
    return sorted(found)


def _counterfactual_summary(block: Any) -> str | None:
    """The About row: the counterfactual's provenance in one line (the full
    block is on the Project sheet). None → `not_established`."""
    if not isinstance(block, dict):
        return None
    if not block.get("present"):
        return f"none — {block.get('basis')}"
    sources = ", ".join(block.get("sources") or []) or "(no lines)"
    reasons = block.get("reasons") or []
    state = "not established: " + ", ".join(reasons) if reasons else "established"
    return (f"{block.get('basis')}; {block.get('n_lines')} lines from {sources}; {state}")


def build_workbook(report: InvestmentCaseReport, *, project: str | None = None) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    about = wb.active
    about.title = "About"
    project_payload = (report.sections.get("project").payload or {}) \
        if report.sections.get("project") else {}
    rows: list[list[Any]] = [
        ["Case", report.case_id],
        ["Project", project or "(unsaved)"],
        ["Reference design", report.reference_design_id],
        ["Assumptions hash", report.assumptions_hash],
        ["Generated at", datetime.now(timezone.utc).isoformat(timespec="seconds")],
        ["CFADS", CFADS_DEFINITION],
        ["Shed cost", "excluded (an opportunity cost on both sides, disclosed)"
         if report.excludes_shed_cost else "included"],
        ["WACC vs discount rate consistent", report.gates.wacc_vs_discount_rate_consistent],
        ["Value-flow conservation", report.gates.conservation_ok],
        ["Counterfactual", _counterfactual_summary(project_payload.get("counterfactual"))],
    ]
    for jur, h in sorted(report.packs.items()):
        rows.append([f"Pack {jur}", h])
    for name in IC_REPORT_SECTIONS:
        rows.append([f"Section {name}", report.completeness.get(name)])
    for f in _flags(report):
        rows.append(["Flag", f])
    _rows(about, rows)

    summary = wb.create_sheet("Summary")
    # The export view (the gate flags live under `gates`; reading the dump's
    # top level wrote them as not established — P4 gate driver finding).
    raw = export_investment_case(report)
    _rows(summary, [["Figure", "Value"]] + [
        [k, raw.get(k)] for k in IC_EXPORT_KEYS
        if k not in ("completeness", "sections", "cashflow_lines", "gates", "packs", "pipeline")
        and not isinstance(raw.get(k), (dict, list))])

    for name in IC_REPORT_SECTIONS:
        ws = wb.create_sheet(name[:31])
        st = report.sections.get(name)
        status = report.completeness.get(name)
        r = _rows(ws, [["Status", status], ["Note", st.note if st else None]])
        if st is None or st.payload is None:
            continue
        flat: list[list[Any]] = []
        tables: list[tuple[str, list]] = []
        _flatten("", st.payload, flat, tables)
        r = _rows(ws, [[]] + flat, r)
        for title, records in tables:
            cols = sorted({k for rec in records for k in rec})
            r = _rows(ws, [[], [title], cols] + [[rec.get(c) for c in cols] for rec in records], r)

    cf = wb.create_sheet("CashflowLines")
    header = ["year", "participant", "counterparty", "value_stream", "tariff_item", "asset",
              "amount", "source", "mode", "pack_hash", "seed", "source_id", "contract_id",
              "period"]
    body = [[ln.year, ln.participant, ln.counterparty, ln.value_stream, ln.tariff_item, ln.asset,
             ln.amount, ln.provenance.source, ln.provenance.mode, ln.provenance.pack_hash,
             ln.provenance.seed, ln.provenance.source_id, ln.provenance.contract_id,
             ln.provenance.period] for ln in report.cashflow_lines]
    _rows(cf, [header] + body)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


__all__ = ["build_workbook", "NOT_ESTABLISHED", "CFADS_DEFINITION"]

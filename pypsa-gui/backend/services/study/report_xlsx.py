"""
The decision report as a workbook (plan S7).

Sheets, in order:

* ``Verdict`` — the verdict class, its rendered sentence, every report fact
  (value, unit, basis, currency year, engine, fidelity, why not established),
  the disclosures with their sentences, the stale flag and its reasons;
* ``Value streams`` and ``Tornado`` — the drivers section's tables;
* ``Assumptions`` — the ledger (``proforma_xlsx._assumptions``) plus an
  ``edited`` column;
* ``Provenance`` — hashes, engines, fidelity, library version;
* per option with an established case, ``Cash flows <option>`` and
  ``KPIs <option>`` — written by ``services/study/proforma_xlsx.py``'s own
  sheet writers, so the report's cash flows are the case workbook's (live
  discounting formulas, the NPV pinned as ``=CF0 + NPV(rate, CF1:CFn)``,
  the IRR and paybacks labelled STATIC).

Every text cell goes through ``proforma_xlsx._text``: a string that starts
with ``=`` (a study name, a ledger source) is stored as a string, never a
formula.
"""
from __future__ import annotations

import io
from typing import Any

from models.study import AssumptionsLedger, DecisionReport, InvestmentCase
from services.study import proforma_xlsx as PX
from services.study import report as R

__all__ = ["write_report_xlsx"]


def _rows(ws, title: str, header: list[str], rows: list[list[Any]]) -> None:
    ws.title = title
    for j, h in enumerate(header, start=1):
        PX._text(ws, 1, j, h)
    for i, row in enumerate(rows, start=2):
        for j, v in enumerate(row, start=1):
            if isinstance(v, str) or v is None:
                PX._text(ws, i, j, v)
            else:
                ws.cell(i, j).value = v


def _verdict(ws, report: DecisionReport, prose: dict[str, list[str]], stale: bool,
             stale_reasons: list[str]) -> None:
    es = report.sections["executive_summary"].payload or {}
    rows: list[list[Any]] = [
        ["study", report.study_name, "", "", "", "", "", ""],
        ["verdict_status", es.get("verdict_status"), "", "", "", "", "", ""],
        ["verdict_class", es.get("verdict_class"), "", "", "", "", "", ""],
        ["verdict", " ".join(prose.get("executive_summary") or []), "", "", "", "", "", ""],
    ]
    # Gate S9 BC-S9-1: the drivers the verdict sentence refers to, by label.
    rows += [["verdict_driver", d["label"], d["key"], es.get("drivers_heading") or "",
              "", "", "", ""] for d in es.get("driver_labels") or []]
    rows.append(["stale", "yes" if stale else "no", "", "", "", "", "", ""])
    rows += [["stale_reason", r, R.help_for(r)[0], "", "", "", "", ""] for r in stale_reasons]
    rows.append(["", "", "", "", "", "", "", ""])
    rows.append(["fact", "value", "unit", "basis", "currency_year", "engine", "fidelity",
                 "not established because"])
    for key, f in report.facts.items():
        b = f.basis
        rows.append([key, f.value, f.unit,
                     None if b is None else f"{b.terms.value}, {b.tax}-tax, subsidy {b.subsidy}",
                     f.currency_year, f.engine,
                     None if f.fidelity is None else f.fidelity.value, f.unavailable])
    rows.append(["", "", "", "", "", "", "", ""])
    rows.append(["disclosure", "sentence", "source", "", "", "", "", ""])
    rows += [[d.code, d.text, d.source, "", "", "", "", ""] for d in report.required_disclosures]
    _rows(ws, "Verdict", ["field", "value", "", "", "", "", "", ""], rows)


def write_report_xlsx(report: DecisionReport, cases: dict[str, InvestmentCase],
                      ledger: AssumptionsLedger, *, stale: bool | None = None,
                      stale_reasons: list[str] | None = None) -> bytes:
    import openpyxl

    prose = R.validate_prose(report)
    stale = report.stale if stale is None else stale
    stale_reasons = report.stale_reasons if stale_reasons is None else stale_reasons
    wb = openpyxl.Workbook()
    _verdict(wb.active, report, prose, stale, stale_reasons)
    drivers = report.sections["drivers"].payload or {}
    _rows(wb.create_sheet(), "Value streams",
          ["stream", "key", "annual_value", "share", "engine", "basis"],
          [[s.get("label"), s.get("key"), s.get("annual_value"), s.get("share"),
            s.get("engine"), drivers.get("value_streams_label")]
           for s in drivers.get("value_streams") or []])
    _rows(wb.create_sheet(), "Tornado",
          ["driver", "key", "unit", "low_value", "high_value", "battery_npv_low",
           "battery_npv_high", "swing", "evaluation"],
          [[r.get("label"), r.get("key"), r.get("unit"), r.get("low_value"),
            r.get("high_value"), r.get("npv_low"), r.get("npv_high"), r.get("swing"),
            r.get("evaluation")] for r in drivers.get("tornado") or []])
    ws = wb.create_sheet()
    PX._assumptions(ws, ledger)
    col = len(PX._LEDGER_COLUMNS) + 1
    PX._text(ws, 1, col, "edited")
    for i, row in enumerate(ledger.rows, start=2):
        edited = row.status == "customised" or row.provenance in ("user", "measured")
        PX._text(ws, i, col, "edited" if edited else "")
    app = report.sections["appendix"].payload or {}
    prov = [["study_id", report.study_id], ["question_id", report.question_id],
            ["generated_at", report.generated_at.isoformat()],
            ["currency_year", report.currency_year],
            ["fidelity", None if report.fidelity is None else report.fidelity.value]]
    prov += [[k, app.get(k)] for k in ("tariff_id", "library_version", "ledger_hash",
                                       "base_network_hash")]
    forks = app.get("option_forks") or {}
    prov += [[f"network_hash {forks.get(k, k)}", h]
             for k, h in (app.get("option_network_hashes") or {}).items()]
    prov += [["honesty_note", n] for n in report.honesty_notes]
    _rows(wb.create_sheet(), "Provenance", ["field", "value"], prov)
    for oid, case in sorted(cases.items()):
        if case.status != "ok":
            continue
        cf_title, kpi_title = f"Cash flows {oid}"[:31], f"KPIs {oid}"[:31]
        npv_row = PX._cash_flows(wb.create_sheet(), case, title=cf_title)
        PX._kpis(wb.create_sheet(), case, npv_row, title=kpi_title, cash_flows_title=cf_title)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

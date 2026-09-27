"""
`InvestmentCaseReport` persistence helpers (P0 WP0.5); the assembler
`assemble_investment_case_report` joins this module in P7.

Mirrors `services/adequacy/eh_report.py`: the report lives in the project's
solver state under `investment_case_report` as a JSON dict (never a model
object, so `results_state.pkl` stays readable by the restricted unpickler),
and the HTTP view is the stable `export_investment_case` projection.
"""
from __future__ import annotations

from typing import Any

from models.finance import InvestmentCaseReport, export_investment_case

IC_REPORT_STORE_KEY = "investment_case_report"


def store_ic_report(store: dict, report: InvestmentCaseReport) -> None:
    store[IC_REPORT_STORE_KEY] = report.model_dump(mode="json")


def load_ic_report(store: dict) -> InvestmentCaseReport | None:
    raw = store.get(IC_REPORT_STORE_KEY)
    if not raw:
        return None
    if isinstance(raw, InvestmentCaseReport):
        return raw
    return InvestmentCaseReport.model_validate(raw)


def ic_report_http_payload(store: dict) -> tuple[dict[str, Any] | None, int]:
    """(body, status) for GET /results/investment_case/report (204 when absent)."""
    report = load_ic_report(store)
    if report is None:
        return None, 204
    return export_investment_case(report), 200

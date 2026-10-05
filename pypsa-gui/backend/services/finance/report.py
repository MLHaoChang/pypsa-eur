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


# ── billing frames and commercial terms (review WP0.5 #1) ────────────────────
#
# Both ride `results_state.pkl`, which is read back through a RESTRICTED
# unpickler (bundles can come from anyone). That allow-list admits UTC and
# naive datetime indexes but refuses pytz / zoneinfo objects, pandas
# Timestamp / Period scalars — and one refused value drops EVERY side result
# on reload. The billing pass rates in local time (DST moves TOU windows), so
# the store side converts to naive UTC and records the zone as a string; the
# load side restores it. Keys and values of the commercial terms become plain
# JSON (ISO strings, lists, floats).

BILLING_FRAMES_STORE_KEY = "billing_frames"
COMMERCIAL_TERMS_STORE_KEY = "last_commercial_terms"


def _frame_to_store(df):
    import pandas as pd

    idx = df.index
    if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None:
        out = df.copy()
        out.index = idx.tz_convert("UTC").tz_localize(None)
        return {"__tz__": str(idx.tz), "frame": out}
    return {"__tz__": None, "frame": df}


def _frame_from_store(entry):
    if not isinstance(entry, dict) or "frame" not in entry:
        return entry  # legacy / hand-written: pass through
    df, tz = entry["frame"], entry.get("__tz__")
    if tz:
        df = df.copy()
        df.index = df.index.tz_localize("UTC").tz_convert(tz)
    return df


def store_billing_frames(store: dict, frames: dict | None) -> None:
    store[BILLING_FRAMES_STORE_KEY] = (
        None if frames is None
        else {str(k): _frame_to_store(v) for k, v in frames.items()})


def load_billing_frames(store: dict) -> dict | None:
    raw = store.get(BILLING_FRAMES_STORE_KEY)
    if raw is None:
        return None
    return {k: _frame_from_store(v) for k, v in raw.items()}


def _jsonable(x: Any) -> Any:
    import numpy as np
    import pandas as pd

    if isinstance(x, dict):
        return {_jsonable_key(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (pd.Timestamp, pd.Period)):
        return x.isoformat() if isinstance(x, pd.Timestamp) else str(x)
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, (str, int, float, bool)) or x is None:
        return x
    raise TypeError(f"commercial terms value of type {type(x).__name__} is not JSON-able")


def _jsonable_key(k: Any) -> str:
    v = _jsonable(k)
    return v if isinstance(v, str) else str(v)


def store_commercial_terms(store: dict, terms: dict | None) -> None:
    store[COMMERCIAL_TERMS_STORE_KEY] = None if terms is None else _jsonable(terms)

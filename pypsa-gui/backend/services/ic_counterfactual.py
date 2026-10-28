"""The `ic.counterfactual` gate fact (study harness issue 19): whether the
finished investment case compares against an established counterfactual.

Read from the stored run, the body of `GET /results/investment_case`
(`routers.results.get_investment_case`), never by re-running the case:

- `not_run`: no case, a case still running, a failed run (no evidence either
  way), or a stored report that is stale (an input changed since the run);
- `established`: a `done` case with no `counterfactual_not_established:*`
  flag, on the site-today basis when flagged `counterfactual_basis:site_today`,
  else the analytic one;
- `not_established`: a `done` case carrying such flags (the reasons), or a
  refused one (its refusal code).

`counterfactual_text` quotes the case's own sentences for its flags (the IC
export's caveats and the baseline wording); the harness report never
restates a price.
"""
from __future__ import annotations

from collections.abc import Iterable

ESTABLISHED, NOT_ESTABLISHED, NOT_RUN = "established", "not_established", "not_run"
_BLOCKER = "counterfactual_not_established:"
_SITE_TODAY = "counterfactual_basis:site_today"


def classify(body: dict | None) -> dict:
    """``{"state", "basis", "reasons"}`` for one investment-case status body
    (None: the route answered 204, no run and no report)."""
    if not body:
        return {"state": NOT_RUN, "basis": None, "reasons": ["no_case"]}
    status = body.get("status")
    if status not in ("done", "refused", "failed"):
        return {"state": NOT_RUN, "basis": None, "reasons": [f"case_{status or 'idle'}"]}
    if status == "failed":
        return {"state": NOT_RUN, "basis": None, "reasons": ["case_failed"]}
    report = body.get("report") or {}
    if not report.get("present"):
        return {"state": NOT_RUN, "basis": None, "reasons": ["no_report"]}
    if report.get("stale") is not False:
        return {"state": NOT_RUN, "basis": None,
                "reasons": ["case_stale", *(f"changed:{c}" for c in report.get("changed") or [])]}
    if status == "refused":
        return {"state": NOT_ESTABLISHED, "basis": None,
                "reasons": [str(body.get("error_code") or "refused")]}
    flags = [str(f) for f in body.get("flags") or []]
    blockers = [f for f in flags if f.startswith(_BLOCKER)]
    basis = "site_today" if _SITE_TODAY in flags else "analytic"
    if blockers:
        return {"state": NOT_ESTABLISHED, "basis": basis, "reasons": blockers}
    return {"state": ESTABLISHED, "basis": basis, "reasons": []}


def read(ctx_call) -> dict:
    """The fact for the active project: ``ctx_call(fn)`` calls ``fn`` with the
    run's project context bound (`services.run_gates._on`)."""
    from fastapi import Response

    from routers.results import get_investment_case

    body = ctx_call(get_investment_case)
    return classify(None if isinstance(body, Response) else body)


def counterfactual_text(flags: Iterable[str]) -> list[str]:
    """The case's own sentences on its counterfactual, quoted for ``flags``:
    the IC export's caveats (a flag matches its key alone or with a
    ``:<value>`` suffix, as the export reads them), then the baseline's notes."""
    from services.finance.baseline_text import flag_text
    from services.finance.export_xlsx import BASELINE_CAVEATS

    flags = [str(f) for f in flags]
    caveats = [text for key, text in BASELINE_CAVEATS
               if any(f == key or f.startswith(key + ":") for f in flags)]
    return [*caveats, *(t for t in (flag_text(f) for f in flags) if t)]

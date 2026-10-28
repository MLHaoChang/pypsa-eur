"""The `ic.counterfactual` gate fact (study harness issue 19): whether the
finished investment case compares against an established counterfactual.

Read from the stored run, the body of `GET /results/investment_case`
(`routers.results.get_investment_case`), never by re-running the case:

- `not_run`: no case, a case still running, a failed run (no evidence either
  way), a stored report that is stale (an input changed since the run), or
  results that no longer describe the network (it was edited after the solve
  the case read: `workflow_tools.solve_freshness`);
- `established`: a `done` case with no `counterfactual_not_established:*`
  flag, on the site-today basis when flagged `counterfactual_basis:site_today`,
  else the analytic one;
- `not_established`: a `done` case carrying such flags (the reasons), or a
  refused one (its refusal code).

`ic.baseline_keeps` (study harness issue 20) lists the owned existing assets
the case's baseline keeps (`BaselinePlan.kept`), `not_run` as above, and
`not_established` when an asset the template declares as existing
(`existing_assets`: the microgrids' `MG_EXISTING`) is not among them.

`counterfactual_text` quotes the case's own sentences for its flags (the IC
export's caveats and the baseline wording); the harness report never
restates a price.
"""
from __future__ import annotations

from collections.abc import Iterable

ESTABLISHED, NOT_ESTABLISHED, NOT_RUN = "established", "not_established", "not_run"
_BLOCKER = "counterfactual_not_established:"
_SITE_TODAY = "counterfactual_basis:site_today"


def classify(body: dict | None, results_current: bool = True) -> dict:
    """``{"state", "basis", "reasons"}`` for one investment-case status body
    (None: the route answered 204, no run and no report) and whether the
    solve's results still describe the network."""
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
    if not results_current:
        return {"state": NOT_RUN, "basis": None, "reasons": ["case_stale", "results_not_current"]}
    if status == "refused":
        return {"state": NOT_ESTABLISHED, "basis": None,
                "reasons": [str(body.get("error_code") or "refused")]}
    flags = [str(f) for f in body.get("flags") or []]
    blockers = [f for f in flags if f.startswith(_BLOCKER)]
    basis = "site_today" if _SITE_TODAY in flags else "analytic"
    if blockers:
        return {"state": NOT_ESTABLISHED, "basis": basis, "reasons": blockers}
    return {"state": ESTABLISHED, "basis": basis, "reasons": []}


def _case(ctx, on) -> tuple[dict | None, bool]:
    """The stored case's status body (None: no run and no report) and whether
    the solve's results still describe the network."""
    from fastapi import Response

    from routers.results import get_investment_case
    from services import workflow_tools

    body = on(get_investment_case)
    return (None if isinstance(body, Response) else body,
            workflow_tools.solve_freshness(ctx)["results_current"])


def read(ctx, on) -> dict:
    """The fact for the run's project ``ctx``: ``on(fn)`` calls ``fn`` with
    that context bound (`services.run_gates._on`)."""
    return classify(*_case(ctx, on))


# ── `ic.baseline_keeps` (study harness issue 20) ─────────────────────────────

def existing_assets(template_id: str | None) -> tuple[str, ...] | None:
    """The owned existing assets a template declares its baseline keeps (the
    island as it stands today, owner decision M2e (a)); None when it declares
    none, and then the fact lists what is kept with no guard."""
    if template_id == "eh_microgrid":
        from project_templates.eh_templates import MG_EXISTING
        return MG_EXISTING
    if template_id == "eh_microgrid_staged":
        from project_templates.eh_staged_hubs import MG_EXISTING
        return MG_EXISTING
    return None


def baseline_keeps(n, cfg, existing: tuple[str, ...] | None, owner: str | None = None) -> dict:
    """``{"state", "kept", "reasons"}``: the owned existing rows the case's
    baseline keeps, and `not_established` with ``baseline_drops:<name>`` for
    each asset of ``existing`` missing from them (a priced or renamed unit).
    The names are compared as an exact set, never by a name pattern (GK A)."""
    from services.results.finance_case import baseline_plan

    plan = baseline_plan(n, cfg, owner)
    if plan is None:
        return {"state": NOT_ESTABLISHED, "kept": [], "reasons": ["no_baseline_plan"]}
    kept = [name for _comp, name in plan.kept]
    kept_set = set(kept)
    missing = [a for a in existing or () if a not in kept_set]
    if missing:
        return {"state": NOT_ESTABLISHED, "kept": kept,
                "reasons": [f"baseline_drops:{a}" for a in missing]}
    return {"state": ESTABLISHED, "kept": kept, "reasons": []}


def read_baseline_keeps(ctx, on, template_id: str | None) -> dict:
    """The fact for ``ctx``: `not_run` unless the stored case is current (as
    `read`), so the list never describes a network the case did not read;
    else the plan of the case's own baseline."""
    body, current = _case(ctx, on)
    fact = classify(body, current)
    if fact["state"] == NOT_RUN:
        return {"state": NOT_RUN, "kept": [], "reasons": fact["reasons"]}

    def plan():
        from routers.results import _state
        from services.pypsa_service import PyPSAService

        # The case route's own call (`routers.results._ic_build_case`):
        # `baseline_plan(n, cfg, owner)` under the network lock, with the
        # owner the run was started for.
        with PyPSAService.get_lock():
            return baseline_keeps(PyPSAService.get_network(), _state["solver_config"],
                                  existing_assets(template_id), body.get("owner"))
    return on(plan)


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

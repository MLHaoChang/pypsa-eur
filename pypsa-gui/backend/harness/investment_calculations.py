"""Bounded investment-case returns for checked answers (checked-results S8).

NPV and IRR of the post-tax equity and project cash, rebuilt from the stored
investment-case report's own cash rows at the rates that report records as the
ones its returns use (`cost_of_equity`, `wacc_nominal`), with the engine's
timing (`services/finance/metrics.py`): end-of-year discounting, index 0 (the
financial-close year) undiscounted. A figure is given only when it matches the
report's headline; an IRR only when the cash changes sign exactly once (one
root in (-1, inf)) and the root is bracketed in [-0.99, 10]. The LCOE, payback,
cover ratios, the solved PPA price and the counterfactual are not checkable yet.
"""

import math
import re

from harness import profile_common as common

PROFILE = "investment-case/1"
REL_TOLERANCE = 1e-6  # of the sum of |cash|
IRR_TOLERANCE = 1e-8  # absolute, against the report's headline
BRACKET = (-0.99, 10.0)
ZERO_CASH = 1e-12  # the engine's sign-change threshold
REFUSALS = frozenset({"network_scope_required", "investment_case_not_current", "unsupported_snapshot_scope"})
# (metric, cash series, recorded rate key, headline key)
SERIES = {
    "equity_npv": ("equity_post_tax", "cost_of_equity", "equity_post_tax_npv"),
    "project_npv": ("project_post_tax", "wacc_nominal", "project_post_tax_npv"),
    "equity_irr": ("equity_post_tax", None, "equity_post_tax_irr"),
    "project_irr": ("project_post_tax", None, "project_post_tax_irr"),
}
METRICS = {
    "equity_npv": {
        "formula_id": "equity-post-tax-npv/1",
        "unit": "report currency",
        "label": "Equity NPV (post tax, at the cost of equity)",
    },
    "project_npv": {
        "formula_id": "project-post-tax-npv/1",
        "unit": "report currency",
        "label": "Project NPV (post tax, at the WACC)",
    },
    "equity_irr": {
        "formula_id": "equity-post-tax-irr/1",
        "unit": "fraction/year",
        "label": "Equity IRR (post tax)",
    },
    "project_irr": {
        "formula_id": "project-post-tax-irr/1",
        "unit": "fraction/year",
        "label": "Project IRR (post tax)",
    },
}
DISCLOSURE = (
    "Rebuilt from the stored investment-case report's cash rows: end-of-year discounting with the "
    "financial-close year undiscounted, at the rates the report records; an IRR only when the cash "
    "changes sign once. LCOE, payback, DSCR, LLCR, PLCR, the solved PPA price and the counterfactual "
    "are not checkable yet."
)


def current(ctx):
    """The investment-case status as its own tab reads it: the run, and the stored
    report's staleness against its own assumptions key. Read outside the network
    lock, as the route does (the campus source is read outside it)."""
    from routers.results import get_investment_case

    body = get_investment_case()
    if getattr(body, "status_code", None) == 204:
        return None
    report = body.get("report") or {}
    return {"status": body.get("status"), "present": report.get("present"), "stale": report.get("stale"),
            "assumptions_hash": report.get("assumptions_hash"), "current_hash": report.get("current_hash")}


def is_current(state):
    return bool(state and state["present"] and state["stale"] is False and state["status"] != "running"
                and state["assumptions_hash"] and state["assumptions_hash"] == state["current_hash"])


def source(ctx, entity, state):
    from harness.calculations import _number

    if entity != "network":
        raise ValueError("network_scope_required")
    stored = ctx.solver_state.get("investment_case_report")
    if stored is not None and hasattr(stored, "model_dump"):
        stored = stored.model_dump(mode="json")
    if not is_current(state) or not stored or stored.get("assumptions_hash") != state["assumptions_hash"]:
        raise ValueError("investment_case_not_current")
    payload = ((stored.get("sections") or {}).get("project") or {}).get("payload") or {}
    cash = payload.get("cash") or {}
    years = [str(y) for y in payload.get("years") or []]
    if not years:
        raise ValueError("investment_case_not_current")
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": years,
        "assumptions_hash": stored["assumptions_hash"],
        "cash": {k: (None if cash.get(k) is None else [_number(v) for v in cash[k]])
                 for k in ("equity_post_tax", "project_post_tax")},
        "rates": {k: _number(payload.get(k)) for k in ("cost_of_equity", "wacc_nominal")},
        "headlines": {h: _number(payload.get(h)) for _s, _r, h in SERIES.values()},
        "currency": str(payload.get("currency") or ""),
        "currency_year": payload.get("currency_year"),
        "basis_statement": str(payload.get("basis_statement") or ""),
        "price_basis": str(payload.get("price_basis") or ""),
    }


def npv(rate, cash):
    return math.fsum(c / (1.0 + rate) ** t for t, c in enumerate(cash))


def sign_changes(cash):
    signs = [c > 0 for c in cash if abs(c) > ZERO_CASH]
    return sum(1 for a, b in zip(signs, signs[1:]) if a != b)


def unique_irr(cash):
    """(root, None) or (None, reason): one sign change has one root in (-1, inf)."""
    changes = sign_changes(cash)
    if changes == 0:
        return None, "irr_no_sign_change"
    if changes > 1:
        return None, "irr_not_unique"
    lo, hi = BRACKET
    f_lo, f_hi = npv(lo, cash), npv(hi, cash)
    if f_lo == 0:
        return lo, None
    if f_hi == 0:
        return hi, None
    if (f_lo > 0) == (f_hi > 0):
        return None, "irr_not_bracketed"
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid, cash)
        if f_mid == 0 or hi - lo < 1e-15:
            break
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    # The float nearest the root: the smallest residual over the last bracket and a
    # few floats either side; among equal residuals, the shortest decimal form.
    candidates = {lo, hi, mid}
    for x in (lo, hi):
        for direction in (-math.inf, math.inf):
            y = x
            for _ in range(16):
                y = math.nextafter(y, direction)
                candidates.add(y)
    return min(sorted(candidates), key=lambda r: (abs(npv(r, cash)), len(repr(r)))), None


def calculate(raw, scope):
    if raw.get("version") != 1 or raw.get("profile") != PROFILE or raw["entity_id"] != scope["entity_id"]:
        raise ValueError("investment_source_identity")
    if "start" in scope:
        raise ValueError("unsupported_snapshot_scope")
    available, reasons = {}, {}
    for metric, (series, rate_key, headline_key) in SERIES.items():
        cash, headline = raw["cash"].get(series), raw["headlines"][headline_key]
        if not cash or not common.finite(cash):
            reasons[metric] = "cash_not_established"
            continue
        tolerance = REL_TOLERANCE * math.fsum(abs(c) for c in cash)
        if rate_key:
            rate = raw["rates"][rate_key]
            if rate is None or rate <= -1:
                reasons[metric] = "npv_rate_not_recorded"
                continue
            value = npv(rate, cash)
            # The headline is the engine's NPV at the rate it applied: a mismatch
            # means the recorded rate is not the one used.
            matches = headline is not None and abs(value - headline) <= max(tolerance, 1e-9)
        else:
            value, why = unique_irr(cash)
            if value is None:
                reasons[metric] = why
                continue
            matches = (headline is not None and abs(npv(value, cash)) <= max(tolerance, 1e-9)
                       and abs(value - headline) <= IRR_TOLERANCE)
        if headline is None:
            reasons[metric] = "headline_not_established"
        elif not matches:
            reasons[metric] = "headline_mismatch"
        else:
            available[metric] = value
    # The currency is a user string: only an ISO-style code becomes a unit.
    currency = raw["currency"] if re.fullmatch(r"[A-Z]{3}", raw["currency"]) else "report currency"
    definitions = {m: ({**d, "unit": currency} if m.endswith("_npv") else d) for m, d in METRICS.items()}
    return common.facts_and_slots(scope, definitions, available, reasons, len(raw["index"]))


def notes(raw, label):
    rates = raw["rates"]

    def pct(v):
        return "not recorded" if v is None else f"{v:.4%}"

    first = raw["index"][0] if raw["index"] else "the first year"
    # The report's own statement of its money basis (nominal cash is in each
    # year's money; costs may be real money of the base year).
    return [
        "Money basis, as the report states it: " + label(raw["basis_statement"] or "not stated") + ".",
        f"Equity at the cost of equity {pct(rates['cost_of_equity'])}; project at the WACC "
        f"{pct(rates['wacc_nominal'])}, as the report records them. End-of-year discounting; {first} "
        "(financial close) is undiscounted.",
        "LCOE is not checkable yet: its energy and value series are not retained in the report.",
    ]

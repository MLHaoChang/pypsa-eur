"""The headline figures at the top of Results, per project type (ui-modern 03
S1; owner decisions DASHBOARD-1, UI-MODERN-1).

One ordered list of cards for the open project. The frontend formats and
places them; it computes nothing. Each figure is read where the Results tabs
already read it, so the strip and the tab below never disagree:

  * system cost and new-build capital cost: `/results/cost_breakdown`'s payload;
  * unserved energy: `/results/lost_load`'s payload;
  * curtailed share, LCOE and peak line loading: the checked formulas in
    `harness.calculations` (`formula_id` names the formula).

A card without a figure says which `ResultState` it is in and why, in one
plain sentence; it never carries 0 for a figure the run did not produce.
`routers.results.get_headline_kpis` keeps the network lookup, the
`_dispatch_ready` gate and the `_state` reads, as the other results routes do.
"""
from __future__ import annotations

import math

from harness import calculations

MICROGRID_TEMPLATES = frozenset({"eh_microgrid", "eh_microgrid_staged"})

#: Project type -> card ids, in display order (at most five).
ORDER = {
    "energy_hub": ("lcoe", "system_cost", "new_build_capital", "curtailed_share", "unserved_energy"),
    "microgrid": ("unserved_energy", "system_cost", "new_build_capital", "curtailed_share"),
    "network": ("system_cost", "new_build_capital", "curtailed_share", "peak_line_loading"),
}

#: Card id -> (checked profile, metric).
CHECKED = {
    "curtailed_share": (calculations.curtailment.PROFILE, "curtailment_share"),
    "lcoe": (calculations.investment.LCOE_PROFILE, "lcoe_real"),
    "peak_line_loading": (calculations.branch.NETWORK_PROFILE, "max_branch_utilisation"),
}

#: A checked formula's refusal -> (ResultState, one plain sentence). No engine
#: identifier appears in a sentence (docs/agents/review-ux.md).
_UNAVAILABLE = ("unavailable", "This run cannot give this figure.")
REASONS = {
    "unverified_solve": ("stale", "The inputs changed since this solve."),
    "no_variable_generators": ("not_applicable", "The project has no generator with a varying availability."),
    "no_available_energy": ("not_applicable", "No varying generator had energy available in this run."),
    "investment_case_not_current": ("not_run", "The investment case has not run on this solve."),
    "unsupported_snapshot_scope": ("unavailable", "This figure is not available for a multi-period run."),
    "ac_pf_present": ("unavailable", "A load flow replaced the line flows of this solve."),
    "unknown_branch": ("not_applicable", "The project has no lines or transformers."),
}
NOT_MODELLED_LOST_LOAD = "The run had no value of lost load, so unserved energy was not measured."
NO_COST = "This solve has no cost breakdown."


def project_kind(template: dict | None) -> str:
    """The card set a project gets: its template's, or the plain network's."""
    if template is None:
        return "network"
    return "microgrid" if str(template.get("id") or "") in MICROGRID_TEMPLATES else "energy_hub"


def _card(card_id, value=None, unit=None, *, state="ready", reason=None, **extra):
    if value is not None and not math.isfinite(value):
        value, state, reason = None, *_UNAVAILABLE
    if state == "ready" and value == 0:
        state = "zero"
    return {"id": card_id, "value": value, "unit": unit, "state": state, "reason": reason,
            "formula_id": None, "detail": None, **extra}


def _refused(card_id, code):
    state, reason = REASONS.get(code, _UNAVAILABLE)
    return _card(card_id, state=state, reason=reason)


def _peak_branch(raw, scope, peak):
    """The name of the branch at the network's peak loading, for the card to say."""
    branch = calculations.branch
    a, b = branch._window(raw, scope)
    for item in raw["branches"].values():
        loading = branch._loading(item, raw["energy_weight"], a, b)
        if loading is not None and loading["peak_branch_utilisation"] == peak:
            return item["name"]
    return None


def _checked(ctx, card_id):
    """One checked fact, read and calculated as a checked answer reads it."""
    from harness.durable import network_guard
    from services.assurance_records import _scoped_read

    profile, metric = CHECKED[card_id]
    scope = {"project_id": ctx.project_uuid, "profile": profile, "entity_id": "network", "metrics": [metric]}
    try:
        raw = _scoped_read(ctx, scope, None)
        with network_guard():
            facts, slots = calculations.scoped_calculate(raw, scope)
    except ValueError as exc:
        return _refused(card_id, str(exc))
    fact = next((f for f in facts if f["metric"] == metric), None)
    if fact is None:
        return _refused(card_id, next((s.get("reason") for s in slots if s["metric"] == metric), None))
    detail = _peak_branch(raw, scope, fact["value"]) if card_id == "peak_line_loading" else None
    return _card(card_id, fact["value"], fact["unit"], formula_id=fact["formula_id"], detail=detail)


def _cost(card_id, cost, field, multi_period):
    if not cost or cost.get(field) is None:
        return _card(card_id, state="unavailable", reason=NO_COST)
    # A multi-period total spans the horizon; a single-period one is a year's.
    unit = "currency" if multi_period else "currency/yr"
    return _card(card_id, float(cost[field]), unit)


def _unserved(lost_load):
    if not lost_load or lost_load.get("total_mwh") is None:
        return _card("unserved_energy", state="not_modelled", reason=NOT_MODELLED_LOST_LOAD)
    return _card("unserved_energy", float(lost_load["total_mwh"]), "MWh")


def headline_kpis(ctx, *, template: dict | None, cost: dict | None, lost_load: dict | None) -> dict:
    """``{"kind", "cards"}`` for the solved open project.

    `cost` is `compute_cost_breakdown`'s payload and `lost_load` is
    `lost_load_payload`'s, either None when its route would answer 204.
    """
    kind = project_kind(template)
    multi = len(ctx.network.investment_periods) > 0
    build = {
        "system_cost": lambda: _cost("system_cost", cost, "total", multi),
        "new_build_capital": lambda: _cost("new_build_capital", cost, "capex_expansion", multi),
        "unserved_energy": lambda: _unserved(lost_load),
    }
    cards = [build[c]() if c in build else _checked(ctx, c) for c in ORDER[kind]]
    return {"kind": kind, "cards": cards}

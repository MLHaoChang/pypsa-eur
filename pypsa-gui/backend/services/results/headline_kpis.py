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

import logging
import math

from harness import calculations, time_to_power_calculations

logger = logging.getLogger("pypsa_gui.results")

MICROGRID_TEMPLATES = frozenset({"eh_microgrid", "eh_microgrid_staged"})

#: Project type -> card ids, in display order (at most five shown; an energy
#: hub's sixth, time to power, only appears with a data-centre load).
ORDER = {
    "energy_hub": (
        "lcoe",
        "system_cost",
        "new_build_capital",
        "curtailed_share",
        "unserved_energy",
        # A data-centre project's, last (ui-modern 05, DC27-TTP-1). Absent
        # from the strip, not a tile, where the project has no data-centre
        # load: the strip then keeps its five cards.
        "time_to_power",
    ),
    "microgrid": (
        "unserved_energy",
        "system_cost",
        "new_build_capital",
        "curtailed_share",
    ),
    "network": (
        "system_cost",
        "new_build_capital",
        "curtailed_share",
        "peak_line_loading",
    ),
}

#: Card id -> (checked profile, metric).
CHECKED = {
    "curtailed_share": (calculations.curtailment.PROFILE, "curtailment_share"),
    "lcoe": (calculations.investment.LCOE_PROFILE, "lcoe_real"),
    "peak_line_loading": (
        calculations.branch.NETWORK_PROFILE,
        "max_branch_utilisation",
    ),
    "time_to_power": (time_to_power_calculations.PROFILE, "time_to_power_months"),
}

#: A checked formula's refusal -> (ResultState, one plain sentence). No engine
#: identifier appears in a sentence (docs/agents/review-ux.md). The two refusals
#: with more than one cause are split in `_why`.
_UNAVAILABLE = ("unavailable", "This run cannot give this figure.")
SWITCHED_OFF = (
    "unavailable",
    "Checked figures are switched off for this installation.",
)
BUSY = ("unavailable", "The project is busy. Try again in a moment.")
INPUTS_CHANGED = ("stale", "The inputs changed since this solve.")
LOAD_FLOW_REPLACED = ("unavailable", "A load flow replaced the results of this solve.")
NOT_COMPARED = (
    "unavailable",
    "This solve did not record its inputs, so this figure is not checked.",
)
NOT_OPTIMAL = ("unavailable", "The solve did not finish with an optimal result.")
CASE_NOT_RUN = ("not_run", "The investment case has not run on this solve.")
# "stale" is kept for the solve itself: the strip marks every figure out of
# date on it. A case that ran but is out of date is "unavailable" with its own
# sentence (UX (17), #1359); the strip links any LCOE card without a figure
# to the Investment case tab.
CASE_RUNNING = ("running", "The investment case is still running.")
CASE_STALE = (
    "unavailable",
    "The investment case is out of date: run it again.",
)
CASE_NO_KEY = (
    "unavailable",
    "The investment case cannot be compared with its inputs.",
)
CASE_UNREAD = ("unavailable", "The investment case could not be read.")
REASONS = {
    "no_variable_generators": (
        "not_applicable",
        "The project has no generator with a varying availability.",
    ),
    "no_available_energy": (
        "not_applicable",
        "No varying generator had energy available in this run.",
    ),
    "unsupported_snapshot_scope": (
        "unavailable",
        "This figure is not available for a multi-period run.",
    ),
    "ac_pf_present": (
        "unavailable",
        "A load flow replaced the line flows of this solve.",
    ),
    "unknown_branch": ("not_applicable", "The project has no lines or transformers."),
}
NOT_MODELLED_LOST_LOAD = (
    "The run had no value of lost load, so unserved energy was not measured."
)
NO_COST = "This solve has no cost breakdown."
#: The breakdown exists but this figure failed to compute (ui-modern 06: the
#: expansion CAPEX compute raised). The Capacity expansion tab shows the same
#: sentence (`FIGURE_NOT_COMPUTED` in frontend pages/results/shared.tsx).
FIGURE_NOT_COMPUTED = "This figure could not be computed for this solve."


def project_kind(template: dict | None) -> str:
    """The card set a project gets: its template's, or the plain network's."""
    if template is None:
        return "network"
    return (
        "microgrid"
        if str(template.get("id") or "") in MICROGRID_TEMPLATES
        else "energy_hub"
    )


def _card(card_id, value=None, unit=None, *, state="ready", reason=None, **extra):
    if value is not None and not math.isfinite(value):
        value, state, reason = None, *_UNAVAILABLE
    if state == "ready" and value == 0:
        state = "zero"
    return {
        "id": card_id,
        "value": value,
        "unit": unit,
        "state": state,
        "reason": reason,
        "formula_id": None,
        "detail": None,
        # S3: `headline_delta.add_deltas` fills the delta; the trend is the
        # figure per investment period, where the run has one.
        "delta": None,
        "trend": None,
        **extra,
    }


SOLVE_FINGERPRINTS = (
    "assistant_solve_input_fingerprint",
    "assistant_solve_exact_input_fingerprint",
)


def _unverified(ctx):
    """Why `calculations.solve_matches_inputs` said no, in its own order."""
    state = ctx.solver_state
    if state.get("ac_pf_results") is not None or state.get("lopf_results") is not None:
        return LOAD_FLOW_REPLACED
    if state.get("status") != "completed" or state.get("condition") != "optimal":
        return NOT_OPTIMAL
    # Nothing recorded, nothing compared: not the same as a comparison that failed.
    if not all(ctx.network.meta.get(key) for key in SOLVE_FINGERPRINTS):
        return NOT_COMPARED
    return INPUTS_CHANGED


def _case_not_current(ctx):
    """Which of `investment.is_current`'s conditions failed."""
    from fastapi import HTTPException

    try:
        state = calculations.investment.current(ctx)
    except (ValueError, KeyError, TypeError, HTTPException):
        return CASE_UNREAD
    if not state or not state["present"]:
        return CASE_NOT_RUN
    if state["status"] == "running":
        return CASE_RUNNING
    # No key to compare against: the case's age is not known.
    if not state["assumptions_hash"] or not state["current_hash"]:
        return CASE_NO_KEY
    if state["stale"] or state["assumptions_hash"] != state["current_hash"]:
        return CASE_STALE
    # Current now: it changed during the read, or it holds no years.
    return CASE_UNREAD


_WHY = {
    "unverified_solve": _unverified,
    "investment_case_not_current": _case_not_current,
}


def _refused(ctx, card_id, code):
    state, reason = _WHY[code](ctx) if code in _WHY else REASONS.get(code, _UNAVAILABLE)
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


def _shown(profile):
    """Whether the strip shows this checked profile: exactly where a user can
    get the same checked answer in chat (owner decision STRIP-GATE-1). Chat's
    own two questions decide: `harness.assurance.admission` admits a checked
    answer (`PYPSA_GUI_ANSWER_ASSURANCE_MODE`), and
    `harness.rollout.profile_enabled` lists the profile
    (`PYPSA_GUI_ANSWER_ASSURANCE_PROFILES`). The one place for the gate."""
    from fastapi import HTTPException

    from harness import assurance
    from harness.rollout import profile_enabled

    try:
        # What a "Check answers" turn sends; refused where none can be given.
        assurance.admission({"version": 1, "mode": "strict"})
    except HTTPException:
        return False
    return profile_enabled(profile)


def _no_tile(card_id, reason=None):
    """Whether a card with this refusal is left off the strip: time to power
    where the project has no data-centre load (a card that cannot apply is
    absent, not a tile saying so)."""
    return card_id == "time_to_power" and reason == "no_dc_load_spec"


def _ttp_words(reason):
    """A not-available reason as the card's plain sentence."""
    from services import dc_time_to_power

    words = dc_time_to_power.REASONS.get(reason)
    if words is None:
        return _UNAVAILABLE
    return "unavailable", f"{words[0].upper()}{words[1:]}."


def _ttp_detail(raw):
    """The caveat under a figure that is not exact to its date."""
    out = time_to_power_calculations.result(raw)
    if out.get("status") == "available" and out["resolution_months"] > 0:
        return f"First power may be as early as {out['earliest_first_power']}."
    return None


def _checked(ctx, card_id):
    """One checked fact, read and calculated as a checked answer reads it. A
    failure costs this card only: the same set a checked answer catches
    (`harness.assurance`)."""
    from fastapi import HTTPException

    from harness.durable import network_guard
    from services.assurance_records import scoped_read

    profile, metric = CHECKED[card_id]
    if not _shown(profile):
        # Whether the project is a data-centre one is unknown while switched off.
        if card_id == "time_to_power":
            return None
        return _card(card_id, state=SWITCHED_OFF[0], reason=SWITCHED_OFF[1])
    scope = {
        "project_id": ctx.project_uuid,
        "profile": profile,
        "entity_id": "network",
        "metrics": [metric],
    }
    try:
        raw = scoped_read(ctx, scope, None)
        with network_guard():
            facts, slots = calculations.scoped_calculate(raw, scope)
        fact = next((f for f in facts if f["metric"] == metric), None)
        if fact is None:
            code = next((s.get("reason") for s in slots if s["metric"] == metric), None)
            if _no_tile(card_id, code):
                return None
            if card_id == "time_to_power":
                state, why = _ttp_words(code)
                return _card(card_id, state=state, reason=why)
            return _refused(
                ctx,
                card_id,
                next((s.get("reason") for s in slots if s["metric"] == metric), None),
            )
        detail = (
            _peak_branch(raw, scope, fact["value"])
            if card_id == "peak_line_loading"
            else _ttp_detail(raw)
            if card_id == "time_to_power"
            else None
        )
        return _card(
            card_id,
            fact["value"],
            fact["unit"],
            formula_id=fact["formula_id"],
            detail=detail,
        )
    except ValueError as exc:
        if _no_tile(card_id, str(exc)):
            return None
        return _refused(ctx, card_id, str(exc))
    except HTTPException:
        return _card(card_id, state=BUSY[0], reason=BUSY[1])
    except (KeyError, TypeError):
        logger.exception("headline card %s could not be read", card_id)
        return _card(card_id, state=_UNAVAILABLE[0], reason=_UNAVAILABLE[1])


def _cost(card_id, cost, field, per_year, trend=None):
    if not cost:
        return _card(card_id, state="unavailable", reason=NO_COST)
    if cost.get(field) is None:
        return _card(card_id, state="unavailable", reason=FIGURE_NOT_COMPUTED)
    return _card(
        card_id,
        float(cost[field]),
        "currency/yr" if per_year else "currency",
        trend=trend,
    )


#: What the trend's points are (`compute_cost_breakdown` `by_period`).
TREND_BASIS = (
    "Each investment period's annualised fixed cost and operating cost over "
    "its years, undiscounted, in the money of the input costs."
)


def _period_totals(cost, investment_periods):
    """The cost breakdown's total per investment period: the trend of a run
    with two or more periods, else None. A series that misses a period, or
    whose points do not add up to the card's total (a cost with no period),
    would read complete when it is not, so it is None too."""
    rows = (cost or {}).get("by_period") or []
    points = [{"period": p["period"], "value": float(p["total"])} for p in rows]
    if len(points) < 2 or not all(math.isfinite(p["value"]) for p in points):
        return None
    if [p["period"] for p in points] != list(investment_periods):
        return None
    if not math.isclose(
        sum(p["value"] for p in points),
        float(cost["total"]),
        rel_tol=1e-9,
        abs_tol=1e-6,
    ):
        return None
    return {"basis": TREND_BASIS, "points": points}


def _unserved(lost_load):
    if not lost_load or lost_load.get("total_mwh") is None:
        return _card(
            "unserved_energy", state="not_modelled", reason=NOT_MODELLED_LOST_LOAD
        )
    return _card("unserved_energy", float(lost_load["total_mwh"]), "MWh")


def headline_kpis(
    ctx, *, template: dict | None, cost: dict | None, lost_load: dict | None
) -> dict:
    """``{"kind", "cards"}`` for the solved open project.

    `cost` is `compute_cost_breakdown`'s payload and `lost_load` is
    `lost_load_payload`'s, either None when its route would answer 204.
    """
    kind = project_kind(template)
    multi = len(ctx.network.investment_periods) > 0
    build = {
        # Annualised fixed cost plus the opex of the modelled snapshots: a
        # year's cost only when they cover a year, so it carries no "/yr" (the
        # Capacity expansion tab's "Total system cost" has none either).
        "system_cost": lambda: _cost(
            "system_cost",
            cost,
            "total",
            False,
            _period_totals(cost, ctx.network.investment_periods),
        ),
        # Annualised: a year's in a single-period run, the horizon's otherwise.
        "new_build_capital": lambda: _cost(
            "new_build_capital", cost, "capex_expansion", not multi
        ),
        "unserved_energy": lambda: _unserved(lost_load),
    }
    cards = [build[c]() if c in build else _checked(ctx, c) for c in ORDER[kind]]
    cards = [c for c in cards if c is not None]
    return {"kind": kind, "cards": cards}

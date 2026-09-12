"""
A budget that spans studies — what makes a chain of them a campaign.

Every adequacy engine already caps itself: `MAX_FRONTIER_POINTS = 12`,
`MAX_CONTINGENCIES = 20`, `MAX_LOOP_SOLVES = 8`, `MAX_DRAWS = 2000`. Nothing
caps CHAINING them. An agent asked to "hit LOLE <= 3 h/yr at least cost" can
legitimately reach for a frontier, then a margin loop, then a coupling loop,
then a sweep — every call inside its own limit, roughly fifty full
capacity-expansion solves in total, unattended, on a shared solver.

This module is that missing accounting, and it is deliberately NOT prompt
discipline: a model cannot be asked to keep a running total of solves it has
spent across a conversation and be relied on to stop. The gate lives in the
dispatcher, where the only way past it is not to call the tool.

Two rules the numbers follow:

* **Charge the worst case, before the study starts.** Every cost here is
  knowable up front — a frontier is one solve per target, a loop is its
  `max_solves` — so the budget is a promise the campaign can keep rather than
  a total discovered after the fact.
* **Monte Carlo is charged ZERO, because it solves nothing.** The engine
  samples a snapshot; its cost is arithmetic, not LP. Charging it would price
  the one study that can be run freely as if it were the most expensive.

Check-then-record, never charge-then-refund: a study that fails to start
(409 while the mesh is busy, 422 for a missing VOLL) must not burn budget, and
a refund path is a second place for the total to go wrong. The study mesh
already allows at most one study in flight, so nothing can slip between the
check and the record.

Scope: this gates the AGENT. The panels are a human clicking one button at a
time and are untouched — a budget there would be a behaviour change nobody
asked for.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# The user-facing default, and the ceiling on an explicit override. 30 solves
# is a frontier plus a reliability loop, or a full class-B sweep — a real
# campaign in one sitting. Past ~4x that it is not a campaign but an overnight
# job, which is a thing a person should start deliberately and watch.
DEFAULT_BUDGET_SOLVES = 30
MAX_BUDGET_SOLVES = 120

MAX_OBJECTIVE_LEN = 500
MAX_ENTRIES = 200

# Studies that can be charged, and how their cost is read off their arguments.
# `mc` is present with a zero cost rather than absent: a missing key would be
# an unknown study and refused, and the point is that MC is known and free.
CHARGEABLE = ("frontier", "fmea_sweep", "coupling_loop", "margin_loop", "mc")


class CampaignError(RuntimeError):
    """A campaign-lifecycle refusal (already running, none running)."""


class CampaignBudgetError(RuntimeError):
    """
    The requested study would exceed the campaign's remaining budget.

    Carries a `detail` dict so the chat dispatcher's typed-error path picks it
    up: `chat_service._dispatch_tool_use` reads `exc.detail["error_kind"]`,
    and without it a budget refusal — which the agent must handle by REPORTING
    and asking, not retrying — arrives as an indistinguishable `tool_error`.
    """

    def __init__(self, message: str, error_kind: str = "campaign_budget_exhausted"):
        super().__init__(message)
        self.detail = {"error_kind": error_kind, "message": message}


@dataclass
class _Campaign:
    objective: str
    budget_solves: int
    started_at: float
    spent_solves: int = 0
    entries: list[dict] = field(default_factory=list)


# Review finding 5: this was a module global while every study record it gates
# lives in the per-project context (`STUDY_KEYS` sits beside `RESULT_STATE_KEYS`
# in services/project_context.py for exactly this reason). On a multi-user
# server that meant two tenants sharing one budget — and a refusal that quoted
# the OTHER tenant's objective text back, free text one user typed surfacing to
# another, the shape of
# docs/superpowers/findings/2026-08-27-lock-holder-email-reaches-the-model.md.
#
# The campaign now lives in the active context's `solver_state`, under its own
# lock, beside the study records. `reset_network` gives each test and each
# fresh project a clean one by construction.
_CAMPAIGN_KEY = "adequacy_campaign"


def _context():
    from services.pypsa_service import PyPSAService
    return PyPSAService.get_active_context()


def _get(ctx) -> _Campaign | None:
    return ctx.solver_state.get(_CAMPAIGN_KEY)


def _put(ctx, campaign: _Campaign | None) -> None:
    if campaign is None:
        ctx.solver_state.pop(_CAMPAIGN_KEY, None)
    else:
        ctx.solver_state[_CAMPAIGN_KEY] = campaign


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _snapshot(campaign: _Campaign, *, active: bool = True) -> dict:
    return {
        "active": active,
        "objective": campaign.objective,
        "budget_solves": campaign.budget_solves,
        "spent_solves": campaign.spent_solves,
        "remaining_solves": campaign.budget_solves - campaign.spent_solves,
        "started_at": _iso(campaign.started_at),
        "entries": list(campaign.entries),
    }


def reset() -> None:
    """Drop the active context's campaign. Test isolation and hard resets."""
    ctx = _context()
    with ctx.solver_state_lock:
        _put(ctx, None)


def start(objective: str, budget_solves: int | None = None) -> dict:
    """Open a campaign. Refuses while one is already open."""
    objective = (objective or "").strip()
    if not objective:
        raise CampaignError(
            "a campaign needs a stated objective — it is what the budget is "
            "being spent ON, and what the closing report answers")
    if len(objective) > MAX_OBJECTIVE_LEN:
        raise CampaignError(
            f"objective exceeds {MAX_OBJECTIVE_LEN} characters")

    budget = DEFAULT_BUDGET_SOLVES if budget_solves is None else int(budget_solves)
    if not (1 <= budget <= MAX_BUDGET_SOLVES):
        raise CampaignError(
            f"budget_solves must be between 1 and {MAX_BUDGET_SOLVES} (got "
            f"{budget}). Past that a run stops being a campaign and becomes an "
            f"overnight job, which a person should start deliberately")

    ctx = _context()
    with ctx.solver_state_lock:
        active = _get(ctx)
        if active is not None:
            # The objective is NOT quoted back. It is free text someone typed,
            # and on a shared context the someone need not be the caller —
            # campaign_status is the authorised way to read it.
            raise CampaignError(
                f"a campaign is already running "
                f"({active.spent_solves}/{active.budget_solves} solves "
                f"spent). End it first — starting a second would silently "
                f"discard the first one's log; read campaign_status for what "
                f"it is working on")
        campaign = _Campaign(objective=objective, budget_solves=budget,
                             started_at=time.time())
        _put(ctx, campaign)
        return _snapshot(campaign)


def status() -> dict:
    """The active campaign, or `{"active": False}`."""
    ctx = _context()
    with ctx.solver_state_lock:
        active = _get(ctx)
        if active is None:
            return {"active": False, "entries": []}
        return _snapshot(active)


def end(note: str | None = None) -> dict:
    """Close the campaign and return its final record."""
    ctx = _context()
    with ctx.solver_state_lock:
        active = _get(ctx)
        if active is None:
            raise CampaignError("no campaign is running")
        final = _snapshot(active, active=False)
        _put(ctx, None)
    if note:
        final["note"] = str(note)[:MAX_OBJECTIVE_LEN]
    return final


def check(study: str, solves: int) -> None:
    """
    Refuse a study that would exceed the remaining budget. No mutation — see
    the module docstring on check-then-record.

    A no-op when no campaign is running: a single study asked for directly is
    not a campaign, and gating it would change behaviour nobody asked to
    change.
    """
    if study not in CHARGEABLE:
        raise CampaignBudgetError(f"unknown study '{study}'",
                                  error_kind="unknown_study")
    ctx = _context()
    with ctx.solver_state_lock:
        active = _get(ctx)
        if active is None:
            return
        remaining = active.budget_solves - active.spent_solves
        if solves > remaining:
            raise CampaignBudgetError(
                f"'{study}' needs up to {solves} solve(s) and the campaign has "
                f"{remaining} of {active.budget_solves} left. Report what the "
                f"campaign has established so far and ask before spending "
                f"more — or narrow the study (fewer frontier targets, a "
                f"smaller max_solves) to fit")


def record(study: str, solves: int) -> dict | None:
    """Charge a STARTED study. Returns the new status, or None if idle."""
    ctx = _context()
    with ctx.solver_state_lock:
        active = _get(ctx)
        if active is None:
            return None
        if len(active.entries) >= MAX_ENTRIES:
            raise CampaignBudgetError(
                f"campaign log is full ({MAX_ENTRIES} entries)")
        active.spent_solves += int(solves)
        active.entries.append({
            "study": study,
            "solves_charged": int(solves),
            "at": _iso(time.time()),
        })
        return _snapshot(active)


def estimate_solves(n, study: str, **kwargs) -> int:
    """
    The worst-case LP-solve cost of one study, from its arguments.

    Every figure is the engine's own promise, read from the engine rather
    than restated here — a second copy of `MAX_LOOP_SOLVES` in this file is
    how the budget starts lying.
    """
    if study == "mc":
        # Spec §4: this engine SOLVES NOTHING. Its metrics are hours and MWh
        # sampled off one snapshot.
        return 0

    # Review finding 4: every one of these ends with a full re-solve that the
    # route's own budget explicitly EXCLUDES — the frontier's `_restore_base`,
    # both loops' `_restore_closing` — and `coupling.py` says so in as many
    # words: "the wall-time budget the route promises is `max_solves + 1` (the
    # closing restore is outside it)". The sweep's `+ 1` was always here; the
    # others were undercharging by exactly that restore, so a campaign could
    # overrun its budget by one solve per study.
    if study == "frontier":
        from services.adequacy.frontier import DEFAULT_TARGETS_PERMYRIAD
        targets = kwargs.get("targets_permyriad") or DEFAULT_TARGETS_PERMYRIAD
        return len(list(targets)) + 1

    if study == "fmea_sweep":
        from services.adequacy.sweep import class_b_contingencies
        try:
            class_b = len(class_b_contingencies(n))
        except Exception:  # noqa: BLE001 — an unreadable frame is not a budget
            class_b = 0
        # +1 for the closing base re-solve, which the sweep always runs.
        return class_b + len(kwargs.get("scenarios") or []) + 1

    if study in ("coupling_loop", "margin_loop"):
        from services.adequacy.coupling import MAX_LOOP_SOLVES
        max_solves = kwargs.get("max_solves")
        budget = MAX_LOOP_SOLVES if max_solves is None else int(max_solves)
        # +1 for the closing restore, outside the route's budget (above), and
        # the margin loop pays ANOTHER for the probing solve that measures its
        # starting margin before the budget opens (spec §2.3). So the cap loop
        # costs budget + 1 and the margin loop budget + 2.
        return budget + (2 if study == "margin_loop" else 1)

    raise CampaignBudgetError(f"unknown study '{study}'",
                              error_kind="unknown_study")

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

import threading
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


_LOCK = threading.Lock()
_ACTIVE: _Campaign | None = None


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
    """Drop any active campaign. For test isolation and hard resets only."""
    global _ACTIVE
    with _LOCK:
        _ACTIVE = None


def start(objective: str, budget_solves: int | None = None) -> dict:
    """Open a campaign. Refuses while one is already open."""
    global _ACTIVE
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

    with _LOCK:
        if _ACTIVE is not None:
            raise CampaignError(
                f"a campaign is already running ('{_ACTIVE.objective}', "
                f"{_ACTIVE.spent_solves}/{_ACTIVE.budget_solves} solves "
                f"spent). End it first — starting a second would silently "
                f"discard the first one's log")
        _ACTIVE = _Campaign(objective=objective, budget_solves=budget,
                            started_at=time.time())
        return _snapshot(_ACTIVE)


def status() -> dict:
    """The active campaign, or `{"active": False}`."""
    with _LOCK:
        if _ACTIVE is None:
            return {"active": False, "entries": []}
        return _snapshot(_ACTIVE)


def end(note: str | None = None) -> dict:
    """Close the campaign and return its final record."""
    global _ACTIVE
    with _LOCK:
        if _ACTIVE is None:
            raise CampaignError("no campaign is running")
        final = _snapshot(_ACTIVE, active=False)
        _ACTIVE = None
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
    with _LOCK:
        if _ACTIVE is None:
            return
        remaining = _ACTIVE.budget_solves - _ACTIVE.spent_solves
        if solves > remaining:
            raise CampaignBudgetError(
                f"'{study}' needs up to {solves} solve(s) and the campaign has "
                f"{remaining} of {_ACTIVE.budget_solves} left "
                f"(objective: {_ACTIVE.objective}). Report what the campaign "
                f"has established so far and ask before spending more — or "
                f"narrow the study (fewer frontier targets, a smaller "
                f"max_solves) to fit")


def record(study: str, solves: int) -> dict | None:
    """Charge a STARTED study. Returns the new status, or None if idle."""
    with _LOCK:
        if _ACTIVE is None:
            return None
        if len(_ACTIVE.entries) >= MAX_ENTRIES:
            raise CampaignBudgetError(
                f"campaign log is full ({MAX_ENTRIES} entries)")
        _ACTIVE.spent_solves += int(solves)
        _ACTIVE.entries.append({
            "study": study,
            "solves_charged": int(solves),
            "at": _iso(time.time()),
        })
        return _snapshot(_ACTIVE)


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

    if study == "frontier":
        from services.adequacy.frontier import DEFAULT_TARGETS_PERMYRIAD
        targets = kwargs.get("targets_permyriad") or DEFAULT_TARGETS_PERMYRIAD
        return len(list(targets))

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
        # The margin loop measures its starting point with a probing solve
        # BEFORE the budget (spec §2.3), so it costs one more than it says.
        return budget + (1 if study == "margin_loop" else 0)

    raise CampaignBudgetError(f"unknown study '{study}'",
                              error_kind="unknown_study")

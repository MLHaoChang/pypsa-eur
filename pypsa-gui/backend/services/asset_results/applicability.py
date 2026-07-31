"""
Resolve a metric or a category to one of three states for a given asset.

  ok      — computable right now
  blocked — applies to this class, but a precondition is unmet; carries a
            reason and an actionable remedy
  na      — cannot ever apply to this class; carries a reason, never a remedy

The distinction is the point. "Gas 1 has no load flow" and "you have not run
AC power flow yet" are both greyed-out, but only one of them is worth acting
on, and conflating them makes the tab useless for diagnosing a model.
"""
from __future__ import annotations

from dataclasses import dataclass

from .registry import CATEGORY_LABELS, Metric, metrics_for

VALID_ACTIONS = frozenset({"run_simulation", "run_ac_pf", "open_properties"})


@dataclass(frozen=True)
class Remedy:
    action: str
    label: str

    def __post_init__(self) -> None:
        if self.action not in VALID_ACTIONS:
            raise ValueError(f"unknown remedy action: {self.action}")


@dataclass(frozen=True)
class Status:
    status: str  # "ok" | "blocked" | "na"
    reason: str = ""
    remedy: Remedy | None = None


OK = Status("ok")

_BRANCH_OR_BUS = {"Line", "Transformer", "Link", "Bus"}
_STORAGE = {"StorageUnit", "Store"}
_DISPATCHING = {"Generator", "Load", "Link", "StorageUnit", "Store"}
_SIZEABLE = {"Generator", "Line", "Transformer", "Link", "StorageUnit", "Store"}


def category_na_reason(category: str, component_class: str) -> str:
    """Why this category can never apply to this class. Specific beats generic."""
    if category == "loadflow" and component_class not in _BRANCH_OR_BUS:
        return f"{component_class} is not a branch or bus component"
    if category == "storage" and component_class not in _STORAGE:
        return f"{component_class} does not store energy"
    if category == "dispatch" and component_class not in _DISPATCHING:
        return f"{component_class} does not dispatch power"
    if category == "capacity" and component_class not in _SIZEABLE:
        return f"{component_class} has no optimisable capacity"
    if category == "emissions":
        return f"{component_class} does not emit CO₂"
    return f"{CATEGORY_LABELS.get(category, category)} does not apply to {component_class}"


def _na(reason: str) -> Status:
    return Status("na", reason)


def resolve_metric(
    metric: Metric, component_class: str, precond: dict[str, Status]
) -> Status:
    """First unmet precondition wins; unlisted preconditions are treated as ok."""
    if component_class not in metric.classes:
        return _na(f"{metric.label} is not defined for {component_class}")
    for req in metric.requires:
        st = precond.get(req, OK)
        if st.status != "ok":
            return st
    return OK


def resolve_category(
    category: str, component_class: str, precond: dict[str, Status]
) -> Status:
    """
    ok      — at least one member metric resolves ok
    blocked — members exist, none is ok, at least one is blocked
    na      — no members, or every member is na; prefer a reason every member
             shares over the generic category_na_reason (e.g., "not yet available"
             beats "Dispatch does not apply to Load")
    """
    members = metrics_for(component_class, category)
    if not members:
        return _na(category_na_reason(category, component_class))
    resolved = [resolve_metric(m, component_class, precond) for m in members]
    if any(r.status == "ok" for r in resolved):
        return OK
    for r in resolved:
        if r.status == "blocked":
            return r
    reasons = {r.reason for r in resolved if r.reason}
    if len(reasons) == 1:
        return _na(reasons.pop())
    return _na(category_na_reason(category, component_class))

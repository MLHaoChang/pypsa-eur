"""The headline strip's delta against the base case (ui-modern 03 S3).

The base is `harness.sensitivity_comparison`'s: the open project is a case
when it holds `sensitivity_case.json` naming its parent, and a base when a
child it can open holds one. Whether the two compare is that module's answer
too (`_read`, `_reasons`), in its words.

The figures are not its metrics, which sit on another basis than the cards
(pro-rated CAPEX + OPEX, all annuitised CAPEX, curtailed GWh, no LCOE). A
card's delta is the change in that card's own figure: the route builds the
base's cards with `headline_kpis` on the base's saved context, and
`add_deltas` subtracts where both cards carry a value in the same unit.
"""

from __future__ import annotations

import logging
import math

from fastapi import HTTPException

logger = logging.getLogger("pypsa_gui.results")

BASE_UNREADABLE = (
    "The base case could not be read, so this case is not compared with it."
)
#: What a change is a change of (the chat comparison reports other metrics).
BASIS = (
    "Each change is this case's figure minus the base case's, in the card's own unit."
)
NO_BASE_FIGURE = "The base case has no figure here to compare with."
OTHER_UNIT = "The base case reports this figure in another unit."
#: The card states that carry a figure; no other state is ever subtracted.
VALUE_STATES = ("ready", "zero")


def _block(role, base, comparable, reason):
    return {
        "role": role,
        "base_name": base.name,
        "base_project_id": str(base.id),
        "comparable": comparable,
        "reason": reason,
        "basis": BASIS if role == "case" and comparable else None,
    }


def _spec(project):
    from services import project_registry
    from services import workflow_tools as wt

    return wt._read_json(
        project_registry.project_dir(project) / wt.SENSITIVITY_CASE_FILE
    )


def _has_case(db, user, project) -> bool:
    """A sensitivity case of `project` the user can open, as
    `sensitivity_comparison._projects` lists them."""
    from harness.experiment_execution import is_case_project_name
    from services import project_registry

    for child in project_registry.direct_children(db, project):
        if is_case_project_name(child.name):
            continue
        try:
            child = project_registry.resolve_project(db, user, str(child.id))
        except HTTPException:
            continue
        if _spec(child):
            return True
    return False


def _relation(ctx):
    """(role, project, spec, base): role None when the open project is neither
    a case nor a base for this user. A base the user cannot open, one that is
    gone and one that never was all read as no base at all."""
    from harness import sensitivity_comparison as sc
    from services import chat_tools

    nothing = (None, None, None, None)
    if not getattr(ctx, "project_uuid", None):
        return nothing
    with chat_tools._acting() as (db, user):
        project = sc._resolve(db, user, str(ctx.project_uuid))
        spec = _spec(project)
        parent = project.parent_project_id
        if spec and parent is not None and spec.get("baseline_id") == str(parent):
            try:
                return "case", project, spec, sc._resolve(db, user, str(parent))
            except HTTPException:
                return nothing
        if _has_case(db, user, project):
            return "base", project, None, project
    return nothing


def base_case(ctx):
    """``(comparison, base_read)`` for the open project.

    `comparison` is None for a project that is neither a case nor a base, and
    for a case whose base this user cannot open (no name, no id, no hint).
    `base_read` is `sensitivity_comparison._read`'s for the base, and only
    there when the case compares with it.
    """
    from harness import sensitivity_comparison as sc

    try:
        role, project, spec, base = _relation(ctx)
    except HTTPException:  # no signed-in user, or the project has no row
        return None, None
    if role is None:
        return None, None
    if role == "base":
        return _block("base", base, True, None), None
    try:
        base_read, read = sc._read(base), sc._read(project)
        reasons = sc._reasons(base_read, read, spec, base_read["fingerprint"])
        if not (base_read["summary"].get("has_solve") and base_read["fresh"]):
            reasons.insert(0, "baseline_unavailable")
    except (HTTPException, OSError, KeyError, TypeError, ValueError):
        logger.exception("headline strip: the base case could not be read")
        return _block("case", base, False, BASE_UNREADABLE), None
    if reasons:
        why = sc._not_comparable("", reasons).split(": ", 1)[1]
        return _block(
            "case", base, False, f"Not compared with the base case: {why}."
        ), None
    return _block("case", base, True, None), base_read


def _figure(card) -> float | None:
    value = card.get("value") if card else None
    if card is None or card.get("state") not in VALUE_STATES or value is None:
        return None
    return value if math.isfinite(value) else None


def add_deltas(cards: list[dict], base_cards: list[dict]) -> None:
    """Set `delta` (case − base, unrounded) on each card that carries a figure.

    A figure is subtracted only when the base's card carries one too, in the
    same unit; otherwise the delta holds no number and `note` says why. The
    arithmetic is the sensitivity comparison's (`_cell`): the percentage is
    over the base's size, and a zero base has none.
    """
    from harness import sensitivity_comparison as sc

    base = {card["id"]: card for card in base_cards}
    for card in cards:
        value = _figure(card)
        if value is None:
            continue  # the card says itself why it has no figure
        other = base.get(card["id"])
        base_value = _figure(other)
        if base_value is None:
            card["delta"] = {"value": None, "pct": None, "note": NO_BASE_FIGURE}
        elif card["unit"] != other["unit"]:
            card["delta"] = {"value": None, "pct": None, "note": OTHER_UNIT}
        else:
            cell = sc._cell(("", 1.0), base_value, value)
            card["delta"] = {
                "value": cell["delta"],
                "pct": cell["delta_pct"],
                "note": cell.get("delta_pct_note"),
            }

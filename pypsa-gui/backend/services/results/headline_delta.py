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

from fastapi import HTTPException

logger = logging.getLogger("pypsa_gui.results")

NO_BASE_ACCESS = (
    "The base case is not open to you, so this case is not compared with it."
)
BASE_UNREADABLE = (
    "The base case could not be read, so this case is not compared with it."
)


def _block(role, base, comparable, reason):
    return {
        "role": role,
        "base_name": None if base is None else base.name,
        "base_project_id": None if base is None else str(base.id),
        "comparable": comparable,
        "reason": reason,
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
    a case nor a base; base None for a case whose base the user cannot open."""
    from harness import sensitivity_comparison as sc
    from services import chat_tools

    if not getattr(ctx, "project_uuid", None):
        return None, None, None, None
    with chat_tools._acting() as (db, user):
        project = sc._resolve(db, user, str(ctx.project_uuid))
        spec = _spec(project)
        parent = project.parent_project_id
        if spec and parent is not None and spec.get("baseline_id") == str(parent):
            try:
                base = sc._resolve(db, user, str(parent))
            except HTTPException:
                base = None
            return "case", project, spec, base
        if _has_case(db, user, project):
            return "base", project, None, project
    return None, None, None, None


def base_case(ctx):
    """``(comparison, base_read)`` for the open project.

    `comparison` is None for a project that is neither a case nor a base.
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
    if base is None:
        return _block("case", None, False, NO_BASE_ACCESS), None
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


def add_deltas(cards: list[dict], base_cards: list[dict]) -> None:
    """Set `delta` (case − base) on each card whose base card carries a value
    in the same unit; the arithmetic is the sensitivity comparison's."""
    from harness import sensitivity_comparison as sc

    base = {card["id"]: card for card in base_cards}
    for card in cards:
        other = base.get(card["id"])
        if other is None or card["value"] is None or other["value"] is None:
            continue
        if card["unit"] != other["unit"]:
            continue
        cell = sc._cell(("", 1.0), other["value"], card["value"])
        card["delta"] = {
            "value": cell["delta"],
            "pct": cell["delta_pct"],
            "note": cell.get("delta_pct_note"),
        }

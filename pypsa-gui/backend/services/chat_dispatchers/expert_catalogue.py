"""
Expert catalogue (expert-catalogue issue 05): the dispatchers for find_parameter
and open_parameter.

Schemas and routes: `harness/catalogue_tools/expert_catalogue.py`.

Both read only. `find_parameter` reads the catalogue (`services/asset_schema`)
and, under the active project's ACL (`_authorized_project`, which a viewer
passes, VIEWER-ROLE-1), that project's network for the assets carrying each
entry. With no project open it answers the entries with no assets. It never
reads another project. `open_parameter` returns the `parameter` ui_event
`uiNavigate.ts` handles; a former id resolves to the current one.

The chat_tools helpers are imported inside the functions: chat_tools imports
this module at its end, so a top-level import would be circular.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from services import parameter_assets as PA
from services.asset_schema import lookup
from services.asset_schema.catalogue import FUNCTION_IDS, guide_body
from services.asset_schema.schema import CLASS_ATTR

#: Most entries one answer lists, and most assets per entry (spec §7.1).
MAX_ENTRIES = 8
MAX_ASSETS = 10
#: The guide text an entry carries, enough to quote; the full guide opens in the app.
GUIDE_EXCERPT = 600


def find_parameter(
    query: str, asset_type: str | None = None, function: str | None = None
) -> dict:
    """Up to 8 catalogue entries for `query`, each with the open project's assets (10 at most)."""
    if asset_type is not None and lookup.asset_type(asset_type) is None:
        raise HTTPException(
            422,
            detail={
                "error_kind": "invalid_parameter_filter",
                "field": "asset_type",
                "message": f"There is no asset type '{asset_type}'.",
            },
        )
    if function is not None and function not in FUNCTION_IDS:
        raise HTTPException(
            422,
            detail={
                "error_kind": "invalid_parameter_filter",
                "field": "function",
                "message": f"There is no function '{function}'. Use one of: {', '.join(FUNCTION_IDS)}.",
            },
        )
    n = PA.active_network()
    results = []
    for e in lookup.search(
        query, asset_type_id=asset_type, function=function, limit=MAX_ENTRIES
    ):
        assets = PA.assets(n, e, asset_type) if n is not None else []
        guide = guide_body(e.id) or ""
        results.append(
            {
                "entry_id": e.id,
                "plain_name": e.plain_name,
                "model_name": e.model_name,
                "component_class": e.scope,
                "asset_types": lookup.asset_types_of(e.scope),
                "group": lookup.group_name(e.group),
                "unit": e.spec.unit_display or None,
                "short_help": e.short_help,
                "guide": guide[:GUIDE_EXCERPT],
                "guide_truncated": len(guide) > GUIDE_EXCERPT,
                "assets": assets[:MAX_ASSETS],
                "assets_total": len(assets),
                "assets_truncated": len(assets) > MAX_ASSETS,
            }
        )
    return {"query": query, "project_open": n is not None, "entries": results}


def open_parameter(entry_id: str, asset: str | None = None) -> dict:
    """The `parameter` ui_event for one entry (its current id), optionally on one asset."""
    entry, renamed = lookup.resolve(entry_id)
    if entry is None:
        raise HTTPException(
            422,
            detail={
                "error_kind": "unknown_parameter",
                "field": "entry_id",
                "message": f"No parameter has the id '{entry_id}'. Call find_parameter for its id.",
            },
        )
    if asset is not None:
        from services.chat_tools import _require_active_project

        _require_active_project()
        n = PA.active_network()
        attr = CLASS_ATTR.get(entry.scope)
        df = getattr(n, attr, None) if attr else None
        if df is None or asset not in df.index:
            other = next(
                (
                    cls
                    for cls, a in CLASS_ATTR.items()
                    if cls != entry.scope
                    and asset in getattr(getattr(n, a, None), "index", ())
                ),
                None,
            )
            why = (
                f"'{asset}' is a {other}, and {entry.plain_name} is a {entry.scope} parameter."
                if other
                else f"no {entry.scope} '{asset}' in the open project."
            )
            raise HTTPException(
                422,
                detail={
                    "error_kind": "component_not_found",
                    "message": f"{entry.plain_name}: {why}",
                },
            )
    event: dict[str, Any] = {
        "_ui_event": True,
        "kind": "parameter",
        "entry_id": entry.id,
        "current_id": entry.id,
        "component_class": entry.scope,
        "plain_name": entry.plain_name,
    }
    if renamed:
        event["former_id"] = entry_id
    if asset is not None:
        event["name"] = asset
    return event


DISPATCHERS = {
    "find_parameter": find_parameter,
    "open_parameter": open_parameter,
}

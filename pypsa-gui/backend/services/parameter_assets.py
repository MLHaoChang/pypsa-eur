"""
The open project's assets that carry one catalogue entry (expert-catalogue 05,
`find_parameter`): each with its value in display units and whether it was
left at its default (`assumed`). Read only, under the project ACL.
"""

from __future__ import annotations

import math
from typing import Any

from services.asset_schema import lookup, ranges
from services.asset_schema.schema import CLASS_ATTR


def active_network():
    """The open project's network after its ACL check, else None (no project open)."""
    from services.chat_tools import PyPSAService, _authorized_project

    name = PyPSAService.get_loaded_project()
    if not name:
        return None
    _authorized_project(name)
    return PyPSAService.get_network()


def _scalar(v: Any) -> Any:
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float):
        if math.isnan(v):
            return None
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
    return v


def _is_assumed(stored: Any, default: Any) -> bool:
    """Left at its default: blank (None, "", or a NaN, the stored "not set"), or the
    catalogue default after write normalisation (`ranges.same_value`: a number sent
    as a string, float noise)."""
    if stored is None or stored == "":
        return True
    if default is None:
        return False
    if isinstance(stored, str) and isinstance(default, str):
        return stored == default
    if isinstance(stored, bool) or isinstance(default, bool):
        return stored == default
    return ranges.same_value(stored, default)


def _bus_carriers(n) -> dict[str, str]:
    buses = getattr(n, "buses", None)
    if buses is None or "carrier" not in buses:
        return {}
    return {str(k): str(v) for k, v in buses["carrier"].items()}


def assets(n, entry, type_id: str | None) -> list[dict[str, Any]]:
    """Every asset of the entry's class (of `type_id` when given), with its value in display units."""
    attr = CLASS_ATTR.get(entry.scope)
    df = getattr(n, attr, None) if attr else None
    if df is None or len(df.index) == 0:
        return []
    carriers = _bus_carriers(n)
    spec = entry.spec
    default = entry.default.get("value")
    out = []
    for name, row in df.iterrows():
        rowd = {"name": str(name), **{str(k): row[k] for k in row.index}}
        t = lookup.asset_type_of(entry.scope, rowd, carriers.get)
        if type_id is not None and (t is None or t.id != type_id):
            continue
        stored = _scalar(row[spec.key]) if spec.key in row.index else None
        assumed = _is_assumed(stored, default)
        value = stored
        if (
            isinstance(stored, (int, float))
            and not isinstance(stored, bool)
            and spec.scale != 1
        ):
            value = stored * spec.scale
        out.append(
            {
                "name": str(name),
                "asset_type": t.id if t is not None else None,
                "value": value,
                "assumed": bool(assumed),
            }
        )
    return out

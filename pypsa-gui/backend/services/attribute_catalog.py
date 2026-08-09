"""
PyPSA attribute catalog — the one reader of `n.components.<attr>.defaults` for
the asset-editing feature (spec D3).

Deliberately NOT consolidated with the four pre-existing
`status.str.startswith("Input")` call sites (`routers/network.py:189, 2459`,
`services/vintage_service.py:235`): `routers/network.py` is a declared hotspot,
each of those has its own fall-back-to-all-columns behaviour, and none is
covered by a test. Recorded in the spec's Out of scope, not an oversight.

Measured against PyPSA 1.1.2 — `defaults` has exactly these nine columns:
    type, unit, default, description, status, static, varying, typ, dtype
There is NO `default_text` column; it is derived here (see `_default_text`).
"""
from __future__ import annotations

from typing import Any

from services.serialization import clean_scalar

# Class name → the pypsa.Network attribute holding that component's frame.
# Mirrors routers/network.py's _COMPONENT_ATTRS. Duplicated on purpose: the
# router imports this service, so the service must not import the router.
_CATALOG_ATTRS: dict[str, str] = {
    "Bus": "buses",
    "Carrier": "carriers",
    "Line": "lines",
    "Link": "links",
    "Transformer": "transformers",
    "Generator": "generators",
    "StorageUnit": "storage_units",
    "Store": "stores",
    "Load": "loads",
    "ShuntImpedance": "shunt_impedances",
}

# The two native columns deliberately not served (D24): `static` and `typ`.
# Nothing in the frontend reads them and `dtype` already carries the type in a
# JSON-safe form.
_SERVED = ("status", "varying", "dtype", "unit", "description", "type", "default")


def known_components() -> list[str]:
    """Every component class the catalog can describe, sorted."""
    return sorted(_CATALOG_ATTRS)


def _py(v: Any) -> Any:
    """
    Convert a numpy scalar to a Python one. Leaves str/None/native types alone.

    Without this a numpy.float64 or numpy.bool_ reaches json.dumps, which
    raises `Object of type float64 is not JSON serializable` — the same class
    of failure CLAUDE.md records for Pydantic models in SSE frames.
    """
    if isinstance(v, str) or v is None:
        return v
    item = getattr(v, "item", None)
    return item() if callable(item) else v


def _default_text(raw: Any) -> str:
    """
    The text PyPSA's default reads as.

    `clean_scalar` maps every non-finite float to None, so `p_nom_max`'s inf
    default would otherwise reach the UI as a blank — which reads as "no
    default" rather than "unbounded". str() of the raw value keeps `inf`
    legible (D24, success criterion 30).
    """
    v = _py(raw)
    return "" if v is None else str(v)


def catalog_for(n: Any, component_class: str) -> list[dict[str, Any]]:
    """
    Every attribute PyPSA defines for `component_class`, as the nine-field
    payload D24 fixes.

    Raises KeyError when the class is unknown; the route turns that into a 400
    naming the valid set.
    """
    attr = _CATALOG_ATTRS[component_class]          # KeyError → 400 at the route
    defaults = getattr(n.components, attr).defaults

    out: list[dict[str, Any]] = []
    for name, row in defaults.iterrows():
        entry: dict[str, Any] = {"name": str(name)}
        for col in _SERVED:
            if col not in defaults.columns:
                entry[col] = None
                continue
            entry[col] = clean_scalar(_py(row[col]))
        # Normalise the four fields whose exact JSON type the frontend relies on.
        entry["status"] = "" if entry["status"] is None else str(entry["status"])
        entry["dtype"] = "" if entry["dtype"] is None else str(entry["dtype"])
        entry["type"] = "" if entry["type"] is None else str(entry["type"])
        entry["varying"] = bool(entry["varying"])
        entry["default_text"] = _default_text(row["default"])
        out.append(entry)
    return out

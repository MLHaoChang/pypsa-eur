"""
Line specials: haversine length rewrite + consenting impedance rescale.

Lifted out of ``routers/network.py``. Plain line CRUD stays on the router.
Geometry primitives stay in ``services.network_geometry`` — this module owns
lock / mutation / changelog only. Never imports ``routers.*``.
"""
from __future__ import annotations

from services import change_log_service
from services.network_geometry import (
    _IMPEDANCE_FIELDS,
    _impedance_preview,
    _line_haversine_km,
)
from services.pypsa_service import PyPSAService


def apply_recalculate_line_lengths() -> dict:
    """
    Rewrite n.lines.length (km) from haversine distance between bus0 / bus1
    coordinates. Buses without a usable (x, y) pair are skipped — their lines'
    length stays unchanged. Returns counts so the frontend can show a summary.

    Triggered by the "Recalculate from coordinates" button in the map toolbar.
    The user is expected to acknowledge that this overrides existing length
    values (which feed length-scaled capital_cost models downstream).
    """
    n = PyPSAService.get_network()
    if n.lines.empty:
        return {"updated": 0, "skipped": 0, "total": 0, "rescale": []}

    updated = 0
    skipped = 0
    previews: list[dict] = []
    with PyPSAService.get_lock():
        for line_name in n.lines.index:
            b0 = str(n.lines.at[line_name, "bus0"]) if "bus0" in n.lines.columns else ""
            b1 = str(n.lines.at[line_name, "bus1"]) if "bus1" in n.lines.columns else ""
            d_km = _line_haversine_km(n, b0, b1)
            if d_km is None:
                skipped += 1
                continue
            old_length = float(n.lines.at[line_name, "length"])
            old = {k: float(n.lines.at[line_name, k]) for k in _IMPEDANCE_FIELDS}
            n.lines.at[line_name, "length"] = float(d_km)
            updated += 1
            p = _impedance_preview(str(line_name), old_length, float(d_km), old)
            if p is not None:
                previews.append(p)

    change_log_service.log(
        "update", "Lines", "(haversine)",
        f"Recalculated line lengths from bus coordinates: {updated} updated, {skipped} skipped",
    )
    return {"updated": updated, "skipped": skipped, "total": int(len(n.lines)), "rescale": previews}


def apply_rescale_impedances(req) -> dict:
    """
    Write the previewed impedances for an explicit list of lines.

    Deliberately takes the VALUES rather than recomputing them: by the time the
    user consents, the length has already been rewritten, so the old per-km is
    no longer derivable from the network. Recomputing here would silently use
    the new length as the old one and scale by 1.
    """
    n = PyPSAService.get_network()
    updated = 0
    skipped: list[dict] = []
    with PyPSAService.get_lock():
        for entry in req.lines:
            if entry.name not in n.lines.index:
                skipped.append({"name": entry.name, "reason": "unknown-line"})
                continue
            n.lines.at[entry.name, "r"] = float(entry.r)
            n.lines.at[entry.name, "x"] = float(entry.x)
            n.lines.at[entry.name, "b"] = float(entry.b)
            updated += 1
    if updated:
        change_log_service.log(
            "update", "Lines", "(rescale)",
            f"Rescaled impedance on {updated} line(s) to preserve per-km values after a length change",
        )
    return {"updated": updated, "skipped": skipped}

"""
Transformer voltage validation, voltage enrichment, and type sanitisation.

Extracted verbatim from `routers/network.py`; `routers.network` re-exports
every name. `_validate_transformer_voltage` raises `HTTPException` — it is a
request validator, and returning a 400 is its whole job; `services/chat_tools.py`
raises the same way. That is a framework import, not a router import: nothing
here imports anything under `routers/`.
"""
from __future__ import annotations

from fastapi import HTTPException


# Tolerance (kV) when matching declared v_nom_X against actual bus voltages.
# Loose enough to accept 132 vs 132.0001 floats but tight enough to reject any
# real mismatch (380 vs 220 differs by 160 kV — far past 0.5).
_VNOM_TOL_KV = 0.5


def _validate_transformer_voltage(n, bus0: str, bus1: str,
                                  v_nom_0: float | None, v_nom_1: float | None) -> None:
    """
    Reject creation/edit when the declared step doesn't match the buses.

    `v_nom_0 is None` OR `v_nom_1 is None` ⇒ user opted out of the check
    (omitted the field from the payload, or picked Custom and left it
    blank). Allows either orientation — bus0 may be the high or low side.
    Legacy `<= 0` sentinel still honoured for old project files / external
    callers that send 0.0.
    """
    if v_nom_0 is None or v_nom_1 is None or v_nom_0 <= 0.0 or v_nom_1 <= 0.0:
        return
    if bus0 not in n.buses.index:
        raise HTTPException(404, f"Bus '{bus0}' not found")
    if bus1 not in n.buses.index:
        raise HTTPException(404, f"Bus '{bus1}' not found")
    actual_0 = float(n.buses.at[bus0, "v_nom"]) if "v_nom" in n.buses.columns else 0.0
    actual_1 = float(n.buses.at[bus1, "v_nom"]) if "v_nom" in n.buses.columns else 0.0
    same_orientation = (
        abs(actual_0 - v_nom_0) <= _VNOM_TOL_KV
        and abs(actual_1 - v_nom_1) <= _VNOM_TOL_KV
    )
    swapped_orientation = (
        abs(actual_0 - v_nom_1) <= _VNOM_TOL_KV
        and abs(actual_1 - v_nom_0) <= _VNOM_TOL_KV
    )
    if not (same_orientation or swapped_orientation):
        raise HTTPException(
            400,
            f"Voltage mismatch: transformer expects {v_nom_0:g}/{v_nom_1:g} kV "
            f"but bus '{bus0}' is {actual_0:g} kV and bus '{bus1}' is {actual_1:g} kV. "
            "Adjust the bus v_nom values or pick a transformer type that matches.",
        )


def _enrich_transformer_voltage(rows: list[dict], n) -> list[dict]:
    """
    Inject derived v_nom_0/v_nom_1 from connected buses into each row.

    PyPSA stores voltages on the buses, not on the Transformer. The GUI
    surfaces these as columns in the bottom-panel table and the right-panel
    properties view, so we attach them here on the read path.
    """
    if "v_nom" not in n.buses.columns:
        return rows
    for r in rows:
        bus0 = r.get("bus0")
        bus1 = r.get("bus1")
        r["v_nom_0"] = float(n.buses.at[bus0, "v_nom"]) if bus0 in n.buses.index else None
        r["v_nom_1"] = float(n.buses.at[bus1, "v_nom"]) if bus1 in n.buses.index else None
    return rows


def _sanitise_transformer_type(n, payload: dict) -> dict:
    """
    The GUI's transformer presets ("380/220 kV" etc.) are stored on
    `transformer.type` for display purposes, but PyPSA treats `type` as a
    foreign key into `n.transformer_types` and crashes at solve time with
    "type does not exist in n.transformer_types" when it isn't registered.

    Our presets ARE NOT PyPSA transformer types — they're UI helpers that
    already filled in the explicit r/x/s_nom values. So if the user-supplied
    `type` isn't in n.transformer_types, drop it (PyPSA will then use the
    explicit parameters) but keep its label out of harm's way.
    """
    raw_type = payload.get("type", "")
    if not raw_type:
        return payload
    try:
        known = set(n.transformer_types.index)
    except Exception:
        known = set()
    if raw_type in known:
        return payload  # legitimate PyPSA type — pass through unchanged
    # Strip the type so n.add() falls back to the explicit s_nom / x we sent.
    cleaned = dict(payload)
    cleaned["type"] = ""
    return cleaned

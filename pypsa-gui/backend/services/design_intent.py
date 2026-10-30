"""
Asset sizing loop AS-01 — derived values and checks for a DesignIntentSpec.

Pure functions of the spec and the critical load; nothing is written to a
network. Hard constraints return as technology bounds the sizing step reads.
"""
from __future__ import annotations

from models.design_intent import DesignIntentSpec


def _check_critical(critical_mw: float) -> float:
    if not critical_mw > 0:
        raise ValueError(f"critical_mw must be > 0, got {critical_mw}")
    return critical_mw


def backup_energy_mwh(spec: DesignIntentSpec, critical_mw: float) -> float | None:
    """Intent A default backup energy (critical load x hours); None otherwise."""
    _check_critical(critical_mw)
    return critical_mw * spec.backup_autonomy_h if spec.intent == "A" else None


def bridge_reserve_mwh(spec: DesignIntentSpec, critical_mw: float) -> float | None:
    """Intent D bridge reserve (critical load x hours); None otherwise."""
    _check_critical(critical_mw)
    return critical_mw * spec.bridge_reserve_h if spec.intent == "D" else None


def genset_p_nom_min_mw(spec: DesignIntentSpec, critical_mw: float) -> float | None:
    """Intents B/C: genset p_nom >= P_crit; None otherwise."""
    _check_critical(critical_mw)
    return critical_mw if spec.intent in ("B", "C") else None


def technology_bounds(spec: DesignIntentSpec, critical_mw: float) -> dict[str, dict]:
    """Hard constraints as per-technology bounds (MW, MWh)."""
    _check_critical(critical_mw)
    out: dict[str, dict] = {}
    if spec.intent == "A":
        out["genset"] = {"p_nom_max": 0.0}
        out["energy_bess_gfm"] = {"e_nom_min": backup_energy_mwh(spec, critical_mw)}
    elif spec.intent in ("B", "C"):
        out["genset"] = {"p_nom_min": genset_p_nom_min_mw(spec, critical_mw)}
        if spec.genset_run_hours_cap is not None:
            out["genset"]["run_hours_cap"] = spec.genset_run_hours_cap
    elif spec.intent == "D":
        out["bess_gfm"] = {"e_nom_min": bridge_reserve_mwh(spec, critical_mw)}
    return out


def grid_code_warnings(spec: DesignIntentSpec, profile: dict) -> list[str]:
    """Intent D: warn unless the profile accepts transfer to BESS as ride-through
    (AS-NOUPS-1). A missing key counts as not accepted. Never blocks."""
    if spec.intent != "D" or profile.get("bess_transfer_ride_through") is True:
        return []
    return ["Intent D (no UPS): the grid-code profile does not accept transfer to "
            "BESS as ride-through; the BESS must bridge in parallel without transfer."]

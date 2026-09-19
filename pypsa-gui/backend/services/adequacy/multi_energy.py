"""
P6(a) — dedicated-bus multi-energy ENS disclosure.

Honesty: one VOLL slack per bus cannot attribute shed to a Load identity.
Unmet H₂/heat is reportable only when buses are carrier-dedicated (Loads on a
bus share one canonical carrier class matching ``buses.carrier``). Shared-bus
models fail closed — they do not invent multi-energy ENS from bus-carrier
roll-ups alone.

Design: docs/superpowers/findings/2026-09-19-eh-p6-multi-energy-spike.md
"""
from __future__ import annotations

from typing import Any

import pandas as pd

ATTRIBUTION = "dedicated_bus_by_carrier"
HONESTY_NOTES = (
    "dedicated_bus_by_carrier",
    "no_per_load_attribution",
    "shared_bus_not_supported",
)


def _carrier_key(raw) -> str:
    from services.solver_service import _canonical_load_carrier_key
    return _canonical_load_carrier_key(raw)


def bus_carrier_key(n, bus: str) -> str | None:
    buses = getattr(n, "buses", None)
    if buses is None or bus not in buses.index:
        return None
    if "carrier" not in getattr(buses, "columns", []):
        return "electrical"
    return _carrier_key(buses.at[bus, "carrier"])


def carrier_columns(n, columns, carrier: str) -> list[str]:
    """Subset of bus columns whose bus carrier canonicalises to ``carrier``."""
    want = str(carrier)
    out: list[str] = []
    for col in columns:
        key = bus_carrier_key(n, str(col))
        if key is None:
            # Unclassifiable column kept only for electrical parity elsewhere;
            # multi-energy scope drops unknowns rather than inventing a bucket.
            continue
        if key == want:
            out.append(str(col))
    return out


def dedicated_carrier_violations(n) -> list[str]:
    """
    Return human-readable violations when a bus is not carrier-dedicated.

    A bus is dedicated when every Load on it has the same canonical carrier
    key as ``buses.carrier`` (blank bus carrier → electrical). Empty buses
    (no loads) are not violations.
    """
    loads = getattr(n, "loads", None)
    buses = getattr(n, "buses", None)
    if loads is None or loads.empty or "bus" not in loads.columns:
        return []
    violations: list[str] = []
    for bus, group in loads.groupby(loads["bus"].astype(str)):
        bus_key = bus_carrier_key(n, str(bus))
        if bus_key is None:
            violations.append(f"bus {bus}: missing from network.buses")
            continue
        load_keys: set[str] = set()
        for name, row in group.iterrows():
            raw = row.get("carrier") if "carrier" in group.columns else None
            load_keys.add(_carrier_key(raw))
        if len(load_keys) > 1:
            violations.append(
                f"bus {bus}: mixed load carriers {sorted(load_keys)} "
                f"(shared-bus attribution unsupported)"
            )
        elif load_keys and (only := next(iter(load_keys))) != bus_key:
            violations.append(
                f"bus {bus}: loads are {only!r} but buses.carrier is {bus_key!r}"
            )
    return violations


def ens_by_carrier_mwh(n, bus_period_mwh) -> dict[str, float]:
    """
    Sum shed MWh by bus carrier class over a (period × bus) capture frame.

    Callers must preflight dedicated buses; this helper does not invent
    per-Load splits.
    """
    if bus_period_mwh is None or getattr(bus_period_mwh, "empty", True):
        return {}
    bp = bus_period_mwh
    totals: dict[str, float] = {}
    for col in bp.columns:
        key = bus_carrier_key(n, str(col))
        if key is None:
            continue
        totals[key] = totals.get(key, 0.0) + float(bp[col].clip(lower=0.0).sum())
    return {k: float(v) for k, v in sorted(totals.items()) if v > 0.0 or k == "electrical"}


def multi_energy_section_from_capture(
    n, capture: dict | None,
) -> tuple[str, dict[str, Any] | None, str | None]:
    """
    Build the EH ``multi_energy`` section (status, payload, note).

    * No non-electrical load buses → ``skipped`` (electrical-only project).
    * Dedicated-bus violations → ``not_established`` (fail-closed).
    * Otherwise ``ok`` with ``ens_by_carrier_mwh`` + honesty pins.
    """
    violations = dedicated_carrier_violations(n)
    if violations:
        return (
            "not_established",
            {
                "attribution": ATTRIBUTION,
                "honesty": list(HONESTY_NOTES),
                "violations": violations,
                "ens_by_carrier_mwh": None,
            },
            "shared or mismatched bus/load carriers — multi-energy ENS not established",
        )

    loads = getattr(n, "loads", None)
    buses = getattr(n, "buses", None)
    non_elec_load = False
    if loads is not None and not loads.empty:
        for _name, row in loads.iterrows():
            raw = row.get("carrier") if "carrier" in loads.columns else None
            if _carrier_key(raw) != "electrical":
                non_elec_load = True
                break
    if not non_elec_load:
        # Also check bus carriers in case loads omit carrier but buses are H2.
        if buses is not None and not buses.empty and "carrier" in buses.columns:
            for b in buses.index:
                if bus_carrier_key(n, str(b)) not in (None, "electrical"):
                    # Transit / resource buses without loads do not force a section.
                    if loads is not None and not loads.empty:
                        on_bus = loads["bus"].astype(str) == str(b)
                        if bool(on_bus.any()):
                            non_elec_load = True
                            break
    if not non_elec_load:
        return (
            "skipped",
            None,
            "no non-electrical loads — multi-energy section not applicable",
        )

    bp = None
    if isinstance(capture, dict):
        bp = capture.get("lost_load_bus_period_mwh")
    by_c = ens_by_carrier_mwh(n, bp)
    payload = {
        "attribution": ATTRIBUTION,
        "honesty": list(HONESTY_NOTES),
        "ens_by_carrier_mwh": by_c,
        "violations": [],
    }
    note = (
        f"unmet by carrier (dedicated-bus roll-up): "
        + ", ".join(f"{k}={v:.4g} MWh" for k, v in by_c.items())
        if by_c else "no shed captured on dedicated carrier buses"
    )
    return "ok", payload, note


def carrier_eue_mwh(n, capture: dict | None, carrier: str) -> float:
    """ΔEUE-style total shed MWh on buses of one carrier class (FMEA helper)."""
    if not isinstance(capture, dict):
        return 0.0
    bp = capture.get("lost_load_bus_period_mwh")
    if bp is None or getattr(bp, "empty", True):
        return 0.0
    cols = carrier_columns(n, list(bp.columns), carrier)
    if not cols:
        return 0.0
    return float(bp[cols].clip(lower=0.0).to_numpy().sum())

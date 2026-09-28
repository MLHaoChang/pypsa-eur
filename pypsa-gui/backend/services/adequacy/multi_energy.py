"""
P6 — multi-energy ENS disclosure (dedicated-bus + per-Load slack).

P6(a): unmet H₂/heat reportable when buses are carrier-dedicated.
P6(b): per-Load VOLL slacks unlock shared-bus attribution via
``lost_load_load_period_mwh`` — never invent ENS from bus roll-ups alone.

Design:
- docs/superpowers/findings/2026-09-19-eh-p6-multi-energy-spike.md
- docs/superpowers/findings/2026-09-24-eh-p6b-multislack-spike.md
"""
from __future__ import annotations

from typing import Any

import pandas as pd

ATTRIBUTION_DEDICATED = "dedicated_bus_by_carrier"
ATTRIBUTION_PER_LOAD = "per_load_slack"
HONESTY_DEDICATED = (
    "dedicated_bus_by_carrier",
    "no_per_load_attribution",
    "shared_bus_not_supported",
)
HONESTY_PER_LOAD = (
    "per_load_slack",
    "shared_bus_supported",
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


def load_carrier_key(n, load_id: str) -> str | None:
    loads = getattr(n, "loads", None)
    if loads is None or loads.empty or load_id not in loads.index:
        return None
    raw = None
    if "carrier" in loads.columns:
        raw = loads.at[load_id, "carrier"]
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or str(raw).strip() == "":
        bus = str(loads.at[load_id, "bus"]) if "bus" in loads.columns else None
        if bus is not None:
            return bus_carrier_key(n, bus)
    return _carrier_key(raw)


def carrier_columns(n, columns, carrier: str) -> list[str]:
    """Subset of columns whose Load or bus carrier canonicalises to ``carrier``."""
    want = str(carrier)
    out: list[str] = []
    loads = getattr(n, "loads", None)
    for col in columns:
        key = None
        if loads is not None and not loads.empty and col in loads.index:
            key = load_carrier_key(n, str(col))
        else:
            key = bus_carrier_key(n, str(col))
        if key is None:
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


def _has_non_electrical_loads(n) -> bool:
    loads = getattr(n, "loads", None)
    buses = getattr(n, "buses", None)
    if loads is not None and not loads.empty:
        for _name, row in loads.iterrows():
            raw = row.get("carrier") if "carrier" in loads.columns else None
            if _carrier_key(raw) != "electrical":
                return True
    if buses is not None and not buses.empty and "carrier" in buses.columns:
        if loads is None or loads.empty:
            return False
        for b in buses.index:
            if bus_carrier_key(n, str(b)) not in (None, "electrical"):
                on_bus = loads["bus"].astype(str) == str(b)
                if bool(on_bus.any()):
                    return True
    return False


def ens_by_carrier_mwh(n, period_mwh) -> dict[str, float]:
    """
    Sum shed MWh by carrier class over a (period × identity) capture frame.

    Identity columns may be Load ids (P6b) or bus ids (P6a / bus roll-up).
    """
    if period_mwh is None or getattr(period_mwh, "empty", True):
        return {}
    bp = period_mwh
    loads = getattr(n, "loads", None)
    totals: dict[str, float] = {}
    for col in bp.columns:
        if loads is not None and not loads.empty and col in loads.index:
            key = load_carrier_key(n, str(col))
        else:
            key = bus_carrier_key(n, str(col))
        if key is None:
            continue
        totals[key] = totals.get(key, 0.0) + float(bp[col].clip(lower=0.0).sum())
    return {k: float(v) for k, v in sorted(totals.items()) if v > 0.0 or k == "electrical"}


def ens_by_load_mwh(n, load_period_mwh) -> dict[str, float]:
    """Per-Load shed totals (only columns that are Load ids)."""
    if load_period_mwh is None or getattr(load_period_mwh, "empty", True):
        return {}
    loads = getattr(n, "loads", None)
    if loads is None or loads.empty:
        return {}
    out: dict[str, float] = {}
    for col in load_period_mwh.columns:
        if col not in loads.index:
            continue
        v = float(load_period_mwh[col].clip(lower=0.0).sum())
        if v > 0.0:
            out[str(col)] = v
    return dict(sorted(out.items()))


def _load_period_frame(capture: dict | None):
    if not isinstance(capture, dict):
        return None
    lp = capture.get("lost_load_load_period_mwh")
    if lp is not None and not getattr(lp, "empty", True):
        return lp
    # Fallback: lost_load_t columns that are Load ids, period-summed.
    ll = capture.get("lost_load_t")
    if ll is None or getattr(ll, "empty", True):
        return None
    return None


def multi_energy_section_from_capture(
    n, capture: dict | None,
) -> tuple[str, dict[str, Any] | None, str | None]:
    """
    Build the EH ``multi_energy`` section (status, payload, note).

    * No non-electrical loads → ``skipped``.
    * Per-Load capture present → ``ok`` with ``per_load_slack`` (shared-bus OK).
    * Else dedicated-bus violations → ``not_established``.
    * Else dedicated-bus roll-up → ``ok`` with ``dedicated_bus_by_carrier``.
    """
    if not _has_non_electrical_loads(n):
        return (
            "skipped",
            None,
            "no non-electrical loads — multi-energy section not applicable",
        )

    lp = None
    if isinstance(capture, dict):
        lp = capture.get("lost_load_load_period_mwh")
    per_load = (
        lp is not None
        and not getattr(lp, "empty", True)
        and ens_by_load_mwh(n, lp) is not None
    )
    # Treat as per-load when any column matches a Load id.
    loads = getattr(n, "loads", None)
    if per_load and loads is not None and not loads.empty:
        if any(c in loads.index for c in lp.columns):
            by_c = ens_by_carrier_mwh(n, lp)
            by_l = ens_by_load_mwh(n, lp)
            payload = {
                "attribution": ATTRIBUTION_PER_LOAD,
                "honesty": list(HONESTY_PER_LOAD),
                "ens_by_carrier_mwh": by_c,
                "ens_by_load_mwh": by_l,
                "violations": [],
            }
            note = (
                "unmet by carrier (per-Load slack): "
                + ", ".join(f"{k}={v:.4g} MWh" for k, v in by_c.items())
                if by_c else "no shed captured on Load slacks"
            )
            return "ok", payload, note

    violations = dedicated_carrier_violations(n)
    if violations:
        return (
            "not_established",
            {
                "attribution": ATTRIBUTION_DEDICATED,
                "honesty": list(HONESTY_DEDICATED),
                "violations": violations,
                "ens_by_carrier_mwh": None,
            },
            "shared or mismatched bus/load carriers — multi-energy ENS not established",
        )

    bp = None
    if isinstance(capture, dict):
        bp = capture.get("lost_load_bus_period_mwh")
    by_c = ens_by_carrier_mwh(n, bp)
    payload = {
        "attribution": ATTRIBUTION_DEDICATED,
        "honesty": list(HONESTY_DEDICATED),
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
    """ΔEUE-style total shed MWh on Load/bus columns of one carrier class."""
    if not isinstance(capture, dict):
        return 0.0
    bp = capture.get("lost_load_load_period_mwh")
    if bp is None or getattr(bp, "empty", True):
        bp = capture.get("lost_load_bus_period_mwh")
    if bp is None or getattr(bp, "empty", True):
        return 0.0
    cols = carrier_columns(n, list(bp.columns), carrier)
    if not cols:
        return 0.0
    return float(bp[cols].clip(lower=0.0).to_numpy().sum())

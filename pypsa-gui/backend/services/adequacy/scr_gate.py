"""
P9 — EH SCR feasibility gate (warn-only thin slice).

Gridspine ``strength.SCR_BANDS`` remain report-only. This module applies the
EH product rule on top of those band edges:

* SCR >= 3.0 → ``pass``, ``emt_recommended=False``
* SCR < 3.0  → ``warn``, ``emt_recommended=True``

``fail`` is reserved (SCR < 2 would be the natural block threshold later).
This thin slice never returns ``fail`` so weak_flexible studies stay
informational.

No full 60909 fault study is required. The proxy is:

* PoC buses = ``buses.eh_poc`` truthy
* Numerator = ``buses.eh_sk_mva`` (caller-supplied short-circuit MVA)
* Denominator = ``buses.eh_ibr_mva`` when set and finite, else sum of
  installed Generator ``p_nom`` (prefer ``p_nom_opt``) for IBR carriers at
  that bus — installed MW read as MVA, matching the gridspine SCR ledger.

The gate uses the **minimum** SCR across PoC buses.
"""
from __future__ import annotations

import math
from typing import Any

from gridspine.static.strength import SCR_BANDS, band as scr_band
from models.energy_hub import GatesBlock, SectionStatus

# Moderate edge of SCR_BANDS (2, 3, 5) — pass/warn threshold.
PASS_SCR = float(SCR_BANDS[1])  # 3.0
# Reserved for a later block rule; thin slice maps everything below PASS to warn.
_FAIL_SCR_RESERVED = float(SCR_BANDS[0])  # 2.0

IBR_CARRIERS = frozenset({
    "wind", "onwind", "offwind", "offwind-ac", "offwind-dc",
    "solar", "solar-utility", "solar-rooftop", "solar pv", "pv",
    "inverter",
})

METHOD = "eh_sk_mva_proxy"


def classify_scr(scr: float) -> GatesBlock:
    """Map a finite SCR to the EH product rule (never ``fail`` in this slice)."""
    if not math.isfinite(scr):
        raise ValueError(f"non-finite SCR: {scr!r}")
    if scr >= PASS_SCR:
        return GatesBlock(scr="pass", emt_recommended=False)
    return GatesBlock(scr="warn", emt_recommended=True)


def _truthy_poc(val: Any) -> bool:
    return val is True or str(val).lower() in ("true", "1", "yes")


def _poc_buses(n) -> list[str]:
    if n.buses is None or n.buses.empty or "eh_poc" not in n.buses.columns:
        return []
    return [
        str(b) for b in n.buses.index
        if _truthy_poc(n.buses.at[b, "eh_poc"])
    ]


def _installed_mw(row) -> float:
    try:
        p_nom = float(row.get("p_nom", 0.0) or 0.0)
    except (TypeError, ValueError):
        p_nom = 0.0
    if "p_nom_opt" in row.index:
        try:
            opt = float(row.get("p_nom_opt"))
            if math.isfinite(opt) and opt > 0:
                p_nom = opt
        except (TypeError, ValueError):
            pass
    return max(0.0, p_nom)


def _ibr_mva_at_bus(n, bus: str) -> float | None:
    """Explicit ``eh_ibr_mva`` wins; else sum IBR generator installed MW as MVA."""
    if "eh_ibr_mva" in n.buses.columns:
        try:
            override = float(n.buses.at[bus, "eh_ibr_mva"])
            if math.isfinite(override) and override > 0:
                return override
        except (TypeError, ValueError):
            pass
    if n.generators is None or n.generators.empty:
        return None
    gens = n.generators
    if "bus" not in gens.columns:
        return None
    total = 0.0
    found = False
    for _name, row in gens.iterrows():
        if str(row.get("bus", "")) != bus:
            continue
        carrier = str(row.get("carrier", "") or "").lower()
        if carrier not in IBR_CARRIERS:
            continue
        found = True
        total += _installed_mw(row)
    if not found or total <= 0:
        return None
    return float(total)


def _sk_mva_at_bus(n, bus: str) -> float | None:
    if "eh_sk_mva" not in n.buses.columns:
        return None
    try:
        sk = float(n.buses.at[bus, "eh_sk_mva"])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(sk) or sk <= 0:
        return None
    return sk


def evaluate_network_scr_gate(
    n,
) -> tuple[GatesBlock | None, SectionStatus, dict[str, Any] | None, str | None]:
    """Compute PoC proxy SCRs and return (gates, section status, payload, note)."""
    pocs = _poc_buses(n)
    if not pocs:
        return (
            None,
            "not_established",
            None,
            "no eh_poc buses tagged for SCR gate",
        )

    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for bus in pocs:
        sk = _sk_mva_at_bus(n, bus)
        ibr = _ibr_mva_at_bus(n, bus)
        if sk is None or ibr is None:
            missing.append(bus)
            continue
        value = sk / ibr
        if not math.isfinite(value):
            missing.append(bus)
            continue
        rows.append({
            "bus": bus,
            "sk_mva": sk,
            "ibr_mva": ibr,
            "scr": value,
            "band": scr_band(value),
        })

    if missing and not rows:
        return (
            None,
            "not_established",
            None,
            "eh_sk_mva (and IBR capacity) required on eh_poc buses for SCR gate; "
            f"missing/invalid at: {', '.join(missing)}",
        )
    if not rows:
        return (
            None,
            "not_established",
            None,
            "could not compute SCR at any eh_poc bus",
        )

    min_row = min(rows, key=lambda r: r["scr"])
    min_scr = float(min_row["scr"])
    gate = classify_scr(min_scr)
    payload = {
        "method": METHOD,
        "pass_scr": PASS_SCR,
        "fail_scr_reserved": _FAIL_SCR_RESERVED,
        "min_scr": min_scr,
        "min_bus": min_row["bus"],
        "buses": rows,
        "incomplete_pocs": missing or None,
        "warn_only": True,
    }
    note = (
        f"SCR {gate.scr} at {min_row['bus']} "
        f"(min SCR={min_scr:.3g}, threshold={PASS_SCR:g}; warn-only thin slice)"
    )
    return gate, "ok", payload, note

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
* Denominator = ``buses.eh_ibr_mva`` when set and finite (``stated``), else,
  on a network with one PoC, derived from its converter-coupled assets
  (``derived``): IBR-carrier Generators and every StorageUnit, except the
  units behind a set power architecture (the caller's ``behind``: the UPS and
  the critical bus, whose rectifier feeds no fault upstream). Installed MW is
  read as MVA (an extendable asset's ``p_nom_opt``, unsized without one),
  matching the gridspine SCR ledger. A non-finite rating is unknown. With several PoCs only the assets on each PoC bus
  itself count: which assets sit behind which PoC is not known.

The gate uses the **minimum** SCR across PoC buses. Every tagged
``eh_poc`` must have a computable SCR — partial coverage is fail-closed
(``not_established``), never a soft ``ok`` over a subset.
"""
from __future__ import annotations

import math
from typing import Any

from models.energy_hub import GatesBlock, SectionStatus
try:
    from gridspine.drivers.process_chain import truthy as _truthy
except ImportError:  # a build without gridspine: the one pinned fallback, not a new copy
    from services.solver.process_uc import _fallback_truthy as _truthy

# gridspine is repo-root (not in gui-requirements pip freeze). Guard like
# ``gridspine_service`` so the frozen desktop build 503s/fail-closes instead of
# importing a missing distribution at module load.
try:
    from gridspine.static.strength import SCR_BANDS, band as scr_band
except ImportError:  # pragma: no cover - build-shape branch
    SCR_BANDS = (2.0, 3.0, 5.0)

    def scr_band(value: float) -> str:
        if value < SCR_BANDS[0]:
            return "weak"
        if value < SCR_BANDS[1]:
            return "moderate"
        if value < SCR_BANDS[2]:
            return "strong"
        return "very_strong"

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


def _poc_buses(n) -> list[str]:
    if n.buses is None or n.buses.empty or "eh_poc" not in n.buses.columns:
        return []
    return [
        str(b) for b in n.buses.index
        if _truthy(n.buses.at[b, "eh_poc"])
    ]


class _Invalid(ValueError):
    """A non-finite or negative rating: the capacity is unknown, never 0."""


def _rating(row) -> tuple[float, bool]:
    """``(MVA, unsized)``: installed MW read as MVA. An extendable asset reads
    its ``p_nom_opt`` (the saved network's last solve), and without one it is
    an unsized candidate counting 0; any other asset reads ``p_nom``. A
    non-finite or negative value raises ``_Invalid``."""
    def num(key):
        try:
            v = float(row.get(key))
        except (TypeError, ValueError):
            raise _Invalid(key) from None
        if not math.isfinite(v) or v < 0:
            raise _Invalid(key)
        return v

    if _truthy(row.get("p_nom_extendable", False)):
        opt = row.get("p_nom_opt") if "p_nom_opt" in row.index else None
        if opt is None or (isinstance(opt, float) and math.isnan(opt)):
            return 0.0, True
        v = num("p_nom_opt")
        return v, v <= 0
    return num("p_nom"), False


def _stated_ibr_mva(n, bus: str) -> float | None:
    """The PoC bus's ``eh_ibr_mva``; None when blank (NaN). Infinite, zero or
    negative raises ``_Invalid``."""
    if "eh_ibr_mva" not in n.buses.columns:
        return None
    try:
        v = float(n.buses.at[bus, "eh_ibr_mva"])
    except (TypeError, ValueError):
        raise _Invalid("eh_ibr_mva") from None
    if math.isnan(v):
        return None
    if not math.isfinite(v) or v <= 0:
        raise _Invalid("eh_ibr_mva")
    return v


def converter_assets(n, bus: str | None = None,
                     behind: frozenset[str] = frozenset()) -> dict[str, dict]:
    """The converter-coupled assets ``{name: {"mva", "unsized"}}`` (only those
    on ``bus`` when given): IBR-carrier Generators and every StorageUnit,
    except the units ``behind`` a set power architecture
    (``dc_architecture.behind_architecture``)."""
    out: dict[str, dict] = {}
    for frame, ibr_only in (("generators", True), ("storage_units", False)):
        df = getattr(n, frame, None)
        if df is None or df.empty or "bus" not in df.columns:
            continue
        for name, row in df.iterrows():
            if str(name) in behind or (bus is not None and str(row.get("bus", "")) != bus):
                continue
            if ibr_only and str(row.get("carrier", "") or "").lower() not in IBR_CARRIERS:
                continue
            mva, unsized = _rating(row)
            out[str(name)] = {"mva": mva, "unsized": unsized}
    return out


def _ibr_mva_at_bus(n, bus: str, n_pocs: int, behind: frozenset[str]) -> dict[str, Any] | None:
    """``{"mva", "source", "assets", "derived_mva", "unsized"}``: the stated
    ``eh_ibr_mva`` wins; else the converter-coupled assets' sum (the
    network's with one PoC, the bus's own with several); None when there are
    none. Raises ``_Invalid`` on a non-finite value."""
    stated = _stated_ibr_mva(n, bus)
    found = converter_assets(n, None if n_pocs == 1 else bus, behind)
    assets = {k: v["mva"] for k, v in found.items()}
    unsized = [k for k, v in found.items() if v["unsized"]]
    derived = float(sum(assets.values())) if assets else None
    out = {"assets": assets, "derived_mva": derived, "unsized": unsized,
           "excluded": sorted(behind)}
    if stated is not None:
        return {**out, "mva": stated, "source": "stated"}
    if derived is None or derived <= 0:
        return None
    return {**out, "mva": derived, "source": "derived"}


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
    n, *, behind: frozenset[str] | set[str] = frozenset(),
) -> tuple[GatesBlock | None, SectionStatus, dict[str, Any] | None, str | None]:
    """Compute PoC proxy SCRs and return (gates, section status, payload, note).
    ``behind``: the units behind a set power architecture, left out of a
    derived inverter capacity (DC grid-readiness walkthrough 05)."""
    behind = frozenset(str(x) for x in behind)
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
        try:
            basis = _ibr_mva_at_bus(n, bus, len(pocs), behind)
        except _Invalid:
            basis = None
        if sk is None or basis is None:
            missing.append(bus)
            continue
        ibr = basis["mva"]
        value = sk / ibr
        if not math.isfinite(value):
            missing.append(bus)
            continue
        rows.append({
            "bus": bus,
            "sk_mva": sk,
            "ibr_mva": ibr,
            "ibr_source": basis["source"],
            "ibr_assets": basis["assets"],
            "ibr_derived_mva": basis["derived_mva"],
            "ibr_unsized": basis["unsized"],
            "ibr_excluded": basis["excluded"],
            "scr": value,
            "band": scr_band(value),
        })

    # Fail-closed: any incomplete PoC blocks the gate (no soft ok over a subset).
    if missing:
        return (
            None,
            "not_established",
            None,
            "eh_sk_mva (and IBR capacity) required on ALL eh_poc buses for SCR "
            f"gate; missing/invalid at: {', '.join(missing)}"
            + ("; with several PoCs only the converters on each PoC bus count: "
               "state eh_ibr_mva on each" if len(pocs) > 1 else ""),
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
        "incomplete_pocs": None,
        "warn_only": True,
    }
    note = (
        f"SCR {gate.scr} at {min_row['bus']} "
        f"(min SCR={min_scr:.3g}, threshold={PASS_SCR:g}; warn-only thin slice)"
    )
    return gate, "ok", payload, note

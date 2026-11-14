"""The plant business case's verdict (business case 14; spec §7).

"Is this plant worth building?" reads one stored, current business-case report and
answers from its project NPV alone: `worth_building` at an NPV of 0 or more,
`not_worth_building` below it, `not_established` when the NPV is not established (never
read as 0). There is no "marginal" band: no sourced threshold exists for one. The
break-even price is the driver that could flip it, given as how far the break-even line's
price can fall (or must rise) before the NPV reaches 0, finite or None, never inf. A
verdict that rests on assumed pack defaults lists their lines (`rests_on_defaults`).

Pure: reads the report dict the runner stores, writes nothing.
"""

from __future__ import annotations

import math
from typing import Any

__all__ = ["verdict"]

#: The KPIs the verdict quotes beside its status, in the order it says them.
FIGURES = (
    "ebitda_margin_net",
    "ebitda_margin_gross",
    "project_npv",
    "project_irr",
    "equity_npv",
    "equity_irr",
    "payback_years",
)


def _finite(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _ok_number(k: Any) -> float | None:
    if not isinstance(k, dict) or k.get("status") != "ok":
        return None
    return _finite(k.get("value"))


def _flip(be: Any) -> dict:
    """The break-even line's price at NPV 0 and the change from the entered price."""
    be = be if isinstance(be, dict) else {}
    out = {
        "line": be.get("line"),
        "status": be.get("status") or "not_established",
        "price": _finite(be.get("price")),
        "money_year": be.get("money_year"),
        "price_scale": _finite(be.get("price_scale")),
        "change_to_npv_zero_pct": None,
    }
    scale = out["price_scale"]
    if out["status"] != "ok" or scale is None:
        return out
    # Scale 0.8: the price can fall 20 % before NPV 0; scale 1.25: it must rise 25 %.
    out["change_to_npv_zero_pct"] = round((float(scale) - 1.0) * 100.0, 1)
    return out


def verdict(report: dict | None) -> dict:
    """The verdict of one current stored report (the caller checks it is not stale)."""
    kpis = (report or {}).get("kpis") or {}
    npv_kpi = kpis.get("project_npv")
    npv = _ok_number(npv_kpi)
    if npv is None:
        status = "not_established"
        reason = (npv_kpi or {}).get("reason") if isinstance(npv_kpi, dict) else None
        reason = reason or "project NPV not established"
    elif npv >= 0.0:
        status, reason = "worth_building", "project NPV is 0 or more"
    else:
        status, reason = "not_worth_building", "project NPV is below 0"
    be = (report or {}).get("break_even")
    basis = be.get("basis") if isinstance(be, dict) else None
    return {
        "status": status,
        "reason": reason,
        "basis": basis or "project post-tax NPV",
        "figures": {
            name: {k: kpis[name].get(k) for k in ("value", "status", "reason", "unit")}
            for name in FIGURES
            if isinstance(kpis.get(name), dict)
        },
        "could_flip_it": _flip(be),
        # A verdict that rests on pack defaults shows them (B5; #1172).
        # A report stored before the list existed: not established, never "none".
        "rests_on_defaults": sorted(
            {
                str(a.get("line"))
                for a in (report or {})["assumed"]
                if isinstance(a, dict)
            }
        )
        if isinstance((report or {}).get("assumed"), list)
        else None,
    }

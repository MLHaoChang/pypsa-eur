"""Grid-extension break-even distance (homer-parity 03c).

The line length at which extending the grid costs the same as the off-grid system:
`grid_cost(km) = grid_supply_npc + fixed_eur + eur_per_km * km` against `offgrid_npc`,
both net present costs in the same money year and discount basis. The caller types
`eur_per_km`; nothing here assumes a cable cost.

The root is bracketed the way `business_case.break_even` brackets a price (km 0, 1, then
x1.5 up to a cap; brentq only between two trials of opposite sign, never an
extrapolation). `business_case.break_even` itself is tied to a revenue line's price
scale, so the constants are shared and the bracketing is repeated here.
"""

from __future__ import annotations

import math

from scipy.optimize import brentq

from services.finance.business_case import BREAK_EVEN_MAX_SCALE, BREAK_EVEN_XTOL

DEFAULT_MAX_KM = BREAK_EVEN_MAX_SCALE

BREAK_EVEN = "break_even"
GRID_CHEAPER_EVERYWHERE = "grid_cheaper_everywhere"
OFFGRID_CHEAPER_EVERYWHERE = "offgrid_cheaper_everywhere"
NOT_ESTABLISHED = "break_even_not_established"

_MESSAGES = {
    BREAK_EVEN: "Extending the grid costs the same as the off-grid system at this distance; "
    "beyond it the off-grid system is cheaper.",
    GRID_CHEAPER_EVERYWHERE: "No break-even: the grid is already as cheap as or cheaper than "
    "the off-grid system at 0 km, so it wins at every distance.",
    OFFGRID_CHEAPER_EVERYWHERE: "No break-even: the off-grid system is cheaper than extending "
    "the grid at every distance up to max_km.",
    NOT_ESTABLISHED: "Not computed: an input is missing, negative or not a finite number.",
}


def _num(v, *, allow_zero_default=None):
    if v is None:
        return allow_zero_default
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return math.nan
    return float(v)


def grid_extension_break_even(
    *,
    eur_per_km: float,
    offgrid_npc: float,
    grid_supply_npc: float = 0.0,
    fixed_eur: float = 0.0,
    max_km: float = DEFAULT_MAX_KM,
) -> dict:
    """The break-even distance in km, or the reason there is none.

    `status` is `break_even` (`km` set), `grid_cheaper_everywhere` (the "no break-even"
    case: the grid is not dearer than off-grid even at 0 km), `offgrid_cheaper_everywhere`
    (extension never catches up within `max_km`) or `break_even_not_established` (a bad
    input; `km` stays None, never 0). `grid_supply_npc` defaults to 0, which compares the
    extension cost alone against the off-grid NPC (`basis` says so)."""
    vals = {
        "eur_per_km": _num(eur_per_km),
        "offgrid_npc": _num(offgrid_npc),
        "grid_supply_npc": _num(grid_supply_npc, allow_zero_default=0.0),
        "fixed_eur": _num(fixed_eur, allow_zero_default=0.0),
        "max_km": _num(max_km),
    }
    out = {
        "status": NOT_ESTABLISHED,
        "km": None,
        "eur_per_km": vals["eur_per_km"],
        "basis": (
            "grid_extension_cost_only_vs_offgrid_npc"
            if vals["grid_supply_npc"] == 0.0
            else "grid_supply_npc_plus_extension_vs_offgrid_npc"
        ),
    }
    bad = [k for k, v in vals.items() if v is None or math.isnan(v)]
    bad += [
        k for k in ("eur_per_km", "fixed_eur", "max_km") if k not in bad and vals[k] < 0
    ]
    if vals["max_km"] == 0:
        bad.append("max_km")
    if bad:
        out["invalid"] = sorted(set(bad))
        out["message"] = _MESSAGES[NOT_ESTABLISHED]
        return out
    k, off, base, fixed, cap = (
        vals["eur_per_km"],
        vals["offgrid_npc"],
        vals["grid_supply_npc"],
        vals["fixed_eur"],
        vals["max_km"],
    )

    def gap(km: float) -> float:
        return base + fixed + k * km - off

    if gap(0.0) >= 0.0:
        out["status"] = GRID_CHEAPER_EVERYWHERE
    else:
        kms = [0.0, 1.0]
        while kms[-1] * 1.5 <= cap:
            kms.append(kms[-1] * 1.5)
        if kms[-1] < cap:
            kms.append(cap)
        root = None
        prev = kms[0]
        for km in kms[1:]:
            if gap(km) >= 0.0:
                root = (
                    km
                    if gap(km) == 0.0
                    else brentq(
                        gap, prev, km, xtol=BREAK_EVEN_XTOL, rtol=BREAK_EVEN_XTOL
                    )
                )
                break
            prev = km
        if root is None:
            out["status"] = OFFGRID_CHEAPER_EVERYWHERE
        else:
            out["status"], out["km"] = BREAK_EVEN, root
    out["message"] = _MESSAGES[out["status"]]
    return out

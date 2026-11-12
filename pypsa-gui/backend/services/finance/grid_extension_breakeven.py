"""Grid-extension break-even distance (homer-parity 03c).

The line length at which extending the grid costs the same as the off-grid system:
`grid_cost(km) = grid_supply_npc + fixed_eur + eur_per_km * km` against `offgrid_npc`,
all net present costs. `gap(km)` is linear, so the answer is the closed form
`km* = (offgrid_npc - grid_supply_npc - fixed_eur) / eur_per_km`; there is no solver.

Fail closed: `grid_supply_npc` has no default (the off-grid NPC holds a lifetime of fuel
and O&M, so comparing it with grid capex alone would overstate the distance), and the two
sides must declare the same currency and currency year; a mismatch refuses, never
converts. `business_case.break_even` is a revenue-price break-even and is not used.
"""

from __future__ import annotations

import math

BREAK_EVEN = "break_even"
OFFGRID_CHEAPER_EVERYWHERE = "offgrid_cheaper_everywhere"
GRID_CHEAPER_EVERYWHERE = "grid_cheaper_everywhere"
BEYOND_MAX_KM = "beyond_max_km"
NOT_ESTABLISHED = "break_even_not_established"

_MESSAGES = {
    BREAK_EVEN: "Extending the grid costs the same as the off-grid system at this "
    "distance; beyond it the off-grid system is cheaper, closer in the grid is.",
    OFFGRID_CHEAPER_EVERYWHERE: "No break-even: the grid supply already costs more than "
    "the off-grid system at 0 km, so off-grid is cheaper at every distance.",
    GRID_CHEAPER_EVERYWHERE: "No break-even: extension costs nothing per km and the grid "
    "is cheaper than the off-grid system at 0 km, so the grid wins at every distance.",
    BEYOND_MAX_KM: "The break-even distance is beyond max_km: the grid is cheaper at "
    "every distance up to max_km.",
    NOT_ESTABLISHED: "Not computed.",
}
_BASIS_KEYS = ("currency", "currency_year")


def _is_finite(v) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def grid_extension_break_even(
    *,
    eur_per_km: float,
    offgrid_npc: float,
    grid_supply_npc: float | None,
    offgrid_basis: dict,
    grid_basis: dict,
    fixed_eur: float = 0.0,
    max_km: float = 10_000.0,
) -> dict:
    """The break-even distance in km, or the reason there is none.

    `offgrid_basis` is the 03a row's basis (`currency`, `currency_year`, plus the discount
    rate and horizon, echoed); `grid_basis` declares the currency and year of
    `eur_per_km`, `fixed_eur` and `grid_supply_npc`. `status` is one of `break_even`
    (`km` set; km 0 counts), `offgrid_cheaper_everywhere`, `grid_cheaper_everywhere`,
    `beyond_max_km` (`km` still reported) or `break_even_not_established` (`km` None,
    `reason` and `invalid` say why; never 0)."""
    out = {"status": NOT_ESTABLISHED, "km": None, "offgrid_basis": offgrid_basis}

    def refuse(reason: str, invalid=()):
        out["reason"] = reason
        out["invalid"] = sorted(invalid)
        out["message"] = _MESSAGES[NOT_ESTABLISHED]
        return out

    if grid_supply_npc is None:
        return refuse("grid_supply_npc_required", ["grid_supply_npc"])
    vals = {
        "eur_per_km": eur_per_km,
        "offgrid_npc": offgrid_npc,
        "grid_supply_npc": grid_supply_npc,
        "fixed_eur": fixed_eur,
        "max_km": max_km,
    }
    bad = [k for k, v in vals.items() if not _is_finite(v)]
    if not bad:
        bad = [
            k
            for k, ok in (
                ("eur_per_km", eur_per_km >= 0),
                ("fixed_eur", fixed_eur >= 0),
                ("max_km", max_km > 0),
            )
            if not ok
        ]
    if bad:
        return refuse("input_not_finite_or_out_of_range", bad)
    if not (isinstance(offgrid_basis, dict) and isinstance(grid_basis, dict)) or any(
        offgrid_basis.get(k) is None or grid_basis.get(k) is None for k in _BASIS_KEYS
    ):
        return refuse("basis_missing", ["offgrid_basis", "grid_basis"])
    if any(offgrid_basis[k] != grid_basis[k] for k in _BASIS_KEYS):
        return refuse(
            "basis_mismatch",
            [k for k in _BASIS_KEYS if offgrid_basis[k] != grid_basis[k]],
        )

    gap0 = grid_supply_npc + fixed_eur - offgrid_npc  # grid cost at 0 km less off-grid
    if gap0 > 0.0:
        out["status"] = OFFGRID_CHEAPER_EVERYWHERE
    elif gap0 == 0.0:
        out["status"], out["km"] = BREAK_EVEN, 0.0
    elif eur_per_km == 0.0:
        out["status"] = GRID_CHEAPER_EVERYWHERE
    else:
        km = -gap0 / eur_per_km
        out["km"] = km
        out["status"] = BREAK_EVEN if km <= max_km else BEYOND_MAX_KM
    out["message"] = _MESSAGES[out["status"]]
    return out

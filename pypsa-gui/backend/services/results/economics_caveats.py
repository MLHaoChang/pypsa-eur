"""
Caveats the economic surfaces must show beside their numbers.

Each is ONE string with one home. The chat tool's reading notes, the
Economics tab and Asset Detail all import it from here, because two copies
of a caveat drift and the reader then sees two different truths about one
number (the lesson `RESERVE_MARGIN_CAVEAT` already taught the adequacy
surfaces).
"""
from __future__ import annotations

from typing import Any

from services.results.sizing import is_interior_optimum

# The zero-profit equilibrium. An extendable asset the LP stopped between its
# bounds earns ≈ zero net profit BY CONSTRUCTION, because the LP builds until
# the marginal MW breaks even. Without this sentence beside the number, a
# near-zero net profit reads as "no return" to a client and as a defect to
# the copilot.
ZERO_PROFIT_BY_CONSTRUCTION = (
    "Zero-profit equilibrium: an extendable asset at an interior "
    "optimum earns approximately zero net profit BY CONSTRUCTION — "
    "the LP builds until the marginal MW breaks even. A near-zero "
    "net_profit_eur here is the expected result, not a fault."
)

# Static table and capacity column per sizeable class, for the note below.
_TABLES: dict[str, tuple[str, str]] = {
    "Generator": ("generators", "p_nom"),
    "StorageUnit": ("storage_units", "p_nom"),
    "Store": ("stores", "e_nom"),
    "Link": ("links", "p_nom"),
}


def interior_optimum_notes(n: Any, names_by_class: dict[str, list[str]]) -> list[str]:
    """
    `[ZERO_PROFIT_BY_CONSTRUCTION]` when any named asset sits at an interior
    optimum, else `[]`. Classes outside the sizeable four are ignored, as is
    a name absent from its table.
    """
    for cls, names in names_by_class.items():
        spec = _TABLES.get(cls)
        if spec is None:
            continue
        df = getattr(n, spec[0], None)
        for name in names:
            if is_interior_optimum(df, name, spec[1]):
                return [ZERO_PROFIT_BY_CONSTRUCTION]
    return []

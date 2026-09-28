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

from services.dispatch_status import dispatch_status_detail
from services.results.sizing import classify_sizing

# The zero-profit equilibrium. An extendable asset the LP stopped between its
# bounds earns ≈ zero net profit BY CONSTRUCTION, because the LP builds until
# the marginal MW breaks even. Without this sentence beside the number, a
# near-zero net profit reads as "no return" to a client and as a defect to
# the copilot.
ZERO_PROFIT_BY_CONSTRUCTION = (
    "Zero-profit equilibrium: an extendable asset at an interior "
    "optimum earns approximately zero net profit BY CONSTRUCTION — "
    "the LP builds until the marginal MW breaks even. A near-zero "
    "net profit here is the expected result, not a fault."
)

# Static table and capacity column per sizeable class — the same six classes
# `explain_investment` classifies, so the chat tool and the surfaces agree
# about every asset, lines and transformers included.
_TABLES: dict[str, tuple[str, str]] = {
    "Generator": ("generators", "p_nom"),
    "StorageUnit": ("storage_units", "p_nom"),
    "Store": ("stores", "e_nom"),
    "Link": ("links", "p_nom"),
    "Line": ("lines", "s_nom"),
    "Transformer": ("transformers", "s_nom"),
}


def network_is_solved(n: Any) -> bool:
    """
    "Solved" the way `explain_investment` decides it: the dispatch tables are
    present AND fresh. A `p_nom_opt` column is NOT evidence — PyPSA carries
    it on every network, defaulting to 0, and it survives `clear_dispatch`
    after an edit, so reading it would put the note on an unsolved network
    or on a stale one the chat tool calls `not_solved`.
    """
    try:
        return dispatch_status_detail(n).get("state") == "fresh"
    except Exception:
        return False


def interior_optimum_notes(n: Any, names_by_class: dict[str, list[str]]) -> list[str]:
    """
    `[ZERO_PROFIT_BY_CONSTRUCTION]` when the network is solved and any named
    asset sits at an interior optimum, else `[]`. Classes outside the six
    sizeable ones are ignored, as is a name absent from its table. An
    unsolved or dispatch-cleared network never carries the note.
    """
    if not network_is_solved(n):
        return []
    for cls, names in names_by_class.items():
        spec = _TABLES.get(cls)
        if spec is None:
            continue
        df = getattr(n, spec[0], None)
        if df is None or not names:
            continue
        for name in names:
            if name not in df.index:
                continue
            sizing = classify_sizing(df.loc[name], spec[1], solved=True)
            if sizing["binding_constraint"] == "interior":
                return [ZERO_PROFIT_BY_CONSTRUCTION]
    return []

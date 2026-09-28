"""
The sizing classification: which constraint stopped the LP at this size.

One implementation, shared by the chat tool (`explain_investment`), the
Economics tab (`/results/asset_economics`) and Asset Detail, so the three
surfaces cannot disagree about whether an asset sits at an interior optimum,
on a bound, or was never sized at all. It moved here from `chat_tools` when
the zero-profit-by-construction caveat started reaching the user directly
rather than only the model.

No engine imports: a static row plus two bounds is all it reads.
"""
from __future__ import annotations

import math
from typing import Any

# One sentence per structural outcome — the LP fact, not advice.
BINDING_EXPLANATIONS: dict[str, str] = {
    "not_solved": (
        "the network has no fresh dispatch, so there is no sizing decision to "
        "explain — every capacity below is an input or a stale leftover"
    ),
    "not_extendable": (
        "the LP could not size this asset at all: its capacity is an INPUT, "
        "not a result. Set p_nom_extendable (or the class's equivalent) to "
        "let the optimisation choose it"
    ),
    "at_upper_bound": (
        "the LP took every MW the upper bound allowed. The BOUND set this "
        "size, not the economics — raise it to learn what the economics would "
        "build"
    ),
    "not_built": (
        "the LP chose to build none of it: at these costs it did not compete "
        "at the margin against everything else on the system. Nothing blocked "
        "it — it was simply not worth building"
    ),
    "at_lower_bound": (
        "the LP built the minimum it was FORCED to and no more. The asset was "
        "not competitive at the margin; a non-zero floor is holding it up, so "
        "this capacity is a constraint's doing, not the economics'"
    ),
    "interior": (
        "the LP stopped between the bounds, so this size IS the economic "
        "answer: the marginal MW broke even against everything else on the "
        "system"
    ),
}


def finite(value: Any) -> float | None:
    """float(value) or None for anything non-finite, missing or unparseable."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def is_at(value: float | None, bound: float | None) -> bool:
    """
    Is an optimised capacity sitting ON a bound?

    Relative, because an LP lands on a bound within solver tolerance and an
    exact `==` reports "interior" for a plainly saturated asset — the single
    wrong answer this whole classification exists to avoid.
    """
    if value is None or bound is None:
        return False
    return abs(value - bound) <= max(abs(bound), abs(value), 1.0) * 1e-6


def classify_sizing(row: Any, nom_col: str, *, solved: bool) -> dict:
    """
    Classify the sizing decision from the asset's static row.

    `row` needs `.get` — a dict or a pandas Series both serve. `nom_col` is
    the class's capacity column (`p_nom`, `e_nom`, `s_nom`), and the `_opt`,
    `_min`, `_max` and `_extendable` siblings are read off it.
    """
    existing = finite(row.get(nom_col))
    optimised = finite(row.get(f"{nom_col}_opt")) if solved else None
    lower = finite(row.get(f"{nom_col}_min"))
    upper = finite(row.get(f"{nom_col}_max"))   # None == unbounded (inf)
    extendable = bool(row.get(f"{nom_col}_extendable", False))

    if not solved:
        binding = "not_solved"
    elif not extendable:
        binding = "not_extendable"
    elif is_at(optimised, upper):
        binding = "at_upper_bound"
    elif is_at(optimised, lower):
        # A floor of zero is not a floor. Reporting "the minimum it was forced
        # to" for an asset nobody forced anywhere reads as if a constraint
        # explained the zero, when the honest answer is that it lost on cost.
        binding = "at_lower_bound" if (lower or 0.0) > 0 else "not_built"
    else:
        binding = "interior"

    added = None if (optimised is None or existing is None) else optimised - existing
    headroom = None if (optimised is None or upper is None) else upper - optimised
    return {
        "capacity_column": nom_col,
        "extendable": extendable,
        "existing": existing,
        "optimised": optimised,
        "added": added,
        "lower_bound": lower,
        "upper_bound": upper,          # null = unbounded (p_nom_max = inf)
        "headroom": headroom,
        "binding_constraint": binding,
        "explanation": BINDING_EXPLANATIONS[binding],
    }


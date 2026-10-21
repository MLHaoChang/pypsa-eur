"""
Revenue by stream per year (IPP renewables + BESS ticket 05, spec §5, G6): the owner's
operating cash lines (`Operating.lines`) grouped into the rows a developer reads, plus the
replacement capex below them.

A difference line of a PPA (a financial PPA, ticket 01: (price − ref) × share × generation) is
shown as its two legs, which is what the developer sells: its price leg (price × share ×
generation) is the PPA row and its reference leg (−ref × share × generation) joins the market
row, so "market" is ref × (export − share × generation). A CfD's difference line stays whole
in its own row (top-up or payback). The legs are the cash's own (`cashflow._two_legs`), so
nothing is recomputed: every year, the rows sum to the operating cash (revenue − costs).

A line whose stream has no row is shown in "Other" and named in `other_keys`, never hidden.
Pure: no I/O, no network.
"""
from __future__ import annotations

from typing import Any

import numpy as np

#: The rows, in display order, with the labels the results view shows (UX, 2026-10-21).
ROWS: tuple[str, ...] = ("ppa", "merchant", "cfd", "grid_imports", "om", "other")
LABELS: dict[str, str] = {
    "ppa": "PPA sales (PPA price × contracted share × generation)",
    "merchant": "Market sales (market price × the rest)",
    "cfd": "CfD top-up or payback",
    "grid_imports": "Grid imports",
    "om": "Operation & maintenance",
    "other": "Other",
    "total": "Operating cash (revenue less operating costs)",
    "replacements": "Replacements",
}
#: The row of each ledger stream (`cashflow.STREAM_CLASS`'s streams); any other is "other".
ROW_OF_STREAM: dict[str, str] = {
    "ppa_settlement": "ppa",
    "energy_export": "merchant",
    "cfd_settlement": "cfd",
    "energy_import": "grid_imports", "energy_wholesale": "grid_imports",
    "network_capacity": "grid_imports", "network_energy": "grid_imports",
    "demand_charge": "grid_imports", "retail_fixed": "grid_imports",
    "certificates": "grid_imports", "tax": "grid_imports",
    "fom": "om", "vom": "om",
}


def _values(arr: np.ndarray) -> list[float | None]:
    return [None if not np.isfinite(v) else float(v) for v in arr]


def revenue_by_stream(op) -> dict[str, Any] | None:
    """The rows per axis year from an `Operating` (`cashflow.build_operating`), or None when
    its operating cash is not established (the reasons are the project section's)."""
    if (op.status or {}).get("operating") != "ok" or op.revenue is None or op.costs is None:
        return None
    n = op.tl.n
    rows = {r: np.zeros(n) for r in ROWS}
    other_keys: list[str] = []
    for key, arr in sorted(op.lines.items()):
        ln = op.line_meta.get(key)
        if arr is None or ln is None:
            continue
        row = ROW_OF_STREAM.get(ln.stream, "other")
        if row == "ppa" and key in op.legs:
            price_leg, reference_leg = op.legs[key]
            rows["ppa"] += price_leg
            rows["merchant"] += reference_leg
            continue
        if row == "other" and np.any(arr != 0.0):
            other_keys.append(key)
        rows[row] += arr
    total = op.revenue - op.costs
    return {
        "years": [int(y) for y in op.tl.years],
        "rows": [{"id": r, "label": LABELS[r], "values": _values(rows[r])} for r in ROWS],
        "total": {"id": "total", "label": LABELS["total"], "values": _values(total)},
        "replacements": {"id": "replacements", "label": LABELS["replacements"],
                         "values": _values(-np.asarray(op.replacement, dtype=float))},
        "other_keys": other_keys,
    }

"""
The optimal system type map (homer-parity 03b, owner decision HOMER-GAPS-1):
over two sensitivity variables, which supply architecture wins in each cell.

Pure part, built on the 03a comparison (`supply_architectures.compare`):

* **Variables.** A small catalogue (:data:`VARIABLES`): a plain-worded
  multiplier on one field of one group's assets (the genset fuel price, the
  solar resource, the PV capital cost, ...). A cell is a copy of the base
  network with both multipliers applied (:func:`apply_variable`); a variable
  that would change nothing in this network (no such asset, or a zero base
  value) is refused, never a silent no-op (:class:`MapRefused`).
* **Meaning of a multiplier.** It multiplies the project's own value, so a
  zero value stays zero (and a variable with only zero values is refused) and
  a negative one becomes more negative (a grid price of -20 at x1.5 is -30:
  a feed-in revenue grows). A resource multiplier (solar, wind) is clipped at
  1.0 of nameplate, never above the base's own value: the share of the
  resource energy the clip removed is recorded per cell (``clipped``). A
  capital cost counts only on assets the optimiser sizes (extendable): on a
  fixed asset it changes no optimum. Assets come from the 03a groups
  (`SA.classify`), which leave out every shortfall slack, so a fuel price
  never touches the value of lost load.
* **Cells.** Every pair of the two axes' values, solved by the 03a comparison
  (the other assets omitted, each architecture sized by the LP). A cell's
  winner is the cheapest solved architecture; its gap is the runner-up's cost
  minus the winner's. A cell where nothing solved has no winner and no gap,
  and every architecture keeps its status in it (never dropped, ADR-0001).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from services.study import supply_architectures as SA

__all__ = [
    "MAX_VALUES",
    "MIN_VALUES",
    "VARIABLES",
    "MapRefused",
    "Applied",
    "apply_variable",
    "build_map",
    "cell_of",
    "check_axes",
    "solves_refusal",
]

#: Values per axis (a 3 x 3 grid is the default shape).
MIN_VALUES = 2
MAX_VALUES = 5
#: A multiplier is above 0 and at most this (a tenfold change of the base).
MAX_FACTOR = 10.0
DEFAULT_VALUES = (0.5, 1.0, 1.5)


@dataclass(frozen=True)
class Variable:
    variable_id: str
    label: str
    #: A 03a group, or None for every Load.
    group: str | None
    component_field: str
    #: Clip the scaled values at 1.0 (a share of nameplate).
    clipped: bool = False
    #: Count only assets the optimiser sizes.
    extendable_only: bool = False


VARIABLES: dict[str, Variable] = {
    v.variable_id: v
    for v in (
        Variable("genset_fuel_price", "Genset fuel price", "genset", "marginal_cost"),
        Variable("grid_energy_price", "Grid energy price", "grid", "marginal_cost"),
        Variable("solar_resource", "Solar resource", "pv", "p_max_pu", clipped=True),
        Variable("wind_resource", "Wind resource", "wind", "p_max_pu", clipped=True),
        Variable("load", "Load", None, "p_set"),
        Variable(
            "pv_capital_cost",
            "PV capital cost",
            "pv",
            "capital_cost",
            extendable_only=True,
        ),
        Variable(
            "battery_capital_cost",
            "Battery capital cost",
            "battery",
            "capital_cost",
            extendable_only=True,
        ),
        Variable(
            "genset_capital_cost",
            "Genset capital cost",
            "genset",
            "capital_cost",
            extendable_only=True,
        ),
    )
}


class MapRefused(ValueError):
    """The request names something the map cannot compute; ``code`` is the
    error kind and ``detail`` the plain-words message."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def check_axes(
    x: dict, y: dict
) -> tuple[tuple[Variable, list[float]], tuple[Variable, list[float]]]:
    """Validate the two axes ``{"variable": id, "values": [multiplier, ...]}``."""
    out = []
    for axis in (x, y):
        var = VARIABLES.get(str(axis.get("variable")))
        if var is None:
            raise MapRefused(
                "unknown_variable",
                "unknown variable; the choices are "
                + ", ".join(v.label for v in VARIABLES.values()),
            )
        values = axis.get("values")
        if values is None:
            values = list(DEFAULT_VALUES)
        ok = (
            isinstance(values, (list, tuple))
            and MIN_VALUES <= len(values) <= MAX_VALUES
            and all(
                isinstance(v, (int, float))
                and not isinstance(v, bool)
                and math.isfinite(v)
                and 0 < v <= MAX_FACTOR
                for v in values
            )
            and len(set(values)) == len(values)
        )
        if not ok:
            raise MapRefused(
                "axis_values_invalid",
                f"{var.label}: give {MIN_VALUES} to {MAX_VALUES} different multipliers "
                f"above 0 and at most {MAX_FACTOR:g}",
            )
        out.append((var, [float(v) for v in values]))
    if out[0][0].variable_id == out[1][0].variable_id:
        raise MapRefused("axes_not_different", "the two axes need different variables")
    return out[0], out[1]


def _members(n, var: Variable, groups: dict) -> list[tuple[str, str]]:
    if var.group is None:
        return [("Load", str(name)) for name in SA._static(n, "Load").index]
    return list(groups.get(var.group, ()))


@dataclass(frozen=True)
class Applied:
    #: How many non-zero values were scaled (static and time series).
    scaled: int
    #: The share of the (unclipped) resource energy a clip at 1.0 removed;
    #: None for a variable that is not clipped.
    clipped_share: float | None = None


_EXTENDABLE = {
    "Generator": "p_nom_extendable",
    "Link": "p_nom_extendable",
    "StorageUnit": "p_nom_extendable",
    "Store": "e_nom_extendable",
}


def _weights(n):
    return n.snapshot_weightings["objective"]


def apply_variable(
    n, var: Variable, factor: float, groups: dict | None = None
) -> Applied:
    """Multiply the variable's field on its assets of ``n`` in place (static
    values and time series; a resource clipped at 1.0, see the module
    docstring). ``n`` must be a private copy."""
    pass
    scaled = 0
    kept = lost = 0.0
    for component, name in _members(n, var, groups):
        comp = n.components[component]
        static = comp.static
        if var.extendable_only:
            flag = _EXTENDABLE.get(component)
            if flag not in static.columns or not bool(static.at[name, flag]):
                continue
        series = comp.dynamic.get(var.component_field)
        dynamic = series is not None and name in series.columns
        if var.component_field in static.columns and name in static.index:
            old = static.at[name, var.component_field]
            if old == old and old != 0:
                new = old * factor
                if var.clipped:
                    new = min(new, max(old, 1.0))
                    if not dynamic:
                        w = float(_weights(n).sum())
                        kept += w * new
                        lost += w * (old * factor - new)
                static.at[name, var.component_field] = new
                scaled += 1
        if dynamic and bool((series[name].fillna(0) != 0).any()):
            old = series[name]
            new = old * factor
            if var.clipped:
                new = new.clip(upper=old.clip(lower=1.0))
                w = _weights(n).reindex(old.index).fillna(0.0)
                kept += float((w * new).sum())
                lost += float((w * (old * factor - new)).sum())
            series[name] = new
            scaled += 1
    share = None
    if var.clipped:
        total = kept + lost
        share = lost / total if total > 0 else 0.0
    return Applied(scaled, share)


def solves_refusal(cells: int, architectures: int, limit: int) -> str | None:
    """The refusal text when ``cells`` x ``architectures`` solves exceed the
    campaign ``limit``, naming the count; None when it fits (the limit itself
    is allowed)."""
    solves = cells * architectures
    if solves <= limit:
        return None
    return (
        f"{cells} cells x {architectures} architectures is {solves} solves; "
        f"the most one campaign allows is {limit}: use fewer values or name "
        "fewer architectures"
    )


def cell_of(i: int, j: int, table: dict, clipped: list[dict] | None = None) -> dict:
    """One cell of the map from its 03a comparison ``table``."""
    rows = [
        {
            "architecture_id": r["architecture_id"],
            "label": r["label"],
            "status": r["status"],
            "total_cost_eur_per_year": r["total_cost_eur_per_year"],
        }
        for r in table["rows"]
    ]
    winner_id = table["best_architecture_id"]
    solved = [
        r
        for r in rows
        if r["status"] == "ok" and r["total_cost_eur_per_year"] is not None
    ]
    winner = next((r for r in rows if r["architecture_id"] == winner_id), None)
    gap = None
    if winner is not None and len(solved) >= 2:
        # `rows` are ranked: the first two solved rows are winner and runner-up.
        gap = (
            solved[1]["total_cost_eur_per_year"] - solved[0]["total_cost_eur_per_year"]
        )
    return {
        "i": i,
        "j": j,
        "winner_architecture_id": winner_id,
        "winner_label": winner["label"] if winner else None,
        "runner_up_gap_eur_per_year": gap,
        # Every architecture solved (a winner is then the best of all of them).
        "all_solved": bool(rows) and all(r["status"] == "ok" for r in rows),
        "clipped": clipped or [],
        "rows": rows,
    }


def build_map(
    n,
    x: tuple[Variable, list[float]],
    y: tuple[Variable, list[float]],
    solve: SA.SolveFn,
    *,
    archs: list[SA.SupplyArchitecture],
    progress: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """The cells of the map: ``x`` runs along ``i``, ``y`` along ``j``.
    Raises :class:`MapRefused` when a variable changes nothing in ``n``."""
    groups = SA.classify(n)
    for var, values in (x, y):
        if not apply_variable(n.copy(), var, values[0], groups).scaled:
            raise MapRefused(
                "variable_not_in_network",
                f"{var.label} changes nothing in this project: it has no such "
                "asset (or none the optimiser sizes) or its value is zero",
            )
    cells = []
    total = len(x[1]) * len(y[1])
    for i, fx in enumerate(x[1]):
        for j, fy in enumerate(y[1]):
            if progress is not None:
                progress(current=f"cell {i * len(y[1]) + j + 1} of {total}")
            m = n.copy()
            clipped = []
            for var, factor in ((x[0], fx), (y[0], fy)):
                share = apply_variable(m, var, factor, groups).clipped_share
                if share:
                    clipped.append(
                        {
                            "variable": var.variable_id,
                            "label": var.label,
                            "share": share,
                        }
                    )
            cells.append(cell_of(i, j, SA.compare(m, solve, archs=archs), clipped))
    return {
        "axes": [
            {"variable": v.variable_id, "label": v.label, "values": vals}
            for v, vals in (x, y)
        ],
        "cells": cells,
    }

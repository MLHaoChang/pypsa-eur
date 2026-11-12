"""
The best system per architecture (homer-parity 03a, owner decision
HOMER-GAPS-1): HOMER Pro's "categorized results", one row per architecture.

* **Groups.** A network's assets fall into the groups of :data:`GROUP_ORDER`
  by component and carrier (:func:`classify`): PV, wind, battery, genset and
  the grid supply (its generator and its import/export links). An asset in no
  group (the load, a bus, a transformer) is in every architecture.
* **Architectures** are the combinations of the groups present that hold a
  source of energy (:func:`architectures_for`); a battery alone holds none.
* **Omitted, not zeroed.** An architecture's network is a copy with every
  asset of the other groups REMOVED (:func:`omit`), as the study's ``none``
  option omits its battery (review v2 BC-1): a size fixed at zero fails
  preflight and is not a "without" case.
* **Best system.** The LP sizes the extendable assets left, so each solve IS
  that architecture's best system. :func:`compare` solves each one through an
  injected ``solve`` and keeps every architecture: one that did not solve is
  listed with its status (``infeasible``, ``failed``, ``not_run``) and null
  figures flagged in ``unavailable``, never dropped (ADR-0001).
* **Renewable fraction** is computed here from served energy
  (:func:`renewable_fraction`). homer-parity 01a builds a ``renewable_fraction``
  KPI; that KPI and this column must share one definition, so 01a reuses or
  replaces this function rather than adding a second one.
"""
from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

__all__ = [
    "GROUP_ORDER", "MAX_ARCHITECTURES", "Architecture", "ArchitectureUnsolved",
    "architectures_for", "classify", "compare", "omit", "renewable_fraction",
    "row_of",
]

# Canonical order: an architecture's id joins its groups in this order.
GROUP_ORDER = ("pv", "wind", "battery", "genset", "grid")
SOURCES = frozenset({"pv", "wind", "genset", "grid"})
RENEWABLE = frozenset({"pv", "wind"})
NON_RENEWABLE = ("genset", "grid")
LABELS = {"pv": "PV", "wind": "Wind", "battery": "Battery", "genset": "Genset",
          "grid": "Grid"}
# One solve each; more than this needs an explicit selection.
MAX_ARCHITECTURES = 16

_PV = ("solar",)
_WIND = ("onwind", "offwind", "wind")
_GENSET = frozenset({"diesel", "genset", "gas", "oil", "ocgt", "ccgt", "hfo", "lpg",
                     "biodiesel", "chp"})
_GRID = frozenset({"grid", "grid_import", "grid_export", "grid_supply"})
_GRID_ROLES = frozenset({"grid_supply", "grid_import", "grid_export"})

# Components a group can hold, in the order `classify` lists them.
_COMPONENTS = ("Generator", "StorageUnit", "Store", "Link")


class ArchitectureUnsolved(RuntimeError):
    """An architecture's solve did not give a best system (``code``: infeasible,
    failed), or the network has no architecture at all (``no_architectures``,
    ``too_many_architectures``, ``unknown_architecture``)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class Architecture:
    architecture_id: str
    groups: tuple[str, ...]

    @property
    def label(self) -> str:
        return " + ".join(LABELS[g] for g in self.groups)


def _carrier(df: pd.DataFrame, name: str) -> str:
    return str(df.at[name, "carrier"] if "carrier" in df.columns else "").strip().lower()


def _role(df: pd.DataFrame, name: str) -> str:
    return str(df.at[name, "eh_role"]).strip().lower() if "eh_role" in df.columns else ""


def _group_of(component: str, carrier: str, role: str) -> str | None:
    if role in _GRID_ROLES or carrier in _GRID:
        return "grid" if component in ("Generator", "Link") else None
    if "battery" in carrier:
        return "battery"
    if component != "Generator":
        return None
    if carrier.startswith(_PV):
        return "pv"
    if carrier.startswith(_WIND):
        return "wind"
    if carrier in _GENSET:
        return "genset"
    return None


def classify(n) -> dict[str, list[tuple[str, str]]]:
    """``group -> [(component, name), ...]`` for the groups present."""
    out: dict[str, list[tuple[str, str]]] = {}
    for component in _COMPONENTS:
        df = n.components[component].static
        for name in df.index:
            group = _group_of(component, _carrier(df, name), _role(df, name))
            if group is not None:
                out.setdefault(group, []).append((component, str(name)))
    return {g: out[g] for g in GROUP_ORDER if g in out}


def architectures_for(groups: Mapping[str, Any]) -> list[Architecture]:
    """Every combination of the present groups that holds a source of energy,
    fewest groups first, each in :data:`GROUP_ORDER`."""
    present = [g for g in GROUP_ORDER if g in groups]
    out = []
    for size in range(1, len(present) + 1):
        for combo in itertools.combinations(present, size):
            if SOURCES.intersection(combo):
                out.append(Architecture("+".join(combo), combo))
    return out


def omit(n, arch: Architecture, groups: Mapping[str, list[tuple[str, str]]] | None = None):
    """A copy of ``n`` without the assets of every group ``arch`` leaves out."""
    groups = classify(n) if groups is None else groups
    m = n.copy()
    for group, members in groups.items():
        if group in arch.groups:
            continue
        for component, name in members:
            m.remove(component, name)
    return m


def _weights(n) -> pd.Series:
    w = n.snapshot_weightings
    return w["generators"] if "generators" in w.columns else w.iloc[:, 0]


def _energy(df: pd.DataFrame, names: Iterable[str], w: pd.Series) -> float:
    cols = [c for c in names if c in df.columns]
    if not cols:
        return 0.0
    return float(df[cols].clip(lower=0.0).mul(w, axis=0).to_numpy().sum())


def _served(n, w: pd.Series) -> float | None:
    if n.loads.empty:
        return None
    p = n.loads_t.p
    if p.empty or not set(n.loads.index).issubset(p.columns):
        p = n.get_switchable_as_dense("Load", "p_set")
    return float(p[list(n.loads.index)].mul(w, axis=0).to_numpy().sum())


def renewable_fraction(n) -> float | None:
    """
    The share of served load not met by a genset or the grid, HOMER's
    definition: ``1 - (genset energy + grid import) / served load``, clipped
    to [0, 1] (exported surplus does not lift it above 1). Grid energy counts
    as non-renewable. None when the network has no dispatch or no load.

    The ONE definition (homer-parity 01a's ``renewable_fraction`` KPI reuses
    it): served energy, snapshot-weighted, generator output clipped at zero.
    """
    if n.generators_t.p.empty:
        return None
    w = _weights(n)
    served = _served(n, w)
    if not served or not math.isfinite(served) or served <= 0:
        return None
    groups = classify(n)
    non_ren = sum(_energy(n.generators_t.p,
                          [name for comp, name in groups.get(g, ()) if comp == "Generator"], w)
                  for g in NON_RENEWABLE)
    return min(1.0, max(0.0, 1.0 - non_ren / served))


def _num(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _sizes(n, members: list[tuple[str, str]]) -> tuple[list[dict], float | None, str | None]:
    """The solved sizes of an architecture's assets and their capex (overnight
    cost x optimal size of each extendable asset), or why capex is unknown."""
    sizes: list[dict] = []
    capex = 0.0
    missing: str | None = None
    for component, name in members:
        df = n.components[component].static
        attr = "e_nom" if component == "Store" else "p_nom"
        extendable = bool(df.at[name, f"{attr}_extendable"])
        size = _num(df.at[name, f"{attr}_opt"]) if extendable else _num(df.at[name, attr])
        sizes.append({"asset": name, "component": component,
                      f"{attr}_opt": size, "extendable": extendable})
        if not extendable or not size:
            continue
        cost = _num(df.at[name, "overnight_cost"]) if "overnight_cost" in df.columns else None
        if cost is None or cost <= 0:
            missing = missing or f"no_overnight_cost:{name}"
        else:
            capex += cost * size
    return sizes, (None if missing else capex), missing


def _members(n, arch: Architecture) -> list[tuple[str, str]]:
    groups = classify(n)
    return [m for g in arch.groups for m in groups.get(g, ())]


def row_of(arch: Architecture, n) -> dict:
    """The solved best system of ``arch`` (``n`` is its solved network)."""
    unavailable: dict[str, str] = {}
    objective = _num(getattr(n, "objective", None))
    const = _num(getattr(n, "objective_constant", 0.0)) or 0.0
    cost = None if objective is None else objective + const
    if cost is None:
        unavailable["total_cost_eur_per_year"] = "no_objective"
    sizes, capex, why = _sizes(n, _members(n, arch))
    if why:
        unavailable["capex_eur"] = why
    rf = renewable_fraction(n)
    if rf is None:
        unavailable["renewable_fraction"] = "no_dispatch"
    return {"architecture_id": arch.architecture_id, "label": arch.label,
            "groups": list(arch.groups), "status": "ok",
            "total_cost_eur_per_year": cost, "capex_eur": capex,
            "renewable_fraction": rf, "sizes": sizes, "unavailable": unavailable}


_FIGURES = ("total_cost_eur_per_year", "capex_eur", "renewable_fraction")


def unsolved_row(arch: Architecture, status: str, error: str | None = None) -> dict:
    """The row of an architecture with no best system (listed, never dropped)."""
    row = {"architecture_id": arch.architecture_id, "label": arch.label,
           "groups": list(arch.groups), "status": status, "sizes": [],
           "unavailable": {k: status for k in _FIGURES}}
    row.update({k: None for k in _FIGURES})
    if error:
        row["error"] = error
    return row


def ranked(rows: list[dict]) -> list[dict]:
    """Solved rows by total cost (cheapest first), then every unsolved one in
    architecture order."""
    def key(item):
        i, r = item
        cost = r.get("total_cost_eur_per_year")
        return (0, cost, i) if r["status"] == "ok" and cost is not None else (1, 0.0, i)
    return [r for _, r in sorted(enumerate(rows), key=key)]


def best_of(rows: list[dict]) -> str | None:
    solved = [r for r in rows if r["status"] == "ok" and r["total_cost_eur_per_year"] is not None]
    return min(solved, key=lambda r: r["total_cost_eur_per_year"])["architecture_id"] if solved else None


def select(n, architecture_ids: list[str] | None = None) -> list[Architecture]:
    """The architectures to solve: all of them, or the named ones."""
    archs = architectures_for(classify(n))
    if not archs:
        raise ArchitectureUnsolved(
            "no_architectures",
            "the network has no PV, wind, genset or grid supply to build an architecture from")
    if architecture_ids:
        known = {a.architecture_id: a for a in archs}
        unknown = [a for a in architecture_ids if a not in known]
        if unknown:
            raise ArchitectureUnsolved(
                "unknown_architecture",
                f"not an architecture of this network: {', '.join(unknown)}; "
                f"choose from {', '.join(known)}")
        archs = [known[a] for a in dict.fromkeys(architecture_ids)]
    if len(archs) > MAX_ARCHITECTURES:
        raise ArchitectureUnsolved(
            "too_many_architectures",
            f"{len(archs)} architectures exceed {MAX_ARCHITECTURES}; name the ones to compare")
    return archs


SolveFn = Callable[[Any, Architecture], Any]


def compare(n, solve: SolveFn, architecture_ids: list[str] | None = None) -> dict:
    """
    One row per architecture: ``solve(network, arch)`` returns the solved
    network, None when it was not reached (stopped, no budget), or raises
    :class:`ArchitectureUnsolved` (``infeasible`` / ``failed``).
    """
    archs = select(n, architecture_ids)
    groups = classify(n)
    rows = []
    for arch in archs:
        try:
            solved = solve(omit(n, arch, groups), arch)
        except ArchitectureUnsolved as exc:
            rows.append(unsolved_row(arch, exc.code, str(exc)))
            continue
        rows.append(unsolved_row(arch, "not_run") if solved is None else row_of(arch, solved))
    rows = ranked(rows)
    return {"rows": rows, "best_architecture_id": best_of(rows),
            "renewable_fraction_basis": "served_energy_genset_and_grid_non_renewable"}

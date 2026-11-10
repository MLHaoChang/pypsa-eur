"""Island survival of the plan as built (study harness 12; GK A Q1 (a)).

``survival(n, island)`` solves a private copy of the solved network with
every extendable asset fixed at its solved size and the island rows of the
project's own solve (``services.solver.island.add_island_rows``) added as a
check, per investment period. Nothing is sized, so the answer is about the
plan the project has: it holds the sidecar's N+k ride-through over
``sustained_h`` and its islanding step, or it does not.

Four outcomes, never folded into one another:

* ``survives``: the LP with the rows is optimal and no event row is short;
* ``fails``: a row the fixed sizes cannot hold (an ``IslandRefusal``, an
  infeasible LP, or an event row short of its step), each named;
* ``not_evaluated``: the solver ended any other way (an error, a time limit);
* ``not_solved``: the network has no solved sizes to fix.

The caller's network is never written: the copy is the only thing solved.
"""

from __future__ import annotations

import math

import pandas as pd

from services.adequacy.sweep import _CAPACITY_ATTRS

SURVIVES, FAILS, NOT_EVALUATED, NOT_SOLVED = "survives", "fails", "not_evaluated", "not_solved"
#: The solver conditions that say the rows cannot be held by the fixed sizes.
_INFEASIBLE = ("infeasible", "infeasible_or_unbounded")


class NotSolved(ValueError):
    """An extendable asset has no solved size, so there is no plan to fix."""


def fix_design(n) -> list[str]:
    """Fix every extendable asset of ``n`` at its solved size, in place:
    ``*_nom = *_nom_opt`` and ``*_nom_extendable = False`` for generators,
    storage units, stores, links, lines and transformers. Returns the
    ``"<table>/<name>"`` of each asset fixed. ``NotSolved`` when a solved
    size is missing or not a number (never fixed at 0 instead)."""
    fixed = []
    for table, nom in _CAPACITY_ATTRS:
        df = getattr(n, table)
        ext = f"{nom}_extendable"
        if df.empty or ext not in df.columns:
            continue
        for name in df.index[df[ext].astype(bool)]:
            opt = df.at[name, f"{nom}_opt"] if f"{nom}_opt" in df.columns else None
            if opt is None or pd.isna(opt) or not math.isfinite(float(opt)):
                raise NotSolved(f"{table}/{name} is extendable and has no solved size")
            df.at[name, nom] = float(opt)
            df.at[name, ext] = False
            fixed.append(f"{table}/{name}")
    return fixed


def extendable(n) -> list[str]:
    """``"<table>/<name>"`` of every asset still extendable."""
    out = []
    for table, nom in _CAPACITY_ATTRS:
        df = getattr(n, table)
        ext = f"{nom}_extendable"
        if not df.empty and ext in df.columns:
            out += [f"{table}/{name}" for name in df.index[df[ext].astype(bool)]]
    return out


def _solved(n) -> bool:
    objective = getattr(n, "objective", None)
    try:
        return objective is not None and math.isfinite(float(objective))
    except (TypeError, ValueError):
        return False


def _solve(n, extra_functionality, **kw):
    if isinstance(n.snapshots, pd.MultiIndex):
        kw["multi_investment_periods"] = True
    return n.optimize(solver_name="highs", extra_functionality=extra_functionality, **kw)


def _facts(facts) -> dict:
    facts = facts or {}
    return {"periods": {str(p): {"window_snapshots": v.get("window_snapshots"),
                                 "critical_peak_mw": v.get("critical_peak_mw")}
                        for p, v in (facts.get("periods") or {}).items()},
            "critical_loads": list(facts.get("critical_loads") or []),
            "seamless_bridge": facts.get("seamless_bridge"),
            "fuel": dict(facts.get("fuel") or {}),
            "missing": dict(facts.get("missing") or {})}


def survival(n, island, *, solve=None) -> dict:
    """The outcome for the solved network ``n`` and its loaded island sidecar
    ``island``: ``{state, reason, shortfalls, binding, fixed, sustained_h,
    scenarios, periods, critical_loads, seamless_bridge, fuel, missing}``.
    ``shortfalls`` are ``{row, sentence}``; ``reason`` is words, None when
    the island survives. ``solve(n, extra_functionality, **kw)`` returns
    ``(status, condition)``; HiGHS by default."""
    from services.adequacy.energization import apply_energization
    from services.solver import island as rows

    req = island["requirements"]
    out = {"state": NOT_SOLVED, "reason": None, "shortfalls": [], "binding": [], "fixed": [],
           "sustained_h": float(req["values"]["sustained_h"]), "scenarios": list(req["scenarios"]),
           **_facts(None)}
    if not _solved(n):
        return {**out, "reason": "the network has no solved plan"}
    from services.study.findings import copy_network

    c = copy_network(n)
    if isinstance(n.snapshots, pd.MultiIndex):
        c.snapshots.name = "snapshot"   # PyPSA's copy() drops the MultiIndex name
    try:
        out["fixed"] = fix_design(c)
    except NotSolved as exc:
        return {**out, "reason": str(exc)}
    # A fixed-size run: the linearised unit commitment still commits (#448 §3.1).
    setattr(c, rows.FIXED_DESIGN_ATTR, True)
    apply_energization(c)
    try:
        with rows.linearised_uc(c, island) as kw:
            status, condition = (solve or _solve)(
                c, rows.island_extra_functionality(island, check=True), **kw)
    except rows.IslandRefusal as exc:
        return {**out, "state": FAILS, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 — a solver that broke is no verdict, in words
        return {**out, "state": NOT_EVALUATED,
                "reason": f"the island check's solve stopped with an error ({type(exc).__name__})"}
    facts = getattr(c, "_island_facts", None)
    out.update(_facts(facts))
    short = [{"row": sh["row"], "sentence": rows.shortfall_sentence(sh)}
             for sh in rows.island_shortfalls(facts)]
    if status == "ok" and not short:
        if (facts or {}).get("skipped") or not facts:
            return {**out, "state": NOT_EVALUATED, "reason": "the island rows were not added to the solve"}
        return {**out, "state": SURVIVES, "binding": rows.binding_rows(c)}
    if short or str(condition) in _INFEASIBLE:
        return {**out, "state": FAILS, "shortfalls": short,
                "reason": ("; ".join(f"{s['row']}: {s['sentence']}" for s in short) if short else
                           "the plan as built cannot hold the island rows (the solve with them is infeasible)")}
    return {**out, "state": NOT_EVALUATED,
            "reason": f"the island check's solve ended {str(condition).replace('_', ' ')}, not optimal"}


# ── the input rules (GK A I8, I9) ──────────────────────────────────────────


def _ties(n) -> list[tuple[str, str]]:
    """(component, name) of every branch between the mainland and the island:
    a Link, Line or Transformer with exactly one end on a PoC bus (``eh_poc``),
    and every Link tagged as the grid import or export."""
    from services.adequacy.archetypes import _poc_buses
    from services.solver.island import _pcc_links

    poc = _poc_buses(n)
    out = []
    for comp, table in (("Link", "links"), ("Line", "lines"), ("Transformer", "transformers")):
        df = getattr(n, table)
        for name in df.index:
            ends = {str(df.at[name, "bus0"]), str(df.at[name, "bus1"])}
            if len(ends & poc) == 1 and len(ends) == 2:
                out.append((comp, str(name)))
    pcc = _pcc_links(n)
    out += [("Link", x) for x in (*pcc["import"], *pcc["export"]) if ("Link", x) not in out]
    return out


def _carries(n, comp, name) -> bool:
    """True when the branch can carry power in either direction in any hour."""
    table, nom = {"Link": ("links", "p_nom"), "Line": ("lines", "s_nom"),
                  "Transformer": ("transformers", "s_nom")}[comp]
    df = getattr(n, table)
    if not bool(df.at[name, f"{nom}_extendable"]) and float(df.at[name, nom]) == 0.0:
        return False
    limits = ("p_max_pu", "p_min_pu") if comp == "Link" else ("s_max_pu",)
    for attr in limits:
        per_hour = n.get_switchable_as_dense(comp, attr)[name]
        if bool((per_hour.fillna(1.0).abs() > 0).any()):
            return True
    return False


def tie_state(n) -> dict:
    """``{state, ties, open}``: ``open`` when every branch between the
    mainland and the island is at 0 in both directions in every hour (the
    island runs alone), ``closed`` naming each branch that can carry power,
    ``no_tie`` when the network marks no PoC bus and tags no grid Link."""
    ties = _ties(n)
    if not ties:
        return {"state": "no_tie", "ties": [], "open": []}
    carrying = [name for comp, name in ties if _carries(n, comp, name)]
    return {"state": "closed" if carrying else "open", "ties": [name for _c, name in ties],
            "open": carrying}


def critical_state(n, island) -> dict:
    """``{state, loads, network_only, sidecar_only}``: ``tagged`` when the
    loads on the network's critical buses (``eh_critical``) are exactly the
    loads the island sidecar marks critical, and there is one; ``none`` when
    neither names a load; else ``mismatch`` with each side's own."""
    from services.adequacy.dc_backup import critical_loads
    from services.solver.process_uc import flag_column

    buses = set(n.buses.index[flag_column(n.buses, "eh_critical")].astype(str))
    net = {str(i) for i, b in n.loads["bus"].items() if str(b) in buses} if len(n.loads) else set()
    side = set(critical_loads(island))
    state = "none" if not net and not side else "tagged" if net == side else "mismatch"
    return {"state": state, "loads": sorted(net & side), "network_only": sorted(net - side),
            "sidecar_only": sorted(side - net)}

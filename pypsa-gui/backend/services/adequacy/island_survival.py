"""Island survival of the plan as built (study harness 12; GK A Q1 (a)).

``survival(n, island)`` solves a private copy of the solved network with
every extendable asset fixed at its solved size and the island rows of the
project's own solve (``services.solver.island.add_island_rows``) added as a
check, per investment period. Nothing is sized, so the answer is about the
plan the project has: it holds the sidecar's N+k ride-through over
``sustained_h`` and its islanding step, or it does not.

Four outcomes, never folded into one another:

* ``survives``: the LP with the rows is optimal (the solver's condition is
  ``optimal``; a time or iteration limit, or a suboptimal or imprecise
  ending, is not) and no event row is short;
* ``fails``: a row the fixed sizes cannot hold (a fixed check that is short,
  an infeasible LP, or an event row short of its step), each named;
* ``not_evaluated``: the rows could not be built from the sidecar and the
  network (any other ``IslandRefusal``), or the solver ended any other way
  (an error, a time limit);
* ``not_solved``: the network has no solved sizes to fix.

The caller's network is never written: the copy is the only thing solved.
The solve is plain HiGHS, as the island study's variants are, not the
project's own solver settings.
"""

from __future__ import annotations

import math

import pandas as pd

from services.adequacy.sweep import _CAPACITY_ATTRS

SURVIVES, FAILS, NOT_EVALUATED, NOT_SOLVED = (
    "survives",
    "fails",
    "not_evaluated",
    "not_solved",
)
#: The solver conditions that say the rows cannot be held by the fixed sizes.
_INFEASIBLE = ("infeasible", "infeasible_or_unbounded")


#: The solved dispatch table an asset of each class is in after a solve.
_DISPATCH = {
    "generators": "p",
    "storage_units": "p",
    "stores": "e",
    "links": "p0",
    "lines": "p0",
    "transformers": "p0",
}


class NotSolved(ValueError):
    """An extendable asset has no solved size, so there is no plan to fix."""


def fix_design(n) -> list[str]:
    """Fix every extendable asset of ``n`` at its solved size, in place:
    ``*_nom = *_nom_opt`` and ``*_nom_extendable = False`` for generators,
    storage units, stores, links, lines and transformers. Returns the
    ``"<table>/<name>"`` of each asset fixed. ``NotSolved`` when a solved
    size is missing or not a number, or the asset is not in the solved
    dispatch (added since the solve: PyPSA holds ``*_nom_opt`` 0 for it), so
    it is never fixed at 0 instead.

    Not ``sweep.freeze_capacities`` (it keeps extendability on and lets the
    LP build a micro-MW) nor ``process_uc._fix_capacities`` (it fixes a
    missing size at 0): the check needs every size off the LP and no guess."""
    fixed = []
    for table, nom in _CAPACITY_ATTRS:
        df = getattr(n, table)
        ext = f"{nom}_extendable"
        if df.empty or ext not in df.columns:
            continue
        for name in df.index[df[ext].astype(bool)]:
            opt = df.at[name, f"{nom}_opt"] if f"{nom}_opt" in df.columns else None
            if (
                opt is None
                or pd.isna(opt)
                or not math.isfinite(float(opt))
                or name not in getattr(n, f"{table}_t")[_DISPATCH[table]].columns
            ):
                raise NotSolved(f"{name} is extendable and has no solved size")
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
    return n.optimize(
        solver_name="highs", extra_functionality=extra_functionality, **kw
    )


def _facts(facts) -> dict:
    facts = facts or {}
    return {
        "periods": {
            str(p): {
                "window_snapshots": v.get("window_snapshots"),
                "critical_peak_mw": v.get("critical_peak_mw"),
            }
            for p, v in (facts.get("periods") or {}).items()
        },
        "critical_loads": list(facts.get("critical_loads") or []),
        "seamless_bridge": facts.get("seamless_bridge"),
        "fuel": dict(facts.get("fuel") or {}),
        "missing": dict(facts.get("missing") or {}),
    }


def survival(n, island, *, solve=None) -> dict:
    """The outcome for the solved network ``n`` and its validated island
    sidecar ``island`` (``island_config.load_island_config`` / ``validate``,
    never the raw file): ``{state, reason, shortfalls, binding, fixed, solves, solver, sustained_h,
    scenarios, periods, critical_loads, seamless_bridge, fuel, missing}``.
    ``shortfalls`` are ``{row, sentence}``; ``reason`` is words, None when
    the island survives. ``solves`` is 1 once the check's solve was attempted
    (0 when there was no plan to fix) and ``solver`` its ``{status,
    condition}``, for the caller that charges a solve budget; the function
    opens no solver context and writes neither ``n`` nor ``n.meta``. ``solve(n, extra_functionality, **kw)`` returns
    ``(status, condition)``; HiGHS by default."""
    from services.adequacy.energization import apply_energization
    from services.solver import island as rows

    req = island["requirements"]
    out = {
        "state": NOT_SOLVED,
        "reason": None,
        "shortfalls": [],
        "binding": [],
        "fixed": [],
        "solves": 0,
        "solver": None,
        "sustained_h": float(req["values"]["sustained_h"]),
        "scenarios": list(req["scenarios"]),
        **_facts(None),
    }
    if not _solved(n):
        return {**out, "reason": "the network has no solved plan"}
    from services.study.findings import copy_network

    try:
        c = copy_network(n)
        if isinstance(n.snapshots, pd.MultiIndex):
            c.snapshots.name = "snapshot"  # PyPSA's copy() drops the MultiIndex name
        out["fixed"] = fix_design(c)
        # A fixed-size run: the linearised unit commitment still commits (#448 §3.1),
        # but only the gensets the own solve committed (it never commits one it sizes).
        setattr(c, rows.FIXED_DESIGN_ATTR, True)
        setattr(c, rows.AS_SIZED_ATTR, frozenset(rows.uc_gensets(n, island)[1]))
        apply_energization(c)
    except NotSolved as exc:
        return {**out, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 — a check that could not be set up is no verdict
        return {
            **out,
            "state": NOT_EVALUATED,
            "reason": f"the island check could not be set up ({type(exc).__name__})",
        }
    out["solves"] = 1  # from here the solve is attempted, whatever its ending
    try:
        with rows.linearised_uc(c, island) as kw:
            status, condition = (solve or _solve)(
                c, rows.island_extra_functionality(island, check=True), **kw
            )
    except rows.IslandRefusal as exc:
        short = getattr(exc, "short", None)
        if (
            short is None
        ):  # the rows could not be built: a bad input is not a failed plan
            return {
                **out,
                "state": NOT_EVALUATED,
                "reason": f"the island rows could not be built: {exc}",
            }
        return {
            **out,
            "state": FAILS,
            "reason": f"{exc.row}: the plan as built is short by {short:.4g}",
        }
    except Exception as exc:  # noqa: BLE001 — a solver that broke is no verdict, in words
        return {
            **out,
            "state": NOT_EVALUATED,
            "reason": f"the island check's solve stopped with an error ({type(exc).__name__})",
        }
    facts = getattr(c, "_island_facts", None)
    out.update(
        _facts(facts), solver={"status": str(status), "condition": str(condition)}
    )
    short = [
        {"row": sh["row"], "sentence": rows.shortfall_sentence(sh)}
        for sh in rows.island_shortfalls(facts)
    ]
    if status == "ok" and str(condition) == "optimal" and not short:
        if (facts or {}).get("skipped") or not facts:
            return {
                **out,
                "state": NOT_EVALUATED,
                "reason": "the island rows were not added to the solve",
            }
        return {**out, "state": SURVIVES, "binding": rows.binding_rows(c)}
    if short or str(condition) in _INFEASIBLE:
        return {
            **out,
            "state": FAILS,
            "shortfalls": short,
            "reason": (
                "; ".join(f"{s['row']}: {s['sentence']}" for s in short)
                if short
                else "the plan as built cannot hold the island rows (the solve with them is infeasible)"
            ),
        }
    return {
        **out,
        "state": NOT_EVALUATED,
        "reason": (
            f"the island check's solve ended with status {status}"
            if str(condition) == "optimal"
            else f"the island check's solve ended {str(condition).replace('_', ' ')}, not optimal"
        ),
    }


# ── the input rules (GK A I8, I9) ──────────────────────────────────────────


def island_buses(n, island) -> set[str]:
    """The island's own buses: the bus of every unit the island sidecar names
    that the network has (the set the island rows cover). A genset or
    storage modelled as a Link counts on its output bus."""
    return set(_unit_buses(n, island).values())


def _unit_buses(n, island) -> dict[str, str]:
    """``{unit: bus}`` for every sidecar unit the network has (see ``island_buses``)."""
    out = {}
    for unit in island["units"]:
        for table, col in (
            ("generators", "bus"),
            ("storage_units", "bus"),
            ("loads", "bus"),
            ("stores", "bus"),
            ("links", "bus1"),
        ):
            df = getattr(n, table)
            if unit in df.index:
                out[unit] = str(df.at[unit, col])
    return out


#: The tables whose rows supply a bus directly. A row the sidecar does not name on a bus that
#: holds a sidecar unit marks that bus as the mainland's (#46).
_SUPPLY_TABLES = ("generators", "storage_units", "stores")


def _units_on_mainland(n, island) -> dict | None:
    """#46 (GK A N5): the refusal when a sidecar unit sits on a bus that reads as the mainland's,
    else None. The island is never repaired here: a unit on the mainland is a sidecar error, and
    the gate says so (#1079: what cannot be read is no evidence, never ``no_tie``).

    A bus reads as the mainland's when it also hosts a supply the sidecar does not list. When the
    mainland's supply arrives only through a Link, the grid-import Link (``eh_role ==
    "grid_import"``, its ``bus0`` the grid side) with both ends on the units' buses names it."""
    unit_bus = _unit_buses(n, island)
    named = set(island["units"])
    foreign: dict[str, list[str]] = {}
    for table in _SUPPLY_TABLES:
        df = getattr(n, table)
        for name in df.index:
            bus = str(df.at[name, "bus"])
            if name not in named and bus in unit_bus.values():
                foreign.setdefault(bus, []).append(str(name))
    links = []
    if "eh_role" in n.links.columns:
        own = set(unit_bus.values())
        for name in n.links.index[n.links["eh_role"].astype(str) == "grid_import"]:
            ends = {str(n.links.at[name, c]) for c in ("bus0", "bus1")}
            if ends <= own:
                links.append(str(name))
                foreign.setdefault(str(n.links.at[name, "bus0"]), [])
    if not foreign:
        return None
    units = sorted(u for u, b in unit_bus.items() if b in foreign)
    buses = sorted({unit_bus[u] for u in units})
    why = []
    for bus in buses:
        on = ", ".join(u for u in units if unit_bus[u] == bus)
        if foreign[bus]:
            reason = f"it hosts {', '.join(sorted(foreign[bus]))}, which the island sidecar does not list"
        else:
            reason = "it is the grid side (bus0) of the grid-import link " + ", ".join(
                l for l in links if str(n.links.at[l, "bus0"]) == bus
            )
        why.append(f"{on} sits on bus {bus}, which reads as the mainland's: {reason}")
    words = (
        "; ".join(why)
        + ". Move the unit off that bus, or list that bus's supply in the "
        "island sidecar if the bus really is part of the island."
    )
    return {
        "state": "unit_on_mainland",
        "units": units,
        "buses": buses,
        "links": links,
        "reason": words,
    }


def _ties(n, own: set[str]) -> list[tuple[str, str]]:
    """(component, name) of every branch between the island and anything
    else: a Link (any of its ports), Line or Transformer with an end on one of
    the island's own buses ``own`` and an end off them. A branch with every
    end off the island (mainland-internal) is not a tie."""
    out = []
    for comp, table in (
        ("Link", "links"),
        ("Line", "lines"),
        ("Transformer", "transformers"),
    ):
        df = getattr(n, table)
        for name in df.index:
            ends = {
                str(df.at[name, col])
                for col in df.columns
                if col.startswith("bus") and col[3:].isdigit()
            } - {"", "nan"}
            if ends & own and ends - own:
                out.append((comp, str(name)))
    return out


def _carries(n, comp, name) -> bool:
    """True when the branch can carry power in either direction in any hour."""
    table, nom = {
        "Link": ("links", "p_nom"),
        "Line": ("lines", "s_nom"),
        "Transformer": ("transformers", "s_nom"),
    }[comp]
    df = getattr(n, table)
    size = f"{nom}_max" if bool(df.at[name, f"{nom}_extendable"]) else nom
    if float(df.at[name, size]) == 0.0:
        return False
    limits = ("p_max_pu", "p_min_pu") if comp == "Link" else ("s_max_pu",)
    for attr in limits:
        per_hour = n.get_switchable_as_dense(comp, attr)[name]
        if bool((per_hour.fillna(1.0).abs() > 0).any()):
            return True
    return False


def tie_state(n, island) -> dict:
    """``{state, ties, carrying}``: ``open`` when every branch between the
    island's own buses (``island_buses``) and any other bus is at 0 in both
    directions in every hour (the island runs alone), ``closed`` naming each
    branch that can carry power, ``no_tie`` when no branch leaves the island
    and ``no_island`` when the network has none of the sidecar's units.
    ``unit_on_mainland`` (with ``units``, ``buses``, ``links`` and ``reason``)
    refuses when a sidecar unit sits on a bus that reads as the mainland's
    (``_units_on_mainland``).

    A bus with no sidecar unit on it is off the island, so a feeder to such a
    bus reads as a tie (closed when it carries power): never an open tie
    that is not one."""
    own = island_buses(n, island)
    if not own:
        return {"state": "no_island", "ties": [], "carrying": []}
    refused = _units_on_mainland(n, island)
    if refused is not None:
        return refused
    ties = _ties(n, own)
    if not ties:
        return {"state": "no_tie", "ties": [], "carrying": []}
    carrying = [name for comp, name in ties if _carries(n, comp, name)]
    return {
        "state": "closed" if carrying else "open",
        "ties": [name for _c, name in ties],
        "carrying": carrying,
    }


def critical_state(n, island) -> dict:
    """``{state, loads, network_only, sidecar_only}``: ``tagged`` when the
    loads on the network's critical buses (``eh_critical``) are exactly the
    loads the island sidecar marks critical, and there is one; ``none`` when
    neither names a load; else ``mismatch`` with each side's own."""
    from services.adequacy.dc_backup import critical_loads
    from services.solver.process_uc import flag_column

    buses = set(n.buses.index[flag_column(n.buses, "eh_critical")].astype(str))
    net = (
        {str(i) for i, b in n.loads["bus"].items() if str(b) in buses}
        if len(n.loads)
        else set()
    )
    side = set(critical_loads(island))
    state = "none" if not net and not side else "tagged" if net == side else "mismatch"
    return {
        "state": state,
        "loads": sorted(net & side),
        "network_only": sorted(net - side),
        "sidecar_only": sorted(side - net),
    }

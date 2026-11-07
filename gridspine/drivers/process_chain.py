"""A project network's process units as the dip and restart screens read them (grid-to-process issue 12b-2).

Each process unit (a Link with ``eh_process``) gives the screens its ride-through curve, restart time, tier,
motor data, rated MW, and two derived inputs, each with a basis column that says how it was found:

**Rate (t/h).** The unit's rated flow onto its product bus, bus1:

    rate_tph = capacity * efficiency * t_per_unit(bus1)

The capacity is ``p_nom``, or ``p_nom_opt`` for an extendable unit. ``t_per_unit`` is 1 on a bus counted in
``t`` and 1 / ``mwh_per_t`` on one counted in ``MWh`` with a tonne factor. The rate is unknown in these cases:
the bus has neither; the efficiency is not above 0 or varies in time; the capacity is not above 0; or the unit's
``throughput_unit`` disagrees with the bus. ``throughput_unit`` is display only, ``<u>/h``, and agrees when
``u`` is ``t`` or the bus's own unit; a disagreeing label is never resolved by guessing which one is right.

**Mass pull (t of the shortfall product per t of the unit's product).** A walk from bus1, Link by Link, to the
bus of a Generator whose ``process_role`` is ``shortfall`` (the product whose loss is priced). At each bus the
consumers are the Links that draw from it: on bus0 (one unit drawn per unit of p0) or on a port with a negative
efficiency (its magnitude drawn per unit of p0). Each consumer's factor is

    factor = efficiency_out / drawn * t_per_unit(out) / t_per_unit(in)

for its one product output. The walk reads unknown in these cases:
- a consumer has no product output (a bus counted in t or MWh), or more than one;
- its product output counts MWh with no tonne factor;
- an efficiency it uses varies in time;
- the consumers differ in output bus or factor (branching);
- the walk returns to a bus (a cycle);
- the walk ends on a bus with no consumer and no shortfall Generator.

Parallel consumers with the same output bus and the same factor are one path: two flotation lines on one slurry
bus. A Store or Load on the path is not a branch. Every Link counts, whatever its build year.
"""
from __future__ import annotations

import math

import pandas as pd

from gridspine.static.flicker import _num

TOLERANCE = 1e-9
SHORTFALL = "shortfall"

#: The columns ``restart_units`` returns, one row per process unit.
UNIT_COLUMNS = ("unit", "tier", "rated_mw", "ride_through_curve", "restart_time_h", "rate_tph", "rate_basis",
                "mass_pull", "mass_pull_basis", "motor_kw", "start_method", "i_start_pu")


def _text(v) -> str:
    return "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v).strip()


def truthy(v) -> bool:
    """The one flag rule: a string is true for "true", "1", "1.0" or "yes" (any case, trimmed); a
    non-string is its ``bool`` (False for None and NaN)."""
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "1.0", "yes")
    return False if v is None or pd.isna(v) else bool(v)


def _cell(df: pd.DataFrame, row: str, col: str):
    return df.at[row, col] if col in df.columns and row in df.index else None


def t_per_unit(n, bus: str) -> tuple[float | None, str]:
    """``(tonnes per bus unit, the bus's unit)``: 1 for ``t``, 1 / ``mwh_per_t`` for ``MWh`` with a tonne
    factor above 0, else None."""
    unit = _text(_cell(n.buses, bus, "unit"))
    if unit == "t":
        return 1.0, unit
    f = _num(_cell(n.buses, bus, "mwh_per_t"))
    if unit == "MWh" and f is not None and f > 0:
        return 1.0 / f, unit
    return None, unit


def _ports(n, link: str) -> list[tuple[int, str, float | None, bool]]:
    """``[(k, bus, efficiency, varies)]`` for the Link's output ports k >= 1 that name a bus."""
    out = []
    k = 1
    while f"bus{k}" in n.links.columns:
        bus = _text(n.links.at[link, f"bus{k}"])
        if bus:
            attr = "efficiency" if k == 1 else f"efficiency{k}"
            series = getattr(n.links_t, attr, None)
            varies = series is not None and link in getattr(series, "columns", ())
            out.append((k, bus, _num(_cell(n.links, link, attr)) if attr in n.links.columns else 1.0, varies))
        k += 1
    return out


def _capacity(n, link: str) -> tuple[float | None, str]:
    if truthy(_cell(n.links, link, "p_nom_extendable")):
        return _num(_cell(n.links, link, "p_nom_opt")), "p_nom_opt"
    return _num(_cell(n.links, link, "p_nom")), "p_nom"


def rate_tph(n, unit: str) -> tuple[float | None, str]:
    """``(rate in t/h or None, basis)`` of a process unit: its rated flow onto bus1 in tonnes."""
    bus = _text(_cell(n.links, unit, "bus1"))
    ports = {k: (b, e, v) for k, b, e, v in _ports(n, unit)}
    if 1 not in ports:
        return None, "unknown: the unit has no bus1"
    _, eff, varies = ports[1]
    f, bus_unit = t_per_unit(n, bus)
    if f is None:
        return None, (f"unknown: {bus} counts {bus_unit or 'no unit'}, neither t nor MWh with a tonne factor "
                      f"(mwh_per_t)")
    label = _text(_cell(n.links, unit, "throughput_unit"))
    if label:
        num = label.split("/")[0].strip()
        if num not in ("t", bus_unit):
            return None, f"unknown: throughput unit {label} disagrees with {bus}, which counts {bus_unit}"
    if varies:
        return None, "unknown: the efficiency varies in time"
    if eff is None or eff <= 0:
        return None, f"unknown: efficiency {eff} onto {bus} is not above 0"
    cap, which = _capacity(n, unit)
    if cap is None or cap <= 0:
        return None, f"unknown: {which} is not above 0"
    conv = "" if bus_unit == "t" else f" / mwh_per_t {1.0 / f:g}"
    return cap * eff * f, f"{which} {cap:g} MW × efficiency {eff:g} {bus_unit}/MWh onto {bus}{conv}"


def _consumers(n, bus: str, skip: str) -> list[tuple[str, float, bool]]:
    """``[(link, drawn per unit of p0, varies)]`` of the Links that draw from ``bus``."""
    out = []
    for link in n.links.index:
        if link == skip:
            continue
        if _text(n.links.at[link, "bus0"]) == bus:
            out.append((link, 1.0, False))
            continue
        for _, b, e, varies in _ports(n, link):
            if b == bus and e is not None and e < 0:
                out.append((link, -e, varies))
                break
    return out


def mass_pull(n, unit: str) -> tuple[float | None, str]:
    """``(t of the shortfall product per t of the unit's product or None, basis)``: the walk the module
    docstring describes."""
    return _walk(n, unit)[:2]


def shortfall_product(n, unit: str) -> str | None:
    """The shortfall product bus the unit's mass pull walk ends at (the product its tonnes are of), or None
    when the walk reads unknown."""
    return _walk(n, unit)[2]


def _walk(n, unit: str) -> tuple[float | None, str, str | None]:
    gens = n.generators
    role = gens["process_role"].map(_text) if "process_role" in gens.columns else pd.Series(dtype=str)
    targets = {str(gens.at[g, "bus"]) for g in role.index[role == SHORTFALL]}
    if not targets:
        return None, "unknown: no Generator has process_role shortfall", None
    bus = _text(_cell(n.links, unit, "bus1"))
    f_in, _ = t_per_unit(n, bus)
    if f_in is None:
        return None, f"unknown: {bus} is not counted in tonnes", None
    factor, path, seen = 1.0, [bus], {bus}
    while bus not in targets:
        steps = []
        for link, drawn, varies in _consumers(n, bus, unit):
            outs = [(b, e, v) for _, b, e, v in _ports(n, link)
                    if b != bus and e is not None and e > 0 and t_per_unit(n, b)[1] in ("t", "MWh")]
            if len(outs) != 1:
                return None, f"unknown: {link} has {len(outs)} product outputs, not one", None
            if t_per_unit(n, outs[0][0])[0] is None:
                return None, f"unknown: {outs[0][0]} counts MWh with no tonne factor (mwh_per_t)", None
            out, e, out_varies = outs[0]
            if varies or out_varies:
                return None, f"unknown: an efficiency of {link} varies in time", None
            steps.append((link, out, e / drawn * t_per_unit(n, out)[0] / t_per_unit(n, bus)[0]))
        if not steps:
            return None, f"unknown: the walk ends at {bus}, with no consumer and no shortfall Generator", None
        out, f = steps[0][1], steps[0][2]
        if any(o != out or not math.isclose(g, f, rel_tol=TOLERANCE) for _, o, g in steps[1:]):
            return None, f"unknown: the product branches at {bus} ({', '.join(s[0] for s in steps)})", None
        if out in seen:
            return None, f"unknown: the walk returns to {out} (a cycle)", None
        factor *= f
        names = " / ".join(s[0] for s in steps)
        path.append(f"{names} ×{f:.6g} → {out}")
        seen.add(out)
        bus = out
    return factor, f"{' '.join(path)} = {factor:.6g} (to the shortfall product)", bus


def restart_units(n) -> pd.DataFrame:
    """One row per process unit (``UNIT_COLUMNS``), every input as stored (None when not set) and the rate and
    mass pull with their bases. A unit with an input missing is kept: the screens read it unknown."""
    links = n.links
    process = [u for u in links.index if truthy(_cell(links, u, "eh_process"))]
    rows = []
    for u in process:
        rate, rate_basis = rate_tph(n, u)
        mp, mp_basis = mass_pull(n, u)
        cap, _ = _capacity(n, u)
        rows.append({"unit": u, "tier": _text(_cell(links, u, "criticality_tier")) or None, "rated_mw": cap,
                     "ride_through_curve": _text(_cell(links, u, "ride_through_curve")) or None,
                     "restart_time_h": _num(_cell(links, u, "restart_time_h")), "rate_tph": rate,
                     "rate_basis": rate_basis, "mass_pull": mp, "mass_pull_basis": mp_basis,
                     "motor_kw": _num(_cell(links, u, "motor_kw")),
                     "start_method": _text(_cell(links, u, "start_method")) or None,
                     "i_start_pu": _num(_cell(links, u, "i_start_pu"))})
    return pd.DataFrame(rows, columns=list(UNIT_COLUMNS))

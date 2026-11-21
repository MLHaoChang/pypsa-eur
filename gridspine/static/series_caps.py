# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Series capacitors in a pandapower net (GC-01b).

A campus series capacitor is a pandapower ``impedance`` of -Xc, marked by the
extra columns ``series_cap`` (True) and ``protection`` (``bypassed`` or
``none``) that ``ingest/campus.py`` writes. Load flow and the sub-synchronous
scan keep Xc. Two kinds of path need more than that:

* **Fault levels** (IEC 60909, grid strength). IEC 60909-0 lets a series
  capacitor with a voltage-limiting device in parallel (MOV, spark gap,
  bypass breaker) be neglected in short-circuit calculations. Keeping -jXc
  would raise S_k'' and so SCR/ESCR, which is non-conservative.
  ``bypass_for_faults`` therefore shorts each ``bypassed`` capacitor: the
  impedance goes out of service and a closed bus-bus switch joins its buses.
  A capacitor with ``protection: none`` stays in.
* **Code that walks only ``net.line`` / ``net.trafo``** (contingency lists,
  DC sensitivities, the island fault solvers, the network writers). It would
  see the capacitor's far side cut off, so it calls ``refuse`` and names the
  capacitor until it models one.

A net without the marker column (every non-campus net, and island_flow's
droop impedances) has no series capacitor.
"""

from __future__ import annotations

import pandapower as pp
import pandas as pd

from gridspine.schema.contracts import ContractError

#: The marker columns ``ingest/campus.py`` writes on ``net.impedance``.
MARK = "series_cap"
PROTECTION = "protection"


def series_caps(net, live_only: bool = True) -> pd.DataFrame:
    """
    The series-capacitor rows of ``net.impedance`` (empty when none): the
    in-service ones, or with ``live_only=False`` also those
    ``bypass_for_faults`` took out.
    """
    imp = getattr(net, "impedance", None)
    if imp is None or imp.empty or MARK not in imp.columns:
        return pd.DataFrame(columns=getattr(imp, "columns", []))
    mark = imp[MARK].fillna(False).astype(bool)
    return imp[mark & imp["in_service"].astype(bool)] if live_only else imp[mark]


def refuse(net, what: str) -> None:
    """
    Raise ``ContractError`` naming every series capacitor in ``net``: ``what``
    walks only lines and transformers, so it would see each capacitor's far
    side cut off. A bypassed one counts too: its bus-bus switch is no line
    either.
    """
    caps = series_caps(net, live_only=False)
    if not caps.empty:
        names = sorted(map(str, caps["name"]))
        raise ContractError(
            f"{what}: series capacitor(s) {names} not modelled here; it walks only lines and "
            f"transformers and would see their far side cut off"
        )


def bypass_for_faults(net):
    """
    Short every ``bypassed`` series capacitor of ``net`` in place, for an
    IEC 60909 / grid-strength calculation (IEC 60909-0 neglects a capacitor
    with a voltage-limiting device in parallel). Each one's impedance goes out
    of service and a closed bus-bus switch joins its buses. ``protection:
    none`` keeps it in. Returns ``net``.
    """
    caps = series_caps(net)
    for i, row in caps.iterrows():
        if row[PROTECTION] == "none":
            continue
        net.impedance.at[i, "in_service"] = False
        pp.create_switch(
            net,
            int(row["from_bus"]),
            int(row["to_bus"]),
            et="b",
            closed=True,
            name=f"{row['name']}~bypass",
        )
    return net


def far_side(net, root_bus: int) -> list:
    """
    ``[(cap name, far-side bus index)]`` for each in-service series
    capacitor: the end that the PCC (``root_bus``) reaches only through the
    capacitor, over in-service lines, transformers, impedances and closed
    bus-bus switches. That is where the units behind the capacitor see grid,
    transformer and capacitor in series (GC-08's sub-synchronous scan). A
    capacitor whose ends stay connected without it (a ring through it, at any
    distances) is refused, naming it, because neither end is behind it alone.
    """
    from collections import deque

    edges = []  # (key, a, b); a capacitor's key is its impedance index

    def join(tbl, kind, a, b, live):
        for i in tbl.index[live(tbl)]:
            edges.append(((kind, i), int(tbl.at[i, a]), int(tbl.at[i, b])))

    def on(t):
        return t["in_service"].astype(bool)

    join(net.line, "line", "from_bus", "to_bus", on)
    join(net.trafo, "trafo", "hv_bus", "lv_bus", on)
    join(net.impedance, "impedance", "from_bus", "to_bus", on)
    join(
        net.switch,
        "switch",
        "bus",
        "element",
        lambda t: (t["et"] == "b") & t["closed"].astype(bool),
    )

    def reach(skip):
        adj = {int(b): set() for b in net.bus.index}
        for key, x, y in edges:
            if key != skip:
                adj[x].add(y)
                adj[y].add(x)
        seen, todo = {int(root_bus)}, deque([int(root_bus)])
        while todo:
            for y in adj[todo.popleft()] - seen:
                seen.add(y)
                todo.append(y)
        return seen

    out = []
    for i, row in series_caps(net).iterrows():
        a, b = int(row["from_bus"]), int(row["to_bus"])
        seen = reach(("impedance", i))
        if (a in seen) == (b in seen):
            where = (
                "both ends stay connected to the PCC without it (a ring through it)"
                if a in seen
                else "neither end reaches the PCC"
            )
            raise ContractError(
                f"series capacitor {row['name']}: {where}; only a capacitor with its far side "
                f"behind it is scanned"
            )
        out.append((str(row["name"]), a if b in seen else b))
    return out

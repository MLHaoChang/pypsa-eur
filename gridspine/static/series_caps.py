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
    capacitor: the end with the greater graph distance from ``root_bus``
    (the PCC) over in-service lines, transformers, impedances and closed
    bus-bus switches. That is where the units behind the capacitor see grid,
    transformer and capacitor in series (GC-08's sub-synchronous scan). A tie
    (a ring) is refused, naming the capacitor, because neither end is behind it.
    """
    from collections import deque

    adj = {int(b): set() for b in net.bus.index}

    def join(tbl, a, b, live):
        for i in tbl.index[live(tbl)]:
            x, y = int(tbl.at[i, a]), int(tbl.at[i, b])
            adj[x].add(y)
            adj[y].add(x)

    def on(t):
        return t["in_service"].astype(bool)

    join(net.line, "from_bus", "to_bus", on)
    join(net.trafo, "hv_bus", "lv_bus", on)
    join(net.impedance, "from_bus", "to_bus", on)
    join(
        net.switch,
        "bus",
        "element",
        lambda t: (t["et"] == "b") & t["closed"].astype(bool),
    )
    dist, todo = {int(root_bus): 0}, deque([int(root_bus)])
    while todo:
        x = todo.popleft()
        for y in adj[x] - dist.keys():
            dist[y] = dist[x] + 1
            todo.append(y)
    out = []
    for _, row in series_caps(net).iterrows():
        a, b = int(row["from_bus"]), int(row["to_bus"])
        da, db = dist.get(a), dist.get(b)
        if da is None or db is None or da == db:
            raise ContractError(
                f"series capacitor {row['name']}: neither end is behind it seen from the PCC "
                f"(graph distances {da} and {db}); a capacitor in a ring is not scanned"
            )
        out.append((str(row["name"]), b if db > da else a))
    return out

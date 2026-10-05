"""Critical hours for a campus (plan C3).

The case39 ranking (``metrics``, ``select``) asks transmission questions:
inertia, IBR share, N-1 severity. A campus behind one PCC is sized by
different hours, and this module ranks on those.

``campus_metrics(hourly, pcc, campus)`` returns one row per
``(period, hour)``, with these columns:

``import_mw``
    The capacity-expansion model's net import at the PCC (``campus_pcc.csv``).
    It is negative when exporting.
``export_mw``
    ``max(0, -import_mw)``.
``consumption_mw``
    Everything drawn on the campus in that hour: loads, battery charging,
    and power into other carriers. It is the sum of the negative injections,
    reported positive.
``renewable_mw``
    PV plus wind output. This is the voltage-rise driver.
``trafo_<NAME>_mw``
    The active power through each transformer group, ``|sum of injections
    downstream|``.
    - "Downstream" means the buses cut off from the PCC when the group is
      removed.
    - Parallel transformers between the same two buses form one group,
      named ``A+B``, because their split depends on impedances and the
      load flow resolves it.
    - The estimate is lossless. It is exact in P for a radial campus.
    - A group whose removal cuts nothing off is meshed. It gets no column,
      since its flow cannot be estimated without impedances.

``select_campus_hours(metrics, k)`` takes, within each investment period,
the union of the k most extreme hours per criterion:
``max_import_mw``, ``max_export_mw``, ``max_consumption_mw``,
``max_renewable_mw``, and ``max_trafo_<NAME>_mw`` per group.
- A criterion whose column never rises above zero in a period selects
  nothing there. A campus that never exports has no export hour to study,
  and ranking a column of zeros would pick hours for no reason.
- Boundary ties are spread exactly as in the case39 selection, by reusing
  its ``_top_k_spread``.

Engine cage: pandas/numpy only, plus the campus schema and ``select``.
"""
from collections import deque

import numpy as np
import pandas as pd

from gridspine.ranking.select import _top_k_spread
from gridspine.schema.campus import validate_hourly, validate_pcc
from gridspine.schema.contracts import ContractError

BASE_CRITERIA = (
    ("max_import_mw", "import_mw"),
    ("max_export_mw", "export_mw"),
    ("max_consumption_mw", "consumption_mw"),
    ("max_renewable_mw", "renewable_mw"),
)
_RENEWABLE = frozenset({"pv", "wind"})


def _downstream(campus_spec):
    """``{group name: set of buses cut off from the PCC by removing it}``,
    for every transformer group whose removal cuts something off."""
    c = campus_spec["campus"]
    pcc = str(c["pcc"]["bus"])
    buses = {pcc, *map(str, c.get("buses") or {})}
    groups = {}
    for name, tr in (c.get("transformers") or {}).items():
        key = frozenset((str(tr["hv_bus"]), str(tr["lv_bus"])))
        groups.setdefault(key, []).append(str(name))
    edges = [frozenset((str(cb["from_bus"]), str(cb["to_bus"]))) for cb in (c.get("cables") or {}).values()]

    def reach(skip):
        adj = {b: set() for b in buses}
        for key in groups:
            if key != skip:
                a, b = tuple(key)
                adj[a].add(b), adj[b].add(a)
        for e in edges:
            a, b = tuple(e)
            adj[a].add(b), adj[b].add(a)
        seen, todo = {pcc}, deque([pcc])
        while todo:
            for nxt in adj[todo.popleft()] - seen:
                seen.add(nxt)
                todo.append(nxt)
        return seen

    out = {}
    for key, names in groups.items():
        cut = buses - reach(key)
        if cut:
            out["+".join(sorted(names))] = cut
    return out


def campus_metrics(hourly: pd.DataFrame, pcc: pd.DataFrame, campus_spec: dict) -> pd.DataFrame:
    """One row per ``(period, hour)`` (a MultiIndex) of the columns in the
    module docstring."""
    hourly, pcc = validate_hourly(hourly), validate_pcc(pcc)
    units = campus_spec["campus"]["units"]
    unknown = sorted(set(hourly["unit_id"]) - set(map(str, units)))
    if unknown:
        raise ContractError(f"hourly table units not in the campus: {unknown}")
    kind = {str(u): spec["kind"] for u, spec in units.items()}
    bus = {str(u): str(spec["bus"]) for u, spec in units.items()}
    h = hourly.assign(kind=hourly["unit_id"].map(kind), bus=hourly["unit_id"].map(bus))
    key = ["period", "hour"]
    out = pcc.set_index(key)[["import_mw"]].copy()
    out["export_mw"] = (-out["import_mw"]).clip(lower=0.0)
    out["consumption_mw"] = (-h["p_mw"].clip(upper=0.0)).groupby([h["period"], h["hour"]]).sum()
    out["renewable_mw"] = h.loc[h["kind"].isin(_RENEWABLE)].groupby(key)["p_mw"].sum()
    for group, cut in _downstream(campus_spec).items():
        inj = h.loc[h["bus"].isin(cut)].groupby(key)["p_mw"].sum()
        out[f"trafo_{group}_mw"] = inj.abs()
    return out.fillna(0.0).astype("float64").sort_index()


def select_campus_hours(metrics: pd.DataFrame, k: int = 3) -> pd.DataFrame:
    """``period``, ``hour``, ``reasons`` (a list): within each period, the
    union of the k most extreme hours under each criterion that occurs."""
    if isinstance(k, bool) or not isinstance(k, (int, np.integer)) or int(k) < 1:
        raise ContractError(f"k must be a positive integer, got {k!r}")
    criteria = list(BASE_CRITERIA) + [
        (f"max_{c}", c) for c in metrics.columns if c.startswith("trafo_")
    ]
    rows = []
    for period, block in metrics.groupby(level="period"):
        block = block.droplevel("period").sort_index(kind="mergesort")
        reasons = {}
        for reason, col in criteria:
            if col not in block.columns or not (block[col] > 0).any():
                continue
            ranked = block[col].sort_values(ascending=False, kind="mergesort")
            for hour in _top_k_spread(ranked, int(k)):
                reasons.setdefault(int(hour), []).append(reason)
        for hour in sorted(reasons):
            rows.append({"period": int(period), "hour": hour, "reasons": reasons[hour]})
    return pd.DataFrame(rows, columns=["period", "hour", "reasons"])

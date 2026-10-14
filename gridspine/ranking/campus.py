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

**Island critical hours (plan I3b).** ``island_metrics(hourly, unit,
island, campus)`` reads the I3a tables (``schema/island.py``) and adds, per
``(period, hour)``:

``island_dp_up_mw``, ``island_dp_down_mw``
    The import and export steps at islanding, the worst over scenarios.
``island_inertia_short_mws``
    (max − M) within the period, M the lowest inertia over scenarios.
``island_gfm_headroom_short_mw``
    (max − x) of the upward headroom summed over the grid-forming bess.
``island_load_mw``
    The critical load.
``island_gfl_share``
    Grid-following PV and wind output over the campus consumption: the
    renewable share, highest at low load (voltage rise, over-frequency).
``island_energy_short_mwh``
    (max − x) of the stored energy at loss, e(t−1) summed over the bess.

Minimum criteria are transformed to (max − x) **within each period**, not
negated: the values stay ≥ 0, so a flat series is 0 and selects nothing,
just as a base column that never rises above zero. ``select_campus_hours``
ranks these columns (``ISLAND_CRITERIA``) with the base ones when they are
present; without them the selection is unchanged.

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

#: (reason, column) of the island criteria (plan I3b), in report order.
ISLAND_CRITERIA = (
    ("max_island_dp_up", "island_dp_up_mw"),
    ("max_island_dp_down", "island_dp_down_mw"),
    ("min_island_inertia", "island_inertia_short_mws"),
    ("min_gfm_headroom", "island_gfm_headroom_short_mw"),
    ("max_island_load", "island_load_mw"),
    ("max_gfl_share", "island_gfl_share"),
    ("min_stored_energy", "island_energy_short_mwh"),
)
_GFM = frozenset({"gfm_droop", "gfm_vsm"})


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


def _short(series: pd.Series) -> pd.Series:
    """(max − x) within each period: a minimum criterion as a maximum one."""
    return series.groupby(level="period").transform("max") - series


def island_metrics(hourly: pd.DataFrame, unit: pd.DataFrame, island: pd.DataFrame,
                   campus_spec: dict) -> pd.DataFrame:
    """The island columns (module docstring) per ``(period, hour)``, from the
    campus hourly table and the two I3a island tables, already validated
    (``schema/island.py``; the driver reads and checks them, which keeps
    this module inside its cage)."""
    hourly = validate_hourly(hourly)
    units = campus_spec["campus"]["units"]
    kind = {str(u): spec["kind"] for u, spec in units.items()}
    control = {str(u): (spec.get("island") or {}).get("control") for u, spec in units.items()}
    key = ["period", "hour"]
    by = island.groupby(key)
    out = pd.DataFrame({"island_dp_up_mw": by["dp_up_mw"].max(),
                        "island_dp_down_mw": by["dp_down_mw"].max(),
                        "island_load_mw": by["critical_load_mw"].max()})
    out["island_inertia_short_mws"] = _short(by["inertia_mws"].min())
    bess = unit[unit["unit_id"].map(kind) == "bess"]
    gfm = bess[bess["unit_id"].map(control).isin(_GFM)]
    if len(gfm):
        out["island_gfm_headroom_short_mw"] = _short(gfm.groupby(key)["headroom_up_mw"].sum())
    if len(bess):
        out["island_energy_short_mwh"] = _short(bess.groupby(key)["soc_start_mwh"].sum())
    h = hourly.assign(kind=hourly["unit_id"].map(kind), control=hourly["unit_id"].map(control))
    gfl = h[h["kind"].isin(_RENEWABLE) & ~h["control"].isin(_GFM)]
    consumption = (-h["p_mw"].clip(upper=0.0)).groupby([h["period"], h["hour"]]).sum()
    renewable = gfl.groupby(key)["p_mw"].sum().reindex(consumption.index, fill_value=0.0)
    share = (renewable / consumption.where(consumption > 0)).fillna(0.0)
    share.index.names = key
    out["island_gfl_share"] = share
    return out.fillna(0.0).astype("float64").sort_index()


def select_campus_hours(metrics: pd.DataFrame, k: int = 3) -> pd.DataFrame:
    """``period``, ``hour``, ``reasons`` (a list): within each period, the
    union of the k most extreme hours under each criterion that occurs."""
    if isinstance(k, bool) or not isinstance(k, (int, np.integer)) or int(k) < 1:
        raise ContractError(f"k must be a positive integer, got {k!r}")
    criteria = list(BASE_CRITERIA) + [
        (f"max_{c}", c) for c in metrics.columns if c.startswith("trafo_")
    ] + [(r, c) for r, c in ISLAND_CRITERIA if c in metrics.columns]
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

"""AC load flow at the campus's critical hours and transformer sizing (plan
C4a).

``apply_hour(campus, rows)`` puts one hour of ``campus_hourly.csv`` on the
campus net:
* every generating unit gets its P, with Q = 0, and is out of service when
  its status is 0;
* every load gets its P and the Q its power factor implies.

Every campus unit must be in ``rows``. A unit missing from the hour is
refused, because a silently idle unit would size the plant for an hour that
did not happen.

``solve_cases(campus, rows)`` solves that hour on a copy of the net. It
solves the intact case, then, for every group of parallel transformers
(``trafo_groups``: the same two buses), once with each member out
(``N-1:<name>``). It returns ``{case: HourFlow}``. An hour that does not
converge is kept, flagged ``converged=False``; it is reported and never
sized from.

``solve_cases(..., setpoints=...)`` first puts a reactive dispatch on the
hour: ``{"sgen_q": {name: Q}, "shunt_step": {name: step}}``, the form
``ReactiveResult.setpoints`` gives (plan C8). The same dispatch is held in
every N-1 case: the case is the steady state right after the outage,
before any controller has re-dispatched. Every ``HourFlow`` also carries
each cable's loading (``line``: ``cable``, ``i_ka``, ``loading_pct``).

``size_transformers(flows, campus, criteria)`` gives, per transformer
group, the unit rating that carries every selected hour:

    need(hour) = max(total intact S through the group / units,
                     the largest S on a survivor under N-1)
    required   = max over hours of need * (1 + margin)

``required`` is rounded up to the next standard MVA (``STANDARD_MVA``) and
compared with the rating the campus has now. The N-1 term applies only to
groups of two or more, and only when ``criteria.n_minus_1`` is set (the
default). A single transformer has no N-1 redundancy to size for, and the
report says so (``max_s_n1_mva`` = 0).

Allowed to import pandapower (``static/``); never pypsa.
"""
import copy
import dataclasses
import math

import pandapower as pp
import pandas as pd

from gridspine.schema.campus import STANDARD_MVA
from gridspine.schema.contracts import ContractError

#: Q of an idle load, and the tolerance for "a load does not inject".
P_TOL_MW = 1e-6


@dataclasses.dataclass(frozen=True)
class SizingCriteria:
    """``margin`` is the design headroom on top of the worst selected hour.
    The default of 20 % is an engineering choice, ledgered as assumed.

    ``n_minus_1`` sizes every unit of a parallel group to carry the hour
    alone when a sibling is out. That is the usual data-centre requirement
    and the default. Turned off, each unit is sized for its share of the
    intact flow only."""
    margin: float = 0.2
    n_minus_1: bool = True


@dataclasses.dataclass
class HourFlow:
    converged: bool
    pcc_p_mw: float
    pcc_q_mvar: float
    losses_mw: float
    trafo: pd.DataFrame          # trafo, s_mva, loading_pct
    bus: pd.DataFrame            # bus, vm_pu
    line: pd.DataFrame = dataclasses.field(
        default_factory=lambda: pd.DataFrame(columns=["cable", "i_ka", "loading_pct"]))   # cable loading


def trafo_groups(campus) -> dict:
    """``{"A+B": ["A", "B"]}``: transformers between the same two buses."""
    net = campus.net
    groups = {}
    for i in net.trafo.index:
        key = frozenset((int(net.trafo.at[i, "hv_bus"]), int(net.trafo.at[i, "lv_bus"])))
        groups.setdefault(key, []).append(str(net.trafo.at[i, "name"]))
    return {"+".join(sorted(names)): sorted(names) for names in groups.values()}


def apply_hour(campus, rows: pd.DataFrame) -> None:
    """Put one hour on ``campus.net`` (mutates it). ``rows`` is that hour's
    slice of the hourly table: ``unit_id``, ``p_mw``, ``status``."""
    net, units = campus.net, campus.units
    hour = rows.set_index("unit_id")
    missing = sorted(set(units.index) - set(hour.index))
    if missing:
        raise ContractError(f"the hour has no row for campus unit(s) {missing}")
    stray = sorted(set(hour.index) - set(units.index))
    if stray:
        raise ContractError(f"the hour has rows for unit(s) not in the campus: {stray}")
    load_idx = dict(zip(net.load["name"], net.load.index))
    sgen_idx = dict(zip(net.sgen["name"], net.sgen.index))
    for uid, u in units.iterrows():
        p, on = float(hour.at[uid, "p_mw"]), int(hour.at[uid, "status"]) == 1
        if u["kind"] == "load":
            draw = -p
            if draw < -P_TOL_MW:
                raise ContractError(f"load {uid} injects {p} MW in the hour; a load only draws")
            i = load_idx[uid]
            net.load.at[i, "p_mw"] = max(draw, 0.0)
            net.load.at[i, "q_mvar"] = max(draw, 0.0) * math.tan(math.acos(float(u["pf"])))
            net.load.at[i, "in_service"] = on
        else:
            i = sgen_idx[uid]
            net.sgen.at[i, "p_mw"] = p
            net.sgen.at[i, "q_mvar"] = 0.0
            net.sgen.at[i, "in_service"] = on


def _solve(net) -> HourFlow:
    try:
        pp.runpp(net)
    except pp.LoadflowNotConverged:
        empty_t = pd.DataFrame(columns=["trafo", "s_mva", "loading_pct"])
        empty_b = pd.DataFrame(columns=["bus", "vm_pu"])
        return HourFlow(False, math.nan, math.nan, math.nan, empty_t, empty_b)
    on = net.trafo["in_service"].astype(bool)
    rt = net.res_trafo[on]
    trafo = pd.DataFrame({
        "trafo": net.trafo.loc[on, "name"].astype(str).to_numpy(),
        "s_mva": (rt["p_hv_mw"] ** 2 + rt["q_hv_mvar"] ** 2).pow(0.5).to_numpy(),
        "loading_pct": rt["loading_percent"].to_numpy(),
    })
    bus = pd.DataFrame({"bus": net.bus["name"].astype(str).to_numpy(),
                        "vm_pu": net.res_bus["vm_pu"].to_numpy()})
    line = pd.DataFrame({"cable": net.line["name"].astype(str).to_numpy(),
                         "i_ka": net.res_line["i_ka"].to_numpy(),
                         "loading_pct": net.res_line["loading_percent"].to_numpy()})
    p_grid = float(net.res_ext_grid["p_mw"].sum())
    injected = float(net.res_sgen["p_mw"].sum()) - float(net.res_load["p_mw"].sum())
    return HourFlow(True, p_grid, float(net.res_ext_grid["q_mvar"].sum()), p_grid + injected, trafo, bus, line)


def apply_setpoints(net, setpoints) -> None:
    """Put a reactive dispatch on ``net`` (mutates it): Q on named sgens and
    steps on named shunts. A name the net does not have is refused."""
    for table, col, key in (("sgen", "q_mvar", "sgen_q"), ("shunt", "step", "shunt_step")):
        idx = dict(zip(net[table]["name"].astype(str), net[table].index))
        for name, v in (setpoints.get(key) or {}).items():
            if name not in idx:
                raise ContractError(f"the dispatch names {name!r}, which is not a {table} of the campus")
            net[table].at[idx[name], col] = v


def solve_cases(campus, rows: pd.DataFrame, setpoints=None, inspect=None) -> dict:
    """``{"intact": HourFlow, "N-1:<trafo>": HourFlow, ...}`` for one hour,
    with an optional reactive dispatch in place (module docstring). Works on
    a copy; ``campus.net`` is not changed. ``inspect(case, net)``, if given,
    is called on the solved net of every converged case (the MILP reads its
    Jacobian there, ``campus_milp``)."""
    work = copy.copy(campus)
    work.net = copy.deepcopy(campus.net)
    apply_hour(work, rows)
    net = work.net
    if setpoints:
        apply_setpoints(net, setpoints)

    def run(case):
        out[case] = _solve(net)
        if inspect is not None and out[case].converged:
            inspect(case, net)

    out = {}
    run("intact")
    idx = dict(zip(net.trafo["name"].astype(str), net.trafo.index))
    for members in trafo_groups(work).values():
        if len(members) < 2:
            continue
        for name in members:
            net.trafo.at[idx[name], "in_service"] = False
            run(f"N-1:{name}")
            net.trafo.at[idx[name], "in_service"] = True
    return out


def _standard_up(s):
    for size in STANDARD_MVA:
        if size >= s - 1e-9:
            return size
    return math.nan


def size_transformers(flows: dict, campus, criteria: SizingCriteria = SizingCriteria()) -> pd.DataFrame:
    """One row per transformer group, from ``flows`` = ``{(period, hour):
    solve_cases(...)}``. See the module docstring for the rule."""
    net = campus.net
    rating = dict(zip(net.trafo["name"].astype(str), net.trafo["sn_mva"].astype(float)))
    rows = []
    for group, members in trafo_groups(campus).items():
        n = len(members)
        best = {"need": -1.0, "period": None, "hour": None}
        max_intact = max_n1 = 0.0
        unconverged = 0
        for (period, hour), cases in flows.items():
            intact = cases["intact"]
            if not intact.converged:
                unconverged += 1
                continue
            t = intact.trafo.set_index("trafo")["s_mva"]
            total = float(t.reindex(members).fillna(0.0).sum())
            n1 = 0.0
            if n >= 2 and criteria.n_minus_1:
                for name in members:
                    case = cases.get(f"N-1:{name}")
                    if case is None or not case.converged:
                        continue
                    surv = case.trafo.set_index("trafo")["s_mva"].reindex([m for m in members if m != name])
                    n1 = max(n1, float(surv.max()))
            need = max(total / n, n1)
            max_intact, max_n1 = max(max_intact, total), max(max_n1, n1)
            if need > best["need"]:
                best = {"need": need, "period": int(period), "hour": int(hour)}
        unit_rating = min(rating[m] for m in members)
        required = max(best["need"], 0.0) * (1.0 + criteria.margin)
        rows.append({
            "group": group, "units": n, "unit_rating_mva": unit_rating,
            "max_s_intact_mva": max_intact, "max_s_n1_mva": max_n1,
            "required_unit_mva": required, "recommended_unit_mva": _standard_up(required),
            "adequate": bool(unit_rating >= required - 1e-9),
            "worst_period": best["period"], "worst_hour": best["hour"],
            "unconverged_hours": unconverged, "margin": criteria.margin,
            "n_minus_1": bool(criteria.n_minus_1 and n >= 2),
        })
    return pd.DataFrame(rows)

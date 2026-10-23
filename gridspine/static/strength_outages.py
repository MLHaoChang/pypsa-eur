"""The fault levels the strength study needs: intact and under outages.

``outage_fault_levels`` puts the hour's fault state on a copy of the net
through ``shortcircuit.apply_fault_state`` (the same state
``shortcircuit.fault_levels`` solves), takes the converter units at the
assessed PoC buses out, and runs the IEC 60909 MINIMUM case at those buses:
once per outage, then intact. ``strength.strength_table`` turns the
result into SCR, ESCR and WSCR.

The outages are ``contingency_set.branch_contingencies`` (N-1) and, when
asked, ``contingency_set.n2_candidates`` (N-2), used read-only: issue 13
extends that module. An N-2 row carries only its two N-1 contingency ids, so
it is applied through the N-1 rows' ``element_type`` / ``element_index``.

The cap is decided per order. While an order's count is at or under
``cap`` every outage of it is solved. Above the cap the order is
pre-screened: one intact fault at each PoC with branch results ranks the
branches by their fault contribution (MVA at the branch end with the larger
current), and only the top branch (N-1) or the top pair (N-2) per PoC is
outaged. ``outage_method`` says which was done for each order.

Every outage is applied to a fresh copy of the faulted net, so no outage
leaks into the next. A NaN Sk'' at a PoC means the outage disconnected it;
a solver exception is a ContractError naming the outage.

Allowed to import pandapower (``static/``).
"""
import copy
import dataclasses
import math

import pandapower.shortcircuit as sc
import pandas as pd

from gridspine.schema.contracts import ContractError
from gridspine.static.contingency_set import branch_contingencies, n2_candidates
from gridspine.static.shortcircuit import apply_fault_state

CASE = "min"
FULL_N1 = "full N-1"
FULL_N2 = "full N-2"
PRESCREEN_N1 = "N-1 pre-screen: highest-contributing line"
PRESCREEN_N2 = "N-2 pre-screen: top contributing pair"


@dataclasses.dataclass(frozen=True)
class OutageFaultLevels:
    """``intact``: S_sc (MVA) by PoC bus. ``outages``: index outage id, one
    column per PoC bus, NaN where the outage disconnects the bus.
    ``method``: what was solved, e.g. ``"full N-1; N-2 pre-screen: top
    contributing pair"``."""
    intact: pd.Series
    outages: pd.DataFrame
    method: str


def _bus_indexes(net, buses):
    by_name = pd.Series(net.bus.index, index=net.bus["name"].values)
    if by_name.index.duplicated().any():
        raise ContractError("bus names on the net are not unique")
    unknown = sorted(b for b in buses if b not in by_name.index)
    if unknown:
        raise ContractError(f"PoC bus(es) not on the net: {unknown}")
    return [int(by_name[b]) for b in buses]


def _solve(net, bus_idx, buses, what):
    try:
        sc.calc_sc(net, fault="3ph", case=CASE, bus=bus_idx)
    except Exception as exc:  # noqa: BLE001 — re-raised as a contract error naming the outage
        raise ContractError(f"IEC 60909 failed for {what}: {exc}") from exc
    return pd.Series(net.res_bus_sc["skss_mw"].reindex(bus_idx).values, index=buses, dtype=float)


def _contributions(net, bus) -> pd.Series:
    """Fault MVA each in-service branch carries for a fault at ``bus``, keyed
    ``(element_type, element_index)``; MVA at the end with the larger current."""
    out = {}
    vn = net.bus["vn_kv"]
    for i in net.line.index[net.line["in_service"].astype(bool)]:
        r = net.res_line_sc.loc[i]
        out[("line", int(i))] = max(
            math.sqrt(3) * float(vn[net.line.at[i, "from_bus"]]) * float(r["ikss_from_ka"]),
            math.sqrt(3) * float(vn[net.line.at[i, "to_bus"]]) * float(r["ikss_to_ka"]),
        )
    for i in net.trafo.index[net.trafo["in_service"].astype(bool)]:
        r = net.res_trafo_sc.loc[i]
        out[("trafo", int(i))] = max(
            math.sqrt(3) * float(net.trafo.at[i, "vn_hv_kv"]) * float(r["ikss_hv_ka"]),
            math.sqrt(3) * float(net.trafo.at[i, "vn_lv_kv"]) * float(r["ikss_lv_ka"]),
        )
    s = pd.Series(out, dtype=float).fillna(0.0)
    return s.sort_values(ascending=False, kind="mergesort")


def _top_branches(faulted, bus_idx, n1, k):
    """The N-1 ids of the ``k`` branches with the largest fault contribution,
    per PoC bus (order kept, duplicates across PoCs removed)."""
    id_of = {(t, int(i)): c for t, i, c in
             zip(n1["element_type"], n1["element_index"], n1["contingency_id"])}
    picked = []
    for b in bus_idx:
        work = copy.deepcopy(faulted)
        try:
            sc.calc_sc(work, fault="3ph", case=CASE, bus=b, branch_results=True)
        except Exception as exc:  # noqa: BLE001
            raise ContractError(f"IEC 60909 branch contributions failed at bus {b}: {exc}") from exc
        ranked = [id_of[key] for key in _contributions(work, b).index if key in id_of]
        picked.append(ranked[:k])
    return picked


def outage_fault_levels(net, dispatch, loads, hour, registry, templates, buses,
                        *, cap: int, n2: bool = False) -> OutageFaultLevels:
    """IEC 60909 minimum-case S_sc at ``buses`` (PoC bus names), intact and
    under the N-1 branch outages (and the N-2 pairs when ``n2``), each order
    pre-screened above ``cap``. The caller's net is not changed."""
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
        raise ContractError(f"cap must be a whole number >= 1, got {cap!r}")
    buses = list(dict.fromkeys(buses))
    if not buses:
        raise ContractError("no PoC bus given")
    faulted = copy.deepcopy(net)
    apply_fault_state(faulted, dispatch, loads, hour, registry, templates)
    bus_idx = _bus_indexes(faulted, buses)
    # The plant being judged is out of its own fault level (ledgered in strength.STUDY_LEDGER).
    sgen = getattr(faulted, "sgen", None)
    if sgen is not None and len(sgen):
        sgen.loc[sgen["bus"].isin(bus_idx), "in_service"] = False

    n1 = branch_contingencies(net)
    handle = {c: (t, int(i)) for c, t, i in
              zip(n1["contingency_id"], n1["element_type"], n1["element_index"])}

    outages, methods = [], []
    if len(n1) <= cap:
        outages += [(c, [c]) for c in n1["contingency_id"]]
        methods.append(FULL_N1)
    else:
        tops = _top_branches(faulted, bus_idx, n1, 1)
        outages += [(c, [c]) for c in dict.fromkeys(t[0] for t in tops if t)]
        methods.append(PRESCREEN_N1)
    if n2:
        pairs = n2_candidates(n1)
        if len(pairs) <= cap:
            outages += [(c, list(e)) for c, e in zip(pairs["contingency_id"], pairs["element_ids"])]
            methods.append(FULL_N2)
        else:
            tops = _top_branches(faulted, bus_idx, n1, 2)
            for t in tops:
                if len(t) == 2:
                    a, b = sorted(t)
                    outages.append((f"{a}--{b}", [a, b]))
            outages = list(dict(outages).items())
            methods.append(PRESCREEN_N2)

    rows = {}
    for cid, elements in outages:
        work = copy.deepcopy(faulted)
        for e in elements:
            if e not in handle:
                raise ContractError(f"outage {cid}: element {e!r} is not an N-1 branch contingency")
            etype, eidx = handle[e]
            getattr(work, etype).at[eidx, "in_service"] = False
        rows[cid] = _solve(work, bus_idx, buses, f"outage {cid}")
    # Solved after the outages: equal to fault_levels' minimum case only if no
    # outage leaked into the shared faulted net (pinned by the tests).
    intact = _solve(copy.deepcopy(faulted), bus_idx, buses, "the intact network")
    table = pd.DataFrame.from_dict(rows, orient="index", columns=buses, dtype=float)
    table.index.name = "outage"
    return OutageFaultLevels(intact=intact, outages=table, method="; ".join(methods))

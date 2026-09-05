"""DC sensitivities for the N-2 prune: PTDF, LODF, and double-outage flows.

Everything here is linear algebra over the DC model pandapower already builds
(``rundcpp`` leaves ``Bbus``, ``Bf`` and ``Pfinj`` in ``_ppc["internal"]``), so
a full year of single outages is one dense multiply per hour — which is what
lets N-1 severity rank all 8760 hours (task 8) while AC verifies only the
selected ones.

    PTDF        = Bf @ inv(Bbus)      (reference column zero)
    D[l, k]     = PTDF[l, from_k] - PTDF[l, to_k]
    LODF[l, k]  = D[l, k] / (1 - D[k, k]),   LODF[k, k] = -1
    f'_l        = f_l + LODF[l, k] f_k                          (single outage)
    [Δa, Δb]    = solve([[1, -L_ab], [-L_ba, 1]], [f_a, f_b])   (double outage)
    f'_l        = f_l + L_la Δa + L_lb Δb

ISLANDING IS DETECTED, NOT DIVIDED THROUGH. A radial branch has D[k,k] = 1 and
the LODF denominator vanishes; its column is NaN — never inf — and the branch
is flagged. A pair that islands only together makes the 2x2 system singular
and is reported as islanded. Both detections are proven against lightsim2grid's
AC connectivity over every one of case39's 1035 pairs.

Branch order is ``loadflow.branch_keys`` order — lines then transformers in
table order — which is also pandapower's ppc branch order (probed) and
lightsim2grid's id order, so a column index means the same thing in all three.
Every branch must be in service: pandapower drops dead branches from the ppc
and the columns would silently shift.

Flows are FROM/HV-side MW and ratings are FROM-side MVA (lines: sqrt(3) x
from-bus kV x max_i_ka x df x parallel; transformers: sn_mva x df x parallel),
so DC loading here is the DC analogue of ``contingency.branch_loading_pct``.

Imports pandapower (``static/``); never pypsa, never lightsim2grid.
"""
import copy
import dataclasses

import numpy as np
import pandapower as pp
import pandas as pd
from pandapower.pypower.idx_bus import BUS_TYPE, VA

from gridspine.schema.contracts import ContractError
from gridspine.schema.dc import DCSensitivities, validate_dc_sensitivities
from gridspine.static.loadflow import branch_keys

_ISLAND_TOL = 1e-8

N2_LEDGER = (
    "N-2 candidates are estimated with DC LODF before any AC solve: post-outage "
    "flows from the base-case DC solution and the line outage distribution "
    "factors; a pair is ISLANDED when either branch is radial (LODF denominator "
    "zero) or the pair's 2x2 system is singular — detected, never divided through",
    "the prune metric is the DC-estimated max loading over branches NOT already "
    "over their rating in the base case: case39 at hour 0 carries three lines "
    "above 100 % before any outage (L11 127.6 %, L21 127.1 %, L19 111.7 %, "
    "measured), so an absolute threshold keeps every pair and only NEW "
    "violations can be screened",
    "the prune threshold is MEASURED, not chosen: the full AC N-2 on the fixture "
    "is the ground truth, and the threshold is the largest value that keeps every "
    "pair which creates a violation the base case did not already have; pairs "
    "flagged by voltage alone are counted separately",
    "measured on case39 at hour 0 (2026-09-05): threshold 92.2 %, which prunes "
    "NOTHING — 520 of 561 connected pairs create a new AC violation, none by "
    "voltage alone, and the lowest DC estimate among them is the lowest over all "
    "pairs; the pairs at the bottom sit at 92 % in DC with no predicted new "
    "overload while AC finds one, so the DC blind spot on this fixture is "
    "UNDERESTIMATED loading on the critical branch, and no lossless prune exists",
)


@dataclasses.dataclass
class DCState:
    flows_mw: np.ndarray        # base-case from/HV-side MW, branch order
    ptdf: np.ndarray            # n_branch x n_bus, bus-table order, ref column zero
    lodf: np.ndarray            # n_branch x n_branch, NaN columns for islanding branches
    islanding: np.ndarray       # bool per branch
    rating_mva: np.ndarray      # from-side MVA rating per branch
    ref_bus: int                # position of the reference bus in bus-table order
    bus_names: list
    branch_keys: pd.DataFrame   # loadflow.branch_keys(net)


def _ratings(net) -> np.ndarray:
    line, trafo = net.line, net.trafo
    l_df = line["df"].values if "df" in line.columns else 1.0
    l_par = line["parallel"].values if "parallel" in line.columns else 1.0
    vn_from = net.bus.loc[line["from_bus"].values, "vn_kv"].values
    line_mva = np.sqrt(3.0) * vn_from * line["max_i_ka"].values * l_df * l_par
    t_df = trafo["df"].values if "df" in trafo.columns else 1.0
    t_par = trafo["parallel"].values if "parallel" in trafo.columns else 1.0
    return np.concatenate([line_mva, trafo["sn_mva"].values * t_df * t_par])


def dc_base(net) -> DCState:
    if not (net.line["in_service"].all() and net.trafo["in_service"].all()):
        raise ContractError(
            "dc_base requires every branch in service: pandapower drops dead "
            "branches from the ppc and the LODF columns would silently shift. "
            "Remove out-of-service branches from the contingency set instead."
        )
    w = copy.deepcopy(net)
    pp.rundcpp(w)
    ppc = w._ppc
    internal = ppc["internal"]
    bus = ppc["bus"]
    n_bus = bus.shape[0]
    Bbus = internal["Bbus"].toarray() if hasattr(internal["Bbus"], "toarray") else np.asarray(internal["Bbus"])
    Bf = internal["Bf"].toarray() if hasattr(internal["Bf"], "toarray") else np.asarray(internal["Bf"])
    Pfinj = np.asarray(internal.get("Pfinj", np.zeros(Bf.shape[0]))).ravel()
    n_branch = len(w.line) + len(w.trafo)
    if Bf.shape != (n_branch, n_bus):
        raise ContractError(f"DC model has {Bf.shape} but the net has {n_branch} branches x {n_bus} buses")

    ref_ppc = int(np.where(bus[:, BUS_TYPE] == 3)[0][0])
    keep = np.arange(n_bus) != ref_ppc
    Binv = np.zeros((n_bus, n_bus))
    Binv[np.ix_(keep, keep)] = np.linalg.inv(Bbus[np.ix_(keep, keep)])
    ptdf_ppc = Bf @ Binv

    # ppc bus order -> bus-table order
    lookup = w._pd2ppc_lookups["bus"]
    ppc_of_pd = lookup[w.bus.index.values]
    ptdf = ptdf_ppc[:, ppc_of_pd]
    ref_pd = int(np.where(ppc_of_pd == ref_ppc)[0][0])

    flows_mw = (Bf @ np.deg2rad(bus[:, VA]) + Pfinj) * float(ppc["baseMVA"])

    keys = branch_keys(w)
    pos = {name: i for i, name in enumerate(w.bus["name"].values)}
    f_idx = np.array([pos[b] for b in keys["from_bus"]])
    t_idx = np.array([pos[b] for b in keys["to_bus"]])
    D = ptdf[:, f_idx] - ptdf[:, t_idx]           # D[l, k]
    denom = 1.0 - np.diag(D)
    islanding = np.abs(denom) < _ISLAND_TOL
    lodf = np.full_like(D, np.nan)
    live = ~islanding
    lodf[:, live] = D[:, live] / denom[live][None, :]
    lodf[live, live] = -1.0

    return DCState(
        flows_mw=flows_mw, ptdf=ptdf, lodf=lodf, islanding=islanding,
        rating_mva=_ratings(w), ref_bus=ref_pd,
        bus_names=list(w.bus["name"].values), branch_keys=keys,
    )


def dc_loading_pct(state: DCState, flows_mw) -> np.ndarray:
    return np.abs(np.asarray(flows_mw, dtype=float)) / state.rating_mva * 100.0


def lodf_column(state: DCState, k: int) -> np.ndarray:
    if state.islanding[k]:
        raise ContractError(
            f"branch {k} ({_key(state, k)}) is radial: its outage islands the grid "
            "and its LODF column is undefined"
        )
    return state.lodf[:, k]


def n1_dc_flows(state: DCState, k: int) -> np.ndarray:
    col = lodf_column(state, k)
    out = state.flows_mw + col * state.flows_mw[k]
    out[k] = 0.0
    return out


def n2_dc_flows(state: DCState, a: int, b: int):
    """(post-outage flows, islanded). Flows are None when the pair islands."""
    if a == b:
        raise ContractError(f"a pair needs two distinct branches, got {a} twice")
    if state.islanding[a] or state.islanding[b]:
        return None, True
    L = state.lodf
    det = 1.0 - L[a, b] * L[b, a]
    if abs(det) < _ISLAND_TOL:
        return None, True
    M = np.array([[1.0, -L[a, b]], [-L[b, a], 1.0]])
    delta = np.linalg.solve(M, np.array([state.flows_mw[a], state.flows_mw[b]]))
    out = state.flows_mw + L[:, a] * delta[0] + L[:, b] * delta[1]
    out[a] = 0.0
    out[b] = 0.0
    return out, False


def _key(state: DCState, k: int) -> str:
    r = state.branch_keys.iloc[k]
    return f"{r['from_bus']}-{r['to_bus']}-{r['ckt']}"


def to_sensitivities(state: DCState) -> DCSensitivities:
    """The engine-free artifact ``ranking/`` consumes; branch ids are the N-1
    contingency ids, built from the keys rather than parsed."""
    ids = [f"{r.from_bus}-{r.to_bus}-{r.ckt}" for r in state.branch_keys.itertuples(index=False)]
    return validate_dc_sensitivities(DCSensitivities(
        ptdf=state.ptdf, lodf=state.lodf, islanding=state.islanding, rating_mva=state.rating_mva,
        bus_names=list(state.bus_names), branch_ids=ids, ref_bus=state.ref_bus,
    ))

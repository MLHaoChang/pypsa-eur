"""
`/results/energy_flows`: where the site's energy came from, what it passed
through and where it went (Site commercial UX, package A1).

Plan: .scratch/site-commercial-ux/issues/01-energy-flow-sankey.md.

`energy_flows(n, period, window)` attributes every snapshot's solved dispatch
into **Source → Via → Destination** rows, in MWh:

  * Source — a generator carrier (`grid` reads "Grid import"), or one of the two
    stock labels below.
  * Via — "Direct", or the storage unit / store the energy was held in last.
  * Destination — a load (the energy it was served, shedding removed), "Export"
    (a generator absorbing, or energy sent into a grid bus) or "Losses".

The attribution rules (stated, not tuned):

  1. **Proportional mix at a bus.** The energy leaving a bus carries the origin
     mix of the energy that arrived there, in proportion to the amounts. A link,
     line or transformer passes its input mix to its outputs in proportion to
     the amounts; what it does not deliver (input − output) is "Losses", with
     the input's origin. Buses that feed each other are solved together per
     snapshot as a linear system, so a loop (hub → H2 → hub) is exact.
  2. **Proportional pool per storage.** A storage holds one pool of stored
     energy by origin. Charging adds the mix of the charging bus (times the
     charge efficiency); discharge draws the pool down in proportion and
     delivers its mix, so "Solar via Battery" and "Grid via Battery" stay apart.
     Standing loss and the round-trip loss are "Losses" with the pool's mix.
     The pool tracks the origin only: discharged energy is relabelled "via"
     the storage it left, whatever it passed before.
  3. **Starting stock.** A cyclic storage starts each period with the state it
     ends in; its origin mix is found by replaying the period until the start
     mix stops changing. Stock whose origin the replay cannot establish
     (non-cyclic start, or a storage that never turns over) is "Stored before
     the period"; energy the pool cannot cover is "Unattributed stored energy".
     Both are shown as sources, never dropped.

MWh are `snapshot_weightings.generators` × MW, per modelled year (not scaled by
the period's years). Storage pools run in physical hours (the `stores`
weighting), so a representative week stays a week.

Conservation (checked, and reported with the payload):

    Σ generation = Σ destinations (loads served + export + losses) + Δ storage

per bus, per storage state of charge and in total, to `REL_TOL` of the energy
moved (plus `ABS_TOL_MWH`). If any check fails, or a quantity the attribution
needs is missing (a dispatch frame, a state of charge), the payload says
`not_established` and carries no rows: an unproven picture of where energy went
reads as the whole one (ADR-0001: unavailable, never zero).

Imports no router.
"""
from __future__ import annotations

import logging
import math
import re
from contextvars import ContextVar
from typing import Any, Callable

import numpy as np
import pandas as pd

from services.adequacy.slack import slack_generator_mask
from models.process_unit import product_role_generators
from services.period_utils import is_multi_period

_log = logging.getLogger(__name__)

DIRECT = "Direct"
LOSSES = "Losses"
EXPORT = "Export"
GRID_IMPORT = "Grid import"
STOCK_BEFORE = "Stored before the period"
UNATTRIBUTED = "Unattributed stored energy"

REL_TOL = 1e-6          # of the energy moved
ABS_TOL_MWH = 1e-3
BUS_TOL_MW = 1e-5       # per bus, per snapshot, relative to its throughput below
MAX_CELLS = 40_000_000  # T × buses² of the batched inverse
MAX_PASSES = 8
SERIES_POINTS = 400
_EPS = 1e-9


class NotEstablished(Exception):
    """A quantity the attribution needs is missing or unusable. Carries no text:
    the reason is recorded with `_refuse`, so nothing from a caught exception
    ever reaches a response."""


_WHY: ContextVar[list[str]] = ContextVar("energy_flows_why")


def _refuse(reason: str):
    """Record why the flows cannot be established, then stop the attribution."""
    _WHY.get().append(reason)
    raise NotEstablished()


_ENERGY_UNIT = re.compile(r"^[kMGT]?W(h)?([_ ]?[A-Za-z]+)?$")


def _is_energy_unit(unit) -> bool:
    """A bus `unit` the MWh arithmetic can read: unset, or a power / energy unit
    (MW, kWh, MW_th …). A commodity bus (t/h, kg) is not."""
    u = str(unit or "").strip()
    return u == "" or bool(_ENERGY_UNIT.match(u))


def _fin(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def periods_of(n) -> list:
    if is_multi_period(n):
        return [int(p) for p in n.snapshots.get_level_values(0).unique()]
    return [None]


def _frame(n, result_df, accessor: str, attr: str) -> pd.DataFrame | None:
    df = result_df(n, accessor, attr) if result_df else getattr(getattr(n, accessor), attr, None)
    return df if isinstance(df, pd.DataFrame) else None


def _cols(df: pd.DataFrame | None, names, what: str, rows: np.ndarray) -> np.ndarray:
    """T×len(names) float array from `df`; a missing column or NaN is
    not-established, not zero."""
    names = list(names)
    if not names:
        return np.zeros((int(rows.sum()), 0))
    if df is None or any(c not in df.columns for c in names):
        _refuse(f"no solved {what} for every component")
    out = df[names].to_numpy(dtype=float)[rows]
    if not np.isfinite(out).all():
        _refuse(f"{what} has unsolved (NaN) values")
    return out


def _source_label(carrier) -> str:
    c = str(carrier or "").strip()
    return GRID_IMPORT if c.lower() == "grid" else (c or "Unspecified")


class _Storage:
    """One storage with a state: its bus, charge/discharge MW, efficiencies.

    `soc0` is the state it starts the period in: its own initial state for the
    first snapshot, otherwise the state the previous period ended in."""
    __slots__ = ("name", "bus", "x", "d", "eta_s", "eta_d", "sl", "soc", "cyclic", "soc0")


def _before(df: pd.DataFrame, name: str, rows: np.ndarray, fallback: float) -> float:
    first = int(np.argmax(rows))
    return float(df[name].iloc[first - 1]) if first > 0 else fallback


def _storages(n, result_df, rows, bus_ix, dt) -> list[_Storage]:
    out: list[_Storage] = []
    su = n.storage_units
    if len(su):
        p_dis = _frame(n, result_df, "storage_units_t", "p_dispatch")
        p_sto = _frame(n, result_df, "storage_units_t", "p_store")
        p_net = _frame(n, result_df, "storage_units_t", "p")
        soc = _frame(n, result_df, "storage_units_t", "state_of_charge")
        names = list(su.index)
        if p_dis is not None and p_sto is not None and all(c in p_dis and c in p_sto for c in names):
            d = _cols(p_dis, names, "storage dispatch", rows)
            x = _cols(p_sto, names, "storage charging", rows)
        else:
            net = _cols(p_net, names, "storage dispatch", rows)
            d, x = np.maximum(net, 0.0), np.maximum(-net, 0.0)
        s = _cols(soc, names, "storage state of charge", rows)
        for i, nm in enumerate(names):
            st = _Storage()
            st.name, st.bus = str(nm), bus_ix[str(su.at[nm, "bus"])]
            st.x, st.d, st.soc = x[:, i], d[:, i], s[:, i]
            st.eta_s, st.eta_d = float(su.at[nm, "efficiency_store"]), float(su.at[nm, "efficiency_dispatch"])
            st.sl = float(su.at[nm, "standing_loss"])
            st.cyclic = bool(su.at[nm, "cyclic_state_of_charge"])
            st.soc0 = _before(soc, nm, rows, float(su.at[nm, "state_of_charge_initial"]))
            out.append(st)
    sto = n.stores
    if len(sto):
        names = list(sto.index)
        p = _cols(_frame(n, result_df, "stores_t", "p"), names, "store dispatch", rows)
        e_df = _frame(n, result_df, "stores_t", "e")
        e = _cols(e_df, names, "store energy", rows)
        for i, nm in enumerate(names):
            st = _Storage()
            st.name, st.bus = str(nm), bus_ix[str(sto.at[nm, "bus"])]
            st.d, st.x, st.soc = np.maximum(p[:, i], 0.0), np.maximum(-p[:, i], 0.0), e[:, i]
            st.eta_s = st.eta_d = 1.0
            st.sl = float(sto.at[nm, "standing_loss"])
            st.cyclic = bool(sto.at[nm, "e_cyclic"])
            st.soc0 = _before(e_df, nm, rows, float(sto.at[nm, "e_initial"]))
            out.append(st)
    return out


def _shed(n, capture, rows, load_names, load_bus) -> np.ndarray | None:
    """T×loads MW of unserved demand per load, or None when unknown/none."""
    df = (capture or {}).get("lost_load_t")
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    if len(df) != len(n.snapshots):
        _refuse("the lost-load capture does not match the snapshots")
    arr = df.to_numpy(dtype=float)[rows]
    out = np.zeros((arr.shape[0], len(load_names)))
    pos = {nm: i for i, nm in enumerate(load_names)}
    for j, col in enumerate(df.columns):
        v = np.maximum(np.nan_to_num(arr[:, j]), 0.0)
        if str(col) in pos:
            out[:, pos[str(col)]] += v
        else:                                   # a legacy bus-scoped slack
            at_bus = [i for i, b in enumerate(load_bus) if b == str(col)]
            if at_bus:
                out[:, at_bus] += v[:, None] / len(at_bus)
    return out


def _attribute(n, mask: np.ndarray, result_df, capture) -> dict[str, Any]:
    """The whole-period attribution: per-snapshot MW by (source, via, dest)."""
    T = int(mask.sum())
    buses = [str(b) for b in n.buses.index]
    bus_ix = {b: i for i, b in enumerate(buses)}
    nb = len(buses)
    if T == 0 or nb == 0:
        _refuse("no snapshots in the period")
    if T * nb * nb > MAX_CELLS:
        _refuse("the network is too large for the flow attribution")

    wg = n.snapshot_weightings["generators"].to_numpy(dtype=float)[mask]
    dt = n.snapshot_weightings["stores"].to_numpy(dtype=float)[mask]
    if (dt <= 0).any():
        _refuse("the stores weighting has non-positive snapshots")

    # ---- generators: sources (p > 0) and absorbing sinks (p < 0) -----------------
    gens = n.generators
    keep = ~slack_generator_mask(gens) if len(gens) else pd.Series([], dtype=bool)
    # A process shortfall is lost production, never supply (grid-to-process
    # ticket 04): it reduces what the product's load was served, like shed demand.
    shortfall = [str(g) for g in product_role_generators(n, "shortfall") if str(g) in set(map(str, gens.index))]
    if shortfall:
        keep = keep & ~gens.index.astype(str).isin(shortfall)
    gnames = [str(g) for g in gens.index[keep.to_numpy()]]
    gp = _cols(_frame(n, result_df, "generators_t", "p"), gnames, "generator dispatch", mask)
    g_bus = [bus_ix[str(gens.at[g, "bus"])] for g in gnames]
    g_lab = [_source_label(gens.at[g, "carrier"]) for g in gnames]
    external = np.zeros(nb, dtype=bool)
    for b, lab in zip(g_bus, g_lab):
        external[b] |= lab == GRID_IMPORT

    # ---- loads ------------------------------------------------------------------
    lnames = [str(x) for x in n.loads.index]
    lp = _cols(_frame(n, result_df, "loads_t", "p"), lnames, "load dispatch", mask)
    if (lp < -_EPS).any():
        _refuse("a load injects energy (negative demand)")
    l_bus = [bus_ix[str(n.loads.at[x, "bus"])] for x in lnames]
    shed = _shed(n, capture, mask, lnames, [buses[b] for b in l_bus])
    lost = np.zeros_like(lp)       # product shortfall MW per load
    if shortfall:
        sp = _cols(_frame(n, result_df, "generators_t", "p"), shortfall, "process shortfall dispatch", mask)
        for j, g in enumerate(shortfall):
            at = [i for i, b in enumerate(l_bus) if buses[b] == str(gens.at[g, "bus"])]
            amount = np.maximum(sp[:, j], 0.0)
            demand = lp[:, at].sum(axis=1) if at else np.zeros(T)
            if (amount > _EPS).any() and (not at or ((amount > _EPS) & (demand <= _EPS)).any()):
                _refuse(f"shortfall {g} runs where its bus has no load to leave unserved")
            share = np.where(demand[:, None] > _EPS, lp[:, at] / np.where(demand > _EPS, demand, 1.0)[:, None], 0.0) if at else 0.0
            if at:
                lost[:, at] += amount[:, None] * share
    served = lp - (shed if shed is not None else 0.0) - lost
    if (served < -1e-6 * (1 + lp.max(initial=0.0))).any():
        _refuse("more demand is recorded as unserved than the load asks for")
    served = np.maximum(served, 0.0)

    # ---- branches (links with any number of ports; lines; transformers) ---------
    elems: list[tuple[str, list[tuple[int, np.ndarray]]]] = []     # name, [(bus, p withdrawn)]
    links = n.links
    if len(links):
        lnk_names = [str(x) for x in links.index]
        ports = [c[3:] for c in links.columns if c.startswith("bus") and c[3:].isdigit()]
        port_p = {}
        for k in ports:
            used = [i for i, nm in enumerate(lnk_names) if str(links.at[links.index[i], f"bus{k}"]).strip()]
            if used:
                port_p[k] = (used, _cols(_frame(n, result_df, "links_t", f"p{k}"),
                                         [lnk_names[i] for i in used], f"link port {k} flow", mask))
        for i, nm in enumerate(lnk_names):
            pl = []
            for k, (used, arr) in port_p.items():
                if i in used:
                    pl.append((bus_ix[str(links.at[links.index[i], f"bus{k}"])], arr[:, used.index(i)]))
            elems.append((f"Link {nm}", pl))
    for comp, attr in (("lines", "lines_t"), ("transformers", "transformers_t")):
        df_s = getattr(n, comp)
        if not len(df_s):
            continue
        names = [str(x) for x in df_s.index]
        p0 = _cols(_frame(n, result_df, attr, "p0"), names, f"{comp[:-1]} flow", mask)
        p1 = _cols(_frame(n, result_df, attr, "p1"), names, f"{comp[:-1]} flow", mask)
        for i, nm in enumerate(names):
            elems.append((f"{comp[:-1]} {nm}", [(bus_ix[str(df_s.at[df_s.index[i], "bus0"])], p0[:, i]),
                                                (bus_ix[str(df_s.at[df_s.index[i], "bus1"])], p1[:, i])]))

    storages = _storages(n, result_df, mask, bus_ix, dt)
    nst = len(storages)
    # The external grid is a bus that only supplies: a "grid" generator on a bus
    # that also serves load or holds storage is the site's own import, mixed there.
    for b in set(l_bus) | {st.bus for st in storages}:
        external[b] = False

    # ---- units: the sums below are energy; a commodity port would make "losses" ---
    bus_unit = [str(u or "").strip() for u in n.buses["unit"]] if "unit" in n.buses else [""] * nb
    for name, pl in elems:
        units = {bus_unit[b] for b, _ in pl}
        if not all(_is_energy_unit(u) for u in units) and any(_is_energy_unit(u) for u in units):
            _refuse(f"mixed units on {name}: " + ", ".join(sorted(u or "energy" for u in units)))
    used = set(g_bus) | set(l_bus) | {st.bus for st in storages} | {b for _, pl in elems for b, _ in pl}
    for b in sorted(used):
        if not _is_energy_unit(bus_unit[b]):
            _refuse(f"bus {buses[b]} is in {bus_unit[b]}, not an energy unit; "
                                 "the flows are shown for energy carriers only")

    # ---- labels -----------------------------------------------------------------
    srcs = sorted(set(g_lab))
    srcs += [STOCK_BEFORE, UNATTRIBUTED]
    ns, nv = len(srcs), 1 + nst
    s_ix = {s: i for i, s in enumerate(srcs)}
    K = ns * nv
    vias = [DIRECT] + [s.name for s in storages]
    dests = lnames + [EXPORT, LOSSES]
    d_ix = {d: i for i, d in enumerate(dests)}
    nd = len(dests)

    # ---- throughput I[t, b] (known from the dispatch) and bus balance -------------
    inj = np.zeros((T, nb))          # what arrives at a bus and is mixed there
    inj_elem = np.zeros((T, nb))     # branch outputs only (an external bus does not mix them)
    drain = np.zeros((T, nb))
    for j, b in enumerate(g_bus):
        inj[:, b] += np.maximum(gp[:, j], 0.0)
        drain[:, b] += np.maximum(-gp[:, j], 0.0)
    for j, b in enumerate(l_bus):
        drain[:, b] += served[:, j]
    for st in storages:
        inj[:, st.bus] += st.d
        drain[:, st.bus] += st.x
    for _, pl in elems:
        for b, p in pl:
            inj_elem[:, b] += np.maximum(-p, 0.0)
            drain[:, b] += np.maximum(p, 0.0)
    inflow = inj + np.where(external[None, :], 0.0, inj_elem)
    export_in = np.where(external[None, :], inj_elem, 0.0)
    # Balance: arrivals (incl. branch outputs into a grid bus) = departures. A bus
    # that records unserved demand is balanced on the served demand.
    bal = (inj + inj_elem) - drain
    scale = np.maximum((inj + inj_elem).max(axis=0), drain.max(axis=0))
    bus_resid = np.abs(bal).max(axis=0)
    bus_bad = [buses[b] for b in range(nb) if bus_resid[b] > BUS_TOL_MW * max(1.0, scale[b])]

    I = np.where(inflow > _EPS, inflow, np.inf)      # nothing arrives → a zero share, not a divide

    # ---- branch matrix A and its inverse ----------------------------------------
    A = np.zeros((T, nb, nb))
    loss_share: list[tuple[list[tuple[int, np.ndarray]], np.ndarray]] = []
    elem_in: list[list[tuple[int, np.ndarray]]] = []
    elem_out: list[list[tuple[int, np.ndarray]]] = []
    for _, pl in elems:
        ins = [(b, np.maximum(p, 0.0)) for b, p in pl]
        outs = [(b, np.maximum(-p, 0.0)) for b, p in pl]
        X = sum(x for _, x in ins) if ins else np.zeros(T)
        Xs = np.where(X > _EPS, X, np.inf)
        for b_o, y in outs:
            if external[b_o]:
                continue                           # leaves the site: Export, not mixed in
            for b_i, x in ins:
                A[:, b_o, b_i] += (y / Xs) * (x / I[:, b_i])
        ins_f = [(b, x) for b, x in ins]
        elem_in.append(ins_f)
        outs_all = sum(y for _, y in outs) if outs else np.zeros(T)
        loss_share.append((ins_f, np.where(X > _EPS, (X - outs_all) / Xs, 0.0)))
        elem_out.append(outs)
    M = np.eye(nb)[None, :, :] - A
    try:
        N = np.linalg.inv(M)
    except np.linalg.LinAlgError:
        _refuse("the branch flows form a lossless cycle; the mix is not unique")

    # ---- external supply per label ----------------------------------------------
    S_ext = np.zeros((T, nb, K))
    for j, b in enumerate(g_bus):
        S_ext[:, b, s_ix[g_lab[j]] * nv] += np.maximum(gp[:, j], 0.0)

    # ---- the storage pools, replayed over the period -----------------------------
    Fg = np.einsum("tab,tbk->tak", N, S_ext)           # flux from external supply alone
    st_idx = np.arange(nst)

    def run(P0: np.ndarray, keep: bool):
        """One replay of the period from pool P0 (nst × ns, physical MWh)."""
        P = P0.copy()
        out = {"F": np.zeros((T, nb, K)), "loss_pool": np.zeros((T, nst, ns)),
               "soc": np.zeros((T, nst))} if keep else None
        for t in range(T):
            S = np.zeros((nb, K))
            lossp = np.zeros((nst, ns))
            if nst:
                rho = np.array([(1.0 - st.sl) ** dt[t] for st in storages])
                P_before = P
                P = P * rho[:, None]
                lossp += (P_before - P) / dt[t]                      # standing loss, MW
                e_out = np.array([st.d[t] * dt[t] / st.eta_d if st.eta_d > 0 else 0.0
                                  for st in storages])
                tot = P.sum(axis=1)
                draw = np.minimum(e_out, tot)
                frac = np.where(tot > _EPS, draw / np.where(tot > _EPS, tot, 1.0), 0.0)
                em = P * frac[:, None]
                short = e_out - draw
                short = np.where(short > 1e-9, short, 0.0)
                eta_d = np.array([st.eta_d for st in storages])
                deliver = em * eta_d[:, None] / dt[t]
                deliver[:, s_ix[UNATTRIBUTED]] += short * eta_d / dt[t]
                lossp += (em * (1 - eta_d)[:, None]) / dt[t]
                lossp[:, s_ix[UNATTRIBUTED]] += short * (1 - eta_d) / dt[t]
                P = P - em
                for k, st in enumerate(storages):
                    S[st.bus, np.arange(ns) * nv + 1 + k] += deliver[k]
            F = Fg[t] + N[t] @ S
            if nst:
                eta_s = np.array([st.eta_s for st in storages])
                for k, st in enumerate(storages):
                    if st.x[t] > 0:
                        c_src = (F[st.bus] * (st.x[t] / I[t, st.bus])).reshape(ns, nv).sum(axis=1)
                        P[k] += eta_s[k] * c_src * dt[t]
                        lossp[k] += (1 - eta_s[k]) * c_src
            if keep:
                out["F"][t], out["loss_pool"][t], out["soc"][t] = F, lossp, P.sum(axis=1)
        return P, out

    # starting pool: cyclic → the period's own end state, found by replaying
    soc_last = np.array([st.soc[-1] for st in storages]) if nst else np.zeros(0)
    P0 = np.zeros((nst, ns))
    for k, st in enumerate(storages):
        if st.cyclic:
            P0[k, s_ix[UNATTRIBUTED]] = soc_last[k]
        else:
            P0[k, s_ix[STOCK_BEFORE]] = max(st.soc0, 0.0)
    passes = 0
    if nst and any(st.cyclic for st in storages):
        for passes in range(1, MAX_PASSES + 1):
            P_end, _ = run(P0, keep=False)
            P_new = P0.copy()
            for k, st in enumerate(storages):
                if st.cyclic and P_end[k].sum() > _EPS:
                    P_new[k] = P_end[k] * (soc_last[k] / P_end[k].sum())
            if np.abs(P_new - P0).max(initial=0.0) <= 1e-9 * (1.0 + np.abs(soc_last).max(initial=0.0)):
                P0 = P_new
                break
            P0 = P_new
    P_end, out = run(P0, keep=True)
    F, loss_pool, soc_pool = out["F"], out["loss_pool"], out["soc"]

    # ---- destinations ------------------------------------------------------------
    D = np.zeros((T, K, nd))
    for j, b in enumerate(l_bus):
        D[:, :, d_ix[lnames[j]]] += F[:, b, :] * (served[:, j] / I[:, b])[:, None]
    for j, b in enumerate(g_bus):
        neg = np.maximum(-gp[:, j], 0.0)
        if neg.any():
            D[:, :, d_ix[EXPORT]] += F[:, b, :] * (neg / I[:, b])[:, None]
    for (ins, ls), outs in zip(loss_share, elem_out):
        for b_i, x in ins:
            D[:, :, d_ix[LOSSES]] += F[:, b_i, :] * (x / I[:, b_i] * ls)[:, None]
        for b_o, y in outs:
            if external[b_o]:
                share = _share_into(ins, y, I, T)
                for b_i, x in ins:
                    D[:, :, d_ix[EXPORT]] += F[:, b_i, :] * (x / I[:, b_i] * share)[:, None]
    for k in range(nst):
        for s in range(ns):
            D[:, s * nv + 1 + k, d_ix[LOSSES]] += loss_pool[:, k, s]

    # ---- the checks ---------------------------------------------------------------
    checks: dict[str, Any] = {}
    soc_prev = np.zeros((T, nst))
    for k, st in enumerate(storages):
        start = P0[k].sum() if not st.cyclic else st.soc[-1]
        soc_prev[:, k] = np.concatenate(([start], st.soc[:-1]))
    soc_err = np.abs(soc_pool - np.array([st.soc for st in storages]).T).max(axis=0) if nst else np.zeros(0)
    soc_bad = [storages[k].name for k in range(nst)
               if soc_err[k] > 1e-6 * (1 + np.abs(storages[k].soc).max())]
    dsoc_t = ((np.array([st.soc for st in storages]).T - soc_prev).sum(axis=1) * wg / dt) if nst else np.zeros(T)

    return {
        "buses": buses, "T": T, "wg": wg, "dt": dt, "srcs": srcs, "vias": vias, "dests": dests,
        "ns": ns, "nv": nv, "D": D, "gp": gp, "g_lab": g_lab, "storages": storages,
        "served": served, "lnames": lnames, "shed": shed, "lost": lost, "dsoc_t": dsoc_t,
        "bus_bad": bus_bad, "soc_bad": soc_bad, "passes": passes,
    }


def _share_into(ins, y, I, T):
    X = sum(x for _, x in ins) if ins else np.zeros(T)
    return np.where(X > _EPS, y / np.where(X > _EPS, X, 1.0), 0.0)




def _unavailable(reason: str, *, periods=None, period=None) -> dict[str, Any]:
    return {"status": "not_established", "reason": reason, "unit": "MWh", "period": period,
            "periods": periods or [], "rows": [], "series": None, "conservation": None, "flags": [reason]}


def _series(att, win: slice, n, mask) -> dict[str, Any]:
    """Average MW per bucket, by supply and by demand, for the window."""
    T = att["T"]
    idx = np.arange(T)[win]
    if idx.size == 0:
        return {"index": [], "supply": {}, "demand": {}, "bucket_snapshots": 1}
    step = max(1, math.ceil(idx.size / SERIES_POINTS))
    cuts = list(range(0, idx.size, step))

    def bucket(a: np.ndarray) -> list[float]:
        return [float(a[idx[c:c + step]].mean()) for c in cuts]

    supply: dict[str, list[float]] = {}
    gp, g_lab = att["gp"], att["g_lab"]
    for lab in sorted(set(g_lab)):
        cols = [j for j, l in enumerate(g_lab) if l == lab]
        supply[lab] = bucket(np.maximum(gp[:, cols], 0.0).sum(axis=1))
    for st in att["storages"]:
        supply[f"{st.name} discharge"] = bucket(st.d)
    D = att["D"].sum(axis=1)                       # T × dests, MW
    demand = {d: bucket(D[:, i]) for i, d in enumerate(att["dests"]) if D[:, i].any()}
    sns = n.snapshots.get_level_values(-1) if is_multi_period(n) else n.snapshots
    stamps = [str(sns[mask][idx[c]]) for c in cuts]
    return {"index": stamps, "supply": supply, "demand": demand, "bucket_snapshots": step}


def _stamps(n, mask, lo: int, hi: int) -> dict[str, str | None]:
    """The timestamps behind the window bounds, for the slider's labels."""
    sns = (n.snapshots.get_level_values(-1) if is_multi_period(n) else n.snapshots)[mask]
    at = lambda i: str(sns[i]) if 0 <= i < len(sns) else None   # noqa: E731
    return {"start": at(lo), "end": at(hi), "first": at(0), "last": at(len(sns) - 1)}


def energy_flows(n, period=None, window: tuple[int, int] | None = None, *,
                 result_df: Callable | None = None, lost_load=None) -> dict[str, Any]:
    """
    Source → Via → Destination MWh for one modelled period.

    `period` is the investment period (None: the first, or the only one);
    `window` is an inclusive positional (from, to) pair into that period's
    snapshots — the `from`/`to` contract of the other /results series — and
    narrows the rows and the series, not the replay of the storages.
    """
    periods = periods_of(n)
    if period is None:
        period = periods[0]
    if period not in periods:
        return _unavailable(f"no period {period}", periods=periods)
    if is_multi_period(n):
        mask = np.asarray(n.snapshots.get_level_values(0)) == period
    else:
        mask = np.ones(len(n.snapshots), dtype=bool)
    why: list[str] = []
    token = _WHY.set(why)
    try:
        att = _attribute(n, mask, result_df, lost_load)
    except NotEstablished:
        return _unavailable(why[-1] if why else "not established", periods=periods, period=period)
    finally:
        _WHY.reset(token)
    T = att["T"]
    lo, hi = 0, T - 1
    if window is not None:
        lo, hi = max(0, int(window[0])), min(T - 1, int(window[1]))
    win = slice(lo, hi + 1) if hi >= lo else slice(0, 0)

    wg = att["wg"]
    ns, nv = att["ns"], att["nv"]
    D = att["D"]                                          # T × K × dests (MW)
    per_label = (D[win] * wg[win, None, None]).sum(axis=0)    # K × dests (MWh)
    rows = []
    for k in range(per_label.shape[0]):
        src, via = att["srcs"][k // nv], att["vias"][k % nv]
        for d, dest in enumerate(att["dests"]):
            v = float(per_label[k, d])
            if abs(v) > 1e-9:
                rows.append({"source": src, "via": via, "destination": dest, "mwh": v})
    rows.sort(key=lambda r: (-r["mwh"], r["source"], r["via"], r["destination"]))

    # ---- conservation: Σ generation = Σ destinations + Δ storage ------------------
    w = wg[win]
    gen = float((np.maximum(att["gp"][win], 0.0).sum(axis=1) * w).sum()) if att["gp"].size else 0.0
    dest_total = float(per_label.sum())
    dsoc = float(att["dsoc_t"][win].sum()) if att["storages"] else 0.0
    # energy delivered from stock the window started with: a source of its own in the rows
    stock = float(per_label[[s * nv + v for s in (att["srcs"].index(STOCK_BEFORE), att["srcs"].index(UNATTRIBUTED))
                             for v in range(nv)], :].sum())
    residual = gen - dest_total - dsoc      # stock released in the window is already in dest_total
    tol = REL_TOL * max(gen, dest_total, 1.0) + ABS_TOL_MWH
    checks = {
        "bus_balance": "ok" if not att["bus_bad"] else "failed",
        "storage_tracking": "ok" if not att["soc_bad"] else "failed",
        "energy_closure": "ok" if abs(residual) <= tol else "failed",
    }
    failed = [k for k, v in checks.items() if v != "ok"]
    flags = ([f"bus balance does not close at {', '.join(att['bus_bad'][:5])}"] if att["bus_bad"] else []) + \
            ([f"storage state of charge not reproduced for {', '.join(att['soc_bad'])}"] if att["soc_bad"] else [])
    return {
        "status": "ok" if not failed else "not_established",
        "unit": "MWh", "period": period, "periods": periods,
        "window": {"from": lo, "to": hi, "snapshots": T, **_stamps(n, mask, lo, hi)},
        "rows": rows if not failed else [],
        "series": _series(att, win, n, mask),
        "conservation": {"status": "ok" if not failed else "failed", "checks": failed,
                         "generation_mwh": gen, "destinations_mwh": dest_total,
                         "storage_delta_mwh": dsoc, "stock_mwh": stock,
                         "residual_mwh": residual, "tolerance_mwh": tol},
        "unserved_product_mwh": {ld: float((att["lost"][win, i] * w).sum()) for i, ld in enumerate(att["lnames"])
                                 if att["lost"][:, i].any()},
        "unserved_mwh": None if att["shed"] is None else float((att["shed"][win].sum(axis=1) * w).sum()),
        "flags": flags,
        "rule": "Proportional mix at each bus and a proportional pool per storage; "
                "MWh = MW × generators weighting, per modelled year.",
    }


CSV_HEADER = ("Source", "Via", "Destination", "MWh")


def csv_text(payload: dict[str, Any]) -> str:
    """The payload's rows as CSV: the file the chart is drawn from, row for row."""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_HEADER)
    for r in payload.get("rows") or []:
        w.writerow([r["source"], r["via"], r["destination"], f"{r['mwh']:.6f}"])
    return buf.getvalue()

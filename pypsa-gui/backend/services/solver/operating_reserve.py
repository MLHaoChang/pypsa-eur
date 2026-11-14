"""Operating reserve (homer-parity 01b): dispatchable headroom held every snapshot.

HOMER's operating reserve: in every time step the dispatchable units hold spare capacity of at least

    required(t) = a·load(t) + b·PV(t) + c·wind(t)

with ``a``, ``b``, ``c`` the shares in `SolverConfig` (``reserve_share_load``, ``reserve_share_pv``,
``reserve_share_wind``, 0–1; all None or 0 is off and adds nothing). One LP row per snapshot, ``ROW``:

    Σ_g headroom_g(t) + Σ_s r_s(t) ≥ required(t)

- ``load(t)``: the electrical Loads' p_set as the LP reads it (`renewable_fraction._load_mw`, 01a R4).
- ``PV(t)``, ``wind(t)``: the dispatched output p(t) of the solar and wind Generators on electrical buses (HOMER's
  "% of output"; GK B Q1 (a)), so curtailing them lowers their own reserve need.
- Dispatchable Generators (GK B O3): active, on an electrical bus, sign > 0 and never withdrawing, not a slack
  (VoLL, DSR, lost production) and not variable renewable (solar, PV, wind, run-of-river, wave, tidal); the same
  classification as the renewable fraction (`renewable_fraction.classify`). Their headroom is
  ``p_nom·status(t)·p_max_pu(t) − p(t)`` when committable (an off unit holds none: the reserve can make a genset
  run), ``p_nom·p_max_pu(t) − p(t)`` otherwise, with ``p_nom`` the LP variable when extendable.
- StorageUnits on electrical buses (GK B Q2 (a), O4): ``r_s(t) ≥ 0`` with
  ``r_s ≤ p_nom·p_max_pu(t) − p_dispatch(t) + p_store(t)`` (the power it could still add, cancelling its charge
  included) and ``r_s ≤ p_store(t) + η_dispatch·soc(t)/w(t)``: cancelling the charge needs no stored energy, and
  the extra discharge over the step must come from what is left at its end, ``soc(t)``, with ``w`` the
  snapshot's own ``stores`` weighting (the one the SoC balance uses). The SoC at the end of the step already
  nets the planned dispatch, so no wrap at the first snapshot is needed and the planned discharge is not counted
  twice. A Store-and-Link battery is not counted; the result lists it under ``not_counted``.

Rolling and myopic windows are per snapshot as well, so the rows are the same on every path (O8). Binding (O2)
is read from the solved values only, by slack: held − required ≤ 1e-6·required + 1e-6 MW, the same rule on an
LP and a MILP (a committable unit makes the solve a MILP, whose duals are absent or from a fixed relaxation).
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd

from services.results.carrier_kpis import _without_hydrogen_words
from services.solver.renewable_fraction import _electrical_buses, _load_mw, classify
from services.solver.runtime import _safe_log

ROW = "operating_reserve"
LABEL = "Operating reserve"
SHARES = ("reserve_share_load", "reserve_share_pv", "reserve_share_wind")
_AUX = "operating_reserve-StorageUnit-r"
_VRE = re.compile(r"solar|\bpv\b|wind|\bror\b|run.of.river|wave|tidal")
_PV = re.compile(r"solar|\bpv\b")
_WIND = re.compile(r"wind")
_REL, _ABS = 1e-6, 1e-6


def shares_of(cfg: Any) -> tuple[float, float, float] | None:
    """(a, b, c) when any share is on, else None. A missing, None, non-finite or 0 share is 0."""
    out = []
    for k in SHARES:
        v = cfg.get(k) if isinstance(cfg, dict) else getattr(cfg, k, None)
        try:
            f = float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            f = 0.0
        out.append(f if math.isfinite(f) else 0.0)
    return tuple(out) if any(out) else None  # type: ignore[return-value]


def _carrier(n, g) -> str:
    return _without_hydrogen_words(str(n.generators.at[g, "carrier"]))


def assets(n) -> dict[str, list[str]]:
    """Which assets the reserve reads (see the module docstring)."""
    cls = classify(n)
    gens = n.generators
    elec = _electrical_buses(n)
    sign = gens["sign"] if "sign" in gens.columns else pd.Series(1.0, index=gens.index)
    active = gens["active"] if "active" in gens.columns else pd.Series(True, index=gens.index)
    candidates = cls["nonren_generators"] + cls["renewable_generators"]
    dispatchable = [
        g
        for g in candidates
        if bool(active[g]) and float(sign[g]) > 0 and not _VRE.search(_carrier(n, g))
        and g not in cls["injecting_generators"]
    ]
    vre = [g for g in cls["renewable_generators"] if bool(active[g])]
    su = n.storage_units
    su_active = su["active"] if "active" in su.columns else pd.Series(True, index=su.index)
    stores = n.stores
    return {
        "dispatchable": sorted(dispatchable),
        "pv": sorted(g for g in vre if _PV.search(_carrier(n, g))),
        "wind": sorted(g for g in vre if _WIND.search(_carrier(n, g)) and not _PV.search(_carrier(n, g))),
        "storage_units": sorted(
            s for s in su.index if bool(su_active[s]) and str(su.at[s, "bus"]) in elec
        ),
        "not_counted": sorted(
            s for s in stores.index if str(stores.at[s, "bus"]) not in elec and _feeds_elec(n, str(stores.at[s, "bus"]), elec)
        ),
        "loads": cls["loads"],
    }


def _feeds_elec(n, bus: str, elec: set[str]) -> bool:
    links = n.links
    if not len(links):
        return False
    return bool(((links["bus0"].astype(str) == bus) & links["bus1"].astype(str).isin(elec)).any())


def _dense(n, comp: str, attr: str, snapshots, names) -> pd.DataFrame:
    if not names:
        return pd.DataFrame(index=snapshots)
    return n.get_switchable_as_dense(comp, attr, snapshots)[names].astype(float)


def _wrap_with_operating_reserve(network, user_fn, cfg, log_queue=None):
    """Chain the per-snapshot reserve rows after ``user_fn``; ``user_fn`` unchanged when the reserve is off."""
    shares = shares_of(cfg)
    if shares is None:
        return user_fn

    def wrapped(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        add_rows(n, snapshots, shares, log_queue)

    return wrapped


def add_rows(n, snapshots, shares, log_queue=None) -> None:
    import xarray as xr

    a, b, c = shares
    m = n.model
    sel = assets(n)
    gvar = m.variables["Generator-p"] if "Generator-p" in m.variables else None
    coord = gvar.coords["snapshot"] if gvar is not None else None
    if coord is None and "StorageUnit-p_dispatch" in m.variables:
        coord = m.variables["StorageUnit-p_dispatch"].coords["snapshot"]
    if coord is None:
        _safe_log(log_queue, f"{LABEL}: no Generator or StorageUnit in the LP; no row ({ROW}) added.")
        return
    gnames = set(gvar.indexes["name"]) if gvar is not None else set()

    def da(values, names):
        return xr.DataArray(
            np.asarray(values, dtype=float), dims=["snapshot", "name"], coords={"snapshot": coord, "name": names}
        )

    def per_t(series):
        return xr.DataArray(np.asarray(series, dtype=float), dims=["snapshot"], coords={"snapshot": coord})

    gens = n.generators
    disp = [g for g in sel["dispatchable"] if g in gnames]
    status_names = (
        set(m.variables["Generator-status"].indexes["name"]) if "Generator-status" in m.variables else set()
    )
    ext_names = set(m.variables["Generator-p_nom"].indexes["name"]) if "Generator-p_nom" in m.variables else set()
    pmax = _dense(n, "Generator", "p_max_pu", snapshots, disp)

    lhs = 0
    const = pd.Series(0.0, index=snapshots)
    committed = [g for g in disp if g in status_names]
    extendable = [g for g in disp if g in ext_names and g not in status_names]
    fixed = [g for g in disp if g not in committed and g not in extendable]
    if committed:
        if any(g in ext_names for g in committed):
            _safe_log(
                log_queue,
                f"{LABEL}: WARNING committable and extendable generator(s) "
                + ", ".join(g for g in committed if g in ext_names)
                + " hold headroom only up to p_nom_min while on (status × p_nom is not linear).",
            )
        p_nom = gens.loc[committed, "p_nom"].where(~gens.loc[committed].index.isin(list(ext_names)),
                                                   gens.loc[committed, "p_nom_min"])
        lhs = lhs + (m.variables["Generator-status"].sel(name=committed) * da(pmax[committed].mul(p_nom), committed)).sum("name")
    if extendable:
        lhs = lhs + (m.variables["Generator-p_nom"].sel(name=extendable) * da(pmax[extendable].values, extendable)).sum("name")
    if fixed:
        const += pmax[fixed].mul(gens.loc[fixed, "p_nom"]).sum(axis=1)
    if disp:
        lhs = lhs - gvar.sel(name=disp).sum("name")

    sus = [s for s in sel["storage_units"] if "StorageUnit-p_dispatch" in m.variables
           and s in m.variables["StorageUnit-p_dispatch"].indexes["name"]]
    if sus:
        su = n.storage_units
        r = m.add_variables(lower=0.0, coords=[coord.to_index(), pd.Index(sus, name="name")], name=_AUX)
        pd_ = m.variables["StorageUnit-p_dispatch"].sel(name=sus)
        ps = m.variables["StorageUnit-p_store"].sel(name=sus)
        soc = m.variables["StorageUnit-state_of_charge"].sel(name=sus)
        su_pmax = _dense(n, "StorageUnit", "p_max_pu", snapshots, sus)
        su_ext = set(m.variables["StorageUnit-p_nom"].indexes["name"]) if "StorageUnit-p_nom" in m.variables else set()
        ext = [s for s in sus if s in su_ext]
        fix = [s for s in sus if s not in su_ext]
        power = r + pd_ - ps
        if fix:
            m.add_constraints(
                power.sel(name=fix) <= da(su_pmax[fix].mul(su.loc[fix, "p_nom"]).values, fix),
                name=f"{_AUX}-power-fixed",
            )
        if ext:
            m.add_constraints(
                power.sel(name=ext) - m.variables["StorageUnit-p_nom"].sel(name=ext) * da(su_pmax[ext].values, ext) <= 0,
                name=f"{_AUX}-power-ext",
            )
        w = n.snapshot_weightings["stores"].reindex(snapshots).astype(float)
        eta = su.loc[sus, "efficiency_dispatch"].astype(float)
        coef = pd.DataFrame({s: eta[s] / w.where(w > 0, np.inf) for s in sus}, index=snapshots)
        m.add_constraints(r - ps - soc * da(coef.values, sus) <= 0, name=f"{_AUX}-energy")
        lhs = lhs + r.sum("name")

    load = _load_mw(n, sel["loads"], snapshots)
    need = 0
    rhs = a * load - const
    pv = [g for g in sel["pv"] if g in gnames]
    wind = [g for g in sel["wind"] if g in gnames]
    if b and pv:
        need = need + b * gvar.sel(name=pv).sum("name")
    if c and wind:
        need = need + c * gvar.sel(name=wind).sum("name")
    if isinstance(lhs, int) and isinstance(need, int):
        _safe_log(log_queue, f"{LABEL}: nothing to hold or to cover; no row ({ROW}) added.")
        return
    expr = lhs - need if not isinstance(need, int) else lhs
    m.add_constraints(expr >= per_t(rhs), name=ROW)
    _safe_log(
        log_queue,
        f"{LABEL} (row {ROW}, one per snapshot): headroom of {len(disp)} generator(s) and {len(sus)} storage "
        f"unit(s) ≥ {a:.0%} of load + {b:.0%} of PV + {c:.0%} of wind"
        + (f"; not counted: {', '.join(sel['not_counted'])} (a Store battery)" if sel["not_counted"] else ""),
    )


# ── the result ──────────────────────────────────────────────────────────────


def _col(frame, name, sns) -> pd.Series | None:
    if frame is None or name not in getattr(frame, "columns", []):
        return None
    return frame[name].reindex(sns).astype(float)


def _solved_p_nom(df, name) -> float:
    """p_nom_opt when extendable (the solved size), else p_nom."""
    if bool(df.at[name, "p_nom_extendable"]) and "p_nom_opt" in df.columns:
        return float(df.at[name, "p_nom_opt"])
    return float(df.at[name, "p_nom"])


def operating_reserve_result(n, shares) -> dict[str, Any]:
    """Per snapshot required and held MW from the solved frames only (a missing column is None, O6), and the
    snapshots where the reserve binds (O2). ``shares`` is the solve's (a, b, c), or None when it was off."""
    sns = n.snapshots
    sel = assets(n)
    out: dict[str, Any] = {
        "shares": dict(zip(("load", "pv", "wind"), shares)) if shares else None,
        "not_counted": sel["not_counted"],
        "per_snapshot": [],
        "binding": [],
        "reason": None,
    }
    if shares is None:
        out["reason"] = "the operating reserve was off for this solve"
        return out
    a, b, c = shares
    gens = n.generators
    gp = n.generators_t.p
    unknown: list[str] = []

    def need(name, frame):
        s = _col(frame, name, sns)
        if s is None:
            unknown.append(name)
        return s

    held = pd.Series(0.0, index=sns)
    pmax = _dense(n, "Generator", "p_max_pu", sns, sel["dispatchable"])
    status = getattr(n.generators_t, "status", None)
    for g in sel["dispatchable"]:
        p = need(g, gp)
        if p is None:
            continue
        p_nom = _solved_p_nom(gens, g)
        cap = pmax[g] * p_nom
        if bool(gens.at[g, "committable"]) if "committable" in gens.columns else False:
            st = need(g, status)
            if st is None:
                continue
            cap = cap * st
        held += cap - p
    su = n.storage_units
    su_t = n.storage_units_t
    w = n.snapshot_weightings["stores"].reindex(sns).astype(float)
    su_pmax = _dense(n, "StorageUnit", "p_max_pu", sns, sel["storage_units"])
    for s in sel["storage_units"]:
        pdis, pst, soc = (need(s, getattr(su_t, k, None)) for k in ("p_dispatch", "p_store", "state_of_charge"))
        if pdis is None or pst is None or soc is None:
            continue
        p_nom = _solved_p_nom(su, s)
        power = su_pmax[s] * p_nom - pdis + pst
        energy = pst + float(su.at[s, "efficiency_dispatch"]) * soc / w.where(w > 0, np.inf)
        held += np.minimum(power, energy).clip(lower=0.0)
    load = n.loads_t.p[[l for l in sel["loads"] if l in n.loads_t.p.columns]].sum(axis=1).reindex(sns) if sel["loads"] else pd.Series(0.0, index=sns)
    missing_loads = [l for l in sel["loads"] if l not in n.loads_t.p.columns]
    unknown += missing_loads
    req = a * load.astype(float)
    for share, names in ((b, sel["pv"]), (c, sel["wind"])):
        if not share:
            continue
        for g in names:
            p = need(g, gp)
            if p is not None:
                req = req + share * p
    if unknown:
        out["reason"] = "the solve has no dispatch for " + ", ".join(sorted(set(map(str, unknown))))
        return out
    if not (np.isfinite(req.to_numpy()).all() and np.isfinite(held.to_numpy()).all()):
        out["reason"] = "the dispatch has missing values (NaN)"
        return out
    for t in sns:
        r, h = float(req[t]), float(held[t])
        binds = r > 0 and h - r <= _REL * r + _ABS
        out["per_snapshot"].append({"snapshot": str(t), "required_mw": r, "held_mw": h, "binding": binds})
        if binds:
            out["binding"].append(str(t))
    return out


def infeasibility_lines(n, cfg) -> list[str]:
    """O7: name the snapshots only when the load share alone exceeds the most headroom the inputs allow
    (capacity·p_max_pu, p_nom_max when extendable, plus storage power); otherwise say the reserve may contribute."""
    shares = shares_of(cfg)
    if shares is None:
        return []
    a = shares[0]
    sns = n.snapshots
    sel = assets(n)
    gens, su = n.generators, n.storage_units

    def cap(df, names, comp):
        if not names:
            return pd.Series(0.0, index=sns)
        p_nom = pd.Series(
            {x: float(df.at[x, "p_nom_max"]) if bool(df.at[x, "p_nom_extendable"]) else float(df.at[x, "p_nom"]) for x in names}
        )
        return _dense(n, comp, "p_max_pu", sns, names).mul(p_nom).sum(axis=1)

    most = cap(gens, sel["dispatchable"], "Generator") + cap(su, sel["storage_units"], "StorageUnit")
    req = a * _load_mw(n, sel["loads"], sns)
    short = [t for t in sns if float(req[t]) > float(most[t]) + _ABS]
    if short:
        shown = ", ".join(str(t) for t in short[:3]) + (f" and {len(short) - 3} more" if len(short) > 3 else "")
        return [
            f"{LABEL}: at {shown} the reserve needs {float(req[short[0]]):,.1f} MW but the dispatchable units and "
            f"storage can hold at most {float(most[short[0]]):,.1f} MW (row {ROW}). Lower the reserve shares or "
            "add dispatchable capacity."
        ]
    return [f"{LABEL}: the reserve rows ({ROW}) may contribute; lower the reserve shares to check."]

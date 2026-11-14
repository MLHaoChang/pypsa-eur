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
  run), ``p_nom·p_max_pu(t) − p(t)`` otherwise, with ``p_nom`` the LP variable when extendable. A committable and
  extendable unit holds ``h ≤ p_nom·p_max_pu − p`` and ``h ≤ p_nom_max·status·p_max_pu − p`` (linear; it needs a
  finite ``p_nom_max``, and holds none without one, with a warning). On a multi-period network a unit holds
  headroom only in the periods it is active (build year and lifetime, `n.get_active_assets`).
- StorageUnits on electrical buses (GK B Q2 (a), O4): ``r_s(t) ≥ 0`` with
  ``r_s ≤ p_nom·p_max_pu(t) − p_dispatch(t) + p_store(t)`` (the power it could still add, cancelling its charge
  included) and ``r_s ≤ p_store(t) + η_dispatch·(soc(t)/w(t) − η_store·p_store(t))``: cancelling the charge needs
  no stored energy but leaves the step's end short of what that charge would have stored, and the extra discharge
  must come from what is then left at the end of the step, with ``w`` the snapshot's own ``stores`` weighting
  (the one the SoC balance uses). The SoC at the end of the step already nets the planned dispatch, standing loss
  and inflow, so no wrap at the first snapshot is needed and the planned discharge is not counted twice (a
  deliberate change from O4's soc(t−1), which counts it twice). A Store-and-Link battery is not counted; the
  result lists it under ``not_counted``.

Rolling and myopic windows are per snapshot as well, so the rows are the same on every path (O8). Binding (O2)
is read from the solved values only, by slack: held − required ≤ 1e-6·required + 1e-6 MW, the same rule on an
LP and a MILP (a committable unit makes the solve a MILP, whose duals are absent or from a fixed relaxation).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from services.results.carrier_kpis import _without_hydrogen_words
from services.solver.renewable_fraction import (
    _electrical_buses,
    _load_mw,
    _withdrawing_generators,
    classify,
)
from services.solver.runtime import _safe_log

ROW = "operating_reserve"
LABEL = "Operating reserve"
SHARES = ("reserve_share_load", "reserve_share_pv", "reserve_share_wind")
_AUX = "operating_reserve-StorageUnit-r"
_AUX_G = "operating_reserve-Generator-h"
# Substrings, like the renewable KPI's carrier match (`carrier_kpis._RENEWABLE_KW`), on the same normalised name.
_PV_KW = ("solar", "pv")
_WIND_KW = ("wind",)
_VRE_KW = _PV_KW + _WIND_KW + ("ror", "run-of-river", "run of river", "wave", "tidal")
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


def _has(carrier: str, kws) -> bool:
    return any(k in carrier for k in kws)


def assets(n) -> dict[str, list[str]]:
    """Which assets the reserve reads (see the module docstring)."""
    cls = classify(n)
    gens = n.generators
    elec = _electrical_buses(n)
    sign = gens["sign"] if "sign" in gens.columns else pd.Series(1.0, index=gens.index)
    withdraw = set(_withdrawing_generators(n)) | set(cls["injecting_generators"])
    dispatchable = [
        g
        for g in cls["nonren_generators"] + cls["renewable_generators"]
        if float(sign[g]) > 0
        and g not in withdraw
        and not _has(_carrier(n, g), _VRE_KW)
    ]
    vre = [g for g in cls["renewable_generators"] if g not in withdraw]
    su = n.storage_units
    stores = n.stores
    return {
        "dispatchable": sorted(dispatchable),
        "pv": sorted(g for g in vre if _has(_carrier(n, g), _PV_KW)),
        "wind": sorted(
            g
            for g in vre
            if _has(_carrier(n, g), _WIND_KW) and not _has(_carrier(n, g), _PV_KW)
        ),
        "storage_units": sorted(s for s in su.index if str(su.at[s, "bus"]) in elec),
        "not_counted": sorted(
            s
            for s in stores.index
            if str(stores.at[s, "bus"]) not in elec
            and _feeds_elec(n, str(stores.at[s, "bus"]), elec)
        ),
        "loads": cls["loads"],
    }


def _feeds_elec(n, bus: str, elec: set[str]) -> bool:
    links = n.links
    if not len(links):
        return False
    return bool(
        (
            (links["bus0"].astype(str) == bus) & links["bus1"].astype(str).isin(elec)
        ).any()
    )


def _active(n, comp: str, sns, names) -> pd.DataFrame:
    """1.0 where the asset is in service at the snapshot: its period's `get_active_assets` on a multi-period
    network (build year and lifetime), the static ``active`` flag otherwise."""
    if not names:
        return pd.DataFrame(index=sns)
    if isinstance(sns, pd.MultiIndex):
        periods = sns.get_level_values(0)
        rows = np.zeros((len(sns), len(names)))
        for P in pd.unique(periods):
            act = n.get_active_assets(comp, P).reindex(names).fillna(False)
            rows[np.asarray(periods == P)] = act.to_numpy(dtype=float)
        return pd.DataFrame(rows, index=sns, columns=names)
    act = n.get_active_assets(comp).reindex(names).fillna(False).astype(float)
    return pd.DataFrame(
        np.tile(act.to_numpy(), (len(sns), 1)), index=sns, columns=names
    )


def _dense(n, comp: str, attr: str, sns, names) -> pd.DataFrame:
    if not names:
        return pd.DataFrame(index=sns)
    return n.get_switchable_as_dense(comp, attr, sns)[names].astype(float)


def _avail(n, comp: str, sns, names) -> pd.DataFrame:
    """p_max_pu(t) × in service(t): the share of p_nom a unit can run at."""
    return _dense(n, comp, "p_max_pu", sns, names) * _active(n, comp, sns, names)


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
    svar = (
        m.variables["StorageUnit-p_dispatch"]
        if "StorageUnit-p_dispatch" in m.variables
        else None
    )
    ref = gvar if gvar is not None else svar
    if ref is None:
        _safe_log(
            log_queue,
            f"{LABEL}: no Generator or StorageUnit in the LP; no row ({ROW}) added.",
        )
        return
    coord = ref.coords["snapshot"]
    # Every right-hand side is built on the LP's own snapshot coordinate, by label (never by position).
    sns = coord.to_index()

    def da(frame: pd.DataFrame, names):
        return xr.DataArray(
            frame[names].to_numpy(dtype=float),
            dims=["snapshot", "name"],
            coords={"snapshot": coord, "name": names},
        )

    def names_in(var, wanted):
        return [x for x in wanted if var is not None and x in var.indexes["name"]]

    gens = n.generators
    disp = names_in(gvar, sel["dispatchable"])
    stat = (
        m.variables["Generator-status"] if "Generator-status" in m.variables else None
    )
    gext = m.variables["Generator-p_nom"] if "Generator-p_nom" in m.variables else None
    committed = names_in(stat, disp)
    extendable = [g for g in names_in(gext, disp) if g not in committed]
    both = [g for g in committed if g in set(names_in(gext, disp))]
    committed = [g for g in committed if g not in both]
    fixed = [
        g for g in disp if g not in committed and g not in extendable and g not in both
    ]
    avail = _avail(n, "Generator", sns, disp)

    lhs = 0
    const = pd.Series(0.0, index=sns)
    if committed:
        cap = avail[committed].mul(gens.loc[committed, "p_nom"])
        lhs = lhs + (stat.sel(name=committed) * da(cap, committed)).sum("name")
    if extendable:
        lhs = lhs + (gext.sel(name=extendable) * da(avail, extendable)).sum("name")
    if fixed:
        const += avail[fixed].mul(gens.loc[fixed, "p_nom"]).sum(axis=1)
    if disp:
        lhs = lhs - gvar.sel(name=disp).sum("name")
    if both:
        big = gens.loc[both, "p_nom_max"].astype(float)
        bounded = [g for g in both if math.isfinite(big[g])]
        if len(bounded) < len(both):
            _safe_log(
                log_queue,
                f"{LABEL}: WARNING committable and extendable generator(s) "
                + ", ".join(g for g in both if g not in bounded)
                + " have no finite p_nom_max, so they hold no reserve here.",
            )
        if bounded:
            h = m.add_variables(
                coords=[sns, pd.Index(bounded, name="name")], name=_AUX_G
            )
            pb = gvar.sel(name=bounded)
            m.add_constraints(
                h + pb - gext.sel(name=bounded) * da(avail, bounded) <= 0,
                name=f"{_AUX_G}-capacity",
            )
            m.add_constraints(
                h
                + pb
                - stat.sel(name=bounded) * da(avail[bounded].mul(big[bounded]), bounded)
                <= 0,
                name=f"{_AUX_G}-status",
            )
            # The p term of these units is inside h, so it is added back to the −Σp above.
            lhs = lhs + h.sum("name") + pb.sum("name")

    sus = names_in(svar, sel["storage_units"])
    if sus:
        su = n.storage_units
        r = m.add_variables(
            lower=0.0, coords=[sns, pd.Index(sus, name="name")], name=_AUX
        )
        pdis = svar.sel(name=sus)
        pst = m.variables["StorageUnit-p_store"].sel(name=sus)
        soc = m.variables["StorageUnit-state_of_charge"].sel(name=sus)
        su_avail = _avail(n, "StorageUnit", sns, sus)
        sext = (
            m.variables["StorageUnit-p_nom"]
            if "StorageUnit-p_nom" in m.variables
            else None
        )
        ext = names_in(sext, sus)
        fix = [s for s in sus if s not in ext]
        power = r + pdis - pst
        if fix:
            m.add_constraints(
                power.sel(name=fix) <= da(su_avail[fix].mul(su.loc[fix, "p_nom"]), fix),
                name=f"{_AUX}-power-fixed",
            )
        if ext:
            m.add_constraints(
                power.sel(name=ext) - sext.sel(name=ext) * da(su_avail, ext) <= 0,
                name=f"{_AUX}-power-ext",
            )
        w = n.snapshot_weightings["stores"].reindex(sns).astype(float)
        eta_d = su.loc[sus, "efficiency_dispatch"].astype(float)
        eta_s = su.loc[sus, "efficiency_store"].astype(float)
        soc_coef = pd.DataFrame(
            {s: eta_d[s] / w.where(w > 0, np.inf) for s in sus}, index=sns
        )
        store_coef = pd.DataFrame(
            {s: 1.0 - eta_d[s] * eta_s[s] for s in sus}, index=sns
        )
        m.add_constraints(
            r - pst * da(store_coef, sus) - soc * da(soc_coef, sus) <= 0,
            name=f"{_AUX}-energy",
        )
        lhs = lhs + r.sum("name")

    load = _load_mw(n, sel["loads"], sns)
    need = 0
    pv = names_in(gvar, sel["pv"])
    wind = names_in(gvar, sel["wind"])
    if b and pv:
        need = need + b * gvar.sel(name=pv).sum("name")
    if c and wind:
        need = need + c * gvar.sel(name=wind).sum("name")
    if isinstance(lhs, int) and isinstance(need, int):
        _safe_log(
            log_queue, f"{LABEL}: nothing to hold or to cover; no row ({ROW}) added."
        )
        return
    expr = lhs if isinstance(need, int) else lhs - need
    rhs = (a * load - const).reindex(sns)
    m.add_constraints(
        expr
        >= xr.DataArray(rhs.to_numpy(), dims=["snapshot"], coords={"snapshot": coord}),
        name=ROW,
    )
    _safe_log(
        log_queue,
        f"{LABEL} (row {ROW}, one per snapshot): headroom of {len(disp)} generator(s) and {len(sus)} storage "
        f"unit(s) ≥ {a:.0%} of load + {b:.0%} of PV + {c:.0%} of wind"
        + (
            f"; not counted: {', '.join(sel['not_counted'])} (a Store battery)"
            if sel["not_counted"]
            else ""
        ),
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


def _label(t) -> str:
    """A snapshot as people read it: "2030-01-01 12:00", with the period first on a multi-period network."""
    if isinstance(t, tuple):
        return f"{t[0]} {pd.Timestamp(t[1]):%Y-%m-%d %H:%M}"
    try:
        return f"{pd.Timestamp(t):%Y-%m-%d %H:%M}"
    except (TypeError, ValueError):
        return str(t)


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
    avail = _avail(n, "Generator", sns, sel["dispatchable"])
    status = getattr(n.generators_t, "status", None)
    for g in sel["dispatchable"]:
        p = need(g, gp)
        if p is None:
            continue
        cap = avail[g] * _solved_p_nom(gens, g)
        if "committable" in gens.columns and bool(gens.at[g, "committable"]):
            st = need(g, status)
            if st is None:
                continue
            cap = cap * st
        held += cap - p
    su = n.storage_units
    su_t = n.storage_units_t
    w = n.snapshot_weightings["stores"].reindex(sns).astype(float)
    su_avail = _avail(n, "StorageUnit", sns, sel["storage_units"])
    for s in sel["storage_units"]:
        pdis, pst, soc = (
            need(s, getattr(su_t, k, None))
            for k in ("p_dispatch", "p_store", "state_of_charge")
        )
        if pdis is None or pst is None or soc is None:
            continue
        eta_d, eta_s = (
            float(su.at[s, "efficiency_dispatch"]),
            float(su.at[s, "efficiency_store"]),
        )
        power = su_avail[s] * _solved_p_nom(su, s) - pdis + pst
        energy = pst + eta_d * (soc / w.where(w > 0, np.inf) - eta_s * pst)
        held += np.minimum(power, energy).clip(lower=0.0)
    req = a * _load_mw(n, sel["loads"], sns)
    for share, names in ((b, sel["pv"]), (c, sel["wind"])):
        if not share:
            continue
        for g in names:
            p = need(g, gp)
            if p is not None:
                req = req + share * p
    if unknown:
        out["reason"] = "the solve has no dispatch for " + ", ".join(
            sorted(set(map(str, unknown)))
        )
        return out
    if not (np.isfinite(req.to_numpy()).all() and np.isfinite(held.to_numpy()).all()):
        out["reason"] = "the dispatch has missing values (NaN)"
        return out
    for t in sns:
        r, h = float(req[t]), float(held[t])
        binds = r > 0 and h - r <= _REL * r + _ABS
        out["per_snapshot"].append(
            {"snapshot": _label(t), "required_mw": r, "held_mw": h, "binding": binds}
        )
        if binds:
            out["binding"].append(_label(t))
    return out


def infeasibility_lines(n, cfg) -> list[str]:
    """O7: name the snapshots only when the load share alone exceeds the most headroom the inputs allow
    (capacity·p_max_pu while in service, p_nom_max when extendable, plus storage power); otherwise say the
    reserve may contribute."""
    shares = shares_of(cfg)
    if shares is None:
        return []
    a = shares[0]
    sns = n.snapshots
    sel = assets(n)

    def most(comp, df, names):
        if not names:
            return pd.Series(0.0, index=sns)
        p_nom = pd.Series(
            {
                x: float(df.at[x, "p_nom_max"])
                if bool(df.at[x, "p_nom_extendable"])
                else float(df.at[x, "p_nom"])
                for x in names
            }
        )
        return _avail(n, comp, sns, names).mul(p_nom).sum(axis=1)

    top = most("Generator", n.generators, sel["dispatchable"]) + most(
        "StorageUnit", n.storage_units, sel["storage_units"]
    )
    req = a * _load_mw(n, sel["loads"], sns)
    short = [t for t in sns if float(req[t]) > float(top[t]) + _ABS]
    if short:
        shown = ", ".join(_label(t) for t in short[:3]) + (
            f" and {len(short) - 3} more" if len(short) > 3 else ""
        )
        return [
            f"{LABEL}: at {shown} the reserve needs {float(req[short[0]]):,.1f} MW but the dispatchable units and "
            f"storage can hold at most {float(top[short[0]]):,.1f} MW (row {ROW}). Lower the reserve shares or "
            "add dispatchable capacity."
        ]
    return [
        f"{LABEL}: the reserve rows ({ROW}) may contribute; lower the reserve shares to check."
    ]

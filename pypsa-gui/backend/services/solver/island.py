"""
Island ride-through, fuel and bridge reserve in the hub LP (campus island
plan 2026-10-07, I2a), as an ``extra_functionality`` wrapper composed like
``services/solver/adequacy.py``.

The study requirements come from the project's island sidecar
(``island_config.json``, schema ``gridspine/schema/island.py``). The rows are
**opt-in** (owner Q5): ``SolverConfig.island_constraints``. Off, the wrapper
hands back the callback it was given, so the LP is untouched.

Per investment period P, over that period's snapshots (T of them, each Δ
hours long; Δ comes from the snapshot index, never from
``snapshot_weightings``, which is 8760/168 on the hub templates):

**(a) Sustained ride-through.** RT is ``ride_through_storage``, L_c the
critical load, k ``genset_redundancy_n``, U_max the largest genset rating
(a fixed unit's p_nom, an extendable one's ``unit_mw``):

    G(τ)  = Σ_g avail_g(τ)·p_nom_g − k·U_max
    d(τ) ≥ L_c(τ) − G(τ),   d(τ) ≥ 0,   d(τ) ≤ Σ_{s∈RT} p_nom_s
    Σ_{s∈RT} η_dis,s·e_s(t−1) ≥ Σ_{τ=t}^{t+D−1} d(τ)·Δ + B(t)   for every t

e(t−1) is the state of charge at the end of the hour before the start, so
the reserve is what the site holds when the outage begins. Windows follow
the storage's own setting: cyclic wraps to the period's first snapshot (and
e(t−1) at the first snapshot is the last snapshot's SoC); non-cyclic stops at
the period end, which is flagged, and reads ``state_of_charge_initial``.

**(b) Bridge reserve (Q1).** For every UPS u and every t:
η_dis,u·e_u(t−1) ≥ P_it,u(t)·bridge_s/3600. Under ``seamless`` the
grid-forming storage also holds B(t) = ΔP⁺(t)·(start_s + sync_s)/3600, the
energy until the slowest genset has joined. B enters (a)'s row for storage
in both RT and the GFM set, so one MWh is not counted twice; otherwise it is
its own row. ΔP⁺ is I2b's: until I2b lands it is passed in, and without it
B is 0 and the facts say so.

**(c) Fuel (Q1).** Where a genset is a Link fed from a bus with Stores:
Σ e_nom ≥ L_c,peak·fuel_autonomy_h/η_genset (the draw at the critical peak,
conservative). Gensets that are Generators have no fuel bus: "not checked".

**Ledgered:** PV is left out (conservative); the site is a copperplate (the
hub's one-way site transformer is not seen here; I4 catches it at the island
hours); U_max over-subtracts when an extendable genset is not built
(conservative); the occurrence derating is not applied, since N+k covers
outages.

**Dispatch-only solves skip the rows** (owner, 2026-10-12). They are a
sizing standard (plan step 1): with nothing extendable, or on an operational
re-solve that pins capacities (``_ic_operational``, set by the FMEA sweep's
``freeze_capacities``), they could only make an as-built dispatch
infeasible. The skip is logged and recorded as ``skipped`` in the facts.

**Where it runs.** The plain solve only. Rolling horizon, SCOPF and myopic
foresight are refused with a reason: their windows cut the cyclic period
the reserve rows wrap around.
"""
from __future__ import annotations

import math
import pathlib

import numpy as np
import pandas as pd

from services.solver.runtime import _safe_log


class IslandRefusal(ValueError):
    """The island rows cannot be built as asked; the message says why."""


def strategy_refusal(*, solve_strategy: str, sclopf: bool) -> str | None:
    """Why the island rows cannot run on this solve path, or None."""
    if sclopf:
        return ("island constraints run on the plain solve only; SCOPF re-solves "
                "per contingency and is refused")
    if solve_strategy == "rolling":
        return ("island constraints run on the plain solve only; a rolling-horizon "
                "window cuts the cyclic period the ride-through rows wrap around")
    if solve_strategy == "myopic":
        return ("island constraints run on the plain solve only; myopic foresight "
                "mixes representative future snapshots into the period")
    return None


def load_project_island() -> dict | None:
    """The solving project's validated island sidecar, or None when the
    project has none (or is unsaved)."""
    from services.adequacy.island_config import SIDECAR_NAME, load_island_config
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.solve_target_context()
    storage = getattr(ctx, "storage_dir", None) if ctx is not None else None
    if not storage:
        return None
    return load_island_config(pathlib.Path(storage) / SIDECAR_NAME)


_UNSET = object()


def _wrap_with_island(network, user_fn, cfg, island=_UNSET, log_queue=None,
                      delta_p_plus=None):
    """Compose ``user_fn`` with the island rows when the config opts in.
    ``island`` defaults to the solving project's sidecar."""
    if not getattr(cfg, "island_constraints", False):
        return user_fn
    if island is _UNSET:
        island = load_project_island()
    if island is None:
        raise IslandRefusal(
            "island constraints are on, but the project has no island_config.json "
            "to read the ride-through requirements from")
    return island_extra_functionality(
        island, user_fn=user_fn, delta_p_plus=delta_p_plus,
        emit=lambda msg: _safe_log(log_queue, f"[ISLAND] {msg}"))


def island_extra_functionality(island, *, user_fn=None, delta_p_plus=None, emit=None):
    """The ``extra_functionality`` callback that adds the island rows.

    ``delta_p_plus`` is the ``seamless`` step ΔP⁺ in MW (a number, a Series
    on the snapshots, or ``callable(n, sns)`` returning either); None until
    I2b supplies it."""
    def fn(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        add_island_rows(n, snapshots, island, delta_p_plus=delta_p_plus, emit=emit)
    return fn


# ── helpers ────────────────────────────────────────────────────────────────


def _periods(sns):
    if isinstance(sns, pd.MultiIndex):
        return [(p, sns[sns.get_level_values(0) == p]) for p in sns.unique(level=0)]
    return [(None, sns)]


def _step_hours(sns_p) -> float:
    """The snapshot duration Δ in hours, from the index. A non-uniform step
    is refused: the window length D = sustained_h/Δ needs one Δ."""
    ts = sns_p.get_level_values(-1) if isinstance(sns_p, pd.MultiIndex) else sns_p
    ts = pd.DatetimeIndex(ts)
    if len(ts) < 2:
        raise IslandRefusal("island constraints need at least two snapshots per period")
    steps = np.unique(np.diff(ts.asi8)) / 3.6e12
    if len(steps) != 1:
        raise IslandRefusal("island constraints need evenly spaced snapshots; "
                            f"found steps of {sorted(steps.tolist())} h")
    return float(steps[0])


def _dense(n, component, attr, sns, names):
    if not names:
        return pd.DataFrame(index=sns)
    return n.get_switchable_as_dense(component, attr, sns).reindex(columns=names)


_LIST_NAMES = {"Generator": "generators", "Link": "links", "StorageUnit": "storage_units",
               "Store": "stores"}


def _nominal(n, component, name, attr="p_nom"):
    """(variable or None, fixed value) for one asset's nominal size."""
    static = getattr(n, _LIST_NAMES[component])
    if bool(static.at[name, f"{attr}_extendable"]):
        var = n.model.variables[f"{component}-{attr}"]
        return var.sel(name=name), 0.0
    return None, float(static.at[name, attr])


def _soc_before(n, sns_p, name, cyclic):
    """e(t−1) for one StorageUnit over the period: (expression with a missing
    first term when non-cyclic, the initial SoC for that first row)."""
    soc = n.model.variables["StorageUnit-state_of_charge"].sel(snapshot=sns_p, name=name)
    if cyclic:
        return soc.roll(snapshot=1), 0.0
    return soc.shift(snapshot=1), float(n.storage_units.at[name, "state_of_charge_initial"])


def _window_sum(d, steps, cyclic):
    """Σ_{j<D} d(t+j) for every t."""
    terms = [d.roll(snapshot=-j) if cyclic else d.shift(snapshot=-j) for j in range(steps)]
    return sum(terms[1:], terms[0].to_linexpr())


def _as_series(value, n, sns_p) -> pd.Series:
    if callable(value):
        value = value(n, sns_p)
    if isinstance(value, pd.Series):
        return value.reindex(sns_p).astype(float).fillna(0.0)
    return pd.Series(float(value), index=sns_p)


# The flag `services.adequacy.sweep.freeze_capacities` sets on an operational
# re-solve (= services.commercial.connection.OPERATIONAL_ATTR; not imported).
OPERATIONAL_ATTR = "_ic_operational"
_EXTENDABLE = (("generators", "p_nom"), ("storage_units", "p_nom"), ("stores", "e_nom"),
               ("links", "p_nom"), ("lines", "s_nom"), ("transformers", "s_nom"))


def dispatch_only(n) -> str | None:
    """Why this solve cannot buy capacity, or None for a sizing solve."""
    if getattr(n, OPERATIONAL_ATTR, False):
        return "dispatch-only: operational re-solve"
    for attr, nom in _EXTENDABLE:
        df = getattr(n, attr, None)
        col = f"{nom}_extendable"
        if df is not None and not df.empty and col in df.columns and df[col].astype(bool).any():
            return None
    return "dispatch-only: nothing extendable"


# ── the rows ───────────────────────────────────────────────────────────────


def add_island_rows(n, snapshots, island, *, delta_p_plus=None, emit=None) -> dict:
    """Add (a), (b) and (c) to ``n.model`` and record what was built on
    ``n._island_facts``."""
    from gridspine.schema.island import GFM_CONTROLS, check_horizon

    emit = emit or (lambda msg: None)
    skipped = dispatch_only(n)
    if skipped:
        n._island_facts = {"skipped": skipped}
        emit(f"island rows skipped ({skipped}); they size the site, and this solve cannot")
        return n._island_facts
    m = n.model
    units, req = island["units"], island["requirements"]
    vals = req["values"]
    sns = pd.Index(snapshots) if not isinstance(snapshots, pd.Index) else snapshots

    critical = [u for u, s in units.items() if s["kind"] == "load" and s["critical"]
                and u in n.loads.index]
    gensets = [u for u, s in units.items() if s["kind"] == "genset"
               and (u in n.generators.index or u in n.links.index)]
    rt = [s for s in req["ride_through_storage"] if s in n.storage_units.index]
    gfm = [u for u, s in units.items() if s["kind"] in ("bess",)
           and s.get("control") in GFM_CONTROLS and u in n.storage_units.index]
    upss = [u for u, s in units.items() if s["kind"] == "ups" and u in n.storage_units.index]
    k = int(vals["genset_redundancy_n"])
    seamless = "seamless" in req["scenarios"]

    facts = {"n_plus_k": k, "ride_through_storage": rt, "critical_loads": critical,
             "truncated": False, "periods": {}, "fuel": {"status": "not checked"},
             "seamless_bridge": None}

    # U_max: the largest unit the N+k test takes out.
    ratings = []
    for g in gensets:
        if g in n.generators.index:
            ext = bool(n.generators.at[g, "p_nom_extendable"])
            rating = float(n.generators.at[g, "p_nom"])
        else:
            ext = bool(n.links.at[g, "p_nom_extendable"])
            rating = float(n.links.at[g, "p_nom"]) * float(n.links.at[g, "efficiency"])
        if ext:
            unit_mw = units[g]["values"].get("unit_mw")
            if unit_mw is None:
                raise IslandRefusal(f"extendable genset {g!r} needs unit_mw in the island "
                                    "sidecar, so the N+k test knows the unit it takes out")
            rating = float(unit_mw)
        ratings.append(rating)
    u_max = max(ratings) if ratings else 0.0
    joins_s = max((units[g]["values"]["start_s"] + units[g]["values"]["sync_s"]
                   for g in gensets), default=0.0)

    for period, sns_p in _periods(sns):
        tag = "" if period is None else f"-{period}"
        dt = _step_hours(sns_p)
        span_h = dt * len(sns_p)
        check_horizon(req, span_h)
        steps_f = vals["sustained_h"] / dt
        steps = int(round(steps_f))
        if not math.isclose(steps_f, steps, abs_tol=1e-9):
            raise IslandRefusal(f"sustained_h ({vals['sustained_h']:g} h) is not a whole number "
                                f"of {dt:g} h snapshots")

        l_c = (_dense(n, "Load", "p_set", sns_p, critical).sum(axis=1)
               if critical else pd.Series(0.0, index=sns_p))

        # (a) the deficit d(τ) ≥ L_c(τ) − G(τ), as d + Σ avail·P_var ≥ L_c + kU − Σ avail·P_fixed.
        coord = _snapshot_coord(m, sns_p)
        d = m.add_variables(lower=0.0, coords=[coord], name=f"Island-deficit{tag}")
        lhs = d.to_linexpr()
        rhs = l_c + k * u_max
        for g in gensets:
            comp = "Generator" if g in n.generators.index else "Link"
            avail = _dense(n, comp, "p_max_pu", sns_p, [g])[g]
            if comp == "Link":
                avail = avail * float(n.links.at[g, "efficiency"])
            var, fixed = _nominal(n, comp, g)
            rhs = rhs - avail * fixed
            if var is not None:
                lhs = lhs + var * xr_series(avail, coord)
        m.add_constraints(lhs >= xr_series(rhs, coord), name=f"Island-deficit-lower{tag}")

        # (a) power: d(τ) ≤ Σ_{s∈RT} p_nom_s.
        p_lhs, p_fixed = d.to_linexpr(), 0.0
        for s in rt:
            var, fixed = _nominal(n, "StorageUnit", s)
            p_fixed += fixed
            if var is not None:
                p_lhs = p_lhs - var
        m.add_constraints(p_lhs <= p_fixed, name=f"Island-power{tag}")

        # B(t): the seamless GFM bridge.
        bridge = pd.Series(0.0, index=sns_p)
        if seamless and gfm:
            if delta_p_plus is None:
                facts["seamless_bridge"] = "zero until I2b supplies the islanding step"
            else:
                bridge = _as_series(delta_p_plus, n, sns_p).clip(lower=0.0) * joins_s / 3600.0
                facts["seamless_bridge"] = "held"

        # (a) energy: Σ η·e(t−1) − Σ_window d·Δ ≥ B(t) for storage in RT ∩ GFM.
        cyclic_all = all(bool(n.storage_units.at[s, "cyclic_state_of_charge"]) for s in rt) \
            if rt else True
        if not cyclic_all and steps > 1:
            facts["truncated"] = True
        e_lhs = -dt * _window_sum(d, steps, cyclic_all)
        e_rhs = pd.Series(0.0, index=sns_p)
        for s in rt:
            eta = float(n.storage_units.at[s, "efficiency_dispatch"])
            cyc = bool(n.storage_units.at[s, "cyclic_state_of_charge"])
            soc, init = _soc_before(n, sns_p, s, cyc)
            e_lhs = e_lhs + eta * soc
            if not cyc:
                e_rhs.iloc[0] -= eta * init
        in_row = [s for s in gfm if s in rt]
        if in_row:
            e_rhs = e_rhs + bridge
        m.add_constraints(e_lhs >= xr_series(e_rhs, coord), name=f"Island-energy{tag}")

        # B(t) for GFM storage outside RT: its own row.
        own = [s for s in gfm if s not in rt]
        if own and bridge.gt(0).any():
            _reserve_row(n, m, sns_p, coord, own, bridge, f"Island-gfm-bridge{tag}")

        # (b) every UPS holds its IT load for bridge_s.
        for u in upss:
            it = [x for x in units[u]["it_load"] if x in n.loads.index]
            p_it = (_dense(n, "Load", "p_set", sns_p, it).sum(axis=1)
                    if it else pd.Series(0.0, index=sns_p))
            _reserve_row(n, m, sns_p, coord, [u], p_it * vals["bridge_s"] / 3600.0,
                         f"Island-ups-bridge-{u}{tag}")

        facts["periods"][str(period)] = {
            "delta_h": dt, "window_snapshots": steps,
            "critical_peak_mw": float(l_c.max()) if len(l_c) else 0.0,
            "u_max_mw": u_max}

    facts["fuel"] = _fuel(n, m, sns, units, gensets, vals, critical)
    n._island_facts = facts
    emit(f"N+{k} ride-through over {vals['sustained_h']:g} h from every hour; "
         f"storage {rt or 'none'}; fuel {facts['fuel']['status']}.")
    return facts


def _snapshot_coord(m, sns_p):
    """The model's own ``snapshot`` coordinate for the period, so new
    variables and constants align with PyPSA's (a MultiIndex with investment
    periods)."""
    for name in m.variables:
        var = m.variables[name]
        if "snapshot" in var.dims:
            return var.sel(snapshot=sns_p).indexes["snapshot"]
    raise IslandRefusal("island constraints need a model with snapshot variables")


def xr_series(s: pd.Series, coord):
    """A Series on the period's snapshots as a DataArray on ``coord``."""
    import xarray as xr
    return xr.DataArray(s.to_numpy(dtype=float), coords=[coord], dims=["snapshot"])


def _reserve_row(n, m, sns_p, coord, names, need: pd.Series, cname):
    """Σ η·e(t−1) ≥ need(t) for the named StorageUnits."""
    lhs, rhs = None, need.copy()
    for s in names:
        eta = float(n.storage_units.at[s, "efficiency_dispatch"])
        cyc = bool(n.storage_units.at[s, "cyclic_state_of_charge"])
        soc, init = _soc_before(n, sns_p, s, cyc)
        term = eta * soc
        lhs = term if lhs is None else lhs + term
        if not cyc:
            rhs.iloc[0] -= eta * init
    m.add_constraints(lhs >= xr_series(rhs, coord), name=cname)


def _fuel(n, m, sns, units, gensets, vals, critical) -> dict:
    """(c): a fuel Store's e_nom covers the critical peak for fuel_autonomy_h."""
    hours = vals.get("fuel_autonomy_h")
    fed = [g for g in gensets if g in n.links.index]
    if not fed:
        return {"status": "not checked",
                "reason": "the gensets have no fuel bus" if gensets else "no gensets"}
    if hours is None:
        return {"status": "not requested"}
    peak = (_dense(n, "Load", "p_set", sns, critical).sum(axis=1).max() if critical else 0.0)
    out = {"status": "row", "buses": {}}
    for bus in sorted({n.links.at[g, "bus0"] for g in fed}):
        eta = min(float(n.links.at[g, "efficiency"]) for g in fed if n.links.at[g, "bus0"] == bus)
        need = float(peak) * float(hours) / eta
        stores = n.stores.index[n.stores.bus == bus].tolist()
        if not stores:
            out["buses"][bus] = {"need_mwh": need, "status": "no store"}
            out["status"] = "fail"
            continue
        lhs, fixed = None, 0.0
        for s in stores:
            var, val = _nominal(n, "Store", s, attr="e_nom")
            fixed += val
            if var is not None:
                lhs = var if lhs is None else lhs + var
        if lhs is None:
            ok = fixed + 1e-9 >= need
            out["buses"][bus] = {"need_mwh": need, "have_mwh": fixed,
                                 "status": "pass" if ok else "fail"}
            if not ok:
                out["status"] = "fail"
            continue
        m.add_constraints(lhs >= need - fixed, name=f"Island-fuel-{bus}")
        out["buses"][bus] = {"need_mwh": need, "status": "row"}
    return out

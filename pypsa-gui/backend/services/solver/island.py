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
``snapshot_weightings``, which is 52.14 on the hub templates):

**(a) Sustained ride-through.** RT is ``ride_through_storage``, L_c the
critical load, k ``genset_redundancy_n``, U_max the largest genset rating
(a fixed unit's p_nom, an extendable one's ``unit_mw``):

    G(τ)  = Σ_g avail_g(τ)·p_nom_g − k·U_max
    d(τ) ≥ L_c(τ) − G(τ),   d(τ) ≥ 0,   d(τ) ≤ Σ_{s∈RT} p_nom_s
    Σ_{s∈RT} η_dis,s·e_s(t−1) ≥ Σ_{τ=t}^{t+D−1} d(τ)·Δ + B(t)   for every t

e(t−1) is the state of charge at the end of the hour before the start, so
the reserve is what the site holds when the outage begins. Windows follow
the storage's own setting: cyclic wraps to the period's first snapshot;
non-cyclic stops at the period end, which is flagged. e(t−1) follows PyPSA's
own SoC linkage, unit by unit: within the period when
``cyclic_state_of_charge_per_period`` / ``state_of_charge_initial_per_period``
say so, otherwise carried across periods (PyPSA's default), and
``state_of_charge_initial`` only where PyPSA starts from it. Each period
counts only the gensets, storage and UPSs active in it (build year and
lifetime), as the reserve margin does.

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
sizing standard (plan step 1): when nothing they can size (a genset, a
ride-through or GFM storage, a UPS, a fuel store) is extendable, or on an
operational re-solve that pins capacities (``_ic_operational``, set by the
FMEA sweep's ``freeze_capacities``; the sidecar is not even read then), they
could only make an as-built dispatch infeasible. The skip is logged and
recorded as ``skipped`` in the facts.

**Where it runs.** The plain LOPF only, judged as the solve will actually
run: a power flow builds no LP and passes through; rolling horizon, SCOPF
(with branches selected) and myopic foresight are refused with a reason,
because their windows cut the cyclic period the reserve rows wrap around.
Every refusal, a sidecar that fails validation included, is an
``IslandRefusal``.
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
    from services.adequacy.island_config import ContractError
    try:
        return load_island_config(pathlib.Path(storage) / SIDECAR_NAME)
    except ContractError as exc:
        raise IslandRefusal(f"{SIDECAR_NAME} is not a valid island config: {exc}") from exc


_UNSET = object()


def _effective_path(network, cfg) -> dict:
    """The strategy and SCOPF flag the solve will actually run with: SCOPF
    falls back to the plain LP with no branch selected, rolling falls back to
    full with SCOPF or investment periods, and myopic needs investment
    periods (``run_simulation``'s own dispatch)."""
    from services.commercial.lp_bindings import effective_strategy
    from services.solver.assumptions import resolve_branch_outages

    multi = bool(getattr(cfg, "multi_investment_periods", False))
    sclopf = bool(getattr(cfg, "sclopf", False)) and bool(resolve_branch_outages(network, cfg))
    strategy = effective_strategy(getattr(cfg, "solve_strategy", "full"),
                                  sclopf=sclopf, multi_period=multi)
    if strategy == "myopic" and not multi:
        strategy = "full"
    return {"solve_strategy": strategy, "sclopf": sclopf}


def _wrap_with_island(network, user_fn, cfg, island=_UNSET, log_queue=None,
                      delta_p_plus=None):
    """Compose ``user_fn`` with the island rows when the config opts in.
    ``island`` defaults to the solving project's sidecar."""
    if not getattr(cfg, "island_constraints", False):
        return user_fn
    if getattr(cfg, "mode", "lopf") != "lopf":
        return user_fn                      # a power flow builds no LP
    if getattr(network, OPERATIONAL_ATTR, False):
        return user_fn                      # skipped anyway; no sidecar read per re-solve
    why = strategy_refusal(**_effective_path(network, cfg))
    if why:
        raise IslandRefusal(why)
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


def _soc_before(n, sns, sns_p, name):
    """e(t−1) for one StorageUnit over the period, linked as PyPSA links its
    SoC: (expression with a missing first term where the SoC starts from
    ``state_of_charge_initial``, that initial value or 0)."""
    su = n.storage_units
    cyclic = bool(su.at[name, "cyclic_state_of_charge"])
    within = isinstance(sns, pd.MultiIndex) and bool(
        su.at[name, "cyclic_state_of_charge_per_period"] if cyclic
        else su.at[name, "state_of_charge_initial_per_period"])
    var = n.model.variables["StorageUnit-state_of_charge"]
    init = 0.0 if cyclic else float(su.at[name, "state_of_charge_initial"])
    if within or not isinstance(sns, pd.MultiIndex):
        soc = var.sel(snapshot=sns_p, name=name)
        return (soc.roll(snapshot=1) if cyclic else soc.shift(snapshot=1)), init
    # Carried across periods: shift over the whole horizon, then take the
    # period. Only the horizon's first snapshot starts from the initial SoC.
    soc = var.sel(snapshot=sns, name=name)
    soc = (soc.roll(snapshot=1) if cyclic else soc.shift(snapshot=1)).sel(snapshot=sns_p)
    starts_here = sns[0] == sns_p[0]
    return soc, (init if starts_here else 0.0)


def _window_sum(d, steps, cyclic):
    """Σ_{j<D} d(t+j) for every t, built in one merge (not D chained sums)."""
    from linopy import merge

    terms = [(d.roll(snapshot=-j) if cyclic else d.shift(snapshot=-j)).to_linexpr()
             for j in range(steps)]
    return terms[0] if steps == 1 else merge(terms, dim="_term")


def _as_series(value, n, sns_p) -> pd.Series:
    if callable(value):
        value = value(n, sns_p)
    if isinstance(value, pd.Series):
        if isinstance(sns_p, pd.MultiIndex) and not isinstance(value.index, pd.MultiIndex):
            # A Series on plain timestamps applies to every period.
            out = value.reindex(sns_p.get_level_values(-1)).to_numpy(dtype=float)
            return pd.Series(out, index=sns_p).fillna(0.0)
        return value.reindex(sns_p).astype(float).fillna(0.0)
    return pd.Series(float(value), index=sns_p)


# The flag `services.adequacy.sweep.freeze_capacities` sets on an operational
# re-solve (= services.commercial.connection.OPERATIONAL_ATTR; not imported).
OPERATIONAL_ATTR = "_ic_operational"


def _sizable(n, island) -> list[tuple[str, str]]:
    """(frame, name) of every asset the rows can size: the gensets, the
    ride-through, GFM and UPS storage, and the Stores on a genset's fuel bus."""
    units, req = island["units"], island["requirements"]
    out = []
    for u, s in units.items():
        if s["kind"] == "genset":
            out += [(f, u) for f in ("generators", "links") if u in getattr(n, f).index]
        elif s["kind"] in ("bess", "ups") and u in n.storage_units.index:
            out.append(("storage_units", u))
    out += [("storage_units", s) for s in req["ride_through_storage"] if s in n.storage_units.index]
    fuel_buses = {n.links.at[u, "bus0"] for f, u in out if f == "links"}
    out += [("stores", s) for s in n.stores.index[n.stores.bus.isin(fuel_buses)]]
    return out


def dispatch_only(n, island) -> str | None:
    """Why this solve cannot buy the reserve, or None for a sizing solve."""
    if getattr(n, OPERATIONAL_ATTR, False):
        return "dispatch-only: operational re-solve"
    for frame, name in _sizable(n, island):
        col = "e_nom_extendable" if frame == "stores" else "p_nom_extendable"
        if bool(getattr(n, frame).at[name, col]):
            return None
    return "dispatch-only: nothing the rows can size is extendable"


# ── the rows ───────────────────────────────────────────────────────────────


def add_island_rows(n, snapshots, island, *, delta_p_plus=None, emit=None) -> dict:
    """Add (a), (b) and (c) to ``n.model`` and record what was built on
    ``n._island_facts``."""
    # gridspine through the guarded seam (a build without it then says so).
    from services.adequacy.activity import active_mask
    from services.adequacy.island_config import GFM_CONTROLS

    emit = emit or (lambda msg: None)
    skipped = dispatch_only(n, island)
    if skipped:
        n._island_facts = {"skipped": skipped}
        emit(f"island rows skipped ({skipped}); they size the site, and this solve cannot")
        return n._island_facts
    m = n.model
    units, req = island["units"], island["requirements"]
    vals = req["values"]
    sns = pd.Index(snapshots) if not isinstance(snapshots, pd.Index) else snapshots

    # Names the sidecar gives that the network lacks are left out, warned and
    # recorded: a genset the network does not have would otherwise drop G
    # silently and size the storage for the whole critical load.
    missing: dict[str, list[str]] = {}

    def present(kind, names, frames):
        have = [x for x in names if any(x in getattr(n, f).index for f in frames)]
        gone = [x for x in names if x not in have]
        if gone:
            missing[kind] = gone
        return have

    critical = present("load", [u for u, s in units.items()
                                if s["kind"] == "load" and s["critical"]], ("loads",))
    gensets = present("genset", [u for u, s in units.items() if s["kind"] == "genset"],
                      ("generators", "links"))
    rt = present("ride_through_storage", list(req["ride_through_storage"]), ("storage_units",))
    gfm = present("bess", [u for u, s in units.items() if s["kind"] == "bess"
                           and s.get("control") in GFM_CONTROLS], ("storage_units",))
    upss = present("ups", [u for u, s in units.items() if s["kind"] == "ups"],
                   ("storage_units",))
    if missing:
        emit(f"island_config.json names {missing}, which this network does not have; "
             "they are left out of the rows")
    k = int(vals["genset_redundancy_n"])
    seamless = "seamless" in req["scenarios"]

    facts = {"n_plus_k": k, "ride_through_storage": rt, "critical_loads": critical,
             "truncated": False, "periods": {}, "fuel": {"status": "not checked"},
             "seamless_bridge": None, "missing": missing}

    # The unit the N+k test takes out of each genset: its sidecar unit_mw
    # (a fleet modelled as one asset), else a fixed unit's own rating. An
    # extendable genset has no rating of its own, so it needs unit_mw.
    unit_rating = {}
    for g in gensets:
        comp = "Generator" if g in n.generators.index else "Link"
        frame = n.generators if comp == "Generator" else n.links
        unit_mw = units[g]["values"].get("unit_mw")
        if unit_mw is not None:
            unit_rating[g] = float(unit_mw)
        elif bool(frame.at[g, "p_nom_extendable"]):
            raise IslandRefusal(f"extendable genset {g!r} needs unit_mw in the island "
                                "sidecar, so the N+k test knows the unit it takes out")
        else:
            eff = float(n.links.at[g, "efficiency"]) if comp == "Link" else 1.0
            unit_rating[g] = float(frame.at[g, "p_nom"]) * eff
    joins_s = max((units[g]["values"]["start_s"] + units[g]["values"]["sync_s"]
                   for g in gensets), default=0.0)

    def active(names, comp_of, period):
        label = "ALL" if period is None else period
        out = []
        for x in names:
            comp = comp_of(x)
            mask = active_mask(n, comp, label)
            if bool(mask.get(x, True)):
                out.append(x)
        return out

    def genset_comp(g):
        return "Generator" if g in n.generators.index else "Link"

    for period, sns_p in _periods(sns):
        g_p = active(gensets, genset_comp, period)
        rt_p = active(rt, lambda _: "StorageUnit", period)
        gfm_p = active(gfm, lambda _: "StorageUnit", period)
        ups_p = active(upss, lambda _: "StorageUnit", period)
        u_max = max((unit_rating[g] for g in g_p), default=0.0)
        tag = "" if period is None else f"-{period}"
        dt = _step_hours(sns_p)
        span_h = dt * len(sns_p)
        if vals["sustained_h"] > span_h + 1e-9:
            raise IslandRefusal(f"sustained_h ({vals['sustained_h']:g} h) is longer than the "
                                f"period ({span_h:g} h) the ride-through is checked over")
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
        for g in g_p:
            comp = genset_comp(g)
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
        for s in rt_p:
            var, fixed = _nominal(n, "StorageUnit", s)
            p_fixed += fixed
            if var is not None:
                p_lhs = p_lhs - var
        m.add_constraints(p_lhs <= p_fixed, name=f"Island-power{tag}")

        # B(t): the seamless GFM bridge.
        bridge = pd.Series(0.0, index=sns_p)
        if seamless and gfm_p:
            if delta_p_plus is None:
                facts["seamless_bridge"] = "zero until I2b supplies the islanding step"
            else:
                bridge = _as_series(delta_p_plus, n, sns_p).clip(lower=0.0) * joins_s / 3600.0
                if bridge.gt(0).any():
                    facts["seamless_bridge"] = "held"
                elif facts["seamless_bridge"] is None:
                    facts["seamless_bridge"] = "zero step"

        # (a) energy: Σ η·e(t−1) − Σ_window d·Δ ≥ B(t) for storage in RT ∩ GFM.
        cyclic_all = all(bool(n.storage_units.at[s, "cyclic_state_of_charge"]) for s in rt_p) \
            if rt_p else True
        if not cyclic_all and steps > 1:
            facts["truncated"] = True
        e_lhs = -dt * _window_sum(d, steps, cyclic_all)
        e_rhs = pd.Series(0.0, index=sns_p)
        for s in rt_p:
            eta = float(n.storage_units.at[s, "efficiency_dispatch"])
            soc, init = _soc_before(n, sns, sns_p, s)
            e_lhs = e_lhs + eta * soc
            e_rhs.iloc[0] -= eta * init
        in_row = [s for s in gfm_p if s in rt_p]
        if in_row:
            e_rhs = e_rhs + bridge
        m.add_constraints(e_lhs >= xr_series(e_rhs, coord), name=f"Island-energy{tag}")

        # B(t) for GFM storage outside RT: its own row.
        own = [s for s in gfm_p if s not in rt_p]
        if own and bridge.gt(0).any():
            _reserve_row(n, m, sns, sns_p, coord, own, bridge, f"Island-gfm-bridge{tag}")

        # (b) every UPS holds its IT load for bridge_s.
        for u in ups_p:
            it = [x for x in units[u]["it_load"] if x in n.loads.index]
            p_it = (_dense(n, "Load", "p_set", sns_p, it).sum(axis=1)
                    if it else pd.Series(0.0, index=sns_p))
            _reserve_row(n, m, sns, sns_p, coord, [u], p_it * vals["bridge_s"] / 3600.0,
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


def _reserve_row(n, m, sns, sns_p, coord, names, need: pd.Series, cname):
    """Σ η·e(t−1) ≥ need(t) for the named StorageUnits."""
    lhs, rhs = None, need.copy()
    for s in names:
        eta = float(n.storage_units.at[s, "efficiency_dispatch"])
        soc, init = _soc_before(n, sns, sns_p, s)
        term = eta * soc
        lhs = term if lhs is None else lhs + term
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

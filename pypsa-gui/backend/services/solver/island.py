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
its own row. ΔP⁺ is I2b's step below, as a linear expression, unless a
caller passes ``delta_p_plus``. B carries max(ΔP⁺, 0) through a variable
b ≥ ΔP⁺, b ≥ 0 in the same row. The UPS bridge applies when islanding is
unplanned (``seamless`` or ``ups_bridged``), not to a ``planned`` transfer.

**(c) Fuel (Q1).** Where a genset is a Link fed from a bus with Stores:
Σ e_nom ≥ L_c,peak·fuel_autonomy_h/η_genset (the draw at the critical peak,
conservative). Gensets that are Generators have no fuel bus: "not checked".

**The instant of islanding (I2b).** The PCC import, site side, is
p_imp = η·p0 over the Links tagged ``eh_role=grid_import`` less p0 over
those tagged ``grid_export``; ``seamless`` without a tagged import Link is
refused. "Late" UPSs transfer and walk in no earlier than the slowest genset
joins (``walk_in_s`` ≥ ``start_s + sync_s``):

    ΔP⁺(t) = p_imp − Σ_late X_u,   0 ≤ X_u ≤ L_it,u,  X_u ≤ p_nom_u
    ΔP⁻(t) = −p_imp + Σ_transferring L_it,u     (at 0⁺; owner, 2026-10-13)
    ΔP⁻_j(t) = −p_imp + Σ_late L_it,u           (once the gensets have joined)

X_u enters only rows a larger X loosens, so it settles at min(L_it, p_nom_u).
By scenario (the plan's matrix): every scenario gets the steady-island GFM
MVA row (GFM s_nom plus every genset's) and the spinning minimum load
p_g ≥ p_min_pu·p_nom_g. ``seamless`` adds upward headroom (GFM storage plus
spinning gensets ≥ ΔP⁺), downward power (GFM p_s + p_nom ≥ ΔP⁻), SoC room
(GFM max_hours·p_nom − e(t−1) ≥ ΔP⁻·t_curt/3600, t_curt from the ``gfl_fw``
units; none, and the exporting hours are reported by
``not_islandable_hours`` instead), the GFM MVA at 0⁺ (GFM plus spinning
gensets), RoCoF 2M ≥ f0·ΔP/RoCoF_max on both steps, and the quasi-steady
state ΣK·Δf_qss ≥ ΔP⁺, ΔP⁻_j. ``ups_bridged`` adds the pickup row
Σ ls·p_nom − k·max(ls·U) ≥ max block, RoCoF on the largest block from the
gensets' inertia at N+k, and ΣK on that block. M sums H·s_nom over spinning
gensets, H_v·s_nom over VSMs and τ_f·p_nom/(2R) over droop GFMs; ΣK sums
p_nom/(R·f0) over GFMs and droop gensets, less the k largest genset units'.
RoCoF reads the ``transition`` limits and the qss band the
``steady_island`` ones; f0 is 50 Hz. A row with no variable that fails is a
refusal naming it, not an infeasible LP.

**Ledgered:** PV is left out (conservative); the site is a copperplate (the
hub's one-way site transformer is not seen here; I4 catches it at the island
hours); U_max over-subtracts when an extendable genset is not built
(conservative); the occurrence derating is not applied, since N+k covers
outages. I2b: only ``spinning`` gensets are online at 0⁺, whatever they
dispatch (linearised UC is I2c); isochronous gensets add no droop gain
(conservative); the instantaneous RoCoF at 0⁺ is stricter than I6's window;
f0 is fixed at 50 Hz.

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

    ``delta_p_plus`` overrides the ``seamless`` step ΔP⁺ in MW (a number, a
    Series on the snapshots, or ``callable(n, sns)`` returning either); None,
    the default, builds it from the PCC Links and the late UPSs (I2b)."""
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
    scenarios = set(req["scenarios"])
    fw = present("gfl_fw", [u for u, s in units.items() if s.get("control") == "gfl_fw"],
                 ("generators", "storage_units", "links"))
    pcc = _pcc_links(n)
    if "seamless" in scenarios and delta_p_plus is None and not pcc["import"]:
        raise IslandRefusal(
            "the seamless scenario reads the import at the PCC from the Link tagged "
            "eh_role=grid_import, and this network tags none; tag the import Link")

    facts = {"n_plus_k": k, "ride_through_storage": rt, "critical_loads": critical,
             "truncated": False, "periods": {}, "fuel": {"status": "not checked"},
             "seamless_bridge": None, "missing": missing, "surplus": None,
             "instant": {"scenarios": sorted(scenarios), "f0_hz": F0_HZ, "rows": [],
                         "late_ups": [], "pcc": pcc}}

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

        # The instant of islanding (I2b): ΔP⁺ and ΔP⁻, and the rows the
        # scenarios ask for. B(t) comes back as a linear quantity.
        joins_p = max((units[g]["values"]["start_s"] + units[g]["values"]["sync_s"]
                       for g in g_p), default=0.0)
        bridge = _instant_rows(
            n, m, sns, sns_p, coord, tag, period, island=island, gensets=g_p, gfm=gfm_p,
            upss=ups_p, l_c=l_c, unit_rating=unit_rating, joins_s=joins_p, k=k,
            links=pcc, fw_units=fw, delta_p_plus=delta_p_plus, facts=facts, active=active)

        # (a) energy: Σ η·e(t−1) − Σ_window d·Δ ≥ B(t) for storage in RT ∩ GFM.
        cyclic_all = all(bool(n.storage_units.at[s, "cyclic_state_of_charge"]) for s in rt_p) \
            if rt_p else True
        if not cyclic_all and steps > 1:
            facts["truncated"] = True
        e_lhs = _Lin(sns_p).var(-dt * _window_sum(d, steps, cyclic_all))
        for s in rt_p:
            _add_soc_before(e_lhs, n, sns, sns_p, s,
                            float(n.storage_units.at[s, "efficiency_dispatch"]))
        in_row = [s for s in gfm_p if s in rt_p]
        e_rhs = bridge if in_row else _Lin(sns_p)
        _row(m, e_lhs, e_rhs, f"Island-energy{tag}", coord)

        # B(t) for GFM storage outside RT: its own row.
        own = [s for s in gfm_p if s not in rt_p]
        if own and not bridge.is_zero():
            _reserve_row(n, m, sns, sns_p, coord, own, bridge, f"Island-gfm-bridge{tag}")

        # (b) every UPS holds its IT load for bridge_s, when islanding is
        # unplanned (the matrix: a planned transfer needs no bridge).
        for u in (ups_p if scenarios & {"seamless", "ups_bridged"} else ()):
            need = _it_load(n, units, u, sns_p) * vals["bridge_s"] / 3600.0
            _reserve_row(n, m, sns, sns_p, coord, [u], _Lin(sns_p).plus(need),
                         f"Island-ups-bridge-{u}{tag}")

        facts["periods"][str(period)] = {
            "delta_h": dt, "window_snapshots": steps,
            "critical_peak_mw": float(l_c.max()) if len(l_c) else 0.0,
            "u_max_mw": u_max}

    facts["fuel"] = _fuel(n, m, sns, units, gensets, vals, critical)
    n._island_facts = facts
    emit(f"N+{k} ride-through over {vals['sustained_h']:g} h from every hour; "
         f"storage {rt or 'none'}; fuel {facts['fuel']['status']}; islanding "
         f"{'/'.join(sorted(scenarios))}: {len(facts['instant']['rows'])} instant rows"
         + (f"; surplus {facts['surplus']['status']}" if facts["surplus"] else "") + ".")
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


class _Lin:
    """A linear quantity Σ coef·variable + const, over one period's
    snapshots (``idx``) or static (``idx`` None). Rows are built from these
    so a side with no variable at all is seen, and checked, before linopy
    gets it."""

    def __init__(self, idx=None):
        self.idx = idx
        self.terms: list = []
        self.const = pd.Series(0.0, index=idx) if idx is not None else 0.0

    def var(self, v, coef=1.0, coord=None):
        """Add ``coef``·``v`` (a linopy Variable or expression); a Series
        ``coef`` needs the period's ``coord``."""
        if isinstance(coef, pd.Series):
            coef = xr_series(coef, coord)
        elif coef == 0:
            return self
        self.terms.append(v * coef)
        return self

    def plus(self, c):
        self.const = self.const + c
        return self

    def add(self, other, sign=1.0):
        self.terms += [t * sign for t in other.terms]
        self.const = self.const + sign * other.const
        return self

    def scaled(self, c):
        out = _Lin(self.idx)
        out.terms = [t * c for t in self.terms]
        out.const = self.const * c
        return out

    def is_zero(self) -> bool:
        return not self.terms and not np.any(np.asarray(self.const) != 0)


def _row(m, lhs: _Lin, rhs: _Lin, name, coord, refuse=None, facts=None) -> bool:
    """Add lhs ≥ rhs. A row with no variable on either side is a fixed
    check: when it holds it is dropped, and when it fails nothing this solve
    can size changes it, so it is an ``IslandRefusal`` (``refuse`` names the
    row) rather than an infeasible LP."""
    terms = lhs.terms + [t * -1 for t in rhs.terms]
    const = rhs.const - lhs.const                     # Σ terms ≥ const
    if not terms:
        short = float(np.max(np.asarray(const, dtype=float))) if np.size(const) else 0.0
        if short > 1e-6:
            raise IslandRefusal(f"{refuse or name}: short by {short:.4g} with nothing this "
                                "solve can size")
        return False
    expr = terms[0]
    for t in terms[1:]:
        expr = expr + t
    rhs_c = xr_series(const, coord) if isinstance(const, pd.Series) else float(const)
    m.add_constraints(expr >= rhs_c, name=name)
    if facts is not None:
        facts["instant"]["rows"].append(name)
    return True


def _add_soc_before(lin: _Lin, n, sns, sns_p, name, coef):
    """lin += coef·e(t−1) for one StorageUnit (its initial SoC as a constant
    where PyPSA starts from it)."""
    soc, init = _soc_before(n, sns, sns_p, name)
    lin.var(soc, coef)
    lin.const.iloc[0] += coef * init
    return lin


def _reserve_row(n, m, sns, sns_p, coord, names, need: _Lin, cname):
    """Σ η·e(t−1) ≥ need(t) for the named StorageUnits."""
    lhs = _Lin(sns_p)
    for s in names:
        _add_soc_before(lhs, n, sns, sns_p, s, float(n.storage_units.at[s, "efficiency_dispatch"]))
    _row(m, lhs, need, cname, coord)


def _it_load(n, units, u, sns_p) -> pd.Series:
    """L_it,u(t): the IT load a UPS protects."""
    it = [x for x in units[u]["it_load"] if x in n.loads.index]
    return (_dense(n, "Load", "p_set", sns_p, it).sum(axis=1)
            if it else pd.Series(0.0, index=sns_p))


# ── the instant of islanding (I2b) ─────────────────────────────────────────

#: The nominal frequency the RoCoF and droop rows use. The hub carries none;
#: the templates are Continental European (ledgered).
F0_HZ = 50.0

#: ``eh_role`` tags of the PCC Links (``results.physical_quantities.POC_ROLES``
#: for the import side; the export side is ``commercial.binding``'s tag).
_IMPORT_ROLES = frozenset({"grid_import", "eh_import", "import"})
_EXPORT_ROLES = frozenset({"grid_export"})


def _pcc_links(n) -> dict:
    """The Links at the PCC, by their ``eh_role`` tag."""
    if "eh_role" not in n.links.columns:
        return {"import": [], "export": []}
    role = n.links["eh_role"].map(lambda r: "" if r is None or r != r else str(r).strip())
    return {"import": [str(x) for x in n.links.index[role.isin(_IMPORT_ROLES)]],
            "export": [str(x) for x in n.links.index[role.isin(_EXPORT_ROLES)]]}


def _genset_out(n, g):
    """(component, output-per-p_nom factor): a Link genset's p_nom is on its
    fuel side, so its electrical output is efficiency·p."""
    if g in n.generators.index:
        return "Generator", 1.0
    return "Link", float(n.links.at[g, "efficiency"])


def _add_nominal(lin: _Lin, n, comp, name, coef):
    """lin += coef·p_nom (a variable when extendable)."""
    var, fixed = _nominal(n, comp, name)
    if var is not None:
        lin.var(var, coef)
    return lin.plus(coef * fixed)


def _instant_rows(n, m, sns, sns_p, coord, tag, period, *, island, gensets, gfm, upss, l_c,
                  unit_rating, joins_s, k, links, fw_units, delta_p_plus, facts, active):
    """The rows of the islanding instant for one period (I2b), by the
    scenario matrix; returns B(t), the seamless GFM bridge energy in MWh."""
    units, req = island["units"], island["requirements"]
    vals, scenarios = req["values"], set(req["scenarios"])
    limits = req["frequency_limits"]
    rocof_max = limits["transition"]["values"]["rocof_hz_per_s"]
    df_qss = limits["steady_island"]["values"]["qss_band_hz"]
    margin = 1.0 + vals["gfm_margin"]
    seamless, bridged = "seamless" in scenarios, "ups_bridged" in scenarios
    sv = lambda name: m.variables[name]                                  # noqa: E731

    def spec(x):
        return units[x]["values"]

    spinning = [g for g in gensets if units[g]["spinning"]]
    droop_gensets = [g for g in gensets if units[g]["governor"] == "droop"]

    def p_out(lin, g, coef):
        comp, eta = _genset_out(n, g)
        lin.var(sv(f"{comp}-p").sel(snapshot=sns_p, name=g), coef * eta)
        return lin

    def nom_out(lin, g, coef):
        comp, eta = _genset_out(n, g)
        return _add_nominal(lin, n, comp, g, coef * eta)

    def p_store(lin, s, coef):
        """lin += coef·(p_dispatch − p_store)."""
        lin.var(sv("StorageUnit-p_dispatch").sel(snapshot=sns_p, name=s), coef)
        lin.var(sv("StorageUnit-p_store").sel(snapshot=sns_p, name=s), -coef)
        return lin

    # The spinning minimum load (every scenario): p_g ≥ p_min_pu·p_nom_g,
    # so spinning headroom is never free.
    for g in spinning:
        lhs = p_out(_Lin(sns_p), g, 1.0)
        rhs = nom_out(_Lin(sns_p), g, spec(g)["p_min_pu"])
        _row(m, lhs, rhs, f"Island-min-load-{g}{tag}", coord, facts=facts)

    # (c) GFM MVA in the steady island, every scenario: the GFMs and every
    # genset once joined.
    mva_steady = _Lin(sns_p)
    for s in gfm:
        _add_nominal(mva_steady, n, "StorageUnit", s, 1.0 / spec(s)["pf_rated"])
    for g in gensets:
        nom_out(mva_steady, g, 1.0 / spec(g)["cos_phi_r"])
    _row(m, mva_steady, _Lin(sns_p).plus(margin * l_c), f"Island-mva-steady{tag}", coord,
         refuse="grid-forming MVA in the steady island (GFM storage and gensets) below "
                "(1 + gfm_margin) × the critical load", facts=facts)

    # ΣK: the droop gains once the gensets have joined, less the k largest
    # genset units' (K = P_n/(R·f0), R on P_n).
    k_sum = _Lin()
    for s in gfm:
        _add_nominal(k_sum, n, "StorageUnit", s, 1.0 / (spec(s)["droop_pct"] / 100.0 * F0_HZ))
    for g in droop_gensets:
        nom_out(k_sum, g, 1.0 / (spec(g)["droop_pct"] / 100.0 * F0_HZ))
    k_max = max((unit_rating[g] / (spec(g)["droop_pct"] / 100.0 * F0_HZ)
                 for g in droop_gensets), default=0.0)
    k_sum.plus(-k * k_max)

    bridge = _Lin(sns_p)
    if seamless:
        bridge = _seamless_rows(
            n, m, sns, sns_p, coord, tag, units=units, gensets=gensets, spinning=spinning,
            gfm=gfm, upss=upss, l_c=l_c, joins_s=joins_s, links=links, fw_units=fw_units,
            delta_p_plus=delta_p_plus, facts=facts, active=active, period=period,
            p_out=p_out, nom_out=nom_out, p_store=p_store, k_sum=k_sum, margin=margin,
            rocof_max=rocof_max, df_qss=df_qss)

    if bridged:
        block = max(vals["pickup_blocks"])
        # (b■) the started gensets pick up the largest block, at N+k.
        ls = {g: spec(g)["load_step_max_pct"] / 100.0 for g in gensets}
        pick = _Lin()
        for g in gensets:
            nom_out(pick, g, ls[g])
        pick.plus(-k * max((ls[g] * unit_rating[g] for g in gensets), default=0.0))
        _row(m, pick, _Lin().plus(block), f"Island-pickup{tag}", coord,
             refuse="genset pickup blocks: the gensets' load step at N+k is below the "
                    "largest pickup block", facts=facts)
        # (d) RoCoF on the largest block: the started gensets' inertia, N+k.
        inertia = _Lin()
        for g in gensets:
            nom_out(inertia, g, 2.0 * spec(g)["H_s"] / spec(g)["cos_phi_r"])
        inertia.plus(-2.0 * k * max((spec(g)["H_s"] * unit_rating[g] / spec(g)["cos_phi_r"]
                                     for g in gensets), default=0.0))
        _row(m, inertia, _Lin().plus(F0_HZ * block / rocof_max), f"Island-rocof-block{tag}",
             coord, refuse="RoCoF on the largest pickup block: the gensets' inertia at N+k "
                           "is too small", facts=facts)
        # (e) quasi-steady state on the largest block.
        _row(m, k_sum.scaled(df_qss), _Lin().plus(block), f"Island-qss-block{tag}", coord,
             refuse="quasi-steady-state frequency on the largest pickup block: the droop "
                    "gain at N+k is too small", facts=facts)
    return bridge


def _seamless_rows(n, m, sns, sns_p, coord, tag, *, units, gensets, spinning, gfm, upss, l_c,
                   joins_s, links, fw_units, delta_p_plus, facts, active, period, p_out,
                   nom_out, p_store, k_sum, margin, rocof_max, df_qss):
    """The ``seamless`` rows: the steps ΔP⁺ and ΔP⁻, headroom both ways, the
    MVA and inertia online at 0⁺, the droop gain, and B(t)."""
    def spec(x):
        return units[x]["values"]

    # p_imp(t) = η·p0_imp − p0_exp, site side, positive when importing.
    p_imp = _Lin(sns_p)
    for name in active(links["import"], lambda _: "Link", period):
        p_imp.var(m.variables["Link-p"].sel(snapshot=sns_p, name=name),
                  float(n.links.at[name, "efficiency"]))
    for name in active(links["export"], lambda _: "Link", period):
        p_imp.var(m.variables["Link-p"].sel(snapshot=sns_p, name=name), -1.0)

    # The UPSs that transfer and walk in only after the gensets have joined.
    late = [u for u in upss if units[u]["transfers_on_islanding"] and gensets
            and spec(u)["walk_in_s"] >= joins_s]
    facts["instant"]["late_ups"] = sorted(set(facts["instant"]["late_ups"]) | set(late))

    # ΔP⁺ = p_imp − Σ_late X_u, with X_u ≤ L_it,u, X_u ≤ p_nom_u, X_u ≥ 0. X
    # appears only where a larger X loosens the row, so it settles at
    # min(L_it, p_nom_u) wherever that matters.
    if delta_p_plus is None:
        dp_up = _Lin(sns_p).add(p_imp)
        for u in late:
            x = m.add_variables(lower=0.0, upper=xr_series(_it_load(n, units, u, sns_p), coord),
                                coords=[coord], name=f"Island-ups-relief-{u}{tag}")
            cap = _add_nominal(_Lin(), n, "StorageUnit", u, 1.0)
            _row(m, cap, _Lin(sns_p).var(x), f"Island-ups-relief-cap-{u}{tag}", coord)
            dp_up.var(x, -1.0)
    else:
        dp_up = _Lin(sns_p).plus(_as_series(delta_p_plus, n, sns_p))
    # ΔP⁻ = −p_imp + Σ L_it,u: a transfer adds surplus, so the constant upper
    # bound L_it ≥ min(L_it, p_nom_u) is taken (conservative). At 0⁺ every
    # transferring UPS has dropped its load, early walk-in or late (owner,
    # 2026-10-13); once the gensets have joined, only the late ones still have.
    transferring = [u for u in upss if units[u]["transfers_on_islanding"]]
    dp_down = _Lin(sns_p).add(p_imp, -1.0)
    for u in transferring:
        dp_down.plus(_it_load(n, units, u, sns_p))
    dp_down_joined = _Lin(sns_p).add(p_imp, -1.0)
    for u in late:
        dp_down_joined.plus(_it_load(n, units, u, sns_p))

    # (b↑) upward headroom at 0⁺: the GFM storage and the spinning gensets.
    up = _Lin(sns_p)
    for s in gfm:
        _add_nominal(up, n, "StorageUnit", s, 1.0)
        p_store(up, s, -1.0)
    for g in spinning:
        comp, _ = _genset_out(n, g)
        avail = _dense(n, comp, "p_max_pu", sns_p, [g])[g]
        var, fixed = _nominal(n, comp, g)
        eta = _genset_out(n, g)[1]
        if var is not None:
            up.var(var, avail * eta, coord)
        up.plus(avail * fixed * eta)
        p_out(up, g, -1.0)
    _row(m, up, dp_up, f"Island-headroom-up{tag}", coord, facts=facts)

    # (b↓) downward power: the GFM storage absorbs the surplus.
    down = _Lin(sns_p)
    for s in gfm:
        _add_nominal(down, n, "StorageUnit", s, 1.0)
        p_store(down, s, 1.0)
    _row(m, down, dp_down, f"Island-headroom-down{tag}", coord, facts=facts)

    # (b↓) SoC room until frequency-watt has curtailed the surplus.
    t_curt = max((spec(f)["fw_delay_s"] + spec(f)["fw_p_limit_mw"] / spec(f)["fw_ramp_mw_per_s"]
                  for f in fw_units), default=None)
    transfer_loads = sorted({x for u in transferring for x in units[u]["it_load"]
                             if x in n.loads.index})
    if t_curt is None:
        facts["surplus"] = {"status": "not curtailable: no gfl_fw unit", "t_curt_s": None,
                            "transfer_loads": transfer_loads}
    else:
        facts["surplus"] = {"status": "row", "t_curt_s": t_curt,
                            "transfer_loads": transfer_loads}
        room = _Lin(sns_p)
        for s in gfm:
            _add_nominal(room, n, "StorageUnit", s,
                         float(n.storage_units.at[s, "max_hours"]))
            _add_soc_before(room, n, sns, sns_p, s, -1.0)
        _row(m, room, dp_down.scaled(t_curt / 3600.0), f"Island-soc-room{tag}", coord,
             facts=facts)

    # (c) GFM MVA at 0⁺: the GFM storage and the spinning gensets.
    mva = _Lin(sns_p)
    for s in gfm:
        _add_nominal(mva, n, "StorageUnit", s, 1.0 / spec(s)["pf_rated"])
    for g in spinning:
        nom_out(mva, g, 1.0 / spec(g)["cos_phi_r"])
    _row(m, mva, _Lin(sns_p).plus(margin * l_c), f"Island-mva-instant{tag}", coord,
         refuse="seamless islanding needs grid-forming MVA online at the instant (GFM "
                "storage and spinning gensets) of (1 + gfm_margin) × the critical load",
         facts=facts)

    # (d) RoCoF at 0⁺, both steps: 2M ≥ f0·ΔP/RoCoF_max, with
    # M = Σ_spin H·s_nom + Σ_VSM H_v·s_nom + Σ_droop GFM τ_f·p_nom/(2R).
    two_m = _Lin()
    for g in spinning:
        nom_out(two_m, g, 2.0 * spec(g)["H_s"] / spec(g)["cos_phi_r"])
    for s in gfm:
        if units[s]["control"] == "gfm_vsm":
            coef = spec(s)["H_v_s"] / spec(s)["pf_rated"]
        else:
            coef = spec(s)["tau_f_s"] / (2.0 * spec(s)["droop_pct"] / 100.0)
        _add_nominal(two_m, n, "StorageUnit", s, 2.0 * coef)
    for side, dp in (("up", dp_up), ("down", dp_down)):
        _row(m, _Lin(sns_p).add(two_m), dp.scaled(F0_HZ / rocof_max),
             f"Island-rocof-{side}{tag}", coord, facts=facts)
    # (e) quasi-steady state, both steps, once the gensets have joined.
    for side, dp in (("up", dp_up), ("down", dp_down_joined)):
        _row(m, _Lin(sns_p).add(k_sum.scaled(df_qss)), dp,
             f"Island-qss-{side}{tag}", coord, facts=facts)

    # B(t) = max(ΔP⁺, 0)·(start_s + sync_s)/3600: b ≥ ΔP⁺, b ≥ 0, and b
    # only tightens the energy row it enters, so it settles at the max.
    if not gfm:
        return _Lin(sns_p)
    if delta_p_plus is not None:
        b = dp_up.const.clip(lower=0.0)
        bridge = _Lin(sns_p).plus(b * joins_s / 3600.0)
        held = bool(b.gt(0).any()) and joins_s > 0
    else:
        b = m.add_variables(lower=0.0, coords=[coord], name=f"Island-step-up{tag}")
        _row(m, _Lin(sns_p).var(b), dp_up, f"Island-step-up{tag}", coord)
        bridge = _Lin(sns_p).var(b, joins_s / 3600.0)
        held = joins_s > 0
    if held:
        facts["seamless_bridge"] = "held"
    elif facts["seamless_bridge"] is None:
        facts["seamless_bridge"] = "zero step"
    return bridge


def not_islandable_hours(n) -> pd.Index:
    """After the solve: the hours whose export step ΔP⁻ > 0 has no
    frequency-watt unit to curtail it (I2b (b↓); no row was added for them).
    Empty when the solve had a curtailment row or no seamless scenario."""
    facts = getattr(n, "_island_facts", None) or {}
    surplus = facts.get("surplus") or {}
    if not str(surplus.get("status", "")).startswith("not curtailable"):
        return pd.Index([])
    pcc = facts["instant"]["pcc"]
    p0 = n.links_t.p0
    p_imp = pd.Series(0.0, index=n.snapshots)
    for name in pcc["import"]:
        p_imp += float(n.links.at[name, "efficiency"]) * p0[name]
    for name in pcc["export"]:
        p_imp -= p0[name]
    loads = surplus["transfer_loads"]
    extra = (n.get_switchable_as_dense("Load", "p_set")[loads].sum(axis=1)
             if loads else 0.0)
    dp_down = -p_imp + extra
    return n.snapshots[(dp_down > 1e-6).to_numpy()]


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

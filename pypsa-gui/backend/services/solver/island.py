"""
Island ride-through, fuel and bridge reserve (I2a), the instant of
islanding (I2b), and linearised UC and the variants (I2c) in the hub LP
(campus island plan 2026-10-07), as an ``extra_functionality`` wrapper
composed like ``services/solver/adequacy.py``.

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

**The seamless island's own events (Guided smoke 2026-10-16).** I6 judges
more than the islanding step (E1): E2, the largest load step; E3, a unit
trip; E5, the PV lost (gridspine ``frequency.campus_frequency_study``). A
plan sized for E1 alone then fails the check whenever one of them is larger
(the staged microgrid: a charging battery's trip with the other one already
at its charge limit). ``seamless`` therefore adds, on (b↑)/(b↓)'s own
headroom (``_event_rows``):

    up ≥ max(pickup_blocks), else the hour's largest draw      Island-e2-up(-<s>)
         (a load, or a GFM battery charging)
    up without u ≥ p_u, for each source u                       Island-n1-up-<u>
    down without s ≥ −p_s, for each GFM storage s               Island-n1-down-<s>
    up ≥ Σ p_pv                                                 Island-e5-up

A trip that leaves no grid-forming unit gets no row (no dispatch survives
it; facts ``n1_no_former``); a PV unit's own trip is implied by E5's row, and
a unit that cannot run (p_nom 0, not extendable) cannot trip. In these rows a
genset is credited at min(its (b↑) share, its load step,
load_step_max_pct·P_n: gridspine frequency.py:632, ``step_mw``), what it
gives inside I6's window; its ramp after the step is not credited
(conservative). **Known gap:** E1's own rows still credit a genset's full
headroom, so a spinning genset counts for more at the islanding step than in
the event rows (``.scratch/guided-smoke-2026-10-16/issues/04-e1-genset-credit.md``).

**Ledgered:** PV is left out (conservative); the site is a copperplate (the
hub's one-way site transformer is not seen here; I4 catches it at the island
hours); U_max over-subtracts when an extendable genset is not built
(conservative); the occurrence derating is not applied, since N+k covers
outages. I2b: only ``spinning`` gensets are online at 0⁺, whatever they
dispatch (linearised UC is I2c); isochronous gensets add no droop gain
(conservative); the instantaneous RoCoF at 0⁺ is stricter than I6's window;
f0 is fixed at 50 Hz.

**Linearised UC (I2c, Q7).** With the sidecar's ``linearised_uc`` on, the
fixed-size gensets are committed for the solve (``linearised_uc``: PyPSA's
``linearized_unit_commitment``, u_g(t) continuous in [0, 1], and the
sidecar's ``p_min_pu``), and u_g·p_nom_g replaces the ``spinning`` flag in
the 0⁺ rows: headroom u·p_max_pu·η·p_nom − η·p, the GFM MVA and the RoCoF
inertia. PyPSA's own p ≥ u·p_min_pu·p_nom is their minimum load. An
extendable genset is not committed (big-M, so u·p_nom is bilinear) and
keeps ``spinning``; the facts say so. u may be fractional: "linearised".

**The variants (I2c, Q3).** ``island_study`` solves copies of the network
without the rows and once per scenario, and reports the cost of island
capability, the cost of seamless islanding and the binding rows' duals.

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

import contextlib
import json
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
        island = load_island_config(pathlib.Path(storage) / SIDECAR_NAME)
    except ContractError as exc:
        raise IslandRefusal(f"{SIDECAR_NAME} is not a valid island config: {exc}") from exc
    if island is not None:
        island["cuts"] = load_applied_cuts(pathlib.Path(storage))
    return island


#: The frequency cuts the user applied (I7, owner QB), beside the sidecar.
CUTS_FILE = "island_cuts.json"


def load_applied_cuts(storage_dir) -> list[dict]:
    """The applied cuts of the project at ``storage_dir`` (``[]`` without
    ``island_cuts.json``); a file that is not JSON is refused by name."""
    path = pathlib.Path(storage_dir) / CUTS_FILE
    if not path.is_file():
        return []
    try:
        return list(json.loads(path.read_text()).get("cuts") or [])
    except (json.JSONDecodeError, AttributeError) as exc:
        raise IslandRefusal(f"{CUTS_FILE} is not valid: {exc}") from exc


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


#: What a short row's hint tells the user to do.
SHORTFALL_ADVICE = "Raise a grid-forming battery's p_nom_max, or commit or add a genset."


def island_shortfalls(facts) -> list[dict]:
    """The event rows the site cannot hold, from ``n._island_facts["events"]``: the largest load
    step is more than the grid formers can pick up at the instant (the GFM storage at its rating
    plus the online gensets' step credit). Pure: one definition for the infeasibility log line and
    for the study's failure reason (UX review N8). Only rows that are actually short appear."""
    out = []
    for ev in ((facts or {}).get("events") or []):
        reach = ev["gfm_mw"] + ev["genset_mw"]
        if ev["step_mw"] > reach + 1e-6:
            out.append({"row": ev["row"], "step_mw": ev["step_mw"], "reach_mw": reach,
                        "missing_mw": ev["step_mw"] - reach,
                        "gfm_mw": ev["gfm_mw"], "genset_mw": ev["genset_mw"]})
    return out


def shortfall_line(sh: dict) -> str:
    """A short row, its sentence and what to do: the text of the log line and of the study's reason."""
    return f"{sh['row']}: {shortfall_sentence(sh)} {SHORTFALL_ADVICE}"


def shortfall_sentence(sh: dict) -> str:
    """One short row in words, exactly as the infeasibility diagnostics print it after the row name."""
    return (f"the largest load step is {sh['step_mw']:,.2f} MW, "
            f"but the grid formers can pick up at most {sh['reach_mw']:,.2f} MW at the instant "
            f"(GFM storage at its rating {sh['gfm_mw']:,.2f} MW, the online gensets' load "
            f"step {sh['genset_mw']:,.2f} MW): {sh['missing_mw']:,.2f} MW is missing.")


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
        if island is _UNSET:
            try:
                island = load_project_island()
            except IslandRefusal:
                island = None
        if island and island["requirements"].get("linearised_uc"):
            why += "; linearised unit commitment (I2c) is refused with them"
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


def island_extra_functionality(island, *, user_fn=None, delta_p_plus=None, emit=None,
                               event_rows=True):
    """The ``extra_functionality`` callback that adds the island rows.

    ``delta_p_plus`` overrides the ``seamless`` step ΔP⁺ in MW (a number, a
    Series on the snapshots, or ``callable(n, sns)`` returning either); None,
    the default, builds it from the PCC Links and the late UPSs (I2b).
    ``event_rows`` is a test seam: False keeps the islanding step (E1) rows
    alone; production never passes it."""
    def fn(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        add_island_rows(n, snapshots, island, delta_p_plus=delta_p_plus, emit=emit,
                        event_rows=event_rows)
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
# A fixed-size run (F1; = sweep.FIXED_DESIGN_ATTR): nothing is sized, but the
# linearised UC still runs (it is a real dispatch).
FIXED_DESIGN_ATTR = "_fixed_design"


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
    if getattr(n, FIXED_DESIGN_ATTR, None):
        return "dispatch-only: sizing off (fixed sizes)"
    for frame, name in _sizable(n, island):
        col = "e_nom_extendable" if frame == "stores" else "p_nom_extendable"
        if bool(getattr(n, frame).at[name, col]):
            return None
    return "dispatch-only: nothing the rows can size is extendable"


# ── linearised unit commitment (I2c) ───────────────────────────────────────


def uc_gensets(n, island, gensets=None) -> tuple[list[str], dict[str, str]]:
    """(committed, {not committed: why}) under the sidecar's ``linearised_uc``.

    Only fixed-size gensets are committed: PyPSA models a committable
    *extendable* unit with big-M, so u·p_nom is bilinear and a fractional u
    relaxes its minimum load almost completely. An extendable genset keeps
    the ``spinning`` flag (I2b)."""
    if not island["requirements"].get("linearised_uc"):
        return [], {}
    if gensets is None:
        gensets = [u for u, s in island["units"].items() if s["kind"] == "genset"
                   and (u in n.generators.index or u in n.links.index)]
    committed, skipped = [], {}
    for g in gensets:
        frame = n.generators if g in n.generators.index else n.links
        if bool(frame.at[g, "p_nom_extendable"]):
            # Owner, 2026-10-13 (option A): never committed, not refused; it
            # keeps I2b's spinning rule.
            skipped[g] = "not committed: extendable (big-M bilinear)"
        else:
            committed.append(g)
    return committed, skipped


@contextlib.contextmanager
def linearised_uc(n, island):
    """Commit the sidecar's fixed-size gensets for one solve, and yield the
    ``optimize`` keywords (``linearized_unit_commitment``). ``committable``
    and the sidecar's ``p_min_pu`` are written for the solve and restored
    after it, so the project is unchanged. A dispatch-only solve, which
    skips the rows, commits nothing."""
    # A fixed-size run (F1) is dispatch-only for the rows but still commits:
    # its fixed gensets get real UC (#448 §3.1).
    if island is None or not island["requirements"].get("linearised_uc") \
            or (dispatch_only(n, island) and not getattr(n, FIXED_DESIGN_ATTR, None)):
        yield {}
        return
    committed, _ = uc_gensets(n, island)
    saved = []
    try:
        for g in committed:
            frame = n.generators if g in n.generators.index else n.links
            p_min = island["units"][g]["values"].get("p_min_pu")
            if p_min is None:
                p_min = float(frame.at[g, "p_min_pu"])
            if not p_min > 0:
                raise IslandRefusal(
                    f"genset {g!r} is committed under linearised_uc and needs p_min_pu above "
                    "0 in the island sidecar, or its online headroom costs no fuel")
            saved.append((frame, g, frame.at[g, "committable"], frame.at[g, "p_min_pu"]))
            frame.at[g, "committable"] = True
            frame.at[g, "p_min_pu"] = float(p_min)
        yield {"linearized_unit_commitment": True} if committed else {}
    finally:
        for frame, g, was_committable, was_p_min in saved:
            frame.at[g, "committable"] = was_committable
            frame.at[g, "p_min_pu"] = was_p_min


@contextlib.contextmanager
def solve_uc(network, cfg, island=_UNSET):
    """``linearised_uc`` for ``run_simulation``'s plain solve: active only
    when the island rows are, read from the same sidecar."""
    if (not getattr(cfg, "island_constraints", False) or getattr(cfg, "mode", "lopf") != "lopf"
            or getattr(network, OPERATIONAL_ATTR, False)):
        yield {}
        return
    if island is _UNSET:
        island = load_project_island()
    with linearised_uc(network, island) as kw:
        yield kw


# ── the variants and the cost of island capability (I2c) ──────────────────


def _default_solve(n, extra_functionality, **kw):
    return n.optimize(solver_name="highs", extra_functionality=extra_functionality, **kw)


def binding_rows(n, tol=1e-6) -> list[dict]:
    """The island rows whose dual is non-zero, after a solve: ``hours`` (None
    for a static row) and ``duals`` at them, in objective units per unit of
    the row."""
    out = []
    for name in n.model.constraints:
        if not name.startswith("Island-"):
            continue
        dual = n.model.constraints[name].dual
        if "snapshot" in dual.dims:
            other = [d for d in dual.dims if d != "snapshot"]
            series = (dual.sum(other) if other else dual).to_pandas()
            hit = series[series.abs() > tol]
            if len(hit):
                out.append({"row": name, "hours": [str(h) for h in hit.index],
                            "duals": [float(x) for x in hit]})
        else:
            value = float(dual.sum())
            if abs(value) > tol:
                out.append({"row": name, "hours": None, "duals": [value]})
    return out


def island_study(n, island, *, scenarios=None, solve=None, tol=1e-6) -> dict:
    """The islanding variants as separate solves of copies of ``n`` (Q3):
    a baseline with no island rows, and one solve per scenario (the
    sidecar's, or ``scenarios``). Reports each objective (the annualised
    cost), the **cost of island capability** (scenario − baseline), the
    **cost of seamless islanding** (seamless − ups_bridged), and the binding
    rows with their hours and duals. A refused or failed variant is recorded
    with its reason and has no cost. ``solve(n, extra_functionality, **kw)``
    returns (status, condition); HiGHS by default."""
    import copy

    from services.adequacy.energization import apply_energization

    if isinstance(n.snapshots, pd.MultiIndex):
        # The variants are plain single-period LOPFs of copies (no
        # multi_investment_periods; PyPSA's copy() also drops the MultiIndex
        # name), so a multi-period network would crash mid-study.
        raise IslandRefusal("island_study_multi_period_unsupported: the cost-of-islanding "
                            "study runs single-period networks only; this network has "
                            "investment periods")
    solve = solve or _default_solve
    scenarios = list(scenarios or island["requirements"]["scenarios"])
    variants = {}

    # Every variant solves a disposable copy outside run_simulation, so each
    # copy gets a connection's energization schedule the way run_simulation
    # applies it (E2E slice 8); the copies are dropped, nothing to undo.
    base = n.copy()
    apply_energization(base)
    status, cond = solve(base, None)
    variants["baseline"] = {"status": status, "condition": cond,
                            "objective": float(base.objective) if status == "ok" else None}

    for sc in scenarios:
        one = copy.deepcopy(island)
        one["requirements"]["scenarios"] = [sc]
        if sc == "ups_bridged" and "pickup_blocks" not in one["requirements"]["values"]:
            variants[sc] = {"status": "refused", "objective": None,
                            "reason": "ups_bridged needs pickup_blocks in the sidecar"}
            continue
        c = n.copy()
        apply_energization(c)
        try:
            with linearised_uc(c, one) as kw:
                status, cond = solve(c, island_extra_functionality(one), **kw)
        except IslandRefusal as exc:
            variants[sc] = {"status": "refused", "objective": None, "reason": str(exc)}
            continue
        if status != "ok":
            variants[sc] = {"status": status, "condition": cond, "objective": None}
            continue
        variants[sc] = {"status": "ok", "condition": cond, "objective": float(c.objective),
                        "binding": binding_rows(c, tol),
                        "facts": getattr(c, "_island_facts", None)}
        # Ticket 08: the tiers set the order only where the sidecar gives no explicit blocks.
        if "pickup_blocks" not in one["requirements"]["values"]:
            variants[sc]["pickup_sequence"] = pickup_sequence(c)

    def obj(name):
        v = variants.get(name)
        return v["objective"] if v else None

    base_obj = obj("baseline")
    capability = {sc: obj(sc) - base_obj for sc in scenarios
                  if obj(sc) is not None and base_obj is not None}
    seamless = (obj("seamless") - obj("ups_bridged")
                if obj("seamless") is not None and obj("ups_bridged") is not None else None)
    return {"variants": variants, "cost_of_island_capability": capability,
            "cost_of_seamless": seamless}


# ── the rows ───────────────────────────────────────────────────────────────


def add_island_rows(n, snapshots, island, *, delta_p_plus=None, emit=None,
                    event_rows=True) -> dict:
    """Add (a), (b) and (c) to ``n.model`` and record what was built on
    ``n._island_facts``. ``event_rows`` is a test seam: False keeps the
    islanding step (E1) rows alone; production never passes it."""
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
    committed, not_committed = uc_gensets(n, island, gensets)
    for g in committed:
        comp, _ = _genset_out(n, g)
        if not bool(getattr(n, _LIST_NAMES[comp]).at[g, "committable"]) \
                or f"{comp}-status" not in m.variables:
            raise IslandRefusal(
                f"linearised_uc is on, but genset {g!r} was not committed for this solve; "
                "solve inside services.solver.island.linearised_uc(n, island)")
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
             "uc": ({"status": "linearised (u may be fractional)", "committed": committed,
                     "not_committed": not_committed} if req.get("linearised_uc")
                    else {"status": "off"}),
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

    labels = {_period_label(p, s_p): (p, s_p) for p, s_p in _periods(sns)}
    cuts_by, not_applied = _sort_cuts(island.get("cuts") or (), labels, "seamless" in scenarios)
    facts["cuts"] = {"applied": [], "not_applied": not_applied}

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
        cuts_p = cuts_by.get(_period_label(period, sns_p), [])
        for cut in cuts_p:
            if cut["hour"] >= len(sns_p):
                raise IslandRefusal(f"island cut {cut['kind']} at period {cut['period']} hour "
                                    f"{cut['hour']}: the period has {len(sns_p)} hours")
        bridge = _instant_rows(
            n, m, sns, sns_p, coord, tag, period, island=island, gensets=g_p, gfm=gfm_p,
            upss=ups_p, l_c=l_c, unit_rating=unit_rating, joins_s=joins_p, k=k,
            links=pcc, fw_units=fw, delta_p_plus=delta_p_plus, facts=facts, active=active,
            committed=committed, cuts=cuts_p, event_rows=event_rows)

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

    facts["cuts"]["applied"].sort()
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


#: The I7 cut kinds (gridspine ``frequency.cuts``): the 0⁺ quantity each
#: bounds, in the unit of the I2b row it tightens.
CUT_KINDS = ("inertia", "headroom_up", "headroom_down", "droop_gain")


def _cut_name(cut) -> str:
    return f"Island-cut-{cut['kind']}-{cut['period']}-{cut['hour']}"


def _cut_row(m, lhs: _Lin, cut, coord, facts):
    """``lhs`` ≥ ``required`` at the cut's hour only (I7): the snapshot terms
    of ``lhs`` are taken at that hour, the nominal terms as they are."""
    hour = cut["hour"]
    terms = [t.isel(snapshot=[hour]) if "snapshot" in t.dims else t for t in lhs.terms]
    const = float(lhs.const.iloc[hour]) if isinstance(lhs.const, pd.Series) else float(lhs.const)
    name = _cut_name(cut)
    if not terms:
        if cut["required"] - const > 1e-6:
            raise IslandRefusal(f"{name}: short by {cut['required'] - const:.4g} with nothing "
                                "this solve can size")
        return
    expr = terms[0]
    for t in terms[1:]:
        expr = expr + t
    m.add_constraints(expr >= cut["required"] - const, name=name)
    facts["cuts"]["applied"].append(name)


def _sort_cuts(cuts, labels, seamless) -> tuple[dict, list]:
    """The cuts by period label, the tightest per (kind, period, hour), and
    those with no row here, each with its reason."""
    by_label: dict = {}
    not_applied = []
    for cut in cuts:
        if cut["kind"] not in CUT_KINDS:
            raise IslandRefusal(f"island cut kind {cut['kind']!r} is not one of {', '.join(CUT_KINDS)}")
        if cut["scenario"] != "seamless":
            not_applied.append({**cut, "reason": f"{cut['scenario']}: only seamless cuts have rows "
                                                 "(the 0⁺ rows I2b builds)"})
        elif not seamless:
            not_applied.append({**cut, "reason": "the sidecar's scenarios have no seamless rows"})
        elif int(cut["period"]) not in labels:
            not_applied.append({**cut, "reason": f"no period {cut['period']} in this solve"})
        else:
            key = (cut["kind"], int(cut["hour"]))
            same = by_label.setdefault(int(cut["period"]), {})
            if key not in same or cut["required"] > same[key]["required"]:
                same[key] = {**cut, "period": int(cut["period"]), "hour": int(cut["hour"]),
                             "required": float(cut["required"])}
    return {label: list(v.values()) for label, v in by_label.items()}, not_applied


ESTIMATE_LABEL = ("estimate (primal): the cheapest extendable candidate that meets the cut, "
                  "times its capital cost; not a solve")


def _cut_coefficients(n, island) -> dict:
    """``{kind: [(candidate, component, coef per MW of p_nom)]}``: the
    extendable units and what one MW of each adds to the quantity a cut
    bounds, as the I2b rows count it (M: a droop GFM's τ_f/(2R), a VSM's
    H_v/pf, a spinning genset's H/cos φ·η; ΣK: 1/(R·f0), ·η for a genset;
    headroom: 1 for GFM storage, η for a spinning genset upward)."""
    from services.adequacy.island_config import GFM_CONTROLS

    units = island["units"]
    out = {kind: [] for kind in CUT_KINDS}
    for name, u in units.items():
        v = u["values"]
        if u["kind"] == "bess" and u.get("control") in GFM_CONTROLS and name in n.storage_units.index:
            if not bool(n.storage_units.at[name, "p_nom_extendable"]):
                continue
            m_coef = (v["H_v_s"] / v["pf_rated"] if u["control"] == "gfm_vsm"
                      else v["tau_f_s"] / (2.0 * v["droop_pct"] / 100.0))
            for kind, coef in (("inertia", m_coef), ("droop_gain", 1.0 / (v["droop_pct"] / 100.0 * F0_HZ)),
                               ("headroom_up", 1.0), ("headroom_down", 1.0)):
                out[kind].append((name, "StorageUnit", coef))
        elif u["kind"] == "genset" and (name in n.generators.index or name in n.links.index):
            comp, eta = _genset_out(n, name)
            frame = n.generators if comp == "Generator" else n.links
            if not bool(frame.at[name, "p_nom_extendable"]):
                continue
            if u.get("governor") == "droop":
                out["droop_gain"].append((name, comp, eta / (v["droop_pct"] / 100.0 * F0_HZ)))
            if u.get("spinning"):                    # online at 0⁺
                out["inertia"].append((name, comp, v["H_s"] / v["cos_phi_r"] * eta))
                out["headroom_up"].append((name, comp, eta))
    return out


def cut_estimates(n, island, cuts) -> list[dict]:
    """The primal estimate of each cut (I7, owner Q6): the shortfall
    ``required − current`` met by the cheapest extendable candidate,
    ``added_mw`` of it at its capital cost. A cut already met costs 0; one
    no candidate can meet has no estimate, and says why."""
    coefs = _cut_coefficients(n, island)
    rows = []
    for cut in cuts:
        short = max(float(cut["required"]) - float(cut.get("current") or 0.0), 0.0)
        row = {**cut, "shortfall": short, "candidate": None, "added_mw": None, "estimate": None,
               "label": ESTIMATE_LABEL}
        if short == 0.0:
            row.update(added_mw=0.0, estimate=0.0)
        else:
            best = None
            for name, comp, coef in coefs.get(cut["kind"], ()):
                frame = getattr(n, _LIST_NAMES[comp])
                mw = short / coef
                cost = mw * float(frame.at[name, "capital_cost"])
                if best is None or cost < best[2]:
                    best = (name, mw, cost)
            if best is None:
                row["label"] = f"no extendable unit adds {cut['kind']}; no estimate"
            else:
                row.update(candidate=best[0], added_mw=best[1], estimate=best[2])
        rows.append(row)
    return rows


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


#: The pickup order by criticality tier (grid-to-process ticket 08): untiered Loads come last; an untiered process
#: unit is picked up as ``process_critical`` (as ``process_loss`` costs it), flagged ``tier_unset``.
_TIER_ORDER = {"safety": 0, "process_critical": 1, "process_flexible": 2, "deferrable": 3}


def _island_process_units(n) -> list[str]:
    """The process units the island picks up: Links with ``eh_process`` whose supply bus is site-side (not a PCC
    import Link's grid bus), the island's own scope (it drafts every Load of the network)."""
    from services.solver.process_uc import process_mask

    grid = {str(n.links.at[lk, "bus0"]) for lk in _pcc_links(n)["import"]}
    return [str(u) for u in n.links.index[process_mask(n.links)] if str(n.links.at[u, "bus0"]) not in grid]


def pickup_sequence(n, period=None) -> list[dict]:
    """The island's pickup sequence when the sidecar has no ``pickup_blocks`` (ticket 08): every Load and every
    island process unit, a block each, ordered by criticality tier (safety, process_critical, process_flexible,
    deferrable, then the untiered Loads), within a tier largest first, then by name. A process unit with no (or an
    unknown) tier sits with ``process_critical``, flagged ``tier_unset``. A Load's block is its largest
    ``p_set`` in the period; a process unit's its largest SOLVED draw ``p0`` (None before a solve), never its p_nom.
    E2 steps on the largest block, so the order is reported, not optimised."""
    from models.process_unit import CRITICALITY_TIERS
    from services.adequacy.process_loss import TIER_UNSET

    sns = n.snapshots if period is None else n.snapshots[n.snapshots.get_level_values(0) == period]
    out = []
    for ld in n.loads.index:
        out.append({"name": str(ld), "kind": "load", "tier": None, "basis": "p_set_max",
                    "block_mw": float(_dense(n, "Load", "p_set", sns, [ld])[ld].max()) if len(sns) else 0.0})
    tiers = n.links["criticality_tier"] if "criticality_tier" in n.links.columns else None
    p0 = n.links_t.p0
    for u in _island_process_units(n):
        tier = str(tiers.get(u, "") or "").strip() if tiers is not None else ""
        solved = u in p0.columns and len(sns) and p0[u].reindex(sns).notna().all()
        block = {"name": u, "kind": "process_unit", "tier": tier or None,
                 "basis": "solved_p0_max" if solved else "not_solved",
                 "block_mw": float(p0[u].reindex(sns).max()) if solved else None}
        if tier not in CRITICALITY_TIERS:
            block["flag"] = TIER_UNSET
        out.append(block)
    order = {"load": 4, "process_unit": _TIER_ORDER["process_critical"]}
    return sorted(out, key=lambda b: (_TIER_ORDER.get(b["tier"] or "", order[b["kind"]]),
                                      -(b["block_mw"] if b["block_mw"] is not None else -math.inf), b["name"]))


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
                  unit_rating, joins_s, k, links, fw_units, delta_p_plus, facts, active,
                  committed=(), cuts=(), event_rows=True):
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

    # Linearised UC (I2c) replaces the spinning flag for the committed
    # gensets: online at 0⁺ is u_g(t), and PyPSA's own p ≥ u·p_min_pu·p_nom
    # is their minimum load.
    online = [g for g in gensets if g in committed]
    spinning = [g for g in gensets if units[g]["spinning"] and g not in committed]
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

    def on_out(lin, g, coef):
        """lin += coef·(online output capacity at 0⁺): u_g·η·p_nom_g for a
        committed genset (p_nom fixed, so linear), η·p_nom_g when spinning."""
        if g not in online:
            return nom_out(lin, g, coef)
        comp, eta = _genset_out(n, g)
        _, p_nom = _nominal(n, comp, g)
        return lin.var(sv(f"{comp}-status").sel(snapshot=sns_p, name=g), coef * eta * p_nom)

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
    cut_lhs: dict = {}
    if seamless:
        bridge = _seamless_rows(
            n, m, sns, sns_p, coord, tag, units=units, gensets=gensets, spinning=spinning,
            gfm=gfm, upss=upss, l_c=l_c, joins_s=joins_s, links=links, fw_units=fw_units,
            delta_p_plus=delta_p_plus, facts=facts, active=active, period=period,
            p_out=p_out, nom_out=nom_out, on_out=on_out, online=online, p_store=p_store,
            k_sum=k_sum, margin=margin,
            rocof_max=rocof_max, df_qss=df_qss, cut_lhs=cut_lhs, blocks=vals.get("pickup_blocks"),
            event_rows=event_rows)
        cut_lhs["droop_gain"] = k_sum
        for cut in cuts:
            _cut_row(m, cut_lhs[cut["kind"]], cut, coord, facts)

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
                   nom_out, on_out, online, p_store, k_sum, margin, rocof_max, df_qss,
                   cut_lhs=None, blocks=None, event_rows=True):
    """The ``seamless`` rows: the steps ΔP⁺ and ΔP⁻, headroom both ways, the
    MVA and inertia online at 0⁺, the droop gain, and B(t). ``cut_lhs``
    receives the 0⁺ quantities the I7 cuts bound (M, headroom both ways)."""
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

    # (b↑) upward headroom at 0⁺: the GFM storage, and the gensets online
    # (spinning, or committed: u·p_max_pu·η·p_nom − η·p). ``without``: the
    # same less one unit's own share (the N-1 rows below).
    def genset_room(g):
        """One online genset's share of (b↑)."""
        room = _Lin(sns_p)
        comp, eta = _genset_out(n, g)
        avail = _dense(n, comp, "p_max_pu", sns_p, [g])[g]
        if g in online:
            _, p_nom = _nominal(n, comp, g)
            room.var(m.variables[f"{comp}-status"].sel(snapshot=sns_p, name=g),
                     avail * p_nom * eta, coord)
        else:
            var, fixed = _nominal(n, comp, g)
            if var is not None:
                room.var(var, avail * eta, coord)
            room.plus(avail * fixed * eta)
        return p_out(room, g, -1.0)

    def headroom_up(without=None, credit=None):
        """(b↑) less ``without``'s own share; ``credit``: a genset's share
        replaced (the event rows' step credit)."""
        up = _Lin(sns_p)
        for s in gfm:
            if s == without:
                continue
            _add_nominal(up, n, "StorageUnit", s, 1.0)
            p_store(up, s, -1.0)
        for g in online + spinning:
            if g == without:
                continue
            up.add(credit[g] if credit is not None else genset_room(g))
        return up

    # The event rows credit a genset at min(its (b↑) share, its load step):
    # inside I6's window a joined genset gives at most its step,
    # load_step_max_pct·P_n (gridspine frequency.py:632, ``step_mw``), before
    # its ramp (not credited). x_g ≤ both and enters only rows a larger x_g
    # loosens, so it settles at the min (as Island-ups-relief does).
    credit = {}
    for g in (online + spinning) if event_rows else ():
        x = m.add_variables(lower=0.0, coords=[coord], name=f"Island-event-genset-credit-{g}{tag}")
        _row(m, genset_room(g), _Lin(sns_p).var(x), f"Island-event-genset-room-{g}{tag}", coord)
        _row(m, on_out(_Lin(sns_p), g, spec(g)["load_step_max_pct"] / 100.0), _Lin(sns_p).var(x),
             f"Island-event-genset-step-{g}{tag}", coord)
        credit[g] = _Lin(sns_p).var(x)

    # (b↓) downward power: the GFM storage absorbs the surplus.
    def headroom_down(without=None):
        down = _Lin(sns_p)
        for s in gfm:
            if s == without:
                continue
            _add_nominal(down, n, "StorageUnit", s, 1.0)
            p_store(down, s, 1.0)
        return down

    up, down = headroom_up(), headroom_down()
    _row(m, up, dp_up, f"Island-headroom-up{tag}", coord, facts=facts)
    _row(m, down, dp_down, f"Island-headroom-down{tag}", coord, facts=facts)
    if event_rows:
        _event_rows(n, m, sns_p, coord, tag, units=units, gfm=gfm, gensets=gensets,
                    active=active, period=period, p_out=p_out, p_store=p_store,
                    headroom_up=lambda without=None: headroom_up(without, credit),
                    headroom_down=headroom_down, blocks=blocks, facts=facts,
                    genset_credit_max=sum(_genset_credit_max(n, g, spec(g)) for g in online + spinning))

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

    # (c) GFM MVA at 0⁺: the GFM storage and the gensets online.
    mva = _Lin(sns_p)
    for s in gfm:
        _add_nominal(mva, n, "StorageUnit", s, 1.0 / spec(s)["pf_rated"])
    for g in spinning + online:
        on_out(mva, g, 1.0 / spec(g)["cos_phi_r"])
    _row(m, mva, _Lin(sns_p).plus(margin * l_c), f"Island-mva-instant{tag}", coord,
         refuse="seamless islanding needs grid-forming MVA online at the instant (GFM "
                "storage and spinning gensets) of (1 + gfm_margin) × the critical load",
         facts=facts)

    # (d) RoCoF at 0⁺, both steps: 2M ≥ f0·ΔP/RoCoF_max, with
    # M = Σ_spin H·s_nom + Σ_VSM H_v·s_nom + Σ_droop GFM τ_f·p_nom/(2R).
    two_m = _Lin()
    for g in spinning + online:
        on_out(two_m, g, 2.0 * spec(g)["H_s"] / spec(g)["cos_phi_r"])
    for s in gfm:
        if units[s]["control"] == "gfm_vsm":
            coef = spec(s)["H_v_s"] / spec(s)["pf_rated"]
        else:
            coef = spec(s)["tau_f_s"] / (2.0 * spec(s)["droop_pct"] / 100.0)
        _add_nominal(two_m, n, "StorageUnit", s, 2.0 * coef)
    for side, dp in (("up", dp_up), ("down", dp_down)):
        _row(m, _Lin(sns_p).add(two_m), dp.scaled(F0_HZ / rocof_max),
             f"Island-rocof-{side}{tag}", coord, facts=facts)
    if cut_lhs is not None:
        cut_lhs.update(inertia=two_m.scaled(0.5), headroom_up=up, headroom_down=down)
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


def _rating_max(n, comp, name) -> float:
    """The most an asset can be built to: its p_nom, or its p_nom_max when extendable."""
    static = getattr(n, _LIST_NAMES[comp])
    if bool(static.at[name, "p_nom_extendable"]):
        return float(static.at[name, "p_nom_max"])
    return float(static.at[name, "p_nom"])


def _genset_credit_max(n, g, values) -> float:
    """The most a genset's event credit can be (the infeasibility hint's
    bound): min(its room at full commitment and its minimum load, its load
    step), at its largest rating and availability."""
    comp, eta = _genset_out(n, g)
    p_nom = _rating_max(n, comp, g) * eta
    avail = float(_dense(n, comp, "p_max_pu", n.snapshots, [g])[g].max())
    room = p_nom * (avail - float(values.get("p_min_pu", 0.0) or 0.0))
    return max(0.0, min(room, p_nom * values["load_step_max_pct"] / 100.0))


def _event_rows(n, m, sns_p, coord, tag, *, units, gfm, gensets, active, period, p_out, p_store,
                headroom_up, headroom_down, blocks, facts, genset_credit_max=0.0):
    """The seamless island's own events at 0⁺, as I6 judges them (gridspine
    ``frequency.campus_frequency_study``), on the same headroom as (b↑)/(b↓),
    a genset credited at min(its share, its load step):

    * E2, the largest load step: ``max(pickup_blocks)`` when given, else the
      hour's largest draw of any unit (frequency.py:1277): the largest load,
      a constant ``p_set`` per hour, or a GFM battery's charging or an island
      process unit's draw ``p0`` (ticket 08), a variable (a row each); up
      headroom ≥ it. ``pickup_sequence`` reports the order by criticality tier;
    * E3, a unit trips: I6 trips the largest |P| of its sources (every model
      but ups/none, frequency.py:1284–1286); a row per source covers
      whichever that is: the headroom without it ≥ p_u up, and for storage
      ≥ −p_u down (a charging battery's trip is surplus). A trip that leaves
      no grid-forming unit gets no row: no dispatch survives it (facts
      ``n1_no_former``);
    * E5, the island's PV output is lost (``kind`` pv only,
      frequency.py:1290): up headroom ≥ Σ p_pv.

    ``headroom_up(without)`` / ``headroom_down(without)``: (b↑)/(b↓) less
    one unit's own share."""
    if not gfm:
        return                   # no former at all: the MVA row refuses (c)
    loads = list(n.loads.index)          # the campus is drafted from every load of the network
    pv = active([u for u, s in units.items() if s["kind"] == "pv" and u in n.generators.index],
                lambda _: "Generator", period)
    gfl = active([u for u, s in units.items() if s["kind"] in ("pv", "wind", "fuel_cell")
                  and u in n.generators.index], lambda _: "Generator", period)
    n1 = facts.setdefault("n1_no_former", [])

    # E2
    if blocks:
        step = pd.Series(float(max(blocks)), index=sns_p)
    elif loads:
        step = _dense(n, "Load", "p_set", sns_p, loads).max(axis=1)
    else:
        step = None
    if step is not None:
        _row(m, headroom_up(), _Lin(sns_p).plus(step), f"Island-e2-up{tag}", coord, facts=facts)
        # what the row can reach at most, for the infeasibility hint: the GFM
        # storage at its rating (idle) and the gensets' step credit
        facts.setdefault("events", []).append({
            "row": f"Island-e2-up{tag}", "step_mw": float(step.max()),
            "gfm_mw": float(sum(_rating_max(n, "StorageUnit", s) for s in gfm)),
            "genset_mw": float(genset_credit_max)})
    if not blocks:
        # I6's "largest load" is the most negative p_mw of any campus unit in
        # the hour (frequency.py:1277 over ``_hour_rows``): a charging battery's
        # draw counts as a load step too.
        for s in gfm:
            _row(m, headroom_up(), p_store(_Lin(sns_p), s, -1.0), f"Island-e2-up-{s}{tag}", coord,
                 facts=facts)
        # ... and so does a process unit's draw (ticket 08): its p0 is a decision here, a row each.
        for u in active(_island_process_units(n), lambda _: "Link", period):
            _row(m, headroom_up(), _Lin(sns_p).var(m.variables["Link-p"].sel(snapshot=sns_p, name=u), 1.0),
                 f"Island-e2-up-{u}{tag}", coord, facts=facts)
    # E3. Left out, as they cannot bind: a PV unit's own trip when E5's row
    # (every PV unit lost, none below 0) is built, and a unit that cannot run
    # (p_nom 0, not extendable).
    implied = set(pv) if pv and (n.generators.loc[pv, "p_min_pu"] >= 0).all() \
        and not any(u in n.generators_t.p_min_pu for u in pv) else set()

    def can_run(u, comp):
        comp = comp or _genset_out(n, u)[0]
        var, fixed = _nominal(n, comp, u)
        return var is not None or fixed > 0

    sources = ([(s, "StorageUnit") for s in gfm] + [(g, None) for g in gensets]
               + [(u, "Generator") for u in gfl if u not in implied])
    for u, comp in sources:
        if u in gfm and len(gfm) == 1:
            n1.append(f"E3 of {u} leaves no grid former{tag}: not sized")
            continue
        if not can_run(u, comp):
            continue
        p_u = _Lin(sns_p)
        if comp == "StorageUnit":
            p_store(p_u, u, 1.0)
        elif comp == "Generator":
            p_u.var(m.variables["Generator-p"].sel(snapshot=sns_p, name=u), 1.0)
        else:
            p_out(p_u, u, 1.0)
        _row(m, headroom_up(u), p_u, f"Island-n1-up-{u}{tag}", coord, facts=facts)
        if comp == "StorageUnit":
            _row(m, headroom_down(u), p_u.scaled(-1.0), f"Island-n1-down-{u}{tag}", coord,
                 facts=facts)
    # E5
    if pv:
        lost = _Lin(sns_p)
        for u in pv:
            lost.var(m.variables["Generator-p"].sel(snapshot=sns_p, name=u), 1.0)
        _row(m, headroom_up(), lost, f"Island-e5-up{tag}", coord, facts=facts)


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


# ── the hourly island tables (I3a; owner option A, 2026-10-13) ─────────────
#
# gridspine owns the two contracts (``gridspine/schema/island.py``); the
# values come from here, evaluated on a solved network with the same
# definitions as the rows above. The campus study reads the *saved* project,
# where the LP is gone, so everything is computed from the stored results
# (dispatch, p_nom_opt, Generator/Link status, state of charge), never from
# the model. X_u, the late UPS relief, is free in the LP wherever its row
# does not bind, so it is taken at its physical value min(L_it, p_nom_u).


def _solved_nom(n, comp, name) -> float:
    frame = getattr(n, _LIST_NAMES[comp])
    if bool(frame.at[name, "p_nom_extendable"]) and "p_nom_opt" in frame.columns:
        v = float(frame.at[name, "p_nom_opt"])
        if np.isfinite(v):
            return v
    return float(frame.at[name, "p_nom"])


def _series(frame, name, index, default):
    if name in frame.columns:
        return frame[name].reindex(index).astype(float).fillna(default)
    return pd.Series(float(default), index=index)


def _storage_p(n, name, sns) -> pd.Series:
    """p_s = p_dispatch − p_store, positive when discharging."""
    st = n.storage_units_t
    p = getattr(st, "p", pd.DataFrame())
    if name in p.columns:
        return p[name].reindex(sns).astype(float).fillna(0.0)
    return (_series(getattr(st, "p_dispatch", pd.DataFrame()), name, sns, 0.0)
            - _series(getattr(st, "p_store", pd.DataFrame()), name, sns, 0.0))


def _solved_soc_before(n, sns, sns_p, name) -> pd.Series:
    """e(t−1) over one period, linked as ``_soc_before`` links it in the LP."""
    su = n.storage_units
    cyclic = bool(su.at[name, "cyclic_state_of_charge"])
    multi = isinstance(sns, pd.MultiIndex)
    within = multi and bool(su.at[name, "cyclic_state_of_charge_per_period"] if cyclic
                            else su.at[name, "state_of_charge_initial_per_period"])
    init = float(su.at[name, "state_of_charge_initial"])
    soc = _series(n.storage_units_t.state_of_charge, name, sns, 0.0)
    if within or not multi:
        s = soc.loc[sns_p]
        prev = s.shift(1)
        prev.iloc[0] = s.iloc[-1] if cyclic else init
        return prev
    prev = soc.shift(1)
    prev.iloc[0] = soc.iloc[-1] if cyclic else init
    return prev.loc[sns_p]


def _period_label(period, sns_p) -> int:
    """The campus tables' period key (``producers/campus.py``): the
    investment period, or the first snapshot's year."""
    if period is not None:
        return int(period)
    first = sns_p[0]
    return int(first.year) if hasattr(first, "year") else 0


def island_hourly(n, island, unit_ids=None):
    """``(island_unit_hourly, island_hourly)`` for a solved hub network and
    its validated sidecar, as gridspine's contracts (I3a). ``unit_ids`` maps
    a PyPSA name to the campus unit id (default: the PyPSA name).

    Per period, hour and scenario, with the I2b/I2c definitions:
    ΔP⁺ = p_imp − Σ_late min(L_it, p_nom), ΔP⁻ = −p_imp + Σ_transferring L_it
    (at 0⁺); M = Σ_online H·u·η·p_nom/cos φ + Σ_VSM H_v·p_nom/pf
    + Σ_droop GFM τ_f·p_nom/(2R) (``seamless`` and ``planned``: the units
    online at 0⁺), and under ``ups_bridged`` the started gensets at N+k;
    ΣK over the GFMs and droop gensets less the k largest genset unit's.
    Per unit: e(t−1), online (u under linearised UC, else the spinning flag),
    headroom both ways, and a UPS's IT load."""
    from services.adequacy.activity import active_mask
    # gridspine's contracts, through the guarded seam (a build without it says so).
    from services.adequacy.island_config import (
        GFM_CONTROLS,
        ISLAND_UNIT_HOURLY_COLUMNS,
        validate_island_hourly,
        validate_island_unit_hourly,
    )

    units, req = island["units"], island["requirements"]
    vals = req["values"]
    unit_ids = dict(unit_ids or {})
    solved = any(len(getattr(getattr(n, ts), attr, pd.DataFrame()).columns)
                 for ts, attr in (("generators_t", "p"), ("storage_units_t", "p"),
                                  ("storage_units_t", "state_of_charge"), ("links_t", "p0")))
    if not solved:
        raise IslandRefusal("the island hourly tables need a solved network; solve the project "
                            "and save it first")

    def named(kind, frames, pred=lambda s: True):
        return [u for u, s in units.items() if s["kind"] == kind and pred(s)
                and any(u in getattr(n, f).index for f in frames)]

    critical = named("load", ("loads",), lambda s: s["critical"])
    gensets = named("genset", ("generators", "links"))
    bess = named("bess", ("storage_units",))
    gfm = [s for s in bess if units[s].get("control") in GFM_CONTROLS]
    upss = named("ups", ("storage_units",))
    committed, _ = uc_gensets(n, island, gensets)
    k = int(vals["genset_redundancy_n"])
    pcc = _pcc_links(n)
    sns = n.snapshots
    unit_rows, hour_rows = [], []

    def spec(x):
        return units[x]["values"]

    for period, sns_p in _periods(sns):
        label = "ALL" if period is None else period
        on = lambda comp, x: bool(active_mask(n, comp, label).get(x, True))   # noqa: E731
        g_p = [g for g in gensets if on(_genset_out(n, g)[0], g)]
        gfm_p = [s for s in gfm if on("StorageUnit", s)]
        ups_p = [u for u in upss if on("StorageUnit", u)]
        hours = np.arange(len(sns_p))
        key = _period_label(period, sns_p)
        loads = n.get_switchable_as_dense("Load", "p_set", sns_p)
        l_c = loads[critical].sum(axis=1) if critical else pd.Series(0.0, index=sns_p)

        def it_load(u):
            it = [x for x in units[u]["it_load"] if x in loads.columns]
            return loads[it].sum(axis=1) if it else pd.Series(0.0, index=sns_p)

        # The gensets: output, online share and nominal output, per hour.
        gen = {}
        for g in g_p:
            comp, eta = _genset_out(n, g)
            ts = n.generators_t if comp == "Generator" else n.links_t
            p = _series(ts.p if comp == "Generator" else ts.p0, g, sns_p, 0.0) * eta
            if g in committed:
                u = _series(getattr(ts, "status", pd.DataFrame()), g, sns_p, 1.0)
            else:
                u = pd.Series(1.0 if units[g]["spinning"] else 0.0, index=sns_p)
            avail = n.get_switchable_as_dense(comp, "p_max_pu", sns_p)[g]
            gen[g] = {"p": p, "u": u, "nom": _solved_nom(n, comp, g) * eta, "avail": avail}

        # (I2b) the steps, numerically.
        p_imp = pd.Series(0.0, index=sns_p)
        for name in pcc["import"]:
            if on("Link", name):
                p_imp += float(n.links.at[name, "efficiency"]) * _series(n.links_t.p0, name,
                                                                         sns_p, 0.0)
        for name in pcc["export"]:
            if on("Link", name):
                p_imp -= _series(n.links_t.p0, name, sns_p, 0.0)
        joins = max((spec(g)["start_s"] + spec(g)["sync_s"] for g in g_p), default=0.0)
        late = [u for u in ups_p if units[u]["transfers_on_islanding"] and g_p
                and spec(u)["walk_in_s"] >= joins]
        transferring = [u for u in ups_p if units[u]["transfers_on_islanding"]]
        dp_up = p_imp - sum((np.minimum(it_load(u), _solved_nom(n, "StorageUnit", u))
                             for u in late), pd.Series(0.0, index=sns_p))
        dp_down = -p_imp + sum((it_load(u) for u in transferring), pd.Series(0.0, index=sns_p))

        # (d) M at 0⁺, and under ups_bridged; (e) ΣK.
        m_gfm = 0.0
        for s in gfm_p:
            nom = _solved_nom(n, "StorageUnit", s)
            m_gfm += (spec(s)["H_v_s"] / spec(s)["pf_rated"] * nom
                      if units[s]["control"] == "gfm_vsm"
                      else spec(s)["tau_f_s"] * nom / (2.0 * spec(s)["droop_pct"] / 100.0))
        m_instant = pd.Series(m_gfm, index=sns_p)
        for g in g_p:
            m_instant += spec(g)["H_s"] / spec(g)["cos_phi_r"] * gen[g]["u"] * gen[g]["nom"]
        unit_mw = {g: (float(spec(g)["unit_mw"]) if spec(g).get("unit_mw") is not None
                       else gen[g]["nom"]) for g in g_p}
        m_bridged = (sum(spec(g)["H_s"] / spec(g)["cos_phi_r"] * gen[g]["nom"] for g in g_p)
                     - k * max((spec(g)["H_s"] * unit_mw[g] / spec(g)["cos_phi_r"] for g in g_p),
                               default=0.0))
        droop_g = [g for g in g_p if units[g]["governor"] == "droop"]
        k_sum = (sum(_solved_nom(n, "StorageUnit", s) / (spec(s)["droop_pct"] / 100.0 * F0_HZ)
                     for s in gfm_p)
                 + sum(gen[g]["nom"] / (spec(g)["droop_pct"] / 100.0 * F0_HZ) for g in droop_g)
                 - k * max((unit_mw[g] / (spec(g)["droop_pct"] / 100.0 * F0_HZ)
                            for g in droop_g), default=0.0))

        for sc in req["scenarios"]:
            m = m_bridged if sc == "ups_bridged" else m_instant
            hour_rows.append(pd.DataFrame({
                "period": key, "hour": hours, "scenario": sc,
                "dp_up_mw": dp_up.to_numpy(dtype=float), "dp_down_mw": dp_down.to_numpy(dtype=float),
                "inertia_mws": np.broadcast_to(np.asarray(m, dtype=float), len(sns_p)).copy(),
                "k_mw_per_hz": float(k_sum), "critical_load_mw": l_c.to_numpy(dtype=float)}))

        nan = np.full(len(sns_p), np.nan)
        for g in g_p:
            d = gen[g]
            p_min = spec(g).get("p_min_pu")
            if p_min is None:
                comp = _genset_out(n, g)[0]
                p_min = float(getattr(n, _LIST_NAMES[comp]).at[g, "p_min_pu"])
            up = (d["u"] * d["avail"] * d["nom"] - d["p"]).clip(lower=0.0)
            down = (d["p"] - d["u"] * p_min * d["nom"]).clip(lower=0.0) * (d["u"] > 0)
            unit_rows.append(pd.DataFrame({
                "period": key, "hour": hours, "unit_id": unit_ids.get(g, g), "soc_start_mwh": nan,
                "online": d["u"].to_numpy(dtype=float), "headroom_up_mw": up.to_numpy(dtype=float),
                "headroom_down_mw": down.to_numpy(dtype=float), "p_ups_it_mw": nan}))
        for s in [x for x in bess if on("StorageUnit", x)] + ups_p:
            soc = _solved_soc_before(n, sns, sns_p, s).to_numpy(dtype=float)
            if s in ups_p:
                row = {"online": nan, "headroom_up_mw": nan, "headroom_down_mw": nan,
                       "p_ups_it_mw": it_load(s).to_numpy(dtype=float)}
            else:
                nom = _solved_nom(n, "StorageUnit", s)
                p_s = _storage_p(n, s, sns_p)
                row = {"online": np.ones(len(sns_p)),
                       "headroom_up_mw": (nom - p_s).to_numpy(dtype=float),
                       "headroom_down_mw": (p_s + nom).to_numpy(dtype=float), "p_ups_it_mw": nan}
            unit_rows.append(pd.DataFrame({"period": key, "hour": hours,
                                           "unit_id": unit_ids.get(s, s), "soc_start_mwh": soc,
                                           **row}))
    unit_df = (pd.concat(unit_rows, ignore_index=True) if unit_rows
               else pd.DataFrame(columns=list(ISLAND_UNIT_HOURLY_COLUMNS)))
    return (validate_island_unit_hourly(unit_df),
            validate_island_hourly(pd.concat(hour_rows, ignore_index=True)))

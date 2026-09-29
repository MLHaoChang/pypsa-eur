"""Detailed-grid -> PyPSA nodal converter + UC dispatch producer.
Nodal = identity region map: every PyPSA element name equals the canonical
detailed-grid name. The only module allowed to import pypsa."""
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa

from gridspine.schema.contracts import ContractError

# Normalised daily load shape (24 h), peak = 1.0 at hour 19. Ledgered
# assumption: synthetic shape for the vertical slice; real studies supply
# measured series.
LOAD_SHAPE = [
    0.62, 0.58, 0.56, 0.45, 0.56, 0.60, 0.68, 0.78, 0.86, 0.90, 0.92, 0.93,
    0.92, 0.90, 0.89, 0.90, 0.93, 0.97, 0.99, 1.00, 0.94, 0.86, 0.76, 0.67,
]

EXT_GRID_P_NOM_MW = 3000.0
EXT_GRID_MARGINAL_COST = 80.0  # EUR/MWh — import priced above all thermal units

# EUR/MWh. Not zero on purpose: a zero-cost renewable makes every curtailment
# pattern that spills the same energy cost-identical, so the solver picks one
# arbitrarily and the dispatch table stops being reproducible run to run. A
# small positive cost breaks that tie while keeping RES below every thermal
# unit (cheapest thermal is 10.0), so merit order is unchanged.
RES_MARGINAL_COST = 0.5

MIN_UP_TIME_H = 2
MIN_DOWN_TIME_H = 2


def _profile(series, snapshots: int, what: str) -> np.ndarray:
    """Length-checked per-unit profile -> plain array aligned to the snapshots.

    Alignment is POSITIONAL, by way of dropping the index. The profile modules
    index by hour-of-year and the network by `range(snapshots)`, so the two
    agree today; discarding the index rather than reindexing onto it means a
    caller who supplies a shifted or datetime-indexed series still gets hour i
    of the profile at snapshot i, instead of a silently all-NaN column that
    would read downstream as a unit that never generates.
    """
    values = np.asarray(series, dtype=float)
    if values.ndim != 1 or len(values) != snapshots:
        raise ContractError(
            f"{what}: expected {snapshots} values, got {len(values)}"
        )
    if not np.isfinite(values).all():
        raise ContractError(f"{what}: non-finite values")
    return values


def _res_rows(net, res_cf, snapshots: int):
    """(name, bus_idx, p_nom, cf array) per sgen row, or raise.

    A missing key is a ContractError rather than a zero profile: a RES unit that
    silently generates nothing still occupies its bus, still enters the dispatch
    table with status 0, and would push the ranking stage's residual-load and
    curtailment figures the wrong way with nothing in the artifacts to show why.
    An UNKNOWN key is rejected for the same reason from the other side — a
    typo'd `res_cf` key would otherwise be dropped in silence while the sgen it
    was meant for reports missing.
    """
    sgen = getattr(net, "sgen", None)
    have = dict(res_cf or {})
    if sgen is None or len(sgen) == 0:
        if have:
            raise ContractError(
                f"res_cf names units the net has no sgen for: {sorted(have)}"
            )
        return []

    names = list(sgen["name"])
    missing = [u for u in names if u not in have]
    if missing:
        raise ContractError(f"res_cf is missing a profile for sgen: {missing}")
    unknown = sorted(set(have) - set(names))
    if unknown:
        raise ContractError(f"res_cf names units the net has no sgen for: {unknown}")

    return [
        (s["name"], s["bus"], float(s["p_mw"]),
         _profile(have[s["name"]], snapshots, f"res_cf[{s['name']}]"))
        for _, s in sgen.iterrows()
    ]


def to_pypsa(net, snapshots=24, load_shape=None, res_cf=None) -> pypsa.Network:
    """Detailed grid -> PyPSA nodal network.

    `snapshots` is a COUNT (the network gets `range(count)`, as every study so
    far) or an INDEX — a `DatetimeIndex` for a network that will live in the
    GUI, whose templates and results views expect one. Profiles align
    positionally either way (`_profile`), so the two calls build the same
    numbers under different labels; `tests/gridspine/test_network_source.py`
    holds that.

    `load_shape=None` is the increment-1 default: the 24 h `LOAD_SHAPE`, so the
    two-argument call is unchanged. Supplying a `load_shape` of length
    `snapshots` replaces it (year-long profiles come in this way).

    `res_cf` maps every `net.sgen` canonical name to its per-hour capacity
    factor. The sgen rows become non-committable Generators with `p_max_pu` set
    to the capacity factor, so they are curtailable but never dispatchable
    above the resource.
    """
    n = pypsa.Network()
    if isinstance(snapshots, (int, np.integer)) and not isinstance(snapshots, bool):
        n.set_snapshots(range(int(snapshots)))
    else:
        n.set_snapshots(pd.Index(snapshots))
    snapshots = len(n.snapshots)
    bus_name = net.bus["name"]

    res = _res_rows(net, res_cf, snapshots)

    for _, b in net.bus.iterrows():
        n.add("Bus", b["name"], v_nom=b["vn_kv"])

    for i, ln in net.line.iterrows():
        vn = net.bus.at[ln["from_bus"], "vn_kv"]
        n.add(
            "Line", f"L_{i:02d}",
            bus0=bus_name.at[ln["from_bus"]], bus1=bus_name.at[ln["to_bus"]],
            r=ln["r_ohm_per_km"] * ln["length_km"] / ln["parallel"],
            x=ln["x_ohm_per_km"] * ln["length_km"] / ln["parallel"],
            s_nom=np.sqrt(3) * vn * ln["max_i_ka"] * ln["parallel"],
        )

    for i, tr in net.trafo.iterrows():
        n.add(
            "Transformer", f"T_{i:02d}",
            bus0=bus_name.at[tr["hv_bus"]], bus1=bus_name.at[tr["lv_bus"]],
            s_nom=tr["sn_mva"], x=tr["vk_percent"] / 100.0,
            r=tr["vkr_percent"] / 100.0, tap_ratio=1.0, model="t",
        )

    per_bus = net.load.groupby("bus")["p_mw"].sum()
    if load_shape is None:
        shape = pd.Series(LOAD_SHAPE[:snapshots], index=n.snapshots)
    else:
        shape = pd.Series(
            _profile(load_shape, snapshots, "load_shape"),
            index=n.snapshots,
        )
    for b, p in per_bus.items():
        n.add("Load", f"LD_{bus_name.at[b]}", bus=bus_name.at[b], p_set=shape * float(p))

    for i, (_, g) in enumerate(net.gen.iterrows()):
        p_nom = g.get("max_p_mw", np.nan)
        if not np.isfinite(p_nom) or p_nom <= 0:
            p_nom = 1.2 * g["p_mw"]
        n.add(
            "Generator", g["name"], bus=bus_name.at[g["bus"]],
            p_nom=float(p_nom), committable=True, p_min_pu=0.3,
            min_up_time=MIN_UP_TIME_H, min_down_time=MIN_DOWN_TIME_H,
            start_up_cost=1000.0,
            marginal_cost=10.0 + 4.0 * i,
        )

    for _, e in net.ext_grid.iterrows():
        n.add("Generator", e["name"], bus=bus_name.at[e["bus"]],
              p_nom=EXT_GRID_P_NOM_MW, committable=False,
              marginal_cost=EXT_GRID_MARGINAL_COST)

    for name, bus, p_nom, cf in res:
        n.add(
            "Generator", name, bus=bus_name.at[bus],
            p_nom=p_nom, committable=False,
            marginal_cost=RES_MARGINAL_COST,
            # p_min_pu stays at its 0.0 default: the resource is an upper
            # bound, so spilling it is always feasible (curtailable).
            p_max_pu=pd.Series(cf, index=n.snapshots),
        )
    return n


def to_loads_table(n: pypsa.Network, net) -> pd.DataFrame:
    """Solved-or-unsolved PyPSA network + its source grid -> validated LoadsTable.

    Rows are (bus, hour, p_mw, q_mvar) — one per PyPSA Load per snapshot, keyed
    by the canonical BUS name rather than the load name, because that is the
    key stage 2 sets `net.load` on and the key `ranking.metrics` aggregates by.

    Two arguments, not one. `p_mw` comes from `n.loads_t.p_set`, which is the
    whole of what the dispatch model knows about demand: PyPSA solves a real-
    power problem and carries no reactive load at all. Q therefore has to come
    from the detailed grid, and `net` is the only place it exists.

    LEDGER ASSUMPTION — CONSTANT POWER FACTOR. `q_mvar` is the bus's native
    pandapower Q scaled by the same factor the hour applies to its P, i.e.
    `q = p * (Q_native / P_native)` per bus. The power factor of every bus is
    thus held at its case39 value for all 8760 hours. Real demand's power
    factor moves with the load mix, so this is an assumption and not a
    measurement; it is recorded in the driver's manifest ledger.

    A bus whose native P is zero has no defined ratio and takes `q = 0.0`
    rather than a division result: the alternative is an inf or a NaN, and
    `validate_loads` would (correctly) refuse the table either way, turning a
    degenerate but harmless input into a hard stop three lines later.
    """
    from gridspine.schema.dispatch import validate_loads

    bus_name = net.bus["name"]
    native = net.load.groupby("bus")[["p_mw", "q_mvar"]].sum()
    ratio = {}
    for bus_idx, rec in native.iterrows():
        p_native = float(rec["p_mw"])
        ratio[bus_name.at[bus_idx]] = (
            float(rec["q_mvar"]) / p_native if p_native != 0.0 else 0.0
        )

    p_set = n.loads_t.p_set
    bus_of = n.loads["bus"]
    missing = sorted(set(bus_of) - set(ratio))
    if missing:
        raise ContractError(
            f"PyPSA loads sit on buses the source grid has no load for: {missing}"
        )
    # And the other direction, which went unchecked: a network that has LOST a
    # load (deleted in the GUI, or never carried) produces a table covering all
    # but that bus. It parses, it validates, and `snapshot_metrics` then ranks
    # the year on a `load_mw` short by that whole bus — until `apply_snapshot`
    # refuses it three stages later, under the `loadflow` stage, for a defect
    # that belongs to this producer. The external producer checks both
    # directions of exactly this correspondence (`_check_load_buses`).
    unmentioned = sorted(set(ratio) - set(bus_of))
    if unmentioned:
        raise ContractError(
            "the source grid carries load at buses the PyPSA network has no load "
            f"for: {unmentioned} — the demand table would be short by them at "
            "every hour"
        )

    rows = []
    for hour, snap in enumerate(n.snapshots):
        for load_name in p_set.columns:
            bus = bus_of.at[load_name]
            p_mw = float(p_set.at[snap, load_name])
            rows.append({
                "bus": bus,
                "hour": hour,
                "p_mw": p_mw,
                "q_mvar": p_mw * ratio[bus],
            })
    return validate_loads(pd.DataFrame(rows))


_STATUS_P_TOL_MW = 1e-4


def run_uc(n: pypsa.Network) -> pypsa.Network:
    status, condition = n.optimize(solver_name="highs")
    if condition != "optimal":
        raise RuntimeError(f"UC solve not optimal: {status}/{condition}")
    return n


def _rounded_status(n: pypsa.Network, sns) -> pd.DataFrame:
    """Solved commitment over `sns`, snapped to exact 0/1.

    Load-bearing, not cosmetic. PyPSA derives the next window's
    `up_time_before` by testing `status.cumsum() == 1, 2, 3, ...` on the frozen
    values, and reads "is this unit up" as `astype(bool)`. A MIP feasibility
    tolerance of 1e-6 means HiGHS may hand back 0.9999999 for a committed unit;
    that compares unequal to 1 in the cumsum test, so the trailing run measures
    as ZERO hours and the seam constraint silently disappears — while
    `astype(bool)` still reads 1e-9 as "up". Rounding once, here, is what makes
    the carried statuses mean what the constraint assumes they mean.
    """
    return n.generators_t.status.loc[sns].round().astype(float)


def _write_status(n: pypsa.Network, frozen: pd.DataFrame) -> None:
    """Publish the frozen statuses where PyPSA's seam logic reads them.

    `define_committable_variables_constraints` looks at
    `n.generators_t.status` for the snapshots BEFORE the window it is solving
    and turns the trailing run into `up_time_before` / `down_time_before`. So
    writing this frame IS the freeze: nothing else carries commitment across a
    window boundary. Hours at or after the window start are written as 0 and
    are re-solved, which is what makes the overlap an overlap.
    """
    full = pd.DataFrame(0.0, index=n.snapshots, columns=n.generators.index)
    if not frozen.empty:
        full.loc[frozen.index, frozen.columns] = frozen
    n.generators_t["status"] = full


# Relative MIP gap for the rolling solve. Measured on case39_res (9 committable
# units, 46 lines), exact solves to proven optimality: 24 h = 6.0 s, 48 h =
# 32.0 s, 72 h = 488.4 s — superlinear, so a 168 h window never finishes. The
# same 168 h window closes to 1 % in 21 s. The gap is an OPTIMALITY tolerance,
# not a feasibility one: min_up_time, min_down_time, p_min_pu and the energy
# balance are hard constraints and hold exactly at any gap, so the seam
# contract is unaffected. `run_uc` (short horizons) stays exact.
DEFAULT_MIP_REL_GAP = 0.01


def run_uc_rolling(
    n: pypsa.Network,
    window: int = 168,
    overlap: int = 24,
    mip_rel_gap: float = DEFAULT_MIP_REL_GAP,
    on_window=None,
) -> pypsa.Network:
    """Solve committable UC over `n.snapshots` in overlapping windows.

    Window k covers `[t0, t0 + window)` and the solve advances by
    `window - overlap`, so the last `overlap` hours of every window are thrown
    away and re-solved with the next window's lookahead behind them. Only the
    pre-overlap hours are frozen, and the freeze is carried into the next solve
    through `n.generators_t.status` (see `_write_status`) — which is where
    PyPSA reads the min-up/min-down history from.

    Why the overlap exists: a window that ends at hour T has no idea what
    happens at T+1, so it shuts units down towards its own horizon end to avoid
    paying for fuel it cannot use. Discarding those hours and re-deciding them
    with a fresh window behind them removes that end-of-horizon artefact.

    A window is a real MILP over its own snapshots, so the seam is the only
    place commitment can go wrong; `tests/gridspine/test_producer_year.py`
    asserts run lengths over the ASSEMBLED series for exactly that reason.

    `on_window(done, total)` is called after each window is solved and frozen —
    the only progress this loop can honestly report, since a window is one
    MILP. A caller that wants to stop raises from it (the driver's `Progress`
    does exactly that): the next window is never started and nothing partial
    is written, because the dispatch table is assembled only at the end.
    """
    if window <= 0 or window % 24 != 0:
        raise ContractError(
            f"window must be a positive whole number of days, got {window}"
        )
    if overlap < 0 or overlap >= window:
        raise ContractError(
            f"overlap must satisfy 0 <= overlap < window={window}, got {overlap}"
        )
    if not (0.0 <= mip_rel_gap < 1.0):
        raise ContractError(
            f"mip_rel_gap must satisfy 0 <= gap < 1, got {mip_rel_gap}"
        )

    sns = n.snapshots
    step = window - overlap
    n_windows = 1 + -(-max(0, len(sns) - window) // step)   # ceil division
    frozen_status, dispatch = [], []
    t0 = 0
    solved = 0
    while True:
        w_sns = sns[t0:t0 + window]
        status, condition = n.optimize(
            w_sns, solver_name="highs",
            solver_options={"mip_rel_gap": mip_rel_gap},
        )
        if condition != "optimal":
            raise RuntimeError(
                f"UC solve not optimal in window [{t0}, {t0 + len(w_sns)}) "
                f"of {len(sns)}: {status}/{condition}"
            )
        last = t0 + window >= len(sns)
        # Keep the whole window only when there is no next window to re-solve
        # its tail; otherwise keep the pre-overlap hours and drop the rest.
        keep = w_sns if last else sns[t0:t0 + step]
        frozen_status.append(_rounded_status(n, keep))
        dispatch.append(n.generators_t.p.loc[keep])
        solved += 1
        if on_window is not None:
            on_window(solved, n_windows)
        if last:
            break
        t0 += step
        _write_status(n, pd.concat(frozen_status))

    # Assemble explicitly rather than trusting whatever the last window left in
    # the network: every hour comes from the window that owned it.
    n.generators_t["status"] = pd.concat(frozen_status).reindex(sns)
    n.generators_t["p"] = pd.concat(dispatch).reindex(sns)
    return n


def to_dispatch_table(n: pypsa.Network) -> pd.DataFrame:
    """Solved network -> validated DispatchTable.

    `q_mvar = 0.0` for every row: generators enter the load flow as PV nodes,
    so Q is a load-flow RESULT, not a dispatch decision (ledgered assumption).
    Non-committable units carry no `status` variable, so their commitment is
    inferred from output: status 1 iff |p| exceeds the tolerance.
    """
    from gridspine.schema.dispatch import validate_dispatch

    rows = []
    p = n.generators_t.p
    committable = n.generators["committable"]
    status_t = getattr(n.generators_t, "status", pd.DataFrame())
    for hour, snap in enumerate(n.snapshots):
        for unit in n.generators.index:
            p_mw = float(p.at[snap, unit])
            if committable.at[unit] and unit in status_t.columns:
                st = int(round(float(status_t.at[snap, unit])))
            else:
                st = 1 if abs(p_mw) > _STATUS_P_TOL_MW else 0
            if st == 0:
                p_mw = 0.0  # zero out solver residuals below tolerance
            rows.append({"unit_id": unit, "hour": hour, "p_mw": p_mw,
                         "q_mvar": 0.0, "status": st})
    return validate_dispatch(pd.DataFrame(rows))


# --------------------------------------------------------------------------
# A solved network as the dispatch source (increment 5, D3)
# --------------------------------------------------------------------------

#: Recorded in the manifest's `dispatch_source.commitment`. Measured 2026-09-08
#: on a netcdf round-trip: PyPSA writes `generators_t.status` when at least one
#: unit ever departs from the default (1), and writes NOTHING when every unit
#: is on at every hour — then the saved project carries no commitment column
#: and `to_dispatch_table` infers it from output. Exact for these units: a
#: committed case39 machine runs at >= 30 % of p_nom (`p_min_pu=0.3`), so
#: "producing" and "committed" coincide.
COMMITMENT_SOLVED = "from generators_t.status of the solved network"
COMMITMENT_INFERRED = (
    "inferred from p_mw (status = 1 iff |p_mw| > 1e-4 MW): the saved network "
    "carries no commitment status; exact for case39 units (p_min_pu = 0.3)"
)


def _commitment_note(n: pypsa.Network) -> str:
    status = getattr(n.generators_t, "status", pd.DataFrame())
    committable = [u for u in n.generators.index if bool(n.generators.at[u, "committable"])]
    inferred = [u for u in committable if u not in status.columns]
    if not inferred:
        return COMMITMENT_SOLVED
    if len(inferred) == len(committable):
        return COMMITMENT_INFERRED
    return f"{COMMITMENT_SOLVED} for {sorted(set(committable) - set(inferred))}; {COMMITMENT_INFERRED} for {inferred}"


def load_solved_network(path) -> pypsa.Network:
    """Read a saved network. Existence is a ContractError here rather than a
    FileNotFoundError three frames down, because the path came from a config."""
    path = Path(path)
    if not path.is_file():
        raise ContractError(
            f"dispatch source network not found: {path} (expected a saved network.nc)"
        )
    return pypsa.Network(str(path))


def tables_from_network(n: pypsa.Network, net, registry):
    """Solved PyPSA network -> (DispatchTable, LoadsTable, commitment note).

    The spec's IDENTITY MAP, checked rather than assumed. Everything after the
    dispatch is the detailed grid `net`, so the network is a source exactly
    when its generators are that grid's units:

    - the same unit ids in BOTH directions — a unit the network lacks would
      leave a machine with no dispatch row for `apply_snapshot` to set, and a
      unit the grid lacks has no machine to be; both are named;
    - each on the bus the grid says — a unit moved to another bus would enter
      the load flow at a node it does not feed;
    - solved: `generators_t.p` covers every unit with no NaN. An unsolved
      project is the most likely mistake and gets the clearest message.

    A PyPSA-Eur clustered network fails the first check by construction. That
    is the point: disaggregating a clustered dispatch onto the detailed grid is
    the spec's "clustered producer", a later item, not a silent best effort.

    Loads take the detailed grid's constant power factor, exactly as a
    generated dispatch does (`to_loads_table`); commitment comes from the
    status column for the units that have one and is inferred from output for
    the rest (`_commitment_note`, recorded in the manifest).
    """
    units = set(n.generators.index)
    expected = set(registry.index)
    missing = sorted(expected - units)
    extra = sorted(units - expected)
    if missing or extra:
        raise ContractError(
            "network is not the detailed grid's unit set (identity map): "
            f"missing {missing}, unknown {extra}"
        )
    wrong_bus = [
        (u, str(n.generators.at[u, "bus"]), str(registry.at[u, "bus"]))
        for u in registry.index
        if str(n.generators.at[u, "bus"]) != str(registry.at[u, "bus"])
    ]
    if wrong_bus:
        raise ContractError(
            "units sit on a different bus than the detailed grid: "
            + "; ".join(f"{u} on {got}, grid has it on {want}" for u, got, want in wrong_bus)
        )
    p = n.generators_t.p
    if p.empty or set(p.columns) != units or p.isna().any().any():
        raise ContractError(
            "network is not solved: generators_t.p does not cover every unit — "
            "solve the project and save it, then pick it as the dispatch source"
        )
    return to_dispatch_table(n), to_loads_table(n, net), _commitment_note(n)

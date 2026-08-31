"""Detailed-grid -> PyPSA nodal converter + UC dispatch producer.
Nodal = identity region map: every PyPSA element name equals the canonical
detailed-grid name. The only module allowed to import pypsa."""
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


def to_pypsa(net, snapshots: int = 24, load_shape=None, res_cf=None) -> pypsa.Network:
    """Detailed grid -> PyPSA nodal network.

    `load_shape=None` is the increment-1 default: the 24 h `LOAD_SHAPE`, so the
    two-argument call is unchanged. Supplying a `load_shape` of length
    `snapshots` replaces it (year-long profiles come in this way).

    `res_cf` maps every `net.sgen` canonical name to its per-hour capacity
    factor. The sgen rows become non-committable Generators with `p_max_pu` set
    to the capacity factor, so they are curtailable but never dispatchable
    above the resource.
    """
    n = pypsa.Network()
    n.set_snapshots(range(snapshots))
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
    frozen_status, dispatch = [], []
    t0 = 0
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

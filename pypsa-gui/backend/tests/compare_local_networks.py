"""
Locally-built networks for the three Compare tabs the golden fixture cannot
exercise: curtailment, lost load, storage cycling. NOT a test module (no
`test_` prefix — mirrors `compare_support.py`, never collected by pytest).

Why these exist: a per-tab census on the golden fixture (see
`test_compare_invariants.py::KNOWN_VACUOUS_TABS`) measured curtailment,
lost_load and storage_cycling all at 0 judged comparisons — no generator has
a time-varying `p_max_pu`, no VOLL capture is ever written for the golden
network (its project_dir is deliberately nonexistent), and the golden
storage unit's dispatch is EXACTLY zero on every snapshot (flat solar, flat
demand, zero-cost storage: no arbitrage incentive). The task-11/12/13 brief
is explicit that `tests/golden/fixture.py` must NOT be modified — changing it
would perturb every other test's LP optimum — so each tab gets its own small,
purpose-built network here instead.

Each network is deliberately tiny (a handful of buses/snapshots) per the
task's "these run in the gate on every future change" constraint.
"""
from __future__ import annotations

import pathlib
import pickle

import pandas as pd
import pypsa


# ── Task 11: curtailment ────────────────────────────────────────────────────

def build_curtailment_network() -> pypsa.Network:
    """
    One bus, one renewable generator with a TIME-VARYING `p_max_pu` profile,
    and a load fixed BELOW the profile's available power at every snapshot.
    With no storage, no export and no other generator, nodal balance forces
    dispatch == load exactly, so the LP leaves real, non-zero curtailment
    (available − dispatch) at every snapshot — unlike the golden fixture's
    solar generator, which carries a flat `p_max_pu` (no profile column at
    all) and therefore never contributes to `_compute_curtailment_summary`.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "b1", carrier="AC")
    n.add("Carrier", "AC")
    n.add("Carrier", "solar")
    n.add(
        "Generator", "solar",
        bus="b1", carrier="solar",
        p_nom=100.0, p_nom_extendable=False,
        # A genuinely zero marginal cost makes the whole LP's objective
        # identically zero across every component (nothing else on this
        # network carries a cost either), which newer PyPSA/linopy refuses
        # to build ("Objective function could not be created"). A nominal
        # 1 EUR/MWh keeps the network economically trivial (it's still the
        # ONLY generator, so nodal balance still forces dispatch == load
        # regardless of cost) while giving linopy a non-empty objective.
        marginal_cost=1.0,
        # Always well above the 20 MW load, so every snapshot stays feasible
        # while still leaving a large, time-varying curtailed gap.
        p_max_pu=[1.0, 0.6, 1.0, 0.6],
    )
    n.add("Load", "load1", bus="b1", p_set=20.0)
    return n


def solve_curtailment_network() -> pypsa.Network:
    n = build_curtailment_network()
    n.optimize(solver_name="highs")
    return n


def install_network(n: pypsa.Network) -> None:
    """
    Install `n` as the active singleton so live `/results/*` endpoint
    functions (imported and called directly, e.g. `routers.results.
    get_curtailment()`) read the SAME network a paired `compare_support.
    summarise(n)` call reads. Generic sibling of
    `tests.golden.fixture.install_golden` — these tabs' networks are
    deliberately NOT the shared golden fixture (see module docstring), so
    they need their own installer rather than reusing that one.
    """
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig
    import routers.simulation as sim_router

    ctx = PyPSAService._ensure_active()
    ctx.network = n
    sim_router._state["solver_config"] = SolverConfig()


# ── Task 12: lost load ──────────────────────────────────────────────────────

def build_lost_load_network() -> pypsa.Network:
    """
    Two buses on different carriers — just enough shape for the per-bus and
    per-carrier lost-load roll-ups to be non-trivial. No solve is needed:
    `_compute_lost_load_summary` (routers/compare.py) reads its numbers
    entirely from a `results_state.pkl` capture (see `write_lost_load_
    capture` below), never from `n.generators_t` — the VOLL slack generators
    are stripped from the network right after the capture is taken (solver_
    service._capture_and_remove_slacks), which is WHY the capture has to
    live in a side-channel pickle instead of surviving on the netcdf.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "bus_elec", carrier="AC")
    n.add("Bus", "bus_h2", carrier="H2")
    return n


def write_lost_load_capture(
    project_dir: pathlib.Path,
    n: pypsa.Network,
    *,
    per_bus_mwh: dict[str, list[float]],
    voll: float,
) -> tuple[float, float]:
    """
    Write a `results_state.pkl` VOLL-slack capture into `project_dir`, in the
    EXACT format `_compute_lost_load_summary` (routers/compare.py:2306)
    reads and `solver_service._capture_and_remove_slacks` (:4429) writes:

        {"__schema__": 1, "data": {
            "last_lost_load": {
                "lost_load_t": DataFrame(snapshot x bus),  # MW, >= 0
                "lost_load_total_mwh": float,
                "lost_load_cost_eur": float,
            },
            <every other RESULT_STATE_KEYS entry>: None,
        }}

    `lost_load_t` is indexed by the snapshots at solve time (here, `n.
    snapshots` — `_compute_lost_load_summary` reindexes onto the CURRENT
    network's snapshots, so using the same index up front avoids an
    unintended reindex-to-NaN-then-fillna(0) that would just zero everything
    out). Columns are bare bus names (the `__voll_` prefix is stripped before
    capture — see solver_service.py:4441).

    Returns `(total_mwh, total_cost_eur)` — the SUM of `per_bus_mwh`, i.e.
    what a caller should expect `total_mwh_scalar`/`total_cost_scalar` to
    carry (used only to derive `voll_eur_per_mwh` inside the compute
    function; the reported `total_mwh`/`total_cost_meur` come from
    `lost_load_t` itself, reindexed and snapshot-weighted).
    """
    from routers.projects import _RESULTS_STATE_SCHEMA
    from services.project_context import RESULT_STATE_KEYS

    df = pd.DataFrame(per_bus_mwh, index=n.snapshots)
    # SNAPSHOT-WEIGHTED, generators basis — mirrors the solver's capture
    # since the 2026-08-14 fix (the raw MW sum understated energy by the
    # weight factor on representative-period networks; on the unit-weight
    # networks every existing caller builds, weighted == raw). Keeping this
    # helper byte-faithful to solver_service._capture_and_remove_slacks is
    # its entire job — see the format contract in the docstring above.
    from services import period_utils
    w = period_utils.snapshot_weights(n, "generators").reindex(df.index).fillna(1.0)
    total_mwh = float(df.clip(lower=0).mul(w, axis=0).to_numpy().sum())
    total_cost = total_mwh * voll

    data: dict = {k: None for k in RESULT_STATE_KEYS}
    data["last_lost_load"] = {
        "lost_load_t": df,
        "lost_load_total_mwh": total_mwh,
        "lost_load_cost_eur": total_cost,
    }
    payload = {"__schema__": _RESULTS_STATE_SCHEMA, "data": data}

    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "results_state.pkl").write_bytes(pickle.dumps(payload))
    return total_mwh, total_cost


# ── Task 13: storage cycling ────────────────────────────────────────────────

def _add_arbitrage_assets(n: pypsa.Network, load_pattern: list[float]) -> None:
    """
    Shared asset set for both the flat and multi-period storage-cycling
    networks: a cheap "base" generator that cannot alone cover the high-load
    snapshots, an expensive "peak" generator that can, and a lossless,
    zero-cost storage unit sized so it can fully absorb "base"'s spare
    capacity during low-load snapshots and discharge it back during
    high-load snapshots. Routing energy through storage this way costs
    10 EUR/MWh (base) instead of 200 EUR/MWh (peak) — a genuine LP arbitrage
    incentive, unlike the golden fixture where flat solar + flat demand +
    zero-cost storage leaves nothing for the optimiser to trade.

    Low-load feasibility: base (p_nom=50) covers load (30) with exactly 20
    MW of spare capacity — matching storage's own p_nom (20 MW), so "charge
    at full power every low snapshot" is feasible without exceeding base's
    own p_nom.
    High-load feasibility: base (50) + storage discharge (<=20) + peak (50)
    = 120 >= 90, so peak alone never needs to exceed its own p_nom either.
    """
    n.add("Carrier", "base")
    n.add("Carrier", "peak")
    n.add(
        "Generator", "base", bus="b1", carrier="base",
        p_nom=50.0, p_nom_extendable=False, marginal_cost=10.0,
    )
    n.add(
        "Generator", "peak", bus="b1", carrier="peak",
        p_nom=50.0, p_nom_extendable=False, marginal_cost=200.0,
    )
    n.add(
        "StorageUnit", "bess", bus="b1",
        p_nom=20.0, p_nom_extendable=False, max_hours=2.0,
        capital_cost=0.0, marginal_cost=0.0,
    )
    n.add("Load", "load1", bus="b1", p_set=load_pattern)


def build_storage_cycling_flat_network() -> pypsa.Network:
    """
    Flat (single-period) network — 8 hourly snapshots alternating a 30 MW
    "low" load and a 90 MW "high" load. Flat on purpose: `_compute_storage_
    cycling_summary`'s horizon-wide `cycles.total` only reduces to the plain
    `throughput / (2 x energy_capacity)` oracle on the non-multi-period
    branch (`else: cycles_total = tp_total / (2 * final_ec)`); on a
    multi-period network `cycles.total` is instead the AVERAGE of per-period
    cycles (see `build_storage_cycling_multi_network` below), a DIFFERENT
    quantity by a factor of the period count. Keeping this network flat is
    what makes Task 13's oracle test an identity rather than an off-by-N bug.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=8, freq="h"))
    n.add("Bus", "b1", carrier="AC")
    n.add("Carrier", "AC")
    _add_arbitrage_assets(n, [30.0, 90.0] * 4)
    return n


def solve_storage_cycling_flat_network() -> pypsa.Network:
    n = build_storage_cycling_flat_network()
    n.optimize(solver_name="highs")
    return n


def build_storage_cycling_multi_network(
    periods: tuple[int, int] = (2030, 2035),
    years: tuple[float, float] = (5.0, 5.0),
) -> pypsa.Network:
    """
    Same bus/asset/load shape as the flat network, replicated identically
    under TWO investment periods — enough for `cycles.by_period` to carry
    >= 2 entries so `test_horizon_cycles_are_the_average_of_periods_not_
    the_sum` has something real to check (on the golden fixture this guard
    test's own `if len(u.cycles.by_period) < 2: continue` always fires,
    since the golden storage unit never cycles at all). No `overnight_cost`
    or `discount_rate` dependency anywhere in this network (every asset is
    non-extendable, capital_cost fixed at 0.0), so — unlike
    `tests/golden/fixture.py` — this solves directly through `n.optimize
    (multi_investment_periods=True)` with no `with_periodized_cost_
    defaults` wrapper needed.
    """
    n = pypsa.Network()
    timesteps = pd.date_range("2030-01-01", periods=8, freq="h")
    idx = pd.MultiIndex.from_product([list(periods), timesteps], names=["period", "timestep"])
    # A MultiIndex built with from_product has `.name = None`; a multi-period
    # LP needs `.name == "snapshot"` or linopy raises `dim_0 is not a valid
    # dimension` (see CLAUDE.md's "multi->multi rebuild" pitfall).
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.investment_periods = list(periods)
    n.investment_period_weightings["years"] = list(years)

    n.add("Bus", "b1", carrier="AC")
    n.add("Carrier", "AC")
    _add_arbitrage_assets(n, [30.0, 90.0] * 4 * len(periods))
    return n


def solve_storage_cycling_multi_network() -> pypsa.Network:
    n = build_storage_cycling_multi_network()
    n.optimize(solver_name="highs", multi_investment_periods=True)
    return n


# ── Task 15: weighting basis (suspect S2) ───────────────────────────────────

def build_weighting_basis_network() -> pypsa.Network:
    """
    Two buses linked by a single, non-extendable, thermally-limited Line,
    sized and loaded so the line is congested (loading >= 0.99, the
    "binding" threshold `_compute_loading_summary` uses) at SOME snapshots
    and not others — and so that congestion produces REAL locational price
    separation, useful for both the loading and the prices half of Task 15's
    basis question.

    `b1` carries a cheap generator (marginal_cost=10) whose only route to
    the load is across `L1` (s_nom=25). `b2` carries a load that varies
    snapshot to snapshot (20/40/30/40 MW) and an expensive backup generator
    (marginal_cost=200) that only dispatches once `L1`'s 25 MW cap is
    exhausted. Verified directly against the solved network before any test
    was written against it: snapshot 0 (load=20 <= 25) leaves `L1` at 80%
    loading and both buses priced at 10; snapshots 1-3 (load=30/40 > 25)
    push `L1` to exactly 100% loading (its cap) and separate the two buses'
    prices to 10 / 200 — a genuine LP-congestion outcome, not a hand-set
    dual. This is what makes the fixture usable for the weighting-basis
    question at all: the golden fixture's `L_ab` line never binds and its
    `objective`/`generators` snapshot_weightings columns are identical, so
    neither `_compute_loading_summary` nor `_compute_prices_summary` can be
    told which basis they're actually using from that fixture alone.

    Deliberately flat (single-period, 4 snapshots) — the weighting-basis
    question this network exists to settle doesn't need
    investment_period_weightings in the mix, and a flat network keeps the
    fixture (and the LP) as small as this suite's "runs in the gate on
    every change" constraint asks for.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "b1", carrier="AC")
    n.add("Bus", "b2", carrier="AC")
    n.add("Carrier", "AC")
    n.add("Carrier", "gas")
    n.add("Carrier", "backup")
    n.add(
        "Generator", "gas", bus="b1", carrier="gas",
        p_nom=100.0, p_nom_extendable=False, marginal_cost=10.0,
    )
    n.add(
        "Generator", "backup", bus="b2", carrier="backup",
        p_nom=100.0, p_nom_extendable=False, marginal_cost=200.0,
    )
    n.add(
        "Line", "L1", bus0="b1", bus1="b2",
        s_nom=25.0, s_nom_extendable=False, x=0.1, r=0.01,
    )
    n.add("Load", "load1", bus="b2", p_set=[20.0, 40.0, 30.0, 40.0])
    return n


def solve_weighting_basis_network() -> pypsa.Network:
    n = build_weighting_basis_network()
    n.optimize(solver_name="highs")
    return n

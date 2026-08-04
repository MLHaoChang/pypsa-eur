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
    total_mwh = float(df.to_numpy().sum())
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

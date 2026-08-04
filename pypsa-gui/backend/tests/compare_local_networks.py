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

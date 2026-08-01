"""
The golden network: one small, real, SOLVED network containing every shape that
has produced a wrong number in this app.

Solved for real rather than hand-built. 11 backend test files already call
`.optimize()`, so this follows convention — but the deciding reason is that a
hand-built solved state encodes the author's belief about PyPSA's sign and
weighting conventions. If that belief is wrong, every surface agrees with every
other and all of them are wrong. Letting HiGHS produce the dispatch removes
that failure mode entirely.

Composition is deliberately MODERATE: only shapes that have actually failed.
It grows by incident, not by imagination.
"""
from __future__ import annotations

import pandas as pd
import pypsa

GOLDEN_DISCOUNT_RATE = 0.07
GOLDEN_PERIODS = (2030, 2035)
# DIFFERENT on purpose. Equal weights would hide an averaging bug, which is
# exactly the class of error the 22% Asset Detail gap belonged to.
GOLDEN_YEARS = (5, 10)
SNAPSHOTS_PER_PERIOD = 24

_SOLVED: pypsa.Network | None = None


def build_golden_network() -> pypsa.Network:
    n = pypsa.Network()

    timesteps = pd.date_range("2030-01-01", periods=SNAPSHOTS_PER_PERIOD, freq="h")
    idx = pd.MultiIndex.from_product(
        [list(GOLDEN_PERIODS), timesteps], names=["period", "timestep"]
    )
    # CLAUDE.md: a MultiIndex built with from_product has `.name = None`, and a
    # multi->multi rebuild propagates that to every _t table, after which
    # linopy reports `dim_0 is not a valid dimension`. Always set it.
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.investment_periods = list(GOLDEN_PERIODS)
    n.investment_period_weightings["years"] = list(GOLDEN_YEARS)

    n.add("Bus", "elec_a")
    n.add("Bus", "elec_b")
    n.add("Bus", "h2", carrier="H2")
    n.add("Carrier", "H2")
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Carrier", "solar")

    # --- the shape that broke: cost via overnight_cost, capital_cost unset ---
    # discount_rate is deliberately left unset here (stays NaN). PyPSA 1.1.2's
    # consistency_check requires SOME discount_rate on any asset carrying
    # overnight_cost, but the app never writes it onto the asset permanently
    # — solver_service.with_periodized_cost_defaults() fills it from
    # SolverConfig.discount_rate transiently around n.optimize() and reverts
    # afterwards (see solve_golden_network below). Baking a value in here
    # would defeat the exact shape this fixture exists to carry: the 22-100%
    # Asset Detail gap came from resolvers that read the per-asset column
    # directly and got NaN where they expected the config fallback to have
    # already run.
    n.add(
        "Generator", "gas",
        bus="elec_a", carrier="gas",
        p_nom=100.0, p_nom_extendable=True, p_nom_max=1000.0,
        marginal_cost=50.0,
        overnight_cost=900_000.0, lifetime=25.0,
        build_year=GOLDEN_PERIODS[0],
    )
    # --- the shape that works: capital_cost supplied directly, NOT extendable
    n.add(
        "Generator", "solar",
        bus="elec_b", carrier="solar",
        p_nom=200.0, p_nom_extendable=False,
        marginal_cost=0.0, capital_cost=27_500.0,
        build_year=GOLDEN_PERIODS[0],
    )
    # --- direct capital_cost on a Line
    n.add(
        "Line", "L_ab",
        bus0="elec_a", bus1="elec_b",
        s_nom=500.0, x=0.1, r=0.01,
        capital_cost=1_000_000.0,
    )
    # --- the class that was missing entirely
    n.add(
        "Link", "electrolyzer",
        bus0="elec_a", bus1="h2", carrier="H2",
        efficiency=0.7,
        p_nom=50.0, p_nom_extendable=True, p_nom_max=500.0,
        marginal_cost=10.0,
        overnight_cost=1_500_000.0, lifetime=20.0,
        build_year=GOLDEN_PERIODS[0],
    )
    # --- a genuinely zero-cost asset: zero must stay zero, not become "unset"
    n.add(
        "StorageUnit", "bess",
        bus="elec_b", p_nom=50.0, p_nom_extendable=False,
        max_hours=4.0, capital_cost=0.0, marginal_cost=0.0,
    )

    n.add("Load", "demand_e", bus="elec_b", p_set=120.0)
    n.add("Load", "demand_h2", bus="h2", p_set=20.0)
    return n


def solve_golden_network() -> pypsa.Network:
    """
    Build + solve once per process. HiGHS on 48 snapshots is sub-second.

    Wraps the solve in `with_periodized_cost_defaults` — the SAME mechanism
    `run_simulation` uses in production (services/solver_service.py) to fill
    per-asset `discount_rate` from the config for the duration of the solve
    only, then revert. PyPSA's consistency_check requires the fill to exist
    at solve time; the app never persists it. Using anything else here (e.g.
    baking the rate onto the asset row) would make the fixture stop covering
    the config-fallback code path every downstream resolver has to get right.
    """
    from services.solver_service import SolverConfig, with_periodized_cost_defaults

    global _SOLVED
    if _SOLVED is None:
        n = build_golden_network()
        cfg = SolverConfig(
            discount_rate=GOLDEN_DISCOUNT_RATE,
            multi_investment_periods=True,
            investment_periods=list(GOLDEN_PERIODS),
        )
        with with_periodized_cost_defaults(n, cfg):
            n.optimize(solver_name="highs", multi_investment_periods=True)
        _SOLVED = n
    return _SOLVED


def install_golden(network: pypsa.Network) -> None:
    """
    Install into the active context and pin the solver config.

    Required per test, not per session: conftest's `reset_backend` is
    autouse and calls `PyPSAService.reset_network()` both before and after
    EVERY test, and resets `solver_config` to `SolverConfig()`. Without the
    re-install the network is empty and the discount rate is a default.
    """
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig
    import routers.simulation as sim_router

    ctx = PyPSAService._ensure_active()
    ctx.network = network
    sim_router._state["solver_config"] = SolverConfig(
        discount_rate=GOLDEN_DISCOUNT_RATE,
        multi_investment_periods=True,
        investment_periods=list(GOLDEN_PERIODS),
    )

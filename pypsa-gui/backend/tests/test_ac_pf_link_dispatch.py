"""Stage 2 AC PF runs every Link at its LP flow, not at an unset p_set.

PyPSA's `n.pf()` takes Link p0 from `links_t.p_set` and sets each further port
to `-p0 * efficiency_i`. Before the fix `_fix_dispatch_for_ac_pf` fixed
generators, storage units and stores but not Links, so an energy hub's PoC
import Link ran without a set point in the PF: its own sub-network's slack
carried the load, and every Link result read after the PF (PoC import,
billing, energy and value flows) lost the Link flows.

Links are not PF branches: the grid bus, the site buses and any further port
bus are separate sub-networks, each with its own slack generator.
"""
import queue

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.ac_pf_service import run_ac_pf_stage
from services.solver_service import SolverConfig

LOAD = [4.0, 6.0, 5.0]


def _hub(multi_period: bool = False, efficiency2: float | None = None,
         user_p_set: float | None = None) -> pypsa.Network:
    n = pypsa.Network()
    hours = pd.date_range("2030-01-01", periods=len(LOAD), freq="h")
    if multi_period:
        n.set_snapshots(pd.MultiIndex.from_product([[2030, 2040], hours]))
        n.set_investment_periods([2030, 2040])
        loads = LOAD + [x + 1 for x in LOAD]
    else:
        n.set_snapshots(hours)
        loads = LOAD
    for bus in ("grid", "site", "site2"):
        n.add("Bus", bus, v_nom=20.0)
    n.add("Generator", "grid_import", bus="grid", p_nom=100, marginal_cost=10, control="Slack")
    # The site sub-network's slack: it takes the feeder's AC losses in the PF.
    n.add("Generator", "site_backup", bus="site", p_nom=100, marginal_cost=200, control="Slack")
    n.add("Link", "poc", bus0="grid", bus1="site", p_nom=50, efficiency=0.98)
    n.links["eh_role"] = "grid_import"
    n.add("Line", "feeder", bus0="site", bus1="site2", x=0.1, r=0.01, s_nom=50)
    n.add("Load", "site_load", bus="site2", p_set=pd.Series(loads, index=n.snapshots))
    if efficiency2 is not None:
        # efficiency2 > 0: an output port; < 0: an input port the Link draws from.
        n.add("Bus", "heat", v_nom=20.0)
        n.add("Generator", "heat_balance", bus="heat", p_nom=100, p_min_pu=-1,
              marginal_cost=0, control="Slack")
        n.links.loc["poc", "bus2"] = "heat"
        n.links.loc["poc", "efficiency2"] = efficiency2
    if user_p_set is not None:
        n.links_t.p_set["poc"] = pd.Series(user_p_set, index=n.snapshots)
    n.optimize(solver_name="highs", multi_investment_periods=multi_period)
    return n


def _feeder_loss(n) -> pd.Series:
    return n.lines_t.p0["feeder"] + n.lines_t.p1["feeder"]


@pytest.mark.parametrize("multi_period", [False, True], ids=["flat", "multi_period"])
@pytest.mark.parametrize("efficiency2", [None, 0.01, -0.01],
                         ids=["two_port", "output_port2", "input_port2"])
def test_ac_pf_keeps_the_lp_link_flow(multi_period, efficiency2):
    n = _hub(multi_period, efficiency2)
    ports = ["p0", "p1"] + (["p2"] if efficiency2 is not None else [])
    lp = {p: n.links_t[p]["poc"].copy() for p in ports}
    lp_backup = n.generators_t.p["site_backup"].copy()
    assert (lp["p0"] > 1).all(), "fixture: the PoC must import"
    if efficiency2 is not None:
        # Pin the port-2 sign: an input port withdraws (p2 > 0), an output feeds.
        assert (np.sign(lp["p2"]) == -np.sign(efficiency2)).all()

    out = run_ac_pf_stage(n, SolverConfig(), queue.Queue())

    assert out["ac_pf_converged_count"] == out["ac_pf_total_snapshots"]
    for p in ports:
        np.testing.assert_allclose(n.links_t[p]["poc"], lp[p], atol=1e-9, err_msg=p)
    # The grid bus is a sub-network of its own: its slack serves exactly the import.
    np.testing.assert_allclose(n.generators_t.p["grid_import"], lp["p0"], atol=1e-9)
    # The site slack moves from its LP dispatch by the feeder's AC losses only
    # (the LP has none), never by the import.
    loss = _feeder_loss(n)
    assert (loss > 0).all() and (loss < 0.01).all()
    np.testing.assert_allclose(n.generators_t.p["site_backup"] - lp_backup, loss, atol=1e-6)
    # The dispatch fix is reverted, so the next LOPF is not pinned.
    assert "poc" not in n.links_t.p_set.columns


def test_ac_pf_uses_the_lp_flow_and_restores_a_user_link_p_set():
    n = _hub(user_p_set=3.0)
    user_p_set = n.links_t.p_set.copy()
    lp_p0 = n.links_t.p0["poc"].copy()
    # PyPSA 1.1.2's LP pins p0 to a user Link p_set; the PF takes the LP's p0.
    np.testing.assert_allclose(lp_p0, 3.0, atol=1e-9)

    run_ac_pf_stage(n, SolverConfig(), queue.Queue())

    np.testing.assert_allclose(n.links_t.p0["poc"], lp_p0, atol=1e-9)
    pd.testing.assert_frame_equal(n.links_t.p_set, user_p_set)


def test_poc_import_read_after_the_ac_pf_equals_the_lp(reset_backend):
    """The PoC import the results read (lopf source) survives Stage 2."""
    import routers.results as R
    import routers.simulation as sim_router
    from services.results.physical_quantities import physical_quantities

    n = _hub()
    cfg = SolverConfig()
    sim_router._state.update(solver_config=cfg, lopf_results=None, ac_pf_results=None)

    def poc_import():
        pq = physical_quantities(n, cfg, result_df=R._result_df)
        return pq["poc"]["import_mw"].copy()

    lp_import = poc_import()
    assert (lp_import > 1).all()

    out = run_ac_pf_stage(n, cfg, queue.Queue())
    sim_router._state.update(lopf_results=out["lopf_results"],
                             ac_pf_results=out["ac_pf_results"])

    pd.testing.assert_series_equal(poc_import(), lp_import, atol=1e-9)

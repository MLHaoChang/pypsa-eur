"""Stage 2 AC PF runs every Link at its LP flow, not at p_set=0.

PyPSA's `n.pf()` takes Link p0 from `links_t.p_set` and sets each further port
to `-p0 * efficiency_i`. Before the fix `_fix_dispatch_for_ac_pf` fixed
generators, storage units and stores but not Links, so an energy hub's PoC
import Link ran at 0 in the PF and the slack carried the import.
"""
import queue

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.ac_pf_service import run_ac_pf_stage
from services.solver_service import SolverConfig

LOAD = [4.0, 6.0, 5.0]


def _hub(multi_period: bool, multiport: bool = False) -> pypsa.Network:
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
    n.add("Generator", "site_backup", bus="site", p_nom=100, marginal_cost=200)
    n.add("Link", "poc", bus0="grid", bus1="site", p_nom=50, efficiency=0.98)
    n.add("Line", "feeder", bus0="site", bus1="site2", x=0.1, r=0.01, s_nom=50)
    n.add("Load", "site_load", bus="site2", p_set=pd.Series(loads, index=n.snapshots))
    if multiport:
        n.add("Bus", "heat", v_nom=20.0)
        n.add("Generator", "heat_dump", bus="heat", p_nom=100, p_min_pu=-1, p_max_pu=0,
              marginal_cost=0, sign=1)
        n.links.loc["poc", "bus2"] = "heat"
        n.links.loc["poc", "efficiency2"] = 0.01
    n.optimize(solver_name="highs", multi_investment_periods=multi_period)
    return n


@pytest.mark.parametrize("multi_period", [False, True], ids=["flat", "multi_period"])
@pytest.mark.parametrize("multiport", [False, True], ids=["two_port", "three_port"])
def test_ac_pf_keeps_the_lp_link_flow(multi_period, multiport):
    n = _hub(multi_period, multiport)
    ports = ["p0", "p1"] + (["p2"] if multiport else [])
    lp = {p: n.links_t[p].copy() for p in ports}
    lp_backup = n.generators_t.p["site_backup"].copy()
    assert (lp["p0"]["poc"].abs() > 1).all(), "fixture: the PoC must import"

    out = run_ac_pf_stage(n, SolverConfig(), queue.Queue())

    assert out["ac_pf_converged_count"] == out["ac_pf_total_snapshots"]
    for p in ports:
        np.testing.assert_allclose(n.links_t[p]["poc"], lp[p]["poc"], atol=1e-6, err_msg=p)
    # The site's own generator keeps its LP dispatch: the import is not
    # re-routed onto whichever generator the PF made the site's slack. As that
    # slack it does carry the feeder's losses (~1e-3 MW), which the LP omits.
    np.testing.assert_allclose(n.generators_t.p["site_backup"], lp_backup, atol=0.05)
    # The feeder carries the import into the load, as in the LP.
    np.testing.assert_allclose(n.lines_t.p0["feeder"].abs(), n.loads_t.p_set["site_load"],
                               rtol=1e-2)
    # The dispatch fix is reverted, so the next LOPF is not pinned.
    assert n.links_t.p_set.empty or "poc" not in n.links_t.p_set.columns


def test_ac_pf_restores_a_user_link_p_set():
    n = _hub(multi_period=False)
    user = pd.Series([3.0, 3.0, 3.0], index=n.snapshots)
    n.links_t.p_set["poc"] = user
    n.optimize(solver_name="highs")

    run_ac_pf_stage(n, SolverConfig(), queue.Queue())

    np.testing.assert_allclose(n.links_t.p0["poc"], user, atol=1e-6)
    pd.testing.assert_series_equal(n.links_t.p_set["poc"], user, check_names=False)

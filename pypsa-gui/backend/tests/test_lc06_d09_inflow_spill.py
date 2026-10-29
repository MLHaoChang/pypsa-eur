"""LC-06 S13, LC-D09: a StorageUnit's native spill through the copilot's tools.

The seam: ``run_simulation`` (the solve), ``get_simulation_status`` (its
outcome) and ``get_asset_results('StorageUnit', …, category='dispatch')``
with ``spill`` (the series) and ``spilled_mwh`` (Σ spill × weighting). No
tool reports the inflow itself (ticket 31), so the inflow enters only as a
network input.

The fixture: one bus, a 4 MW load, and a hydro StorageUnit (10 MW, 2 h, so
20 MWh) as the only source. It has a 10 MW inflow and is held full, with its
state of charge fixed at 20 MWh in every snapshot. Three snapshots, each
weighted 3 h. The balance gives dispatch = 4 MW. A constant full reservoir
gives spill = inflow − dispatch = 6 MW in every snapshot, so the LP has no
freedom. spilled_mwh = 6 · (3 + 3 + 3) = 54 MWh. A unit weighting would give
18. The 1 EUR/MWh dispatch cost only gives the solve an objective.

Tolerance ``abs=1e-9``: spill is pinned by two equalities that HiGHS meets to
its feasibility tolerance. The mutation moves it by 1 MW.
"""
# ruff: noqa: F811 -- shared public harness fixtures
import time

import pandas as pd
import pytest

from services import chat_tools
from tests.test_workflow_tools import baseline  # noqa: F401

W, LOAD, E_NOM = 3.0, 4.0, 20.0
TOL = 1e-9


def _solve(ctx, inflow):
    n = ctx.network
    n.set_snapshots(pd.date_range("2026-01-01", periods=3, freq="h"))
    n.snapshot_weightings.loc[:, :] = W
    n.remove("Generator", "G1")
    n.loads.at["L1", "p_set"] = LOAD
    n.add("StorageUnit", "hydro", bus="B1", p_nom=10.0, max_hours=2.0, state_of_charge_initial=E_NOM,
          cyclic_state_of_charge=False, inflow=inflow, marginal_cost=1.0)
    n.storage_units_t.state_of_charge_set = pd.DataFrame({"hydro": [E_NOM] * 3}, index=n.snapshots)
    assert chat_tools.run_simulation()["status"] == "started"
    deadline = time.time() + 120
    while time.time() < deadline:
        status = chat_tools.get_simulation_status()
        if not status.get("running") and status.get("status") not in ("running", "started", "queued"):
            break
        time.sleep(0.2)
    assert (status["status"], status["condition"]) == ("completed", "optimal"), status
    return chat_tools.get_asset_results("StorageUnit", "hydro", category="dispatch",
                                        metrics=["spill", "p_dispatch", "spilled_mwh"], resolution="raw")


@pytest.mark.parametrize("inflow, spill", [(10.0, 6.0), (9.0, 5.0)], ids=["inflow10", "inflow9"])
def test_a_full_reservoir_spills_its_inflow_less_its_dispatch(baseline, inflow, spill):
    out = _solve(baseline[0], inflow)
    assert out["unavailable"] == [] and out["n_snapshots"] == 3 and not out["truncated"]
    assert out["series"]["p_dispatch"] == pytest.approx([LOAD] * 3, abs=TOL)
    assert out["series"]["spill"] == pytest.approx([spill] * 3, abs=TOL)
    assert out["scalars"]["spilled_mwh"] == pytest.approx(spill * 3 * W, abs=TOL)    # 54, then 45

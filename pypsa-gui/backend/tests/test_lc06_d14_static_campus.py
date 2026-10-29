"""LC-06 S12, LC-D14: the static campus ranking through the copilot's tools.

The seam: ``campus_draft_campus`` (the campus file from the hub),
``campus_run_study(k)`` (the queued Run, which ranks the campus hours,
``gridspine.ranking.campus.select_campus_hours``), and the authenticated route
``GET /api/campus-electrical/{name}``, whose ``results.selection`` lists each
picked hour with its reasons, and whose ``settings`` carry ``k`` as the job ran
it. No island sidecar, so only the base criteria rank.

The hub is the ``test_campus_electrical_service`` hub (grid 110 kV, one
110/20 kV transformer ``grid_import`` with efficiency 0.99, MV bus with load,
PV and BESS), here with six hours chosen so that no criterion ties:

    hour            0     1     2     3     4     5
    load (MW)      40    42    38    30    36    44
    PV (MW)         0    10    30    50    20     0
    BESS (MW, +out) -5    5   -10    -8     6     3
    net = load − PV − BESS
                   45    27    18   −12    10    41
    import = net/0.99 (PCC, negative exporting)
                45.45 27.27 18.18 −12.12 10.10 41.41
    consumption = load + charging
                   45    42    48    38    36    44
    renewable = PV  0    10    30    50    20     0
    trafo |PV + BESS − load| = |net|
                   45    27    18    12    10    41

k = 1: max import h0, max export h3 (its only export hour), max consumption
h2, max renewable h3, max trafo h0, so hours {0, 2, 3}. k = 2 adds the
runners-up: import and trafo h5, consumption h0, renewable h2.
The oracle is the picks, not a tolerance: every criterion's top two
differ by at least 1 MW (the closest are consumption 45 and 44 MW). The
mutation moves the picks themselves.
"""
from __future__ import annotations

import time
import uuid

import numpy as np
import pandas as pd
import pypsa
import pytest

from services import chat_tools, project_registry
from services import solve_queue as sq
from tests import test_campus_electrical_service as _ces

user_and_db = _ces.user_and_db
P = "Rank Hub"
LOAD = [40.0, 42.0, 38.0, 30.0, 36.0, 44.0]
PV = [0.0, 10.0, 30.0, 50.0, 20.0, 0.0]
BESS = [-5.0, 5.0, -10.0, -8.0, 6.0, 3.0]


def _network(load=LOAD):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-07-01", periods=len(load), freq="h"))
    n.add("Bus", "grid", v_nom=110.0)
    n.add("Bus", "mv", v_nom=20.0)
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=500.0, marginal_cost=50.0)
    n.add("Link", "grid_import", bus0="grid", bus1="mv", p_nom=60.0, p_min_pu=-1.0, efficiency=0.99)
    n.add("Load", "dc_load", bus="mv", p_set=40.0)
    n.add("Generator", "pv", bus="mv", carrier="solar", p_nom=60.0)
    n.add("StorageUnit", "bess", bus="mv", carrier="battery", p_nom=10.0, max_hours=2.0)
    n.buses["eh_poc"] = [True, False]
    n.buses["eh_sk_mva"] = [2000.0, float("nan")]
    load, pv, bess = np.array(load), np.array(PV), np.array(BESS)
    net = load - pv - bess
    n.generators_t.p = pd.DataFrame({"pv": pv, "grid_supply": net / 0.99}, index=n.snapshots)
    n.storage_units_t.p = pd.DataFrame({"bess": bess}, index=n.snapshots)
    n.loads_t.p = pd.DataFrame({"dc_load": load}, index=n.snapshots)
    n.links_t.p0 = pd.DataFrame({"grid_import": net / 0.99}, index=n.snapshots)
    n.links_t.p1 = -0.99 * n.links_t.p0
    return n


@pytest.fixture
def rank(user_and_db, client):
    db, user = user_and_db
    row = project_registry.create_root(db, user, P)

    def run(k, load=LOAD):
        _network(load).export_to_netcdf(str(project_registry.ensure_project_dir(row) / "network.nc"))
        chat_tools.campus_draft_campus(P, overwrite=True)
        job = chat_tools.campus_run_study(P, k=k, invest=False)
        deadline = time.time() + 300
        while time.time() < deadline:
            state = sq.solve_queue.get_job(uuid.UUID(job["id"]))
            if state["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(0.2)
        assert state["status"] == "completed", state
        resp = client.get(f"/api/campus-electrical/{P}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["settings"]["k"] == k                     # k as the job ran it
        return {s["hour"]: set(s["reasons"]) for s in body["results"]["selection"]}
    return run


def test_k1_picks_each_criterions_extreme_hour_and_unions_the_reasons(rank):
    assert rank(1) == {
        0: {"max_import_mw", "max_trafo_GRID_IMPORT_mw"},
        2: {"max_consumption_mw"},
        3: {"max_export_mw", "max_renewable_mw"},
    }


def test_k2_adds_each_criterions_runner_up(rank):
    picks = rank(2)
    assert {"max_import_mw", "max_trafo_GRID_IMPORT_mw"} <= picks[5]       # 41.41 / 41 after 45.45 / 45
    assert "max_consumption_mw" in picks[0]                                # 45 after 48
    assert "max_renewable_mw" in picks[2]                                  # 30 after 50
    assert {0, 2, 3, 5} <= set(picks)
    assert picks[3] >= {"max_export_mw", "max_renewable_mw"}


def test_more_load_in_the_last_hour_moves_the_import_pick(rank):
    """Mutation: hour 5 at 50 MW makes its net 47 (import 47.47), above hour 0's 45."""
    picks = rank(1, load=[*LOAD[:5], 50.0])
    assert {"max_import_mw", "max_trafo_GRID_IMPORT_mw", "max_consumption_mw"} <= picks[5]   # 50 + 0 > 48
    assert 0 not in picks

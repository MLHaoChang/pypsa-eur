"""
A network with no demand charge solves exactly as it did before S3.

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md
(S3 acceptance; global constraint "a project with no attached demand charge
solves byte-for-byte as before, proven through `run_simulation`"; review v1
S4: the no-op test must not be vacuous).

The pinned hashes below were recorded on the UNMODIFIED code, before any S3
change, at commit 217ccfb3d5acd2ef783555f23cd1b052c4f68af9 (PyPSA 1.1.2,
linopy 0.8.0, HiGHS, this container), by running `_network()` through
`run_simulation(SolverConfig(), ...)` twice with identical output and
hashing with `_frame_hash`. The mutation "`_wrap_with_demand_charge` always
active" turns this test red. A different platform or HiGHS build may pivot
differently; if this test goes red with no S3 change in the diff, re-record
the hashes on the commit before the change and say so in the commit.
"""
from __future__ import annotations

import hashlib
import queue
import threading

import numpy as np
import pandas as pd
import pypsa

from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig, run_simulation

PINNED_OBJECTIVE = 3927057.2038609087
PINNED_GENERATORS_T_P = "43066a765d83f789dc8521f84501ee7bb2cc31afc2699b1868fa340a3ae18d8b"
PINNED_LINKS_T_P0 = "baf37bbc7766d783bff177a6ae2f56be848984b5fbeecd2102f5b33f26375e3e"


def _network() -> pypsa.Network:
    sn = pd.date_range("2030-01-01", "2030-02-28 23:00", freq="h")
    n = pypsa.Network()
    n.set_snapshots(sn)
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Generator", "grid_supply", bus="grid", p_nom=1e3, p_min_pu=-1.0,
          marginal_cost=0.0)
    hr = np.asarray(sn.hour)
    imp = pd.Series(np.where((hr >= 8) & (hr < 20), 110.0, 90.0), index=sn)
    n.add("Link", "grid_import", bus0="grid", bus1="site", p_nom=60.0,
          marginal_cost=imp)
    n.add("Link", "grid_export", bus0="site", bus1="grid", p_nom=60.0,
          marginal_cost=-40.0)
    rng = np.random.default_rng(0)
    load = 20 + 15 * np.sin((hr - 6) / 24 * 2 * np.pi).clip(0) + 5 * rng.random(len(sn))
    n.add("Load", "site_load", bus="site", p_set=pd.Series(load, index=sn))
    n.add("StorageUnit", "bess", bus="site", p_nom_extendable=True, p_nom_max=40.0,
          max_hours=2.0, overnight_cost=650_000.0, lifetime=15, discount_rate=0.07,
          fom_cost=5_000.0, efficiency_store=0.95 ** 0.5,
          efficiency_dispatch=0.95 ** 0.5, cyclic_state_of_charge=True)
    return n


def _frame_hash(df: pd.DataFrame) -> str:
    h = pd.util.hash_pandas_object(df.sort_index(axis=1), index=True).values
    return hashlib.sha256(
        h.tobytes() + "|".join(map(str, df.columns)).encode()).hexdigest()


def test_no_demand_charge_solves_byte_identically_through_run_simulation():
    n = _network()
    PyPSAService.set_network(n)
    cfg = SolverConfig()
    assert cfg.demand_charge is None
    status, condition = run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(), queue.SimpleQueue())
    assert (status, condition) == ("ok", "optimal")
    assert float(n.objective) == PINNED_OBJECTIVE
    assert _frame_hash(n.generators_t.p) == PINNED_GENERATORS_T_P
    assert _frame_hash(n.links_t.p0) == PINNED_LINKS_T_P0
    assert "peak_import" not in n.model.variables

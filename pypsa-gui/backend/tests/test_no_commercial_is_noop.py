"""
U2 WP2 port of `test_demand_charge_absent_is_noop.py` (WP6 target, green
from the start): a project WITHOUT a commercial config solves byte for byte
as before through `run_simulation` — IC's commercial chain adds no variable,
no constraint and no price when `SolverConfig.commercial` is None, exactly as
GS's demand wrapper added none without `demand_charge`.

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md WP2,
WP6. The network and the pinned hashes are the source test's (recorded on
217ccfb, PyPSA 1.1.2, linopy 0.8.0, HiGHS); the source file goes in WP10, so
they are restated here.
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


def test_no_commercial_config_solves_byte_identically_through_run_simulation():
    from services.commercial import lp_bindings as LP

    n = _network()
    PyPSAService.set_network(n)
    cfg = SolverConfig()
    assert cfg.commercial is None and getattr(cfg, "demand_charge", None) is None
    status, condition = run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(), queue.SimpleQueue())
    assert (status, condition) == ("ok", "optimal")
    assert float(n.objective) == PINNED_OBJECTIVE
    assert _frame_hash(n.generators_t.p) == PINNED_GENERATORS_T_P
    assert _frame_hash(n.links_t.p0) == PINNED_LINKS_T_P0
    names = set(n.model.variables)
    assert not {v for v in names if v.startswith("ic_")} and "peak_import" not in names
    assert LP.META_DEMAND not in n.meta and LP.META_LINKS not in n.meta

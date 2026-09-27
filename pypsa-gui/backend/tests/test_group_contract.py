"""
Energy-hub group contract (Edge Investment Case P1 WP1.6).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP1.6
Spec §5 table "Energy-hub group contract | Σ member PoC Links ≤ group cap per
snapshot".

Members share one grid contract: their PoC Links' combined import is capped at
`group_cap_mw` in every snapshot. It is a pure constraint with no objective term,
so the gap stays 0. Each member's share of the group's import energy is reported
(cost allocation is P3), and nothing on the network changes outside the solve.
"""
from __future__ import annotations

import queue
import threading

import numpy as np
import pandas as pd
import pytest

from services.commercial import lp_bindings as L
from tests.fixtures.investment_case.edge_15min import build_edge_15min


def _two_members():
    n = build_edge_15min()
    n.add("Bus", "poc_b", carrier="AC")
    n.add("Bus", "site_b", carrier="AC")
    n.add("Link", "import_b", bus0="grid", bus1="poc_b", p_nom=80.0, carrier="AC")
    n.add("Link", "poc_site_b", bus0="poc_b", bus1="site_b", p_nom=200.0, carrier="AC")
    n.add("Load", "site_b_load", bus="site_b", p_set=n.loads_t.p_set["site_load"] * 0.8)
    for site in ("site", "site_b"):
        n.add("Generator", f"backup_{site}", bus=site, p_nom=100.0, marginal_cost=500.0,
              carrier="grid")
    return n


def _commercial(cap=60.0, members=("import", "import_b")):
    return {"poc_link": "import", "group_contract": "hub", "group_members": list(members),
            "group_cap_mw": cap}


def test_the_group_spec_is_set_for_the_solve_and_undone():
    n = _two_members()
    applied = L.materialise_poc_prices(n, _commercial())
    spec = getattr(n, L.GROUP_SPEC_ATTR)
    assert spec == {"name": "hub", "members": ["import", "import_b"], "cap_mw": 60.0}
    applied.undo()
    assert not hasattr(n, L.GROUP_SPEC_ATTR)


@pytest.mark.parametrize("bad", [
    {"group_members": ["import", "ghost"], "group_cap_mw": 10.0},
    {"group_members": ["import"], "group_cap_mw": None},
])
def test_a_bad_group_is_refused(bad):
    n = _two_members()
    with pytest.raises(Exception):
        L.materialise_poc_prices(n, {"poc_link": "import", "group_contract": "hub", **bad})


def _solve(n, commercial):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    PyPSAService.set_network(n)
    cfg = SolverConfig(commercial=commercial)
    sink: dict = {}
    status, condition = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                       queue.SimpleQueue(),
                                       state_update=lambda **kw: sink.update(kw))
    assert status in ("ok", "optimal"), (status, condition)
    return cfg, sink


@pytest.mark.live_solve
def test_the_group_cap_binds_when_the_members_would_exceed_it():
    free = _two_members()
    _solve(free, None)
    combined_free = (free.links_t.p0["import"] + free.links_t.p0["import_b"]).max()
    assert combined_free > 60.0  # the cap is binding in this fixture
    n = _two_members()
    cfg, sink = _solve(n, _commercial(cap=60.0))
    combined = n.links_t.p0["import"] + n.links_t.p0["import_b"]
    assert combined.max() <= 60.0 + 1e-6
    assert combined.max() >= 60.0 - 1e-3  # it binds
    shares = n.meta[L.META_GROUP]["energy_share"]
    assert set(shares) == {"import", "import_b"}
    assert sum(shares.values()) == pytest.approx(1.0)
    assert sink["last_commercial_terms"]["group"]["energy_share"] == shares


@pytest.mark.live_solve
def test_the_group_adds_no_objective_term():
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    n = _two_members()
    cfg, _ = _solve(n, _commercial(cap=60.0))
    cb = compute_cost_breakdown(n, cfg)
    assert abs(compute_objective_decomposition(n, cb)["gap_pct"]) < 1e-6

"""
The COPT screening sees the grid surplus (plan
docs/superpowers/plans/2026-09-28-eh-zonal-mc-open-items.md, WP3).

A surplus-limited import is not a two-state unit, so the 2026-09-27 COPT
(fmea_top class A) screened the sampled Link as if the grid always had the
power. The Link unit in the SCREENING fleet now carries a per-hour
availability profile ``f_h = E[min(cap_h, S_h)] / cap_h`` from the grid
area's own COPT — the Link outage stays exact (two-state), the grid
randomness is netted at its expectation. The MC keeps the unprofiled unit.
"""
from __future__ import annotations

import threading

import numpy as np
import pytest

from models.energy_hub import default_strong_grid_pack
from services.adequacy import eh_stages as ST
from services.adequacy import mc_zonal as Z
from services.adequacy.copt import CoptUnit
from services.solver_service import SolverConfig
from tests.eh_stage_fixtures import VOLL, certifiable_weak_network

OVERLAY = default_strong_grid_pack().import_overlay


def _freeze(n, **kw):
    return ST.freeze_fixed_plan(n, SolverConfig(voll=VOLL), threading.Lock(),
                                overlay=OVERLAY, **kw)


def _weak(*, grid_p_nom=200.0, grid_q=0.02, grid_load=None, firm_link=False):
    n = certifiable_weak_network()
    n.links.at["import_poc", "p_nom"] = 50.0
    n.generators.at["grid_supply", "p_nom"] = grid_p_nom
    n.generators.at["grid_supply", "outage_rate_value"] = grid_q
    if grid_load is not None:
        n.add("Load", "grid_load", bus="grid", p_set=grid_load)
    if firm_link:
        for c in ("outage_rate_value", "mttr_hours"):
            n.links[c] = np.nan
        n.links["outage_rate_basis"] = ""
    return n


def test_expected_surplus_fraction_matches_a_hand_computed_pmf():
    """
    One 100 MW grid unit, q = 0.2. Hour 0: grid residual 60 → surplus 40
    w.p. 0.8, else 0 → E[min(50, S)] = 32 → 0.64. Hour 1: residual 0 →
    E[min(50, S)] = 0.8 × 50 → 0.8. Hour 2: residual 100 → 0.
    """
    unit = CoptUnit(name="g", capacity_mw=100.0, q=0.2, mttr_hours=10.0)
    f = Z.expected_surplus_fraction(
        (unit,), np.array([60.0, 0.0, 100.0]), cap=np.full(3, 50.0),
        ratio=np.ones(3), periods=(("ALL", 0, 3),))
    np.testing.assert_allclose(f, [0.64, 0.8, 0.0], atol=1e-12)


def test_expected_surplus_fraction_applies_the_delivery_ratio():
    """
    Ratio 0.5: the hub receives half of what the grid sends, so a 50 MW
    hub-side cap needs 100 MW of grid surplus to be full.
    """
    unit = CoptUnit(name="g", capacity_mw=100.0, q=0.0, mttr_hours=10.0)
    f = Z.expected_surplus_fraction(
        (unit,), np.array([20.0]), cap=np.array([50.0]),
        ratio=np.array([0.5]), periods=(("ALL", 0, 1),))
    # S = 80 × 0.5 = 40 MW at the hub → 40 / 50.
    np.testing.assert_allclose(f, [0.8], atol=1e-12)


def test_an_unbound_grid_screens_exactly_like_v1():
    n1, n2 = _weak(grid_p_nom=1000.0, grid_q=0.0), _weak(grid_p_nom=1000.0,
                                                          grid_q=0.0)
    zonal = _freeze(n1)
    v1 = _freeze(n2, import_model="sampled_unit")
    assert zonal.scope["import_model"] == "zonal"
    assert zonal.copt_metrics == v1.copt_metrics
    assert zonal.copt_rows == v1.copt_rows
    assert zonal.scope["copt_import_model"] == "expected_surplus_profile"
    assert v1.scope["copt_import_model"] == "two_state"


def test_a_grid_whose_load_eats_its_supply_raises_the_copt_lole():
    zonal = _freeze(_weak(grid_load=180.0))
    v1 = _freeze(_weak(grid_load=180.0), import_model="sampled_unit")
    assert zonal.copt_metrics["lole_hours"] > v1.copt_metrics["lole_hours"]
    area = zonal.scope["grid_areas"][0]
    # At most 20 MW of 50 MW is ever available → f ≤ 0.4.
    assert area["copt_surplus_fraction_min"] <= 0.4 + 1e-12


def test_the_mc_keeps_the_unprofiled_link_unit():
    zonal = _freeze(_weak(grid_load=180.0))
    link = [u for u in zonal.mc_inputs.units if u.name == "link:import_poc"]
    assert link and link[0].profile is None


def test_a_firm_link_behind_a_short_grid_is_derated_in_the_screening():
    zonal = _freeze(_weak(grid_load=180.0, firm_link=True))
    v1 = _freeze(_weak(grid_load=180.0, firm_link=True),
                 import_model="sampled_unit")
    assert zonal.scope["import_model"] == "zonal"
    assert zonal.copt_metrics["lole_hours"] > v1.copt_metrics["lole_hours"]


def test_the_copt_note_says_the_grid_is_netted_at_expectation():
    zonal = _freeze(_weak(grid_load=180.0))
    assert "expected" in zonal.scope["copt_import_note"]
    v1 = _freeze(_weak(), import_model="sampled_unit")
    assert v1.scope["copt_import_note"] is None


@pytest.mark.parametrize("load", [None, 180.0])
def test_fraction_is_in_the_unit_interval(load):
    zonal = _freeze(_weak(grid_load=load))
    f = zonal.scope["grid_areas"][0]["copt_surplus_fraction_min"]
    assert 0.0 <= f <= 1.0

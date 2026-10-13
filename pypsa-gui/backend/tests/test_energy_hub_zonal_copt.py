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
    exact = dict(zonal.copt_metrics).pop("import_exact")
    screened = {k: v for k, v in zonal.copt_metrics.items()
                if k != "import_exact"}
    assert screened == v1.copt_metrics
    # With the grid never binding, the exact import metric IS the v1 COPT
    # (the Link a plain two-state unit): two independent routes, one number.
    assert exact["lole_hours"] == pytest.approx(v1.copt_metrics["lole_hours"],
                                                rel=1e-9)
    assert exact["eue_mwh"] == pytest.approx(v1.copt_metrics["eue_mwh"],
                                             rel=1e-9)
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


# ── review of WP3 ─────────────────────────────────────────────────────────

def _mc(frozen, draws=4000):
    return Z.zonal_mc_adequacy(frozen.zonal_inputs, draws=draws, seed=17,
                               cov_target=0.0, max_draws=draws)


def _within_ci(value, res, key, ci_key):
    lo, hi = res[ci_key]
    half = max(hi - lo, 1e-9) / 2.0
    # 95 % CI widened by one half-width: a 1-in-~10^4 false alarm at most.
    return lo - half <= value <= hi + half, (value, res[key], res[ci_key])


@pytest.mark.parametrize("kw", [
    {"grid_load": 180.0},
    {"grid_load": 150.0, "grid_q": 0.1},
    {"grid_load": 180.0, "firm_link": True},
])
def test_the_exact_import_metric_is_the_mc_expectation(kw):
    """
    Without storage the MC's expected LOLE / EUE is Σ_h w_h P(short_h),
    exactly what the level-mixed COPT computes — the screening's
    expected-surplus profile is NOT (review of WP3: 802 h vs 576 h here).
    """
    frozen = _freeze(_weak(**kw))
    exact = frozen.copt_metrics["import_exact"]
    res = _mc(frozen)
    ok, info = _within_ci(exact["lole_hours"], res, "lole_hours", "lole_ci")
    assert ok, info
    ok, info = _within_ci(exact["eue_mwh"], res, "eue_mwh", "eue_ci")
    assert ok, info
    assert exact["delta_mw"] == pytest.approx(1.0)


def test_two_sampled_links_into_one_area_mix_exactly():
    n = _weak(grid_load=160.0)
    n.add("Link", "poc_2", bus0="grid", bus1="hub", p_nom=30.0, carrier="AC",
          outage_rate_value=0.1, outage_rate_basis="FOR", mttr_hours=24.0)
    n.links.at["poc_2", "eh_role"] = "grid_import"
    frozen = _freeze(n)
    exact = frozen.copt_metrics["import_exact"]
    res = _mc(frozen)
    ok, info = _within_ci(exact["lole_hours"], res, "lole_hours", "lole_ci")
    assert ok, info


def test_the_screening_note_says_it_may_over_or_under_state():
    zonal = _freeze(_weak(grid_load=180.0))
    note = zonal.scope["copt_import_note"]
    assert "over- or under-state" in note and "import_exact" in note


def test_an_ample_grid_with_outages_is_snapped_to_v1():
    """
    Ten 100 MW grid units at q = 0.02 behind a 50 MW Link: f = 1 − 5e-15.
    Unsnapped, the Link took a K_EXACT mixture slot for nothing.
    """
    n = _weak(grid_p_nom=100.0)
    for i in range(9):
        n.add("Generator", f"grid_{i}", bus="grid", carrier="gas",
              p_nom=100.0, outage_rate_value=0.02, outage_rate_basis="EFORd",
              mttr_hours=50.0)
    n.add("Load", "grid_load", bus="grid", p_set=100.0)
    zonal = _freeze(n)
    link = next(u for u in zonal.screening_units if u.name == "link:import_poc")
    assert link.profile is None


def test_the_copt_fidelity_note_is_kept():
    zonal = _freeze(_weak(grid_load=180.0))
    assert hasattr(zonal, "copt_fidelity_note")


def test_a_failed_fraction_falls_back_to_two_state_and_says_so(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(Z, "expected_surplus_fraction", boom)
    zonal = _freeze(_weak(grid_load=180.0))
    assert zonal.scope["copt_import_model"] == "two_state"
    assert "synthetic" in zonal.scope["grid_areas"][0]["copt_note"]


def test_no_copt_note_on_the_normal_path():
    zonal = _freeze(_weak(grid_load=180.0))
    assert "copt_note" not in zonal.scope["grid_areas"][0]


@pytest.mark.parametrize("mode", ["auto", "sampled_unit"])
def test_a_time_varying_link_cap_screens_without_error(mode):
    """
    A Link with an hourly p_max_pu gets an HOURLY capacity series, which
    the per-period COPT refused ("not constant over the block"). The
    screening copy folds the hourly shape into the profile.
    """
    import pandas as pd

    n = _weak(grid_load=150.0)
    n.links_t.p_max_pu["import_poc"] = pd.Series(
        [1.0, 0.8, 0.6, 1.0, 1.0, 0.5, 1.0, 1.0], index=n.snapshots)
    frozen = _freeze(n, import_model=mode)
    assert frozen.copt_error is None, frozen.copt_error
    assert frozen.copt_metrics["lole_hours"] >= 0.0


def test_two_areas_get_their_own_fractions():
    from tests.test_energy_hub_zonal_areas import two_grid_hub

    frozen = _freeze(two_grid_hub(a_load=170.0))
    a, b = frozen.scope["grid_areas"]
    assert a["copt_surplus_fraction_min"] != b["copt_surplus_fraction_min"]
    prof = {u.name: u.profile for u in frozen.screening_units
            if u.name.startswith("link:")}
    assert prof["link:poc_a"] is not None
    assert not np.array_equal(prof["link:poc_a"], prof["link:poc_b"]
                              if prof["link:poc_b"] is not None
                              else np.ones_like(prof["link:poc_a"]))


def test_the_delivery_ratio_reaches_the_fraction_end_to_end():
    """
    Efficiency 0.5: 20 MW of grid surplus is 10 MW at the hub, of a 25 MW
    hub-side cap → f ≤ 0.4 (a ratio-1 wiring would give 0.8).
    """
    n = _weak(grid_load=180.0)
    n.links.at["import_poc", "efficiency"] = 0.5
    frozen = _freeze(n)
    assert frozen.scope["grid_areas"][0]["copt_surplus_fraction_min"] <= 0.4 + 1e-9


def test_a_negative_grid_residual_is_handled():
    """
    Must-take above demand (r = −30): S = C + 30 → E[min(50, S)] =
    0.2·30 + 0.8·50 = 46 → 0.92.
    """
    unit = CoptUnit(name="g", capacity_mw=100.0, q=0.2, mttr_hours=10.0)
    f = Z.expected_surplus_fraction(
        (unit,), np.array([-30.0]), cap=np.array([50.0]), ratio=np.ones(1),
        periods=(("ALL", 0, 1),))
    np.testing.assert_allclose(f, [0.92], atol=1e-12)


def test_a_zero_delivery_ratio_backs_nothing():
    unit = CoptUnit(name="g", capacity_mw=100.0, q=0.0, mttr_hours=10.0)
    f = Z.expected_surplus_fraction(
        (unit,), np.array([0.0]), cap=np.array([50.0]), ratio=np.zeros(1),
        periods=(("ALL", 0, 1),))
    np.testing.assert_allclose(f, [0.0])


def test_the_grid_surface_is_evaluated_per_period_block():
    """
    A grid unit built for the first period only: ES = 0.2·50 in P1 and
    the whole 50 in P2.
    """
    unit = CoptUnit(name="g", capacity_mw=100.0, q=0.2, mttr_hours=10.0,
                    capacity_series=np.array([100.0, 100.0, 0.0, 0.0]))
    es = Z._grid_expected_shortfall(
        (unit,), np.full(4, 50.0), (("P1", 0, 2), ("P2", 2, 4)))
    np.testing.assert_allclose(es, [10.0, 10.0, 50.0, 50.0])

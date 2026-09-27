"""
The grid side as a second area (plan
docs/superpowers/plans/2026-09-27-eh-zonal-mc-import-outages.md, WP2).

v1 samples the import Link but still assumes the grid behind it always has
the power to send. The zonal path samples the grid side's own fleet and
demand (the part of the network ``hub_fleet_scope`` pruned off) and lets the
hub receive ``min(link_up × cap, grid surplus)`` each hour. It lives in
``services/adequacy/mc_zonal.py``; ``mc.mc_adequacy`` only gained a
``blocks_fn`` hook, so the single-area engine, ELCC and the coupling loops
are untouched.
"""
from __future__ import annotations

import threading

import numpy as np
import pytest

from models.energy_hub import default_strong_grid_pack
from services.adequacy import eh_stages as ST
from services.adequacy import mc as MC
from services.adequacy import mc_zonal as Z
from services.solver_service import SolverConfig
from tests.eh_stage_fixtures import VOLL, certifiable_weak_network

OVERLAY = default_strong_grid_pack().import_overlay
SEED = 11
DRAWS = 300


def _weak(cap: float = 50.0, *, grid_q: float = 0.02, grid_p_nom: float = 200.0,
          grid_load: float | None = None):
    n = certifiable_weak_network()
    n.links.at["import_poc", "p_nom"] = cap
    n.generators.at["grid_supply", "outage_rate_value"] = grid_q
    n.generators.at["grid_supply", "p_nom"] = grid_p_nom
    if grid_load is not None:
        n.add("Load", "grid_load", bus="grid", p_set=grid_load)
    return n


def _freeze(n, **kw):
    return ST.freeze_fixed_plan(n, SolverConfig(voll=VOLL), threading.Lock(),
                                overlay=OVERLAY, **kw)


def _lole(res) -> float:
    return float(res["lole_hours"])


def _zonal(frozen, **kw):
    return Z.zonal_mc_adequacy(frozen.zonal_inputs, draws=DRAWS, seed=SEED,
                               cov_target=0.0, max_draws=DRAWS, **kw)


def _v1(frozen, **kw):
    return MC.mc_adequacy(frozen.mc_inputs, draws=DRAWS, seed=SEED,
                          cov_target=0.0, max_draws=DRAWS, **kw)


def test_auto_goes_zonal_when_the_grid_side_carries_occurrence_data():
    frozen = _freeze(_weak())
    scope = frozen.scope
    assert scope["import_model"] == "zonal"
    assert scope["import_firmness"] == "outage_and_grid_sampled"
    assert scope["grid_area"]["units"] == ["grid_supply"]
    assert scope["grid_area"]["storage_dispatched"] is False
    assert frozen.zonal_inputs is not None
    # The hub half of the zonal inputs IS the v1 snapshot (same units, same
    # positions) — the COPT screens that one.
    assert frozen.zonal_inputs.hub is frozen.mc_inputs
    assert frozen.zonal_inputs.import_idx == (2,)


def test_grid_with_surplus_above_the_cap_every_hour_reproduces_v1_exactly():
    """
    Q = 0, 1000 MW behind a 50 MW Link: the grid never binds, so the
    two-area path must replay the v1 draws BIT FOR BIT — same seed, same
    positional substreams, same float32 accumulation order.
    """
    frozen = _freeze(_weak(grid_q=0.0, grid_p_nom=1000.0))
    assert frozen.scope["import_model"] == "zonal"
    hub = frozen.zonal_inputs.hub
    v1 = MC._simulate_blocks(hub, draws=DRAWS, seed=SEED)
    zb = Z.simulate_zonal_blocks(frozen.zonal_inputs, draws=DRAWS, seed=SEED)
    assert v1.keys() == zb.keys()
    for lab in v1:
        np.testing.assert_array_equal(v1[lab][0], zb[lab][0])
        np.testing.assert_array_equal(v1[lab][1], zb[lab][1])
    assert _zonal(frozen) == _v1(frozen)


def test_a_grid_whose_own_load_eats_its_supply_raises_lole_over_v1():
    # 200 MW grid unit, 180 MW grid load: at most 20 MW left for the hub.
    frozen = _freeze(_weak(grid_load=180.0))
    assert frozen.scope["import_model"] == "zonal"
    assert frozen.scope["grid_area"]["demand_peak_mw"] == pytest.approx(180.0)
    assert _lole(_zonal(frozen)) > _lole(_v1(frozen))


def test_grid_outages_alone_raise_lole_over_v1():
    frozen = _freeze(_weak(grid_q=0.2))
    assert _lole(_zonal(frozen)) > _lole(_v1(frozen))


def test_grid_side_without_occurrence_data_falls_back_to_v1_and_says_so():
    n = _weak()
    n.generators.at["grid_supply", "carrier"] = "custom_fuel"
    n.add("Carrier", "custom_fuel")
    n.generators.loc["grid_supply", "outage_rate_value"] = np.nan
    n.generators.loc["grid_supply", "mttr_hours"] = np.nan
    frozen = _freeze(n)
    assert frozen.zonal_inputs is None
    assert frozen.scope["import_model"] == "sampled_unit"
    assert frozen.scope["grid_area"] is None
    assert "grid side has no occurrence data" in frozen.scope["note"]


def test_sampled_unit_mode_never_builds_the_grid_area():
    frozen = _freeze(_weak(), import_model="sampled_unit")
    assert frozen.zonal_inputs is None
    assert frozen.scope["import_model"] == "sampled_unit"


def test_zonal_payload_has_the_single_area_shape():
    frozen = _freeze(_weak())
    z, v = _zonal(frozen), _v1(frozen)
    assert set(z) == set(v)
    assert z["n_samples"] == DRAWS


def test_mc_adequacy_default_path_is_unchanged_by_the_hook():
    """``blocks_fn=None`` is the historic call — identical output."""
    frozen = _freeze(_weak(), import_model="sampled_unit")
    a = MC.mc_adequacy(frozen.mc_inputs, draws=DRAWS, seed=SEED)
    b = MC.mc_adequacy(frozen.mc_inputs, draws=DRAWS, seed=SEED,
                       blocks_fn=None)
    assert a == b


def test_a_firm_link_in_front_of_a_sampled_grid_is_zonal_grid_sampled():
    n = _weak()
    for c in ("outage_rate_value", "mttr_hours"):
        n.links[c] = np.nan
    n.links["outage_rate_basis"] = ""
    frozen = _freeze(n)
    assert frozen.scope["import_model"] == "zonal"
    assert frozen.scope["import_firmness"] == "grid_sampled"
    assert frozen.scope["import_link_models"][0]["model"] == "firm_block"
    # The firm Link still binds at its cap when the grid has plenty.
    assert _lole(_zonal(frozen)) >= _lole(_v1(frozen))


def test_a_hub_with_no_sampled_unit_still_certifies_through_the_grid_area():
    """
    Firm Link, no occurrence-bearing hub unit, sampled grid: the two-area
    MC has something to sample even though the hub side alone does not.
    """
    n = _weak()
    n.remove("Generator", ["base", "peaker"])
    for c in ("outage_rate_value", "mttr_hours"):
        n.links[c] = np.nan
    n.links["outage_rate_basis"] = ""
    frozen = _freeze(n)
    assert frozen.mc_error is None
    assert frozen.zonal_inputs is not None and not frozen.mc_inputs.units
    assert "COPT screening skipped" in (frozen.copt_error or "")
    # Hub load 80–120 MW behind a 50 MW Link: short every hour, every draw.
    res = _zonal(frozen)
    assert _lole(res) == pytest.approx(8760.0)

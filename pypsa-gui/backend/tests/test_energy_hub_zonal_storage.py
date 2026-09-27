"""
Grid-side storage in the zonal area (plan
docs/superpowers/plans/2026-09-28-eh-zonal-mc-open-items.md, WP1).

The 2026-09-27 zonal engine left grid storage idle (conservative). The
pinned non-anticipative policy now dispatches it: the grid's own deficit
first, the hub's own storage before remote support, remote support bounded
by the Link headroom, and charging only from surplus that was not offered
to the hub.
"""
from __future__ import annotations

import threading

import numpy as np

from models.energy_hub import default_strong_grid_pack
from services.adequacy import eh_stages as ST
from services.adequacy import mc_zonal as Z
from services.solver_service import SolverConfig
from tests.eh_stage_fixtures import HOURS, VOLL, certifiable_weak_network
from tests.zonal_oracle import legacy_zonal_blocks

OVERLAY = default_strong_grid_pack().import_overlay
DRAWS = 200
SEED = 5
W = 8760.0 / HOURS


def _freeze(n, **kw):
    return ST.freeze_fixed_plan(n, SolverConfig(voll=VOLL), threading.Lock(),
                                overlay=OVERLAY, **kw)


def _weak(*, grid_load=None, grid_battery=None, grid_p_nom=200.0, grid_q=0.02):
    n = certifiable_weak_network()
    n.links.at["import_poc", "p_nom"] = 50.0
    n.generators.at["grid_supply", "p_nom"] = grid_p_nom
    n.generators.at["grid_supply", "outage_rate_value"] = grid_q
    if grid_load is not None:
        n.add("Load", "grid_load", bus="grid", p_set=grid_load)
    if grid_battery is not None:
        p, hours = grid_battery
        n.add("StorageUnit", "grid_bat", bus="grid", carrier="battery",
              p_nom=p, max_hours=hours, outage_rate_value=0.0,
              outage_rate_basis="FOR", mttr_hours=24.0)
    return n


def _oracle(z, **kw):
    a = Z.single_area(z)
    return legacy_zonal_blocks(z.hub, a.grid, a.import_idx, a.firm_import_mw,
                               a.delivery_ratio, draws=DRAWS, seed=SEED, **kw)


def _blocks(z, **kw):
    return Z.simulate_zonal_blocks(z, draws=DRAWS, seed=SEED, **kw)


def _total(blocks, i):
    return sum(v[i] for v in blocks.values())


def _assert_same(a, b):
    assert a.keys() == b.keys()
    for k in a:
        np.testing.assert_array_equal(a[k][0], b[k][0])
        np.testing.assert_array_equal(a[k][1], b[k][1])


def test_grid_without_storage_is_bit_identical_to_the_2026_09_27_kernel():
    frozen = _freeze(_weak(grid_load=150.0))
    z = frozen.zonal_inputs
    assert not Z.single_area(z).grid.storage
    _assert_same(_blocks(z), _oracle(z))


def test_grid_storage_switched_off_is_the_2026_09_27_kernel():
    frozen = _freeze(_weak(grid_load=150.0, grid_battery=(100.0, 4.0)))
    z = frozen.zonal_inputs
    assert Z.single_area(z).grid.storage
    _assert_same(_blocks(z, grid_storage_enabled=False), _oracle(z))


def test_a_grid_battery_supports_the_hub_and_lowers_lole():
    # 200 MW grid unit, 180 MW grid load: 20 MW spare → the hub is short.
    frozen = _freeze(_weak(grid_load=180.0, grid_battery=(100.0, 8.0)))
    z = frozen.zonal_inputs
    with_s = _blocks(z)
    without = _blocks(z, grid_storage_enabled=False)
    assert _total(with_s, 1).mean() < _total(without, 1).mean()   # EUE
    assert _total(with_s, 0).mean() <= _total(without, 0).mean()  # LOLE
    area = frozen.scope["grid_areas"][0] if "grid_areas" in frozen.scope \
        else frozen.scope["grid_area"]
    assert area["storage_dispatched"] is True
    assert area["storage"] == ["grid_bat"]


def test_remote_support_is_bounded_by_the_link_headroom():
    """
    No hub unit, a firm 50 MW Link, a 1000 MW grid battery behind it and
    no grid generation to speak of: the hub can never receive more than 50
    MW, so EUE is exactly Σ w·(load − 50) in every draw.
    """
    n = _weak(grid_p_nom=1.0, grid_q=0.0, grid_battery=(1000.0, 10.0))
    n.remove("Generator", ["base", "peaker"])
    for c in ("outage_rate_value", "mttr_hours"):
        n.links[c] = np.nan
    n.links["outage_rate_basis"] = ""
    frozen = _freeze(n)
    z = frozen.zonal_inputs
    assert z is not None
    load = n.loads_t.p_set["hub_load"].to_numpy()
    expected = W * float(np.maximum(load - 50.0, 0.0).sum())
    eue = _total(_blocks(z), 1)
    np.testing.assert_allclose(eue, expected, rtol=1e-9)


def test_grid_storage_never_charges_from_power_offered_to_the_hub():
    """
    Grid unit exactly as large as the Link (50 MW, q = 0): the whole
    surplus is offered to the hub every hour, so an EMPTY grid battery can
    never charge and never helps — EUE equals the no-storage EUE.
    """
    n = _weak(grid_p_nom=50.0, grid_q=0.0, grid_battery=(100.0, 4.0))
    frozen = _freeze(n)
    z = frozen.zonal_inputs
    empty = _blocks(z, initial_soc_frac=0.0)
    none = _blocks(z, initial_soc_frac=0.0, grid_storage_enabled=False)
    _assert_same(empty, none)


def test_grid_battery_charges_from_the_surplus_left_after_the_offer():
    """
    Plenty of grid surplus beyond the Link: an empty grid battery charges
    and later supports the hub, so it must beat the no-storage run.
    """
    n = _weak(grid_p_nom=100.0, grid_q=0.2, grid_battery=(50.0, 8.0))
    frozen = _freeze(n)
    z = frozen.zonal_inputs
    empty = _blocks(z, initial_soc_frac=0.0)
    none = _blocks(z, initial_soc_frac=0.0, grid_storage_enabled=False)
    assert _total(empty, 1).mean() < _total(none, 1).mean()


def test_mc_payload_through_the_adequacy_wrapper_accepts_the_switch():
    frozen = _freeze(_weak(grid_load=180.0, grid_battery=(100.0, 8.0)))
    res = Z.zonal_mc_adequacy(frozen.zonal_inputs, draws=DRAWS, seed=SEED,
                              cov_target=0.0, max_draws=DRAWS)
    off = Z.zonal_mc_adequacy(frozen.zonal_inputs, draws=DRAWS, seed=SEED,
                              cov_target=0.0, max_draws=DRAWS,
                              grid_storage_enabled=False)
    assert res["eue_mwh"] < off["eue_mwh"]

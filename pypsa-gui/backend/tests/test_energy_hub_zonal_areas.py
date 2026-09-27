"""
Several grid areas behind one hub (plan
docs/superpowers/plans/2026-09-28-eh-zonal-mc-open-items.md, WP2).

The 2026-09-27 zonal path refused a hub whose PoC Links reach several
disconnected grid components ("one grid area") and fell back to v1. Each
component reached by a live import Link is now its own area, sampled from
its own substream; an area with no sampled unit leaves its Links on v1
(unbounded surplus) while the others are bounded.
"""
from __future__ import annotations

import threading

import numpy as np
import pandas as pd
import pypsa

from models.energy_hub import default_strong_grid_pack
from services.adequacy import eh_stages as ST
from services.adequacy import mc as MC
from services.adequacy import mc_zonal as Z
from services.solver_service import SolverConfig
from tests.eh_stage_fixtures import HOURS, VOLL, certifiable_weak_network

OVERLAY = default_strong_grid_pack().import_overlay
DRAWS = 300
SEED = 3


def _freeze(n, **kw):
    return ST.freeze_fixed_plan(n, SolverConfig(voll=VOLL), threading.Lock(),
                                overlay=OVERLAY, **kw)


def two_grid_hub(*, b_sampled: bool = True, b_islanded: bool = False,
                 a_load: float = 170.0) -> pypsa.Network:
    """A hub fed by two PoC Links from two separate grids A and B."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    n.snapshot_weightings.loc[:, :] = 8760.0 / HOURS
    for c in ("gas", "AC", "grid_mix"):
        n.add("Carrier", c)
    for b in ("grid_a", "grid_b", "hub"):
        n.add("Bus", b, carrier="AC")
    n.add("Load", "hub_load", bus="hub", p_set=pd.Series(
        [90.0, 100.0, 110.0, 120.0, 110.0, 100.0, 90.0, 80.0],
        index=n.snapshots))
    n.add("Generator", "base", bus="hub", carrier="gas", p_nom=60.0,
          outage_rate_value=0.05, outage_rate_basis="EFORd", mttr_hours=50.0)
    n.add("Generator", "gen_a", bus="grid_a", carrier="gas", p_nom=200.0,
          outage_rate_value=0.05, outage_rate_basis="EFORd", mttr_hours=50.0)
    n.add("Load", "load_a", bus="grid_a", p_set=a_load)
    if b_sampled:
        n.add("Generator", "gen_b", bus="grid_b", carrier="gas", p_nom=60.0,
              outage_rate_value=0.1, outage_rate_basis="EFORd", mttr_hours=30.0)
    else:
        n.add("Generator", "gen_b", bus="grid_b", carrier="grid_mix", p_nom=60.0)
    for name, g in (("poc_a", "grid_a"), ("poc_b", "grid_b")):
        n.add("Link", name, bus0=g, bus1="hub", p_nom=40.0, carrier="AC",
              outage_rate_value=0.02, outage_rate_basis="FOR", mttr_hours=24.0)
    if b_islanded:
        n.links.at["poc_b", "p_max_pu"] = 0.0
    n.links["eh_role"] = "grid_import"
    n.buses["eh_critical"] = False
    n.buses.at["hub", "eh_critical"] = True
    return n


def _areas(frozen):
    return frozen.scope["grid_areas"]


def test_a_single_grid_is_one_area_on_stream_zero():
    n = certifiable_weak_network()
    n.links.at["import_poc", "p_nom"] = 50.0
    frozen = _freeze(n)
    [area] = _areas(frozen)
    assert area["sampled"] is True and area["links"] == ["import_poc"]
    assert Z.single_area(frozen.zonal_inputs).stream == 0


def test_two_sampled_grids_are_two_areas():
    frozen = _freeze(two_grid_hub())
    scope = frozen.scope
    assert scope["import_model"] == "zonal"
    areas = _areas(frozen)
    assert [a["links"] for a in areas] == [["poc_a"], ["poc_b"]]
    assert [a["units"] for a in areas] == [["gen_a"], ["gen_b"]]
    assert all(a["sampled"] for a in areas)
    z = frozen.zonal_inputs
    assert [a.stream for a in z.areas] == [0, 1]
    # Each area owns its own Link unit position in the hub fleet.
    names = [u.name for u in z.hub.units]
    assert [[names[i] for i in a.import_idx] for a in z.areas] == [
        ["link:poc_a"], ["link:poc_b"]]


def test_the_grid_bound_never_lowers_lole_below_v1_draw_by_draw():
    frozen = _freeze(two_grid_hub())
    z = frozen.zonal_inputs
    zb = Z.simulate_zonal_blocks(z, draws=DRAWS, seed=SEED)
    v1 = MC._simulate_blocks(z.hub, draws=DRAWS, seed=SEED)
    for lab in v1:
        assert np.all(zb[lab][0] >= v1[lab][0])
        assert np.all(zb[lab][1] >= v1[lab][1] - 1e-9)
    # Grid A's own 170 MW load leaves at most 30 MW for a 40 MW Link: the
    # bound bites, so the zonal EUE is strictly higher on average.
    assert sum(v[1] for v in zb.values()).mean() > \
        sum(v[1] for v in v1.values()).mean()


def test_an_unsampled_grid_leaves_its_links_on_v1_and_says_why():
    frozen = _freeze(two_grid_hub(b_sampled=False))
    assert frozen.scope["import_model"] == "zonal"
    a, b = _areas(frozen)
    assert a["sampled"] is True
    assert b["sampled"] is False and b["links"] == ["poc_b"]
    assert "no occurrence data" in b["reason"]
    assert frozen.zonal_inputs.areas[1].grid is None


def test_a_component_reached_only_by_islanded_links_gets_no_area():
    frozen = _freeze(two_grid_hub(b_islanded=True))
    areas = _areas(frozen)
    assert [a["links"] for a in areas] == [["poc_a"]]


def test_no_sampled_grid_anywhere_stays_on_v1():
    n = two_grid_hub(b_sampled=False)
    n.generators.at["gen_a", "carrier"] = "grid_mix"
    for c in ("outage_rate_value", "mttr_hours"):
        n.generators.at["gen_a", c] = np.nan
    frozen = _freeze(n)
    assert frozen.zonal_inputs is None
    assert frozen.scope["import_model"] == "sampled_unit"
    assert frozen.scope["grid_areas"] == []
    assert "no grid area is sampled" in frozen.scope["note"]

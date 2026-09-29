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
                 a_load: float | None = 170.0, caps=(40.0, 40.0),
                 base_mw: float = 60.0, gen_a: tuple = (200.0, 0.05),
                 gen_b: tuple = (60.0, 0.1), effs=(1.0, 1.0),
                 a2: bool = False) -> pypsa.Network:
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
    n.add("Generator", "base", bus="hub", carrier="gas", p_nom=base_mw,
          outage_rate_value=0.05, outage_rate_basis="EFORd", mttr_hours=50.0)
    n.add("Generator", "gen_a", bus="grid_a", carrier="gas", p_nom=gen_a[0],
          outage_rate_value=gen_a[1], outage_rate_basis="EFORd",
          mttr_hours=50.0)
    if a_load is not None:
        n.add("Load", "load_a", bus="grid_a", p_set=a_load)
    if b_sampled:
        n.add("Generator", "gen_b", bus="grid_b", carrier="gas",
              p_nom=gen_b[0], outage_rate_value=gen_b[1],
              outage_rate_basis="EFORd", mttr_hours=30.0)
    else:
        n.add("Generator", "gen_b", bus="grid_b", carrier="grid_mix", p_nom=60.0)
    links = [("poc_a", "grid_a", caps[0], effs[0]),
             ("poc_b", "grid_b", caps[1], effs[1])]
    if a2:
        links.append(("poc_a2", "grid_a", 25.0, 1.0))
    for name, g, cap, eff in links:
        n.add("Link", name, bus0=g, bus1="hub", p_nom=cap, carrier="AC",
              efficiency=eff, outage_rate_value=0.02,
              outage_rate_basis="FOR", mttr_hours=24.0)
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


# ── review of WP2: pins for the mutants that survived the first tests ────

def _assert_same_blocks(a, b):
    assert a.keys() == b.keys()
    for k in a:
        np.testing.assert_array_equal(a[k][0], b[k][0])
        np.testing.assert_array_equal(a[k][1], b[k][1])


def test_two_unbound_areas_replay_v1_bit_for_bit():
    """
    Non-integer caps and 2000 draws: summing the areas in float64 before
    the float32 cast differed from v1 by up to 0.008 MWh per draw. The
    per-area float32 accumulation in Link-position order is exact.
    """
    n = two_grid_hub(a_load=None, caps=(40.37, 17.13), base_mw=61.77,
                     gen_a=(5000.0, 0.0), gen_b=(5000.0, 0.0))
    z = _freeze(n).zonal_inputs
    assert len(z.areas) == 2
    _assert_same_blocks(Z.simulate_zonal_blocks(z, draws=2000, seed=SEED),
                        MC._simulate_blocks(z.hub, draws=2000, seed=SEED))


def test_an_unsampled_area_passes_its_links_through_at_v1():
    """Area A unbound, area B unsampled: both Links behave as in v1."""
    n = two_grid_hub(b_sampled=False, a_load=None, gen_a=(5000.0, 0.0))
    z = _freeze(n).zonal_inputs
    assert z.areas[1].grid is None
    _assert_same_blocks(Z.simulate_zonal_blocks(z, draws=DRAWS, seed=SEED),
                        MC._simulate_blocks(z.hub, draws=DRAWS, seed=SEED))


def test_each_area_samples_its_own_substream():
    """Identical fleets in A and B must NOT draw identical outage paths."""
    n = two_grid_hub(a_load=None, gen_a=(100.0, 0.2), gen_b=(100.0, 0.2))
    n.generators.at["gen_b", "mttr_hours"] = 50.0  # same chain as gen_a
    z = _freeze(n).zonal_inputs
    a, b = z.areas
    assert [(u.capacity_mw, u.q, u.mttr_hours) for u in a.grid.units] == \
        [(u.capacity_mw, u.q, u.mttr_hours) for u in b.grid.units]
    ss = np.random.SeedSequence(SEED)
    states = [Z._AreaState(a, z.hub, tuple(z.hub.units), ss, len(z.hub.residual),
                           DRAWS, True, 1.0) for a in z.areas]
    assert not np.array_equal(states[0].grid_t, states[1].grid_t)


def test_a_firm_link_lands_in_its_own_area():
    n = two_grid_hub()
    n.links.at["poc_b", "mttr_hours"] = np.nan
    frozen = _freeze(n)
    z = frozen.zonal_inputs
    names = [u.name for u in z.hub.units]
    a, b = z.areas
    assert [names[i] for i in a.import_idx] == ["link:poc_a"]
    assert b.import_idx == ()
    np.testing.assert_array_equal(a.firm_import_mw, 0.0)
    np.testing.assert_array_equal(b.firm_import_mw, 40.0)


def test_the_delivery_ratio_is_per_area():
    frozen = _freeze(two_grid_hub(effs=(0.9, 0.5)))
    a, b = frozen.zonal_inputs.areas
    np.testing.assert_allclose(a.delivery_ratio, 0.9)
    np.testing.assert_allclose(b.delivery_ratio, 0.5)


def test_interleaved_links_group_by_area_in_listing_order():
    """Links a, b, a2: area A holds a and a2 (in position order), B holds b."""
    frozen = _freeze(two_grid_hub(a2=True))
    assert [a["links"] for a in frozen.scope["grid_areas"]] == [
        ["poc_a", "poc_a2"], ["poc_b"]]
    z = frozen.zonal_inputs
    names = [u.name for u in z.hub.units]
    assert [[names[i] for i in a.import_idx] for a in z.areas] == [
        ["link:poc_a", "link:poc_a2"], ["link:poc_b"]]


def test_a_refused_grid_snapshot_leaves_that_area_unsampled(monkeypatch):
    from services.adequacy import mc as MCmod

    real = MCmod.snapshot_inputs

    def refuse_grid_b(n, **kw):
        if "grid_b" in n.buses.index and "hub" not in n.buses.index:
            raise ValueError("synthetic refusal")
        return real(n, **kw)

    monkeypatch.setattr(MCmod, "snapshot_inputs", refuse_grid_b)
    frozen = _freeze(two_grid_hub())
    a, b = frozen.scope["grid_areas"]
    assert a["sampled"] is True
    assert b["sampled"] is False and "synthetic refusal" in b["reason"]
    assert b["capacity_mw"] is None and b["demand_peak_mw"] is None

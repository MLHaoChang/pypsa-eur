"""AS-11 slice 1: the readers count a split storage (Store + charger/discharger Links) like the equal StorageUnit.

Each test builds one network with a `StorageUnit` and a copy where `migrate_storage_unit` turns it into the split
layout (min_hours = max_hours = its max_hours, the same LP), then reads both. Generic engineering values only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.adequacy import elcc as E
from services.adequacy import mc as M
from services.compare.dispatch import _compute_dispatch_summary
from services.compare.storage_cycling import _compute_storage_cycling_summary
from services.solver.split_storage import _wrap_with_split_storage, add_split_storage, migrate_storage_unit
from services.solver.storage_inventory import SPLIT, STORAGE_UNIT, list_storages, storage_notes

HOURS = 48


def _adequacy_site(**su) -> pypsa.Network:
    """A 100 MW peak on two 80 MW units that fail now and then; a battery rides the short peaks."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    n.add("Carrier", "gas")
    n.add("Carrier", "battery")
    n.add("Bus", "ac", carrier="AC")
    load = np.where(np.arange(HOURS) % 12 < 2, 100.0, 60.0)
    n.add("Load", "load", bus="ac", p_set=pd.Series(load, n.snapshots))
    for g in ("g1", "g2"):
        n.add("Generator", g, bus="ac", carrier="gas", p_nom=55.0,
              outage_rate_value=0.1, outage_rate_basis="FOR", mttr_hours=6.0)
    n.add("StorageUnit", "bess", bus="ac", carrier="battery", **su)
    return n


def _pair(**su) -> tuple[pypsa.Network, pypsa.Network]:
    legacy = _adequacy_site(**su)
    split = _adequacy_site(**su)
    migrate_storage_unit(split, "bess")
    return legacy, split


SU = dict(p_nom=20.0, max_hours=2.0, efficiency_store=0.95, efficiency_dispatch=0.9, standing_loss=0.001)


def _spec(s: M.StorageSpec) -> tuple:
    return (s.name, s.p_nom_mw, s.e_nom_mwh, s.eff_store, s.eff_dispatch, s.q, s.basis, s.source)


def test_the_inventory_lists_both_layouts_with_power_energy_and_efficiencies():
    legacy, split = _pair(**SU)
    (a,), (b,) = list_storages(legacy), list_storages(split)
    assert (a.layout, b.layout) == (STORAGE_UNIT, SPLIT)
    for e in (a, b):
        assert (e.name, e.bus, e.carrier) == ("bess", "ac", "battery")
        assert e.p_nom_mw == pytest.approx(20.0)
        assert e.e_nom_mwh == pytest.approx(40.0)
        assert (e.eff_store, e.eff_dispatch) == pytest.approx((0.95, 0.9))
        assert e.standing_loss == pytest.approx(0.001)
        assert (e.p_charge_mw, e.p_dispatch_mw) == pytest.approx((20.0, 20.0))
    assert (b.power_component, b.power_row, b.discharger) == ("links", "bess charger", "bess discharger")


def test_the_inventory_reads_solved_sizes_of_an_extendable_split_storage():
    n = _adequacy_site(**SU)
    add_split_storage(n, "new", "ac", "energy_bess")
    n.links.loc[["new charger", "new discharger"], "p_nom_opt"] = [12.0, 12.0 / 0.96]
    n.stores.loc["new", "e_nom_opt"] = 48.0
    by = {e.name: e for e in list_storages(n)}
    assert [e.name for e in list_storages(n)] == ["bess", "new"]
    assert (by["new"].p_nom_mw, by["new"].e_nom_mwh) == pytest.approx((12.0, 48.0))
    # Declined by the LP: zero, never the pre-solve nominal.
    n.links.loc["new charger", "p_nom_opt"] = 0.0
    n.stores.loc["new", "e_nom_opt"] = 0.0
    assert {e.name: e.p_nom_mw for e in list_storages(n)}["new"] == 0.0


def test_a_split_storage_whose_link_is_gone_is_not_listed_but_named():
    _, split = _pair(**SU)
    split.remove("Link", "bess discharger")
    assert list_storages(split) == []
    inp = M.snapshot_inputs(split)
    assert inp.storage == ()
    assert inp.storage_notes == ("bess: split storage without its charger or discharger link; not counted",)
    assert M.mc_adequacy(inp, draws=50, seed=1, max_draws=50)["storage_notes"] == list(inp.storage_notes)


def test_an_untagged_store_with_links_is_named_not_counted():
    n = _adequacy_site(**SU)
    n.add("Bus", "pe store", carrier="battery")
    n.add("Store", "pe", bus="pe store", carrier="battery", e_nom=40.0)
    n.add("Link", "pe charger", bus0="ac", bus1="pe store", p_nom=20.0)
    n.add("Link", "pe discharger", bus0="pe store", bus1="ac", p_nom=20.0)
    assert [e.name for e in list_storages(n)] == ["bess"]
    assert storage_notes(n) == ["pe: Store with charging and discharging links but no split-storage tag; not counted"]
    # A hydrogen store with an electrolyser and a fuel cell is not a battery: no note.
    n.add("Bus", "h2", carrier="H2")
    n.add("Store", "h2 tank", bus="h2", carrier="H2", e_nom=100.0)
    n.add("Link", "electrolysis", bus0="ac", bus1="h2", p_nom=5.0)
    n.add("Link", "fuel cell", bus0="h2", bus1="ac", p_nom=5.0)
    assert len(storage_notes(n)) == 1


@pytest.mark.parametrize("p_nom", [17.3, 0.1 + 0.2, 123.456789, 1e-3 * 7])
def test_a_migrated_storage_unit_reads_bit_identical_at_any_size(p_nom):
    legacy, split = _pair(**{**SU, "p_nom": p_nom, "max_hours": 2.7})
    assert [_spec(s) for s in M.snapshot_inputs(split).storage] == [_spec(s) for s in M.snapshot_inputs(legacy).storage]


def test_an_undersized_discharger_caps_the_power_and_is_named():
    """A fixed split storage whose discharger delivers 12 MW to the bus but whose charger draws 20 MW counts as
    12 MW, the AC-side power it can give back."""
    _, split = _pair(**SU)
    split.links.loc["bess discharger", "p_nom"] = 12.0 / 0.9
    (e,) = list_storages(split)
    assert (e.p_charge_mw, e.p_dispatch_mw, e.p_nom_mw) == pytest.approx((20.0, 12.0, 12.0))
    inp = M.snapshot_inputs(split)
    assert inp.storage[0].p_nom_mw == pytest.approx(12.0)
    assert inp.storage[0].e_nom_mwh == pytest.approx(40.0 * 12.0 / 12.0)
    assert len(inp.storage_notes) == 1 and inp.storage_notes[0].startswith("bess: charger 20 MW")


def test_a_discharger_availability_below_one_derates_the_power():
    legacy, split = _pair(**SU)
    split.links.loc["bess discharger", "p_max_pu"] = 0.5
    (s,) = M.snapshot_inputs(split).storage
    assert s.p_nom_mw == pytest.approx(10.0)
    assert s.e_nom_mwh == pytest.approx(40.0)          # energy is not an availability
    # Time-varying: the lowest value in the block counts (one block here).
    split.links.loc["bess discharger", "p_max_pu"] = 1.0
    split.links_t.p_max_pu = pd.DataFrame({"bess discharger": np.linspace(1.0, 0.25, HOURS)}, index=split.snapshots)
    assert M.snapshot_inputs(split).storage[0].p_nom_mw == pytest.approx(5.0)
    assert M.snapshot_inputs(legacy).storage_notes == ()


def test_the_monte_carlo_gives_a_split_storage_the_same_ens_as_the_equal_storage_unit():
    legacy, split = _pair(**SU)
    a, b = M.snapshot_inputs(legacy), M.snapshot_inputs(split)
    assert [_spec(s) for s in a.storage] == [_spec(s) for s in b.storage]
    assert a.storage[0].q > 0       # the battery carrier default reaches the split storage through its Store
    ra = M.mc_adequacy(a, draws=200, seed=7, max_draws=200)
    rb = M.mc_adequacy(b, draws=200, seed=7, max_draws=200)
    assert ra["eue_mwh"] > 0
    assert rb["eue_mwh"] == ra["eue_mwh"]
    assert rb["lole_hours"] == ra["lole_hours"]
    # The battery matters here, so equality is not two empty results agreeing.
    none = M.mc_adequacy(a, draws=200, seed=7, max_draws=200, storage_enabled=False)
    assert none["eue_mwh"] > ra["eue_mwh"]


def test_elcc_credits_a_split_storage_like_the_equal_storage_unit():
    legacy, split = _pair(**SU)
    rows = [E.elcc_for_asset(M.snapshot_inputs(n), "storage_unit", "bess", seed=3, draws=100)
            for n in (legacy, split)]
    assert rows[0]["status"] == "ok", rows[0]
    assert rows[0]["elcc_mw"] > 0
    assert rows[1] == rows[0]


# ── compare readers (the AS-02 hand case: arbitrage on a two-price day) ─────────────────────────────────────

def _priced_site(price: np.ndarray) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(len(price)))
    n.snapshot_weightings.loc[:, "objective"] = 8760 / len(price)
    n.add("Bus", "ac", carrier="AC")
    n.add("Load", "load", bus="ac", p_set=50.0)
    n.add("Generator", "import", bus="ac", carrier="import", p_nom=1e4,
          marginal_cost=pd.Series(price, n.snapshots))
    n.add("StorageUnit", "bess", bus="ac", carrier="battery", p_nom=100.0, max_hours=4.0,
          efficiency_store=0.95, efficiency_dispatch=0.95)
    return n


def _solved_pair() -> tuple[pypsa.Network, pypsa.Network]:
    price = np.array([200.0 if t % 12 < 4 else 10.0 for t in range(HOURS)])
    legacy = _priced_site(price)
    assert legacy.optimize(solver_name="highs")[0] == "ok"
    split = _priced_site(price)
    migrate_storage_unit(split, "bess")
    assert split.optimize(solver_name="highs", extra_functionality=_wrap_with_split_storage(split, None))[0] == "ok"
    assert float(split.objective) == pytest.approx(float(legacy.objective), rel=1e-6)
    return legacy, split


def test_storage_cycling_and_compare_dispatch_show_a_split_storage_like_the_storage_unit():
    legacy, split = _solved_pair()

    cyc = [_compute_storage_cycling_summary(n, [], False, True) for n in (legacy, split)]
    (ua,), (ub,) = cyc[0].by_unit, cyc[1].by_unit
    assert (ub.name, ub.carrier) == ("bess", "battery")
    assert (ub.p_nom_mw, ub.energy_mwh) == pytest.approx((100.0, 400.0))
    assert ua.cycles.total > 0
    assert ub.cycles.total == pytest.approx(ua.cycles.total, rel=1e-6)
    assert ub.throughput_mwh.total == pytest.approx(ua.throughput_mwh.total, rel=1e-6)

    dis = [_compute_dispatch_summary(n, [], False, True) for n in (legacy, split)]
    da, db = (d.model_dump() for d in dis)
    assert db["dispatch_gwh_by_carrier"]["battery"]["total"] == pytest.approx(
        da["dispatch_gwh_by_carrier"]["battery"]["total"], rel=1e-6)
    assert db["storage_cycles_by_carrier"]["battery"]["total"] == pytest.approx(
        da["storage_cycles_by_carrier"]["battery"]["total"], rel=1e-6)
    assert da["storage_cycles_by_carrier"]["battery"]["total"] > 0


def test_storage_cycling_is_available_on_a_network_with_only_split_storage():
    _, split = _solved_pair()
    out = _compute_storage_cycling_summary(split, [], False, True)
    assert out.available and [u.name for u in out.by_unit] == ["bess"]


def test_a_migrated_discharge_only_unit_is_named_when_its_charger_is_blocked():
    """GK A ruling (a): the split reading applies the charger's p_max_pu (= -p_min_pu = 0), so the storage is not
    counted; it is named, not dropped silently. The StorageUnit reader does the same since slice 2."""
    _, split = _pair(**{**SU, "p_min_pu": 0.0})
    inp = M.snapshot_inputs(split)
    assert inp.storage == ()
    assert inp.storage_notes == ("bess: p_max_pu of bess charger is 0 in every block; not counted",)
    assert M.mc_adequacy(inp, draws=50, seed=1, max_draws=50)["storage_notes"] == list(inp.storage_notes)


# ── slice 2: the adequacy readers ─────────────────────────────────────────────────────────────────────────────

def _island_site(split: bool) -> pypsa.Network:
    """A 48-hour site with a 20 MW / 80 MWh ride-through battery and a 40 MW / 10 MWh UPS, either as
    StorageUnits or migrated to the split layout."""
    from tests.test_smoothing_screen import _site
    n = _site([], {"critical": 4.0, "it": 6.0})
    n.add("Carrier", "battery")
    for name, p, h in (("rt", 20.0, 4.0), ("ups", 40.0, 0.25)):
        n.add("StorageUnit", name, bus="site", carrier="battery", p_nom=p, max_hours=h,
              efficiency_store=0.95, efficiency_dispatch=0.9)
        if split:
            migrate_storage_unit(n, name)
    return n


ISLAND = {"units": {"rt": {"kind": "bess"}, "critical": {"kind": "load", "critical": True},
                    "ups": {"kind": "ups", "it_load": ["it"], "walk_in_s": 600.0}},
          "requirements": {"sustained_h": {"value": 12.0}, "bridge_s": {"value": 600.0}}}


def test_the_ride_through_and_smoothing_screens_read_a_split_storage_like_the_storage_unit():
    from services.adequacy import smoothing as SM
    from services.adequacy.site_peak import ride_through_batteries
    from tests.test_smoothing_screen import _phase
    legacy, split = _island_site(False), _island_site(True)
    from services.adequacy.site_peak import deliverable_mwh, storage_sizes
    for u, p, e in (("rt", 20.0, 80.0), ("ups", 40.0, 10.0)):
        assert storage_sizes(legacy, u) == storage_sizes(split, u) == (p, e, 0.95, 0.9)
        assert deliverable_mwh(split, u) == deliverable_mwh(legacy, u) == 0.9 * e
    rt = [ride_through_batteries(n, ISLAND) for n in (legacy, split)]
    assert rt[0]["names"] == ["rt"] and rt[0]["e_mwh"] == pytest.approx(0.9 * 80.0)
    assert rt[1] == rt[0]
    screens = [SM.smoothing_screen(n, ISLAND, {"phases": [_phase(10.0)]}, ramp_limit_mw_per_min=20.0,
                                   oscillation_limit_pct=1.0, connection_mw=200.0) for n in (legacy, split)]
    (pa,), (pb,) = screens[0]["phases"], screens[1]["phases"]
    assert pa["reserve_mw"] == pytest.approx(60.0)
    for k in ("reserve_mw", "reserve_mwh", "backup_mwh", "need_mwh"):
        assert pb[k] == pytest.approx(pa[k]), k
    assert pb["note"] == pa["note"]


def test_an_extendable_split_storage_is_unsized_as_the_storage_unit_is():
    from services.adequacy.site_peak import committed_storage
    n = _island_site(True)
    n.stores.at["rt", "e_nom_extendable"] = True
    assert committed_storage(n, ["rt", "ups"]) == (["ups"], ["rt"])


def test_a_split_storage_is_in_service_only_while_all_three_rows_are():
    """GK A: the latest build year and the earliest end of life decide; a Store that retires first ends it."""
    from services.adequacy.site_peak import committed_storage
    n = _island_site(True)
    for df, row in ((n.links, "rt charger"), (n.links, "rt discharger"), (n.stores, "rt")):
        df.at[row, "build_year"], df.at[row, "lifetime"] = 2030, 20
    n.stores.at["rt", "lifetime"] = 5
    assert committed_storage(n, ["rt"], 2034) == (["rt"], [])
    assert committed_storage(n, ["rt"], 2036) == ([], [])
    n.stores.at["rt", "lifetime"] = 20
    n.links.at["rt discharger", "build_year"] = 2033
    assert committed_storage(n, ["rt"], 2031) == ([], [])
    n.links.at["rt discharger", "build_year"] = 2030
    n.stores.at["rt", "active"] = False
    assert committed_storage(n, ["rt"], 2031) == ([], [])


def test_the_portfolio_fingerprint_follows_a_split_storage_and_leaves_storage_unit_networks_alone():
    from services.adequacy.portfolio import network_fingerprint
    legacy, split = _pair(**SU)
    before = network_fingerprint(split)
    split.stores.at["bess", "e_nom"] = 50.0
    assert network_fingerprint(split) != before
    split.stores.at["bess", "e_nom"] = 40.0
    split.links.at["bess discharger", "p_max_pu"] = 0.5
    assert network_fingerprint(split) != before
    split.links.at["bess discharger", "p_max_pu"] = 1.0
    split.links.at["bess charger", "p_min_pu"] = -0.5
    assert network_fingerprint(split) != before
    split.links.at["bess charger", "p_min_pu"] = -1.0
    split.add("Bus", "other", carrier="AC")
    split.links.at["bess discharger", "bus1"] = "other"
    assert network_fingerprint(split) != before


def test_storage_unit_fingerprints_are_byte_identical_to_before_slice_2():
    """Values from master 513e15333: a StorageUnit-only network, and one without storage, hash as before."""
    from services.adequacy.portfolio import network_fingerprint
    n = _adequacy_site(**SU)
    n.add("StorageUnit", "b2", bus="ac", carrier="battery", p_nom=5.0, max_hours=4.0, p_nom_extendable=True,
          p_nom_opt=7.0)
    assert network_fingerprint(n) == "v2:a511248443c65748bf8be4ff4c9c40ec51caac456c8abb0870e403682de91c7b"
    n.storage_units_t.p_max_pu = pd.DataFrame({"bess": 1.0}, index=n.snapshots)   # explicit defaults
    assert network_fingerprint(n) == "v2:a511248443c65748bf8be4ff4c9c40ec51caac456c8abb0870e403682de91c7b"
    n.remove("StorageUnit", ["bess", "b2"])
    assert network_fingerprint(n) == "v2:d389fa4a2019f0a1dce4ee39e95475b14a09e010904611ee09984d985da689f0"


def test_a_redundancy_scenario_restores_a_split_storage_store():
    from services.adequacy.redundancy import apply_redundancy_scenario
    _, split = _pair(**SU)
    split.stores_t.e = pd.DataFrame({"bess": 1.0}, index=split.snapshots)
    stores, e, p = split.stores.copy(), split.stores_t.e.copy(), split.stores_t.p.copy()
    undo, _ = apply_redundancy_scenario(split, "parallel_storage")
    split.stores.at["bess", "e_nom_opt"] = 123.0      # what a solve writes
    split.stores_t.e = split.stores_t.e * 0 + 9.0
    split.stores_t.p = pd.DataFrame({"bess": 2.0}, index=split.snapshots)
    undo()
    pd.testing.assert_frame_equal(split.stores, stores)
    pd.testing.assert_frame_equal(split.stores_t.e, e)
    pd.testing.assert_frame_equal(split.stores_t.p, p)


def test_storage_duration_sets_a_split_storage_to_the_storage_unit_energy():
    from services.adequacy.levers import apply_lever_scenario
    legacy, split = _pair(**SU)
    out = []
    for n in (legacy, split):
        undo, meta = apply_lever_scenario(n, "storage_duration", value=4.0)
        assert meta["applied_storage"] == ["bess"]
        out.append(M.snapshot_inputs(n))
        undo()
    assert out[0].storage[0].e_nom_mwh == pytest.approx(80.0)
    assert [_spec(s) for s in out[1].storage] == [_spec(s) for s in out[0].storage]
    assert split.stores.at["bess", "e_nom"] == pytest.approx(40.0)      # undone
    assert M.mc_adequacy(out[1], draws=100, seed=2, max_draws=100)["eue_mwh"] == \
        M.mc_adequacy(out[0], draws=100, seed=2, max_draws=100)["eue_mwh"]


def test_storage_duration_outside_the_technology_range_is_refused_by_name():
    from services.adequacy.levers import LeverScenarioError, apply_lever_scenario
    _, split = _pair(**SU)                          # migrated 2 h: the energy BESS range, 2–8 h
    before = split.stores.copy()
    with pytest.raises(LeverScenarioError, match="bess"):
        apply_lever_scenario(split, "storage_duration", value=1.0)
    pd.testing.assert_frame_equal(split.stores, before)


def test_eh_readiness_counts_a_split_storage_and_its_outage_data():
    from services.adequacy.eh_readiness import outage_scan
    legacy, split = _pair(**SU)
    assert outage_scan(split, [])[0]["StorageUnit"] == outage_scan(legacy, [])[0]["StorageUnit"] == 1
    split.add("Bus", "other", carrier="AC")
    split.links.at["bess discharger", "bus1"] = "other"      # not counted by the inventory, so not here
    assert outage_scan(split, [])[0]["StorageUnit"] == 0


def test_the_activity_note_names_an_inactive_split_storage():
    from services.adequacy.activity import activity_summary, period_blocks
    legacy, split = _pair(**SU)
    legacy.storage_units.at["bess", "active"] = False
    for row in ("charger", "discharger", "store"):
        split = _pair(**SU)[1]
        if row == "store":
            split.stores.at["bess", "active"] = False
        else:
            split.links.at[f"bess {row}", "active"] = False
        notes = [activity_summary(n, period_blocks(n.snapshots)) for n in (legacy, split)]
        assert notes[1] == notes[0], row
        assert "bess" in notes[1]["note"]
        assert M.snapshot_inputs(split).storage == M.snapshot_inputs(legacy).storage == ()


def test_links_blocked_in_different_blocks_are_both_named():
    """GK A nit on slice 1: charger at 0 in one period, discharger in the other, neither throughout."""
    n = _adequacy_site(**SU)
    migrate_storage_unit(n, "bess")
    half = HOURS // 2
    n.snapshots = pd.MultiIndex.from_arrays([[2030] * half + [2040] * half, n.snapshots])
    n.links_t.p_max_pu = pd.DataFrame({"bess charger": [0.0] * half + [1.0] * half,
                                       "bess discharger": [1.0] * half + [0.0] * half}, index=n.snapshots)
    inp = M.snapshot_inputs(n)
    assert inp.storage == ()
    assert inp.storage_notes == ("bess: no block has both bess charger and bess discharger available "
                                 "(p_max_pu above 0) with its Store active; not counted",)


def test_a_split_storage_whose_links_sit_on_different_buses_is_named_not_counted():
    _, split = _pair(**SU)
    split.add("Bus", "other", carrier="AC")
    split.links.at["bess discharger", "bus1"] = "other"
    assert [e.name for e in list_storages(split)] == []
    assert storage_notes(split) == ["bess: charger bess charger draws from ac but discharger bess discharger "
                                    "delivers to other; not counted"]


def test_the_zonal_monte_carlo_gives_a_split_grid_battery_the_same_blocks():
    from tests.test_energy_hub_zonal_storage import _assert_same, _blocks, _freeze, _weak
    legacy = _weak(grid_load=180.0, grid_battery=(100.0, 8.0))
    split = _weak(grid_load=180.0, grid_battery=(100.0, 8.0))
    migrate_storage_unit(split, "grid_bat")
    za, zb = (_freeze(n).zonal_inputs for n in (legacy, split))
    from services.adequacy import mc_zonal as Z
    assert Z.single_area(zb).grid.storage
    _assert_same(_blocks(zb), _blocks(za))


@pytest.mark.parametrize("limits", [{"p_max_pu": 0.5}, {"p_min_pu": -0.25}, {"p_max_pu": 0.8, "p_min_pu": -0.6}])
def test_a_storage_unit_availability_derates_it_as_the_split_links_do(limits):
    """Follow-up to GK A ruling (a): the StorageUnit reader applies p_max_pu and -p_min_pu like the migrated
    Links' p_max_pu, so the two layouts agree on non-default limits."""
    legacy, split = _pair(**{**SU, **limits})
    a, b = M.snapshot_inputs(legacy), M.snapshot_inputs(split)
    assert a.storage[0].p_nom_mw < SU["p_nom"]
    assert a.storage[0].e_nom_mwh == pytest.approx(SU["p_nom"] * SU["max_hours"])
    assert [_spec(s) for s in a.storage] == [_spec(s) for s in b.storage]
    assert M.mc_adequacy(a, draws=100, seed=4, max_draws=100)["eue_mwh"] == \
        M.mc_adequacy(b, draws=100, seed=4, max_draws=100)["eue_mwh"]


def test_a_discharge_only_storage_unit_is_named_not_counted():
    legacy, _ = _pair(**{**SU, "p_min_pu": 0.0})
    inp = M.snapshot_inputs(legacy)
    assert inp.storage == ()
    assert inp.storage_notes == ("bess: p_max_pu or -p_min_pu is 0 in every block; not counted",)


def test_the_portfolio_fingerprint_follows_a_storage_unit_availability():
    from services.adequacy.portfolio import network_fingerprint
    legacy, _ = _pair(**SU)
    before = network_fingerprint(legacy)
    legacy.storage_units.at["bess", "p_max_pu"] = 0.5
    assert network_fingerprint(legacy) != before


def test_an_inflow_fed_storage_unit_keeps_its_dispatch_power_and_is_named():
    """GK A on #1010: reservoir hydro (p_min_pu 0, inflow > 0) keeps its pre-slice P and E; the note says why."""
    n = _adequacy_site(p_nom=20.0, max_hours=6.0, p_min_pu=0.0)
    n.storage_units_t.inflow = pd.DataFrame({"bess": 2.0}, index=n.snapshots)
    inp = M.snapshot_inputs(n)
    (s,) = inp.storage
    assert (s.p_nom_mw, s.e_nom_mwh) == (20.0, 120.0)
    assert inp.storage_notes == ("bess: inflow-fed; counted at its dispatch power, inflow not modelled",)
    n.storage_units.at["bess", "p_max_pu"] = 0.5          # the dispatch side still derates it
    assert M.snapshot_inputs(n).storage[0].p_nom_mw == 10.0


def test_a_static_inflow_also_marks_a_storage_unit_inflow_fed():
    """GK A on #1010: the static ``inflow`` branch; only a positive inflow counts, in either form."""
    n = _adequacy_site(p_nom=20.0, max_hours=6.0, p_min_pu=0.0)
    n.storage_units.at["bess", "inflow"] = 2.0
    inp = M.snapshot_inputs(n)
    assert (inp.storage[0].p_nom_mw, inp.storage[0].e_nom_mwh) == (20.0, 120.0)
    assert inp.storage_notes == ("bess: inflow-fed; counted at its dispatch power, inflow not modelled",)
    n.storage_units.at["bess", "inflow"] = 0.0
    n.storage_units_t.inflow = pd.DataFrame({"bess": -2.0}, index=n.snapshots)
    assert not any("inflow-fed" in note for note in M.snapshot_inputs(n).storage_notes)

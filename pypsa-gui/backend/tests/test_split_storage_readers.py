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
    counted; it is named, not dropped silently. The StorageUnit reader ignores p_min_pu (issue follow-up)."""
    _, split = _pair(**{**SU, "p_min_pu": 0.0})
    inp = M.snapshot_inputs(split)
    assert inp.storage == ()
    assert inp.storage_notes == ("bess: p_max_pu of bess charger is 0 in every block; not counted",)
    assert M.mc_adequacy(inp, draws=50, seed=1, max_draws=50)["storage_notes"] == list(inp.storage_notes)

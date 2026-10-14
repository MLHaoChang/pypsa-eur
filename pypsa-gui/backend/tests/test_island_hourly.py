"""
I3a of the campus island plan (2026-10-07): the hourly island tables, from a
solved hub network (owner option A, 2026-10-13: gridspine owns the
contracts, ``services/solver/island.py`` produces the values).

The hand network has two hours, and its "solved" results are set by hand, so
every number below is worked from the plan's definitions, not from an LP:

- ΔP⁺ = p_imp − Σ_late min(L_it, p_nom_ups), ΔP⁻ = −p_imp + Σ_transferring L_it;
- M at 0⁺ = Σ_online H·u·η·p_nom/cos φ + Σ_droop GFM τ_f·p_nom/(2R) (+ VSMs);
  under ups_bridged, Σ_g H·η·p_nom/cos φ − k·max(H·U/cos φ);
- ΣK = Σ p_nom/(R·f0) over the GFMs and droop gensets, less the k largest
  genset unit's;
- e(t−1) as PyPSA links the SoC: cyclic wraps, otherwise the initial SoC.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pypsa
import pytest

from gridspine.schema.island import (
    ISLAND_HOURLY_COLUMNS,
    ISLAND_UNIT_HOURLY_COLUMNS,
)
from services.solver.island import island_hourly
from gridspine.schema.island import validate_island_config
from tests.test_island_rows import _BESS, _VSM, _a, _genset, _island, _ups

TOL = 1e-9
#: A minimal requirements block, for validating one unit on its own.
_REQ = {"bridge_s": _a(1.0), "sustained_h": _a(1.0), "rocof_window_ms": _a(500.0),
        "gfm_margin": _a(0.0), "scenarios": ["seamless"],
        "frequency_limits": {s: {"rocof_hz_per_s": _a(1.0), "f_min_hz": _a(47.5),
                                 "f_max_hz": _a(51.5), "qss_band_hz": _a(1.0)}
                             for s in ("transition", "steady_island")}}


def _units(*, uc=True, spinning=False):
    units = {"crit": {"kind": "load", "critical": True},
             "bess": dict(_BESS),
             "ups": _ups(walk_in_s=20.0),
             "g1": _genset(p_min_pu=_a(0.3), spinning=spinning),
             "g2": _genset(p_min_pu=_a(0.3))}
    return units


def _sidecar(*, uc=True, spinning=False, scenarios=("seamless", "ups_bridged", "planned")):
    island = _island(units=_units(uc=uc, spinning=spinning), k=1, scenarios=scenarios,
                     blocks=[5.0] * 4, sustained_h=1.0, bridge_s=1.0)
    island["requirements"]["linearised_uc"] = uc
    return island


def _solved(*, cyclic=True, soc_init=25.0, g1_status=(0.5, 0.0), g2_status=(0.0, 1.0)):
    """Two hours of 2030, with the results a solve would leave, set by hand."""
    n = pypsa.Network()
    sns = pd.date_range("2030-01-01", periods=2, freq="h")
    n.set_snapshots(sns)
    for bus in ("grid", "site"):
        n.add("Bus", bus)
    n.add("Generator", "supply", bus="grid", p_nom=1000.0)
    n.add("Link", "imp", bus0="grid", bus1="site", p_nom=100.0, efficiency=1.0)
    n.add("Link", "exp", bus0="site", bus1="grid", p_nom=100.0, efficiency=1.0)
    n.links["eh_role"] = ["grid_import", "grid_export"]
    n.add("Load", "crit", bus="site", p_set=pd.Series([8.0, 6.0], index=sns))
    n.add("Load", "it", bus="site", p_set=6.0)
    n.add("Generator", "g1", bus="site", p_nom=6.0)
    n.add("Generator", "g2", bus="site", p_nom=10.0)
    n.add("StorageUnit", "bess", bus="site", p_nom_extendable=True, max_hours=2.0,
          cyclic_state_of_charge=cyclic, state_of_charge_initial=soc_init)
    n.add("StorageUnit", "ups", bus="site", p_nom=4.0, max_hours=1.0,
          cyclic_state_of_charge=True)
    n.storage_units.at["bess", "p_nom_opt"] = 20.0
    n.storage_units.at["ups", "p_nom_opt"] = 4.0
    n.generators["p_nom_opt"] = n.generators["p_nom"]
    n.links_t.p0 = pd.DataFrame({"imp": [10.0, 0.0], "exp": [0.0, 3.0]}, index=sns)
    n.generators_t.p = pd.DataFrame({"g1": [2.0, 0.0], "g2": [0.0, 4.0]}, index=sns)
    n.generators_t.status = pd.DataFrame({"g1": list(g1_status), "g2": list(g2_status)},
                                         index=sns)
    n.storage_units_t.p = pd.DataFrame({"bess": [2.0, -2.0], "ups": [0.0, 0.0]}, index=sns)
    n.storage_units_t.state_of_charge = pd.DataFrame({"bess": [30.0, 32.0], "ups": [3.0, 3.0]},
                                                     index=sns)
    return n


def _row(df, unit, hour):
    return df[(df["unit_id"] == unit) & (df["hour"] == hour)].iloc[0]


def _hrow(df, scenario):
    return df[df["scenario"] == scenario].sort_values("hour")


def test_the_tables_carry_the_contract_columns():
    unit, hourly = island_hourly(_solved(), _sidecar())
    assert tuple(unit.columns) == ISLAND_UNIT_HOURLY_COLUMNS
    assert tuple(hourly.columns) == ISLAND_HOURLY_COLUMNS
    assert set(unit["period"]) == {2030} and set(unit["hour"]) == {0, 1}
    assert sorted(unit["unit_id"].unique()) == ["bess", "g1", "g2", "ups"]
    assert sorted(hourly["scenario"].unique()) == ["planned", "seamless", "ups_bridged"]


# ── island_hourly: the steps ───────────────────────────────────────────────


def test_the_import_step_takes_the_late_ups_relief():
    """p_imp = 10 − 0 and 0 − 3. The UPS walks in at 20 s ≥ 10 + 5 s, so it
    relieves min(L_it 6, p_nom 4) = 4: ΔP⁺ = 6 and −7."""
    _, hourly = island_hourly(_solved(), _sidecar())
    assert _hrow(hourly, "seamless")["dp_up_mw"].tolist() == pytest.approx([6.0, -7.0])


def test_the_export_step_at_0_plus_adds_every_transferring_ups():
    """ΔP⁻ = −p_imp + L_it = −10 + 6 and 3 + 6."""
    _, hourly = island_hourly(_solved(), _sidecar())
    assert _hrow(hourly, "seamless")["dp_down_mw"].tolist() == pytest.approx([-4.0, 9.0])


def test_a_walk_in_exactly_at_the_join_counts_as_late():
    """walk_in 15 s = start 10 + sync 5: late, so the relief stands."""
    island = _sidecar()
    island["units"]["ups"]["values"]["walk_in_s"] = 15.0
    _, hourly = island_hourly(_solved(), island)
    assert _hrow(hourly, "seamless")["dp_up_mw"].tolist() == pytest.approx([6.0, -7.0])


def test_the_import_is_counted_site_side():
    """Import efficiency 0.9: p_imp = 0.9·10 − 0 and 0 − 3, so ΔP⁺ = 5 and
    −7, ΔP⁻ = −3 and 9."""
    n = _solved()
    n.links.at["imp", "efficiency"] = 0.9
    _, hourly = island_hourly(n, _sidecar())
    assert _hrow(hourly, "seamless")["dp_up_mw"].tolist() == pytest.approx([5.0, -7.0])
    assert _hrow(hourly, "seamless")["dp_down_mw"].tolist() == pytest.approx([-3.0, 9.0])


def test_an_early_walk_in_gives_no_import_relief():
    island = _sidecar()
    island["units"]["ups"]["values"]["walk_in_s"] = 10.0
    _, hourly = island_hourly(_solved(), island)
    assert _hrow(hourly, "seamless")["dp_up_mw"].tolist() == pytest.approx([10.0, -3.0])
    assert _hrow(hourly, "seamless")["dp_down_mw"].tolist() == pytest.approx([-4.0, 9.0])


# ── inertia and droop gain ─────────────────────────────────────────────────


def test_inertia_at_0_plus_counts_the_committed_share():
    """g1 at u 0.5: 1.5·0.5·6/0.8 = 5.625; g2 at u 1 in hour 1: 1.5·10/0.8 =
    18.75; the droop BESS: 0.1·20/(2·0.04) = 25. M = 30.625 and 43.75."""
    _, hourly = island_hourly(_solved(), _sidecar())
    for sc in ("seamless", "planned"):
        assert _hrow(hourly, sc)["inertia_mws"].tolist() == pytest.approx([30.625, 43.75])


def test_ups_bridged_inertia_is_every_genset_started_at_n_plus_1():
    """1.875·(6 + 10) − 1.875·10 = 11.25 MW·s, whatever the gensets ran."""
    _, hourly = island_hourly(_solved(), _sidecar())
    assert _hrow(hourly, "ups_bridged")["inertia_mws"].tolist() == pytest.approx([11.25] * 2)


def test_without_uc_the_spinning_flag_sets_who_is_online():
    """UC off, g1 spinning: online 1 every hour (5.625·2 = 11.25 + 25);
    g2 cold adds nothing even where it runs."""
    unit, hourly = island_hourly(_solved(), _sidecar(uc=False, spinning=True))
    assert _hrow(hourly, "seamless")["inertia_mws"].tolist() == pytest.approx([36.25, 36.25])
    assert _row(unit, "g1", 1)["online"] == 1.0
    assert _row(unit, "g2", 1)["online"] == 0.0


def test_a_vsm_counts_its_inertia_on_apparent_power():
    """A VSM BESS, H_v 4 s at pf 0.8: 4/0.8·20 = 100 MW·s, plus the
    committed gensets' 5.625 and 18.75."""
    island = _sidecar()
    island["units"]["bess"] = validate_island_config({"units": {"bess": {
        **_VSM, "pf_rated": _a(0.8)}}, "requirements": _REQ})["units"]["bess"]
    _, hourly = island_hourly(_solved(), island)
    assert _hrow(hourly, "seamless")["inertia_mws"].tolist() == pytest.approx([105.625, 118.75])


def test_the_droop_gain_takes_out_the_largest_genset():
    """BESS 20/(0.04·50) = 10, g1 3, g2 5, less k·K_max = 5: ΣK = 13."""
    _, hourly = island_hourly(_solved(), _sidecar())
    assert hourly["k_mw_per_hz"].tolist() == pytest.approx([13.0] * 6)


def test_the_critical_load_is_the_tagged_loads():
    _, hourly = island_hourly(_solved(), _sidecar())
    assert _hrow(hourly, "seamless")["critical_load_mw"].tolist() == pytest.approx([8.0, 6.0])


# ── the unit table ──────────────────────────────────────────────────────────


def test_soc_start_is_the_soc_before_the_hour_cyclic():
    """Cyclic: e(t−1) at hour 0 is the last hour's 32 MWh."""
    unit, _ = island_hourly(_solved(), _sidecar())
    assert [_row(unit, "bess", h)["soc_start_mwh"] for h in (0, 1)] == pytest.approx([32.0, 30.0])
    assert [_row(unit, "ups", h)["soc_start_mwh"] for h in (0, 1)] == pytest.approx([3.0, 3.0])


def test_soc_start_is_the_initial_soc_when_not_cyclic():
    unit, _ = island_hourly(_solved(cyclic=False, soc_init=25.0), _sidecar())
    assert [_row(unit, "bess", h)["soc_start_mwh"] for h in (0, 1)] == pytest.approx([25.0, 30.0])


def test_bess_headroom_both_ways():
    """p_nom 20, p_s = 2 and −2: up 18 and 22, down 22 and 18."""
    unit, _ = island_hourly(_solved(), _sidecar())
    assert [_row(unit, "bess", h)["headroom_up_mw"] for h in (0, 1)] == pytest.approx([18.0, 22.0])
    assert [_row(unit, "bess", h)["headroom_down_mw"] for h in (0, 1)] == pytest.approx(
        [22.0, 18.0])
    assert _row(unit, "bess", 0)["online"] == 1.0


def test_committed_genset_headroom_and_online():
    """g1 u 0.5, p 2: up 0.5·6 − 2 = 1, down 2 − 0.3·0.5·6 = 1.1.
    g2 u 1, p 4 in hour 1: up 6, down 4 − 3 = 1; off in hour 0: 0 and 0."""
    unit, _ = island_hourly(_solved(), _sidecar())
    g1 = _row(unit, "g1", 0)
    assert (g1["online"], g1["headroom_up_mw"], g1["headroom_down_mw"]) == pytest.approx(
        (0.5, 1.0, 1.1))
    g2 = [_row(unit, "g2", h) for h in (0, 1)]
    assert [r["headroom_up_mw"] for r in g2] == pytest.approx([0.0, 6.0])
    assert [r["headroom_down_mw"] for r in g2] == pytest.approx([0.0, 1.0])
    assert math.isnan(g1["soc_start_mwh"]) and math.isnan(g1["p_ups_it_mw"])


def test_genset_headroom_is_scaled_by_its_availability():
    """g2 at p_max_pu 0.5 in hour 1 (u 1, p 4): up 0.5·10 − 4 = 1."""
    n = _solved()
    n.generators.at["g2", "p_max_pu"] = 0.5
    unit, _ = island_hourly(n, _sidecar())
    assert _row(unit, "g2", 1)["headroom_up_mw"] == pytest.approx(1.0)


def test_a_ups_row_carries_its_it_load_and_no_headroom():
    unit, _ = island_hourly(_solved(), _sidecar())
    ups = _row(unit, "ups", 0)
    assert ups["p_ups_it_mw"] == 6.0
    assert math.isnan(ups["headroom_up_mw"]) and math.isnan(ups["online"])


def test_unit_ids_are_mapped_by_pypsa_name():
    unit, _ = island_hourly(_solved(), _sidecar(), unit_ids={"bess": "B1", "g1": "G1"})
    assert sorted(unit["unit_id"].unique()) == ["B1", "G1", "g2", "ups"]


def test_an_unsolved_network_is_refused():
    from services.solver.island import IslandRefusal
    n = _solved()
    n.storage_units_t.state_of_charge = n.storage_units_t.state_of_charge.iloc[:, :0]
    n.generators_t.p = n.generators_t.p.iloc[:, :0]
    n.links_t.p0 = n.links_t.p0.iloc[:, :0]
    n.storage_units_t.p = n.storage_units_t.p.iloc[:, :0]
    with pytest.raises(IslandRefusal, match="solved"):
        island_hourly(n, _sidecar())


# ── two investment periods ─────────────────────────────────────────────────


def test_each_period_is_keyed_by_its_year_and_carries_soc_across():
    """Non-cyclic, carried across periods (PyPSA's default): 2040's first
    e(t−1) is 2030's last SoC; only 2030 starts from the initial SoC."""
    n = _solved(cyclic=False)
    n.set_snapshots(pd.MultiIndex.from_product([[2030, 2040], n.snapshots[:2]]))
    n.set_investment_periods([2030, 2040])
    sns = n.snapshots
    n.links_t.p0 = pd.DataFrame({"imp": [10.0, 0.0, 10.0, 0.0], "exp": [0.0, 3.0, 0.0, 3.0]},
                                index=sns)
    n.generators_t.p = pd.DataFrame({"g1": [2.0, 0.0] * 2, "g2": [0.0, 4.0] * 2}, index=sns)
    n.generators_t.status = pd.DataFrame({"g1": [0.5, 0.0] * 2, "g2": [0.0, 1.0] * 2}, index=sns)
    n.storage_units_t.p = pd.DataFrame({"bess": [2.0, -2.0] * 2, "ups": [0.0] * 4}, index=sns)
    n.storage_units_t.state_of_charge = pd.DataFrame(
        {"bess": [30.0, 32.0, 34.0, 36.0], "ups": [3.0] * 4}, index=sns)
    n.loads_t.p_set = pd.DataFrame({"crit": [8.0, 6.0] * 2}, index=sns)
    unit, hourly = island_hourly(n, _sidecar())
    bess = unit[unit["unit_id"] == "bess"].sort_values(["period", "hour"])
    assert bess["soc_start_mwh"].tolist() == pytest.approx([25.0, 30.0, 32.0, 34.0])
    assert sorted(hourly["period"].unique()) == [2030, 2040]


# ── from a real solve, through a saved file ────────────────────────────────


def test_the_tables_from_a_solve_survive_a_netcdf_round_trip(tmp_path):
    """The campus study reads the saved project: the tables from the network
    in memory and from the file it was saved to agree."""
    from tests.test_island_rows import _hub
    from tests.test_island_rows import _units as hub_units
    from tests.test_island_study import _solve_uc, _uc

    n = _hub(load=10.0, gensets=[("g", 6.0)])
    n.storage_units.at["bess", "p_nom_max"] = 7.0
    island = _uc(hub_units(gensets={"g": _genset(p_min_pu=_a(0.3))}))
    n = _solve_uc(n, island)
    before = island_hourly(n, island)
    path = tmp_path / "net.nc"
    n.export_to_netcdf(path)
    after = island_hourly(pypsa.Network(path), island)
    for a, b in zip(before, after):
        pd.testing.assert_frame_equal(a, b)
    unit, hourly = after
    assert _row(unit, "g", 0)["online"] == pytest.approx(0.5)
    assert _hrow(hourly, "seamless")["dp_up_mw"].tolist() == pytest.approx(
        list(10.0 - n.generators_t.p["g"]))
    assert np.isfinite(hourly["inertia_mws"]).all()


def test_the_data_center_template_gives_complete_tables():
    """The template, solved under its own sidecar (ups_bridged, N+1): one
    row per hour, every value finite, and the ups_bridged inertia is the
    rows' own: 1.875·(40 + 36.67) − 1.875·10 = 125 MW·s, which is the
    RoCoF block row's 2M = 250 at a 5 MW block and 1 Hz/s."""
    from gridspine.schema.island import validate_island_config
    from tests.test_island_rows import T, _solve

    island = validate_island_config(T.ISLAND_CONFIG["eh_datacenter"])
    n = _solve(T.build_eh_datacenter(), island)
    unit, hourly = island_hourly(n, island)
    assert len(hourly) == len(n.snapshots)
    assert set(hourly["scenario"]) == {"ups_bridged"}
    assert hourly["inertia_mws"].tolist() == pytest.approx([125.0] * len(n.snapshots), abs=1e-3)
    assert unit.groupby("unit_id").size().eq(len(n.snapshots)).all()

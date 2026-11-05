"""Heat as a modelled product: temperature and pressure levels (grid-to-process issue 15, design §2.6).

Acceptance first: the two-level demo solves and each level balances; a heat pump above its reach fails naming the
band, the device and its reach; gas serves while electricity is dearer and the electric Link takes a negative-price
hour; a network with no level behaves as today. Then the gatekeeper's conditions (GK A plan GO, 2026-11-05).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.process_heat import (DEMO_LEDGER, HeatDemand, HeatLevelError, add_heat_demand, add_hybrid_supply,
                                   build_two_level_heat_demo, heat_band, level_label, steam_class)
from services.validation_service import _check_heat_levels

NEW_CODES = ("heat_level_above_reach", "steam_level_above_reach", "level_without_supplier")
LEVEL_COLUMNS = {"buses": ("temperature_c", "pressure_bar"), "links": ("max_supply_temp_c", "max_pressure_bar")}
NEGATIVE_HOUR = 12


def _solve(n):
    status, cond = n.optimize(solver_name="highs", solver_options={"output_flag": False})
    assert (status, cond) == ("ok", "optimal")
    return n


def _codes(n):
    return [i.code for i in _check_heat_levels(n)]


# ── Acceptance ───────────────────────────────────────────────────────────────

def test_the_two_level_demo_solves_and_each_level_balances():
    n = _solve(build_two_level_heat_demo(24))
    assert _codes(n) == []
    for bus, (gas, electric) in {"heat low": ("gas boiler", "heat pump"),
                                 "heat high": ("gas furnace", "electric heater")}.items():
        delivered = n.links_t.p1[[gas, electric]].sum(axis=1)   # p1 < 0: delivered to bus1
        demand = n.loads_t.p_set[f"{bus} demand"]
        np.testing.assert_allclose(-delivered.to_numpy(), demand.to_numpy(), atol=1e-6)
        assert demand.gt(0).all()


def test_gas_serves_while_electricity_is_dearer_and_electric_takes_a_negative_price_hour():
    price = [60.0] * 24
    price[NEGATIVE_HOUR] = -20.0
    n = _solve(build_two_level_heat_demo(24, electricity_price=price))
    p0 = n.links_t.p0
    dear = [t for t in range(24) if t != NEGATIVE_HOUR]
    # High band: the heater costs 60 / 0.99 per MWh_th, the furnace 40 / 0.9: gas serves.
    assert (p0["gas furnace"].iloc[dear] > 1.0).all()
    np.testing.assert_allclose(p0["electric heater"].iloc[dear], 0.0, atol=1e-6)
    # The negative-price hour: the reverse.
    assert p0["electric heater"].iloc[NEGATIVE_HOUR] > 1.0
    assert p0["gas furnace"].iloc[NEGATIVE_HOUR] == pytest.approx(0.0, abs=1e-6)


def test_a_heat_pump_above_its_reach_fails_naming_band_device_and_reach():
    n = build_two_level_heat_demo(4)
    n.links.loc["heat pump", "bus1"] = "heat high"
    issues = _check_heat_levels(n)
    assert [(i.code, i.severity, i.component_class, i.name) for i in issues] == [
        ("heat_level_above_reach", "error", "Link", "heat pump")]
    msg = issues[0].message
    assert "high band" in msg and "heat pump" in msg and "120 °C" in msg and "450 °C" in msg
    assert "'" not in msg and "—" not in msg   # UX: no repr quotes, no em dash


def test_a_network_with_no_level_gets_none_of_the_new_issues():
    from services.solver_service import SolverConfig
    from services.validation_service import validate_for_run

    n = build_two_level_heat_demo(4)
    for frame, cols in LEVEL_COLUMNS.items():
        getattr(n, frame).drop(columns=list(cols), inplace=True, errors="ignore")
    assert _codes(n) == []
    codes = {i.code for i in validate_for_run(n, SolverConfig())}
    assert not codes & set(NEW_CODES)


# ── Reach rule (GK conditions 2 and 10) ──────────────────────────────────────

def test_a_steam_device_above_its_rating_fails():
    n = build_two_level_heat_demo(4)
    add_heat_demand(n, [HeatDemand("steam hp", pressure_bar=40.0, peak_mw=2.0)])
    add_hybrid_supply(n, "steam hp", gas_bus="gas", elec_bus="grid",
                      gas={"name": "gas boiler 40", "p_nom": 5.0, "max_pressure_bar": 45.0},
                      electric={"name": "e-boiler 10", "p_nom": 5.0, "max_pressure_bar": 10.0})
    issues = _check_heat_levels(n)
    assert [(i.code, i.name) for i in issues] == [("steam_level_above_reach", "e-boiler 10")]
    assert "HP steam" in issues[0].message and "10 bar(g)" in issues[0].message


def test_a_reach_equal_to_the_level_passes():
    """Mutant: `>=` at the reach."""
    n = build_two_level_heat_demo(4)
    n.links.loc["heat pump", "max_supply_temp_c"] = 90.0
    assert _codes(n) == []


def test_a_negative_efficiency_source_port_on_a_hotter_bus_is_not_checked():
    """Mutant: drop the efficiencyN > 0 filter. The heat pump draws from bus2; a hot source is no output."""
    n = build_two_level_heat_demo(4)
    n.links.loc["heat pump", "bus2"] = "heat high"
    assert "heat_level_above_reach" not in _codes(n)


def test_a_port_with_a_time_varying_positive_efficiency_is_an_output():
    n = build_two_level_heat_demo(4)
    n.links.loc["heat pump", "bus2"] = "heat high"
    n.links_t.efficiency2["heat pump"] = pd.Series([-2.0, -2.0, 0.5, -2.0], index=n.snapshots)
    assert _codes(n) == ["heat_level_above_reach"]


@pytest.mark.parametrize("unset", ["level", "reach"])
def test_either_side_unset_skips_the_rule(unset):
    """Mutant: skip only when both are unset."""
    n = build_two_level_heat_demo(4)
    n.links.loc["heat pump", "bus1"] = "heat high"
    if unset == "level":
        n.buses.loc["heat high", "temperature_c"] = math.nan
    else:
        n.links.loc["heat pump", "max_supply_temp_c"] = math.nan
    assert "heat_level_above_reach" not in _codes(n)


# ── Supplier rule (GK condition 3) ───────────────────────────────────────────

def test_a_level_with_demand_and_no_supplier_fails():
    n = build_two_level_heat_demo(4)
    add_heat_demand(n, [HeatDemand("heat mid", temperature_c=200.0, peak_mw=1.0)])
    issues = _check_heat_levels(n)
    assert [(i.code, i.component_class, i.name) for i in issues] == [("level_without_supplier", "Bus", "heat mid")]
    assert "medium band" in issues[0].message


@pytest.mark.parametrize("cut", ["p_nom", "p_max_pu", "p_max_pu_series", "efficiency"])
def test_a_link_that_cannot_deliver_is_no_supplier(cut):
    """Mutant: count a zero-capacity Link as a supplier."""
    n = build_two_level_heat_demo(4)
    add_heat_demand(n, [HeatDemand("heat mid", temperature_c=200.0, peak_mw=1.0)])
    n.add("Link", "mid boiler", bus0="gas", bus1="heat mid", p_nom=2.0, efficiency=0.9)
    assert _codes(n) == []
    if cut == "p_nom":
        n.links.loc["mid boiler", "p_nom"] = 0.0
    elif cut == "p_max_pu":
        n.links.loc["mid boiler", "p_max_pu"] = 0.0
    elif cut == "p_max_pu_series":
        n.links_t.p_max_pu["mid boiler"] = pd.Series(0.0, index=n.snapshots)
    else:
        n.links.loc["mid boiler", "efficiency"] = 0.0
    assert _codes(n) == ["level_without_supplier"]


def test_an_extendable_link_and_a_generator_count_and_a_store_does_not():
    n = build_two_level_heat_demo(4)
    add_heat_demand(n, [HeatDemand("heat mid", temperature_c=200.0, peak_mw=1.0)])
    n.add("Store", "mid tank", bus="heat mid", e_nom=100.0, e_initial=100.0)
    assert _codes(n) == ["level_without_supplier"]
    n.add("Link", "mid boiler", bus0="gas", bus1="heat mid", p_nom=0.0, p_nom_extendable=True)
    assert _codes(n) == []
    n.remove("Link", "mid boiler")
    n.add("Generator", "mid gen", bus="heat mid", p_nom=0.0)
    assert _codes(n) == ["level_without_supplier"]
    n.generators.loc["mid gen", "p_nom"] = 3.0
    assert _codes(n) == []


# ── Band labels: derived, never stored (GK conditions 7 and 10) ──────────────

def test_band_labels_are_derived_from_demo_edges_and_never_stored():
    """Mutant: store a band label on the bus."""
    assert [heat_band(t) for t in (20, 99.9, 100, 399, 400, 1200)] == ["low", "low", "medium", "medium", "high", "high"]
    assert [steam_class(p) for p in (3, 5, 19, 20, 60)] == ["LP", "MP", "MP", "HP", "HP"]
    n = build_two_level_heat_demo(4)
    assert level_label(n, "heat high") == "high band (demo edges) at 450 °C"
    assert level_label(n, "grid") is None
    stored = n.buses.drop(columns=["temperature_c"]).astype(str).to_numpy().ravel()
    assert not {"low", "medium", "high", "LP", "MP", "HP"} & set(stored)
    assert all(row["label"] == "demo" and row["source"] == "synthetic" for row in DEMO_LEDGER)


# ── Demand input (GK condition 9) ────────────────────────────────────────────

def _bare(hours=6):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-03-02", periods=hours, freq="h"))
    return n


def test_a_flat_demand_is_flat_while_running_and_scales_by_peak_or_annual():
    n = _bare()
    add_heat_demand(n, [HeatDemand("h", temperature_c=150.0, peak_mw=4.0),
                        HeatDemand("s", pressure_bar=12.0, annual_mwh=8760.0)])
    np.testing.assert_allclose(n.loads_t.p_set["h demand"], 4.0)
    np.testing.assert_allclose(n.loads_t.p_set["s demand"], 1.0)
    assert n.buses.at["h", "carrier"] == "heat" and n.buses.at["s", "carrier"] == "steam"
    assert n.buses.at["h", "temperature_c"] == 150.0 and n.buses.at["s", "pressure_bar"] == 12.0


def test_a_flat_demand_is_off_in_a_shutdown_week():
    n = _bare()  # 2 March 2030 is in ISO week 9
    add_heat_demand(n, [HeatDemand("h", temperature_c=150.0, peak_mw=4.0, shutdown_weeks=(9,))])
    np.testing.assert_allclose(n.loads_t.p_set["h demand"], 0.0)


def test_a_given_series_on_the_snapshots_is_used_as_is():
    n = _bare()
    s = pd.Series([1.0, 2.0, 3.0, 0.0, 2.0, 1.0], index=n.snapshots)
    add_heat_demand(n, [HeatDemand("h", temperature_c=-10.0, shape=s)])  # a negative temperature is a level too
    np.testing.assert_allclose(n.loads_t.p_set["h demand"], s.to_numpy())


def test_a_misaligned_series_is_refused_never_reindexed():
    n = _bare()
    s = pd.Series(1.0, index=n.snapshots + pd.Timedelta(hours=1))
    with pytest.raises(HeatLevelError, match="never reindexed"):
        add_heat_demand(n, [HeatDemand("h", temperature_c=150.0, shape=s)])
    assert "h" not in n.buses.index


def test_a_series_in_another_time_zone_is_refused():
    n = _bare()
    s = pd.Series(1.0, index=n.snapshots.tz_localize("Europe/Berlin"))
    with pytest.raises(HeatLevelError, match="time zone"):
        add_heat_demand(n, [HeatDemand("h", temperature_c=150.0, shape=s)])


@pytest.mark.parametrize("row", [HeatDemand("h", peak_mw=1.0),
                                 HeatDemand("h", temperature_c=90.0, pressure_bar=3.0, peak_mw=1.0),
                                 HeatDemand("h", temperature_c=90.0),
                                 HeatDemand("h", pressure_bar=-1.0, peak_mw=1.0)])
def test_a_demand_row_must_state_one_level_and_one_scale(row):
    with pytest.raises(HeatLevelError):
        add_heat_demand(_bare(), [row])


# ── Fingerprint and lazy columns (GK conditions 4 and 5) ─────────────────────

def _fingerprints(n):
    from types import SimpleNamespace

    from services import workflow_tools as wt
    from services.results.finance_case import owner_assets_digest

    ctx = SimpleNamespace(network=n, solver_state={"solver_config": {}}, user_ts={})
    commercial = {"value_flows": {"asset_owners": [{"component": "Link", "asset_id": "heat pump", "owner": "c"},
                                                   {"component": "Bus", "asset_id": "heat low", "owner": "c"}]}}
    return (wt._network_input_fingerprint(ctx), wt._network_input_fingerprint(ctx, exact=True),
            owner_assets_digest(n, commercial))


def test_the_level_columns_leave_the_input_fingerprint_and_case_digest_unchanged():
    bare = build_two_level_heat_demo(4)
    for frame, cols in LEVEL_COLUMNS.items():
        getattr(bare, frame).drop(columns=list(cols), inplace=True, errors="ignore")
    with_levels = build_two_level_heat_demo(4)
    with_levels.buses.loc["heat high", "pressure_bar"] = 3.0
    with_levels.links.loc["gas furnace", "max_pressure_bar"] = 9.0
    assert _fingerprints(bare) == _fingerprints(with_levels)


def _round_trip(n, tmp_path):
    from services.pypsa_service import PyPSAService

    path = tmp_path / "n.nc"
    PyPSAService.export_network_to_netcdf(n, path)
    m = pypsa.Network()
    PyPSAService.import_network_from_netcdf(m, path)
    return m


def test_a_network_without_levels_gains_no_column_on_a_round_trip(tmp_path):
    n = build_two_level_heat_demo(4)
    for frame, cols in LEVEL_COLUMNS.items():
        getattr(n, frame).drop(columns=list(cols), inplace=True, errors="ignore")
    before = {f: getattr(n, f).copy() for f in ("buses", "links", "loads")}
    m = _round_trip(n, tmp_path)
    for f, df in before.items():
        assert not set(getattr(m, f).columns) & {c for cs in LEVEL_COLUMNS.values() for c in cs}
        pd.testing.assert_frame_equal(getattr(m, f)[df.columns], df, check_names=False)


def test_the_demo_round_trips_with_its_levels(tmp_path):
    n = build_two_level_heat_demo(4)
    m = _round_trip(n, tmp_path)
    for f in ("buses", "links", "loads"):
        df = getattr(n, f)
        pd.testing.assert_frame_equal(getattr(m, f)[df.columns], df, check_names=False)
    assert "pressure_bar" not in m.buses.columns and "max_pressure_bar" not in m.links.columns
    assert _codes(m) == []


# ── API, schema and registries (GK conditions 1 and 6) ───────────────────────

def _api_net():
    n = pypsa.Network()
    n.add("Bus", "grid", carrier="AC")
    return n


def test_a_bus_level_round_trips_through_the_api_and_stays_lazy(client, install_network):
    n = _api_net()
    install_network(n)
    r = client.post("/api/network/buses", json={"name": "plain", "carrier": "heat"})
    assert r.status_code in (200, 201), r.text
    assert "temperature_c" not in n.buses.columns and "pressure_bar" not in n.buses.columns
    r = client.post("/api/network/buses", json={"name": "cold", "carrier": "heat", "temperature_c": -5.0})
    assert r.status_code in (200, 201), r.text
    r = client.post("/api/network/buses", json={"name": "lp", "carrier": "steam", "pressure_bar": 3.5})
    assert r.status_code in (200, 201), r.text
    rows = {b["name"]: b for b in client.get("/api/network/buses").json()}
    assert rows["cold"]["temperature_c"] == -5.0 and rows["lp"]["pressure_bar"] == 3.5


def test_a_link_reach_round_trips_and_a_negative_pressure_is_refused(client, install_network):
    n = _api_net()
    n.add("Bus", "hot", carrier="heat")
    install_network(n)
    link = {"name": "hp", "bus0": "grid", "bus1": "hot", "p_nom": 1.0, "efficiency": 3.0}
    r = client.post("/api/network/links", json=link)
    assert r.status_code in (200, 201), r.text
    assert "max_supply_temp_c" not in n.links.columns
    r = client.post("/api/network/links", json={**link, "name": "hp2", "max_supply_temp_c": 120.0})
    assert r.status_code in (200, 201), r.text
    assert n.links.at["hp2", "max_supply_temp_c"] == 120.0
    r = client.post("/api/network/links", json={**link, "name": "hp3", "max_pressure_bar": -1.0})
    assert r.status_code >= 400


def test_the_schema_rows_carry_units_and_clear_when_emptied():
    from services.asset_schema import fields as F

    bus = {f.key: f for f in F.fields_for("Bus")}
    link = {f.key: f for f in F.fields_for("Link")}
    assert (bus["temperature_c"].label, bus["temperature_c"].unit_display) == ("Supply temperature", "°C")
    assert (bus["pressure_bar"].label, bus["pressure_bar"].unit_display) == ("Header pressure", "bar(g)")
    assert (link["max_supply_temp_c"].label, link["max_supply_temp_c"].unit_display) == ("Max supply temperature", "°C")
    assert (link["max_pressure_bar"].label, link["max_pressure_bar"].unit_display) == ("Max steam pressure", "bar(g)")
    assert {bus[k].blank for k in ("temperature_c", "pressure_bar")} == {"clear"}
    assert {link[k].blank for k in ("max_supply_temp_c", "max_pressure_bar")} == {"clear"}


def test_the_steam_carrier_is_in_the_catalogue():
    from services.carrier_catalog import CARRIER_CATALOG

    assert CARRIER_CATALOG["steam"]["co2_emissions"] == 0.0 and "unit" not in CARRIER_CATALOG["steam"]


@pytest.mark.parametrize("code", NEW_CODES)
def test_each_new_code_has_a_title_and_a_section(code):
    from services.flow_sections import CODE_SECTION
    from services.issue_titles import ISSUE_TITLES

    assert ISSUE_TITLES[code] and CODE_SECTION[code] == "assets"

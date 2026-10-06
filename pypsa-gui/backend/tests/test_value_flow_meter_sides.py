"""
Which side of the commercial meter each bus and asset is on (Edge Investment
Case P3 WP3.1, plan review D1 and WP3.1 review #5).

Plan: docs/superpowers/plans/2026-09-29-edge-investment-case-p3.md WP3.1
(meter-side classifier, "tested on its own"). Two searches over every branch
except the meter Links — from the meter's site buses and from its grid buses; a
bus both reach is behind a meter bypass (flagged, on the nearer side).
"""
from __future__ import annotations

from models.commercial import CommercialConfig
from services.commercial import participants as P
from tests.fixtures.investment_case.edge_15min import build_edge_15min


def _cfg(**extra):
    return CommercialConfig.model_validate({"poc_link": "import", **extra})


def _side(n, cfg, comp, name):
    return P.asset_side(n, comp, name, P.classify_buses(n, cfg))


def test_the_edge_site():
    n = build_edge_15min()
    sides = P.classify_buses(n, _cfg())
    assert sides.site == {"poc", "site"} and sides.grid == {"grid"} and not sides.bypass
    assert P.asset_side(n, "Generator", "grid_supply", sides) == ("grid", [])
    assert P.asset_side(n, "Generator", "pv", sides) == ("site", [])
    assert P.asset_side(n, "StorageUnit", "bess", sides) == ("site", [])
    assert P.asset_side(n, "Link", "import", sides) == ("site", [])      # the meter Link
    assert P.asset_side(n, "Link", "poc_site", sides) == ("site", [])


def test_a_poc_inside_a_larger_grid():
    n = build_edge_15min()
    n.add("Bus", "hv", carrier="AC")
    n.add("Line", "tie", bus0="grid", bus1="hv", x=0.1, s_nom=100.0)
    n.add("Generator", "far_gen", bus="hv", p_nom=10.0)
    n.add("Store", "far_store", bus="hv", e_nom=5.0)
    cfg = _cfg()
    assert _side(n, cfg, "Generator", "far_gen") == ("grid", [])
    assert _side(n, cfg, "Store", "far_store") == ("grid", [])
    assert _side(n, cfg, "Line", "tie") == ("grid", [])


def test_a_bypass_through_an_intermediate_grid_bus():
    """grid – Line – hv – Line – site: hv is nearer the grid, and flagged."""
    n = build_edge_15min()
    n.add("Bus", "hv", carrier="AC")
    n.add("Line", "g_hv", bus0="grid", bus1="hv", x=0.1, s_nom=10.0)
    n.add("Line", "hv_site", bus0="hv", bus1="site", x=0.1, s_nom=10.0)
    n.add("Generator", "hv_gen", bus="hv", p_nom=5.0)
    cfg = _cfg()
    sides = P.classify_buses(n, cfg)
    assert {"hv", "grid", "site", "poc"} <= sides.bypass
    assert P.asset_side(n, "Generator", "hv_gen", sides) == ("grid", ["meter_bypass"])
    assert P.asset_side(n, "Generator", "pv", sides) == ("site", ["meter_bypass"])
    side, flags = P.asset_side(n, "Line", "hv_site", sides)
    assert side == "site" and "meter_bypass" in flags                  # it crosses the meter


def test_a_fuel_bus_behind_a_chp_link_is_site_side():
    n = build_edge_15min()
    n.add("Bus", "gas", carrier="gas")
    n.add("Generator", "gas_supply", bus="gas", p_nom=50.0, marginal_cost=30.0)
    n.add("Link", "chp", bus0="gas", bus1="site", p_nom=10.0, efficiency=0.4)
    cfg = _cfg()
    assert _side(n, cfg, "Generator", "gas_supply") == ("site", [])
    assert _side(n, cfg, "Link", "chp") == ("site", [])


def test_an_export_link_to_another_grid_bus():
    n = build_edge_15min()
    n.add("Bus", "grid2", carrier="AC")
    n.add("Link", "export", bus0="poc", bus1="grid2", p_nom=10.0)
    n.add("Generator", "grid2_sink", bus="grid2", p_nom=10.0, p_min_pu=-1.0)
    cfg = _cfg(export_link="export")
    sides = P.classify_buses(n, cfg)
    assert "grid2" in sides.grid
    assert P.asset_side(n, "Link", "export", sides) == ("site", [])    # a meter Link
    assert P.asset_side(n, "Generator", "grid2_sink", sides) == ("grid", [])


def test_an_island_is_unclassified():
    n = build_edge_15min()
    n.add("Bus", "island", carrier="AC")
    n.add("Generator", "island_gen", bus="island", p_nom=1.0)
    assert _side(n, _cfg(), "Generator", "island_gen") == ("unclassified", [])


def test_a_site_link_with_an_output_on_the_grid_crosses_the_meter():
    n = build_edge_15min()
    n.add("Bus", "h2", carrier="H2")
    n.add("Link", "multi", bus0="site", bus1="h2", bus2="grid", p_nom=1.0)
    side, flags = _side(n, _cfg(), "Link", "multi")
    assert side == "site" and "meter_bypass" in flags


def test_a_group_connection_starts_from_every_member():
    n = build_edge_15min()
    n.add("Bus", "poc2", carrier="AC")
    n.add("Link", "import2", bus0="grid", bus1="poc2", p_nom=10.0)
    n.add("Generator", "pv2", bus="poc2", p_nom=3.0)
    cfg = _cfg(group_contract="g", group_members=["import", "import2"], group_cap_mw=50.0)
    sides = P.classify_buses(n, cfg)
    assert {"poc", "site", "poc2"} <= sides.site
    assert P.asset_side(n, "Link", "import2", sides) == ("site", [])
    assert P.asset_side(n, "Generator", "pv2", sides) == ("site", [])


# ── fuel supply (WP3.1 review round 2 #3) ──────────────────────────────────


def test_pv_on_a_low_voltage_or_dc_bus_is_not_a_fuel_supply():
    from services.results.value_flows import is_fuel_supply

    n = build_edge_15min()
    n.buses.loc["site", "carrier"] = "low voltage"               # PyPSA-Eur convention
    assert not is_fuel_supply(n, _cfg(), "pv")
    n.add("Bus", "dc", carrier="DC")
    n.add("Generator", "pv_dc", bus="dc", p_nom=2.0)
    n.add("Link", "inverter", bus0="dc", bus1="site", p_nom=2.0)
    assert not is_fuel_supply(n, _cfg(), "pv_dc")


def test_gas_behind_a_chp_link_is_a_fuel_supply():
    from services.results.value_flows import is_fuel_supply

    n = build_edge_15min()
    n.add("Bus", "gas", carrier="gas")
    n.add("Generator", "gas_supply", bus="gas", p_nom=50.0)
    n.add("Link", "chp", bus0="gas", bus1="site", p_nom=10.0, efficiency=0.4)
    assert is_fuel_supply(n, _cfg(), "gas_supply")
    n.add("Load", "gas_load", bus="gas", p_set=1.0)               # gas is also consumed
    assert not is_fuel_supply(n, _cfg(), "gas_supply")


def test_a_heat_bus_fed_by_a_link_is_not_a_fuel_supply():
    from services.results.value_flows import is_fuel_supply

    n = build_edge_15min()
    n.add("Bus", "heat", carrier="heat")
    n.add("Generator", "boiler", bus="heat", p_nom=5.0)
    n.add("Link", "hp", bus0="site", bus1="heat", p_nom=2.0)        # delivers into heat
    assert not is_fuel_supply(n, _cfg(), "boiler")

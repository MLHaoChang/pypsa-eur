"""
Grid-to-process ticket 02: the process unit in the asset schema, carriers and API.

A process unit is a Link with `eh_process` set (design §2.1): bus0 the main input, bus1 the product, bus2..bus4
other inputs. It may be committable, so the Link shares the Generator's unit-commitment rows. No solve change.
"""
from __future__ import annotations

import math

import pypsa
import pytest

from services.asset_schema import fields as F

# ── schema ───────────────────────────────────────────────────────────────────


def _link() -> dict[str, F.FieldSpec]:
    return {f.key: f for f in F.fields_for("Link")}


def test_link_declares_the_extra_ports():
    link = _link()
    for k in ("bus2", "bus3", "bus4"):
        assert link[k].kind == "bus" and not link[k].required
        assert link[k].blank == "empty"          # an emptied port saves "" (no port)
    for k in ("efficiency2", "efficiency3", "efficiency4"):
        assert k in link


@pytest.mark.parametrize("key", ["min_up_time", "min_down_time", "ramp_limit_up", "ramp_limit_down",
                                 "stand_by_cost", "start_up_cost"])
def test_link_uc_fields_show_only_when_committable(key):
    assert _link()[key].reveal_when == {"committable": True}
    assert "committable" in _link() and _link()["committable"].reveal_when is None


def test_link_start_up_cost_is_read_only_with_a_note_to_ticket_03():
    spec = _link()["start_up_cost"]
    assert spec.derived
    assert "ticket 03" in spec.help


def test_generator_and_link_share_the_uc_helper():
    gen = {f.key: f for f in F.fields_for("Generator")}
    link = _link()
    for k in ("committable", "min_up_time", "min_down_time", "ramp_limit_up", "ramp_limit_down"):
        assert gen[k].help == link[k].help and gen[k].reveal_when == link[k].reveal_when


def test_process_columns_on_the_link():
    link = _link()
    assert link["eh_process"].kind == "bool"
    assert link["criticality_tier"].options == ("safety", "process_critical", "process_flexible", "deferrable")
    assert link["criticality_tier"].reveal_when == {"eh_process": True}
    assert link["throughput_unit"].derived and link["specific_energy_kwh_per_t"].derived


def test_bus_carries_unit_and_tonne_factor():
    bus = {f.key: f for f in F.fields_for("Bus")}
    assert bus["unit"].kind in ("text", "enum")
    assert bus["mwh_per_t"].reveal_when == {"unit": "MWh"}


# ── carriers ─────────────────────────────────────────────────────────────────


def test_product_carriers_have_units():
    from services.carrier_catalog import CARRIER_CATALOG, PRODUCT_MWH_PER_T
    assert CARRIER_CATALOG["NH3"]["unit"] == "MWh"                 # energy product, LHV (design D2)
    for c in ("N2", "ore", "concentrate"):
        assert CARRIER_CATALOG[c]["unit"] == "t"                   # material products
    assert PRODUCT_MWH_PER_T["NH3"] == 5.166
    assert "heat" in CARRIER_CATALOG                                # the form's heat-bus filter has a carrier


def test_ensure_carrier_adds_a_product_with_its_unit_and_no_nan_neighbours():
    from services.carrier_catalog import ensure_carrier
    n = pypsa.Network()
    ensure_carrier(n, "AC")
    ensure_carrier(n, "ore")
    assert n.carriers.at["ore", "unit"] == "t"
    assert n.carriers.at["AC", "unit"] == ""


def test_existing_catalog_entries_are_unchanged():
    from services.carrier_catalog import CARRIER_CATALOG
    for name in ("AC", "H2", "gas", "battery", "solar"):
        assert "unit" not in CARRIER_CATALOG[name]


# ── bus unit and tonne factor ────────────────────────────────────────────────


def test_bus_tonne_factor():
    from services.carrier_catalog import bus_tonne_factor
    n = pypsa.Network()
    n.add("Bus", "ore", carrier="ore", unit="t")
    n.add("Bus", "nh3", carrier="NH3", unit="MWh")
    n.add("Bus", "nh3_bare", carrier="NH3", unit="MWh")
    n.add("Bus", "ac", carrier="AC")
    n.buses["mwh_per_t"] = math.nan
    n.buses.at["nh3", "mwh_per_t"] = 5.166
    assert bus_tonne_factor(n, "ore") == 1.0
    assert bus_tonne_factor(n, "nh3") == pytest.approx(1 / 5.166)
    # An energy product with no factor, and a bus that is no product, are Unavailable (ADR-0001), never 0.
    assert bus_tonne_factor(n, "nh3_bare") is None
    assert bus_tonne_factor(n, "ac") is None


# ── API: round trip ──────────────────────────────────────────────────────────

PROCESS_UNIT = {
    "name": "haber_bosch", "bus0": "hub", "bus1": "nh3", "bus2": "h2", "carrier": "NH3",
    "p_nom": 7.0, "efficiency": 0.8, "efficiency2": -4.6, "p_min_pu": 0.4,
    "committable": True, "min_up_time": 24, "min_down_time": 8, "ramp_limit_up": 0.2, "ramp_limit_down": 0.2,
    "stand_by_cost": 3.5,
    "eh_process": True, "criticality_tier": "process_critical", "throughput_unit": "t/h",
    "specific_energy_kwh_per_t": 1280.0,
}


def _net() -> pypsa.Network:
    n = pypsa.Network()
    n.add("Bus", "hub", carrier="AC")
    n.add("Bus", "nh3", carrier="NH3", unit="MWh")
    n.add("Bus", "h2", carrier="H2")
    return n


def _row(client, name):
    rows = client.get("/api/network/links").json()
    return next(r for r in rows if r["name"] == name)


def test_a_multi_port_committable_link_round_trips(client, install_network):
    n = _net()
    install_network(n)
    r = client.post("/api/network/links", json=PROCESS_UNIT)
    assert r.status_code in (200, 201), r.text
    first = _row(client, "haber_bosch")
    for k, v in PROCESS_UNIT.items():
        assert first[k] == v, (k, first[k], v)

    # Asset write: the full current row spread under the patch, then PUT.
    patched = {**first, "min_down_time": 6}
    r = client.put("/api/network/links/haber_bosch", json=patched)
    assert r.status_code == 200, r.text
    second = _row(client, "haber_bosch")
    assert second["min_down_time"] == 6
    for k, v in PROCESS_UNIT.items():
        if k != "min_down_time":
            assert second[k] == v, (k, second[k], v)
    # …and it survives a reload of the live network's frame.
    assert bool(n.links.at["haber_bosch", "committable"]) is True
    assert n.links.at["haber_bosch", "bus2"] == "h2"
    assert bool(n.links.at["haber_bosch", "eh_process"]) is True


def test_a_tier_outside_the_four_is_refused(client, install_network):
    install_network(_net())
    r = client.post("/api/network/links", json={**PROCESS_UNIT, "criticality_tier": "urgent"})
    assert r.status_code == 422


# ── regression: CHP and existing projects unchanged ──────────────────────────

CHP = {
    "name": "CHP 1", "bus0": "gas", "bus1": "elec", "bus2": "heat", "carrier": "gas",
    "p_nom": 100.0, "efficiency": 0.4, "efficiency2": 0.4, "p_nom_extendable": False,
    "capital_cost": 0.0, "marginal_cost": 0.0,
}


def test_a_chp_creates_no_process_columns(client, install_network):
    n = pypsa.Network()
    n.add("Bus", "gas", carrier="gas")
    n.add("Bus", "elec", carrier="AC")
    n.add("Bus", "heat", carrier="heat")
    install_network(n)
    before_links = set(n.links.columns)
    before_buses = set(n.buses.columns)
    r = client.post("/api/network/links", json=CHP)
    assert r.status_code in (200, 201), r.text
    for col in ("eh_process", "criticality_tier", "throughput_unit", "specific_energy_kwh_per_t"):
        assert col not in n.links.columns, col
    assert "mwh_per_t" not in n.buses.columns
    assert set(n.buses.columns) == before_buses
    assert not bool(n.links.at["CHP 1", "committable"])
    assert n.links.at["CHP 1", "bus2"] == "heat" and float(n.links.at["CHP 1", "efficiency2"]) == 0.4
    assert not (set(n.links.columns) - before_links) & {"min_up_time_x"}   # no stray columns beyond the ports


def test_generator_rows_are_unchanged_by_the_shared_helper():
    gen = {f.key: f for f in F.fields_for("Generator")}
    assert gen["start_up_cost"].help == "Cost of one start-up." and not gen["start_up_cost"].derived
    assert gen["shut_down_cost"].reveal_when == {"committable": True}
    assert "stand_by_cost" not in gen

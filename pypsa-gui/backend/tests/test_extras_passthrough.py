"""
Catalog-whitelisted passthrough for attributes beyond each Create model's
declared fields (spec D21).

Pydantic v2 defaults to extra='ignore' and there are no Update models, so
before this change a newly-exposed attribute returned 200 and never persisted.
The two halves — extra='allow' and the whitelist at the two generic CRUD
helpers — must ship together: the first alone lets an arbitrary key reach
n.add().
"""
from __future__ import annotations

import pypsa

from tests.conftest import build_network

# GeneratorCreate declares required fields, so a realistic PUT carries the whole
# object — which is what the frontend sends (it spreads the cached row). A
# one-key body would 422 on validation before reaching the whitelist at all.
BASE = {"name": "gas", "bus": "B1", "carrier": "gas", "p_nom": 200.0}


def test_a_catalog_input_attribute_persists_through_put(client, install_network):
    n = build_network()
    install_network(n)
    # `weight` is a real Generator Input attribute that GeneratorCreate does
    # not declare — exactly the case D21 exists for.
    r = client.put("/api/network/generators/gas", json={**BASE, "weight": 3.0})
    assert r.status_code == 200
    assert float(n.generators.at["gas", "weight"]) == 3.0


def test_a_non_catalog_key_is_dropped_not_persisted(client, install_network):
    n = build_network()
    install_network(n)
    r = client.put("/api/network/generators/gas", json={**BASE, "not_a_pypsa_attribute": 5})
    assert r.status_code == 200
    assert "not_a_pypsa_attribute" not in n.generators.columns


def test_a_declared_field_still_persists(client, install_network):
    # The whitelist must not narrow existing behaviour for declared fields.
    n = build_network()
    install_network(n)
    r = client.put("/api/network/generators/gas", json={**BASE, "p_nom": 250.0})
    assert r.status_code == 200
    assert float(n.generators.at["gas", "p_nom"]) == 250.0


def test_a_catalog_input_attribute_persists_through_post(client, install_network):
    n = build_network()
    install_network(n)
    r = client.post("/api/network/generators", json={
        "name": "new_gen", "bus": "B1", "carrier": "gas", "p_nom": 10.0,
        "weight": 4.0,
    })
    assert r.status_code in (200, 201)
    assert float(n.generators.at["new_gen", "weight"]) == 4.0


def test_a_non_catalog_key_is_dropped_on_post(client, install_network):
    n = build_network()
    install_network(n)
    r = client.post("/api/network/generators", json={
        "name": "g2", "bus": "B1", "carrier": "gas", "p_nom": 10.0,
        "bogus_key": 1,
    })
    assert r.status_code in (200, 201)
    assert "bogus_key" not in n.generators.columns


def test_extras_do_not_disturb_the_partial_update_merge(client, install_network):
    # _merge_partial_update keeps unsent fields at their current value; an
    # extra must not reset any of them.
    n = build_network()
    install_network(n)
    before = float(n.generators.at["gas", "marginal_cost"])
    r = client.put("/api/network/generators/gas", json={**BASE, "weight": 2.0})
    assert r.status_code == 200
    assert float(n.generators.at["gas", "marginal_cost"]) == before


def test_input_attributes_reports_the_catalog_inputs():
    from services.attribute_catalog import input_attributes
    n = pypsa.Network()
    attrs = input_attributes(n, "Generator")
    assert "p_nom" in attrs
    assert "weight" in attrs
    assert "p_nom_opt" not in attrs          # Output


def test_input_attributes_is_empty_for_an_unknown_class():
    from services.attribute_catalog import input_attributes
    assert input_attributes(pypsa.Network(), "Widget") == set()


# ── Multi-port Links (visual-layers plan 1, A1) ──────────────────────────────
# PyPSA's Link catalog lists bus0 / bus1 / efficiency only; the extra ports a
# CHP or heat pump needs (`bus2`, `efficiency2`, …) are columns PyPSA creates
# on first use and honours by regex (`pypsa.constants.RE_PORTS_GE_2`). The
# whitelist used to drop them on a network with no prior `bus2` column, so a
# CHP created from the palette silently lost its heat port.

def _three_bus_network() -> pypsa.Network:
    n = pypsa.Network()
    n.add("Bus", "gas", carrier="gas")
    n.add("Bus", "elec", carrier="AC")
    n.add("Bus", "heat", carrier="heat")
    return n


# Exactly what `CreationForm`'s `chp` palette item posts (name aside).
CHP = {
    "name": "CHP 1", "bus0": "gas", "bus1": "elec", "bus2": "heat", "carrier": "gas",
    "p_nom": 100.0, "efficiency": 0.4, "efficiency2": 0.4, "p_nom_extendable": False,
    "capital_cost": 0.0, "marginal_cost": 0.0,
}


def test_a_chp_from_the_palette_keeps_bus2_and_efficiency2(client, install_network):
    n = _three_bus_network()
    install_network(n)
    assert "bus2" not in n.links.columns          # the fresh-network case
    r = client.post("/api/network/links", json=CHP)
    assert r.status_code in (200, 201), r.text
    assert n.links.at["CHP 1", "bus2"] == "heat"
    assert float(n.links.at["CHP 1", "efficiency2"]) == 0.4
    assert n.components.links.additional_ports == ["2"]


def test_unset_ports_do_not_grow_empty_columns(client, install_network):
    # LinkCreate declares bus3 / bus4 / efficiency3 with "" / 1.0 defaults and
    # the route dumps the whole model, so an unset port must not create a
    # `bus3` column PyPSA would then treat as a third port on every link.
    n = _three_bus_network()
    install_network(n)
    r = client.post("/api/network/links", json=CHP)
    assert r.status_code in (200, 201), r.text
    for col in ("bus3", "bus4", "efficiency3"):
        assert col not in n.links.columns, col


def test_a_bogus_key_on_a_link_is_still_dropped(client, install_network):
    n = _three_bus_network()
    install_network(n)
    r = client.post("/api/network/links", json={**CHP, "bogus_key": 1, "bus_two": "heat"})
    assert r.status_code in (200, 201), r.text
    assert "bogus_key" not in n.links.columns
    assert "bus_two" not in n.links.columns


def test_a_second_port_edit_through_put_survives(client, install_network):
    n = _three_bus_network()
    install_network(n)
    assert client.post("/api/network/links", json=CHP).status_code in (200, 201)
    r = client.put("/api/network/links/CHP 1", json={**CHP, "efficiency2": 0.55})
    assert r.status_code == 200, r.text
    assert float(n.links.at["CHP 1", "efficiency2"]) == 0.55
    assert n.links.at["CHP 1", "bus2"] == "heat"


def test_link_port_extras_is_a_regex_not_a_list():
    from services.network_crud import _link_port_extras
    kept = _link_port_extras({
        "bus2": "heat", "efficiency2": 0.4,
        "bus10": "x", "efficiency10": 0.1,       # two-digit ports are ports too
        "bus3": "", "efficiency3": 1.0,          # unset port: neither key survives
        "bus1": "elec", "efficiency": 0.4,       # catalog attributes, not this arm's job
        "p_min_pu2": 0.0,                        # not a PyPSA per-port attribute
    })
    assert kept == {"bus2": "heat", "efficiency2": 0.4, "bus10": "x", "efficiency10": 0.1}

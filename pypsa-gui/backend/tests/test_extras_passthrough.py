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

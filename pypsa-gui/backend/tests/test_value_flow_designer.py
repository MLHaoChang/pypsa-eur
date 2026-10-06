"""
`GET /api/simulation/value_flows/designer` (Edge Investment Case P3 WP3.6):
what the participants designer offers — the assets with their meter side (a
grid-side asset is the market's, never ownable), the tariff items with the
payee they resolve to by default, the contracts' parties and the group.
"""
from __future__ import annotations

from tests.fixtures.investment_case.edge_15min import build_edge_15min

TARIFF = {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2029-01-01",
          "items": [{"id": "energy", "kind": "energy", "unit": "per_kwh",
                     "periods": [{"name": "all", "rate": 0.2}]},
                    {"id": "demand", "kind": "demand", "unit": "per_kw_month",
                     "periods": [{"name": "all", "rate": 9.0}]}]}
PPA = {"type": "ppa", "id": "ppa1", "kind": "pay_as_produced", "price": 20.0,
       "tenor_years": 10, "seller": "Solar BV", "buyer": "site", "asset_ids": ["pv"]}


def test_the_designer_context(client, install_network):
    install_network(build_edge_15min(), name="designer")
    r = client.put("/api/simulation/solver_config", json={"commercial": {
        "poc_link": "import", "import_tariff": TARIFF, "contracts": [PPA]}})
    assert r.status_code == 200, r.text
    r = client.get("/api/simulation/value_flows/designer")
    assert r.status_code == 200, r.text
    body = r.json()
    assets = {(a["component"], a["name"]): a for a in body["assets"]}
    assert assets[("Generator", "grid_supply")]["ownable"] is False
    assert assets[("Generator", "pv")]["side"] == "site" and assets[("Generator", "pv")]["ownable"]
    assert assets[("Link", "import")]["bus"] == "grid"          # a Link's bus0
    items = {i["id"]: i for i in body["tariff_items"]}
    assert items["energy"]["default_payee"] == "retailer"
    assert items["demand"]["default_payee"] == "dso"
    assert body["contract_parties"] == ["Solar BV", "site"]
    assert body["site_party"] == "site" and "market" in body["default_externals"]


def test_the_designer_needs_a_commercial_config(client, install_network):
    install_network(build_edge_15min(), name="designer_bare")
    r = client.get("/api/simulation/value_flows/designer")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_commercial_config"

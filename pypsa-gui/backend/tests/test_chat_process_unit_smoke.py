"""
The chat creates an HB-like process unit (grid-to-process 14): an `eh_process` Link with a negative `efficiency2`, a
product bus with `mwh_per_t` and `lost_production_value`, and `buy_in` / `shortfall` Generators, all through
`create_component` (the schema validation the chat tools share). The solved network then has `process_view` rows for
the product, and the attribute docs the chat reads name the two fields.
"""
from __future__ import annotations

import pypsa

from services import chat_tools
from services.asset_schema.attrs_doc import attrs_doc
from services.results.process_view import compute_process_view

MWH_PER_T = 5.166


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(range(4))
    n.add("Carrier", ["AC", "H2", "NH3"])
    n.add("Bus", "el", carrier="AC")
    n.add("Bus", "h2", carrier="H2")
    n.add("Generator", "grid", bus="el", carrier="AC", p_nom=100.0, marginal_cost=50.0)
    n.add("Generator", "h2_supply", bus="h2", carrier="H2", p_nom=200.0, marginal_cost=100.0)
    return n


def test_the_docs_name_the_two_process_fields():
    doc = attrs_doc()
    assert "lost_production_value" in doc
    assert "process_role=buy_in|shortfall" in doc


def test_the_chat_creates_an_hb_like_unit_and_the_view_has_its_product(install_network):
    n = install_network(_network())
    chat_tools.create_component("Bus", "nh3", attrs={"carrier": "NH3", "unit": "MWh", "mwh_per_t": MWH_PER_T,
                                                     "product_value": 120.0, "lost_production_value": 150.0})
    chat_tools.create_component("Link", "hb", attrs={"bus0": "el", "bus1": "nh3", "bus2": "h2", "carrier": "NH3",
                                                     "p_nom": 10.0, "efficiency": 4.0, "efficiency2": -4.6,
                                                     "p_min_pu": 0.0, "eh_process": True})
    chat_tools.create_component("Generator", "buy_nh3", attrs={"bus": "nh3", "carrier": "NH3", "p_nom": 5.0,
                                                               "marginal_cost": 97.0, "process_role": "buy_in"})
    chat_tools.create_component("Generator", "lost_nh3", attrs={"bus": "nh3", "carrier": "NH3", "p_nom": 1000.0,
                                                                "marginal_cost": 150.0,
                                                                "process_role": "shortfall"})
    chat_tools.create_component("Load", "nh3_delivery", attrs={"bus": "nh3", "carrier": "NH3", "p_set": 20.0})

    assert n.buses.at["nh3", "lost_production_value"] == 150.0
    assert n.generators.at["lost_nh3", "process_role"] == "shortfall"
    assert bool(n.links.at["hb", "eh_process"]) and n.links.at["hb", "efficiency2"] < 0

    n.optimize(solver_name="highs")
    view = compute_process_view(n)
    assert [p["bus"] for p in view["products"]] == ["nh3"]
    assert [u["name"] for u in view["process_units"]] == ["hb"]

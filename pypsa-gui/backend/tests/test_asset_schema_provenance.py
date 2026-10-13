"""
S1a: per-field provenance, `n.meta["asset_params"][class][name][field]`
(uniform asset parameterisation, rule 7). The guided ledger (S2) reads this
store; it never keeps its own. One write helper; an Expert or chat edit that
CHANGES a value stamps it `customised`; a rename re-keys it; every removal
drops it.
"""
from __future__ import annotations

import os
import tempfile

import pypsa
import pytest

from services.asset_schema import provenance as P
from tests.conftest import build_network

ROUTE = "/api/network/asset_schema"


def _net() -> pypsa.Network:
    n = pypsa.Network()
    n.add("Bus", "b")
    n.add("Generator", "g", bus="b", p_nom=10.0)
    return n


# ── the store ────────────────────────────────────────────────────────────────

def test_empty_store_reads_as_no_entries():
    assert P.get(_net(), "Generator", "g") == {}


def test_record_then_get():
    n = _net()
    P.record(n, "Generator", "g", "overnight_cost", status="default", source="technology-data v0.14.0",
             source_year=2025, currency_year=2020, range=(337_750.0, 627_250.0))
    entry = P.get(n, "Generator", "g")["overnight_cost"]
    assert entry == {
        "status": "default", "source": "technology-data v0.14.0", "source_year": 2025,
        "currency_year": 2020, "range": [337_750.0, 627_250.0], "illustrative": False,
    }


def test_unknown_status_is_refused():
    with pytest.raises(ValueError):
        P.record(_net(), "Generator", "g", "p_nom", status="guessed")


def test_customised_keeps_the_source_it_replaced():
    n = _net()
    P.record(n, "Generator", "g", "marginal_cost", status="default", source="pack 2026-10-05")
    P.mark_customised(n, "Generator", "g", ["marginal_cost"])
    e = P.get(n, "Generator", "g")["marginal_cost"]
    assert e["status"] == "customised"
    assert e["source"] == "pack 2026-10-05"     # what the user overrode stays visible


def test_rename_and_drop():
    n = _net()
    P.record(n, "Generator", "g", "p_nom", status="measured")
    P.rename(n, "Generator", "g", "g2")
    assert P.get(n, "Generator", "g") == {}
    assert P.get(n, "Generator", "g2")["p_nom"]["status"] == "measured"
    P.drop(n, "Generator", "g2")
    assert P.get(n, "Generator", "g2") == {}
    assert n.meta["asset_params"] == {}         # no empty shells left behind


def test_survives_a_netcdf_round_trip():
    n = _net()
    P.record(n, "Generator", "g", "p_nom", status="measured", range=(9.0, 11.0))
    path = os.path.join(tempfile.mkdtemp(), "n.nc")
    n.export_to_netcdf(path)
    assert P.get(pypsa.Network(path), "Generator", "g") == P.get(n, "Generator", "g")


# ── the write paths ──────────────────────────────────────────────────────────

def _full_generator_payload(n, name: str) -> dict:
    """What the Expert card sends: the whole form, changed or not."""
    row = n.generators.loc[name]
    return {"name": name, "bus": row.bus, "carrier": row.carrier, "p_nom": float(row.p_nom),
            "marginal_cost": float(row.marginal_cost)}


def test_an_edit_marks_only_the_fields_it_changed(client, install_network):
    n = install_network(build_network())
    body = _full_generator_payload(n, "gas") | {"marginal_cost": 61.0}
    assert client.put("/api/network/generators/gas", json=body).status_code == 200
    prov = P.get(n, "Generator", "gas")
    assert prov["marginal_cost"]["status"] == "customised"
    assert "p_nom" not in prov                  # sent, but unchanged
    assert "name" not in prov


def test_a_blank_field_sent_blank_is_not_an_edit(client, install_network):
    n = install_network(build_network())
    body = _full_generator_payload(n, "gas") | {"discount_rate": None, "mttr_hours": None}
    assert client.put("/api/network/generators/gas", json=body).status_code == 200
    assert P.get(n, "Generator", "gas") == {}


def test_derived_fields_are_never_marked(client, install_network):
    n = build_network()
    n.add("StorageUnit", "bess", bus="B1", p_nom=10.0, max_hours=2.0)
    n = install_network(n)
    r = client.put("/api/network/storage_units/bess", json={
        "name": "bess", "bus": "B1",
        "inv_power_overnight": 200_000.0, "inv_power_lifetime": 10.0,
        "inv_energy_overnight": 150_000.0, "inv_energy_lifetime": 20.0,
    })
    assert r.status_code == 200, r.text
    prov = P.get(n, "StorageUnit", "bess")
    assert prov["inv_power_overnight"]["status"] == "customised"
    for derived in ("capital_cost", "fom_cost", "lifetime"):
        assert derived not in prov, derived


def test_rename_through_the_route_moves_the_entries(client, install_network):
    n = install_network(build_network())
    body = _full_generator_payload(n, "gas") | {"marginal_cost": 61.0}
    client.put("/api/network/generators/gas", json=body)
    r = client.put("/api/network/generators/gas", json={"name": "gas_ccgt", "bus": "B1"})
    assert r.status_code == 200, r.text
    assert P.get(n, "Generator", "gas") == {}
    assert P.get(n, "Generator", "gas_ccgt")["marginal_cost"]["status"] == "customised"


def test_delete_drops_the_entries(client, install_network):
    n = install_network(build_network())
    client.put("/api/network/generators/gas", json=_full_generator_payload(n, "gas") | {"marginal_cost": 61.0})
    assert client.delete("/api/network/generators/gas").status_code == 204
    assert P.get(n, "Generator", "gas") == {}


def test_provenance_route(client, install_network):
    n = install_network(build_network())
    client.put("/api/network/generators/gas", json=_full_generator_payload(n, "gas") | {"marginal_cost": 61.0})
    r = client.get(f"{ROUTE}/Generator/provenance/gas")
    assert r.status_code == 200
    assert r.json() == {"component": "Generator", "name": "gas",
                        "fields": {"marginal_cost": P.get(n, "Generator", "gas")
                                   ["marginal_cost"]}}
    assert client.get(f"{ROUTE}/Generator/provenance/nope").status_code == 404

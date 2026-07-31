"""
A zero CO2 result must never be silently caused by a missing emission factor.

A real project reported 0 tCO2 for a 300 MW gas plant. The number was correct:
its generator's carrier is `gas`, which was absent from the catalog, so
ensure_carrier created a bare row with co2_emissions=0.0 and the emissions
formula multiplied by it.

Nothing warned because both existing guards are CONDITIONAL — one on
co2_price > 0, the other on a global constraint existing. That network had
neither, which is why this warning is ungated.
"""
from __future__ import annotations

from services.carrier_catalog import CARRIER_CATALOG, ensure_carrier
from services.validation_service import _looks_fossil


def _codes(client) -> list[str]:
    # NOTE: the brief's helper assumed `GET /api/validation`. The real
    # endpoint (verified against routers/simulation.py + frontend/src/api/
    # simulation.ts) is `POST /api/simulation/preflight`, returning
    # {"ok", "errors", "warnings", "issues": [Issue.to_dict(), ...]}. The
    # `issues[].code` shape the brief specified is unchanged — only the
    # path and HTTP method differ.
    r = client.post("/api/simulation/preflight")
    assert r.status_code == 200, r.text
    return [i["code"] for i in r.json().get("issues", [])]


def test_gas_is_in_the_catalog_with_the_same_factor_as_CCGT(client):
    # Justified by this repo's own entries rather than invented: CCGT and OCGT
    # burn natural gas and are already 0.187.
    assert CARRIER_CATALOG["gas"]["co2_emissions"] == CARRIER_CATALOG["CCGT"]["co2_emissions"]
    assert CARRIER_CATALOG["diesel"]["co2_emissions"] == CARRIER_CATALOG["oil"]["co2_emissions"]


def test_ensure_carrier_never_repairs_an_existing_row(client):
    # This is why the fix must be OFFERED rather than left to the catalog.
    import pypsa
    n = pypsa.Network()
    n.add("Carrier", "gas", nice_name="gas", color="", co2_emissions=0.0)
    ensure_carrier(n, "gas")
    assert float(n.carriers.at["gas", "co2_emissions"]) == 0.0


def test_looks_fossil_excludes_biogas():
    # `biogas` contains `gas` but its CO2 is biogenic and conventionally zero.
    # A false positive here teaches users to ignore the warning.
    assert _looks_fossil("gas") is True
    assert _looks_fossil("CCGT") is True
    assert _looks_fossil("lignite") is True
    assert _looks_fossil("diesel") is True
    assert _looks_fossil("biogas") is False
    assert _looks_fossil("solar") is False
    assert _looks_fossil("onwind") is False


def test_warns_for_a_fossil_carrier_with_no_intensity(client):
    client.post("/api/network/buses", json={"name": "B1", "v_nom": 380.0, "x": 6.96, "y": 50.9})
    client.post("/api/network/generators", json={
        "name": "G1", "bus": "B1", "carrier": "gas", "p_nom": 300.0, "efficiency": 0.45,
    })
    # Zero the intensity the catalog would now supply, reproducing the state a
    # project created before this fix is in.
    client.put("/api/network/carriers/gas", json={"name": "gas", "co2_emissions": 0.0})
    assert "carrier_zero_co2" in _codes(client)


def test_does_not_warn_for_renewables(client):
    client.post("/api/network/buses", json={"name": "B1", "v_nom": 380.0, "x": 6.96, "y": 50.9})
    client.post("/api/network/generators", json={
        "name": "G1", "bus": "B1", "carrier": "solar", "p_nom": 300.0,
    })
    assert "carrier_zero_co2" not in _codes(client)


def test_does_not_warn_once_an_intensity_is_set(client):
    client.post("/api/network/buses", json={"name": "B1", "v_nom": 380.0, "x": 6.96, "y": 50.9})
    client.post("/api/network/generators", json={
        "name": "G1", "bus": "B1", "carrier": "gas", "p_nom": 300.0, "efficiency": 0.45,
    })
    client.put("/api/network/carriers/gas", json={"name": "gas", "co2_emissions": 0.187})
    assert "carrier_zero_co2" not in _codes(client)

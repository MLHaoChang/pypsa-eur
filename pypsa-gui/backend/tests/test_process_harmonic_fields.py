"""
Grid-to-process issue 17 (harmonics): a process unit's harmonic inputs, `rated_mva`, `harmonic_source`,
`harmonic_spectrum` and `spectrum_post_filter`, through the API.
"""
from __future__ import annotations

import math

import pypsa
import pytest

from models.process_unit import HARMONIC_SOURCES

DRIVE = {
    "name": "compressor", "bus0": "hub", "bus1": "nh3", "carrier": "NH3", "p_nom": 20.0, "efficiency": 1.0,
    "p_nom_extendable": False, "capital_cost": 0.0, "marginal_cost": 0.0, "eh_process": True,
    "rated_mva": 25.0, "harmonic_source": "6-pulse", "harmonic_spectrum": "5: 20, 7: 14",
    "spectrum_post_filter": True,
}


def _net() -> pypsa.Network:
    n = pypsa.Network()
    n.add("Bus", "hub", carrier="AC")
    n.add("Bus", "nh3", carrier="NH3", unit="MWh")
    return n


def _row(client, name):
    return next(r for r in client.get("/api/network/links").json() if r["name"] == name)


def test_the_sources_are_the_screens_topologies():
    from gridspine.static.harmonics import TOPOLOGIES
    assert HARMONIC_SOURCES == TOPOLOGIES


def test_harmonic_inputs_round_trip(client, install_network):
    n = _net()
    install_network(n)
    r = client.post("/api/network/links", json=DRIVE)
    assert r.status_code in (200, 201), r.text
    row = _row(client, "compressor")
    for k in ("rated_mva", "harmonic_source", "harmonic_spectrum", "spectrum_post_filter"):
        assert row[k] == DRIVE[k], (k, row[k])
    assert n.links.at["compressor", "harmonic_source"] == "6-pulse"


def test_rated_mva_has_no_fallback_to_the_capacity(client, install_network):
    n = _net()
    install_network(n)
    body = {k: v for k, v in DRIVE.items() if k != "rated_mva"}
    assert client.post("/api/network/links", json=body).status_code in (200, 201)
    assert "rated_mva" not in n.links.columns or math.isnan(float(n.links.at["compressor", "rated_mva"]))


@pytest.mark.parametrize("patch", [
    {"harmonic_source": "24-pulse"},
    {"harmonic_spectrum": "5:20, 5:10"},
    {"harmonic_spectrum": "1:20"},
    {"harmonic_spectrum": "51:1"},
    {"harmonic_spectrum": "5:-1"},
    {"harmonic_spectrum": "5.5:1"},
    {"rated_mva": -1.0},
])
def test_bad_harmonic_inputs_are_refused(client, install_network, patch):
    install_network(_net())
    r = client.post("/api/network/links", json={**DRIVE, **patch})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("patch, message", [
    ({"harmonic_spectrum": "51:1"}, "Harmonic spectrum: order 51 is above 50."),
    ({"harmonic_source": "24-pulse"}, "Harmonic source: '24-pulse' is not one of linear, 6-pulse"),
])
def test_refusals_name_the_field_by_its_label(client, install_network, patch, message):
    """UX: a refusal starts capitalised and names the field by its label, not its column."""
    install_network(_net())
    r = client.post("/api/network/links", json={**DRIVE, **patch})
    assert r.status_code == 422 and message in r.text, r.text

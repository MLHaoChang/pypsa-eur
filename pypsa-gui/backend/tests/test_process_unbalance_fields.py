"""
Datacenter readiness 12 (unbalance): a process unit's `single_phase_share` (0..1, shown as %) through the API.
"""
from __future__ import annotations

import math

import pytest

from services.asset_schema import fields as F
from tests.test_process_harmonic_fields import DRIVE, _net, _row

FURNACE = {**DRIVE, "name": "furnace", "single_phase_share": 0.4}


def test_the_share_is_an_editable_process_unit_field():
    spec = {f.key: f for f in F.fields_for("Link")}["single_phase_share"]
    assert (spec.label, spec.unit_storage, spec.unit_display, spec.scale) == ("Single-phase share", "fraction", "%", 100.0)
    assert spec.reveal_when == {"eh_process": True} and not spec.derived


def test_the_share_round_trips_through_create_and_edit(client, install_network):
    n = _net()
    install_network(n)
    r = client.post("/api/network/links", json=FURNACE)
    assert r.status_code in (200, 201), r.text
    first = _row(client, "furnace")
    assert first["single_phase_share"] == 0.4
    r = client.put("/api/network/links/furnace", json={**first, "single_phase_share": 1.0})
    assert r.status_code == 200, r.text
    assert n.links.at["furnace", "single_phase_share"] == 1.0


def test_a_unit_without_a_share_never_gets_the_column(client, install_network):
    n = _net()
    install_network(n)
    assert client.post("/api/network/links", json=DRIVE).status_code in (200, 201)
    assert ("single_phase_share" not in n.links.columns
            or math.isnan(float(n.links.at["compressor", "single_phase_share"])))


@pytest.mark.parametrize("share", [-0.1, 1.01, "half"])
def test_a_share_outside_0_to_1_is_refused(client, install_network, share):
    install_network(_net())
    r = client.post("/api/network/links", json={**FURNACE, "single_phase_share": share})
    assert r.status_code == 422 and "single_phase_share" in r.text, r.text


def test_an_edit_outside_0_to_1_is_refused(client, install_network):
    install_network(_net())
    assert client.post("/api/network/links", json=FURNACE).status_code in (200, 201)
    first = _row(client, "furnace")
    r = client.put("/api/network/links/furnace", json={**first, "single_phase_share": 1.5})
    assert r.status_code == 422, r.text

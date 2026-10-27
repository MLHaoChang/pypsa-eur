"""
Grid-to-process issue 17 (flicker): a process unit's flicker inputs, `flicker_step_mva` and
`flicker_changes_per_min`, through the API.
"""
from __future__ import annotations

import math

import pytest

from tests.test_process_harmonic_fields import DRIVE, _net, _row

HEATER = {**DRIVE, "name": "heater", "harmonic_source": "burst", "harmonic_spectrum": "",
          "flicker_step_mva": 12.5, "flicker_changes_per_min": 4.0}


def test_flicker_inputs_round_trip(client, install_network):
    n = _net()
    install_network(n)
    r = client.post("/api/network/links", json=HEATER)
    assert r.status_code in (200, 201), r.text
    row = _row(client, "heater")
    for k in ("flicker_step_mva", "flicker_changes_per_min"):
        assert row[k] == HEATER[k], (k, row[k])
    assert n.links.at["heater", "flicker_changes_per_min"] == 4.0


def test_a_unit_without_flicker_inputs_never_gets_the_columns(client, install_network):
    n = _net()
    install_network(n)
    assert client.post("/api/network/links", json=DRIVE).status_code in (200, 201)
    for k in ("flicker_step_mva", "flicker_changes_per_min"):
        assert k not in n.links.columns or math.isnan(float(n.links.at["compressor", k]))


@pytest.mark.parametrize("patch", [{"flicker_step_mva": -1.0}, {"flicker_changes_per_min": -0.5}])
def test_negative_flicker_inputs_are_refused(client, install_network, patch):
    install_network(_net())
    r = client.post("/api/network/links", json={**HEATER, **patch})
    assert r.status_code == 422, r.text

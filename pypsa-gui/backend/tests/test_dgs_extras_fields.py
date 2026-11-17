"""
DGS-EXTRAS-1: the harmonic screen's inputs on a Load or Generator (`rated_mva`, `harmonic_source`,
`harmonic_spectrum`) and the slack's external-grid strength (`sk_max_mva`, `sk_min_mva`, `rx_max`, `rx_min`),
through the same write path and checks as a process unit's.
"""

from __future__ import annotations

import math

import pypsa
import pytest

from services.asset_schema import fields as F

GEN = {
    "name": "pv",
    "bus": "b",
    "carrier": "solar",
    "p_nom": 10.0,
    "rated_mva": 12.0,
    "harmonic_source": "active-front-end",
    "harmonic_spectrum": "5:3, 7:2",
}
SLACK = {
    "name": "grid",
    "bus": "b",
    "carrier": "AC",
    "p_nom": 100.0,
    "control": "Slack",
    "sk_max_mva": 3000.0,
    "sk_min_mva": 1200.0,
    "rx_max": 0.1,
    "rx_min": 0.15,
}
LOAD = {
    "name": "pump",
    "bus": "b",
    "p_set": 3.0,
    "rated_mva": 4.5,
    "harmonic_source": "6-pulse",
    "harmonic_spectrum": "5:20, 7:14",
}


def _net() -> pypsa.Network:
    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    return n


@pytest.mark.parametrize(
    "route, body, attr",
    [
        ("generators", GEN, "generators"),
        ("generators", SLACK, "generators"),
        ("loads", LOAD, "loads"),
    ],
)
def test_the_new_inputs_round_trip(client, install_network, route, body, attr):
    n = _net()
    install_network(n)
    r = client.post(f"/api/network/{route}", json=body)
    assert r.status_code in (200, 201), r.text
    df = getattr(n, attr)
    for k, v in body.items():
        if k in ("name", "bus", "carrier", "p_nom", "p_set", "control"):
            continue
        assert df.at[body["name"], k] == v, (k, df.at[body["name"], k])


@pytest.mark.parametrize("route, body", [("generators", GEN), ("loads", LOAD)])
@pytest.mark.parametrize(
    "patch",
    [
        {"harmonic_source": "24-pulse"},
        {"harmonic_spectrum": "5:20, 5:10"},
        {"harmonic_spectrum": "51:1"},
        {"rated_mva": -1.0},
    ],
)
def test_bad_harmonic_inputs_are_refused(client, install_network, route, body, patch):
    install_network(_net())
    r = client.post(f"/api/network/{route}", json={**body, **patch})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize(
    "patch",
    [{"sk_max_mva": -1.0}, {"sk_min_mva": -5.0}, {"rx_max": -0.1}, {"rx_min": -1.0}],
)
def test_negative_grid_strength_is_refused(client, install_network, patch):
    install_network(_net())
    r = client.post("/api/network/generators", json={**SLACK, **patch})
    assert r.status_code == 422, r.text


def test_an_edit_writes_the_new_inputs(client, install_network):
    n = _net()
    install_network(n)
    assert client.post(
        "/api/network/loads", json={"name": "pump", "bus": "b", "p_set": 3.0}
    ).status_code in (200, 201)
    r = client.put(
        "/api/network/loads/pump",
        json={
            "name": "pump",
            "bus": "b",
            "p_set": 3.0,
            "harmonic_source": "12-pulse",
            "rated_mva": 4.0,
        },
    )
    assert r.status_code == 200, r.text
    assert n.loads.at["pump", "harmonic_source"] == "12-pulse"
    assert n.loads.at["pump", "rated_mva"] == 4.0


def test_a_generator_without_the_inputs_gets_no_column(client, install_network):
    n = _net()
    install_network(n)
    body = {"name": "g", "bus": "b", "carrier": "AC", "p_nom": 1.0}
    assert client.post("/api/network/generators", json=body).status_code in (200, 201)
    for k in ("rated_mva", "sk_max_mva"):
        assert k not in n.generators.columns or math.isnan(
            float(n.generators.at["g", k])
        )


def test_grid_strength_shows_only_on_the_slack():
    by_key = {f.key: f for f in F.fields_for("Generator")}
    for k in ("sk_max_mva", "sk_min_mva", "rx_max", "rx_min"):
        assert by_key[k].reveal_when == {"control": "Slack"}
        assert by_key[k].blank == "clear"
    for k in ("harmonic_source", "harmonic_spectrum"):
        assert by_key[k].blank == "empty"
        assert {f.key: f for f in F.fields_for("Load")}[k].blank == "empty"
    assert by_key["harmonic_source"].options == F.HARMONIC_SOURCES
    assert {f.key: f for f in F.fields_for("Load")}["rated_mva"].blank == "clear"

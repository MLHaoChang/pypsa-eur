"""R1-single-run: the clustering UI's undo wording rests on this contract.

Solver settings tells the user "You can undo it with Ctrl+Z" before it
clusters, and turns its done line into "Clustering undone." when the undo
poll (`/api/network/undo/info`) shows the network revision moved AND the
undo depth dropped. So POST /api/network/cluster must be one undo step that
bumps the revision, and undoing it must restore the network, bump the
revision again and drop the depth back.
"""

from __future__ import annotations

import pandas as pd
import pypsa


def _two_country_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.add("Carrier", "gas")
    for i, (country, x) in enumerate([("DE", 8), ("DE", 9), ("FR", 2), ("FR", 3)]):
        n.add("Bus", f"b{i}", carrier="AC", country=country, x=x, y=50, v_nom=110)
    for a, b in [(0, 1), (1, 2), (2, 3)]:
        n.add("Line", f"l{a}{b}", bus0=f"b{a}", bus1=f"b{b}", x=0.1, r=0.01, s_nom=100)
    n.add("Generator", "g0", bus="b0", carrier="gas", p_nom=200, marginal_cost=50)
    n.add("Load", "d3", bus="b3", p_set=50)
    return n


def test_clustering_is_one_undo_step_and_its_undo_is_visible_to_the_poll(
    client, install_network, api_project
):
    api_project("clustering_undo_project")
    install_network(_two_country_network())

    def bus_names():
        return sorted(b["name"] for b in client.get("/api/network/buses").json())

    def info():
        return client.get("/api/network/undo/info").json()

    before = info()
    assert bus_names() == ["b0", "b1", "b2", "b3"]

    r = client.post("/api/network/cluster", json={"mode": "zone"})
    assert r.status_code == 200, r.text
    assert r.json()["bus_count"] == 2
    clustered = info()
    assert clustered["network_revision"] != before["network_revision"]
    assert clustered["depth"] == before["depth"] + 1

    assert client.post("/api/network/undo").status_code == 200
    assert bus_names() == ["b0", "b1", "b2", "b3"]
    undone = info()
    # What the UI reads as "Clustering undone.": the revision moved on and
    # the depth fell below the one the clustering left.
    assert undone["network_revision"] != clustered["network_revision"]
    assert undone["depth"] < clustered["depth"]

"""Provenance-graph 13: a value edit through a component PUT route marks the last run stale, never not_run.

Slice 04 (D1 a): "a value edit keeping the dispatch shape reads stale". The route middleware cleared the
dispatch on every accepted `/api/network/*` write, so the flow read `not_run` and Results lost the run.
"""

import pandas as pd
import pypsa
import pytest

from tests.test_project_flow import _flow, _kinds, _wait_idle, site  # noqa: F401


def _all_components_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=4, freq="h"))
    n.add("Carrier", "AC")
    n.add("Carrier", "gas")
    n.add("Bus", "a", carrier="AC")
    n.add("Bus", "b", carrier="AC")
    n.add("Bus", "c", carrier="AC")
    n.add("Generator", "g", bus="a", carrier="gas", p_nom=100.0, marginal_cost=20.0)
    n.add("Load", "l", bus="b", p_set=30.0)
    n.add("Line", "ab", bus0="a", bus1="b", x=0.1, r=0.01, s_nom=200.0)
    n.add("Link", "bc", bus0="b", bus1="c", p_nom=50.0)
    n.add("StorageUnit", "su", bus="b", p_nom=10.0, max_hours=2.0, marginal_cost=1.0)
    n.add("Store", "st", bus="c", e_nom=10.0, e_cyclic=True)
    n.add("Load", "lc", bus="c", p_set=5.0)
    return n


# (route, name, field, new value): each edit changes one number the solve read.
ROUTES = [
    ("generators", "g", "marginal_cost", 25.0),
    ("loads", "l", "p_set", 35.0),
    ("lines", "ab", "s_nom", 150.0),
    ("links", "bc", "p_nom", 40.0),
    ("storage_units", "su", "p_nom_max", 20.0),
    ("stores", "st", "e_nom", 12.0),
]


@pytest.mark.parametrize("route,name,field,value", ROUTES, ids=[r[0] for r in ROUTES])
def test_a_value_edit_marks_the_last_run_stale(client, site, route, name, field, value):  # noqa: F811
    project = site(_all_components_network(), name=f"pg13_{route}")
    assert client.post("/api/simulation/run").status_code == 200
    assert _wait_idle(client)["status"] == "completed"
    assert _kinds(_flow(client, project))["optimisation"] == "fresh"

    row = next(
        r for r in client.get(f"/api/network/{route}").json() if r["name"] == name
    )
    r = client.put(f"/api/network/{route}/{name}", json={**row, field: value})
    assert r.status_code == 200, r.text

    assert _kinds(_flow(client, project))["optimisation"] == "stale"
    # The last run's figures stay readable.
    assert client.get("/api/results/generators").status_code == 200


def test_the_expert_table_bulk_edit_marks_the_last_run_stale(client, site):  # noqa: F811
    """The Expert table saves through `PATCH /_bulk`, not the per-row PUT."""
    project = site(_all_components_network(), name="pg13_bulk")
    assert client.post("/api/simulation/run").status_code == 200
    assert _wait_idle(client)["status"] == "completed"
    r = client.patch(
        "/api/network/_bulk",
        json={
            "component_class": "StorageUnit",
            "names": ["su"],
            "updates": {"p_nom_max": 20.0},
        },
    )
    assert r.status_code == 200, r.text
    assert _kinds(_flow(client, project))["optimisation"] == "stale"
    assert client.get("/api/results/generators").status_code == 200

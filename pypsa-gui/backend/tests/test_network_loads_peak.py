"""
P22.9-BE bug 4 — a loaded bus showed "Total load 0 MW".

The Properties panel summed the STATIC ``p_set`` of a bus's loads, and a load
whose demand lives in ``loads_t.p_set`` has a static ``p_set`` of 0. The Load
rows now carry an additive ``p_set_peak``: the largest value of the time
series when the load has one, else the static ``p_set``, else ``null``.

It is computed in ``_serialize_component`` (``services/network_crud.py``), the
core that both the active-network shim (``GET /api/network/loads``) and the
path-scoped ``GET /api/projects/{id}/network/loads`` serialise through, so the
two routes cannot disagree. Both are exercised over HTTP.
"""
from __future__ import annotations

import math

import pandas as pd
import pypsa
import pytest

PROJECT = "loads_peak"


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=4, freq="h"))
    n.add("Bus", "b")
    n.add("Generator", "gas", bus="b", carrier="gas", p_nom=500.0,
          marginal_cost=50.0)
    n.add("Load", "L_static", bus="b", p_set=42.0)
    # The static value stays at its default 0 — the case the panel misread.
    n.add("Load", "L_ts", bus="b",
          p_set=pd.Series([10.0, 80.0, 30.0, 20.0], index=n.snapshots))
    n.add("Load", "L_none", bus="b")
    n.loads.loc["L_none", "p_set"] = math.nan
    return n


def _by_name(rows: list[dict]) -> dict[str, dict]:
    return {r["name"]: r for r in rows}


@pytest.fixture
def loads_routes(client, install_network, tmp_projects_dir):
    """Both routes' Load rows for the same network: (shim, path-scoped)."""
    install_network(_network(), name=PROJECT)
    r = client.post(f"/api/projects/{PROJECT}",
                    params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    shim = client.get("/api/network/loads")
    assert shim.status_code == 200, shim.text
    scoped = client.get(f"/api/projects/{PROJECT}/network/loads")
    assert scoped.status_code == 200, scoped.text
    return _by_name(shim.json()), _by_name(scoped.json())


def test_static_load_peak_is_its_p_set(loads_routes):
    for rows in loads_routes:
        row = rows["L_static"]
        assert row["p_set_peak"] == pytest.approx(42.0)
        assert row["p_set_peak"] == pytest.approx(row["p_set"])


def test_time_series_load_peak_is_the_series_max(loads_routes):
    for rows in loads_routes:
        row = rows["L_ts"]
        assert row["p_set"] == 0.0          # the value the panel used to sum
        assert row["p_set_peak"] == pytest.approx(80.0)


def test_load_without_p_set_has_null_peak(loads_routes):
    for rows in loads_routes:
        assert "p_set_peak" in rows["L_none"]
        assert rows["L_none"]["p_set_peak"] is None


def test_both_routes_agree(loads_routes):
    shim, scoped = loads_routes
    assert ({k: v["p_set_peak"] for k, v in shim.items()}
            == {k: v["p_set_peak"] for k, v in scoped.items()})


def test_other_components_do_not_gain_the_field(client, install_network):
    install_network(_network())
    rows = client.get("/api/network/generators").json()
    assert rows and all("p_set_peak" not in r for r in rows)

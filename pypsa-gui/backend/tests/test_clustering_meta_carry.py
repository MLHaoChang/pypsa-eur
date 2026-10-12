"""
Spatial clustering must not throw away `n.meta`.

`get_clustering_from_busmap` returns a freshly constructed network, and
`apply_clustering` swapped it into the singleton as-is. `n.meta` is where the
GUI keeps `vintage_bounds` and `vintage_results`, so every clustering run
silently discarded the user's entire per-period bounds configuration and any
stored vintage results. The comment beside the swap reassures about
coordinates, which is what made the omission easy to miss.

Carrying `meta` across blindly is not the fix either: clustering renames and
aggregates components, so a bounds entry for an asset that no longer exists
would be handed to the solver to expand — the same stale-bounds failure the
cascade-delete cleanup exists to prevent. The carry therefore prunes, and
says what it pruned.

There were no clustering tests at all before this file.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from routers.clustering import ClusterRequest, apply_clustering
from services import vintage_service
from services.pypsa_service import PyPSAService


def _two_zone_network() -> pypsa.Network:
    """Four buses in two `sub_network` zones — the shape `mode="region"`
    clusters deterministically, so the test does not depend on k-means."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    for i, (zone, x) in enumerate([("N", 0.0), ("N", 0.1), ("S", 5.0), ("S", 5.1)]):
        n.add("Bus", f"B{i}", v_nom=380.0, x=x, y=float(i), sub_network=zone)
    n.add("Line", "L01", bus0="B0", bus1="B1", x=0.1, r=0.01, s_nom=100.0)
    n.add("Line", "L23", bus0="B2", bus1="B3", x=0.1, r=0.01, s_nom=100.0)
    n.add("Line", "L12", bus0="B1", bus1="B2", x=0.2, r=0.02, s_nom=100.0)
    n.add("Generator", "G0", bus="B0", carrier="wind", p_nom=100.0)
    n.add("Generator", "G2", bus="B2", carrier="wind", p_nom=100.0)
    n.add("Load", "LD0", bus="B0", p_set=50.0)
    n.add("Load", "LD2", bus="B2", p_set=50.0)
    return n


@pytest.fixture
def clustered(install_network):
    # Called directly rather than over HTTP: a request runs against its own
    # org-scoped `ProjectContext`, so `PyPSAService.get_network()` after the
    # response does not return the network the handler swapped in.
    live = install_network(_two_zone_network())
    # Two bounds and a plain meta key — the three things the carry has to get
    # right. PyPSA 1.1.2 re-buses one-port components under their ORIGINAL
    # names, so `G0` survives the run and its bounds must survive with it;
    # the three lines collapse to one renamed branch, so `L01`'s bounds have
    # no asset left to apply to and must go.
    vintage_service.set_bounds_for_asset(
        live, "Generator", "G0", {2030: {"p_nom_min": 10.0, "p_nom_max": 500.0}},
    )
    vintage_service.set_bounds_for_asset(
        live, "Line", "L01", {2030: {"p_nom_min": 0.0, "p_nom_max": 300.0}},
    )
    live.meta["parent_project"] = "base-study"
    body = apply_clustering(ClusterRequest(mode="region"))
    return body, PyPSAService.get_network()


def test_clustering_reduced_the_network(clustered):
    # Guards the fixture itself: if the busmap stopped clustering, the
    # assertions below would pass vacuously.
    body, _ = clustered
    assert body["bus_count"] == 2, body


def test_clustering_keeps_unrelated_meta(clustered):
    _, new_n = clustered
    assert (new_n.meta or {}).get("parent_project") == "base-study", (
        f"clustering discarded n.meta: {new_n.meta!r}"
    )


def test_clustering_keeps_bounds_for_assets_that_survive(clustered):
    _, new_n = clustered
    bounds = (new_n.meta or {}).get("vintage_bounds", {}).get("Generator", {})
    assert "G0" in bounds, (
        "a generator that survived clustering lost its per-period bounds"
    )
    assert bounds["G0"]["2030"]["p_nom_max"] == 500.0


def test_clustering_drops_bounds_for_assets_it_aggregated_away(clustered):
    # The guard against the naive fix: carrying `meta` across WITHOUT pruning
    # hands the solver bounds for an asset that no longer exists.
    _, new_n = clustered
    root = (new_n.meta or {}).get("vintage_bounds", {})
    for cls, attr in [("Generator", "generators"), ("Line", "lines")]:
        survivors = set(getattr(new_n, attr).index)
        orphans = [name for name in root.get(cls, {}) if name not in survivors]
        assert orphans == [], f"stale {cls} bounds survived clustering: {orphans}"


def test_clustering_reports_what_it_pruned(clustered):
    # The user chose those bounds; losing them to a clustering run is a thing
    # to be told about, not to discover when a later solve behaves differently.
    body, _ = clustered
    assert body.get("dropped_vintage_bounds") == {"Line": ["L01"]}, body
    assert "dropped per-period bounds for 1 asset(s)" in body["message"], body

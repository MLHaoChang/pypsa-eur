"""
`POST /vintage_bounds/_cleanup_orphans` sweeps BOTH meta stores.

The endpoint had no tests. It carried its own copy of the "drop saved entries
whose asset is gone" walk, which is the same job spatial clustering needs
doing after PyPSA aggregates components — and the two copies had already
diverged: this one swept `vintage_bounds` only, so an orphaned
`vintage_results` entry with no bounds beside it survived, and Compare's
capacity and economics roll-ups walk that store.

Both callers now share `vintage_service.prune_orphaned_entries`.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from routers.vintage import cleanup_orphan_vintages
from services import vintage_service


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "B")
    n.add("Generator", "G", bus="B", p_nom=100.0, carrier="gas")
    return n


@pytest.fixture
def live(install_network):
    return install_network(_network())


def test_bounds_for_a_missing_asset_are_dropped(live):
    vintage_service.set_bounds_for_asset(
        live, "Generator", "ghost", {2030: {"p_nom_max": 100.0}},
    )
    cleanup_orphan_vintages()
    assert "ghost" not in (live.meta or {}).get("vintage_bounds", {}).get("Generator", {})


def test_bounds_for_a_live_asset_survive(live):
    # The control: the sweep must not take anything that still has an asset.
    vintage_service.set_bounds_for_asset(
        live, "Generator", "G", {2030: {"p_nom_max": 250.0}},
    )
    cleanup_orphan_vintages()
    bounds = (live.meta or {}).get("vintage_bounds", {}).get("Generator", {})
    assert bounds.get("G", {}).get("2030", {}).get("p_nom_max") == 250.0


def test_orphaned_vintage_results_are_dropped_too(live):
    # The half the endpoint's own copy of the walk missed: a results entry
    # with no bounds beside it. Compare's roll-ups read this store, so a
    # stale entry is a wrong number rather than an unused key.
    live.meta.setdefault("vintage_results", {})["Generator"] = {
        "ghost": {"initial_capacity": 10.0, "periods": []},
        "G": {"initial_capacity": 5.0, "periods": []},
    }
    cleanup_orphan_vintages()
    results = (live.meta or {}).get("vintage_results", {}).get("Generator", {})
    assert "ghost" not in results, "an orphaned vintage_results entry survived"
    assert "G" in results, "a live asset's vintage_results entry was taken"

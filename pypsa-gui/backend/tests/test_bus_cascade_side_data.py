"""
Cascade bus delete must clean up after itself exactly as the plain delete does.

`_delete_component` removes a component and then drops two pieces of side
data: the asset's per-period vintage bounds and its `_user_ts` profile
entries. Its own comment says why the second matters — "a future component
reusing the same name inherits the deleted asset's profile".

`apply_delete_bus_cascade` removes a bus and everything attached to it with a
bare `n.remove(...)` and did neither. That is the path a user reaches by
deleting a node on the map, so the common path was the leaky one: orphaned
`_user_ts` entries are re-injected on every solve and are inherited by any
later component that reuses the name, and stale vintage bounds are expanded
by the solver for an asset that no longer exists.

These tests pin the cleanup on the cascade path and, as a control, on the
plain path it was supposed to match.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from routers.network import _user_ts, _user_ts_lock
from services import vintage_service
from services.network_buses import apply_delete_bus_cascade
from services.network_crud import _delete_component
from services.pypsa_service import PyPSAService


def _network_with_side_data() -> pypsa.Network:
    """Two buses; a generator and a load on `B0`, a line between them.

    Every removable asset carries both kinds of side data so a cleanup that
    covers only one of them still fails.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.add("Bus", "B0")
    n.add("Bus", "B1")
    n.add("Generator", "G", bus="B0", p_nom=100.0, carrier="wind")
    n.add("Load", "L", bus="B0", p_set=50.0)
    n.add("Line", "LN", bus0="B0", bus1="B1", x=0.1, r=0.01)
    return n


def _seed_side_data(n) -> None:
    with _user_ts_lock:
        _user_ts[("generators", "p_max_pu", "G")] = pd.Series(0.5, index=n.snapshots)
        _user_ts[("loads", "p_set", "L")] = pd.Series(50.0, index=n.snapshots)
    vintage_service.set_bounds_for_asset(
        n, "Generator", "G", {2030: {"p_nom_min": 10.0, "p_nom_max": 200.0}},
    )


def _bounds_for(n, component_class: str, name: str):
    return (n.meta or {}).get("vintage_bounds", {}).get(component_class, {}).get(name)


@pytest.fixture
def seeded(install_network):
    live = install_network(_network_with_side_data())
    _seed_side_data(live)
    try:
        yield live
    finally:
        with _user_ts_lock:
            _user_ts.clear()


def test_plain_delete_drops_user_ts_and_vintage_bounds(seeded):
    # The control: this path was already correct, and must stay correct.
    _delete_component("Generator", "generators", "G")
    with _user_ts_lock:
        assert ("generators", "p_max_pu", "G") not in _user_ts
    assert _bounds_for(seeded, "Generator", "G") is None


def test_cascade_delete_drops_user_ts_of_every_component_it_removes(seeded):
    apply_delete_bus_cascade("B0")
    with _user_ts_lock:
        leaked = [k for k in _user_ts if k[2] in {"G", "L"}]
    assert leaked == [], f"cascade left orphaned _user_ts entries: {leaked}"


def test_cascade_delete_drops_vintage_bounds_of_every_component_it_removes(seeded):
    apply_delete_bus_cascade("B0")
    assert _bounds_for(seeded, "Generator", "G") is None, (
        "cascade left stale vintage bounds for a generator it removed"
    )


def test_recreated_component_does_not_inherit_the_deleted_profile(seeded):
    # The failure the cleanup exists to prevent, stated end to end.
    apply_delete_bus_cascade("B0")
    n = PyPSAService.get_network()
    n.add("Bus", "B0")
    n.add("Generator", "G", bus="B0", p_nom=100.0, carrier="solar")
    with _user_ts_lock:
        assert ("generators", "p_max_pu", "G") not in _user_ts, (
            "a new generator reusing the name inherited the deleted one's profile"
        )

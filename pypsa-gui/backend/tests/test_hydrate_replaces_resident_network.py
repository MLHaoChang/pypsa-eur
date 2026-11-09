"""
Hydrating a context that already holds a network replaces that network.

WHAT THIS PINS. `solve_queue._apply_capture` saves a queued solve and then
re-hydrates the RESIDENT context from disk (`_hydrate_context_from_disk`).
PyPSA's `import_from_netcdf` into a populated network skips every component
it already holds ("already defined and will be skipped") and keeps the old
static row, while it takes the new time series. The resident then held the
solve's dispatch (`links_t.p0`) next to its pre-solve outputs (`p_nom_opt`
0): an extendable electrolyser dispatching 11.1 MW on a capacity of 0, and
the H2 hub's utilisation reading 1.23 (study harness 11). Every other
import path resets the network first (`load_project`, `network_undo`).

Written before the fix and proven red against it.
"""

from __future__ import annotations

import logging

import pandas as pd
import pypsa
import pytest

from routers.projects import _hydrate_context_from_disk, _save_context
from services.project_context import ProjectContext


def _network(*, extra: bool = False) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=3, freq="h"))
    n.add("Bus", "b")
    n.add("Load", "d", bus="b", p_set=[5.0, 7.0, 6.0])
    n.add(
        "Generator",
        "g",
        bus="b",
        p_nom_extendable=True,
        capital_cost=1.0,
        marginal_cost=1.0,
    )
    if extra:
        n.add("Generator", "only_in_memory", bus="b", p_nom=1.0)
    return n


def _solved_on_disk(tmp_projects_dir, name="Hub"):
    solved = _network()
    logging.disable(logging.WARNING)
    try:
        solved.optimize(solver_name="highs", solver_options={"output_flag": False})
    finally:
        logging.disable(logging.NOTSET)
    assert float(solved.generators.at["g", "p_nom_opt"]) == 7.0  # fixture precondition
    ctx = ProjectContext(network=solved)
    ctx.loaded_project = name
    _save_context(ctx, name, expect=name)
    return tmp_projects_dir / name


def test_a_resident_context_takes_the_saved_solves_static_outputs(tmp_projects_dir):
    src = _solved_on_disk(tmp_projects_dir)
    resident = ProjectContext(network=_network())  # the pre-solve network, p_nom_opt 0

    _hydrate_context_from_disk(resident, src, "Hub")

    n = resident.network
    assert float(n.generators.at["g", "p_nom_opt"]) == 7.0, (
        "the resident kept its pre-solve p_nom_opt next to the solve's dispatch"
    )
    assert float(n.generators_t.p["g"].max()) == 7.0


def test_a_resident_context_holds_only_the_saved_components(tmp_projects_dir):
    src = _solved_on_disk(tmp_projects_dir)
    resident = ProjectContext(network=_network(extra=True))

    _hydrate_context_from_disk(resident, src, "Hub")

    assert list(resident.network.generators.index) == ["g"]


def test_a_failed_import_leaves_the_residents_network_as_it_was(
    tmp_projects_dir, monkeypatch
):
    """GK B (1): the new network is built in full before the swap."""
    import pytest

    from services.pypsa_service import PyPSAService

    src = _solved_on_disk(tmp_projects_dir)
    before = _network()
    resident = ProjectContext(network=before)

    def broken(n, path):
        n.add("Bus", "half_imported")
        raise OSError("disk went away")

    monkeypatch.setattr(
        PyPSAService, "import_network_from_netcdf", staticmethod(broken)
    )
    with pytest.raises(OSError):
        _hydrate_context_from_disk(resident, src, "Hub")
    assert resident.network is before
    assert list(resident.network.buses.index) == ["b"]


def test_the_name_and_the_series_store_survive_the_swap(tmp_projects_dir):
    """GK B (3): what netCDF does not carry. The network takes the project's
    name; the user series store is the context's own object, refilled from
    the sidecar; the solve's transient marks live on the context, untouched."""
    src = _solved_on_disk(tmp_projects_dir)
    resident = ProjectContext(network=_network())
    store = resident.user_ts
    resident.transient_rows["Generator"] = {"g@2030"}
    marks = resident.transient_rows

    _hydrate_context_from_disk(resident, src, "Hub")

    assert resident.network.name == "Hub" and resident.loaded_project == "Hub"
    assert resident.user_ts is store
    assert resident.transient_rows is marks and marks == {"Generator": {"g@2030"}}


def test_the_swap_clears_the_caches_keyed_by_the_old_networks_id(tmp_projects_dir):
    """GK B (39) F1: the freed old network's `id` may be reused by a later
    network, so the swap drops the id-keyed derived results, as every other
    swap does (`PyPSAService._clear_swap_caches`)."""
    from services import time_aggregation_service as tas

    src = _solved_on_disk(tmp_projects_dir)
    resident = ProjectContext(network=_network())
    key = (id(resident.network), 2030, "cfg")
    with tas._cache_lock:
        tas._cache[key] = object()
    try:
        _hydrate_context_from_disk(resident, src, "Hub")
        with tas._cache_lock:
            assert key not in tas._cache
    finally:
        with tas._cache_lock:
            tas._cache.pop(key, None)


def test_a_captured_queued_solve_on_the_resident_project_shows_its_solved_capacity(
    client, install_network, tmp_projects_dir, session_ctx, monkeypatch
):
    """GK B (4), end to end: the open project's queued solve is captured (as
    an envelope-admitted run's is, HH-10), solved apart and written back;
    then its live network and the asset economics view read the solve's
    p_nom_opt, never the pre-solve 0."""
    from services import solve_capture
    from tests.conftest import build_network
    from tests.test_solve_queue import _save_project, _wait_for_terminal

    n = build_network()
    n.generators.at["gas", "p_nom_extendable"] = (
        True  # 100 MW of load less 30 MW of solar
    )
    n.generators.at["gas", "p_nom_max"] = (
        500.0  # a finite LP bound (the solve preflight refuses inf)
    )
    install_network(n, name="P1")
    _save_project(client, "P1")
    resident = session_ctx(client)
    assert float(resident.network.generators.at["gas", "p_nom_opt"]) == 0.0

    monkeypatch.setattr(solve_capture, "request", lambda: {"ctx": resident})
    job = client.post("/api/simulation/queue", json={"project_id": "P1"}).json()
    done = _wait_for_terminal(job["id"])
    assert done["status"] == "completed" and done["capture"]["applied"] is True, (
        done.get("capture")
    )

    live = session_ctx(client)
    assert live is resident
    assert float(live.network.generators.at["gas", "p_nom_opt"]) == pytest.approx(70.0)
    r = client.get("/api/results/asset_economics")
    assert r.status_code == 200, r.text
    rows = [a for a in r.json()["generators"] if a["name"] == "gas"]
    assert rows and rows[0]["p_nom_opt_mw"] == pytest.approx(70.0), rows

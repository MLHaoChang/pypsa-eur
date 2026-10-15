"""The PoC Link's state (H4): an open PoC (the island's normally open subsea
tie) has no grid in operation, so the Investment detail hides the connection,
tariff and export fields. Read-only."""
from __future__ import annotations

import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

from routers.simulation import poc_state  # noqa: E402


def test_the_islands_open_tie_is_an_open_poc():
    n = T.BUILDERS["eh_microgrid_staged"]()
    com = T.solver_config("eh_microgrid_staged")["commercial"]
    assert poc_state(n, com) == {"poc_link": "subsea_tie", "open": True}


def test_a_grid_connected_poc_is_not_open():
    n = T.BUILDERS["eh_h2_hub_staged"]()
    com = T.solver_config("eh_h2_hub_staged")["commercial"]
    assert poc_state(n, com) == {"poc_link": "grid_import", "open": False}


def test_a_tie_that_opens_only_some_hours_is_not_open():
    n = T.BUILDERS["eh_microgrid_staged"]()
    com = T.solver_config("eh_microgrid_staged")["commercial"]
    n.links_t.p_max_pu["subsea_tie"] = 0.0
    n.links_t.p_max_pu.iloc[0, n.links_t.p_max_pu.columns.get_loc("subsea_tie")] = 1.0
    assert poc_state(n, com)["open"] is False


def test_no_commercial_config_or_an_unknown_link_is_not_open():
    n = T.BUILDERS["eh_microgrid_staged"]()
    assert poc_state(n, None) == {"poc_link": None, "open": False}
    assert poc_state(n, {"poc_link": "nope"}) == {"poc_link": "nope", "open": False}


def test_the_route_reads_the_resident_network(client, reset_backend):
    import routers.simulation as sim_router
    from routers.projects import _solver_config_from_dict
    from tests.conftest import install_network_into_backend

    install_network_into_backend(T.BUILDERS["eh_microgrid_staged"]())
    sim_router._state["solver_config"] = _solver_config_from_dict(
        T.solver_config("eh_microgrid_staged"))
    r = client.get("/api/simulation/commercial/poc")
    assert r.status_code == 200, r.text
    assert r.json() == {"poc_link": "subsea_tie", "open": True}


def test_the_route_never_waits_on_a_running_solve(client, reset_backend, monkeypatch):
    """Gatekeeper review of #314: a solve holds the network lock; the read
    answers "unknown" within its short wait instead of hanging."""
    import threading
    import time

    import routers.simulation as sim_router
    from routers.projects import _solver_config_from_dict
    from services.pypsa_service import PyPSAService
    from tests.conftest import install_network_into_backend

    install_network_into_backend(T.BUILDERS["eh_microgrid_staged"]())
    sim_router._state["solver_config"] = _solver_config_from_dict(
        T.solver_config("eh_microgrid_staged"))
    monkeypatch.setattr(sim_router, "_POC_LOCK_WAIT_S", 0.5)
    held, done = threading.Event(), threading.Event()

    def solve():
        with PyPSAService.get_lock():
            held.set()
            done.wait(10)

    t = threading.Thread(target=solve)
    t.start()
    try:
        assert held.wait(5)
        start = time.monotonic()
        r = client.get("/api/simulation/commercial/poc")
        assert time.monotonic() - start < 3.0
        assert r.status_code == 200
        assert r.json() == {"poc_link": "subsea_tie", "open": None, "reason": "network_busy"}
    finally:
        done.set()
        t.join()

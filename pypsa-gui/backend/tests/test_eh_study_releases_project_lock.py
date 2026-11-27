"""
template-e2e 12 (b): a running energy-hub study leaves the project usable.

The study solves its own private copy of the network, so it takes the
project's mutation lock only to make that copy. Before this fix it passed the
shared lock to every solve, and ``run_simulation`` holds the lock it is given
for the whole build, solve and restore: on the mining concentrator that was
30 minutes in which every edit, autosave and project delete waited, the
request threadpool filled with them, and the Abort request queued behind.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa

from models.energy_hub import AvailabilityTarget, default_strong_grid_pack
from services import solver_service
from services.adequacy import eh_study as S
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "cheap", bus="b", carrier="gas", p_nom=60.0, marginal_cost=10.0)
    n.add("Generator", "backup", bus="b", carrier="gas", p_nom=40.0, marginal_cost=200.0)
    return n


def _pack():
    return default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
    })


def _probe_lock_from_other_thread(lock) -> bool:
    """Whether another thread (a request worker) can take ``lock`` now."""
    got: list[bool] = []

    def _try() -> None:
        ok = lock.acquire(timeout=1.0)
        if ok:
            lock.release()
        got.append(ok)

    t = threading.Thread(target=_try)
    t.start()
    t.join(5.0)
    return bool(got and got[0])


def test_study_solve_does_not_hold_the_project_mutation_lock(monkeypatch):
    n = _network()
    PyPSAService.set_network(n)
    shared = PyPSAService.get_lock()
    real = solver_service.run_simulation
    seen: dict = {}

    def _spy(cfg, network, lock, *args, **kwargs):
        def _inside(*a, **kw):
            seen["shared_free_during_solve"] = _probe_lock_from_other_thread(shared)
            seen["solved_shared_network"] = network is n
            return real(cfg, network, lock, *args, **kwargs)
        # Hold whichever lock the study hands the solve, as run_simulation does.
        with lock:
            return _inside()

    monkeypatch.setattr(solver_service, "run_simulation", _spy)
    report = S.run_eh_study(
        n, _pack(), SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=shared, stop_event=threading.Event(), log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "assemble"),
    )
    assert report is not None
    assert seen["solved_shared_network"] is False
    assert seen["shared_free_during_solve"] is True


def test_abort_during_solve_still_stops_the_study(monkeypatch):
    """The private lock changes nothing for Abort: a stop set during the
    solve still ends the study as aborted, and the shared lock is free."""
    n = _network()
    PyPSAService.set_network(n)
    shared = PyPSAService.get_lock()
    stop = threading.Event()
    real = solver_service.run_simulation

    def _spy(cfg, network, lock, stop_event, *args, **kwargs):
        stop.set()  # the user's Abort lands while the solve is starting
        return real(cfg, network, lock, stop_event, *args, **kwargs)

    monkeypatch.setattr(solver_service, "run_simulation", _spy)
    report = S.run_eh_study(
        n, _pack(), SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=shared, stop_event=stop, log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "assemble"),
    )
    assert report.pipeline.aborted is True
    assert _probe_lock_from_other_thread(shared) is True


def test_delete_refuses_while_the_project_runs_a_study(client, api_project,
                                                        project_storage_dir,
                                                        session_state):
    """With the lock free during a study, delete no longer waits on it: it
    answers at once, and refuses rather than deleting the project the study
    will publish into."""
    from tests.test_adequacy_study_swap_guard import _LiveStudy

    name = api_project("studying")
    pdir = project_storage_dir(name)
    with _LiveStudy(session_state(client), key="eh_study"):
        resp = client.delete(f"/api/projects/{name}")
        assert resp.status_code == 409, resp.text
        assert "abort" in resp.text.lower()
        assert pdir.exists()
    assert client.delete(f"/api/projects/{name}").status_code == 200

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
import time

import pandas as pd
import pypsa
import pytest

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
    n.add(
        "Generator", "backup", bus="b", carrier="gas", p_nom=40.0, marginal_cost=200.0
    )
    return n


def _pack():
    return default_strong_grid_pack().model_copy(
        update={
            "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
        }
    )


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
        n,
        _pack(),
        SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=shared,
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
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
        n,
        _pack(),
        SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=shared,
        stop_event=stop,
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "assemble"),
    )
    assert report.pipeline.aborted is True
    assert _probe_lock_from_other_thread(shared) is True


def test_delete_refuses_while_the_project_runs_a_study(
    client, api_project, project_storage_dir, session_state
):
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
        assert (pdir / "network.nc").exists()
        listed = [p["name"] for p in client.get("/api/projects/").json()]
        assert name in listed, "the refusal must come before the DB row is touched"
    assert client.delete(f"/api/projects/{name}").status_code == 200


def test_delete_of_a_project_with_no_resident_context_is_not_held_up(
    client, api_project, project_storage_dir
):
    """C4: a project no context holds has no study to wait for or refuse."""
    name = api_project("cold")
    pdir = project_storage_dir(name)
    key = next(
        k
        for k, c in PyPSAService._contexts.items()
        if getattr(c, "loaded_project", None) == name
    )
    PyPSAService.drop(key)
    assert PyPSAService.get_context(key) is None
    resp = client.delete(f"/api/projects/{name}")
    assert resp.status_code == 200, resp.text
    assert not pdir.exists()


# ── Through the routes: a real study whose solve blocks (GK A C2, C3, C5) ───

STUDY_URL = "/api/results/eh_study"
ABORT_URL = "/api/results/eh_study/abort"
STAGES = {
    "archetype": "weak_flexible",
    "stages": ["apply_pack", "ens_solve", "assemble"],
}


class _BlockedSolve:
    """Replace the study's solve with one that holds the lock it is given, as
    ``run_simulation`` does, until Abort (``stop_event``) or ``release``; on
    release it runs the real solve, so the study can finish ``done``."""

    def __init__(self, monkeypatch):
        self.solving = threading.Event()
        self.release = threading.Event()
        self.real = solver_service.run_simulation
        monkeypatch.setattr(solver_service, "run_simulation", self)

    def __call__(self, cfg, network, lock, stop_event, *args, **kwargs):
        with lock:
            self.solving.set()
            for _ in range(1500):  # 30 s cap: a hung test fails, it does not hang
                if stop_event.is_set() or self.release.is_set():
                    break
                time.sleep(0.02)
        if stop_event.is_set():
            return "aborted", "user_aborted:native_solve"
        return self.real(cfg, network, lock, stop_event, *args, **kwargs)


def _poll(client, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    body: dict = {}
    while time.time() < deadline:
        body = client.get(STUDY_URL).json()
        if body.get("status") != "running":
            return body
        time.sleep(0.05)
    raise AssertionError(f"eh_study never finished: {body!r}")


@pytest.fixture
def blocked_study(client, install_network, monkeypatch, session_state):
    """A saved hub project whose EH study is inside its (blocked) solve."""
    from tests.test_eh_study_record_survives_reactivation import hub_project

    hub_project(client, install_network, "blocked-study")
    solve = _BlockedSolve(monkeypatch)
    r = client.post(STUDY_URL, json=STAGES)
    assert r.status_code == 200, r.text
    assert solve.solving.wait(30), "the study never reached its solve"
    yield solve
    client.post(ABORT_URL)
    solve.release.set()
    t = (session_state(client).get("eh_study") or {}).get("thread")
    if t is not None:
        t.join(30)


def _edit_bus(client, dx: float = 0.001):
    bus = client.get("/api/network/buses").json()[0]
    return client.put(
        f"/api/network/buses/{bus['name']}",
        json={**bus, "x": float(bus.get("x") or 0.0) + dx},
    )


def test_abort_answers_while_edits_are_in_flight(blocked_study, client, session_ctx):
    """C3: with edits arriving during the solve, they complete, the abort
    route answers within seconds and the study ends aborted, lock free."""
    results: list[int] = []

    def _edit(i: int) -> None:
        results.append(_edit_bus(client, dx=0.001 * (i + 1)).status_code)

    edits = [threading.Thread(target=_edit, args=(i,)) for i in range(8)]
    for t in edits:
        t.start()
    for t in edits:
        t.join(15)
    assert results == [200] * 8, results

    t0 = time.monotonic()
    r = client.post(ABORT_URL)
    assert r.status_code == 200, r.text
    assert time.monotonic() - t0 < 3.0
    assert r.json() == {"status": "running", "aborting": True}
    assert _poll(client)["status"] == "aborted"
    assert _probe_lock_from_other_thread(session_ctx(client).mutation_lock) is True


def test_lock_status_reads_free_during_a_study(blocked_study, client):
    """C3: the project lock is free while the study solves, so the frontend's
    project-switch helper (``abortRunningSim``) no longer calls
    ``/simulation/abort`` for it; the study is stopped by its own route."""
    body = client.get("/api/simulation/lock_status").json()
    assert body["lock_held"] is False, body


def test_an_edit_during_the_study_marks_the_result_edited_since(blocked_study, client):
    """C2: a component edit completes at once and the finished study reads
    edited-since."""
    t0 = time.monotonic()
    assert _edit_bus(client).status_code == 200
    assert time.monotonic() - t0 < 5.0
    blocked_study.release.set()
    rec = _poll(client)
    assert rec["status"] == "done", rec
    assert rec["edited_since_study"] is True, rec


@pytest.mark.parametrize(
    "method, url, body",
    [
        ("POST", "/api/simulation/run", None),
        ("POST", "/api/results/fmea_sweep", {}),
        ("POST", "/api/results/frontier", {}),
        ("POST", "/api/results/mc", {}),
        ("POST", STUDY_URL, STAGES),
        ("POST", "/api/network/reset", None),
        ("POST", "/api/network/undo", None),
        ("POST", "/api/projects/from_template/eh_concentrator_staged", None),
        ("GET", "/api/projects/blocked-study", None),
    ],
)
def test_actions_the_lock_used_to_hold_off_are_refused(
    blocked_study, client, method, url, body
):
    """C2: what used to wait for the lock now answers at once with 409 from
    the study mesh or the swap guard; the study keeps running."""
    t0 = time.monotonic()
    r = client.request(method, url, json=body)
    assert time.monotonic() - t0 < 5.0
    assert r.status_code == 409, (url, r.status_code, r.text)
    assert client.get(STUDY_URL).json()["status"] == "running"


def test_snapshot_restore_is_refused(blocked_study, client):
    r = client.post("/api/projects/blocked-study/snapshots/missing/restore")
    assert r.status_code == 409, r.text


def test_the_live_input_fingerprint_neither_waits_nor_reads_the_copy(
    client, install_network, monkeypatch, session_state, session_ctx
):
    """C5: the solve registers its private copy's inputs under its own lock.
    The live project's input fingerprint, read during the study, answers at
    once and is the live network's, unchanged by the study's pack overlay."""
    from services.workflow_tools import _network_input_fingerprint, _solve_snapshot
    from tests.test_eh_study_record_survives_reactivation import hub_project

    hub_project(client, install_network, "fingerprint")
    ctx = session_ctx(client)
    before = _network_input_fingerprint(ctx)

    seen: dict = {}
    real = solver_service.run_simulation

    def _spy(cfg, network, lock, stop_event, *args, **kwargs):
        def _probe(**kw):
            if "fp" not in seen:
                out: list = []
                t = threading.Thread(
                    target=lambda: out.append(_network_input_fingerprint(ctx))
                )
                t.start()
                t.join(5.0)
                seen["fp"] = out[0] if out else None
                seen["live_snapshot"] = _solve_snapshot(ctx.network)
            if outer is not None:
                outer(**kw)

        outer = kwargs.pop("state_update", None)
        return real(
            cfg, network, lock, stop_event, *args, state_update=_probe, **kwargs
        )

    monkeypatch.setattr(solver_service, "run_simulation", _spy)
    assert client.post(STUDY_URL, json=STAGES).status_code == 200
    rec = _poll(client)
    assert rec["status"] in ("done", "failed"), rec
    assert seen.get("fp") == before
    assert seen.get("live_snapshot") is None

"""
Study records must belong to the NETWORK they measured (Phase 10).

Every adequacy study — the FMEA sweep, the frontier, the sequential MC and the
two planning loops — publishes a record into the active project's
``solver_state`` under its own key, and those records are what
``GET /results/{study}`` serves back. NOTHING clears them:

* ``services/study_state.STUDY_KEYS`` is referenced in exactly one place (the
  409 mutual-exclusion mesh), never to reset;
* ``PyPSAService.reset_network`` — which runs at the START of every load,
  import and restore, and on "New" — carries the SAME ``solver_state`` dict
  object forward onto a brand-new network;
* ``load_project`` re-hydrates ``solver_config`` and ``RESULT_STATE_KEYS``, and
  the study keys are in NEITHER list.

So a study record outlives the network it describes, and the panel that
renders it cannot tell. This is not hypothetical: QA round 7 found a stored
168 h MC study answering a horizon question for a live 48 h network, which put
a 3.5× wrong reliability standard on the wire. That conversion was fixed at
the read site; the record scoping was not, and every other consumer is still
exposed.

Driven over the REAL HTTP stack, because the leak lives in the interaction
between the project routes and the results routes — calling either in
isolation cannot see it.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services.project_context import STUDY_KEYS

MC_URL = "/api/results/mc"


def _served_marker(client) -> str | None:
    """The marker of whatever study record `GET /results/mc` serves, if any.

    A cleared record makes the route answer "no study" — 204, or a JSON null —
    and both are the SAME answer as far as this file is concerned: the panel
    has nothing to render. Only a body carrying a marker is a leak.
    """
    r = client.get(MC_URL)
    if r.status_code == 204 or not (r.content or b"").strip():
        return None
    body = r.json()
    if not isinstance(body, dict):
        return None
    return ((body.get("result") or {}) or {}).get("marker")


def _network(load_mw: float, periods: int) -> pypsa.Network:
    """A samplable one-bus network whose SIZE is visible in any study of it."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=periods, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC", country="AA")
    n.add("Load", "l", bus="b", p_set=load_mw)
    n.add("Generator", "firm", bus="b", carrier="gas", p_nom=load_mw * 0.8,
          marginal_cost=10.0, outage_rate_value=0.1,
          outage_rate_basis="EFORd", mttr_hours=4.0)
    return n


def _seed_record(key: str, marker: str) -> None:
    """Publish a finished study record the way a worker thread would."""
    from services.pypsa_service import PyPSAService
    with PyPSAService.get_solver_state_lock():
        PyPSAService.get_solver_state()[key] = {
            "status": "done", "result": {"marker": marker},
            "error": None, "started_at": 1.0, "finished_at": 2.0,
            "thread": None,
        }


def test_a_study_record_does_not_survive_a_project_load(
        client, install_network, api_project):
    """★ Project A's finished MC study must not be served for project B.

    Bite (verified): remove the study-key reset from the load path.
    """
    api_project("study_scope_a")
    _seed_record("mc", "PROJECT_A")

    # A different project, loaded the way the UI loads one: `GET
    # /api/projects/{name}` IS the load (routers/projects.py::load_project).
    api_project("study_scope_b")
    r = client.get("/api/projects/study_scope_b")
    assert r.status_code == 200, r.text

    assert _served_marker(client) != "PROJECT_A", (
        "project B is being served project A's MC study")


def test_a_study_record_does_not_survive_a_reset_to_a_new_network(
        client, install_network):
    """★ "New" hands back an empty network — and, today, a finished study of
    the network that is gone.

    `reset_network` carries the solver_state dict forward verbatim, so the
    Adequacy tab renders LOLE and EUE for a network with no assets in it.

    Bite (verified): drop the study-key reset from `reset_network`.
    """
    install_network(_network(100.0, 24))
    _seed_record("mc", "OLD_NETWORK")

    from services.pypsa_service import PyPSAService
    PyPSAService.reset_network()

    assert _served_marker(client) != "OLD_NETWORK", (
        "a study of the discarded network survived the reset")


@pytest.mark.parametrize("key", STUDY_KEYS)
def test_every_study_key_is_cleared_not_just_the_one_that_was_noticed(
        client, install_network, key):
    """★ A4: all five studies, parametrized over STUDY_KEYS ITSELF.

    The MC is the study the defect was noticed on, and fixing only `mc` would
    leave the frontier, the FMEA sweep and BOTH planning loops leaking — the
    loops worst of all, since their records carry a certified value the user
    is told to type into solver settings. Driving the parametrization off the
    real tuple means a sixth study added later is covered by construction
    rather than by someone remembering this file exists.

    Bite (verified): clear only `state["mc"]`.
    """
    install_network(_network(100.0, 24))
    _seed_record(key, "OLD_NETWORK")

    from services.pypsa_service import PyPSAService
    PyPSAService.reset_network()

    assert PyPSAService.get_solver_state().get(key) is None, (
        f"the {key} record survived the network swap")


def test_a_RUNNING_study_is_deliberately_left_alone(client, install_network):
    """★ A3: the one record `reset_network` must NOT clear, pinned.

    Clearing a live record would make `study_running()` False while the worker
    thread is still alive and still mutating a network — breaking the 409 mesh
    and admitting a SECOND study, which is the exact corruption the mesh
    exists to prevent. Leaking it keeps the mutex honest and leaves the panel
    reading "running" instead of showing a fabricated result.

    Phase 11 made the user-facing case unreachable: `reset_network` now
    REFUSES a swap while a study is live (409), because the worker closes over
    the network object, so a load detaches the study rather than stopping it.
    This path therefore only remains reachable through the explicit
    `allow_during_study=True` opt-out — and the no-clear rule still has to
    hold there, which is what this pins.

    This test exists so a later tidy-up cannot quietly turn the conditional
    clear into an unconditional one.

    Bite (verified): drop the `record_is_running` guard from the clear loop.
    """
    import threading

    from services.pypsa_service import PyPSAService
    from services.study_state import study_running

    release = threading.Event()
    t = threading.Thread(target=release.wait, daemon=True, name="fake-study")
    t.start()
    try:
        install_network(_network(100.0, 24))
        with PyPSAService.get_solver_state_lock():
            PyPSAService.get_solver_state()["mc"] = {
                "status": "running", "result": None, "error": None,
                "started_at": 1.0, "finished_at": None, "thread": t,
            }
        assert study_running("mc") is True, "the fixture is not actually live"

        PyPSAService.reset_network(allow_during_study=True)

        assert PyPSAService.get_solver_state().get("mc") is not None, (
            "a LIVE study record was cleared: the 409 mesh now reads False "
            "while the worker thread is still running and still mutating a "
            "network, which admits a second study")
        assert study_running("mc") is True, (
            "the mesh no longer sees the running study it must refuse for")
    finally:
        release.set()
        t.join(timeout=5)
        PyPSAService.get_solver_state().pop("mc", None)


def test_the_409_mesh_still_names_the_study_it_refuses_for(
        client, install_network):
    """★ A5: the Phase-7 guarantee, unchanged by §2.1's predicate refactor.

    `study_running` now delegates to `project_context.record_is_running`. That
    is one definition instead of two, which is the point — but a refactor of a
    guard has to prove the guard still guards.

    Bite (verified): make `record_is_running` return False unconditionally.
    """
    import threading

    from services.pypsa_service import PyPSAService
    from services.study_state import blocking_study_detail, running_study

    release = threading.Event()
    t = threading.Thread(target=release.wait, daemon=True, name="fake-study")
    t.start()
    try:
        install_network(_network(100.0, 24))
        with PyPSAService.get_solver_state_lock():
            PyPSAService.get_solver_state()["coupling_loop"] = {
                "status": "running", "result": None, "error": None,
                "started_at": 1.0, "finished_at": None, "thread": t,
            }
        assert running_study() == "coupling_loop"
        detail = blocking_study_detail()
        assert detail and "coupling-loop study" in detail, detail

        # …and the refusal reaches a real caller over HTTP.
        r = client.post("/api/simulation/run")
        assert r.status_code == 409, r.text
        assert "coupling-loop study" in r.json()["detail"], r.text
    finally:
        release.set()
        t.join(timeout=5)
        PyPSAService.get_solver_state().pop("coupling_loop", None)


def test_a_study_run_AFTER_a_swap_does_not_leak_back_to_the_old_project(
        client, install_network, api_project, session_state):
    """★ BLOCKER 5: Phase 10's clear fixed one direction and not the other.

    `reset_network` carries `solver_state` forward as the SAME dict object,
    and Phase 10's clear runs once, at swap time. So it clears what was there
    THEN — and anything written AFTER the swap lands in a dict the outgoing
    project's context is still pointing at.

    Reproduced over HTTP: load project A, press "New", run a study on the
    fresh network, then re-activate A — and A is served the scratch network's
    study. That is byte-for-byte Phase 10's headline symptom ("project A's
    finished MC study was served for project B"), still live with the order
    reversed.

    The fix is that the fresh context gets its OWN state dict. Safe because
    `simulation._state` is `_ActiveStateProxy`, which resolves through
    `PyPSAService.get_solver_state()` on every access rather than holding a
    bound reference — so nothing depends on the dict's identity surviving a
    swap.

    Bite (verified): carry `prev.solver_state` itself instead of a copy.
    """
    name = api_project("leak_probe_project")
    assert client.get(f"/api/projects/{name}").status_code == 200
    state_before = session_state(client)

    assert client.post("/api/network/reset").status_code == 200
    state_after = session_state(client)
    assert state_after is not state_before, (
        "the fresh context still ALIASES the outgoing project's state dict")

    # A study belonging to the scratch network, written the way a worker would.
    state_after["mc"] = {
        "status": "done", "result": {"marker": "SCRATCH_ONLY"},
        "error": None, "started_at": 1.0, "finished_at": 2.0, "thread": None,
    }
    assert _served_marker(client) == "SCRATCH_ONLY", "the fixture never took"

    assert client.post(f"/api/projects/{name}/activate").status_code == 200
    assert _served_marker(client) != "SCRATCH_ONLY", (
        "project A is being served a study of the network that replaced it")

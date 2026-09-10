"""
The study mutual-exclusion mesh is a CLAIM, not a check.

Whole-branch review (2026-09-08), findings S2, S3, S4 and M1. Five study
POSTs (`fmea_sweep`, `frontier`, `mc`, `coupling_loop`, `margin_loop`) and
the two foreground solve entrypoints (`/simulation/run`, `/run_ac_pf`) share
one foreground network. Before this file:

* S4 — the study POSTs tested the solve by its status STRING. `/abort` flips
  it to `"aborted"` while the worker keeps running (restore phase, or HiGHS
  refusing to yield), so a study could start on a network whose LP
  transforms were still being reverted. Preflight, save and activate all use
  `_solver_in_flight()`; the studies did not. Reachable with two clicks.
* S2 — the study gates ran with NO lock and the publish ran under one: two
  POSTs close together both passed the gates, the second overwrote the
  first's record, and the first worker was orphaned — unabortable and
  invisible to every guard.
* S3 — `/run` checked the study mesh outside its claim lock and the studies
  checked the solve outside theirs: a `/run` and a study POST arriving
  together were BOTH admitted.
* M1 — the record was published before `t.start()`; a `start()` that raised
  left a never-started thread that `record_is_running` counts as running for
  the rest of the process.

What ships: one predicate (`_study_mesh_blocker`) called early AND inside
the publish hold (`_publish_study`), `/run` and `/run_ac_pf` re-checking the
mesh inside their claim hold, and a rollback when `start()` raises.

Bites (each verified red before the fix, or against the named removal):
* S4 — put the status-string check back: the first test admits a study
  while the aborted worker is alive (200, expected 409).
* S2 — drop the re-check inside `_publish_study`: the race test reads
  `[200, 200]`.
* S3 — drop the re-check inside `/run`'s claim: both a `/run` and a study
  are admitted.
* M1 — drop the rollback: the surface is wedged after a failed `start()`.
"""
from __future__ import annotations

import contextvars
import dataclasses
import threading
import time

import pytest

import routers.results as RR
import routers.simulation as RS
from services import study_state as STUDY
from services.project_context import record_is_running
from tests.conftest import build_network

STUDY_POSTS = {
    "fmea_sweep": "/api/results/fmea_sweep",
    "frontier": "/api/results/frontier",
    "mc": "/api/results/mc",
    "coupling_loop": "/api/results/coupling_loop",
    "margin_loop": "/api/results/margin_loop",
}


def _voll(state):
    state["solver_config"] = dataclasses.replace(
        state["solver_config"], voll=1000.0)


def _drain(state):
    """Stop and join anything a test may have started, so a bite that admits
    a study cannot leak a worker into the next test."""
    for key in STUDY.STUDY_KEYS:
        rec = state.get(key) or {}
        ev = rec.get("stop_event")
        if ev is not None:
            ev.set()
        th = rec.get("thread")
        if th is not None and th.is_alive():
            th.join(timeout=10.0)
        state[key] = None
    th = state.get("thread")
    if th is not None and th.is_alive():
        ev = state.get("stop_event")
        if ev is not None:
            ev.set()
        th.join(timeout=10.0)
    state["status"] = "idle"
    state["thread"] = None


class _Barrier2:
    """A `copy_context` stand-in that makes two request threads rendezvous
    AFTER their early gates and BEFORE their publish/claim — the exact
    interleaving the review measured. Both parties must arrive before either
    proceeds; with a real check-then-act window both are then admitted."""

    def __init__(self, real, parties=2):
        self.barrier = threading.Barrier(parties, timeout=10.0)
        self.real = real

    def copy_context(self):
        try:
            self.barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return self.real.copy_context()


def _slow_sweep(*args, **kwargs):
    stop = kwargs.get("stop_event")
    for _ in range(30):
        if stop is not None and stop.is_set():
            break
        time.sleep(0.05)
    return [], {"base_restored": True, "base_restore_status": "ok"}


# ── S4 ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", list(STUDY_POSTS))
def test_study_refused_while_an_aborted_solver_worker_is_alive(
        client, install_network, session_state, monkeypatch, key):
    """★ S4. `status == "aborted"` with a live worker thread is the state
    `/abort` leaves for seconds (restore) or forever (stuck native solve).
    Preflight answers `deferred_stuck`; every study POST must answer 409 —
    the gate is `_solver_in_flight()`, not the status string.

    Bite (verified): restore `_state.get("status") == "running"` as the
    solve gate — the POST answers 200 and starts the study.
    """
    install_network(build_network())
    st = session_state(client)
    _voll(st)
    import services.adequacy.sweep as SW
    monkeypatch.setattr(SW, "run_class_b_sweep", _slow_sweep)

    release = threading.Event()
    worker = threading.Thread(target=release.wait, kwargs={"timeout": 10.0},
                              daemon=True, name="stuck-solver")
    worker.start()
    st["status"] = "aborted"
    st["thread"] = worker
    try:
        # Through the route, which resolves the SESSION context this state
        # belongs to (a direct `_solver_in_flight()` call from the test
        # thread resolves the active project instead).
        pre = client.post("/api/simulation/preflight").json()
        assert pre.get("deferred") is True and pre.get("deferred_stuck") is True
        r = client.post(STUDY_POSTS[key], json={})
        assert r.status_code == 409, (key, r.status_code, r.text)
        assert "abort" in r.json()["detail"].lower()
        assert not record_is_running(st.get(key))
    finally:
        release.set()
        _drain(st)


def test_mesh_sentences_name_the_running_study(client, install_network,
                                                session_state):
    """The refusal copy the frontend toasts verbatim is unchanged by the
    refactor: the study's own label, and "already running" for itself."""
    install_network(build_network())
    st = session_state(client)
    release = threading.Event()
    t = threading.Thread(target=release.wait, kwargs={"timeout": 10.0},
                         daemon=True)
    t.start()
    st["mc"] = {"status": "running", "thread": t, "stop_event": release}
    try:
        r = client.post("/api/results/frontier", json={})
        assert r.status_code == 409
        assert r.json()["detail"] == "a sequential-MC study is running — wait for it to finish"
        r = client.post("/api/results/mc", json={})
        assert r.status_code == 409
        assert r.json()["detail"] == "a sequential-MC study is already running"
    finally:
        release.set()
        _drain(st)


# ── S2 ────────────────────────────────────────────────────────────────────

def test_two_study_posts_racing_admit_exactly_one(client, install_network,
                                                   session_state, monkeypatch):
    """★ S2. Two `POST /fmea_sweep` rendezvous after their early gates and
    race to publish. Exactly one may win: the loser must read the winner's
    record INSIDE the publish hold and answer 409, and exactly one worker
    thread may exist afterwards.

    Bite (verified): drop `_refuse_if_mesh_busy(key)` from `_publish_study`
    — both answer 200 and two `fmea-sweep` workers are alive, the first of
    them orphaned.
    """
    install_network(build_network())
    st = session_state(client)
    _voll(st)
    import services.adequacy.sweep as SW
    monkeypatch.setattr(SW, "run_class_b_sweep", _slow_sweep)
    monkeypatch.setattr(RR, "_contextvars", _Barrier2(contextvars))

    out: dict[int, object] = {}

    def go(i):
        out[i] = client.post("/api/results/fmea_sweep", json={})

    threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20.0)
        codes = sorted(r.status_code for r in out.values())
        alive = [t for t in threading.enumerate() if t.name == "fmea-sweep"]
        assert codes == [200, 409], (codes, [r.text for r in out.values()])
        assert len(alive) == 1, f"{len(alive)} sweep workers alive"
        loser = next(r for r in out.values() if r.status_code == 409)
        assert loser.json()["detail"] == "an FMEA sweep is already running"
    finally:
        _drain(st)


# ── S3 ────────────────────────────────────────────────────────────────────

def test_run_and_study_racing_admit_exactly_one(client, install_network,
                                                 session_state, monkeypatch):
    """★ S3. A `/simulation/run` and a `POST /fmea_sweep` rendezvous after
    their early gates. Each side now re-checks the OTHER inside its own
    claim hold, and the two holds are the same RLock, so exactly one wins.

    Bite (verified): drop the `blocking_study_detail()` re-check inside
    `/run`'s claim block — the study publishes between /run's early check
    and its claim, and both are admitted (`status == "running"` beside a
    live sweep record).
    """
    install_network(build_network())
    st = session_state(client)
    _voll(st)
    import services.adequacy.sweep as SW
    monkeypatch.setattr(SW, "run_class_b_sweep", _slow_sweep)

    def fake_run(config, n, lock, stop_event, log_queue, state_update=None,
                 **kwargs):
        for _ in range(30):
            if stop_event.is_set():
                break
            time.sleep(0.05)
        return "ok", "optimal"

    monkeypatch.setattr(RS, "run_simulation", fake_run)
    bar = _Barrier2(contextvars)
    monkeypatch.setattr(RR, "_contextvars", bar)
    monkeypatch.setattr(RS, "contextvars", bar)

    out: dict[str, object] = {}

    def a():
        out["run"] = client.post("/api/simulation/run")

    def b():
        out["sweep"] = client.post("/api/results/fmea_sweep", json={})

    threads = [threading.Thread(target=a), threading.Thread(target=b)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20.0)
        codes = sorted(r.status_code for r in out.values())
        assert codes == [200, 409], {k: (v.status_code, v.text[:120])
                                     for k, v in out.items()}
        # And the state agrees with the answers: never BOTH running.
        both = (st.get("status") == "running"
                and record_is_running(st.get("fmea_sweep")))
        assert not both
    finally:
        _drain(st)


# ── M1 ────────────────────────────────────────────────────────────────────

def test_a_failed_thread_start_rolls_the_record_back(client, install_network,
                                                      session_state, monkeypatch):
    """★ M1. If `Thread.start()` raises (thread limit), the record must not
    stay published: `record_is_running` counts `ident is None` as running by
    design, so a leaked record would 409 every guarded route for the rest of
    the process with nothing able to clear it.

    Bite (verified): drop the `except: _state[key] = None; raise` — the
    second POST and `/run` answer 409 forever.
    """
    install_network(build_network())
    st = session_state(client)
    _voll(st)
    import services.adequacy.sweep as SW
    monkeypatch.setattr(SW, "run_class_b_sweep", _slow_sweep)

    real_start = threading.Thread.start
    armed = {"on": True}

    def failing_start(self):
        if armed["on"] and self.name == "fmea-sweep":
            armed["on"] = False
            raise RuntimeError("can't start new thread")
        return real_start(self)

    monkeypatch.setattr(threading.Thread, "start", failing_start)
    try:
        # The TestClient re-raises server exceptions; the wire would be 500.
        with pytest.raises(Exception):
            client.post("/api/results/fmea_sweep", json={})
        assert not record_is_running(st.get("fmea_sweep")), "record leaked"
        assert st.get("fmea_sweep") is None
        # The surface is free again: the next POST is admitted.
        r2 = client.post("/api/results/fmea_sweep", json={})
        assert r2.status_code == 200, r2.text
    finally:
        _drain(st)

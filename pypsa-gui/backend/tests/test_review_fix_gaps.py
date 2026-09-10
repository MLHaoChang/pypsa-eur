"""
What the review of the seven fixes found the fixes had left open.

Three findings from the shipped-code review of the whole-branch fixes
(`notes/2026-09-08-whole-branch-e2e-review.md`, §6):

* F1 — the S1 refusal (`OutageRateError` from the one membership walk) was
  caught by `/copt`, `/mc` and both loops, and NOT by
  `GET /results/mc/elcc_candidates`, which let it out as a 500.
* F2 — S5 was only NARROWED: the save gate ran before `_save_context`
  took `ctx.mutation_lock`, and shared no lock with `_publish_study`, so a
  save that passed its gate, then paused before its export, could export a
  network a sweep had meanwhile started mutating lock-free (measured: the
  study's `p_nom` on every generator, on disk, as the user's project).
* F3 — S3 was only NARROWED: `POST /simulation/queue`, the Run button's
  real path, had no study gate at all; the dispatcher claimed the resident
  context and re-solved the network a live study was measuring, failing
  only at its post-solve save.

What ships: the route catches the walk's error; `_save_context` re-checks
the study INSIDE `ctx.mutation_lock` and `_publish_study` takes that same
lock outside the state lock, so a study publishes either before a save has
begun exporting or after it has finished; the enqueue route refuses when
the resident context has a live study, and the dispatcher refuses again
under the state lock at its claim.

Bites (verified red against the named removal):
* F1 — drop the `except ValueError` on the candidates route → 500;
* F2a — drop the in-lock re-check from `_save_context` → the held save
  exports the study's mutation (4321 on disk) and answers 200;
* F2b — drop `PyPSAService.get_lock()` from `_publish_study` → the study
  publishes while the save holds the mutation lock and its mutation lands
  on disk;
* F3 — drop the enqueue gate → 200 queued; drop the dispatcher check → the
  job runs to `completed` under the live study.
"""
from __future__ import annotations

import dataclasses
import threading
import time

import pytest

from services.project_context import STUDY_LABELS, record_is_running
from services.pypsa_service import PyPSAService
from tests.conftest import build_network
from tests.test_outage_rate_range import _network as _rate_network
from tests.test_save_activate_during_study import _FakeStudy
from tests.test_study_mesh_claim import _drain, _slow_sweep, _voll


# ── F1 ────────────────────────────────────────────────────────────────────

def test_elcc_candidates_refuses_a_bad_outage_rate_as_422(client, install_network):
    """★ F1. Same walk, same answer as `/copt`: 422 naming the unit.

    Bite (verified): drop the `except ValueError` — the TestClient re-raises
    the `OutageRateError` (a 500 on the wire)."""
    install_network(_rate_network(1.5))
    r = client.get("/api/results/mc/elcc_candidates")
    assert r.status_code == 422, r.text
    assert "bad" in r.json()["detail"] and "[0, 1)" in r.json()["detail"]
    install_network(_rate_network(with_bad=False))
    assert client.get("/api/results/mc/elcc_candidates").status_code == 200


# ── F2 ────────────────────────────────────────────────────────────────────
#
# `_refuse_save_during_study` is called three times on the route path: the
# wrapper's precheck (before `create_root`), `_save_context`'s early gate
# (before the mutation lock) and the in-lock re-check. The harness below
# patches it to HOLD the save at a chosen call so a study can start in the
# window that call leaves, with a stub sweep that rewrites `p_nom` lock-free
# — the shape of the sweep's first contingency.


class _HeldSave:
    def __init__(self, monkeypatch, hold_at: int):
        import routers.projects as RP
        self.real = RP._refuse_save_during_study
        self.calls = 0
        self.hold_at = hold_at
        self.reached = threading.Event()
        self.release = threading.Event()

        def gate(ctx):
            self.calls += 1
            self.real(ctx)                     # the real refusal, every time
            if self.calls == hold_at:
                self.reached.set()
                self.release.wait(timeout=15.0)

        monkeypatch.setattr(RP, "_refuse_save_during_study", gate)


def _mutating_sweep(p_nom_before, mutated):
    def run(n_, lock, cfg, *, stop_event=None, final_state_update=None):
        n_.generators["p_nom"] = 4321.0        # lock-free, like sweep.py
        mutated.set()
        for _ in range(100):
            if stop_event is not None and stop_event.is_set():
                break
            time.sleep(0.05)
        n_.generators["p_nom"] = p_nom_before
        return [], {"base_restored": True, "base_restore_status": "ok"}
    return run


def _saved_p_nom(nc_path):
    import pypsa
    saved = pypsa.Network()
    PyPSAService.import_network_from_netcdf(saved, nc_path)
    return float(saved.generators["p_nom"].iloc[0])


def _racy_project(client, api_project, session_state, project_storage_dir):
    import pathlib
    demo = api_project("racy")
    assert client.get(f"/api/projects/{demo}").status_code == 200
    st = session_state(client)
    _voll(st)
    nc_path = pathlib.Path(project_storage_dir(demo)) / "network.nc"
    gens = client.get("/api/network/generators").json()
    gens = gens if isinstance(gens, list) else gens.get("items") or gens.get("generators")
    return demo, st, nc_path, float(gens[0]["p_nom"])


def test_a_study_starting_after_the_early_gate_is_caught_in_the_lock(
        client, api_project, session_state, project_storage_dir, monkeypatch):
    """★ F2a. The save is held AFTER its pre-lock early gate (call 2). A
    study then publishes and mutates. The in-lock re-check (call 3) must
    refuse: 409, and the pre-study `p_nom` stays on disk.

    Bite (verified): drop the in-lock `_refuse_save_during_study(ctx)` —
    the save exports the mutated network (4321 on disk) and answers 200.
    """
    import services.adequacy.sweep as SW

    demo, st, nc_path, p_nom_before = _racy_project(
        client, api_project, session_state, project_storage_dir)
    mutated = threading.Event()
    monkeypatch.setattr(SW, "run_class_b_sweep", _mutating_sweep(p_nom_before, mutated))
    held = _HeldSave(monkeypatch, hold_at=2)
    out: dict = {}
    t_save = threading.Thread(
        target=lambda: out.update(save=client.post(f"/api/projects/{demo}?force=true")))
    t_save.start()
    try:
        assert held.reached.wait(timeout=10.0)
        r = client.post("/api/results/fmea_sweep", json={"scenarios": []})
        assert r.status_code == 200, r.text
        assert mutated.wait(timeout=10.0)
        held.release.set()
        t_save.join(timeout=30.0)
        assert out["save"].status_code == 409, out["save"].text
        assert out["save"].json()["detail"]["error_kind"] == "study_in_flight"
        assert _saved_p_nom(nc_path) == pytest.approx(p_nom_before)
    finally:
        held.release.set()
        _drain(st)


def test_a_study_cannot_publish_while_a_save_is_exporting(
        client, api_project, session_state, project_storage_dir, monkeypatch):
    """★ F2b. The save is held INSIDE the mutation lock, after its re-check
    (call 3) — the export is about to run. A study POST arriving now must
    BLOCK on that lock (its record stays unpublished) until the save has
    finished, and the file on disk must carry the pre-study plan.

    Bite (verified): drop `with PyPSAService.get_lock():` from
    `_publish_study` — the study publishes during the hold, its worker
    mutates, and the export writes 4321 to disk.
    """
    import services.adequacy.sweep as SW

    demo, st, nc_path, p_nom_before = _racy_project(
        client, api_project, session_state, project_storage_dir)
    mutated = threading.Event()
    monkeypatch.setattr(SW, "run_class_b_sweep", _mutating_sweep(p_nom_before, mutated))
    held = _HeldSave(monkeypatch, hold_at=3)
    out: dict = {}
    t_save = threading.Thread(
        target=lambda: out.update(save=client.post(f"/api/projects/{demo}?force=true")))
    t_save.start()
    t_study = None
    try:
        assert held.reached.wait(timeout=10.0)
        t_study = threading.Thread(
            target=lambda: out.update(study=client.post(
                "/api/results/fmea_sweep", json={"scenarios": []})))
        t_study.start()
        # The publish must be waiting on the mutation lock the save holds.
        time.sleep(0.5)
        assert not record_is_running(st.get("fmea_sweep")), \
            "study published while a save held the mutation lock"
        assert not mutated.is_set()
        held.release.set()
        t_save.join(timeout=30.0)
        assert out["save"].status_code == 200, out["save"].text
        t_study.join(timeout=30.0)
        assert out["study"].status_code == 200, out["study"].text
        assert mutated.wait(timeout=10.0)
        assert _saved_p_nom(nc_path) == pytest.approx(p_nom_before)
    finally:
        held.release.set()
        _drain(st)


# ── F3 ────────────────────────────────────────────────────────────────────

def test_enqueue_refuses_while_a_study_runs_on_the_resident_project(
        client, api_project, session_state):
    """★ F3a. `POST /simulation/queue` answers 409 with the study's sentence
    when the project is resident with a live study.

    Bite (verified): drop the enqueue gate — 200 queued."""
    demo = api_project("queued")
    assert client.get(f"/api/projects/{demo}").status_code == 200
    assert client.post(f"/api/projects/{demo}?force=true").status_code == 200
    st = session_state(client)
    projs = {p["name"]: p for p in client.get("/api/projects/").json()}
    pid = projs[demo].get("id") or demo
    with _FakeStudy(st, "frontier"):
        r = client.post("/api/simulation/queue", json={"project_id": pid})
        assert r.status_code == 409, r.text
        assert STUDY_LABELS["frontier"] in r.json()["detail"]
        assert "abort" in r.json()["detail"].lower()


def test_dispatcher_refuses_the_claim_under_a_live_study(tmp_projects_dir,
                                                          monkeypatch):
    """★ F3b. A study that starts AFTER enqueue: the dispatcher refuses at
    its claim, records the job `failed` with the study named, and never
    calls `run_simulation` on the study's network.

    Bite (verified): drop the dispatcher's in-lock check — the job runs
    to `completed` while the study record is live."""
    from services import solve_queue as SQ
    from services import solver_service as SS
    from services.project_context import ProjectContext

    ctx = ProjectContext(network=build_network(), loaded_project="bg")
    PyPSAService.register("bg-key", ctx)
    ran = {"n": 0}

    def fake_run(*a, **k):
        ran["n"] += 1
        return "ok", "optimal"

    # The dispatcher imports `run_simulation` lazily at the top of its
    # drain loop, so the module attribute is what it binds.
    monkeypatch.setattr(SS, "run_simulation", fake_run)
    try:
        with _FakeStudy(ctx.solver_state, "mc"):
            job = SQ.solve_queue.enqueue("bg", project_key="bg-key",
                                         storage_dir=None)
            for _ in range(200):
                j = SQ.solve_queue.get_job(job.id)
                if j["status"] in ("completed", "failed", "aborted"):
                    break
                time.sleep(0.05)
            assert j["status"] == "failed", j
            assert "sequential-MC" in (j.get("error") or ""), j
            assert ran["n"] == 0, "run_simulation ran under a live study"
            assert record_is_running(ctx.solver_state.get("mc"))
    finally:
        PyPSAService._contexts.pop("bg-key", None)


class _LockProxy:
    """Delegates to the dispatcher context's real state RLock; on the
    dispatcher thread it blocks BEFORE the `n`-th acquisition, so a test can
    park the dispatcher exactly between two lock holds."""

    def __init__(self, real, hold_before: int):
        self.real, self.hold_before, self.n = real, hold_before, 0
        self.in_gap = threading.Event()
        self.go = threading.Event()

    def __enter__(self):
        # MERGE NOTE (2026-09-10): prefix, not equality. Master runs ONE
        # dispatcher named exactly "solve-queue-dispatcher"; this branch runs
        # `PYPSA_GUI_MAX_CONCURRENT_SOLVES` of them, named
        # "solve-queue-dispatcher-0", "-1", … . The invariant under test — the
        # check and the claim happen under ONE lock hold — is unchanged and
        # still held by both designs; only the thread name moved, and an
        # equality match silently stopped arming the gap at all, which reads
        # as "dispatcher never reached the gap" rather than as a real race.
        if threading.current_thread().name.startswith("solve-queue-dispatcher"):
            self.n += 1
            if self.n == self.hold_before:
                self.in_gap.set()
                self.go.wait(10.0)
        return self.real.__enter__()

    def __exit__(self, *a):
        return self.real.__exit__(*a)

    def acquire(self, *a, **k):
        return self.real.acquire(*a, **k)

    def release(self):
        return self.real.release()


def test_dispatcher_check_and_claim_are_one_lock_hold(
        client, install_network, tmp_projects_dir, session_state, session_ctx,
        monkeypatch):
    """★ F3c (fix review, second pass, P7). The dispatcher's study check and
    its claim must be ONE acquisition of the state lock: with two, a study
    POST landing between them was admitted and the queued solve then ran on
    the study's network.

    Harness: the dispatcher's state lock is proxied to park the dispatcher
    thread before its SECOND acquisition. With one hold, the second
    acquisition is the post-solve status write — by then the claim is
    published, so a study POST in the gap is refused (409) and the solve
    runs alone. With the check and the claim as two holds, the gap sits
    between them: the study is admitted (200) AND the solve runs.

    Bite (verified): split the claim back out into `ctx_state_update(...)`
    after the check block — the study POST answers 200 while the queued
    solve also runs.
    """
    from services import solver_service as SS
    import services.adequacy.sweep as SW
    from tests.test_solve_queue import _save_project, _wait_for_terminal

    install_network(build_network(), name="P1")
    _save_project(client, "P1")
    ctx = session_ctx(client)
    st = ctx.solver_state
    _voll(st)
    ran = {"n": 0}

    def stub(config, n, lock, stop_event, log_queue, state_update=None, **kw):
        ran["n"] += 1
        time.sleep(0.3)
        return "ok", "optimal"

    monkeypatch.setattr(SS, "run_simulation", stub)
    monkeypatch.setattr(SW, "run_class_b_sweep", _slow_sweep)
    proxy = _LockProxy(ctx.solver_state_lock, hold_before=2)
    monkeypatch.setattr(ctx, "solver_state_lock", proxy)
    r = client.post("/api/simulation/queue", json={"project_id": "P1"})
    assert r.status_code == 200, r.text
    try:
        assert proxy.in_gap.wait(10.0), "dispatcher never reached the gap"
        rs = client.post("/api/results/fmea_sweep", json={})
        proxy.go.set()
        done = _wait_for_terminal(r.json()["id"], timeout=30)
        assert not (rs.status_code == 200 and ran["n"] == 1), (
            "study admitted in the gap AND the queued solve ran on its network")
        # With one hold the claim is visible in the gap: the study is refused
        # and the solve completes.
        assert rs.status_code == 409, rs.text
        assert done["status"] == "completed", done
        assert ran["n"] == 1
    finally:
        proxy.go.set()
        _drain(st)

"""
Save, activate and eviction refuse while an adequacy study is running.

Whole-branch review (2026-09-08), findings S5, M12 and U2. Load, import,
template, undo, snapshot restore and reset were guarded against a running
study since Phase 11 (`refuse_if_study_running`). Save and activate were
not: both gated on `_solver_in_flight`, and a study's worker is never
`state["thread"]`. So:

* S5 — a save landing between a sweep's lock-free contingency mutations
  (`sweep.py` freezes capacities and applies each outage in turn) exported
  the CONTINGENCY network, and a `results_state.pkl` carrying the
  contingency's lost load, as the user's project. Every closing restore runs
  AFTER such a save, so the disk held a plan the study never reported on.
* M12 — `POST /projects/{id}/activate`, the only route the frontend's
  project switch calls, admitted a switch away from a running study: the
  study kept running on a project the user could neither see nor abort, and
  the frontend told them to "abort the running solve", which would not have
  stopped it.
* U2 — the LRU eviction protected active / session-active / queued-solve
  contexts only; a context carrying a live study was evictable, and the
  eviction SAVES its victim first.

What ships: `_save_context` refuses (structured 409, `error_kind:
"study_in_flight"`, naming the study) for every caller — the route, autosave,
the background dispatcher, eviction; the save wrapper refuses BEFORE
`create_root` so a refused first save leaves no project row; `activate`
refuses with the same shape; and `_evict_if_over_cap` protects any context
with a live study.

Bites (verified red against the named removal):
* drop `_refuse_save_during_study(ctx)` in `_save_context` → the save test
  answers 200 and writes `network.nc`;
* drop the wrapper's precheck → the new-name test finds a project row;
* drop the activate gate → the activate test answers 200;
* drop the eviction protection → the study context is evicted.
"""
from __future__ import annotations

import threading
import time

import pytest

from services.project_context import ProjectContext, STUDY_LABELS
from services.pypsa_service import PyPSAService
from tests.conftest import build_network


class _FakeStudy:
    """A LIVE study record under `state[key]` — a real daemon thread, since
    `record_is_running` tests `is_alive()`, not the status string."""

    def __init__(self, state, key="fmea_sweep"):
        self.state, self.key = state, key
        self.release = threading.Event()
        self.t = threading.Thread(target=self.release.wait,
                                  kwargs={"timeout": 30.0}, daemon=True,
                                  name=f"fake-{key}")

    def __enter__(self):
        self.t.start()
        self.state[self.key] = {"status": "running", "rows": [], "error": None,
                                "started_at": time.time(), "thread": self.t,
                                "stop_event": self.release}
        return self

    def __exit__(self, *exc):
        self.release.set()
        self.t.join(timeout=5.0)
        self.state[self.key] = None


def _assert_study_refusal(r, key):
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert isinstance(detail, dict), detail
    assert detail["error_kind"] == "study_in_flight"
    assert detail["study"] == key
    assert STUDY_LABELS[key] in detail["message"]
    assert "abort" in detail["message"].lower()


# ── S5: save ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", ["fmea_sweep", "frontier", "coupling_loop"])
def test_save_refuses_while_a_study_runs(client, api_project, session_state,
                                          key):
    """★ S5. `POST /projects/{name}` answers 409 naming the study, and the
    file on disk is untouched.

    Bite (verified): drop `_refuse_save_during_study(ctx)` from
    `_save_context` — 200, and `network.nc` is rewritten mid-study.
    """
    demo = api_project("demo")
    assert client.get(f"/api/projects/{demo}").status_code == 200
    st = session_state(client)
    proj = next(p for p in client.get("/api/projects/").json()
                if p["name"] == demo)
    nc = proj.get("path")
    import pathlib
    nc_path = pathlib.Path(nc) / "network.nc" if nc else None
    before = nc_path.stat().st_mtime_ns if nc_path and nc_path.exists() else None
    with _FakeStudy(st, key):
        r = client.post(f"/api/projects/{demo}?force=true")
        _assert_study_refusal(r, key)
        if before is not None:
            assert nc_path.stat().st_mtime_ns == before, "network.nc rewritten"
    # …and once the study is gone the save goes through.
    r = client.post(f"/api/projects/{demo}?force=true")
    assert r.status_code == 200, r.text


def test_first_save_under_a_new_name_leaves_no_project_row(client, api_project,
                                                            session_state):
    """★ The wrapper refuses BEFORE `create_root`: a refused first save must
    not leave a project row that makes the retry fail with "already exists".

    Bite (verified): drop the wrapper's precheck — the row exists after the
    409 (the gate inside `_save_context` still refuses, one step too late).
    """
    demo = api_project("demo")
    assert client.get(f"/api/projects/{demo}").status_code == 200
    st = session_state(client)
    with _FakeStudy(st, "mc"):
        r = client.post("/api/projects/brand_new_during_study?force=true")
        _assert_study_refusal(r, "mc")
        names = {p["name"] for p in client.get("/api/projects/").json()}
        assert "brand_new_during_study" not in names, sorted(names)


def test_save_context_refuses_for_every_caller(tmp_projects_dir):
    """The gate lives in `_save_context`, so autosave, the background
    dispatcher and the eviction save are covered by the same refusal."""
    from fastapi import HTTPException

    from routers.projects import _save_context

    # A bare context, as the dispatcher and the eviction hold one — not the
    # session's (which the test thread cannot resolve as "active").
    ctx = ProjectContext(network=build_network(), loaded_project="bg")
    with _FakeStudy(ctx.solver_state, "margin_loop"):
        with pytest.raises(HTTPException) as ei:
            _save_context(ctx, "bg", expect="bg")
        assert ei.value.status_code == 409
        assert ei.value.detail["error_kind"] == "study_in_flight"
        assert "margin-loop" in ei.value.detail["message"]


# ── M12: activate ─────────────────────────────────────────────────────────

def test_activate_refuses_while_a_study_runs(client, api_project,
                                             session_state):
    """★ M12. Switching projects would leave the study running on a project
    the user can neither see nor abort; the refusal is the study's own
    sentence, the same shape the in-flight refusal already has.

    Bite (verified): drop the gate in `activate_project` — 200, switched.
    """
    demo = api_project("demo")
    other = api_project("other")
    assert client.get(f"/api/projects/{demo}").status_code == 200
    st = session_state(client)
    projs = {p["name"]: p for p in client.get("/api/projects/").json()}
    other_id = projs[other].get("id") or other
    with _FakeStudy(st, "frontier"):
        r = client.post(f"/api/projects/{other_id}/activate")
        _assert_study_refusal(r, "frontier")
        # The load route's refusal (Phase 11) still agrees with it.
        assert client.get(f"/api/projects/{other}").status_code == 409
    r = client.post(f"/api/projects/{other_id}/activate")
    assert r.status_code == 200, r.text


# ── U2: eviction ──────────────────────────────────────────────────────────

def _bound_ctx(name: str, stamp: float) -> ProjectContext:
    n = build_network()
    n.name = name
    ctx = ProjectContext(network=n, loaded_project=name)
    ctx.last_interacted_at = stamp
    return ctx


def test_eviction_protects_a_context_with_a_live_study(tmp_projects_dir,
                                                        monkeypatch):
    """★ U2. Over the cap, the LRU victim carrying a live study is skipped
    and the next LRU is evicted instead.

    Bite (verified): drop the study loop in `_evict_if_over_cap` — the
    study context is evicted (and its save refused, so it is dropped with
    the study still running on it).
    """
    prev = PyPSAService.RESIDENT_CAP
    PyPSAService.RESIDENT_CAP = 2
    a = _bound_ctx("A", 1.0)
    b = _bound_ctx("B", 2.0)
    c = _bound_ctx("C", 3.0)
    try:
        PyPSAService.register("A", a)
        PyPSAService.register("B", b)
        a.last_interacted_at, b.last_interacted_at = 1.0, 2.0
        with _FakeStudy(a.solver_state, "mc"):
            evicted = PyPSAService.register("C", c)
            assert evicted == ["B"], evicted
            assert PyPSAService.get_context("A") is not None
    finally:
        PyPSAService.RESIDENT_CAP = prev
        for pid in ("A", "B", "C"):
            PyPSAService._contexts.pop(pid, None)

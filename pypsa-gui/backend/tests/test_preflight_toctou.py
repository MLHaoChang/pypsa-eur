"""
The `/api/simulation/preflight` route's check-then-act window.

Recorded as a MINOR pre-existing finding by the Phase 12c v3 review and
carried as backlog through 12d–12i: the route guarded on
`_solver_in_flight()` and then validated, so a solve starting in between was
judged against a half-transformed network.

**What the window can and cannot do**, established before fixing it:

* it CANNOT corrupt the network — because **nothing on the validation path
  writes**. That is the true reason, verified by a pattern sweep over
  `validation_service`, `reserve_margin_facts` and the adequacy modules it
  calls: zero writes. (An earlier version of this file said "because
  `lp_demand_frame` copies before scaling"; it does, but only when there is
  something to scale, and the test that claimed to pin it exercised the
  no-copy branch. Test 5 now pins the scaled branch AND the honest claim.)
* it CAN produce wrong ADVICE. Scaling is not idempotent (measured ×1.25² by
  the 12c v3 review), so validating while a solve has already scaled the
  demand in place double-scales it and can invent a
  `reserve_margin_unreachable` that is not true of the user's network.

**The first fix (5e32f02) did not close the window.** It re-checked
`_solver_in_flight()` after validating. A worker whose whole transient fits
inside the validation span starts after the guard, restores, and exits before
the re-check — measured at 24 of 27 non-deferred answers wrong with a 40 ms
transient against an 80 ms validation. `/run` survived it only because
`run_simulation` happens to validate under the lock before mutating; the
AC-PF worker does not.

**The fix is three gates**: the worker check (kept, for its stuck-abort
message); the running-study check that `/run` already makes and preflight did
not (studies mutate with NO lock held, so nothing else can see them); and a
NON-blocking acquire of the mutation lock held for the validation span. A
solve already holding the lock makes the route answer `deferred` instantly
rather than block the UI for its whole duration, which is why the route never
took the lock before; a solve arriving mid-validation waits one validation.
"""
from __future__ import annotations

import threading

import pandas as pd
import pypsa
import pytest

import routers.simulation as SIM
from services import study_state as STUDY
from services.pypsa_service import PyPSAService


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.add("Bus", "b", carrier="AC")
    n.add("Carrier", "gas")
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=200.0,
          marginal_cost=10.0)
    n.add("Load", "l", bus="b", p_set=100.0)
    return n


def _deferred_shape(out: dict) -> None:
    assert out["deferred"] is True, out
    assert out["issues"] == []
    assert out["errors"] == 0 and out["warnings"] == 0


def test_preflight_answers_normally_when_nothing_is_running(install_network):
    """The common path is untouched: no worker, no study, lock free."""
    install_network(_network())
    out = SIM.preflight()
    assert out.get("deferred") is not True
    assert isinstance(out["issues"], list)
    assert out["errors"] == sum(1 for i in out["issues"]
                                if i["severity"] == "error")


def test_preflight_defers_when_a_solver_worker_is_alive(install_network,
                                                        monkeypatch):
    """Gate 1, the pre-existing guard, still fires first."""
    install_network(_network())
    monkeypatch.setattr(SIM, "_solver_in_flight", lambda: True)
    out = SIM.preflight()
    _deferred_shape(out)
    assert "Solver worker still active" in out["deferred_reason"]


def test_preflight_defers_when_the_lock_is_held_and_no_worker_is_registered(
        install_network):
    """★ Gate 3 — THE window. Another thread holds the mutation lock while
    `_solver_in_flight()` is False at every check: the shape of a worker
    that `force_reset` disowned, and of any mutation the worker registry
    cannot see. The first fix's `flaky()` in-flight stub could not even
    express this case; the reviewer's harness measured it slipping through.

    The route must answer `deferred` at once — not block on the lock, and
    not validate against whatever the holder is doing to the network.

    Bite (verified): drop the non-blocking acquire — the route validates
    and returns a normal issue list.
    """
    install_network(_network())
    lock = PyPSAService.get_lock()
    holding = threading.Event()
    release = threading.Event()

    def holder():
        with lock:
            holding.set()
            release.wait(timeout=10.0)

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    assert holding.wait(timeout=5.0)
    try:
        assert SIM._solver_in_flight() is False
        out = SIM.preflight()
    finally:
        release.set()
        t.join(timeout=5.0)

    _deferred_shape(out)
    assert out["deferred_stuck"] is False


def test_preflight_does_not_block_on_a_held_lock(install_network):
    """The property that justified never taking the lock, kept: a long solve
    holds the lock for minutes, and the route must return in milliseconds
    with `deferred`, not wait it out."""
    import time

    install_network(_network())
    lock = PyPSAService.get_lock()
    holding = threading.Event()
    release = threading.Event()

    def holder():
        with lock:
            holding.set()
            release.wait(timeout=10.0)

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    assert holding.wait(timeout=5.0)
    try:
        t0 = time.perf_counter()
        out = SIM.preflight()
        elapsed = time.perf_counter() - t0
    finally:
        release.set()
        t.join(timeout=5.0)
    assert out["deferred"] is True
    assert elapsed < 1.0, f"preflight blocked on the lock for {elapsed:.2f}s"


def test_preflight_defers_while_an_adequacy_study_is_running(install_network):
    """★ Gate 2. Studies mutate the foreground network between their own
    solves with NO lock held, so neither the worker check nor the lock can
    see them. `/run` refuses on `blocking_study_detail()`; preflight now
    does too, with the study's own sentence so the user can abort it by
    name.

    Bite (verified): drop gate 2 — `_solver_in_flight()` is False, the lock
    is free, and the route validates a network a study is rewriting.
    """
    install_network(_network())
    state = PyPSAService.get_solver_state()
    key = STUDY.STUDY_KEYS[0]
    stop = threading.Event()
    worker = threading.Thread(target=stop.wait, kwargs={"timeout": 10.0},
                              daemon=True)
    worker.start()
    prev = state.get(key)
    state[key] = {"status": "running", "thread": worker}
    try:
        assert STUDY.running_study() == key
        assert SIM._solver_in_flight() is False
        out = SIM.preflight()
    finally:
        state[key] = prev
        stop.set()
        worker.join(timeout=5.0)

    _deferred_shape(out)
    # The study's own sentence, naming it, so the user can abort it by name.
    assert STUDY.STUDY_LABELS.get(key, key) in out["deferred_reason"]
    assert "abort" in out["deferred_reason"].lower()
    assert out["deferred_stuck"] is False


def test_the_deferred_answer_still_distinguishes_a_stuck_abort(
        install_network, monkeypatch):
    """The two remediations stay distinct: a solve aborted but still in
    native solver code needs a backend restart, and the route says so rather
    than telling the user to wait. Status is saved and RESTORED, not reset
    to a value the dataclass never uses."""
    install_network(_network())
    monkeypatch.setattr(SIM, "_solver_in_flight", lambda: True)
    prev = SIM._state.get("status")
    SIM._state["status"] = "aborted"
    try:
        out = SIM.preflight()
    finally:
        SIM._state["status"] = prev

    _deferred_shape(out)
    assert out["deferred_stuck"] is True
    assert "Restart the backend" in out["deferred_reason"]


def test_validation_reads_the_scaled_demand_from_a_copy_and_writes_nothing(
        install_network):
    """★ The half of the finding that bounds its severity — pinned on the
    branch that actually matters. The first version of this test used
    `SolverConfig()` on a flat index, where `lp_demand_frame` returns the
    LIVE frame and nothing is copied at all (shipped-code review of
    5e32f02, finding 3). Here the demand is really scaled: MultiIndex
    snapshots, `multi_investment_periods`, a 1.25 load scaler.

    Two claims, both honest: the scaled frame the validator reads IS a copy
    (`got is not live`, and `got == live x 1.25`), and a full
    `validate_for_run` on that config leaves the live frame byte-identical
    — the real reason the window could never corrupt anything is that
    nothing on the path writes.
    """
    from services.adequacy.demand import lp_demand_frame
    from services.solver_service import SolverConfig
    from services.validation_service import validate_for_run

    n = pypsa.Network()
    idx = pd.MultiIndex.from_product(
        [[2030, 2040], pd.date_range("2030-01-01", periods=3, freq="h")],
        names=["period", "timestep"])
    n.set_snapshots(idx)
    n.investment_periods = [2030, 2040]
    n.add("Bus", "b", carrier="AC")
    n.add("Carrier", "gas")
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=200.0,
          marginal_cost=10.0)
    n.add("Load", "l", bus="b", p_set=100.0)
    n.loads_t.p_set = pd.DataFrame({"l": [100.0] * 6}, index=n.snapshots)
    install_network(n)

    cfg = SolverConfig(multi_investment_periods=True,
                       investment_periods=[2030, 2040],
                       load_scalers={"2030": 1.25})
    before = n.loads_t.p_set.copy(deep=True)

    got = lp_demand_frame(n, cfg)
    assert got is not n.loads_t.p_set, "scaled branch must return a copy"
    assert got.loc[2030, "l"].iloc[0] == pytest.approx(125.0)
    assert got.loc[2040, "l"].iloc[0] == pytest.approx(100.0)

    validate_for_run(n, cfg)
    pd.testing.assert_frame_equal(n.loads_t.p_set, before)

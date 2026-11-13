"""PG-04b: the reserve commitments' freshness on the provenance graph (`runs.reserves_verdict`).

GK B plan GO conditions: W1 byte-identical flags, W2 a killing case per pin, W3 one read under 04a's lock and
bracket, W4 fail closed (V2 None-vs-None, unreadable current), W5 no cache."""

from __future__ import annotations

import threading

import pytest

from services.provenance_graph import pins, runs
from services.pypsa_service import PyPSAService
from services.solver import reserve_products as RP
from tests.test_reserve_products import STRING, _cfg, _product, _site, _solve

STALE = (None, ["reserve_not_solved", "reserve_inputs_changed_since_solve"])
FLOORS = {STRING: 20.0}


@pytest.fixture
def solved():
    n = _site()
    fcr = _product()
    _solve(n, [fcr], FLOORS)
    return n, fcr


def _digest(products, floors=FLOORS):
    return RP.inputs_digest(products, floors)


# ── W1 / W2: the flags, one row per pin ──────────────────────────────────────


def test_untouched_is_fresh_and_returns_the_frame(solved):
    n, fcr = solved
    frame, flags = RP.solved(n, _digest([fcr]))
    assert flags == [] and frame is not None
    # The callable form reads the same.
    assert RP.solved(n, lambda: _digest([fcr]))[1] == []


def test_an_inputs_only_edit_is_stale_with_the_weights_unchanged(solved):
    """W2: kills a mutant that drops the inputs pin."""
    n, _ = solved
    key = RP.weights_key(n)
    other = _digest([_product(max_commit_mw=1.0)])
    assert RP.weights_key(n) == key
    assert RP.solved(n, other) == STALE


def test_a_reweighting_only_is_stale_with_the_inputs_unchanged(solved):
    """W2: kills a mutant that drops the weights pin. Non-unit weights leave `inputs_digest` equal."""
    n, fcr = solved
    before = _digest([fcr])
    n.snapshot_weightings.loc[n.snapshots[1], "objective"] = 0.5
    assert _digest([fcr]) == before
    assert RP.solved(n, before) == STALE


def test_no_digest_compares_the_weights_only(solved):
    """As today: a caller that passes no digest does not compare the inputs."""
    n, _ = solved
    assert RP.solved(n)[1] == []


def test_no_record_and_another_axis_are_no_solve_as_today(solved):
    n, fcr = solved
    meta = n.meta.pop(RP.META_KEY)
    assert RP.solved(n, _digest([fcr])) == (None, ["reserve_not_solved"])
    n.meta[RP.META_KEY] = {**meta, "axis_hash": "other"}
    assert RP.solved(n, _digest([fcr])) == (None, ["reserve_not_solved"])


def test_a_record_with_no_weights_key_reads_stale_as_today(solved):
    """W4: the older meta shape had no `weights`; today `None != weights_key(n)` gives the stale flags."""
    n, fcr = solved
    n.meta[RP.META_KEY].pop("weights")
    assert RP.solved(n, _digest([fcr])) == STALE


# ── W4: fail closed ──────────────────────────────────────────────────────────


def test_a_recorded_none_against_a_current_none_never_matches(solved):
    """V2: a recorded None digest against a current None is stale, never fresh (red under None == None)."""
    n, _ = solved
    n.meta[RP.META_KEY]["inputs"] = None
    assert RP.solved(n, lambda: None) == STALE
    n.meta[RP.META_KEY]["weights"] = None
    assert RP.solved(n) == STALE


def test_an_unreadable_current_digest_reads_stale(solved):
    n, _ = solved

    def boom():
        raise RuntimeError("the products cannot be read")

    assert RP.solved(n, boom) == STALE


def test_an_unreadable_current_weighting_reads_stale(solved, monkeypatch):
    n, fcr = solved

    def boom(_n):
        raise RuntimeError("no weightings")

    monkeypatch.setattr(RP, "weights_key", boom)
    assert RP.solved(n, _digest([fcr])) == STALE


def test_the_verdict_runs_through_the_run_node(solved):
    n, fcr = solved
    meta = n.meta[RP.META_KEY]
    v = runs.reserves_verdict(
        pins.Pin("reserve inputs", meta["inputs"], lambda: "other"),
        pins.Pin(
            "hours and period weights", meta["weights"], lambda: RP.weights_key(n)
        ),
    )
    assert v.state == "stale"
    assert {b.field for b in v.broken} == {"reserve inputs"}


# ── W1: the two callers on a stale case ──────────────────────────────────────


def test_both_callers_flag_a_stale_solve_as_today(solved):
    from services.results import billing as B
    from services.results.reserves import reserve_view

    n, _ = solved
    now = [_product(max_commit_mw=1.0)]
    (line,), flags = B.reserve_lines(
        n, _cfg(now), "site", backup=(FLOORS, frozenset({STRING}))
    )
    assert line.amount is None
    assert {"reserve_not_solved", "reserve_inputs_changed_since_solve"} <= set(
        line.flags
    )
    view = reserve_view(n, _cfg(now), solved=True, backup=(FLOORS, frozenset({STRING})))
    assert "reserve_inputs_changed_since_solve" in str(view)


# ── W3: one read under the open project's lock ───────────────────────────────


@pytest.fixture
def active(install_network):
    n = _site()
    fcr = _product()
    _solve(n, [fcr], FLOORS)
    install_network(n)
    ctx = PyPSAService.get_active_context()
    assert ctx.network is n
    return n, fcr, ctx


def test_a_held_lock_never_stalls_or_stales_the_read(active, monkeypatch):
    """GK B F2: another thread holding the mutation lock (a save, a hand-over, a solve) is never waited for and never
    reads as stale: every acquire is non-blocking and the commitments read as they stand."""
    n, fcr, ctx = active
    held, done = threading.Event(), threading.Event()

    def holder():
        with ctx.mutation_lock:
            held.set()
            done.wait(10)

    t = threading.Thread(target=holder)
    t.start()
    real_lock = ctx.mutation_lock
    waits = []

    class Spy:
        def acquire(self, blocking=True, timeout=-1):
            waits.append((blocking, timeout))
            return real_lock.acquire(blocking, timeout)

        def release(self):
            real_lock.release()

    try:
        assert held.wait(5)
        ctx.mutation_lock = Spy()
        got = RP.solved(n, _digest([fcr]))
    finally:
        ctx.mutation_lock = real_lock
        done.set()
        t.join()
    assert got[1] == [] and got[0] is not None
    assert waits == [(False, -1)]


def test_an_edit_during_the_read_is_read_again_then_unreadable(active, monkeypatch):
    """An edit landing during the read reads again once; a network that keeps moving reads stale."""
    n, fcr, ctx = active
    real = RP._solved_read
    calls = []

    def moving(*a, **k):
        calls.append(1)
        ctx.network_revision = int(ctx.network_revision or 0) + 1
        return real(*a, **k)

    monkeypatch.setattr(RP, "_solved_read", moving)
    assert RP.solved(n, _digest([fcr])) == STALE and len(calls) == 2

    once = []

    def moves_once(*a, **k):
        if not once:
            once.append(1)
            ctx.network_revision = int(ctx.network_revision or 0) + 1
        return real(*a, **k)

    monkeypatch.setattr(RP, "_solved_read", moves_once)
    assert RP.solved(n, _digest([fcr]))[1] == []


def test_an_edit_right_after_the_read_is_stale_on_the_next_read(active):
    n, fcr, ctx = active
    assert RP.solved(n, _digest([fcr]))[1] == []
    with ctx.mutation_lock:
        n.snapshot_weightings.loc[n.snapshots[0], "objective"] = 2.0
        ctx.network_revision = int(ctx.network_revision or 0) + 1
    assert RP.solved(n, _digest([fcr])) == STALE


def test_the_read_holds_the_mutation_lock(active, monkeypatch):
    n, fcr, ctx = active
    seen = []
    real = RP._solved_read

    def spy(*a, **k):
        seen.append(ctx.mutation_lock._is_owned())
        return real(*a, **k)

    monkeypatch.setattr(RP, "_solved_read", spy)
    RP.solved(n, _digest([fcr]))
    assert seen == [True]


def test_another_network_reads_without_the_open_project_lock(active, monkeypatch):
    """A network that is not the open project's (a saved one) has no lock to wait for."""
    _, _, ctx = active
    m = _site()
    fcr = _product()
    _solve(m, [fcr], FLOORS)
    with ctx.mutation_lock:
        got = []
        t = threading.Thread(target=lambda: got.append(RP.solved(m, _digest([fcr]))))
        t.start()
        t.join()
    assert got[0][1] == []


def test_a_network_swapped_during_the_read_reads_stale(active, monkeypatch):
    """The bracket compares the network object too: a project swap mid-read is unreadable."""
    import pypsa

    n, fcr, ctx = active
    real = RP._solved_read

    def swapping(*a, **k):
        got = real(*a, **k)
        ctx.network = pypsa.Network()
        return got

    monkeypatch.setattr(RP, "_solved_read", swapping)
    try:
        assert RP.solved(n, _digest([fcr])) == STALE
    finally:
        ctx.network = n

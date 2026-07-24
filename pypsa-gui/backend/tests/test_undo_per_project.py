"""
B3 — per-project undo subsystem.

The undo stack moved off `undo_service`'s module globals onto a `_UndoState`
carried by each `ProjectContext` (`ctx.undo`); the module functions operate on
the ACTIVE project's instance via `PyPSAService.get_active_context().undo`.
These tests verify (a) push/pop still work on the active context, (b) two
resident contexts keep INDEPENDENT undo histories (the B8 precondition), (c)
single-resident behaviour is byte-identical: undo is carried forward across a
network swap and emptied by `clear()`, and (d) the coalesce window is per-active.

`push` only pickles its args (it never parses the netcdf), so dummy bytes
exercise the stack mechanics without a real network. The autouse `reset_backend`
fixture clears `_contexts` + the active undo around every test.
"""
from __future__ import annotations

import pypsa

from services import undo_service
from services.project_context import ProjectContext
from services.pypsa_service import PyPSAService


def _ctx(name: str) -> ProjectContext:
    n = pypsa.Network()
    n.name = name
    return ProjectContext(network=n, loaded_project=name)


def test_push_pop_depth_on_active_context():
    undo_service.clear()
    assert undo_service.depth() == 0
    undo_service.push(b"snap1", {"a": 1})
    undo_service.push(b"snap2", {"b": 2})
    assert undo_service.depth() == 2
    nc, ts = undo_service.pop()
    assert nc == b"snap2" and ts == {"b": 2}
    assert undo_service.depth() == 1


def test_undo_is_independent_per_resident_context():
    a, b = _ctx("A"), _ctx("B")
    PyPSAService.register("A", a)
    PyPSAService.register("B", b)

    PyPSAService.set_active("A")
    undo_service.push(b"A1", {})
    undo_service.push(b"A2", {})
    assert undo_service.depth() == 2

    # Switch to B — its undo is its own (empty), not A's.
    PyPSAService.set_active("B")
    assert undo_service.depth() == 0
    undo_service.push(b"B1", {})
    assert undo_service.depth() == 1

    # Back to A — its 2-deep history is intact.
    PyPSAService.set_active("A")
    assert undo_service.depth() == 2

    # The two stacks are distinct objects (the B8 precondition).
    assert a.undo is not b.undo
    assert a.undo.stack is not b.undo.stack


def test_undo_carried_forward_across_reset_network():
    # Single-resident byte-identical behaviour: the old global undo deque
    # survived a network swap, so the carried-forward _UndoState must too.
    undo_service.clear()
    undo_service.push(b"x", {})
    assert undo_service.depth() == 1
    PyPSAService.reset_network()
    assert undo_service.depth() == 1  # carried forward, not reset


def test_undo_carried_forward_across_set_network():
    undo_service.clear()
    undo_service.push(b"y", {})
    assert undo_service.depth() == 1
    PyPSAService.set_network(pypsa.Network())  # clustering-style in-place swap
    assert undo_service.depth() == 1


def test_clear_empties_active_undo_and_resets_coalesce():
    undo_service.push(b"z", {})
    assert undo_service.depth() >= 1
    undo_service.clear()
    assert undo_service.depth() == 0
    assert undo_service.memory_bytes() == 0
    # After clear the coalesce timer is reset, so the next claim fires True.
    assert undo_service.claim_push_slot(window_s=10.0) is True


def test_coalesce_window_is_per_active():
    undo_service.clear()
    assert undo_service.claim_push_slot(window_s=10.0) is True   # first push allowed
    assert undo_service.claim_push_slot(window_s=10.0) is False  # within window → coalesced


def test_byte_budget_eviction_trims_to_one(monkeypatch):
    # With a tiny per-project MAX_BYTES, push() trims oldest entries until the
    # cumulative size fits — but ALWAYS retains the newest (>=1 entry), so a
    # single oversized snapshot is still undoable. Guards the byte-accounting in
    # push() (the most refactor-fragile part) against regressions.
    undo_service.clear()
    monkeypatch.setattr(undo_service, "MAX_BYTES", 100)  # << one payload's size
    undo_service.push(b"x" * 1000, {})
    undo_service.push(b"y" * 1000, {})
    undo_service.push(b"z" * 1000, {})
    assert undo_service.depth() == 1          # trimmed to the single newest
    assert undo_service.memory_bytes() > 100  # the retained jumbo exceeds the cap
    nc, _ = undo_service.pop()
    assert nc == b"z" * 1000                  # newest survived
    assert undo_service.memory_bytes() == 0

"""
B4.1 — thread-local transient-registry targeting (the Risk-6 blocker fix).

`run_simulation` → `_apply_modelling_assumptions` / `vintage_service` call
`PyPSAService.mark_transient/unmark_transient/clear_transient`, which used to
resolve to the ACTIVE context. A background solve of project B (B4.3) would then
mark B's `__voll_*`/`@year` rows on the FOREGROUND A's registry (and clear A's
marks in restore). `PyPSAService.solving_context(ctx)` binds the transient target
to `ctx` for the current thread, so the solve marks ITS OWN rows. Unbound
(request threads / GET reads) → active, byte-identical to before.
"""
from __future__ import annotations

import pypsa

from services.project_context import ProjectContext
from services.pypsa_service import PyPSAService


def _ctx(name: str) -> ProjectContext:
    n = pypsa.Network()
    n.name = name
    return ProjectContext(network=n, loaded_project=name)


def test_marks_go_to_active_when_unbound():
    PyPSAService.mark_transient("Generator", "__voll_A")
    active = PyPSAService.get_active_context()
    assert "__voll_A" in active.transient_rows.get("Generator", set())


def test_marks_go_to_solving_ctx_when_bound():
    bg = _ctx("BG")
    active = PyPSAService.get_active_context()
    with PyPSAService.solving_context(bg):
        PyPSAService.mark_transient("Generator", "__voll_B")
        assert PyPSAService.has_any_transient_rows() is True  # resolves bg, not active
    # bg got the mark; the foreground (active) ctx did NOT.
    assert "__voll_B" in bg.transient_rows.get("Generator", set())
    assert "__voll_B" not in active.transient_rows.get("Generator", set())


def test_binding_restored_after_context_exit():
    bg = _ctx("BG")
    with PyPSAService.solving_context(bg):
        pass
    # After exit, marks resolve to the active ctx again (not the stale bg).
    PyPSAService.mark_transient("Generator", "__voll_C")
    assert "__voll_C" in PyPSAService.get_active_context().transient_rows.get("Generator", set())
    assert "Generator" not in bg.transient_rows


def test_clear_and_unmark_target_solving_ctx():
    bg = _ctx("BG")
    active = PyPSAService.get_active_context()
    # Seed a mark on the FOREGROUND so we can prove clear() doesn't touch it.
    PyPSAService.mark_transient("Generator", "__voll_FG")
    with PyPSAService.solving_context(bg):
        PyPSAService.mark_transient("Generator", "a")
        PyPSAService.mark_transient("Generator", "b")
        assert bg.transient_rows.get("Generator") == {"a", "b"}
        PyPSAService.unmark_transient("Generator", "a")
        assert bg.transient_rows.get("Generator") == {"b"}
        PyPSAService.clear_transient()
        assert not bg.transient_rows
    # The foreground's mark survived the background clear (the Risk-6 invariant).
    assert "__voll_FG" in active.transient_rows.get("Generator", set())

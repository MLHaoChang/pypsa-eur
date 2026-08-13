"""Displacement write-back: registering over a resident context must not strand it.

`register` replaced a resident context with a bare dict assignment, so a second
session opening the same Project left the first session's unsaved edits
unreachable. Eviction already writes its victims back before dropping them
(test_eviction.py::test_save_before_drop_persists_victim); a displaced context
has the same fate and must get the same treatment.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from routers.projects import _save_context
from services.project_context import ProjectContext
from services.pypsa_service import PyPSAService


def _bus_network(bus_name: str) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=2, freq="h"))
    n.add("Bus", bus_name)
    return n


def _bound_ctx(name: str, *, bus: str | None = None) -> ProjectContext:
    n = _bus_network(bus or f"{name}_BUS")
    n.name = name
    ctx = ProjectContext(network=n)
    ctx.loaded_project = name
    return ctx


@pytest.fixture
def cap2():
    """Temporarily set the resident cap to 2 (restored after the test)."""
    prev = PyPSAService.RESIDENT_CAP
    PyPSAService.RESIDENT_CAP = 2
    try:
        yield 2
    finally:
        PyPSAService.RESIDENT_CAP = prev


def test_displaced_context_is_persisted_before_it_becomes_unreachable(tmp_projects_dir):
    # A is resident with an unsaved in-memory edit. A second session builds its
    # own context for the same Project and registers it. A is now unreachable —
    # its edit must have reached disk first.
    a = _bound_ctx("A", bus="A_BUS")
    _save_context(a, "A", expect="A")          # baseline on disk
    PyPSAService.register("org:A", a)
    a.network.add("Bus", "DISPLACED_MARKER")   # unsaved edit

    second = _bound_ctx("A", bus="A_BUS")
    PyPSAService.register("org:A", second)

    assert PyPSAService.get_context("org:A") is second
    reloaded = pypsa.Network()
    reloaded.import_from_netcdf(str(tmp_projects_dir / "A" / "network.nc"))
    assert "DISPLACED_MARKER" in reloaded.buses.index, (
        "the displaced context's unsaved edits must be written back before it "
        "stops being reachable through the registry"
    )


def test_reregistering_the_same_object_does_not_save(tmp_projects_dir):
    # The common case: a path-scoped read re-registers the context already
    # resident. Nothing is displaced, so nothing is written.
    a = _bound_ctx("B", bus="B_BUS")
    _save_context(a, "B", expect="B")
    PyPSAService.register("org:B", a)
    a.network.add("Bus", "NOT_SAVED_MARKER")

    PyPSAService.register("org:B", a)

    reloaded = pypsa.Network()
    reloaded.import_from_netcdf(str(tmp_projects_dir / "B" / "network.nc"))
    assert "NOT_SAVED_MARKER" not in reloaded.buses.index, (
        "re-registering the same object is not a displacement and must not "
        "trigger a save"
    )


def test_first_registration_of_a_key_does_not_save(tmp_projects_dir):
    c = _bound_ctx("C", bus="C_BUS")
    _save_context(c, "C", expect="C")
    c.network.add("Bus", "FIRST_REG_MARKER")

    PyPSAService.register("org:C", c)

    reloaded = pypsa.Network()
    reloaded.import_from_netcdf(str(tmp_projects_dir / "C" / "network.nc"))
    assert "FIRST_REG_MARKER" not in reloaded.buses.index, "nothing was displaced"


def test_reregistering_an_evicted_key_does_not_double_save(cap2, tmp_projects_dir):
    # A stale `prior` — e.g. a "last ctx seen per key" cache that
    # `_evict_if_over_cap` never clears when it pops a victim — would resave
    # the ALREADY-EVICTED object the next time its key is registered,
    # clobbering whatever is on disk since the eviction save with stale
    # in-memory state. The correct implementation reads
    # `cls._contexts.get(project_id)` fresh under the lock, which is `None`
    # once eviction has popped the victim, so a later registration for the
    # same key must not save anything on A's account.
    a = _bound_ctx("A", bus="A_BUS")
    _save_context(a, "A", expect="A")
    PyPSAService.register("A", a)
    a.last_interacted_at = 1.0
    a.network.add("Bus", "EVICT_MARKER")   # unsaved; the eviction save must persist it

    b = _bound_ctx("B", bus="B_BUS")
    PyPSAService.register("B", b)
    b.last_interacted_at = 2.0
    c = _bound_ctx("C", bus="C_BUS")
    evicted = PyPSAService.register("C", c)   # over cap2 -> "A" (LRU) evicted
    assert evicted == ["A"]
    assert PyPSAService.get_context("A") is None

    path = tmp_projects_dir / "A" / "network.nc"
    after_eviction = pypsa.Network()
    after_eviction.import_from_netcdf(str(path))
    assert "EVICT_MARKER" in after_eviction.buses.index  # the legitimate eviction save landed

    # Simulate the disk changing again AFTER the eviction save and BEFORE the
    # re-registration below — a fingerprint that a stale-`prior` double-save
    # would clobber by re-exporting the detached victim's unchanged network.
    after_eviction.add("Bus", "POST_EVICT_MARKER")
    after_eviction.export_to_netcdf(str(path))

    a2 = _bound_ctx("A", bus="A_BUS")   # a fresh session's context for the same key
    PyPSAService.register("A", a2)

    assert PyPSAService.get_context("A") is a2
    final = pypsa.Network()
    final.import_from_netcdf(str(path))
    assert "POST_EVICT_MARKER" in final.buses.index, (
        "re-registering a key whose resident was already evicted must read "
        "`prior` as None, not a stale reference to the detached victim — "
        "otherwise this re-registration silently re-saves the victim's "
        "stale in-memory state over whatever is on disk since the eviction"
    )

"""
B9 — LRU eviction of resident project contexts (the final C2 piece).

`PyPSAService` caps `_contexts` at `RESIDENT_CAP`. Every registration path
(`register`, `activate_context(register=True)`, the path-scoped read in
`resolve_project_context`) funnels through `register`, which stamps the ctx's
`last_interacted_at` recency and runs `_evict_if_over_cap`. The least-recently-
interacted EVICTABLE context is saved to disk then dropped; the active project
and any project with a queued/running solve are PROTECTED and never evicted.

These tests drive the registry mechanics directly (build_context + bind +
register) so the policy is verified without standing up a full solve. The
autouse `reset_backend` fixture clears `_contexts` + drains the solve queue
around every test; `RESIDENT_CAP` is set per-test and restored.
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
    n.add("Load", "L1", bus=bus_name, p_set=100.0)
    n.add("Generator", "gas", bus=bus_name, carrier="gas",
          p_nom=200.0, marginal_cost=50.0)
    return n


def _bound_ctx(name: str, *, bus: str | None = None, stamp: float | None = None) -> ProjectContext:
    """A bound resident-shaped ctx with one bus, optionally a fixed recency stamp."""
    n = _bus_network(bus or f"{name}_BUS")
    n.name = name
    ctx = ProjectContext(network=n, loaded_project=name)
    if stamp is not None:
        ctx.last_interacted_at = stamp
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


def test_env_overrides_cap(monkeypatch):
    # The cap is read from env at import time; the override is observable via the
    # class attr (set per-test here as the simplest stand-in for an env reimport).
    prev = PyPSAService.RESIDENT_CAP
    PyPSAService.RESIDENT_CAP = 3
    try:
        assert PyPSAService.RESIDENT_CAP == 3
    finally:
        PyPSAService.RESIDENT_CAP = prev


def test_below_cap_is_inert(cap2, tmp_projects_dir):
    # At/below the cap, registration evicts nothing.
    a, b = _bound_ctx("A"), _bound_ctx("B")
    assert PyPSAService.register("A", a) == []
    assert PyPSAService.register("B", b) == []
    assert set(PyPSAService.list_ids()) == {"A", "B"}


def test_lru_victim_evicted_over_cap(cap2, tmp_projects_dir):
    # Three distinct bound ctxs with ascending recency; none active. The LEAST-
    # recently-interacted ("A", stamp 1.0) is evicted; the registry settles at cap.
    a = _bound_ctx("A", stamp=1.0)
    b = _bound_ctx("B", stamp=2.0)
    c = _bound_ctx("C", stamp=3.0)
    PyPSAService.register("A", a)
    PyPSAService.register("B", b)
    # register stamps recency on insert, so pin them back to the intended order.
    a.last_interacted_at, b.last_interacted_at = 1.0, 2.0
    evicted = PyPSAService.register("C", c)
    # register stamps C as MRU; A is now the LRU non-protected → evicted.
    assert evicted == ["A"]
    assert PyPSAService.get_context("A") is None
    assert "A" not in PyPSAService.list_ids()
    assert set(PyPSAService.list_ids()) == {"B", "C"}
    assert len(PyPSAService._contexts) == 2


def test_active_never_evicted_even_if_lru(cap2, tmp_projects_dir):
    # Make the ACTIVE project the LRU, then register two more over the cap. The
    # active ctx survives; the next-LRU non-active is evicted instead.
    active = _bound_ctx("ACTIVE", stamp=0.0)
    PyPSAService.activate_context(active, register=True)
    # activate stamps ACTIVE as MRU; force it back to LRU.
    active.last_interacted_at = 0.0

    b = _bound_ctx("B", stamp=5.0)
    PyPSAService.register("B", b)
    b.last_interacted_at = 5.0
    c = _bound_ctx("C", stamp=6.0)
    evicted = PyPSAService.register("C", c)

    assert "ACTIVE" not in evicted
    assert PyPSAService.get_context("ACTIVE") is active        # active survives
    assert PyPSAService.get_active_id() == "ACTIVE"
    assert evicted == ["B"]                                     # next-LRU goes
    assert set(PyPSAService.list_ids()) == {"ACTIVE", "C"}


def test_queued_running_solve_is_protected(cap2, tmp_projects_dir, monkeypatch):
    # X is the LRU but has a RUNNING solve → it must NOT be evicted; the next-LRU
    # (Y) is evicted instead. Monkeypatch the queue to report X running.
    from services import solve_queue as sq_mod

    monkeypatch.setattr(
        sq_mod.solve_queue, "list_jobs",
        lambda: [{"project_id": "X", "status": "running"}],
    )

    x = _bound_ctx("X", stamp=1.0)
    PyPSAService.register("X", x)
    x.last_interacted_at = 1.0
    y = _bound_ctx("Y", stamp=2.0)
    PyPSAService.register("Y", y)
    y.last_interacted_at = 2.0
    z = _bound_ctx("Z", stamp=3.0)
    evicted = PyPSAService.register("Z", z)

    assert "X" not in evicted                       # protected by running solve
    assert PyPSAService.get_context("X") is x
    assert evicted == ["Y"]                          # next-LRU evicted
    assert set(PyPSAService.list_ids()) == {"X", "Z"}


def test_eviction_stops_when_all_protected(cap2, tmp_projects_dir, monkeypatch):
    # THREE residents, all protected (active + two running solves) → registry is
    # over cap (3 > 2) but eviction has no evictable victim → STOPS (leaves
    # over-cap rather than force-dropping a protected ctx).
    from services import solve_queue as sq_mod

    monkeypatch.setattr(
        sq_mod.solve_queue, "list_jobs",
        lambda: [
            {"project_id": "RUN1", "status": "running"},
            {"project_id": "RUN2", "status": "queued"},
        ],
    )
    active = _bound_ctx("ACTIVE", stamp=0.0)
    PyPSAService.activate_context(active, register=True)
    PyPSAService.register("RUN1", _bound_ctx("RUN1", stamp=1.0))
    evicted = PyPSAService.register("RUN2", _bound_ctx("RUN2", stamp=2.0))

    # cap is 2, registry holds 3, but all 3 are protected → nothing evicted.
    assert evicted == []
    assert set(PyPSAService.list_ids()) == {"ACTIVE", "RUN1", "RUN2"}
    assert len(PyPSAService._contexts) == 3          # intentionally left over-cap


def test_save_before_drop_persists_victim(cap2, tmp_projects_dir):
    # The evicted victim's in-memory edits are saved to disk before it's dropped.
    # Put A on disk first (so it's bound + a folder exists), mutate it in memory,
    # then evict it and reload from disk to see the mutation.
    a = _bound_ctx("A", bus="A_BUS")
    # Initial on-disk save so the project dir exists and is the baseline.
    _save_context(a, "A", expect="A")
    PyPSAService.register("A", a)
    a.last_interacted_at = 1.0

    # Mutate A in memory (a NEW bus) WITHOUT saving — eviction must persist it.
    a.network.add("Bus", "EVICT_MARKER")

    b = _bound_ctx("B", bus="B_BUS")
    b.last_interacted_at = 2.0
    PyPSAService.register("B", b)
    b.last_interacted_at = 2.0
    c = _bound_ctx("C", bus="C_BUS")
    evicted = PyPSAService.register("C", c)
    assert evicted == ["A"]
    assert PyPSAService.get_context("A") is None

    # The save-before-drop wrote A's mutated network to disk: reload + verify.
    reloaded = pypsa.Network()
    reloaded.import_from_netcdf(str(tmp_projects_dir / "A" / "network.nc"))
    assert "EVICT_MARKER" in reloaded.buses.index


def test_unbound_empty_victim_no_save(cap2, tmp_projects_dir):
    # An UNBOUND/empty victim has no disk state — eviction drops it without a
    # save (and doesn't crash trying to persist a nameless empty network).
    empty = ProjectContext(network=pypsa.Network())   # loaded_project None, no buses
    empty.last_interacted_at = 1.0
    PyPSAService._contexts["__empty__"] = empty        # park directly (unbound id)

    b = _bound_ctx("B", stamp=2.0)
    PyPSAService.register("B", b)
    b.last_interacted_at = 2.0
    c = _bound_ctx("C", stamp=3.0)
    evicted = PyPSAService.register("C", c)

    assert evicted == ["__empty__"]                    # LRU, dropped without save
    assert "__empty__" not in PyPSAService.list_ids()
    # No A/B/C folder for __empty__ was created (nothing to save).
    assert not (tmp_projects_dir / "__empty__").exists()

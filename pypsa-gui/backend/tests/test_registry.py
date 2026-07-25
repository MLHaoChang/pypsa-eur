"""
B2 — resident-context registry mechanics.

`PyPSAService` gained a `{project_id: ProjectContext}` registry + the
register/get_context/set_active/drop/list_ids/get_active_id methods. B2 ships it
DORMANT (production code still resolves everything through `_active`); these tests
exercise the data structure directly so the mechanics are verified before B6
(path-scoped endpoints) and B8 (instant tab switch) wire them into production.

The autouse `reset_backend` fixture (conftest) clears `_contexts` + resets
`_active` around every test, so registrations here can't bleed across tests.
"""
from __future__ import annotations

import pypsa
import pytest

from services.project_context import ProjectContext
from services.pypsa_service import PyPSAService


def _ctx(name: str) -> ProjectContext:
    n = pypsa.Network()
    n.name = name
    return ProjectContext(network=n, loaded_project=name)


def test_register_get_context_list_ids():
    a, b = _ctx("A"), _ctx("B")
    PyPSAService.register("A", a)
    PyPSAService.register("B", b)
    assert PyPSAService.get_context("A") is a
    assert PyPSAService.get_context("B") is b
    assert PyPSAService.get_context("missing") is None
    assert set(PyPSAService.list_ids()) == {"A", "B"}


def test_set_active_makes_get_network_follow():
    a, b = _ctx("A"), _ctx("B")
    PyPSAService.register("A", a)
    PyPSAService.register("B", b)
    PyPSAService.set_active("A")
    assert PyPSAService.get_network() is a.network
    assert PyPSAService.get_active_id() == "A"
    PyPSAService.set_active("B")
    assert PyPSAService.get_network() is b.network
    assert PyPSAService.get_active_id() == "B"


def test_set_active_unknown_raises():
    with pytest.raises(KeyError):
        PyPSAService.set_active("nope")


def test_drop_removes_from_registry_and_is_idempotent():
    a = _ctx("A")
    PyPSAService.register("A", a)
    PyPSAService.drop("A")
    assert PyPSAService.get_context("A") is None
    assert "A" not in PyPSAService.list_ids()
    PyPSAService.drop("A")  # second drop is a clean no-op (no KeyError)


def test_per_context_solver_state_is_distinct():
    # Distinct contexts own distinct solver_state + lock (B1) — the precondition
    # for a background solve writing its own state without touching the foreground.
    a, b = _ctx("A"), _ctx("B")
    assert a.solver_state is not b.solver_state
    assert a.solver_state_lock is not b.solver_state_lock


def test_locks_stay_global_in_b2():
    # Mutation lock + netCDF I/O lock are SHARED across contexts in B2
    # (per-context mutation lock deferred to B4; io-lock global forever).
    assert PyPSAService.get_lock() is PyPSAService.get_lock()
    assert PyPSAService.get_netcdf_io_lock() is PyPSAService.get_netcdf_io_lock()


def test_get_active_id_none_when_unbound():
    # A fresh reset leaves an UNBOUND active context — the registry can't key it.
    PyPSAService.reset_network()
    assert PyPSAService.get_active_id() is None


def test_delete_project_evicts_resident_context(tmp_projects_dir):
    """
    Deleting a project must ALSO evict its resident context.

    Regression: delete_project removed the on-disk directory but left the
    context in the registry. `activate_project` has a resident fast path — a
    pure pointer swap with no disk I/O — so recreating a project under the SAME
    name and activating it served the DELETED project's network. A brand-new
    project from a template came up carrying ghost components that were never
    in that template, and the next save persisted them.

    Reproduced end-to-end against the live backend: create from the 3bus
    template (Bus 0/1/2), add 'qa_bus', delete, recreate the same name from
    3bus, activate -> four buses including 'qa_bus'.
    """
    from routers.projects import delete_project

    name = "ghost_probe"
    # A resident context whose network carries a component the template lacks.
    ctx = _ctx(name)
    ctx.network.add("Bus", "ghost_bus", v_nom=380.0)
    PyPSAService.register(name, ctx)
    assert name in PyPSAService.list_ids()

    # Minimal on-disk project so delete_project gets past its existence check.
    d = tmp_projects_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "network.nc").write_bytes(b"")
    (d / "metadata.json").write_text('{"name": "ghost_probe"}', encoding="utf-8")

    out = delete_project(name, cascade=True)

    assert name in out["deleted"]
    assert not d.exists(), "directory should be gone"
    assert name not in PyPSAService.list_ids(), (
        "resident context survived deletion — a project recreated under this "
        "name would activate onto the deleted project's network"
    )
    assert PyPSAService.get_context(name) is None


def test_delete_project_eviction_is_safe_for_never_resident_names(tmp_projects_dir):
    """
    `drop` is a no-op for a name that was never registered — deleting a
    project that was never activated must not raise.
    """
    from routers.projects import delete_project

    name = "never_resident"
    d = tmp_projects_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "network.nc").write_bytes(b"")
    (d / "metadata.json").write_text('{"name": "never_resident"}', encoding="utf-8")

    assert name not in PyPSAService.list_ids()
    out = delete_project(name, cascade=True)
    assert name in out["deleted"]

"""
Pytest harness for the pypsa-gui FastAPI backend.

Run with (from the repo root, using the pixi env's python):
    .pixi/envs/default/python.exe -m pytest pypsa-gui/backend/tests
or from the backend dir:
    cd pypsa-gui/backend && <pixi-python> -m pytest

The existing `qa_*.py` files in this directory are standalone PASS/FAIL scripts;
`pytest.ini`'s `python_files = test_*.py` means pytest never collects them.

Isolation: the backend holds ONE shared in-memory `pypsa.Network` singleton plus
a module-level `_state` dict (in routers.simulation). The autouse `reset_backend`
fixture resets both before AND after every test so state can't bleed between tests.
"""
from __future__ import annotations

import pathlib
import sys

# Make `main`, `routers`, `services` importable (mirrors the qa_*.py header).
_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pandas as pd
import pypsa
import pytest
from fastapi.testclient import TestClient

import main
from routers import projects as projects_router
from routers import simulation as sim_router
from routers.projects import _RESULTS_STATE_KEYS
from services import undo_service
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig


def _reset_backend_state() -> None:
    """Fresh singleton network (unbound) + cleared lifecycle/result state."""
    PyPSAService.reset_network()  # new Network, _loaded_project -> None
    sim_router._state["solver_config"] = SolverConfig()
    sim_router._state_update(**{k: None for k in _RESULTS_STATE_KEYS})
    sim_router._state_update(status="idle", condition=None, objective=None, solve_time=None)
    # Clear solver-worker handles too, so a FUTURE test that exercises
    # POST /api/simulation/run can't observe a stale in-flight worker via
    # `_solver_in_flight()` (the current suite doesn't run a solve through the
    # API, but the harness is meant to be extended). Also drop the undo stack
    # so undo/changelog state can't bleed across tests.
    sim_router._state_update(thread=None, stop_event=None, log_queue=None)
    undo_service.clear()


@pytest.fixture(autouse=True)
def reset_backend():
    """Reset shared backend state around every test (the singleton leaks otherwise)."""
    _reset_backend_state()
    yield
    _reset_backend_state()


@pytest.fixture
def client():
    """FastAPI TestClient over the real app (all routers + middleware mounted)."""
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def tmp_projects_dir(tmp_path, monkeypatch):
    """
    Redirect routers.projects.PROJECTS_DIR to a temp dir so save/load tests
    never read or write the user's real projects. The functions read the module
    global at call time, so monkeypatching the attribute is sufficient.
    """
    d = tmp_path / "projects"
    d.mkdir()
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", d)
    return d


def build_network(*, solve: bool = False, gens_weight=None, obj_weight=None) -> pypsa.Network:
    """
    Tiny single-bus network. `solve=True` runs HiGHS so `_dispatch_ready` is
    True and the /results endpoints return data. `gens_weight` / `obj_weight`
    set DIFFERENT snapshot_weightings columns so the energy(generators)-vs-
    cost(objective) basis is observable; the per-snapshot dispatch is identical
    regardless (no global constraints / storage), so the weighting only changes
    the post-hoc weighted sums.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=4, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=100.0)
    n.add("Generator", "gas", bus="B1", carrier="gas",
          p_nom=200.0, marginal_cost=50.0, capital_cost=100_000.0)
    n.add("Generator", "solar", bus="B1", carrier="solar",
          p_nom=50.0, marginal_cost=0.0, capital_cost=60_000.0, p_max_pu=0.6)
    if gens_weight is not None:
        n.snapshot_weightings["generators"] = float(gens_weight)
    if obj_weight is not None:
        n.snapshot_weightings["objective"] = float(obj_weight)
    if solve:
        n.optimize(solver_name="highs")
    return n


@pytest.fixture
def install_network():
    """
    Install a network as the live singleton, optionally binding it to a project
    name (mimics a load: sets n.name + _loaded_project). Without `name` the
    network is left UNBOUND (_loaded_project stays None) — the first-save case.
    """
    def _install(n: pypsa.Network, name: str | None = None) -> pypsa.Network:
        PyPSAService.set_network(n)
        sim_router._state["solver_config"] = SolverConfig()
        if name is not None:
            n.name = name
            PyPSAService.set_loaded_project(name)
        return n
    return _install

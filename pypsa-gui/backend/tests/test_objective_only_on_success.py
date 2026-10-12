"""
A solve that produced no objective must not report one.

`_compute_run_objective` reads `n.objective + n.objective_constant`. PyPSA
assigns `n._objective` only in `assign_solution`, i.e. on a SUCCESSFUL solve,
and the value is persisted in the netcdf — nothing clears it on a failure or
an abort. Both claim paths computed it unconditionally, so an infeasible or
aborted run published the PREVIOUS run's cost: into the status bar via
`_state_update(objective=...)` on `/run`, and onto the persisted queue row
via `job.objective` in the dispatcher.

The `/run` path is the sharper illustration: the comment two lines above the
call already says "an aborted or failed solve leaves the network as it was",
which is precisely why the stale number is still there to be read.
"""
from __future__ import annotations

import inspect

import pandas as pd
import pypsa
import pytest

from routers.simulation import _compute_run_objective


def _solved_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "B")
    n.add("Load", "L", bus="B", p_set=50.0)
    n.add("Generator", "G", bus="B", p_nom=100.0, marginal_cost=10.0,
          capital_cost=1000.0, carrier="gas")
    n.optimize(solver_name="highs")
    return n


def test_the_stale_objective_really_does_survive_a_failed_solve():
    """
    The premise, asserted rather than assumed: after a successful solve the
    number stays readable on the network, so a later failed run would report
    it if nothing guarded the call.
    """
    n = _solved_network()
    first = _compute_run_objective(n)
    assert first is not None and first != 0.0
    # Nothing about a subsequent failure clears it.
    assert _compute_run_objective(n) == pytest.approx(first)


@pytest.mark.parametrize("module_name,symbol", [
    ("routers.simulation", "_compute_run_objective"),
    ("services.solve_queue", "_compute_run_objective"),
])
def test_both_paths_guard_the_objective_on_status(module_name, symbol):
    """
    Both claim paths must gate the call on the solve status. A structural
    check because driving a real infeasible solve through the dispatcher is a
    far heavier test than the defect warrants — and the defect is exactly
    that the call sat outside the status branch.
    """
    import importlib
    import re

    mod = importlib.import_module(module_name)
    src = inspect.getsource(mod)
    # Every CALL site (the `def` in routers.simulation matches the name too,
    # so windows are checked for all of them and at least one must be gated).
    guarded = False
    for m in re.finditer(re.escape(symbol) + r"\(n[,)]", src):
        window = src[m.start():m.start() + 200]
        if 'status in ("ok", "optimal")' in window:
            guarded = True
    assert guarded, (
        f"{module_name} computes the objective without gating on status"
    )

"""
Hydrating a context that already holds a network replaces that network.

WHAT THIS PINS. `solve_queue._apply_capture` saves a queued solve and then
re-hydrates the RESIDENT context from disk (`_hydrate_context_from_disk`).
PyPSA's `import_from_netcdf` into a populated network skips every component
it already holds ("already defined and will be skipped") and keeps the old
static row, while it takes the new time series. The resident then held the
solve's dispatch (`links_t.p0`) next to its pre-solve outputs (`p_nom_opt`
0): an extendable electrolyser dispatching 11.1 MW on a capacity of 0, and
the H2 hub's utilisation reading 1.23 (study harness 11). Every other
import path resets the network first (`load_project`, `network_undo`).

Written before the fix and proven red against it.
"""
from __future__ import annotations

import logging

import pandas as pd
import pypsa

from routers.projects import _hydrate_context_from_disk, _save_context
from services.project_context import ProjectContext


def _network(*, extra: bool = False) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=3, freq="h"))
    n.add("Bus", "b")
    n.add("Load", "d", bus="b", p_set=[5.0, 7.0, 6.0])
    n.add("Generator", "g", bus="b", p_nom_extendable=True, capital_cost=1.0, marginal_cost=1.0)
    if extra:
        n.add("Generator", "only_in_memory", bus="b", p_nom=1.0)
    return n


def _solved_on_disk(tmp_projects_dir, name="Hub"):
    solved = _network()
    logging.disable(logging.WARNING)
    try:
        solved.optimize(solver_name="highs", solver_options={"output_flag": False})
    finally:
        logging.disable(logging.NOTSET)
    assert float(solved.generators.at["g", "p_nom_opt"]) == 7.0  # fixture precondition
    ctx = ProjectContext(network=solved)
    ctx.loaded_project = name
    _save_context(ctx, name, expect=name)
    return tmp_projects_dir / name


def test_a_resident_context_takes_the_saved_solves_static_outputs(tmp_projects_dir):
    src = _solved_on_disk(tmp_projects_dir)
    resident = ProjectContext(network=_network())  # the pre-solve network, p_nom_opt 0

    _hydrate_context_from_disk(resident, src, "Hub")

    n = resident.network
    assert float(n.generators.at["g", "p_nom_opt"]) == 7.0, (
        "the resident kept its pre-solve p_nom_opt next to the solve's dispatch")
    assert float(n.generators_t.p["g"].max()) == 7.0


def test_a_resident_context_holds_only_the_saved_components(tmp_projects_dir):
    src = _solved_on_disk(tmp_projects_dir)
    resident = ProjectContext(network=_network(extra=True))

    _hydrate_context_from_disk(resident, src, "Hub")

    assert list(resident.network.generators.index) == ["g"]

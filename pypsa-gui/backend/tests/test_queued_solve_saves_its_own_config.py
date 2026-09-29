"""
A solve must be saved beside the config that produced it.

`solve_queue._run_solve_job` deliberately solves with the config snapshotted at
ENQUEUE time rather than whatever the context holds now — the module comment
records why: a `PUT /solver_config` after enqueue used to change the solve
silently. But it never wrote that snapshot back, and `_save_context` persists
`solver_config.json` from `ctx.solver_state["solver_config"]`.

So `network.nc` held the snapshot-config solve while the `solver_config.json`
written beside it described whatever the context happened to hold. The original
defect was not closed — it moved from the solve to the RECORD of the solve,
which is worse: the mismatch is silent and durable, and the two files are what
a later reader reconciles against each other.

The repair is an explicit override rather than writing the snapshot into the
live context: the user may have edited their config after enqueueing, and that
edit is theirs to keep. Only the FILE has to match the solve.
"""
from __future__ import annotations

import json

import pytest

from routers.projects import _save_context
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig
from tests.conftest import build_network


@pytest.fixture
def bound(install_network, api_project, project_storage_dir):
    name = api_project("cfgmatch")
    install_network(build_network(), name=name)
    ctx = PyPSAService.get_active_context()
    # What the CONTEXT holds — the user's current config, edited after enqueue.
    ctx.solver_state["solver_config"] = SolverConfig(voll=1111.0)
    return {"name": name, "ctx": ctx, "dir": project_storage_dir(name)}


def _saved_config(directory) -> dict:
    return json.loads((directory / "solver_config.json").read_text(encoding="utf-8"))


def test_the_override_is_what_lands_on_disk(bound):
    # What the JOB solved with — the enqueue-time snapshot.
    solved_with = SolverConfig(voll=2222.0)
    _save_context(bound["ctx"], bound["name"], force=True,
                  storage_dir=bound["dir"], solver_config_override=solved_with)
    assert _saved_config(bound["dir"])["voll"] == 2222.0, (
        "the solve was saved beside a config that did not produce it"
    )


def test_the_override_does_not_disturb_the_live_context(bound):
    _save_context(bound["ctx"], bound["name"], force=True,
                  storage_dir=bound["dir"],
                  solver_config_override=SolverConfig(voll=2222.0))
    assert bound["ctx"].solver_state["solver_config"].voll == 1111.0, (
        "persisting the job's config overwrote the user's own config"
    )


def test_without_an_override_the_context_config_is_saved(bound):
    # The control: every ordinary save must behave exactly as before.
    _save_context(bound["ctx"], bound["name"], force=True,
                  storage_dir=bound["dir"])
    assert _saved_config(bound["dir"])["voll"] == 1111.0

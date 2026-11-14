# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
# ruff: noqa: F811 -- the `baseline` fixture is imported by name
"""Study harness 14, S-B2b (GK B (59) G3): the sweep's children through the
fork helper are what master's inline steps wrote, file for file."""

from __future__ import annotations

import json
from pathlib import Path

import pypsa
import pytest

from harness import shipped_fork
from services import chat_tools, project_registry, workflow_tools
from tests.test_workflow_tools import baseline, case  # noqa: F401


def _master_save(ctx, existing, spec, *, db, user):
    """Master 7d14d085's inline block of `run_sensitivity_sweep`, verbatim."""
    from routers import projects
    from services.atomic_io import atomic_write_bytes
    from services.workflow_tools import SENSITIVITY_CASE_FILE, _isolated

    ctx.loaded_project = existing.name
    ctx.org_id, ctx.project_uuid = str(existing.org_id), str(existing.id)
    ctx.storage_dir = str(project_registry.project_dir(existing))
    with _isolated(ctx):
        from services.dispatch_status import clear_dispatch

        clear_dispatch(ctx.network)
        ctx.network._objective = None
        ctx.network._objective_constant = None
    ctx.solver_state.update(status="idle", objective=None, condition=None)
    projects._enforce_project_lock(db, existing, user)
    projects._save_context(
        ctx,
        existing.name,
        storage_dir=Path(ctx.storage_dir),
        db=db,
        user=user,
        project_row=existing,
    )
    atomic_write_bytes(
        Path(ctx.storage_dir) / SENSITIVITY_CASE_FILE,
        json.dumps(spec, sort_keys=True).encode(),
    )


def _child(project_id, name):
    with chat_tools._acting() as (db, user):
        row = project_registry.find_project(db, user, name)
        return project_registry.project_dir(row)


def _frames(path):
    n = pypsa.Network(str(path / "network.nc"))
    return {
        c: getattr(n, c).sort_index(axis=1) for c in ("buses", "generators", "loads")
    }


def test_a_sweep_child_is_what_masters_inline_steps_wrote(baseline, monkeypatch):
    _, project_id, _ = baseline
    out = chat_tools.run_sensitivity_sweep(project_id, [case("Helper child", 25)])
    assert out["status"] == "submitted"
    monkeypatch.setattr(shipped_fork, "save", _master_save)
    out = chat_tools.run_sensitivity_sweep(project_id, [case("Master child", 25)])
    assert out["status"] == "submitted"
    helper, master = (
        _child(project_id, "Helper child"),
        _child(project_id, "Master child"),
    )
    assert (helper / "solver_config.json").read_bytes() == (
        master / "solver_config.json"
    ).read_bytes()
    a, b = (
        json.loads((d / workflow_tools.SENSITIVITY_CASE_FILE).read_text())
        for d in (helper, master)
    )
    assert (
        a["case"].pop("new_name") == "Helper child"
        and b["case"].pop("new_name") == "Master child"
    )
    assert a == b
    fa, fb = _frames(helper), _frames(master)
    for name in fa:
        assert fa[name].equals(fb[name]), name


def test_the_fork_helpers_take_no_patch_or_change_argument():
    import inspect

    for helper in (shipped_fork.create, shipped_fork.save, shipped_fork.held):
        params = set(inspect.signature(helper).parameters)
        assert not {"patch_cfg", "edit_network", "changes", "cases"} & params
    with pytest.raises(TypeError):
        shipped_fork.create(None, None, None, "x", "y", patch_cfg=lambda c: c)

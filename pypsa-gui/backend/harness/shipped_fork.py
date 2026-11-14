# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""The fork a sensitivity case runs on (study harness 14, S-B2b; GK B (57),
GK B (59) G1, G3).

Shared by `run_sensitivity_sweep` and the shipped-case engine
(`harness/run_shipped_engine.py`): neither tool entry calls the other, and
neither passes the other's arguments. The steps are the sweep's own, moved
here unchanged so its children stay byte-identical:

- `create`: `projects.create_scenario` copies the base's SAVED bundle into a
  new child project (the parent link set) and returns the child's row.
- `save`: binds a context to the child, clears the dispatch and objective it
  copied (only the case's own queued solve gives it results), saves it and
  writes its case record (`SENSITIVITY_CASE_FILE`).
- `held`: the child's resident context, under the locks its existing writers
  take (G1): the harness hold on the child (`harness_locks`, which every
  write route's `_enforce_project_lock` refuses from outside the run) and the
  context's own `mutation_lock` (which `_save_context` and the solve queue
  take). A read-modify-write of the child inside it cannot interleave with
  another writer. The sweep does not use it (its children are written from
  private copies before anyone can resolve them).
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path


def create(db, user, base, name: str, description: str):
    """A new child of the saved `base` named `name`: its project row."""
    from models.schemas import CreateScenarioRequest
    from routers import projects
    from services import project_registry

    request = CreateScenarioRequest(name=name, description=description)
    info = projects.create_scenario(str(base.id), request, db=db, user=user)
    return project_registry.resolve_project(db, user, str(info.id))


def save(ctx, child, spec: dict, *, db, user) -> None:
    """Bind `ctx` to `child`, drop the dispatch and objective it carries, save
    it as the child and write the child's case record `spec`."""
    from routers import projects
    from services import project_registry
    from services.atomic_io import atomic_write_bytes
    from services.dispatch_status import clear_dispatch
    from services.workflow_tools import SENSITIVITY_CASE_FILE, _isolated

    ctx.loaded_project = child.name
    ctx.org_id, ctx.project_uuid = str(child.org_id), str(child.id)
    ctx.storage_dir = str(project_registry.project_dir(child))
    # Invalidate copied dispatch/results; only the new queued solve
    # establishes results for the altered inputs.
    with _isolated(ctx):
        clear_dispatch(ctx.network)
        ctx.network._objective = None
        ctx.network._objective_constant = None
    ctx.solver_state.update(status="idle", objective=None, condition=None)
    projects._enforce_project_lock(db, child, user)
    projects._save_context(
        ctx,
        child.name,
        storage_dir=Path(ctx.storage_dir),
        db=db,
        user=user,
        project_row=child,
    )
    atomic_write_bytes(
        Path(ctx.storage_dir) / SENSITIVITY_CASE_FILE,
        json.dumps(spec, sort_keys=True).encode(),
    )


@contextlib.contextmanager
def held(child, task_id: str):
    """The child's resident context (built and registered when none is),
    held for `task_id`: the harness hold on the child and the context's
    `mutation_lock`, both released on exit."""
    from routers.projects import _hydrate_context_from_disk
    from services import harness_locks, project_registry
    from services.pypsa_service import PyPSAService

    key = project_registry.registry_key(child)
    with PyPSAService.hydrate_or_adopt(key) as resident:
        ctx = resident
        if ctx is None:
            ctx = PyPSAService.build_context()
            _hydrate_context_from_disk(
                ctx, project_registry.project_dir(child), child.name
            )
            project_registry.bind_context(ctx, child)
            PyPSAService.register(key, ctx)
    harness_locks.hold(str(child.id), task_id)
    try:
        token = harness_locks.enter_run(task_id)
        try:
            with ctx.mutation_lock:
                yield ctx
        finally:
            harness_locks.leave_run(token)
    finally:
        harness_locks.release(str(child.id), task_id)

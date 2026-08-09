# pypsa-gui

The React + FastAPI application layered over PyPSA. It owns a vocabulary that
is *not* PyPSA's, and the two overlap on several words — this file is where
that overlap is settled so a spec, a plan, or a reviewer does not have to
re-derive it.

Every term below was checked against the code at the cited location. Extend
this file with `domain-modeling` when a spec needs a term it does not define;
do not add general programming concepts.

## Language

**Project**:
An org-scoped row in the `projects` table owning a storage directory. Name and
`storage_path` are each unique per org (`backend/db/models.py:41`).
_Avoid_: workspace, model, case

**Scenario**:
A Project derived from another Project via `POST /api/projects/{base}/scenarios`,
which copies the base's chat history into the new directory
(`backend/routers/projects.py:2300`). A Scenario *is* a Project row — not a
separate entity — so anything true of Projects is true of Scenarios.
_Avoid_: variant, branch, copy

**Context** (`ctx`):
The Project currently bound to a session, and the thing most routes resolve
before doing anything (`backend/services/active_project.py`). Route handlers
reject when the active ctx is not the project named in the request.
_Avoid_: current project, session project, active model

**Snapshot**:
Overloaded across three unrelated concepts. In prose, a spec, or a plan, always
qualify it; unqualified "snapshot" is the defect, not any one of the meanings.

- **Saved snapshot** — a project state persisted to disk under the project's
  snapshot directory, addressed by `snapshot_id` (`backend/routers/snapshots.py`).
  This is the one users see.
- **Time step** — PyPSA's own meaning: one entry on the network's time index,
  carrying `snapshot_weightings`. Upstream API, not renameable.
- **State capture** — an in-memory copy taken for undo or for the dispatch-fix
  path (`_state_snapshot`, `undo_snapshot_middleware`).

**Component**:
PyPSA's term for a network element class — Bus, Generator, Link, Line, Store.
Correct in backend code and anywhere the PyPSA API is in view.

**Asset**:
The user-facing word for the same objects on the map canvas and in the
parameter table. Correct in UI copy and user-facing endpoints
(`backend/routers/asset_results.py`); prefer **Component** in backend prose so
the PyPSA mapping stays visible.

**Vintage**:
A per-investment-period capacity bound on a component
(`backend/routers/vintage.py`). Multi-period work expands these transiently
rather than storing one row per period.

**Solve**:
One optimisation run over the network — `n.optimize()`, driven through
`solver_service.run_simulation` and queued by `backend/routers/solve_queue.py`.
Always off the request thread; it blocks.
_Avoid_: simulation, optimisation run, job

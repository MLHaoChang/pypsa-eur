# Context Binding and Compare Nulls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop two live defects — a session's unsaved work being stranded when another session opens the same Project, and the Compare tab presenting unresolvable figures as €0.00.

**Architecture:** Two independent slices on one branch. Slice A makes `PyPSAService.register` save the context it displaces (reusing the detach-then-save shape eviction already uses) and makes the four endpoints that rebind the caller's own active context also move the session's DB pointer. Slice B gives the eight Compare payload blocks that lack one an `available` flag, sets it at the early-return sites, and teaches `CompareView` to render "unavailable" instead of a zero.

**Tech Stack:** FastAPI + SQLAlchemy + PyPSA (backend, Python 3.13); React + TypeScript + React Query + Vitest (frontend); pytest via `pixi run gui-tests`.

## Global Constraints

- The canonical backend test command is `pixi run gui-tests`, never `pixi run pytest` — the latter resolves an environment missing `pywebview` and silently tests the wrong thing.
- Backend tests run from `pypsa-gui/backend`; `pytest.ini` pins `python_files = test_*.py`, so a file named `qa_*.py` is never collected.
- A save takes `mutation_lock` + the netCDF I/O lock and **must never nest under `_registry_lock`**. Any new save must happen after the registry lock is released.
- Zero is a legitimate result in an energy-system model. An unresolvable figure never ships as `0.0` — see `docs/adr/0001-unresolvable-figures-ship-as-null.md` and the **Unavailable** entry in `pypsa-gui/CONTEXT.md`.
- Domain vocabulary comes from `pypsa-gui/CONTEXT.md`. In particular *snapshot* is ambiguous — say **saved snapshot**, **time step**, or **state capture**.
- Another session is active in this repo. Before each commit, run `git status --short` and stage only the files named in that task.
- TDD is required for every task. Each implementer report carries a filled-in **TDD Evidence** section: the RED command with its failing output, then the GREEN command with its passing output.

## File Structure

| File | Responsibility in this plan |
|---|---|
| `backend/services/pypsa_service.py` | `register()` gains displaced-context write-back |
| `backend/tests/test_registry_displacement.py` | new — proves the displaced context is saved |
| `backend/routers/projects.py` | 3 endpoints gain the session dep + pointer write |
| `backend/routers/snapshots.py` | `restore_snapshot` gains db/user/session + pointer write |
| `backend/tests/test_active_pointer_paths.py` | new — proves all 4 paths move the pointer |
| `backend/models/schemas.py` | `available` on 8 Comparison blocks |
| `backend/routers/compare.py` | set `available` at the early-return sites |
| `backend/tests/test_compare_availability.py` | new — no block ships a bare zero |
| `frontend/src/pages/CompareView.tsx` | per-side availability at cell level: shared primitives + 6 tabs (Task 5), bespoke tables + 4 tabs (Task 6) |
| `frontend/src/pages/CompareView.availability.test.tsx` | new — renders unavailable, not 0.00 |

---

### Task 1: `register()` saves the context it displaces

**Files:**
- Modify: `backend/services/pypsa_service.py:501-504`
- Test: `backend/tests/test_registry_displacement.py`

**Interfaces:**
- Consumes: `PyPSAService.register(project_id: str, ctx: ProjectContext) -> list[str]` (unchanged signature), `PyPSAService._save_evicted_ctx(victim_id: str, victim_ctx: ProjectContext) -> None`
- Produces: nothing new. `register`'s signature and return value are unchanged; only its side effect grows.

Background: today `register` does a bare `cls._contexts[project_id] = ctx`. When a second session opens a Project the first session holds, the first session's context object is dropped from the registry with no write-back, and its unsaved edits are unreachable. Eviction already solves this exact problem by detaching under the lock and saving outside it.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_registry_displacement.py`. This verifies the write-back **behaviourally** — mutate in memory, displace, reload `network.nc` from disk — exactly as `tests/test_eviction.py::test_save_before_drop_persists_victim` (line 169) verifies the eviction save. Do not assert that `_save_evicted_ctx` was called; that couples the test to the implementation and the repo already has the better pattern. Reuse `_bus_network`, `_bound_ctx` and the `cap2` / `tmp_projects_dir` fixtures from `test_eviction.py` by copying their definitions (they are module-local there), or import them if the module exposes them.

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `pypsa-gui/backend`:

```bash
pixi run gui-tests tests/test_registry_displacement.py -v
```

Expected: `F..` — `test_displaced_context_is_persisted_before_it_becomes_unreachable` FAILS on `assert "DISPLACED_MARKER" in reloaded.buses.index`, because the displaced context's edit never reached disk. The other two PASS already: they assert a save did *not* happen, which is true of the unfixed code too.

- [ ] **Step 3: Write the minimal implementation**

In `backend/services/pypsa_service.py`, replace the body of `register` (currently lines 501-504) with:

```python
        with cls._registry_lock:
            prior = cls._contexts.get(project_id)
            ctx.last_interacted_at = time.monotonic()
            cls._contexts[project_id] = ctx
        # Write back OUTSIDE `_registry_lock`. A displaced context is no longer
        # reachable through the registry, so its unsaved edits are lost unless
        # they are persisted here — the same reasoning, and the same
        # detach-then-save shape, as `_evict_if_over_cap`. The save takes
        # mutation_lock + the netCDF I/O lock, which must never nest under
        # `_registry_lock`.
        if prior is not None and prior is not ctx:
            cls._save_evicted_ctx(project_id, prior)
        return cls._evict_if_over_cap(protected_ids={project_id})
```

Also extend the `LOCK DISCIPLINE:` paragraph of `register`'s docstring with:

```
        A registration that REPLACES a resident context writes that context back
        to disk first, outside the lock, via `_save_evicted_ctx` — identical to
        an eviction victim, because the outcome for that context is identical:
        it stops being reachable. `prior is not ctx` keeps a plain
        re-registration (the common case) from triggering a save.
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pixi run gui-tests tests/test_registry_displacement.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Run the registry and eviction suites for regressions**

```bash
pixi run gui-tests tests/test_registry.py tests/test_eviction.py -v
```

Expected: all pass. If eviction now double-saves a victim, the guard in Step 3 is wrong — `_evict_if_over_cap` pops victims from `_contexts`, so `prior` must be `None` on a re-register after eviction.

- [ ] **Step 6: Commit**

```bash
git status --short
git add backend/services/pypsa_service.py backend/tests/test_registry_displacement.py
git commit -m "fix(registry): save the context a registration displaces

register() replaced a resident context with a bare dict assignment, so a
second session opening the same Project left the first session's unsaved
edits unreachable. Eviction already writes its victims back; a displaced
context has the same fate and now gets the same treatment, outside the
registry lock so the no-nesting invariant holds."
```

---

### Task 2: `load_project`, `import_bundle` and `create_from_template` move the session pointer

**Files:**
- Modify: `backend/routers/projects.py` — `load_project`, `import_bundle`, `create_from_template`
- Test: `backend/tests/test_active_pointer_paths.py`

**Interfaces:**
- Consumes: `deps.current_session` (FastAPI dependency returning `SessionRow | None`), `db.models.Session as SessionRow`, `services.active_project.set_active_project(db: DBSession, session: SessionRow, project: Project | None) -> None`, `services.project_registry.require_user(user) -> User`, `services.project_registry.resolve_project(db, user, id_or_name) -> Project`
- Produces: nothing new. Three endpoint signatures gain one parameter each.

Background: these three endpoints rebind the caller's own active context but never move `sessions.active_project_id`. Because `resolve_for_session` reads the DB pointer first, the next request reverts the switch — the client and backend then disagree and autosave's `expect=` guard starts returning 409. `activate_project` (`projects.py:1995-2005`) already does this correctly and is the template: the pointer is written **after** the swap succeeds, so a failed switch does not leave the session pointing somewhere it never reached.

`projects.py` already imports everything needed — `SessionRow` and `User` at line 22, `current_session` and `optional_user` at line 24, `active_project` at line 33.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_active_pointer_paths.py`. The fixtures used here all exist in `backend/tests/conftest.py`: `client` (authenticated `TestClient`), `api_project` (factory creating a real project, returns its name), `project_row` (factory returning the `Project` ORM row for a name), and `_auth_db` (yields `(engine, session_local)`). The cookie-to-session-row lookup below is the same one `session_ctx` uses at `conftest.py:342`.

```python
"""Every path that rebinds the caller's own active context also moves the
session's DB pointer.

`resolve_for_session` reads `sessions.active_project_id` before falling back to
the process context, so a path that moves only the context is reverted on the
next request. Background paths (the solve queue, which has no session) and
path-scoped reads (`resolve_project_context`) deliberately do NOT move the
pointer — the last test here pins that.
"""
from __future__ import annotations

import pytest


def _pointer(session_local, test_client) -> str | None:
    """The session's active_project_id, read the way session_ctx reads it."""
    from services.auth_service import resolve_session_row
    from settings import get_settings

    raw = test_client.cookies.get(get_settings().session_cookie_name)
    assert raw, "client has no session cookie"
    with session_local() as db:
        row = resolve_session_row(db, raw)
        return str(row.active_project_id) if row.active_project_id else None


def test_load_project_moves_the_pointer(client, api_project, project_row, _auth_db):
    _engine, session_local = _auth_db
    a = api_project("alpha")
    b = api_project("beta")
    client.post(f"/api/projects/{a}/activate")

    client.get(f"/api/projects/{b}")

    assert _pointer(session_local, client) == str(project_row(b).id), (
        "load_project rebinds the active context; the pointer must follow or "
        "the next request reverts the switch"
    )


def test_create_from_template_moves_the_pointer(client, api_project, _auth_db):
    _engine, session_local = _auth_db
    a = api_project("alpha")
    client.post(f"/api/projects/{a}/activate")
    before = _pointer(session_local, client)

    client.post("/api/projects/from-template/blank", params={"name": "fromtpl"})

    after = _pointer(session_local, client)
    assert after is not None and after != before, (
        "create_from_template binds the new Project as the active context; "
        "the pointer must follow"
    )


def test_import_bundle_moves_the_pointer(client, api_project, _auth_db, tmp_path):
    _engine, session_local = _auth_db
    a = api_project("alpha")
    b = api_project("beta")
    client.post(f"/api/projects/{a}/activate")
    before = _pointer(session_local, client)

    bundle = client.get(f"/api/projects/{b}/bundle").content
    client.post(
        "/api/projects/import",
        files={"file": ("beta.zip", bundle, "application/zip")},
        params={"name": "imported"},
    )

    after = _pointer(session_local, client)
    assert after is not None and after != before, (
        "import_bundle binds the imported Project as the active context; "
        "the pointer must follow"
    )


def test_path_scoped_read_does_not_move_the_pointer(client, api_project, _auth_db):
    _engine, session_local = _auth_db
    a = api_project("alpha")
    b = api_project("beta")
    client.post(f"/api/projects/{a}/activate")
    before = _pointer(session_local, client)

    client.get(f"/api/projects/{b}/snapshots")

    assert _pointer(session_local, client) == before, (
        "reading another Project's data must not switch the session to it"
    )
```

**One thing to verify before running:** the exact route paths for `create_from_template`, `import_bundle` and the bundle download. Read their decorators in `backend/routers/projects.py` and correct the three URLs above if they differ. Everything else — fixtures, the pointer helper, the assertions — is accurate as written.

- [ ] **Step 2: Run the test to verify it fails**

```bash
pixi run gui-tests tests/test_active_pointer_paths.py -v
```

Expected: the three pointer-moving tests FAIL (the pointer still names project `a`); the path-scoped-read test PASSES.

- [ ] **Step 3: Add the session dependency to the three endpoints**

For each of `load_project`, `import_bundle` and `create_from_template` in `backend/routers/projects.py`, add one parameter to the signature, matching `activate_project`'s shape exactly:

```python
    session: SessionRow | None = Depends(current_session),
```

- [ ] **Step 4: Write the pointer after the swap succeeds**

In each of the three endpoints, immediately **after** the point where the context has been successfully bound and registered — for `load_project` that is directly after the `PyPSAService.register(...)` call near line 2216 — insert:

```python
    # Persist the pointer, mirroring activate_project. Written AFTER the swap
    # succeeds so a failed load does not leave the session pointing at a project
    # it never reached. Without this, `resolve_for_session` reads the stale
    # pointer on the next request and silently reverts the switch.
    if session is not None:
        active_project.set_active_project(db, session, project)
```

`load_project` and `create_from_template` already hold a resolved `project` row. In `import_bundle`, resolve it after the import completes, using the name the import produced:

```python
    if session is not None:
        imported_row = project_registry.find_project(
            db, project_registry.require_user(user), imported_name
        )
        if imported_row is not None:
            active_project.set_active_project(db, session, imported_row)
```

Substitute `imported_name` with whatever local variable that endpoint already uses for the created project's display name — do not introduce a new one.

- [ ] **Step 5: Run the test to verify it passes**

```bash
pixi run gui-tests tests/test_active_pointer_paths.py -v
```

Expected: all pass, including the path-scoped-read test, which must still pass unchanged.

- [ ] **Step 6: Run the project suites for regressions**

```bash
pixi run gui-tests tests/test_storage_layout.py tests/test_project_dir_resolver.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git status --short
git add backend/routers/projects.py backend/tests/test_active_pointer_paths.py
git commit -m "fix(projects): move the session pointer when the active context rebinds

load_project, import_bundle and create_from_template rebound the caller's
active context without moving sessions.active_project_id. resolve_for_session
reads that pointer first, so the next request reverted the switch and the
client and backend disagreed. Follows activate_project: written after the
swap succeeds."
```

---

### Task 3: `restore_snapshot` moves the session pointer

**Files:**
- Modify: `backend/routers/snapshots.py` — `restore_snapshot` (signature, and after the bind near line 505)
- Test: `backend/tests/test_active_pointer_paths.py` (extend)

**Interfaces:**
- Consumes: same as Task 2, plus `routers.deps.AuthorizedProject` (a dataclass carrying `name`, `directory`, `uuid`, `org_id`, `registry_key`)
- Produces: nothing new.

Background: `restore_snapshot` rebinds the caller's active context exactly as `load_project` does, so the user ends up viewing the restored Project and the pointer must follow. It is split from Task 2 because it needs three new dependencies rather than one: its signature is `(snapshot_id: str, project: AuthorizedProject = ProjectAccessDep)` with no `db`, no `user` and no `session`. `AuthorizedProject` carries `uuid` and `org_id` but **not** the `Project` ORM row, and `set_active_project` needs the row.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_active_pointer_paths.py`, reusing the `_pointer` helper already defined there:

```python
def test_restore_snapshot_moves_the_pointer(client, api_project, project_row, _auth_db):
    _engine, session_local = _auth_db
    a = api_project("alpha")
    b = api_project("beta")
    client.post(f"/api/projects/{b}/snapshots", json={"label": "before"})
    client.post(f"/api/projects/{a}/activate")

    snap_id = client.get(f"/api/projects/{b}/snapshots").json()[0]["id"]
    client.post(f"/api/projects/{b}/snapshots/{snap_id}/restore")

    assert _pointer(session_local, client) == str(project_row(b).id), (
        "restoring a saved snapshot rebinds the active context to that Project; "
        "the pointer must follow"
    )
```

**Verify before running:** the snapshot create/list/restore route paths and the field name for a saved snapshot's id (`id` vs `snapshot_id`) in `backend/routers/snapshots.py`. Correct the URLs and the key above if they differ; the fixtures and the assertion are accurate as written.

- [ ] **Step 2: Run the test to verify it fails**

```bash
pixi run gui-tests tests/test_active_pointer_paths.py::test_restore_snapshot_moves_the_pointer -v
```

Expected: FAIL — the pointer still names project `a`.

- [ ] **Step 3: Add the three dependencies**

In `backend/routers/snapshots.py`, add to the imports:

```python
from db.models import Session as SessionRow, User
from deps import current_session, optional_user
from db.session import get_db          # match the exact import path projects.py uses
from fastapi import Depends
from services import active_project, project_registry
```

Only add the names that are not already imported — check the existing import block first (line 39 already imports `AuthorizedProject, ProjectAccessDep`).

Change the signature to:

```python
def restore_snapshot(
    snapshot_id: str,
    project: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
    session: SessionRow | None = Depends(current_session),
):
```

- [ ] **Step 4: Write the pointer after the bind succeeds**

After the `with PyPSAService.get_lock():` block that ends with `PyPSAService.bind_project(...)` (around line 505), and after the solver-config load that follows it, insert:

```python
    # Restoring a saved snapshot rebinds this session's active context to that
    # Project, so the pointer follows — same rule as load_project. AuthorizedProject
    # carries the identity but not the ORM row, and set_active_project needs the row.
    if session is not None:
        project_row = project_registry.find_project(
            db, project_registry.require_user(user), project.name
        )
        if project_row is not None:
            active_project.set_active_project(db, session, project_row)
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
pixi run gui-tests tests/test_active_pointer_paths.py -v
```

Expected: all pass.

- [ ] **Step 6: Run the snapshot suite for regressions**

```bash
pixi run gui-tests -k snapshot -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git status --short
git add backend/routers/snapshots.py backend/tests/test_active_pointer_paths.py
git commit -m "fix(snapshots): move the session pointer when restore rebinds the context

restore_snapshot rebinds the caller's active context like load_project, so
the pointer must follow. Needs db/user/session added: AuthorizedProject
carries the identity but not the ORM row set_active_project requires."
```

---

### Task 4: `available` on the eight Comparison blocks that lack it

**Files:**
- Modify: `backend/models/schemas.py` — `CapacityComparison`, `DispatchComparison`, `LoadingComparison`, `PricesComparison`, `EmissionsComparison`, `EconomicsComparison`, `CurtailmentComparison`, `StorageCyclingComparison`
- Modify: `backend/routers/compare.py` — the early-return sites
- Test: `backend/tests/test_compare_availability.py`

**Interfaces:**
- Consumes: `models.schemas.CarrierPeriodValue` (`total: float = 0.0`, `by_period: dict[str, float]`)
- Produces: every `*Comparison` block gains `available: bool = False`. `LostLoadComparison.available` already exists and keeps its current meaning and default.

Background: nine Comparison blocks exist; only `LostLoadComparison` carries an availability flag. The other eight early-return a bare default instance at 21 sites in `compare.py`, and `CarrierPeriodValue.total` defaults to `0.0` — so an unresolvable figure reaches the client as a real-looking zero. `results.py:3371` handles the identical failure the opposite way, setting a flag and nulling the fields, with a comment saying the flag exists so it "cannot happen silently again". This is a violation of ADR-0001, not a reopening of it.

Follow `LostLoadComparison`'s shape: a defaulted `available: bool = False` plus a docstring stating what `True` guarantees.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_compare_availability.py`:

```python
"""No Comparison block may ship a figure without saying whether it resolved.

ADR-0001: zero is a legitimate result in an energy-system model, so an
unresolvable figure must never be indistinguishable from a real zero. Every
block therefore carries `available`, and `available=False` is the only way to
ship the default zeros.
"""
from __future__ import annotations

import inspect

import pytest

from models import schemas


def _comparison_models():
    for name, obj in vars(schemas).items():
        if (
            inspect.isclass(obj)
            and name.endswith("Comparison")
            and hasattr(obj, "model_fields")
        ):
            yield name, obj


def test_every_comparison_block_declares_available():
    missing = [n for n, m in _comparison_models() if "available" not in m.model_fields]
    assert missing == [], (
        f"these Comparison blocks can ship a zero indistinguishable from a real "
        f"result: {missing}"
    )


def test_available_defaults_to_false():
    wrong = [
        n for n, m in _comparison_models()
        if m.model_fields["available"].default is not False
    ]
    assert wrong == [], (
        f"a default-constructed block is the early-return path and has resolved "
        f"nothing, so it must default to unavailable: {wrong}"
    )


def test_at_least_nine_blocks_are_covered():
    assert len(list(_comparison_models())) >= 9, (
        "the suite found fewer blocks than exist — the discovery filter is wrong"
    )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pixi run gui-tests tests/test_compare_availability.py -v
```

Expected: `test_every_comparison_block_declares_available` FAILS listing the eight blocks; `test_at_least_nine_blocks_are_covered` PASSES.

- [ ] **Step 3: Add the field to the eight blocks**

In `backend/models/schemas.py`, add to each of the eight blocks named above, as the first field:

```python
    # False means this block resolved nothing and every figure below is a
    # default zero, not a measurement — see ADR-0001. True guarantees the
    # figures were computed from a solved network.
    available: bool = False
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pixi run gui-tests tests/test_compare_availability.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Set the flag on the success paths in `compare.py`**

Every `_compute_*_summary` in `backend/routers/compare.py` has early `return XComparison()` statements (23 in total, not 21 — this plan undercounted) and one success path that populates the block. On each **success** path, set `available=True`.

Most early returns keep the `False` default, which is correct: they fire when nothing resolved. **Three do not**, and were corrected by ruling during Task 4 after a review found them — they fire on a *solved* network that simply contains nothing of that kind, so the zero is the real answer and the block is available:

- `compare.py:2314` — no generator has a time-varying `p_max_pu`, so curtailment is genuinely 0 GWh
- `compare.py:2216` — no generators
- `compare.py:2551` — no storage units, so 0 cycles is the answer

`CapacityComparison` is the one function that takes `has_solve` and never reads it; its success path must be `available=has_solve`, not an unconditional `True`. Pre-solve it computes nothing at all — `_walk_plain:710` skips every asset because `p_nom_opt` defaults to `0.` — so an unconditional `True` asserts a falsehood on every unsolved project.

Find them with:

```bash
grep -n "return \w*Comparison(" backend/routers/compare.py
```

A populated construction gains the flag:

```python
    return EconomicsComparison(
        available=True,
        ...
    )
```

- [ ] **Step 6: Add a round-trip test and run it**

Append to `backend/tests/test_compare_availability.py`:

```python
def test_solved_golden_project_reports_available(golden_summary):
    """The golden fixture is solved, so its populated blocks must say so."""
    assert golden_summary.economics.available is True
    assert golden_summary.capacity.available is True
```

**Note for the implementer:** reuse the golden-fixture helper the existing Compare suites use — read `backend/tests/compare_support.py` and `backend/tests/test_compare_endpoint.py` and follow their setup. Adapt the attribute names to the real `ResultsSummary` field names.

```bash
pixi run gui-tests tests/test_compare_availability.py -v
```

Expected: all pass.

- [ ] **Step 7: Run the Compare suites for regressions**

```bash
pixi run gui-tests -k compare -v
```

Expected: all pass. Existing Compare tests construct blocks without `available`; because it defaults to `False`, they keep working.

- [ ] **Step 8: Commit**

```bash
git status --short
git add backend/models/schemas.py backend/routers/compare.py backend/tests/test_compare_availability.py
git commit -m "fix(compare): say whether a figure resolved instead of shipping zero

Eight of nine Comparison blocks had no availability flag and 21 early returns
shipped default zeros, which ADR-0001 exists to prevent — results.py handles
the same failure by flagging and nulling. Follows LostLoadComparison's shape."
```

---

### Task 5: per-side availability in the shared Compare primitives

> **Re-scoped 2026-08-11, after reading the code.** The original Task 5 said "CompareView branches on `available` across 9 tabs". That was wrong about where the branch lives, and it understated the work. It is replaced by Tasks 5 and 6 below. The reasoning is recorded here so the next reader does not re-derive it:
>
> When a block is unavailable its `by_carrier` (or equivalent) is `{}`. A tab whose **both** sides are unavailable therefore already falls into its own empty-data path — `EconomicsTab` prints *"No economic data — both projects have empty asset lists"*. Wrong message, but not a zero, so the headline defect is not there.
>
> The defect is the **mixed** case: one side resolved, the other not. Then the carrier union is non-empty, the tab renders its tables and charts normally, and the unresolved side's cells render `0.00` — a fabricated zero sitting next to a real figure. That is what ADR-0001 forbids, and it is what the committed RED test's third case pins.
>
> So the branch belongs at **cell level**, in the shared render primitives, not as a tab-level early return.

**Files:**
- Modify: `frontend/src/pages/CompareView.tsx` — `ABTable` (:2335), `ABBarChart` (:2288), `EconomicsTable` (:1512), and the six tabs listed below
- Test: `frontend/src/pages/CompareView.availability.test.tsx` (already committed at `612f031f`, currently RED)

**Interfaces:**
- Consumes: `available: boolean` on every Comparison block from Task 4; `COST_UNAVAILABLE` from `frontend/src/pages/results/shared.tsx:124` (its value is the string `'unavailable'`)
- Produces: `availableA?: boolean` / `availableB?: boolean` on `ABTable`, `ABBarChart` and `EconomicsTable`, **defaulting to `true`**. Task 6 relies on that default holding, so every existing call site keeps working untouched.

**The seam already exists — extend it, do not invent one.** `ABTable` (`:2372-2382`) already distinguishes *"carrier absent from this scenario"* from *"carrier present, value 0"*, rendering `—` for the first and `0 MW` for the second. Its comment reasons identically to ADR-0001: without the distinction, "project B with no heat sector shows `heat-dump: 0 MW` next to A's 500 MW — reads as 'B built nothing' when the carrier doesn't exist in B at all." You are adding a **third** state to that same ladder:

| State | Renders |
|---|---|
| side's block did not resolve | `COST_UNAVAILABLE` |
| carrier absent from this scenario | `—` (existing) |
| carrier present, value 0 | `0.00` (existing) |

Δ must render `—` whenever either side is unavailable — a delta against an unresolved figure is meaningless.

`ABBarChart` needs the same treatment and is the easier one to get wrong: plotting a zero-height bar for an unavailable side IS the defect. Omit that side's `<Bar>` entirely rather than plotting zero, and if both sides are unavailable render the marker instead of the chart.

**Scope: 6 of the 10 tabs.** These consume only `ABTable` / `ABBarChart` / `EconomicsTable`, so wiring them is mechanical once the primitives take the props:

| Tab | Line | Block on `ResultsSummary` | Call sites |
|---|---|---|---|
| `CapacityTab` | 328 | `capacity` | 4 × ABBarChart, 4 × ABTable |
| `DispatchTab` | 519 | `dispatch` | 1 + 1 |
| `EmissionsTab` | 1211 | `emissions` | 1 + 1 |
| `EconomicsTab` | 1315 | `economics` | 1 × ABBarChart, 1 × EconomicsTable |
| `CurtailmentTab` | 1693 | `curtailment` | 2 + 2 |
| `StorageCyclingTab` | 2108 | `storage_cycling` | 1 + 1 |

`EconomicsTab` must be finished in this task, because the committed RED test renders it. Also add `export` to `function EconomicsTab` — that one line is the only structural change; do not restructure the file.

**Left for Task 6** (bespoke tables, each with its own row shape): `LoadingTab`, `PricesTab`, `LostLoadTab`, `OverviewTab`.

- [ ] **Step 1: Confirm the committed test is RED for the right reason**

The test already exists at `frontend/src/pages/CompareView.availability.test.tsx`, committed at `612f031f`. Do not rewrite it. Run it first:

```bash
npx vitest run src/pages/CompareView.availability.test.tsx
```

Expected: fails at import, because `EconomicsTab` is not exported. That is the RED state. Capture the output — it is your TDD evidence.

Read the test's header comment before implementing. It records two findings worth keeping: `EconomicsComparison` carries `by_carrier`, not the `total_cost` this plan originally invented; and `has_solve: true` is mandatory in the fixture, because otherwise `EconomicsTab` returns `UnsolvedBanner` and the available and unavailable cases render identical prose — the test would then pass whatever the branch did.

- [ ] **Step 2: Add the props to `ABTable`**

`frontend/src/pages/CompareView.tsx:2335`. Add to the prop type, defaulting both to `true` so the nine existing call sites keep compiling:

```tsx
  availableA = true, availableB = true,
```
```tsx
  availableA?: boolean
  availableB?: boolean
```

In the row body (`:2372-2382`), extend the existing present/absent ladder rather than replacing it. The side's own cell:

```tsx
<td className="py-1 text-right font-mono text-text">
  {!availableA
    ? <span className="text-muted" title="This scenario's figures could not be resolved">{COST_UNAVAILABLE}</span>
    : presentA ? fmt(va) : <span className="text-muted" title="Carrier not present in this scenario">—</span>}
</td>
```

and the same for B. The Δ cell renders `—` when either side is unavailable:

```tsx
{(availableA && availableB && presentA && presentB)
  ? <Delta v={vb - va} fmt={fmt} neutral />
  : <span className="text-muted" title="Δ undefined — a scenario is unresolved or lacks this carrier">—</span>}
```

The totals row must not sum an unavailable side. Render `COST_UNAVAILABLE` in that side's total cell instead of `fmt(sumA)`.

Import `COST_UNAVAILABLE` from `'./results/shared'` alongside the existing imports.

- [ ] **Step 3: Add the props to `ABBarChart`**

`frontend/src/pages/CompareView.tsx:2288`. Same two optional props, same defaults. An unavailable side must not plot — a zero-height bar reads as a measured zero, which is the defect:

```tsx
{availableA && <Bar dataKey={aName} fill="#3b82f6" />}
{availableB && <Bar dataKey={bName} fill="#f59e0b" />}
```

and when neither side is available, return the marker instead of the chart:

```tsx
if (!availableA && !availableB) {
  return <p className="text-[11px] text-muted py-2">{COST_UNAVAILABLE}</p>
}
```

Leave the existing `data.length === 0` branch alone — "no data for this period" is a different statement from "unresolved".

- [ ] **Step 4: Add the props to `EconomicsTable` and export `EconomicsTab`**

`EconomicsTable` (`:1512`) takes `ecA` / `ecB` rather than maps, but the cell rule is identical: an unavailable side's numeric cells render `COST_UNAVAILABLE`, and any Δ or derived column against it renders `—`.

Add `export` to `function EconomicsTab` (`:1315`). That is the only structural change to the file.

- [ ] **Step 5: Wire the six tabs**

Each tab reads its own block's flag off both summaries and passes them down. `EconomicsTab` for example:

```tsx
availableA={sa.economics?.available ?? false}
availableB={sb.economics?.available ?? false}
```

**`?? false`, never `?? true`.** A payload without the field must read as unavailable; coalescing absence to available reintroduces the whole defect.

Apply to all call sites in `CapacityTab` (`capacity`), `DispatchTab` (`dispatch`), `EmissionsTab` (`emissions`), `EconomicsTab` (`economics`), `CurtailmentTab` (`curtailment`), `StorageCyclingTab` (`storage_cycling`) — 19 call sites in total. Confirm the block names against `backend/models/schemas.py`'s `ResultsSummary` rather than assuming them.

- [ ] **Step 6: Run the test to verify it passes**

```bash
npx vitest run src/pages/CompareView.availability.test.tsx
```

Expected: 2 passed.

- [ ] **Step 5: Run the frontend suite and the type check**

```bash
npx vitest run
npx tsc --noEmit
```

Expected: all pass, no type errors.

- [ ] **Step 7: Commit**

```bash
git status --short
git add frontend/src/pages/CompareView.tsx
git commit -m "fix(compare): render unavailable per side instead of a fabricated zero

The defect is the mixed case — one scenario resolved, the other not. Both
unresolved already fell into the empty-data path; one-of-each rendered the
unresolved side's cells as 0.00 next to the other's real figure, which is
exactly what ADR-0001 forbids.

The branch therefore lives at cell level in ABTable / ABBarChart /
EconomicsTable, extending the ladder ABTable already had for absent-carrier
vs present-but-zero. An unavailable side plots no bar at all: a zero-height
bar reads as a measured zero."
```

Note the test file is already committed (`612f031f`) — only `CompareView.tsx` is staged here.

---

### Task 6: per-side availability in the bespoke Compare tables

**Files:**
- Modify: `frontend/src/pages/CompareView.tsx` — `LoadingTable` (:800), `PricesTable` (:1132), `PerCarrierPricesTable` (:927), the two LostLoad tables, and the Overview tables
- Test: `frontend/src/pages/CompareView.availability.test.tsx` (extend)

**Interfaces:**
- Consumes: the `availableA` / `availableB` convention Task 5 establishes on `ABTable`, `ABBarChart` and `EconomicsTable`. Match it exactly — same prop names, same `true` defaults, same three-state ladder.
- Produces: nothing consumed later.

Background: four tabs render bespoke tables rather than the shared primitives, each with its own row shape, so they could not be wired mechanically in Task 5. They carry the same defect: a side whose block did not resolve renders `0.00`.

| Tab | Line | Block | Renders |
|---|---|---|---|
| `LoadingTab` | 628 | `loading` | `LoadingTable` × 2 (lines, links) |
| `PricesTab` | 856 | `prices` | `PricesTable`, `PerCarrierPricesTable` |
| `LostLoadTab` | 1837 | `lost_load` | `LostLoadByCarrierTable`, `LostLoadBusTable` |
| `OverviewTab` | 287 | several | `CountsTable`, `SolverTable`, `OverviewCapacityTable`, `OverviewStorageTable`, `OverviewLinksTable` |

**Check `LostLoadTab` first, before assuming it needs anything.** `LostLoadComparison` is the one block that already carried `available` before Task 4 — it was the template the other eight copied. That tab may already branch on it correctly. If it does, say so in the report and change nothing there; do not add a second mechanism beside a working one.

**`OverviewTab` needs a judgement call, so make it explicitly rather than silently.** It reads several blocks at once and its tables mix resolved and unresolved sources in one grid. Decide whether a per-cell marker or a per-table banner reads better there, state which you chose and why in the report, and keep it consistent across its five tables.

- [ ] **Step 1: Extend the test first, one tab at a time**

For each tab you touch, add the **pair** — the unavailable case AND the resolved case — following the shape of the three cases already in `CompareView.availability.test.tsx`. A test that only asserts "renders the marker when unavailable" passes against a component that always renders the marker; it proves nothing. This is not hypothetical: Task 4 shipped a Critical behind exactly that shape, and the test that caught it was the one asserting the opposite direction on the same fixture.

Each tab needs its own `export` on its function to be renderable in isolation, same as `EconomicsTab`.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
npx vitest run src/pages/CompareView.availability.test.tsx
```

- [ ] **Step 3: Implement, one table at a time**

Same three-state ladder as Task 5. `?? false` on every flag read, never `?? true`.

- [ ] **Step 4: Verify**

```bash
npx vitest run src/pages/CompareView.availability.test.tsx
npx vitest run
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git status --short
git add frontend/src/pages/CompareView.tsx frontend/src/pages/CompareView.availability.test.tsx
git commit -m "fix(compare): per-side availability in the bespoke tables

Completes the Compare half: Loading, Prices, LostLoad and Overview render
their own tables rather than the shared primitives, so Task 5's wiring did
not reach them."
```

---

### Task 7: separate "measured zero" from "could not read the capture" on lost load

> **Added 2026-08-12 by ruling**, after Task 6's review found a fabricated zero reachable on `LostLoadTab` that no frontend-only change can fix. Task 6 was told not to touch the backend; this task is that backend change.

**Files:**
- Modify: `backend/models/schemas.py` — `LostLoadComparison` (:1040)
- Modify: `backend/routers/compare.py` — `_compute_lost_load_summary`, its docstring and its eight return sites
- Modify: `frontend/src/api/types.ts`, `frontend/src/pages/CompareView.tsx` — `LostLoadTab`
- Test: `backend/tests/test_compare_availability.py`, `frontend/src/pages/CompareView.availability.test.tsx`

**Interfaces:**
- Produces: `captured: bool = False` on `LostLoadComparison`, beside the existing `available`. `available` keeps its current meaning exactly — do not redefine it, several tests and the frontend depend on it.

Background: `LostLoadComparison.available` is the one block whose `False` is overloaded. Its docstring says `False` covers "voll was zero, the project hasn't been solved, or no shedding occurred (happy path)", and `_compute_lost_load_summary`'s own docstring claims "all three are 'no shedding' states from the user's perspective". That is false. Within the `has_solve=True` path there are six `available=False` returns and only one is a measured zero:

| Line | Cause | Measured? |
|---|---|---|
| 2381 | `not has_solve` | no |
| 2384 | `results_state.pkl` absent | no |
| 2388 | `_safe_unpickle_results` raises | no |
| 2393 | `last_lost_load` absent / `lost_load_t` None | ambiguous |
| 2396 | capture DataFrame empty | ambiguous |
| 2416 | `reindex`/`mul` raises | no |
| 2422 | `total_e <= 1e-9` | **yes — a real zero** |
| 2515 | success path | **yes** |

So a project that solved but whose `results_state.pkl` is missing renders `0.0 MWh` and `0.00 M€` on the lost-load KPIs, with a signed Δ against those zeros, beside a scenario that genuinely shed load. That is the defect ADR-0001 exists to prevent, on the figure where a false zero matters most — unserved energy.

The two ambiguous rows take `captured=False`. Erring toward "we don't know" matches the rule the rest of this plan follows: absence reads as unavailable, never as a measured value.

- [ ] **Step 1: Write the failing backend test**

Add to `backend/tests/test_compare_availability.py`. Build a solved network, then drive `_compute_lost_load_summary` down the "capture unreadable" path — the simplest is a `project_dir` with no `results_state.pkl` — and assert:

```python
def test_lost_load_reports_uncaptured_when_the_capture_is_unreadable(tmp_path):
    block = _compute_lost_load_summary(n, periods, is_multi, has_solve=True, project_dir=tmp_path)
    assert block.available is False
    assert block.captured is False, (
        "a solved project whose capture cannot be read has not measured zero "
        "shedding — it has measured nothing"
    )


def test_lost_load_reports_captured_on_a_genuine_zero(...):
    # solved, capture present, total energy below the 1e-9 threshold
    assert block.available is False
    assert block.captured is True, "zero shedding is a real, measured result"
```

Read the real signature of `_compute_lost_load_summary` and its fixtures in `tests/compare_support.py` before writing; adapt the call, not the assertions.

- [ ] **Step 2: Run it and watch it fail**

```bash
pixi run gui-tests tests/test_compare_availability.py -v
```

Expected: `AttributeError` — `LostLoadComparison` has no `captured`.

- [ ] **Step 3: Add the field**

`backend/models/schemas.py`, in `LostLoadComparison`, beside `available`:

```python
    # Whether the lost-load capture was READ AT ALL, independent of what it
    # said. `available=False, captured=True` is a real measured zero — the
    # solver ran with voll > 0 and the LP shed nothing. `captured=False` means
    # we could not read the capture (no results_state.pkl, an unpickle error,
    # a mid-solve state) and therefore know nothing; the frontend must render
    # "unavailable" there, never 0.0. See ADR-0001.
    captured: bool = False
```

Correct the class docstring: `available=False` no longer implies a happy path on its own.

- [ ] **Step 4: Set it at the eight return sites**

Per the table above: `captured=True` at `:2422` and `:2515`; leave the default `False` at the other six. Also correct `_compute_lost_load_summary`'s docstring, which currently asserts all three early states are "no shedding" from the user's perspective — they are not.

- [ ] **Step 5: Verify the backend**

```bash
pixi run gui-tests tests/test_compare_availability.py -v
pixi run gui-tests -k "compare or lost_load" -v
```

- [ ] **Step 6: Wire the frontend**

Add `captured: boolean` to `LostLoadComparison` in `frontend/src/api/types.ts`. In `LostLoadTab`, pass `availableA={llA?.captured ?? false}` / `availableB={llB?.captured ?? false}` to both `ABKpiPair` sites and to the two LostLoad tables.

Note the inversion deliberately: the availability props key on **`captured`**, not on `available`. A measured zero (`captured: true, available: false`) must render `0.0 MWh` — it is a real result. Only an unread capture renders the marker.

- [ ] **Step 7: Frontend test, both directions plus the third rung**

Three cases, on the same fixture shape: `captured: false` renders the marker and no `0.0 MWh`; `captured: true, available: false` renders `0.0 MWh` and NOT the marker; `captured: true, available: true` renders the real shedding figure. The middle case is the one that distinguishes this task from a naive wiring — get it wrong and you relabel the happy path as broken, which is exactly why Task 6 refused to wire this tab.

Prove non-vacuity by hardcoding `availableB = true` and confirming RED, then revert.

- [ ] **Step 8: Commit**

```bash
git status --short
git add backend/models/schemas.py backend/routers/compare.py backend/tests/test_compare_availability.py frontend/src/api/types.ts frontend/src/pages/CompareView.tsx frontend/src/pages/CompareView.availability.test.tsx
git commit -m "fix(compare): distinguish a measured zero from an unread lost-load capture

LostLoadComparison.available was overloaded: its False covered a genuine
zero AND five states where nothing was read. A project that solved but whose
results_state.pkl is missing therefore rendered 0.0 MWh with a signed delta
beside a scenario that really shed load — on the one figure where a false
zero matters most.

captured says whether the capture was read at all. available keeps its
meaning. The frontend keys the unavailable marker on captured, so a measured
zero still renders 0.0 MWh."
```

---

## Out of scope — deliberately parked

Recorded so a reviewer does not read these as omissions:

- **The cross-session swap (#5b).** Task 1 stops a displaced context losing work; session A still lands on session B's context afterwards. The fix is one context per Project, which the `org:uuid` registry key already implies.
- **Making half-binding unrepresentable (#5b).** A fifth endpoint that rebinds the active context will hit the same trap Tasks 2 and 3 fix by hand.
- **`get_lock()` guarding the wrong context (#5b).** Snapshot routes take the caller's active-context lock while touching another Project's directory, so the lock protects nothing.
- **`Resolved[T]` (#2b).** Tasks 4 and 5 add a flag per block; unifying the five spellings of the rule in `results.py` belongs with the figure seam.
- **The figure seam (#1) and the assetWrite chokepoint (#3).** Sequenced after these defects by the grilling decision.

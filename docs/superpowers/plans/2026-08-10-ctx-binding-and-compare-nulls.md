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
| `frontend/src/pages/CompareView.tsx` | 9 tabs branch on `available` |
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

Create `backend/tests/test_registry_displacement.py`:

```python
"""Displacement write-back: registering over a resident context must not strand it.

Mirrors the setup style of test_eviction.py — build a bound, non-empty ctx
directly and drive the registry, rather than standing up a full request.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services.project_context import ProjectContext
from services.pypsa_service import PyPSAService


def _bus_network(bus_name: str) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=2, freq="h"))
    n.add("Bus", bus_name)
    return n


def _bound_ctx(name: str) -> ProjectContext:
    ctx = PyPSAService.build_context()
    n = _bus_network(f"{name}_BUS")
    n.name = name
    ctx.network = n
    ctx.loaded_project = name
    return ctx


def test_register_saves_the_context_it_displaces(monkeypatch):
    saved: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        PyPSAService,
        "_save_evicted_ctx",
        staticmethod(lambda vid, vctx: saved.append((vid, vctx.loaded_project))),
    )

    first = _bound_ctx("alpha")
    second = _bound_ctx("alpha")
    PyPSAService.register("org:alpha", first)
    PyPSAService.register("org:alpha", second)

    assert saved == [("org:alpha", "alpha")], (
        "displacing a resident context must write it back before it becomes unreachable"
    )
    assert PyPSAService.get_context("org:alpha") is second


def test_reregistering_the_same_object_saves_nothing(monkeypatch):
    saved: list[str] = []
    monkeypatch.setattr(
        PyPSAService,
        "_save_evicted_ctx",
        staticmethod(lambda vid, vctx: saved.append(vid)),
    )

    ctx = _bound_ctx("beta")
    PyPSAService.register("org:beta", ctx)
    PyPSAService.register("org:beta", ctx)

    assert saved == [], "re-registering the same object is not a displacement"


def test_first_registration_saves_nothing(monkeypatch):
    saved: list[str] = []
    monkeypatch.setattr(
        PyPSAService,
        "_save_evicted_ctx",
        staticmethod(lambda vid, vctx: saved.append(vid)),
    )

    PyPSAService.register("org:gamma", _bound_ctx("gamma"))

    assert saved == [], "nothing was displaced"
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `pypsa-gui/backend`:

```bash
pixi run gui-tests tests/test_registry_displacement.py -v
```

Expected: `test_register_saves_the_context_it_displaces` FAILS with `assert [] == [('org:alpha', 'alpha')]`. The other two PASS already (they assert absence).

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

Every `_compute_*_summary` in `backend/routers/compare.py` has one or more early `return XComparison()` statements (21 in total) and one success path that populates the block. Leave every early return exactly as it is — the new default of `False` is already correct for them. On each **success** path, set `available=True` where the populated block is constructed or returned.

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

### Task 5: `CompareView` renders "unavailable" instead of a zero

**Files:**
- Modify: `frontend/src/pages/CompareView.tsx`
- Test: `frontend/src/pages/CompareView.availability.test.tsx`

**Interfaces:**
- Consumes: `available: boolean` on every Comparison block from Task 4; `COST_UNAVAILABLE` exported from `frontend/src/pages/results/shared.tsx`
- Produces: nothing consumed by later tasks.

Background: `CompareView` has no unavailable branch today — its only `available` occurrences are `availableCarriers`, an unrelated carrier filter. So Task 4 alone changes nothing a user sees. `COST_UNAVAILABLE` already exists in `results/shared.tsx`, imported by `Economics.tsx` and `CapacityExpansion.tsx`; its own comment says it lives there so that "two tabs each spelling their own version of 'unavailable'" cannot drift. Compare is the third consumer it was built for.

- [ ] **Step 1: Write the failing test**

The tabs are internal functions taking project **names**, not data — `function EconomicsTab({ a, b }: { a: string; b: string })` at `CompareView.tsx:1315` — and each fetches via `useQuery({ queryKey: ['results-summary', a], queryFn: () => projectsApi.resultsSummary(a) })`. So the test mocks `../api/projects` and wraps in a `QueryClientProvider`, following the recipe in `src/layout/PropertiesPanel.rescale.test.tsx:55-63`.

Add one line to `CompareView.tsx` so the tab can be rendered in isolation — `export` on the existing `function EconomicsTab`. That is the whole change; do not restructure.

Create `frontend/src/pages/CompareView.availability.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { COST_UNAVAILABLE } from './results/shared'

vi.mock('../api/projects', () => ({
  projectsApi: { resultsSummary: vi.fn() },
}))

import { projectsApi } from '../api/projects'
import { EconomicsTab } from './CompareView'

const summary = (available: boolean) => ({
  economics: {
    available,
    total_cost: { total: available ? 1234.5 : 0, by_period: {} },
  },
})

function renderTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <EconomicsTab a="alpha" b="beta" />
    </QueryClientProvider>,
  )
}

describe('Compare tabs distinguish unavailable from zero', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders the unavailable marker, never a zero, when the block did not resolve', async () => {
    vi.mocked(projectsApi.resultsSummary).mockResolvedValue(summary(false) as never)
    renderTab()
    expect(await screen.findAllByText(COST_UNAVAILABLE)).not.toHaveLength(0)
    expect(screen.queryByText(/0\.00/)).toBeNull()
  })

  it('renders the figure when the block resolved', async () => {
    vi.mocked(projectsApi.resultsSummary).mockResolvedValue(summary(true) as never)
    renderTab()
    expect(await screen.findByText(/1,?234/)).toBeTruthy()
    expect(screen.queryByText(COST_UNAVAILABLE)).toBeNull()
  })
})
```

**Verify before running:** the real field names on `EconomicsComparison` (the mock's `economics.total_cost` is a placeholder shape) and how `ResultsSummary` nests the economics block. Read `backend/models/schemas.py` and the `EconomicsTab` body, then correct the `summary()` factory. Keep both assertions unchanged: the marker appears, and no `0.00` appears.

- [ ] **Step 2: Run the test to verify it fails**

Run from `pypsa-gui/frontend`:

```bash
npx vitest run src/pages/CompareView.availability.test.tsx
```

Expected: FAIL — either the component is not exported, or it renders `0.00` with no marker.

- [ ] **Step 3: Branch on `available` in every tab**

For each of the nine tabs in `frontend/src/pages/CompareView.tsx`, render `COST_UNAVAILABLE` in place of the figure when the block's `available` is false. Import it alongside the existing imports:

```tsx
import { COST_UNAVAILABLE } from './results/shared'
```

Apply the same shape at each site — the value cell renders the marker, not a number:

```tsx
{block.available ? formatEur(block.total_cost.total) : COST_UNAVAILABLE}
```

Do not coalesce a missing `available` to `true`. A block from an older payload has no flag, and treating that as available reintroduces the defect.

- [ ] **Step 4: Run the test to verify it passes**

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

- [ ] **Step 6: Commit**

```bash
git status --short
git add frontend/src/pages/CompareView.tsx frontend/src/pages/CompareView.availability.test.tsx
git commit -m "fix(compare): render unavailable instead of a zero

CompareView had no unavailable branch, so Task 4's flags changed nothing a
user saw. Reuses COST_UNAVAILABLE from results/shared, which exists so tabs
cannot each spell their own version of unavailable."
```

---

## Out of scope — deliberately parked

Recorded so a reviewer does not read these as omissions:

- **The cross-session swap (#5b).** Task 1 stops a displaced context losing work; session A still lands on session B's context afterwards. The fix is one context per Project, which the `org:uuid` registry key already implies.
- **Making half-binding unrepresentable (#5b).** A fifth endpoint that rebinds the active context will hit the same trap Tasks 2 and 3 fix by hand.
- **`get_lock()` guarding the wrong context (#5b).** Snapshot routes take the caller's active-context lock while touching another Project's directory, so the lock protects nothing.
- **`Resolved[T]` (#2b).** Tasks 4 and 5 add a flag per block; unifying the five spellings of the rule in `results.py` belongs with the figure seam.
- **The figure seam (#1) and the assetWrite chokepoint (#3).** Sequenced after these defects by the grilling decision.

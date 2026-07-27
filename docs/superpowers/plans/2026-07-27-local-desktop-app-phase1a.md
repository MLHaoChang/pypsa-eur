# PyPSA GUI Local Desktop App — Phase 1a Implementation Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make pypsa-gui run as a single-user local application in the dev environment — no login screen, SQLite, all writable paths outside the source tree, and the SPA served by FastAPI itself — with the web/multi-tenant deployment still fully working.

**Architecture:** Local mode is an additive flag, never a fork. One branch inside the existing auth middleware injects a seeded local user, so the 172 routes, the org-scoped storage layer, and the existing test suite stay unmodified. `PYPSAGUI_LOCAL_MODE` is read **per call**, never cached, so no module reloading is needed anywhere — in the app or in tests. The frontend already has an `authEnabled === false` path; making `/api/health` report it is what activates it.

**Tech Stack:** FastAPI, SQLAlchemy 2 + Alembic, SQLite (local) / Postgres (web), React 19 + Vite 6, pytest, vitest.

**Source spec:** `docs/superpowers/specs/2026-07-26-pypsa-gui-desktop-app-design.md` (workstreams D, G, B, C, A).

**Base:** `master` at `09bd7020` (Step 0b — session-bound active project). Every line reference below was verified against that commit.

**Scope:** Phase 1b (workstreams E storage model + F migration) is a separate plan. Phase 2 (H–L: shell, freeze, installers, key handling, CI) follows.

---

## Revision log

**v2 (2026-07-27)** — adversarial review found v1 would have broken the frontend build and the backend test suite. All 13 findings applied:

| # | v1 defect | v2 |
|---|---|---|
| 1 | Task 10 created `frontend/src/api/csrf.ts` — **it already exists** (`1d930244`), wired at `client.ts:114-122`. The replacement dropped `needsCsrfHeader`/`CSRF_SAFE_METHODS` (TS build failure) and made `readCsrfToken`'s parameter required (runtime crash). | Task 10 is additive only. Spec §5.6's "live CSRF bug" is **withdrawn**. |
| 2 | Task 3 deleted `PROJECTS_DIR` and repointed it at `settings.projects_root` — but `conftest.py:430` monkeypatches that attribute (9 test files depend on it), and the two are **different stores**. | Only the *default* moves. Attribute preserved. |
| 3 | Task 5 renamed `enable_sqlite_foreign_keys`; `conftest.py:151` calls it from a session-scoped autouse fixture. | Alias retained. |
| 4 | Task 13 said "after the last `include_router`" (`:519`) but `health` is at `:542` → catch-all shadows `/api/health`. | Appended after `health()`, registered GET+HEAD. |
| 5 | Tasks 8/13/15 rebuilt the app with `del sys.modules[...]` + `importlib.reload`. `del sys.modules["db.session"]` is a **no-op** for `from db import session`, so the local app kept conftest's monkeypatched `SessionLocal`, seeded into the shared in-memory DB, and left `security`/`settings` split-brained for the rest of the run. | **No reloading anywhere.** `is_local_mode()` is per-call, so tests monkeypatch the env against conftest's existing app. |
| 6 | Task 8 never created the app-data dir → `alembic upgrade` dies with "unable to open database file" on a clean machine. | `mkdir(parents=True)` added. |
| 7 | Task 6's rationale for `render_as_batch` was wrong — it is an autogenerate-rendering flag and does not change how `0002` executes. | Rationale corrected, test relaxed, `stamp` path added (G4). |
| 8 | Task 13 read `authed` from `request.state.auth_user`, only set for `/api/*` → every web-mode deep link served the login page. | Resolves the session directly. |
| 9 | Task 11's second test cleared caches *before* `setenv` then asserted `False`; it returns `True`. | Ordering fixed, plus a real integration test (C4). |
| 10 | Task 2's tests never restored the settings cache, and `legacy_root` moved without `conftest` pinning `LEGACY_ROOT` → later tests write into the developer's real app-data dir. | `finally: cache_clear()` everywhere; `LEGACY_ROOT` pinned in conftest. |
| 11 | Task 9 imported `AuthUser` from `./types` (does not exist) and targeted the wrong re-arm site. | Corrected to `../api/auth`; real site at `client.ts:147-152` incl. its 503 branch, plus `shouldRedirectToLogin` and sign-out hiding (B5). |
| 12 | In-process Alembic calls `fileConfig(...)` with `disable_existing_loggers=True`, killing app and uvicorn logging. | `configure_logger=false`. |
| 13 | Spec items G1, G4, G6, B4, B5, C4, A5, D6 had no task. | Folded into Tasks 5, 6, 7, 9, 11, 13, 14, 16. |

One reviewer claim was itself wrong and is **not** actioned: `dist/login.html` does exist, so Task 12's `/login.html` branch stays.

---

## Global Constraints

- **Both modes must keep working.** Every change is conditional on local mode or is mode-neutral. The web deployment is not being retired.
- **Never delete auth code.** Local mode bypasses; it does not remove.
- **Never reload or re-import modules in tests.** `del sys.modules["db.session"]` does not work for `from db import session`, and partial reloads split-brain `security`/`settings` for the rest of the session.
- **Never remove a name `tests/conftest.py` monkeypatches.** Check with `grep -n "<name>" tests/conftest.py` before deleting anything.
- Python ≥3.10, SQLAlchemy ≥2.0, FastAPI ≥0.115, Alembic ≥1.13.
- **Cross-platform: Windows x64 and macOS arm64.** `pathlib` throughout; explicit `encoding="utf-8"` on every text read.
- **New env vars use `PYPSAGUI_`, not `PYPSA_GUI_`** — PyPSA's option system claims the whole `PYPSA_*` namespace and already warns on every boot.
- `get_settings()` and `security.allowed_origins()` are `lru_cache`d. Any test that mutates their env must `cache_clear()` in a `finally`.
- Run the pixi Python: `../../.pixi/envs/default/bin/python` from `pypsa-gui/backend`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app_paths.py` | NEW. Per-user writable locations. Imports nothing from the package so `settings` can import it. |
| `backend/local_mode.py` | NEW. `is_local_mode()` + the seeded org/user/membership bootstrap. |
| `backend/static_gate.py` | NEW. Pure port of `decideGateRoute`; the FastAPI wiring stays in `main.py`. |
| `backend/settings.py` | MODIFY. Absolute SQLite default; `flat_projects_root`; `legacy_root` override; `frontend_dist`. |
| `backend/db/session.py` | MODIFY. SQLite WAL/timeout pragmas, `NullPool`, alias. |
| `backend/main.py` | MODIFY. Local-mode auth branch, CSRF short-circuit, health flag, first-run bootstrap, SPA catch-all. |
| `backend/routers/projects.py` | MODIFY. `PROJECTS_DIR` default only. |
| `backend/services/chat_service.py` | MODIFY. Resolve `chat.jsonl` from `ctx.storage_dir`. |
| `backend/alembic/env.py` | MODIFY. `render_as_batch=True`. |
| `backend/tools/bootstrap_local.py` | NEW. CLI wrapper around the seed (B4). |
| `frontend/src/auth/localMode.ts` | NEW. Re-arm predicate + synthetic local admin user. |
| `frontend/src/api/csrf.ts` | MODIFY (append only). `rawFetchHeaders()`. |

---

## Task 0: Confirm the tree is safe to branch from

- [ ] **Step 1: Concurrency check**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git log --oneline -3 master
git status --short
git worktree list
```

Expected: `09bd7020 feat(gui): Step 0b — session-bound active project` at the top, and no
modifications under `pypsa-gui/`. Note the second worktree
(`cursor/agent-continuation-plan-a0f0`) — it is a different workstream; do not branch from it.

If `git status` shows modified files under `pypsa-gui/backend/`, another session is working.
**Stop** and re-check later.

- [ ] **Step 2: Branch**

```bash
git checkout -b feature/local-desktop-app-impl master
```

- [ ] **Step 3: Record the baseline**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
cd ../frontend && npm test 2>&1 | tail -5
```

Write both numbers down. Every later task compares against them. A task that changes the
backend count has broken something.

---

## Task 1: Per-user writable paths

**Files:** Create `backend/app_paths.py`, `backend/tests/test_app_paths.py`

**Interfaces:** Produces `app_data_dir() -> Path`, `default_projects_root() -> Path`, `default_flat_projects_root() -> Path`, `default_database_url() -> str`.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_app_paths.py
import sys
from pathlib import Path

import app_paths


def test_app_data_dir_is_absolute_and_outside_the_source_tree():
    d = app_paths.app_data_dir()
    assert d.is_absolute()
    backend = Path(app_paths.__file__).resolve().parent
    assert backend not in d.parents and d != backend


def test_app_data_dir_is_platform_correct():
    d = app_paths.app_data_dir()
    if sys.platform == "darwin":
        assert d.parts[-3:] == ("Library", "Application Support", "PyPSA GUI")
    elif sys.platform == "win32":
        assert d.name == "PyPSA GUI"
    else:
        assert "pypsa gui" in str(d).lower()


def test_projects_root_default_is_user_visible():
    r = app_paths.default_projects_root()
    assert r.is_absolute()
    assert r.parts[-2:] == ("PyPSA GUI", "Projects")


def test_flat_root_is_distinct_from_projects_root():
    """Different stores with different layouts — see Task 3."""
    assert app_paths.default_flat_projects_root() != app_paths.default_projects_root()


def test_database_url_is_absolute_sqlite():
    url = app_paths.default_database_url()
    assert url.startswith("sqlite+pysqlite:///")
    assert Path(url.removeprefix("sqlite+pysqlite:///")).is_absolute()


def test_env_overrides_win(monkeypatch, tmp_path):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "custom"))
    assert app_paths.app_data_dir() == (tmp_path / "custom").resolve()
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_app_paths.py -v
```

Expected: `ModuleNotFoundError: No module named 'app_paths'`.

- [ ] **Step 3: Implement**

```python
# pypsa-gui/backend/app_paths.py
"""
Per-user writable locations.

Deliberately imports nothing from this package: `settings.py` imports THIS
module for its defaults, so a dependency the other way is a cycle.

Every path the application writes to must originate here. The previous defaults
were `__file__`-relative (`settings.py`) or CWD-relative (the `.env`
DATABASE_URL); both land inside a read-only app bundle once the backend is
frozen, and a macOS `.app` launched from Finder has cwd `/`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "PyPSA GUI"


def app_data_dir() -> Path:
    """Config + database. Not user-facing; survives app updates."""
    override = os.environ.get("PYPSAGUI_APP_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return (base / APP_NAME).resolve()


def default_projects_root() -> Path:
    """
    Org-scoped project store, `<root>/<org_uuid>/<project_uuid>/`.

    User-visible on purpose — being able to find, back up and zip your own
    projects is most of the point of a local app.
    """
    override = os.environ.get("PYPSAGUI_PROJECTS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / "Documents" / APP_NAME / "Projects").resolve()


def default_flat_projects_root() -> Path:
    """
    FLAT legacy store, `<root>/<display-name>/network.nc`. NOT the same as
    `default_projects_root()` — see Task 3. Kept in app-data because it is an
    implementation detail the user should not be browsing.
    """
    return app_data_dir() / "flat_projects"


def default_database_url() -> str:
    """Absolute on purpose: a relative SQLite URL resolves against cwd."""
    return f"sqlite+pysqlite:///{(app_data_dir() / 'pypsa-gui.db').as_posix()}"
```

- [ ] **Step 4: Run the tests**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_app_paths.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/app_paths.py pypsa-gui/backend/tests/test_app_paths.py
git commit -m "feat(gui): add app_paths for per-user writable locations"
```

---

## Task 2: Settings reads the new defaults

**Files:** Modify `backend/settings.py`, `backend/tests/conftest.py`; create `backend/tests/test_settings_paths.py`

**Context:** `database_url` currently defaults to Postgres; `projects_root` and `legacy_root`
to `Path(__file__).parent / "projects"` and `/ "legacy_unclaimed"`. Web deployments always set
`DATABASE_URL` explicitly, so changing the default affects only local runs.

**Critical:** `conftest.py:41,47,58` pins `DATABASE_URL`, `CORS_ALLOWED_ORIGINS` and
`PROJECTS_ROOT` — but **not** `LEGACY_ROOT`. Moving `legacy_root` to app-data without pinning
it makes `tests/test_legacy_migrate.py` and `tests/test_tenancy_api.py` create directories in
the developer's real `~/Library/Application Support/PyPSA GUI/`, leaving residue that makes
those tests history-dependent. Pin it in the same task.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_settings_paths.py
from pathlib import Path

import app_paths
import settings as settings_module


def _fresh(monkeypatch, **env):
    """Build a Settings with env applied. ALWAYS paired with a finally-clear."""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    settings_module.get_settings.cache_clear()
    return settings_module.get_settings()


def test_projects_root_default_is_outside_the_source_tree(monkeypatch, tmp_path):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.delenv("PROJECTS_ROOT", raising=False)
    monkeypatch.setenv("PYPSAGUI_PROJECTS_ROOT", str(tmp_path / "projects"))
    try:
        s = _fresh(monkeypatch)
        backend = Path(app_paths.__file__).resolve().parent
        assert backend not in Path(s.projects_root).parents
    finally:
        settings_module.get_settings.cache_clear()


def test_legacy_root_is_env_overridable(monkeypatch, tmp_path):
    try:
        s = _fresh(monkeypatch, LEGACY_ROOT=str(tmp_path / "legacy"))
        assert Path(s.legacy_root) == tmp_path / "legacy"
    finally:
        settings_module.get_settings.cache_clear()


def test_database_url_default_is_sqlite_not_postgres(monkeypatch, tmp_path):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    try:
        assert _fresh(monkeypatch).database_url.startswith("sqlite+pysqlite:///")
    finally:
        settings_module.get_settings.cache_clear()


def test_conftest_pins_legacy_root():
    """Regression guard: without this pin, legacy tests write to the real app-data dir."""
    import os
    assert os.environ.get("LEGACY_ROOT"), (
        "conftest must pin LEGACY_ROOT now that its default lives in app-data"
    )
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_settings_paths.py -v
```

Expected: the `database_url` and `conftest pins` tests fail.

- [ ] **Step 3: Edit `settings.py`**

Add `import app_paths` next to the existing `from pathlib import Path`, then replace the
three defaults:

```python
    # SQLite under the per-user app-data dir. Web deployments set DATABASE_URL
    # explicitly, so this default only ever applies to a local run — where the
    # previous Postgres default produced a 503 on every route.
    database_url: str = app_paths.default_database_url()
```

```python
    projects_root: Path = app_paths.default_projects_root()
    legacy_root: Path = app_paths.app_data_dir() / "legacy_unclaimed"
    # FLAT legacy store — see Task 3. Distinct from projects_root.
    flat_projects_root: Path = app_paths.default_flat_projects_root()
    # Built SPA — see Task 13. Overridable so a frozen app can point at its copy.
    frontend_dist: Path = Path(__file__).resolve().parent.parent / "frontend" / "dist"
```

- [ ] **Step 4: Pin `LEGACY_ROOT` in conftest**

In `tests/conftest.py`, immediately after the `PROJECTS_ROOT` pin at `:58`:

```python
# Pinned for the same reason as PROJECTS_ROOT above: `legacy_root` now defaults
# into the per-user app-data dir, and test_legacy_migrate / test_tenancy_api
# create directories under it. Without this they accumulate in the developer's
# real ~/Library/Application Support/PyPSA GUI/ and go history-dependent.
_TEST_LEGACY_ROOT = _tempfile.mkdtemp(prefix="pypsa-gui-test-legacy-")
os.environ["LEGACY_ROOT"] = _TEST_LEGACY_ROOT
```

- [ ] **Step 5: Run everything and commit**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_settings_paths.py -v
../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
```

Expected: 4 passed; full suite matches the Task 0 baseline.

```bash
git add pypsa-gui/backend/settings.py pypsa-gui/backend/tests/conftest.py \
        pypsa-gui/backend/tests/test_settings_paths.py
git commit -m "feat(gui): default settings paths to per-user writable locations"
```

---

## Task 3: Move the flat projects root out of the source tree

**Files:** Modify `backend/routers/projects.py:48`; create `backend/tests/test_projects_dir_default.py`

**Context — two roots, deliberately. Do not merge them:**

| Root | Layout | Used by |
|---|---|---|
| `routers.projects.PROJECTS_DIR` | flat, `<root>/<display-name>/network.nc` | `_safe_project_dir` (`:180-183`), `_find_direct_children` (`:462-465`), `_walk_ancestors` (`:507-511`) |
| `settings.projects_root` | org-scoped, `<root>/<org_uuid>/<project_uuid>/` | `services/storage_paths.py:10` |

`conftest.py:58` pins `PROJECTS_ROOT` to one tmpdir while `:430` monkeypatches `PROJECTS_DIR`
to a *different* one. Pointing `PROJECTS_DIR` at `projects_root` makes `_find_direct_children`
iterate org-UUID directories whose `(d / "network.nc").exists()` filter never matches, so
scenario-tree delete and reparent silently return `[]`.

**`PROJECTS_DIR` must stay a settable module attribute** — `conftest.py:430` does
`monkeypatch.setattr(projects_router, "PROJECTS_DIR", d)`, which raises `AttributeError` if
the name is gone. Nine test files depend on it. Only the *default* changes.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_projects_dir_default.py
from pathlib import Path

import app_paths
import settings as settings_module


def test_flat_root_default_is_outside_the_source_tree(monkeypatch, tmp_path):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.delenv("FLAT_PROJECTS_ROOT", raising=False)
    settings_module.get_settings.cache_clear()
    try:
        root = Path(settings_module.get_settings().flat_projects_root)
        backend = Path(app_paths.__file__).resolve().parent
        assert backend not in root.parents and root != backend
    finally:
        settings_module.get_settings.cache_clear()


def test_flat_root_is_env_overridable(monkeypatch, tmp_path):
    monkeypatch.setenv("FLAT_PROJECTS_ROOT", str(tmp_path / "flat"))
    settings_module.get_settings.cache_clear()
    try:
        assert Path(settings_module.get_settings().flat_projects_root) == tmp_path / "flat"
    finally:
        settings_module.get_settings.cache_clear()


def test_projects_dir_attribute_still_exists_and_is_settable(monkeypatch, tmp_path):
    """conftest.py:430 monkeypatches this attribute; nine test files depend on it."""
    from routers import projects as projects_router

    assert hasattr(projects_router, "PROJECTS_DIR")
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_path / "patched")
    assert projects_router.PROJECTS_DIR == tmp_path / "patched"


def test_flat_root_is_not_the_org_scoped_root(monkeypatch, tmp_path):
    """Different stores, different layouts. Merging them breaks _find_direct_children."""
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "org"))
    monkeypatch.setenv("FLAT_PROJECTS_ROOT", str(tmp_path / "flat"))
    settings_module.get_settings.cache_clear()
    try:
        s = settings_module.get_settings()
        assert Path(s.projects_root) != Path(s.flat_projects_root)
    finally:
        settings_module.get_settings.cache_clear()
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_projects_dir_default.py -v
```

Expected: the two `flat_projects_root` tests fail (the setting exists after Task 2, so if
Task 2 is done they pass — in that case this task's only real change is Step 3). The two
`PROJECTS_DIR` tests pass already; they are regression guards.

- [ ] **Step 3: Point the attribute at the setting, keeping it settable**

Replace `routers/projects.py:48`:

```python
# Initialised from settings, but deliberately left a MODULE ATTRIBUTE rather
# than a function: tests/conftest.py:430 monkeypatches this name and nine test
# files depend on that, directly or via the tmp_projects_dir fixture.
PROJECTS_DIR = pathlib.Path(get_settings().flat_projects_root)
```

Confirm `get_settings` is imported in the module; add `from settings import get_settings` if
not. Change nothing else — every existing `PROJECTS_DIR` reference keeps working, and
`routers/compare.py` and `services/chat_service.py` keep importing the same name.

- [ ] **Step 4: Run both suites**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_projects_dir_default.py -v
../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
```

Expected: 4 passed; full suite matches baseline. Any `AttributeError: has no attribute
'PROJECTS_DIR'` means the attribute was removed — revert and re-read the Context.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/routers/projects.py pypsa-gui/backend/tests/test_projects_dir_default.py
git commit -m "fix(gui): move the flat projects root out of the source tree"
```

---

## Task 4: Chat history follows the project

**Files:** Modify `backend/services/chat_service.py` (`get_persist_path`); create `backend/tests/test_chat_persist_path.py`

**Context:** `get_persist_path` builds `PROJECTS_DIR / ctx.loaded_project / "chat.jsonl"` from
the flat display name, while project data lives at `projects_root/<org>/<project>/`. Different
directories — which is why `chat.jsonl` cannot be in the export bundle.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_chat_persist_path.py
from services import chat_service


class _Ctx:
    def __init__(self, storage_dir, loaded_project):
        self.storage_dir = storage_dir
        self.loaded_project = loaded_project
        self.chat_state = type("S", (), {"persist_path": None})()


def test_persist_path_uses_storage_dir(tmp_path):
    storage = tmp_path / "org-uuid" / "project-uuid"
    storage.mkdir(parents=True)
    ctx = _Ctx(str(storage), "My Project")
    assert chat_service.get_persist_path(ctx) == storage / chat_service.CHAT_FILENAME


def test_persist_path_falls_back_when_unbound(tmp_path):
    """UNBOUND (New Project) has no project directory yet."""
    ctx = _Ctx(None, "My Project")
    p = chat_service.get_persist_path(ctx)
    assert p is None or p.name == chat_service.CHAT_FILENAME
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_chat_persist_path.py -v
```

Expected: the first test fails — the path is under the flat display name.

- [ ] **Step 3: Resolve from the bound context**

Replace the `expected = ...` construction in `get_persist_path`:

```python
    # Resolve from the BOUND context, not the display name. Project data lives
    # at projects_root/<org>/<project>/; the flat-name path was a pre-tenancy
    # leftover that put chat history in a different directory from the project
    # it belongs to — and is why it could not go in the export bundle.
    storage_dir = getattr(ctx, "storage_dir", None)
    if storage_dir:
        expected = Path(storage_dir) / CHAT_FILENAME
    else:
        from routers.projects import PROJECTS_DIR
        expected = PROJECTS_DIR / ctx.loaded_project / CHAT_FILENAME
```

- [ ] **Step 4: Run both suites**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_chat_persist_path.py -v
../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/chat_service.py pypsa-gui/backend/tests/test_chat_persist_path.py
git commit -m "fix(gui): store chat.jsonl in the project's own storage dir"
```

---

## Task 5: SQLite concurrency configuration (covers spec G1, G6)

**Files:** Modify `backend/db/session.py`; create `backend/tests/test_sqlite_pragmas.py`

**Context:** Measured on the current engine: `journal_mode: delete`, `busy_timeout: 5000`,
`QueuePool` 5 + 10 overflow. Without WAL a writer blocks every reader; past the 5s timeout
`database is locked` is swallowed by `main.py`'s bare `except` and returned as a 503 telling
a desktop user to start Postgres. `chat_tools.py` opens its own `SessionLocal()` on a pool
worker and commits, so contention is routine.

`enable_sqlite_foreign_keys` **must keep working** — `conftest.py:151` calls it from
`_auth_db`, which is session-scoped and pulled in by the autouse `_reset_tenant_tables` and
`_acting_user` fixtures, i.e. every test.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_sqlite_pragmas.py
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

import db.session as db_session_module
from db.session import configure_sqlite


def test_wal_and_busy_timeout_are_set(tmp_path):
    db = tmp_path / "t.db"
    engine = configure_sqlite(create_engine(f"sqlite+pysqlite:///{db.as_posix()}"))
    with engine.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert c.execute(text("PRAGMA busy_timeout")).scalar() >= 30000
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
    engine.dispose()


def test_non_sqlite_engine_is_returned_untouched():
    engine = create_engine("postgresql+psycopg://u:p@localhost/db")
    assert configure_sqlite(engine) is engine


def test_old_name_is_still_callable():
    """conftest.py:151 calls this from a session-scoped autouse fixture."""
    assert db_session_module.enable_sqlite_foreign_keys is configure_sqlite


def test_sqlite_uses_nullpool(monkeypatch, tmp_path):
    """Spec G1. QueuePool holds up to 15 connections against one file; with WAL
    and a single local user, pooling buys nothing and multiplies lock windows."""
    import settings as settings_module

    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'p.db').as_posix()}")
    settings_module.get_settings.cache_clear()
    db_session_module.get_engine.cache_clear()
    try:
        assert isinstance(db_session_module.get_engine().pool, NullPool)
    finally:
        settings_module.get_settings.cache_clear()
        db_session_module.get_engine.cache_clear()
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_sqlite_pragmas.py -v
```

Expected: `ImportError: cannot import name 'configure_sqlite'`.

- [ ] **Step 3: Rename, extend, alias, and switch the pool**

In `db/session.py`, rename `enable_sqlite_foreign_keys` to `configure_sqlite` and replace the
pragma body:

```python
def configure_sqlite(engine: Engine) -> Engine:
    """
    Per-connection SQLite pragmas. No-op on Postgres.

    foreign_keys — SQLite ships with enforcement OFF, per connection. Without
    it every ON DELETE SET NULL / CASCADE in db/models.py is inert.

    journal_mode=WAL — without it a writer blocks every reader. chat_tools
    opens its own SessionLocal on a pool worker and commits while the request
    path reads, so contention is routine, not theoretical.

    busy_timeout — 5s (the default) is too short for that. Past it,
    `database is locked` surfaces through main.py's bare except as a 503.

    synchronous=NORMAL — safe under WAL and materially faster than FULL.
    """
    if not engine.url.get_backend_name().startswith("sqlite"):
        return engine

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
        finally:
            cursor.close()

    return engine


# Retained: tests/conftest.py:151 calls this from a session-scoped autouse
# fixture, so renaming it outright errors every test in the suite.
enable_sqlite_foreign_keys = configure_sqlite
```

Update `get_engine`:

```python
@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        # NullPool: one local user, one file. QueuePool's 5+10 connections buy
        # nothing here and widen the window in which a writer holds the file.
        # In-memory URLs are exempt — NullPool would discard the database
        # between connections, which is how the test suite's shared
        # `:memory:` DB works.
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if ":memory:" not in url:
            kwargs["poolclass"] = NullPool
    return configure_sqlite(create_engine(url, **kwargs))
```

Add `from sqlalchemy.pool import NullPool` to the imports.

- [ ] **Step 4: Record the `with_for_update` gap (spec G6)**

Add above `auth_service.py:127`:

```python
    # NOTE (spec G6): SQLAlchemy's SQLite dialect renders `.with_for_update()`
    # as nothing — no error, no lock. On SQLite this row lock silently does not
    # exist. Harmless in local mode (one user, no concurrent password change);
    # load-bearing on Postgres, where it works. Do not "simplify" it away.
```

- [ ] **Step 5: Run both suites and commit**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_sqlite_pragmas.py -v
../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
```

Expected: 4 passed; full suite matches baseline. If the suite collapses, the alias is missing.

```bash
git add pypsa-gui/backend/db/session.py pypsa-gui/backend/services/auth_service.py \
        pypsa-gui/backend/tests/test_sqlite_pragmas.py
git commit -m "fix(gui): configure SQLite for a single-writer local app"
```

---

## Task 6: Alembic on SQLite (covers spec G4)

**Files:** Modify `backend/alembic/env.py`; create `backend/tests/test_alembic_sqlite.py`

**Context — correcting a v1 error:** `render_as_batch` is an **autogenerate-rendering** flag.
It makes `alembic revision --autogenerate` emit `op.batch_alter_table(...)`; it does **not**
change how an already-written migration executes. What makes `0002` work on SQLite is that its
author hand-wrote `with op.batch_alter_table("sessions")`. Setting the flag is still worth
doing — it means the next autogenerated migration is SQLite-safe by default — but the
rationale in v1 was false.

The real G4 problem: a database created by `Base.metadata.create_all` (which
`tools/bootstrap_super_admin.py:35` does) has **no `alembic_version` row**, so
`alembic upgrade head` fails with "table organizations already exists".

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_alembic_sqlite.py
from pathlib import Path

from sqlalchemy import create_engine, inspect

from db.models import Base


def test_env_py_sets_render_as_batch_for_autogenerate():
    """Online block only — the offline block never autogenerates."""
    env = (Path(__file__).resolve().parent.parent / "alembic" / "env.py").read_text(
        encoding="utf-8"
    )
    assert "render_as_batch=True" in env


def test_upgrade_or_stamp_handles_a_create_all_database(tmp_path):
    """Spec G4: a DB built by create_all has no alembic_version row."""
    from local_bootstrap import ensure_schema

    url = f"sqlite+pysqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    engine = create_engine(url)
    Base.metadata.create_all(bind=engine)   # simulate the old bootstrap path
    engine.dispose()

    ensure_schema(url)                      # must not raise

    engine = create_engine(url)
    assert "alembic_version" in inspect(engine).get_table_names()
    engine.dispose()


def test_upgrade_creates_a_fresh_database(tmp_path):
    from local_bootstrap import ensure_schema

    url = f"sqlite+pysqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    ensure_schema(url)
    engine = create_engine(url)
    names = inspect(engine).get_table_names()
    assert "alembic_version" in names and "organizations" in names
    engine.dispose()
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_alembic_sqlite.py -v
```

Expected: `ModuleNotFoundError: No module named 'local_bootstrap'` and the `render_as_batch`
test fails.

- [ ] **Step 3: Add the flag and the bootstrap helper**

In `alembic/env.py`, in the **online** `context.configure(...)` only:

```python
        # Autogenerate-rendering flag: makes `alembic revision --autogenerate`
        # emit op.batch_alter_table(...), which SQLite needs because it cannot
        # ALTER/DROP COLUMN. It does NOT change how existing migrations run —
        # 0002 works because its author wrote batch_alter_table by hand.
        render_as_batch=True,
```

Create `backend/local_bootstrap.py`:

```python
"""
First-run database bootstrap.

Separate from `local_mode` so it can be unit-tested and reused by a CLI
without importing the FastAPI app.
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import app_paths


def _alembic_config(url: str) -> Config:
    backend = Path(__file__).resolve().parent
    # configure_logger=false is load-bearing: alembic/env.py calls
    # logging.config.fileConfig(), which defaults to
    # disable_existing_loggers=True. Called in-process from the app's lifespan
    # that silences main.logger, pypsa_gui.chat, and uvicorn's own loggers, and
    # repoints root at alembic.ini's stderr handler — which on a windowed
    # Windows build with no console can raise on write.
    cfg = Config(
        str(backend / "alembic.ini"),
        attributes={"configure_logger": "false"},
    )
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def ensure_schema(url: str) -> None:
    """
    Bring `url` to head, whatever state it is in.

    Three cases:
      * file does not exist  -> upgrade creates everything
      * has alembic_version  -> upgrade applies what is missing
      * built by create_all  -> no alembic_version, so `upgrade` would fail with
        "table organizations already exists". Stamp it at the revision whose
        tables are already present, then upgrade the rest. This is spec G4.
    """
    if url.startswith("sqlite"):
        db_path = Path(url.split("///", 1)[1])
        db_path.parent.mkdir(parents=True, exist_ok=True)

    cfg = _alembic_config(url)
    engine = create_engine(url)
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    if names and "alembic_version" not in names:
        command.stamp(cfg, "0001_tenancy")
    command.upgrade(cfg, "head")


def ensure_app_dirs() -> None:
    """Create every directory the app writes to. Must run before ensure_schema."""
    from settings import get_settings

    app_paths.app_data_dir().mkdir(parents=True, exist_ok=True)
    s = get_settings()
    for p in (s.projects_root, s.legacy_root, s.flat_projects_root):
        Path(p).mkdir(parents=True, exist_ok=True)
```

Also update `alembic/env.py`'s `fileConfig` guard so the attribute is honoured:

```python
if config.config_file_name is not None and \
        config.attributes.get("configure_logger", "true") != "false":
    fileConfig(config.config_file_name)
```

- [ ] **Step 4: Run the tests**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_alembic_sqlite.py -v
../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
```

Expected: 3 passed; full suite matches baseline.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/alembic/env.py pypsa-gui/backend/local_bootstrap.py \
        pypsa-gui/backend/tests/test_alembic_sqlite.py
git commit -m "feat(gui): first-run schema bootstrap that survives a create_all database"
```

---

## Task 7: Local-mode predicate and seed (covers spec B4)

**Files:** Create `backend/local_mode.py`, `backend/tools/bootstrap_local.py`, `backend/tests/test_local_mode_seed.py`

**Interfaces:** Produces `is_local_mode() -> bool`, `LOCAL_ORG_ID`, `LOCAL_USER_ID`, `ensure_local_identity(db) -> User`, `get_local_user(db) -> User | None`.

**Context — constraints verified in `db/models.py`:** `Organization.created_at:17` and
`User.created_at:28` are NOT NULL with no Python default; `OrgMembership.role:38` NOT NULL,
no default; `OrgMembership.__table_args__:33` carries `UniqueConstraint("user_id")`;
`users.email:24` is uniquely indexed; `password_hash:25` is nullable; `status:26` defaults to
`"invited"` and `auth_service` rejects anything but `"active"`. The user needs
`is_super_admin=True` **and** membership `role="admin"` — the first gates `/api/admin/*`,
the second is the only see-everything short-circuit in `project_acl.can_access_project`.

Fixed UUIDs so `projects_root/<org_id>/` survives a reinstall.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_local_mode_seed.py
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import local_mode
from db.models import Base, OrgMembership, Organization, User


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'x.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as s:
        yield s
    engine.dispose()


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("  ", False),
])
def test_is_local_mode_reads_env(monkeypatch, value, expected):
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", value)
    assert local_mode.is_local_mode() is expected


def test_is_local_mode_defaults_off(monkeypatch):
    monkeypatch.delenv("PYPSAGUI_LOCAL_MODE", raising=False)
    assert local_mode.is_local_mode() is False


def test_is_local_mode_is_read_per_call(monkeypatch):
    """Load-bearing: the whole test strategy depends on no caching."""
    monkeypatch.delenv("PYPSAGUI_LOCAL_MODE", raising=False)
    assert local_mode.is_local_mode() is False
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    assert local_mode.is_local_mode() is True


def test_seed_creates_org_user_and_membership(db):
    user = local_mode.ensure_local_identity(db)
    assert user.id == local_mode.LOCAL_USER_ID
    assert user.status == "active"
    assert user.is_super_admin is True
    assert db.get(Organization, local_mode.LOCAL_ORG_ID) is not None
    m = db.scalar(select(OrgMembership).where(OrgMembership.user_id == user.id))
    assert m is not None and m.role == "admin" and m.org_id == local_mode.LOCAL_ORG_ID


def test_seed_is_idempotent(db):
    a = local_mode.ensure_local_identity(db)
    b = local_mode.ensure_local_identity(db)
    assert a.id == b.id
    assert len(db.scalars(select(User)).all()) == 1
    assert len(db.scalars(select(OrgMembership)).all()) == 1


def test_get_local_user_returns_none_on_an_unseeded_db(db):
    assert local_mode.get_local_user(db) is None


def test_ids_are_stable_constants():
    assert isinstance(local_mode.LOCAL_ORG_ID, uuid.UUID)
    assert isinstance(local_mode.LOCAL_USER_ID, uuid.UUID)
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_local_mode_seed.py -v
```

Expected: `ModuleNotFoundError: No module named 'local_mode'`.

- [ ] **Step 3: Implement**

```python
# pypsa-gui/backend/local_mode.py
"""
Single-user local mode.

The desktop build has no login. Rather than delete the tenancy layer — 68
require_user/optional_user sites, org-scoped storage, a large test suite —
local mode seeds ONE org + user + membership and injects that user on every
request. Every downstream check then passes for the reason it was written to
pass.

The IDs are fixed constants, not generated: projects_root/<org_id>/<project_id>/
embeds the org id, so a regenerated id orphans every project on reinstall.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from db.models import OrgMembership, Organization, User

LOCAL_ORG_ID = uuid.UUID("00000000-0000-4000-8000-00000000da7a")
LOCAL_USER_ID = uuid.UUID("00000000-0000-4000-8000-000000005e1f")
LOCAL_USER_EMAIL = "local@pypsa-gui.localhost"
LOCAL_ORG_NAME = "Local"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def is_local_mode() -> bool:
    """
    True when the launcher set PYPSAGUI_LOCAL_MODE.

    Read from os.environ on EVERY call, never cached. That is what lets the
    same app object serve both modes, and what lets a test monkeypatch the env
    without reimporting anything — reimporting is unsafe here because
    `del sys.modules["db.session"]` is a no-op for `from db import session`.
    """
    return os.environ.get("PYPSAGUI_LOCAL_MODE", "").strip().lower() in _TRUTHY


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ensure_local_identity(db: DBSession) -> User:
    """
    Idempotently seed the local org, user, and membership. Returns the user.

    Select-then-insert, not merge: users.email is uniquely indexed and
    OrgMembership carries UniqueConstraint("user_id"), so a blind insert on the
    second boot raises IntegrityError.

    status="active" is required — auth_service rejects anything else. Both
    created_at columns are NOT NULL with no Python default, so they are set
    explicitly. password_hash stays NULL: there is no login to perform.
    """
    org = db.get(Organization, LOCAL_ORG_ID)
    if org is None:
        db.add(Organization(id=LOCAL_ORG_ID, name=LOCAL_ORG_NAME, created_at=_now_utc()))
        db.flush()

    user = db.get(User, LOCAL_USER_ID)
    if user is None:
        user = User(
            id=LOCAL_USER_ID,
            email=LOCAL_USER_EMAIL,
            password_hash=None,
            status="active",
            is_super_admin=True,
            created_at=_now_utc(),
        )
        db.add(user)
        db.flush()

    if db.scalar(select(OrgMembership).where(OrgMembership.user_id == LOCAL_USER_ID)) is None:
        db.add(OrgMembership(org_id=LOCAL_ORG_ID, user_id=LOCAL_USER_ID, role="admin"))

    db.commit()
    db.refresh(user)
    return user


def get_local_user(db: DBSession) -> User | None:
    """
    Re-fetch the seeded user in the CALLER's session.

    Never cache the ORM object across requests: sessionmaker uses the default
    expire_on_commit=True, so a cached instance is detached and reading
    `user.id` raises DetachedInstanceError inside project_registry /
    project_acl.
    """
    return db.get(User, LOCAL_USER_ID)
```

```python
# pypsa-gui/backend/tools/bootstrap_local.py
"""
Create (or repair) the local database and identity from the command line.

Spec B4. The web equivalent is `bootstrap_super_admin.py`, which takes an email
and password; local mode has neither, so this is a separate entry point rather
than a flag on that one.

    python -m tools.bootstrap_local
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import local_mode  # noqa: E402
from db.session import SessionLocal  # noqa: E402
from local_bootstrap import ensure_app_dirs, ensure_schema  # noqa: E402
from settings import get_settings  # noqa: E402


def main() -> int:
    ensure_app_dirs()
    url = get_settings().database_url
    ensure_schema(url)
    with SessionLocal() as db:
        user = local_mode.ensure_local_identity(db)
    print(f"database: {url}")
    print(f"projects: {get_settings().projects_root}")
    print(f"identity: {user.email} ({user.id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_local_mode_seed.py -v
PYPSAGUI_APP_DATA_DIR=$(mktemp -d) ../../.pixi/envs/default/bin/python -m tools.bootstrap_local
```

Expected: 15 passed; the CLI prints three lines and exits 0.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/local_mode.py pypsa-gui/backend/tools/bootstrap_local.py \
        pypsa-gui/backend/tests/test_local_mode_seed.py
git commit -m "feat(gui): seed a single local identity for desktop mode"
```

---

## Task 8: Wire local mode into the auth gate

**Files:** Modify `backend/main.py` (`lifespan` `:124-128`, auth block `:241-271`, `_csrf_rejection` `:140-193`, `health` `:542-553`); create `backend/tests/test_local_mode_api.py`

**Context:** The auth middleware sets `request.state.auth_user` at `:248` and 401s at `:267`.
Starlette makes the last-added middleware outermost, so an *added* middleware is either
overwritten at `:248` or never reached — **the branch must live inside this block**. That
block gates 118 of 172 routes that never touch `deps.optional_user`; `deps.optional_user`
separately honours a pre-populated `request.state.auth_user` (`deps.py:112-113`), covering
the rest.

`_csrf_rejection` already returns `None` with no session cookie, so local mode is exempt by
construction — but a stale cookie in a packaged webview profile would re-arm it.

Step 0b needs no handling: `bind_active_project` returns early without a session cookie,
`PyPSAService._request_ctx` keeps its `None` default, and `_ensure_active` falls through to
the process foreground — correct for one user.

**Test strategy — no module reloading.** `is_local_mode()` is per-call, so the same
`main.app` conftest already imported serves both modes. The fixture monkeypatches the env,
seeds the identity into conftest's own DB, and uses a **cookie-less** TestClient.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_local_mode_api.py
"""
Local-mode API tests.

Deliberately NO importlib.reload and NO sys.modules surgery. `is_local_mode()`
reads os.environ per call, so the app conftest already built serves both modes;
`del sys.modules["db.session"]` would not work anyway (it is a no-op for
`from db import session`) and would leave security/settings split-brained for
the rest of the session.
"""
import pytest
from fastapi.testclient import TestClient

import local_mode
import main


@pytest.fixture
def local_client(_auth_db, monkeypatch):
    """Cookie-less client with local mode on, seeded into conftest's DB."""
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
    with TestClient(main.app) as c:
        c.cookies.clear()
        yield c


def test_health_reports_auth_disabled(local_client):
    assert local_client.get("/api/health").json()["auth_enabled"] is False


def test_health_still_reports_enabled_in_web_mode(client):
    """The default `client` fixture runs with local mode off."""
    assert client.get("/api/health").json()["auth_enabled"] is True


def test_api_reachable_without_a_session_cookie(local_client):
    r = local_client.get("/api/projects/")
    assert r.status_code == 200, r.text


def test_mutation_succeeds_without_a_csrf_token(local_client):
    r = local_client.post("/api/network/reset")
    assert r.status_code != 403, r.text


def test_seeded_identity_is_visible(local_client):
    r = local_client.get("/api/auth/me")
    assert r.status_code == 200, r.text
    assert r.json()["is_super_admin"] is True


def test_web_mode_still_401s_without_a_cookie(monkeypatch, _auth_db):
    monkeypatch.delenv("PYPSAGUI_LOCAL_MODE", raising=False)
    with TestClient(main.app) as c:
        c.cookies.clear()
        assert c.get("/api/projects/").status_code == 401
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_local_mode_api.py -v
```

Expected: the local-mode tests 401, and `test_health_reports_auth_disabled` fails because
`auth_enabled` is hardcoded `True`.

- [ ] **Step 3: Add the branch**

Add `import local_mode` and `import local_bootstrap` at the top of `main.py`.

In `lifespan`, before `yield`:

```python
async def lifespan(app: FastAPI):
    if local_mode.is_local_mode():
        # Order matters: the directories must exist before Alembic opens the
        # database file, or `upgrade` dies with "unable to open database file"
        # on a machine that has never run the app.
        local_bootstrap.ensure_app_dirs()
        local_bootstrap.ensure_schema(get_settings().database_url)
        with db_session_module.SessionLocal() as db:
            local_mode.ensure_local_identity(db)
    PyPSAService.initialize()
    yield
```

In the auth block, replace the assignment at `:248`:

```python
                if local_mode.is_local_mode():
                    # Re-fetched per request, never cached: expire_on_commit is
                    # on, so a cached User is detached and reading user.id
                    # raises inside project_registry / project_acl.
                    request.state.auth_user = local_mode.get_local_user(db)
                else:
                    request.state.auth_user = resolve_request_user(request, db)
```

Split the 503 message in the same `except` block:

```python
            if local_mode.is_local_mode():
                detail = (
                    "Local database unavailable. Close any other running copy "
                    "of PyPSA GUI and try again."
                )
            else:
                detail = (
                    "Auth database unavailable. Start Postgres (or use a sqlite "
                    "DATABASE_URL), run alembic upgrade head, then restart the backend."
                )
            return JSONResponse(status_code=503, content={"detail": detail})
```

As the first statement of `_csrf_rejection`:

```python
    # No session cookie is ever issued locally, so the check below would already
    # exempt every request — but a stale cookie left in a packaged webview
    # profile would re-arm it.
    if local_mode.is_local_mode():
        return None
```

At `main.py:552`:

```python
        # False in local mode. This is the SPA's boot contract: AuthModeProvider
        # overwrites its compile-time flag from this value, and spa.html's
        # pre-React gate skips /api/auth/me when it is false. Flipping this one
        # field is what turns the login gate off.
        "auth_enabled": not local_mode.is_local_mode(),
```

- [ ] **Step 4: Run both suites**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_local_mode_api.py -v
../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
```

Expected: 6 passed; full suite matches the Task 0 baseline. If the count *changed*, a
local-mode test leaked env state — check every `monkeypatch.setenv` is function-scoped.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/main.py pypsa-gui/backend/tests/test_local_mode_api.py
git commit -m "feat(gui): run the backend without authentication in local mode"
```

---

## Task 9: Frontend stops fighting local mode (covers spec B5)

**Files:** Create `frontend/src/auth/localMode.ts`, `frontend/src/auth/localMode.test.ts`; modify `frontend/src/api/client.ts:96-112,147-152`, `frontend/src/auth/AuthProvider.tsx:32-35`, `frontend/src/layout/AppHeader.tsx`, `frontend/src/pages/ProjectsHomePage.tsx`

**Context — three separate mechanisms re-arm the login UI, not one:**

1. `client.ts:147-152` — on a 401 with `"Authentication required"` **or a 503 with
   `"Auth database unavailable"`**, calls `setAuthEnabled(true)` and
   `notifyAuthBackendRequired(...)`. One stray response permanently re-arms the gate.
2. `client.ts:96-112` `shouldRedirectToLogin` — returns `true` when the detail contains
   `"Authentication required"` **regardless of `getAuthEnabled()`** (`:108`), then
   `forceLoginRedirect()`. Independent of (1).
3. `AuthProvider.tsx:32-35` — sets `user = null` when auth is off, making
   `hasAdminConsoleAccess(null)` false so `/admin/*` redirects away.

`AuthUser` is exported from `../api/auth` (`frontend/src/api/auth.ts:3`), **not** `./types`,
which does not exist.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/auth/localMode.test.ts
import { describe, expect, it } from 'vitest'
import { localAdminUser, shouldRearmAuth, shouldRedirectWhenAuthDisabled } from './localMode'

describe('shouldRearmAuth', () => {
  it('re-arms on an auth 401 when auth is enabled', () => {
    expect(shouldRearmAuth(401, 'Authentication required', true)).toBe(true)
  })
  it('re-arms on the 503 auth-database branch when enabled', () => {
    expect(shouldRearmAuth(503, 'Auth database unavailable', true)).toBe(true)
  })
  it('never re-arms when auth is disabled', () => {
    expect(shouldRearmAuth(401, 'Authentication required', false)).toBe(false)
    expect(shouldRearmAuth(503, 'Auth database unavailable', false)).toBe(false)
  })
  it('ignores unrelated messages', () => {
    expect(shouldRearmAuth(401, 'Bad token', true)).toBe(false)
  })
})

describe('shouldRedirectWhenAuthDisabled', () => {
  it('never redirects to login while auth is disabled', () => {
    expect(shouldRedirectWhenAuthDisabled(false)).toBe(false)
  })
  it('leaves web mode alone', () => {
    expect(shouldRedirectWhenAuthDisabled(true)).toBe(true)
  })
})

describe('localAdminUser', () => {
  it('is an admin so the console stays reachable', () => {
    const u = localAdminUser()
    expect(u.is_super_admin).toBe(true)
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/frontend && npm test -- src/auth/localMode.test.ts
```

Expected: cannot resolve `./localMode`.

- [ ] **Step 3: Implement**

```typescript
// frontend/src/auth/localMode.ts
import type { AuthUser } from '../api/auth'

/**
 * Whether a response should turn the login UI back on.
 *
 * client.ts used to do this unconditionally, which made one stray response a
 * one-way ratchet: local mode boots with auth off and re-arms the gate on the
 * first unrelated 401 — or on the 503 the auth-database branch emits.
 */
export function shouldRearmAuth(
  status: number | undefined,
  message: string,
  authEnabled: boolean,
): boolean {
  if (!authEnabled) return false
  if (status === 401 && message.includes('Authentication required')) return true
  if (status === 503 && message.includes('Auth database unavailable')) return true
  return false
}

/**
 * Guard for `shouldRedirectToLogin`, which is a SEPARATE path from the ratchet
 * above and fires on the message alone regardless of the flag. With auth off
 * there is no login page to redirect to.
 */
export function shouldRedirectWhenAuthDisabled(authEnabled: boolean): boolean {
  return authEnabled
}

/**
 * The synthetic user rendered when auth is off.
 *
 * AuthProvider previously used `null`, which made hasAdminConsoleAccess(null)
 * false and bounced /admin/* to /projects. A local user owns their machine.
 */
export function localAdminUser(): AuthUser {
  return {
    id: 'local',
    email: 'local@pypsa-gui.localhost',
    is_super_admin: true,
  } as AuthUser
}
```

Apply in `client.ts` — replace the condition at `:147-149`:

```typescript
    if (shouldRearmAuth(status, String(msg), getAuthEnabled())) {
      setAuthEnabled(true)
      notifyAuthBackendRequired(status ?? 401)
    }
```

and in `shouldRedirectToLogin`, gate the detail branch at `:108`:

```typescript
  if (!shouldRedirectWhenAuthDisabled(getAuthEnabled())) return false
  if (detail.includes('Authentication required') || getAuthEnabled()) {
    return true
  }
```

In `AuthProvider.tsx:32-35`, return the synthetic user:

```typescript
    if (!authEnabled) {
      const local = localAdminUser()
      setUser(local)
      setStatus('authenticated')
      return local
    }
```

- [ ] **Step 4: Hide sign-out (spec B5)**

`AppHeader.tsx` renders `<UserMenu/>` when `authEnabled`, and `ProjectsHomePage.tsx` renders
a second Sign-out under the same flag. Both are already conditional — confirm with:

```bash
cd pypsa-gui/frontend
grep -n "authEnabled" src/layout/AppHeader.tsx src/pages/ProjectsHomePage.tsx
```

If either renders unconditionally, wrap it in `authEnabled &&`. Sign-out in local mode is a
dead end: it POSTs to `/api/auth/logout` and then hard-navigates to `/`, with no password to
get back in.

- [ ] **Step 5: Run the tests and commit**

```bash
cd pypsa-gui/frontend && npm test
git add pypsa-gui/frontend/src/auth/localMode.ts pypsa-gui/frontend/src/auth/localMode.test.ts \
        pypsa-gui/frontend/src/api/client.ts pypsa-gui/frontend/src/auth/AuthProvider.tsx \
        pypsa-gui/frontend/src/layout/AppHeader.tsx pypsa-gui/frontend/src/pages/ProjectsHomePage.tsx
git commit -m "fix(gui): stop re-arming the login gate when auth is disabled"
```

---

## Task 10: Cover the raw `fetch` mutation sites with CSRF

**Files:** Modify `frontend/src/api/csrf.ts` (append only), `frontend/src/api/uploads.ts:75,87,101,129`, `frontend/src/api/chat.ts:63`, `frontend/src/pages/TopologyCanvas.tsx:147,2361`; create `frontend/src/api/csrf.rawfetch.test.ts`

**Context — read before starting.** The axios path is **already done**.
`frontend/src/api/csrf.ts` exists (added in `1d930244`) and exports `CSRF_COOKIE`,
`CSRF_HEADER`, `CSRF_SAFE_METHODS`, `needsCsrfHeader()`, `readCsrfToken(cookieSource?)`.
`client.ts:5` imports them and `:114-122` wires the interceptor; `:164` handles
`csrf_token_invalid` by refreshing and retrying.

**Do not create or overwrite `csrf.ts` or `csrf.test.ts`.** An earlier draft of this plan
did, which would have dropped `needsCsrfHeader`/`CSRF_SAFE_METHODS` that `client.ts` imports
(TypeScript build failure) and made `readCsrfToken`'s parameter required while `client.ts`
calls it with none.

The residual gap is only the calls that bypass axios.

- [ ] **Step 1: Confirm the current state before touching anything**

```bash
cd pypsa-gui/frontend
grep -n "export" src/api/csrf.ts
grep -n "csrf\|CSRF" src/api/client.ts
grep -rn "fetch(\|sendBeacon(" src --include=*.ts --include=*.tsx | grep -v "\.test\."
```

Reconcile the `fetch` list with the **Files** list above and use what you find.

- [ ] **Step 2: Write the failing test**

```typescript
// frontend/src/api/csrf.rawfetch.test.ts
import { describe, expect, it } from 'vitest'
import { CSRF_HEADER, rawFetchHeaders } from './csrf'

describe('rawFetchHeaders', () => {
  it('adds the header when a token cookie is present', () => {
    expect(rawFetchHeaders('POST', 'pypsa_gui_csrf=tok')).toEqual({ [CSRF_HEADER]: 'tok' })
  })
  it('adds nothing for a safe method', () => {
    expect(rawFetchHeaders('GET', 'pypsa_gui_csrf=tok')).toEqual({})
  })
  it('adds nothing when the cookie is absent', () => {
    expect(rawFetchHeaders('POST', 'other=1')).toEqual({})
  })
  it('handles DELETE, used by the keepalive teardown path', () => {
    expect(rawFetchHeaders('DELETE', 'pypsa_gui_csrf=t')).toEqual({ [CSRF_HEADER]: 't' })
  })
})
```

- [ ] **Step 3: Run it and watch it fail**

```bash
cd pypsa-gui/frontend && npm test -- src/api/csrf.rawfetch.test.ts
```

Expected: `rawFetchHeaders` is not exported.

- [ ] **Step 4: Append the helper and apply it**

Append to the **existing** `src/api/csrf.ts`:

```typescript
/**
 * Header bag for a raw `fetch`/`sendBeacon` call.
 *
 * The axios instance gets this from its request interceptor; direct fetch
 * callers bypass that and 403 on any mutation once a session cookie exists.
 * Built on the same two helpers so there is one definition of "which methods
 * need a token" and one cookie parser.
 */
export function rawFetchHeaders(
  method: string,
  cookieSource?: string,
): Record<string, string> {
  if (!needsCsrfHeader(method)) return {}
  const token = readCsrfToken(cookieSource)
  return token ? { [CSRF_HEADER]: token } : {}
}
```

At each site, spread it into the headers:

```typescript
const resp = await fetch(url, {
  method: 'POST',
  credentials: 'include',
  headers: { ...existingHeaders, ...rawFetchHeaders('POST') },
  body,
})
```

`TopologyCanvas.tsx:2361` is a `keepalive` DELETE with no headers object — add one.

- [ ] **Step 5: Run the tests and commit**

```bash
cd pypsa-gui/frontend && npm test
git add pypsa-gui/frontend/src/api/csrf.ts pypsa-gui/frontend/src/api/csrf.rawfetch.test.ts \
        pypsa-gui/frontend/src/api/uploads.ts pypsa-gui/frontend/src/api/chat.ts \
        pypsa-gui/frontend/src/pages/TopologyCanvas.tsx
git commit -m "fix(gui): send the CSRF token from raw fetch call sites too"
```

---

## Task 11: Dynamic origin (covers spec C4)

**Files:** Create `backend/tests/test_dynamic_origin.py`; modify `pypsa-gui/README.md`

**Context:** `settings.py:23` pins `cors_allowed_origins` to the two Vite dev origins, and
that one string drives **both** CORS and the CSRF Origin check. Browsers send `Origin` on
same-origin non-GET, so a shell on an ephemeral port gets `403 csrf_origin_rejected` on every
mutation. Both `get_settings()` and `security.allowed_origins()` are `lru_cache`d, so the
value must be in `os.environ` before `import main`.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_dynamic_origin.py
import pytest
from fastapi.testclient import TestClient

import local_mode
import main
import security
import settings as settings_module


def _reset():
    settings_module.get_settings.cache_clear()
    security.reset_caches_for_tests()


def test_origin_allowlist_follows_the_env(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:51234")
    _reset()
    try:
        assert security.is_allowed_origin("http://127.0.0.1:51234") is True
        assert security.is_allowed_origin("http://127.0.0.1:5173") is False
    finally:
        _reset()


def test_the_caches_must_be_cleared_after_the_env_changes(monkeypatch):
    """
    Documents the ordering constraint the desktop shell depends on: set the env
    FIRST, then clear. Clearing first re-populates from the already-mutated env
    on the next read, which is why the naive version of this test asserts the
    wrong thing.
    """
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:5173")
    _reset()
    try:
        assert security.is_allowed_origin("http://127.0.0.1:40000") is False
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:40000")
        assert security.is_allowed_origin("http://127.0.0.1:40000") is False  # stale cache
        _reset()
        assert security.is_allowed_origin("http://127.0.0.1:40000") is True
    finally:
        _reset()


def test_mutation_succeeds_from_a_non_5173_origin(_auth_db, monkeypatch):
    """
    Spec C4 — the integration test the unit tests above cannot replace. Drives a
    real mutation through _csrf_rejection with an Origin header, which is what
    would actually catch a regression in the origin gate.
    """
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
    with TestClient(main.app) as c:
        c.cookies.clear()
        r = c.post("/api/network/reset", headers={"Origin": "http://127.0.0.1:51234"})
        assert r.status_code != 403, r.text
```

- [ ] **Step 2: Run it**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_dynamic_origin.py -v
```

Expected: 3 passed. These are characterization tests — a failure means the caching contract
changed and the desktop shell's env ordering is no longer safe.

- [ ] **Step 3: Document the contract**

Add to `pypsa-gui/README.md`:

```markdown
### Local desktop mode

The launcher MUST set these before `import main` — `get_settings()` and
`security.allowed_origins()` are `lru_cache`d and read once:

| Variable | Value |
|---|---|
| `PYPSAGUI_LOCAL_MODE` | `1` |
| `DATABASE_URL` | absolute SQLite path under the app-data dir |
| `PROJECTS_ROOT` | user-visible projects folder |
| `FLAT_PROJECTS_ROOT`, `LEGACY_ROOT` | app-data dir |
| `CORS_ALLOWED_ORIGINS` | `http://127.0.0.1:<chosen port>` |
| `MPLBACKEND` | `Agg` (matplotlib resolves to `macosx` otherwise, which crashes off the main thread) |

One-shot setup without the shell: `python -m tools.bootstrap_local`.
```

- [ ] **Step 4: Full suite**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/tests/test_dynamic_origin.py pypsa-gui/README.md
git commit -m "test(gui): pin the dynamic-origin contract and prove a non-dev-origin mutation"
```

---

## Task 12: Port the SPA routing gate to Python

**Files:** Create `backend/static_gate.py`, `backend/tests/test_static_gate.py`

**Interfaces:** Produces `is_static_asset(path) -> bool` and `decide_route(path, *, local_mode, authed) -> tuple[str, str]`.

**Context:** The routing brain is `frontend/vite.auth-gate.ts:41-67` (`decideGateRoute`),
registered only via `configureServer` — it emits nothing into `dist/`. `vite.config.ts` sets
`appType: 'mpa'`, which disables Vite's SPA history fallback. Two traps: `dist/index.html`
is the **login** page with no React entry, so a stock `StaticFiles(html=True)` catch-all
serves a sign-in form for `/projects`; and wiring `/` to `spa.html` instead creates an
infinite redirect loop via `spa.html:46` (`location.replace('/?needLogin=…')`).

`dist/login.html` **does** exist — it is a second copy of the login document.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_static_gate.py
import pytest

from static_gate import decide_route, is_static_asset


@pytest.mark.parametrize("path", [
    "/assets/spa-B6BHlEqH.js", "/brand.css", "/img/logo.svg", "/favicon.ico", "/api/health",
])
def test_static_assets_pass_through(path):
    assert is_static_asset(path) is True


@pytest.mark.parametrize("path", ["/", "/projects", "/app", "/admin/users", "/login.html"])
def test_html_routes_are_not_static(path):
    assert is_static_asset(path) is False


@pytest.mark.parametrize("path", ["/", "/projects", "/app", "/admin/users"])
def test_local_mode_always_serves_the_spa(path):
    assert decide_route(path, local_mode=True, authed=False) == ("serve", "spa.html")


def test_local_mode_never_serves_the_login_document():
    """spa.html's boot gate redirects to '/' on a 401; serving index.html there loops."""
    assert decide_route("/", local_mode=True, authed=False) == ("serve", "spa.html")


def test_web_mode_anonymous_gets_the_login_document():
    assert decide_route("/", local_mode=False, authed=False) == ("serve", "index.html")
    assert decide_route("/projects", local_mode=False, authed=False) == ("serve", "index.html")


def test_web_mode_authed_deep_links_get_the_spa():
    assert decide_route("/projects", local_mode=False, authed=True) == ("serve", "spa.html")


def test_web_mode_authed_root_redirects_to_projects():
    assert decide_route("/", local_mode=False, authed=True) == ("redirect", "/projects")


def test_spa_html_is_never_served_directly_in_web_mode():
    assert decide_route("/spa.html", local_mode=False, authed=False) == ("redirect", "/")


def test_login_html_always_serves_the_login_document():
    assert decide_route("/login.html", local_mode=False, authed=False) == ("serve", "index.html")
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_static_gate.py -v
```

Expected: `ModuleNotFoundError: No module named 'static_gate'`.

- [ ] **Step 3: Implement**

```python
# pypsa-gui/backend/static_gate.py
"""
Server-side port of `frontend/vite.auth-gate.ts`.

That gate is a Vite DEV-SERVER plugin: it rewrites req.url per request and
emits nothing into dist/. Serving the built SPA from FastAPI therefore needs
the logic reimplemented, not copied.

Two traps a stock StaticFiles(html=True) mount walks into:
  * dist/index.html is the LOGIN page with no React entry. A catch-all that
    serves it for /projects renders a sign-in form instead of the app.
  * Wiring "/" to spa.html instead loops: spa.html's pre-React boot gate does
    location.replace('/?needLogin=…') on a 401, and "/" would serve spa.html
    again.

Pure functions; the FastAPI wiring lives in main.py so this stays testable.
"""
from __future__ import annotations

SPA = "spa.html"
LOGIN = "index.html"

_ASSET_PREFIXES = ("/assets/", "/img/", "/api/", "/favicon")
_ASSET_FILES = ("/brand.css",)

Decision = tuple[str, str]


def is_static_asset(path: str) -> bool:
    """True for anything served verbatim rather than routed."""
    if path.startswith(_ASSET_PREFIXES) or path in _ASSET_FILES:
        return True
    leaf = path.rsplit("/", 1)[-1]
    return "." in leaf and not leaf.endswith(".html")


def decide_route(path: str, *, local_mode: bool, authed: bool) -> Decision:
    """Which document to serve. Returns ("serve", filename) or ("redirect", location)."""
    if local_mode:
        # No login exists. Every HTML route is the app — including "/", which is
        # what breaks the redirect loop described above.
        return ("serve", SPA)

    if path == "/login.html":
        return ("serve", LOGIN)

    if authed:
        if path in ("/", "/index.html"):
            return ("redirect", "/projects")
        return ("serve", SPA)

    if path == f"/{SPA}":
        return ("redirect", "/")
    return ("serve", LOGIN)
```

- [ ] **Step 4: Run the tests**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_static_gate.py -v
```

Expected: 17 passed (5 + 5 parametrized, 4 more parametrized, 7 named).

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/static_gate.py pypsa-gui/backend/tests/test_static_gate.py
git commit -m "feat(gui): port the SPA routing gate to the backend"
```

---

## Task 13: Serve the built SPA from FastAPI

**Files:** Modify `backend/main.py` (append at end of file); create `backend/tests/test_serve_spa.py`

**Context — placement is the whole task.** FastAPI matches routes in registration order.
The last `include_router` is `main.py:519`, but `@app.get("/api/health")` is declared at
`:542`. A catch-all inserted at `:520` matches `GET /api/health` first,
`is_static_asset("/api/health")` returns `True`, and health 404s — breaking `spa.html`'s
pre-React boot gate and `AuthModeProvider`. **Append at the very end of the file.**

`request.state.auth_user` is only set for `/api/*` paths, so reading it for an HTML route
always yields `None` and every web-mode deep link would serve the login page. Resolve the
session directly instead.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_serve_spa.py
import pytest
from fastapi.testclient import TestClient

import local_mode
import main
import settings as settings_module


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<html data-pypsa-page='login'></html>", encoding="utf-8")
    (d / "login.html").write_text("<html data-pypsa-page='login'></html>", encoding="utf-8")
    (d / "spa.html").write_text("<html id='spa'></html>", encoding="utf-8")
    (d / "assets" / "spa.js").write_text("console.log(1)", encoding="utf-8")
    (d / "brand.css").write_text("body{}", encoding="utf-8")
    return d


@pytest.fixture
def local_spa_client(_auth_db, dist, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    settings_module.get_settings.cache_clear()
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
    try:
        with TestClient(main.app) as c:
            c.cookies.clear()
            yield c
    finally:
        settings_module.get_settings.cache_clear()


def test_serves_spa_at_root(local_spa_client):
    r = local_spa_client.get("/")
    assert r.status_code == 200 and "id='spa'" in r.text


@pytest.mark.parametrize("path", ["/projects", "/app", "/admin/users"])
def test_serves_spa_for_deep_links(local_spa_client, path):
    r = local_spa_client.get(path)
    assert r.status_code == 200 and "id='spa'" in r.text


def test_assets_are_served_verbatim(local_spa_client):
    assert local_spa_client.get("/assets/spa.js").status_code == 200
    assert local_spa_client.get("/brand.css").status_code == 200


def test_api_routes_are_not_swallowed(local_spa_client):
    """Regression guard for the catch-all placement."""
    r = local_spa_client.get("/api/health")
    assert r.status_code == 200 and "auth_enabled" in r.json()


def test_unknown_asset_404s_rather_than_returning_html(local_spa_client):
    assert local_spa_client.get("/assets/missing.js").status_code == 404


def test_traversal_is_refused(local_spa_client):
    assert local_spa_client.get("/assets/../../settings.py").status_code == 404


def test_head_is_supported(local_spa_client):
    assert local_spa_client.head("/projects").status_code == 200
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_serve_spa.py -v
```

Expected: every HTML route 404s — nothing serves static files today.

- [ ] **Step 3: Append the catch-all at the END of `main.py`**

After the `health()` definition, at the very bottom of the file:

```python
# ── Static SPA (must be LAST) ────────────────────────────────────────────────
# FastAPI matches in registration order and `health` above is declared after
# the routers, so this has to come after BOTH or it swallows /api/health.
# Mounted at document root because every asset reference in dist/ is
# root-absolute (/assets/…, /brand.css) — a sub-path mount 404s every asset.
def _dist() -> Path:
    # Read per call, not at import: tests point FRONTEND_DIST at a tmpdir.
    return Path(get_settings().frontend_dist)


@app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def serve_spa(full_path: str, request: Request):
    dist = _dist()
    if not dist.is_dir():
        raise HTTPException(status_code=503, detail="Frontend not built. Run `npm run build`.")

    path = "/" + full_path
    if static_gate.is_static_asset(path):
        candidate = (dist / full_path).resolve()
        if not candidate.is_relative_to(dist.resolve()) or not candidate.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(candidate)

    # NOT request.state.auth_user: the auth middleware only sets it for /api/*
    # paths, so for an HTML route it is always None and every authenticated
    # deep link would fall through to the login document.
    authed = local_mode.is_local_mode()
    if not authed:
        try:
            with db_session_module.SessionLocal() as db:
                authed = resolve_request_user(request, db) is not None
        except Exception:  # noqa: BLE001 — a DB outage must still serve the shell
            authed = False

    kind, target = static_gate.decide_route(
        path, local_mode=local_mode.is_local_mode(), authed=authed
    )
    if kind == "redirect":
        return RedirectResponse(url=target, status_code=302)
    return FileResponse(dist / target)
```

Add to the imports at the top of `main.py`:

```python
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
import static_gate
```

- [ ] **Step 4: Run both suites**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_serve_spa.py -v
../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
```

Expected: 9 passed; full suite matches baseline. A failure in
`test_api_routes_are_not_swallowed` means the catch-all is not last.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/main.py pypsa-gui/backend/tests/test_serve_spa.py
git commit -m "feat(gui): serve the built SPA from the backend"
```

---

## Task 14: End-to-end smoke, against the real build (covers spec A5, D6)

**Files:** Create `backend/tests/test_local_mode_e2e.py`

**Context:** Task 13's fixture uses a hand-written `dist`. The two traps it exists to catch —
`index.html` *is* the login page, assets are root-absolute — are only real against actual
build output, so this task runs against `frontend/dist/` when present.

- [ ] **Step 1: Write the test**

```python
# pypsa-gui/backend/tests/test_local_mode_e2e.py
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app_paths
import local_mode
import main
import settings as settings_module

_REAL_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@pytest.fixture
def real_dist_client(_auth_db, monkeypatch):
    if not (_REAL_DIST / "spa.html").is_file():
        pytest.skip("frontend not built — run `npm run build` in pypsa-gui/frontend")
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.setenv("FRONTEND_DIST", str(_REAL_DIST))
    settings_module.get_settings.cache_clear()
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
    try:
        with TestClient(main.app) as c:
            c.cookies.clear()
            yield c
    finally:
        settings_module.get_settings.cache_clear()


def test_root_serves_the_react_entry_not_the_login_page(real_dist_client):
    """The trap: dist/index.html IS the login document."""
    body = real_dist_client.get("/").text
    assert 'data-pypsa-page="login"' not in body
    assert "/assets/" in body


def test_real_assets_resolve(real_dist_client):
    import re
    body = real_dist_client.get("/").text
    for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"', body)[:5]:
        assert real_dist_client.get(asset).status_code == 200, asset


def test_full_local_journey(real_dist_client):
    assert real_dist_client.get("/api/health").json()["auth_enabled"] is False
    assert real_dist_client.get("/api/projects/").status_code == 200
    created = real_dist_client.post("/api/projects/smoke-test")
    assert created.status_code in (200, 201), created.text
    listed = real_dist_client.get("/api/projects/").json()
    assert any(p.get("name") == "smoke-test" for p in listed), listed
    assert real_dist_client.get("/api/network/buses").status_code == 200


def test_no_writable_path_resolves_inside_the_source_tree(monkeypatch, tmp_path):
    """Spec D6 — as a test, not a shell snippet, and covering all four paths."""
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    for var in ("PROJECTS_ROOT", "LEGACY_ROOT", "FLAT_PROJECTS_ROOT", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    settings_module.get_settings.cache_clear()
    try:
        s = settings_module.get_settings()
        backend = Path(app_paths.__file__).resolve().parent
        db_file = Path(s.database_url.split("///", 1)[1])
        for p in (s.projects_root, s.legacy_root, s.flat_projects_root, db_file):
            resolved = Path(p).resolve()
            assert backend not in resolved.parents and resolved != backend, p
    finally:
        settings_module.get_settings.cache_clear()
```

- [ ] **Step 2: Build the frontend and run**

```bash
cd pypsa-gui/frontend && npm run build
cd ../backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_local_mode_e2e.py -v
```

Expected: 4 passed. A 403 means the CSRF short-circuit regressed; a 401 means the auth branch
did; a login page at `/` means `decide_route` regressed.

- [ ] **Step 3: Full suite, both sides**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
cd ../frontend && npm test
```

Both must match the Task 0 baseline.

- [ ] **Step 4: Manual confirmation**

```bash
cd pypsa-gui/backend
PYPSAGUI_LOCAL_MODE=1 PYPSAGUI_APP_DATA_DIR=$(mktemp -d) \
  ../../.pixi/envs/default/bin/python -m uvicorn main:app --port 8123
```

Open `http://127.0.0.1:8123/`. Expected: the workbench, no login screen, no Vite server running.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/tests/test_local_mode_e2e.py
git commit -m "test(gui): end-to-end local mode against the real build output"
```

---

## Task 15: Retire the server-only surfaces in local mode

**Files:** Modify `backend/main.py` (admin router, replica middleware `:447-460`), `backend/security.py`; create `backend/tests/test_local_mode_surfaces.py`

**Context:** `routers/admin.py` mounts nine multi-tenant endpoints including a claim path that
`shutil.move`s whole project directories. The login throttle blocks for **15 minutes** after
10 attempts with a process restart as the only escape. `X-PyPSA-Replica` is dead weight.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_local_mode_surfaces.py
import pytest
from fastapi.testclient import TestClient

import local_mode
import main
import security


@pytest.fixture
def local_client(_auth_db, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
    with TestClient(main.app) as c:
        c.cookies.clear()
        yield c


def test_admin_router_is_not_reachable(local_client):
    assert local_client.get("/api/admin/organizations").status_code in (404, 405)


def test_no_replica_header(local_client):
    present = {k.lower() for k in local_client.get("/api/health").headers}
    assert security.REPLICA_HEADER.lower() not in present


def test_login_throttle_is_disabled(monkeypatch):
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    security.reset_login_throttle_for_tests()
    for _ in range(50):
        security.record_failed_login("127.0.0.1", local_mode.LOCAL_USER_EMAIL)
    assert security.login_retry_after("127.0.0.1", local_mode.LOCAL_USER_EMAIL) is None


def test_throttle_still_active_in_web_mode(monkeypatch):
    monkeypatch.delenv("PYPSAGUI_LOCAL_MODE", raising=False)
    security.reset_login_throttle_for_tests()
    for _ in range(50):
        security.record_failed_login("127.0.0.1", "someone@example.com")
    assert security.login_retry_after("127.0.0.1", "someone@example.com") is not None
    security.reset_login_throttle_for_tests()
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_local_mode_surfaces.py -v
```

Expected: the first three fail.

- [ ] **Step 3: Make the surfaces conditional**

The admin router and the replica middleware are registered at import time, when local mode is
already known from the environment, so a plain `if` is correct:

```python
# Nine multi-tenant endpoints, including a claim path that shutil.moves whole
# project directories. There is no second tenant locally and no admin to be.
if not local_mode.is_local_mode():
    app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
```

Guard the replica middleware registration (`:447-460`) the same way.

In `security.py`, as the first statement of `login_retry_after`:

```python
    # A 15-minute lockout with a process restart as the only escape is a support
    # call on a machine with one user and no attacker to throttle.
    import local_mode
    if local_mode.is_local_mode():
        return None
```

- [ ] **Step 4: Run both suites**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_local_mode_surfaces.py -v
../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
```

Expected: 4 passed; full suite matches baseline.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/main.py pypsa-gui/backend/security.py \
        pypsa-gui/backend/tests/test_local_mode_surfaces.py
git commit -m "feat(gui): retire admin, replica, and throttle surfaces in local mode"
```

---

## Done When

- `PYPSAGUI_LOCAL_MODE=1` boots with no login screen and a usable workbench, served entirely by uvicorn — no Vite process.
- Nothing writes inside `pypsa-gui/backend/` (Task 14's D6 test proves it).
- With `PYPSAGUI_LOCAL_MODE` unset, both suites match the Task 0 baseline exactly.
- `python -m tools.bootstrap_local` sets up a fresh machine from scratch.
- A first run on a machine with no `~/Library/Application Support/PyPSA GUI/` succeeds.

## Not In This Plan

- Workstreams E (human-readable storage layout) and F (migration of existing projects) — Phase 1b.
- Workstreams H–L (pywebview shell, PyInstaller, installers, API-key handling, CI) — Phase 2.
- The five Step 0b endpoints missing `set_active_project` (`create_from_template`, `import_bundle`, `import_unclaimed_project`, `load_project`, `create_scenario`) — belongs to the cloud/SaaS workstream. Web-mode only; local mode never holds a session.

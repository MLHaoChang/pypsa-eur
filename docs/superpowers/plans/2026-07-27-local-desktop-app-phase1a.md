# PyPSA GUI Local Desktop App — Phase 1a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make pypsa-gui run as a single-user local application in the dev environment — no login screen, SQLite, all writable paths outside the source tree, and the SPA served by FastAPI itself — with the web/multi-tenant deployment still fully working.

**Architecture:** Local mode is an additive flag, never a fork. One branch inside the existing auth middleware injects a seeded local user so the ~172 routes, the org-scoped storage layer, and the 541-test suite stay unmodified. A `PYPSAGUI_LOCAL_MODE` env var, set before `import main`, selects it. The frontend already has an `authEnabled === false` path; making `/api/health` report it is what activates it.

**Tech Stack:** FastAPI, SQLAlchemy 2 + Alembic, SQLite (local) / Postgres (web), React 19 + Vite 6, pytest, vitest.

**Source spec:** `docs/superpowers/specs/2026-07-26-pypsa-gui-desktop-app-design.md` (workstreams D, G, B, C, A).

**Scope note:** Phase 1b (workstreams E storage model + F migration) is a separate plan. Phase 2 (H–L: shell, freeze, installers, key handling, CI) follows that.

## Global Constraints

- **Both modes must keep working.** Every change is conditional on local mode or is mode-neutral. The web deployment is not being retired.
- **Never delete auth code.** Local mode bypasses; it does not remove.
- **Python ≥3.10**, SQLAlchemy ≥2.0, FastAPI ≥0.115, Alembic ≥1.13.
- **Cross-platform: Windows x64 and macOS arm64.** Use `pathlib` throughout; never assume `/` separators; open text files with explicit `encoding="utf-8"`.
- **New env vars use the `PYPSAGUI_` prefix, not `PYPSA_GUI_`.** PyPSA's option system claims the whole `PYPSA_*` namespace and already prints `Unknown option 'gui_auth_enabled'` warnings on every boot.
- **`get_settings()` and `security.allowed_origins()` are `lru_cache`d.** Any env var they read must be set before the first call, which happens at `import main`.
- **Existing tests must stay green.** Run `python -m pytest` from `pypsa-gui/backend` after every task.
- **Run the pixi env's Python:** `../../.pixi/envs/default/bin/python` from `pypsa-gui/backend`.

## Blocking Precondition

Step 0b of the cloud/SaaS migration is uncommitted in the working tree and touches
seven of the files below. **Do not start Task 1 until it is committed to `master`.**
Task 0 re-checks this.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app_paths.py` | NEW. Resolve per-user writable locations (app data dir, default projects root). No imports from `settings`, so `settings` can import it. |
| `backend/local_mode.py` | NEW. `is_local_mode()` predicate + the seeded org/user/membership bootstrap. Single place that knows what "local" means. |
| `backend/static_gate.py` | NEW. Pure port of `decideGateRoute` from `frontend/vite.auth-gate.ts` + the FastAPI catch-all that uses it. Pure function separated for testability. |
| `backend/settings.py` | MODIFY. Absolute SQLite default, `legacy_root` env override. |
| `backend/db/session.py` | MODIFY. SQLite WAL/busy_timeout pragmas, pool choice. |
| `backend/main.py` | MODIFY. Local-mode auth branch, CSRF short-circuit, health flag, static mount, first-run migrate+seed in `lifespan`. |
| `backend/routers/projects.py` | MODIFY. `PROJECTS_DIR` constant → function reading settings. |
| `backend/services/chat_service.py` | MODIFY. Resolve `chat.jsonl` from `ctx.storage_dir`. |
| `backend/alembic/env.py` | MODIFY. `render_as_batch=True`. |
| `frontend/src/api/client.ts` | MODIFY. CSRF request interceptor; suppress the 401 auth-ratchet in local mode. |
| `frontend/src/auth/AuthProvider.tsx` | MODIFY. Synthesize a local admin user when auth is off. |

---

## Task 0: Confirm the tree is safe to branch from

**Files:** none (verification only)

- [ ] **Step 1: Re-run the concurrency check**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git log --oneline -3 master
git status --short
ls -lt pypsa-gui/backend/main.py pypsa-gui/backend/services/pypsa_service.py
```

Expected: a Step 0b commit present in `git log`, and `git status --short` showing no
modifications under `pypsa-gui/backend/`. If either fails, **stop** — the premise of
this plan is a settled `master`.

- [ ] **Step 2: Branch**

```bash
git checkout master && git pull
git checkout -b feature/local-desktop-app-impl
```

- [ ] **Step 3: Record the baseline test count**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
```

Write the pass count down. Every later task compares against it.

---

## Task 1: Per-user writable paths

**Files:**
- Create: `pypsa-gui/backend/app_paths.py`
- Test: `pypsa-gui/backend/tests/test_app_paths.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `app_data_dir() -> Path`, `default_projects_root() -> Path`, `default_database_url() -> str`.

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
        assert "pypsa-gui" in str(d).lower()


def test_projects_root_default_is_user_visible():
    r = app_paths.default_projects_root()
    assert r.is_absolute()
    assert r.parts[-2:] == ("PyPSA GUI", "Projects")


def test_database_url_is_absolute_sqlite():
    url = app_paths.default_database_url()
    assert url.startswith("sqlite+pysqlite:///")
    assert not url.startswith("sqlite+pysqlite:///./")
    assert Path(url.removeprefix("sqlite+pysqlite:///")).is_absolute()


def test_env_overrides_win(monkeypatch, tmp_path):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "custom"))
    assert app_paths.app_data_dir() == tmp_path / "custom"
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

Deliberately imports nothing from this package. `settings.py` imports THIS
module for its defaults, so a dependency in the other direction would be a
cycle. Keep it free-standing.

Every path the application writes to must come from here. The previous
defaults were `__file__`-relative (`settings.py`) or CWD-relative (the
`.env` DATABASE_URL), and both land inside a read-only app bundle once the
backend is frozen — a macOS `.app` launched from Finder has cwd `/`.
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
    Projects live somewhere the user can find, back up, and zip — that is the
    point of a local app. Overridable because Documents is not always the
    right answer (network homes, small SSDs).
    """
    override = os.environ.get("PYPSAGUI_PROJECTS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / "Documents" / APP_NAME / "Projects").resolve()


def default_database_url() -> str:
    """Absolute on purpose: a relative SQLite URL resolves against cwd."""
    return f"sqlite+pysqlite:///{(app_data_dir() / 'pypsa-gui.db').as_posix()}"
```

- [ ] **Step 4: Run the tests**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_app_paths.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/app_paths.py pypsa-gui/backend/tests/test_app_paths.py
git commit -m "feat(gui): add app_paths for per-user writable locations"
```

---

## Task 2: Settings reads the new defaults

**Files:**
- Modify: `pypsa-gui/backend/settings.py:11` (`database_url`), `:38-39` (`projects_root`, `legacy_root`)
- Test: `pypsa-gui/backend/tests/test_settings_paths.py`

**Interfaces:**
- Consumes: `app_paths.default_projects_root()`, `app_paths.default_database_url()`.
- Produces: `Settings.projects_root`, `Settings.legacy_root`, `Settings.database_url` — all absolute, all env-overridable.

**Context:** `database_url` currently defaults to Postgres and `projects_root`/`legacy_root`
to `Path(__file__).parent / "projects"`. Web deployments always set `DATABASE_URL`
explicitly, so changing the *default* does not affect them.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_settings_paths.py
from pathlib import Path

import app_paths


def _fresh_settings(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import settings as settings_module
    settings_module.get_settings.cache_clear()
    return settings_module.get_settings()


def test_projects_root_defaults_outside_the_source_tree(monkeypatch, tmp_path):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.delenv("PROJECTS_ROOT", raising=False)
    monkeypatch.setenv("PYPSAGUI_PROJECTS_ROOT", str(tmp_path / "projects"))
    s = _fresh_settings(monkeypatch)
    backend = Path(app_paths.__file__).resolve().parent
    assert backend not in Path(s.projects_root).parents


def test_legacy_root_is_env_overridable(monkeypatch, tmp_path):
    s = _fresh_settings(monkeypatch, LEGACY_ROOT=str(tmp_path / "legacy"))
    assert Path(s.legacy_root) == tmp_path / "legacy"


def test_database_url_default_is_sqlite_not_postgres(monkeypatch, tmp_path):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    s = _fresh_settings(monkeypatch)
    assert s.database_url.startswith("sqlite+pysqlite:///")
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_settings_paths.py -v
```

Expected: `test_database_url_default_is_sqlite_not_postgres` fails — the value is the
Postgres URL. `test_legacy_root_is_env_overridable` may already pass via pydantic-settings.

- [ ] **Step 3: Edit `settings.py`**

Add the import at the top, next to the existing `from pathlib import Path`:

```python
import app_paths
```

Replace the `database_url` default (line 11):

```python
    # SQLite under the per-user app-data dir. Web deployments set DATABASE_URL
    # explicitly, so this default only ever applies to a local run — where the
    # previous Postgres default meant a 503 on every route (`main.py`'s auth
    # middleware converts the connection failure into "start Postgres").
    database_url: str = app_paths.default_database_url()
```

Replace the two path defaults (lines 38-39):

```python
    projects_root: Path = app_paths.default_projects_root()
    legacy_root: Path = app_paths.app_data_dir() / "legacy_unclaimed"
```

- [ ] **Step 4: Run the new tests and the full suite**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_settings_paths.py -v
../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
```

Expected: new tests pass; full-suite count matches the Task 0 baseline (`conftest.py`
pins `DATABASE_URL` and `PROJECTS_ROOT`, so it is insulated from these defaults).

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/settings.py pypsa-gui/backend/tests/test_settings_paths.py
git commit -m "feat(gui): default settings paths to per-user writable locations"
```

---

## Task 3: Collapse the second project root

**Files:**
- Modify: `pypsa-gui/backend/routers/projects.py:48`
- Test: `pypsa-gui/backend/tests/test_projects_dir_follows_settings.py`

**Interfaces:**
- Produces: `routers.projects.projects_dir() -> Path`. The module constant `PROJECTS_DIR` is removed.

**Context:** `PROJECTS_DIR = pathlib.Path(__file__).parent.parent / "projects"` never reads
settings, so setting `PROJECTS_ROOT` relocates `storage_path_for` but leaves
`_safe_project_dir`, the legacy scan, `routers/compare.py`, `services/upload_service.py`
and `services/chat_service.py` pointing into the source tree. This is the "half-relocated
app" failure.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_projects_dir_follows_settings.py
from pathlib import Path

import pytest


def test_projects_dir_follows_settings(monkeypatch, tmp_path):
    import settings as settings_module
    from routers import projects as projects_router

    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "relocated"))
    settings_module.get_settings.cache_clear()
    try:
        assert projects_router.projects_dir() == tmp_path / "relocated"
    finally:
        settings_module.get_settings.cache_clear()


def test_no_module_level_projects_dir_constant():
    from routers import projects as projects_router
    assert not hasattr(projects_router, "PROJECTS_DIR"), (
        "PROJECTS_DIR is a second source of truth; use projects_dir()"
    )
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_projects_dir_follows_settings.py -v
```

Expected: both fail — no `projects_dir`, and `PROJECTS_DIR` still exists.

- [ ] **Step 3: Replace the constant with a function**

In `routers/projects.py`, delete line 48 and add:

```python
def projects_dir() -> pathlib.Path:
    """
    The projects root, from settings, resolved per call.

    Was a module constant pinned to `__file__` — which meant PROJECTS_ROOT moved
    `storage_path_for` while every consumer here kept writing next to the source.
    Resolved per call rather than cached so a test's monkeypatched PROJECTS_ROOT
    takes effect without a reimport.
    """
    return pathlib.Path(get_settings().projects_root)
```

Then replace every `PROJECTS_DIR` reference in the file with `projects_dir()`.
Find them with:

```bash
grep -n "PROJECTS_DIR" pypsa-gui/backend/routers/projects.py
```

Known sites: the docstring at `:163`, the comment at `:167`, `dest = (PROJECTS_DIR / name).resolve()`
at `:180`, `PROJECTS_DIR.resolve()` at `:183`, the comment at `:207`, the scan at `:451-465`,
and the two `parent_project` reads at `:507-511`.

- [ ] **Step 4: Update the other importers**

```bash
grep -rn "PROJECTS_DIR" pypsa-gui/backend --include=*.py | grep -v __pycache__
```

Update `routers/compare.py`, `services/upload_service.py`, and `services/chat_service.py`
to `from routers.projects import projects_dir` and call it. Then:

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_projects_dir_follows_settings.py -v
../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
```

Expected: new tests pass, full suite matches baseline.

- [ ] **Step 5: Commit**

```bash
git add -A pypsa-gui/backend
git commit -m "fix(gui): make the projects root a single settings-driven source of truth"
```

---

## Task 4: Chat history follows the project

**Files:**
- Modify: `pypsa-gui/backend/services/chat_service.py` (`get_persist_path`, ~`:750-760`)
- Test: `pypsa-gui/backend/tests/test_chat_persist_path.py`

**Context:** `get_persist_path` builds `projects_dir() / ctx.loaded_project / "chat.jsonl"`
using the flat display name, while project data lives at
`projects_root/<org_uuid>/<project_uuid>/`. They are different directories today, which is
why `chat.jsonl` cannot be in the export bundle.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_chat_persist_path.py
from pathlib import Path

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
    ctx = _Ctx(None, "My Project")
    p = chat_service.get_persist_path(ctx)
    assert p is None or p.name == chat_service.CHAT_FILENAME
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_chat_persist_path.py -v
```

Expected: first test fails — the path is under the flat display name.

- [ ] **Step 3: Resolve from the bound context**

Replace the `expected = ...` lines in `get_persist_path`:

```python
    # Resolve from the BOUND context, not the display name. Project data lives
    # at projects_root/<org>/<project>/; the flat-name path was a pre-tenancy
    # leftover that put chat history in a different directory from the project
    # it belongs to — which is also why it could not go in the export bundle.
    storage_dir = getattr(ctx, "storage_dir", None)
    if storage_dir:
        expected = Path(storage_dir) / CHAT_FILENAME
    else:
        # UNBOUND (New Project) — no project directory exists yet.
        from routers.projects import projects_dir
        expected = projects_dir() / ctx.loaded_project / CHAT_FILENAME
```

- [ ] **Step 4: Run the tests**

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

## Task 5: SQLite concurrency pragmas

**Files:**
- Modify: `pypsa-gui/backend/db/session.py:10-44`
- Test: `pypsa-gui/backend/tests/test_sqlite_pragmas.py`

**Context:** `enable_sqlite_foreign_keys` already registers a `connect` listener; extend it
rather than adding a second. Measured today: `journal_mode: delete`, `busy_timeout: 5000`,
`QueuePool` size 5 + 10 overflow. Without WAL a writer blocks all readers, and after 5s the
`database is locked` error is swallowed by `main.py`'s bare `except Exception` and returned
as a 503 telling a desktop user to start Postgres.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_sqlite_pragmas.py
from sqlalchemy import create_engine, text

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
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_sqlite_pragmas.py -v
```

Expected: `ImportError: cannot import name 'configure_sqlite'`.

- [ ] **Step 3: Rename and extend the listener**

In `db/session.py`, rename `enable_sqlite_foreign_keys` to `configure_sqlite` and replace
the pragma body:

```python
def configure_sqlite(engine: Engine) -> Engine:
    """
    Per-connection SQLite pragmas. No-op on Postgres.

    foreign_keys — SQLite ships with enforcement OFF, per connection. Without it
    every ON DELETE SET NULL / CASCADE in db/models.py is inert.

    journal_mode=WAL — without it a writer blocks every reader. Chat tools write
    from a pool worker (`chat_tools.py` opens its own SessionLocal) while the
    request path reads, so contention is routine, not theoretical.

    busy_timeout — 5s (the default) is too short for that contention. Past it,
    `database is locked` surfaces through main.py's bare except as a 503 telling
    a desktop user to start Postgres.

    synchronous=NORMAL — safe under WAL, and materially faster than FULL.
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
```

Update `get_engine` to call it, and widen the SQLite connect args:

```python
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    return configure_sqlite(create_engine(url, **kwargs))
```

- [ ] **Step 4: Run the tests**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_sqlite_pragmas.py -v
../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/db/session.py pypsa-gui/backend/tests/test_sqlite_pragmas.py
git commit -m "fix(gui): enable WAL and a real busy timeout on SQLite"
```

---

## Task 6: Alembic batch mode for SQLite

**Files:**
- Modify: `pypsa-gui/backend/alembic/env.py`
- Test: `pypsa-gui/backend/tests/test_alembic_batch_mode.py`

**Context:** SQLite cannot `ALTER COLUMN` or `DROP COLUMN`. Without `render_as_batch=True`
the *next* migration written will pass on Postgres and fail on SQLite. Migration `0002`
works around this by calling `batch_alter_table` by hand; setting it globally means the
next author does not have to remember.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_alembic_batch_mode.py
from pathlib import Path


def test_env_py_sets_render_as_batch():
    env = (Path(__file__).resolve().parent.parent / "alembic" / "env.py").read_text(
        encoding="utf-8"
    )
    configures = env.count("context.configure(")
    assert configures > 0
    assert env.count("render_as_batch=True") >= configures, (
        "every context.configure() needs render_as_batch=True or SQLite "
        "migrations will fail on the next ALTER"
    )
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_alembic_batch_mode.py -v
```

Expected: FAIL — zero occurrences.

- [ ] **Step 3: Add the flag to both `context.configure()` calls**

In `alembic/env.py`, both the offline and online blocks:

```python
    context.configure(
        # ... existing kwargs unchanged ...
        # SQLite cannot ALTER/DROP COLUMN. Batch mode makes Alembic rebuild the
        # table instead, and is a no-op on Postgres — so one migration source
        # works on both. Without it a migration passes in CI on Postgres and
        # fails on a user's local SQLite.
        render_as_batch=True,
    )
```

- [ ] **Step 4: Run the test and verify migrations still apply**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_alembic_batch_mode.py -v
DATABASE_URL="sqlite+pysqlite:///$(mktemp -d)/probe.db" ../../.pixi/envs/default/bin/python -m alembic upgrade head
```

Expected: test passes; `alembic upgrade head` reports running `0001` then `0002`.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/alembic/env.py pypsa-gui/backend/tests/test_alembic_batch_mode.py
git commit -m "fix(gui): render alembic migrations in batch mode for SQLite"
```

---

## Task 7: Local-mode predicate and seed

**Files:**
- Create: `pypsa-gui/backend/local_mode.py`
- Test: `pypsa-gui/backend/tests/test_local_mode_seed.py`

**Interfaces:**
- Consumes: `db.models.{Organization, User, OrgMembership}`, `db.session.SessionLocal`.
- Produces: `is_local_mode() -> bool`, `LOCAL_ORG_ID`, `LOCAL_USER_ID` (fixed `uuid.UUID`s), `ensure_local_identity(db) -> User`, `get_local_user(db) -> User | None`.

**Context:** Fixed UUIDs so `projects_root/<org_id>/` is stable across reinstalls.
Constraints from `db/models.py`: `Organization.created_at` and `User.created_at` are NOT NULL
with no Python default; `OrgMembership.role` is NOT NULL with no default; `OrgMembership`
has `UniqueConstraint("user_id")`; `users.email` is uniquely indexed; `password_hash` is
nullable; `status` defaults to `"invited"` and `auth_service` rejects non-`"active"`.
The user needs `is_super_admin=True` **and** membership `role="admin"` to see everything —
`project_acl.can_access_project` treats `role == "admin"` as the only see-everything
short-circuit.

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
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s
    engine.dispose()


def test_is_local_mode_reads_env(monkeypatch):
    monkeypatch.delenv("PYPSAGUI_LOCAL_MODE", raising=False)
    assert local_mode.is_local_mode() is False
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    assert local_mode.is_local_mode() is True


def test_seed_creates_org_user_and_membership(db):
    user = local_mode.ensure_local_identity(db)
    assert user.id == local_mode.LOCAL_USER_ID
    assert user.status == "active"
    assert user.is_super_admin is True
    org = db.get(Organization, local_mode.LOCAL_ORG_ID)
    assert org is not None
    m = db.scalar(select(OrgMembership).where(OrgMembership.user_id == user.id))
    assert m is not None and m.role == "admin" and m.org_id == local_mode.LOCAL_ORG_ID


def test_seed_is_idempotent(db):
    a = local_mode.ensure_local_identity(db)
    b = local_mode.ensure_local_identity(db)
    assert a.id == b.id
    assert len(db.scalars(select(User)).all()) == 1
    assert len(db.scalars(select(OrgMembership)).all()) == 1


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
require_user/optional_user sites, org-scoped storage, a 541-test suite — local
mode seeds ONE org + user + membership and injects that user on every request.
Every downstream check then passes for the reason it was written to pass.

The IDs are fixed constants, not generated: `projects_root/<org_id>/<project_id>/`
embeds the org id, so a regenerated id would orphan every project directory on
reinstall.
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


def is_local_mode() -> bool:
    """
    True when the desktop shell set PYPSAGUI_LOCAL_MODE before `import main`.

    Read from os.environ rather than Settings because `main` branches on it at
    import time, before get_settings() is necessarily warm — and because
    Settings is lru_cached, which makes it awkward to flip in a test.
    """
    return os.environ.get("PYPSAGUI_LOCAL_MODE", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ensure_local_identity(db: DBSession) -> User:
    """
    Idempotently seed the local org, user, and membership. Returns the user.

    Select-then-insert rather than merge: `users.email` is uniquely indexed and
    OrgMembership carries UniqueConstraint("user_id"), so a blind insert on a
    second boot raises IntegrityError.

    status="active" is required — auth_service rejects anything else. Both
    created_at columns are NOT NULL with no Python default, so they are set
    explicitly. password_hash stays NULL: there is no login to perform.

    is_super_admin AND role="admin" are both needed. The first gates
    /api/admin/*; the second is the only see-everything short-circuit in
    project_acl.can_access_project.
    """
    org = db.get(Organization, LOCAL_ORG_ID)
    if org is None:
        org = Organization(id=LOCAL_ORG_ID, name=LOCAL_ORG_NAME, created_at=_now_utc())
        db.add(org)
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

    membership = db.scalar(
        select(OrgMembership).where(OrgMembership.user_id == LOCAL_USER_ID)
    )
    if membership is None:
        db.add(OrgMembership(org_id=LOCAL_ORG_ID, user_id=LOCAL_USER_ID, role="admin"))

    db.commit()
    db.refresh(user)
    return user


def get_local_user(db: DBSession) -> User | None:
    """
    Re-fetch the seeded user in the CALLER's session.

    Never cache the ORM object across requests: sessionmaker uses the default
    expire_on_commit=True, so a cached instance is detached and reading user.id
    raises DetachedInstanceError inside project_registry / project_acl.
    """
    return db.get(User, LOCAL_USER_ID)
```

- [ ] **Step 4: Run the tests**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_local_mode_seed.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/local_mode.py pypsa-gui/backend/tests/test_local_mode_seed.py
git commit -m "feat(gui): seed a single local identity for desktop mode"
```

---

## Task 8: Wire local mode into the auth gate

**Files:**
- Modify: `pypsa-gui/backend/main.py:240-270` (auth block), `:131-184` (`_csrf_rejection`), `:542-553` (health), `:123-127` (`lifespan`)
- Test: `pypsa-gui/backend/tests/test_local_mode_api.py`

**Context:** The auth middleware sets `request.state.auth_user` at `main.py:248` and 401s at
`:267`. Starlette makes the last-added middleware outermost, so an *added* middleware is
either overwritten at `:248` or never reached — the branch must live inside this block.
That block gates 118 of 172 routes that never touch `deps.optional_user`, so one edit covers
the whole surface. `deps.optional_user` separately honours a pre-populated
`request.state.auth_user` (`deps.py:112-113`), covering the dependency-based routes.

`_csrf_rejection` already returns `None` when no session cookie is present, so local mode is
exempt by construction — but a stale `pypsa_gui_session` cookie in a webview profile would
re-arm it, hence the explicit short-circuit.

Step 0b needs no special handling: `bind_active_project` returns early without a session
cookie, `PyPSAService._request_ctx` keeps its `None` default, and `_ensure_active` falls
through to the process foreground — which is the correct answer for one user.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_local_mode_api.py
"""
Local mode runs in a SEPARATE app instance from the rest of the suite: the
shared conftest imports `main` once with auth on, and local mode is read at
import time. Building the app here with the env pinned keeps both covered.
"""
import importlib
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def local_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'l.db').as_posix()}")
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    for mod in [m for m in sys.modules if m in {"main", "settings", "db.session", "security"}]:
        del sys.modules[mod]
    import main as main_module
    importlib.reload(main_module)
    with TestClient(main_module.app) as client:
        yield client


def test_health_reports_auth_disabled(local_client):
    body = local_client.get("/api/health").json()
    assert body["auth_enabled"] is False


def test_api_reachable_without_a_session_cookie(local_client):
    r = local_client.get("/api/projects/")
    assert r.status_code == 200, r.text


def test_mutation_succeeds_without_a_csrf_token(local_client):
    r = local_client.post("/api/network/reset")
    assert r.status_code != 403, r.text


def test_seeded_identity_is_visible(local_client):
    r = local_client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["is_super_admin"] is True
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_local_mode_api.py -v
```

Expected: `test_health_reports_auth_disabled` fails (hardcoded `True`) and the others 401.

- [ ] **Step 3: Add the branch**

At the top of `main.py`, next to the other local imports:

```python
import local_mode
```

In `lifespan`, before `yield`, create the schema and seed:

```python
async def lifespan(app: FastAPI):
    if local_mode.is_local_mode():
        # First run has no database at all. Alembic (not create_all) so the
        # file carries an alembic_version row and later migrations apply.
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(Path(__file__).parent / "alembic.ini"))
        cfg.set_main_option("script_location", str(Path(__file__).parent / "alembic"))
        cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
        Path(get_settings().projects_root).mkdir(parents=True, exist_ok=True)
        command.upgrade(cfg, "head")
        with db_session_module.SessionLocal() as db:
            local_mode.ensure_local_identity(db)
    PyPSAService.initialize()
    yield
```

In the auth block, replace the assignment at `:248` and guard the 401 at `:267`:

```python
        try:
            with db_session_module.SessionLocal() as db:
                if local_mode.is_local_mode():
                    # Re-fetched per request, never cached: expire_on_commit is
                    # on, so a cached User is detached and reading user.id
                    # raises inside project_registry / project_acl.
                    request.state.auth_user = local_mode.get_local_user(db)
                else:
                    request.state.auth_user = resolve_request_user(request, db)
        except Exception:
            # ... existing 503 handler unchanged ...
```

The 401 at `:267` needs no change — in local mode `auth_user` is never `None` after a
successful seed. If the seed failed, a 401 is the correct signal.

In `_csrf_rejection`, add as the first statement:

```python
    # No session cookie is ever issued locally, so the check below would already
    # exempt every request — but a stale cookie left in a packaged webview
    # profile would re-arm it, and the frontend never sends X-CSRF-Token (§5.6).
    if local_mode.is_local_mode():
        return None
```

In `health` at `:552`:

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

Expected: 4 new tests pass; the full suite still matches the Task 0 baseline — every
existing test runs with `PYPSAGUI_LOCAL_MODE` unset, so it takes the web branch.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/main.py pypsa-gui/backend/tests/test_local_mode_api.py
git commit -m "feat(gui): run the backend without authentication in local mode"
```

---

## Task 9: Frontend stops re-arming the login gate

**Files:**
- Modify: `frontend/src/api/client.ts:133-139`, `frontend/src/auth/AuthProvider.tsx:31-36`
- Test: `frontend/src/auth/localMode.test.ts`

**Context:** `client.ts` turns any 401 carrying `"Authentication required"` into
`setAuthEnabled(true)` plus a `pypsa-auth-backend-required` event, which
`AuthModeProvider` converts to `enableAuth()`. One stray 401 permanently re-arms the login
UI mid-session. Separately, `AuthProvider` sets `user = null` when auth is off, which makes
`hasAdminConsoleAccess(null)` false and `/admin/*` redirect away.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/auth/localMode.test.ts
import { describe, expect, it } from 'vitest'
import { shouldRearmAuth, localAdminUser } from './localMode'

describe('shouldRearmAuth', () => {
  it('re-arms on a real auth 401 when auth is enabled', () => {
    expect(shouldRearmAuth({ status: 401, detail: 'Authentication required', authEnabled: true })).toBe(true)
  })
  it('never re-arms when auth is disabled', () => {
    expect(shouldRearmAuth({ status: 401, detail: 'Authentication required', authEnabled: false })).toBe(false)
  })
  it('ignores unrelated 401s', () => {
    expect(shouldRearmAuth({ status: 401, detail: 'Bad token', authEnabled: true })).toBe(false)
  })
  it('ignores non-401s', () => {
    expect(shouldRearmAuth({ status: 403, detail: 'Authentication required', authEnabled: true })).toBe(false)
  })
})

describe('localAdminUser', () => {
  it('is an admin so the console stays reachable', () => {
    const u = localAdminUser()
    expect(u.is_super_admin).toBe(true)
    expect(u.role).toBe('admin')
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/frontend && npm test -- src/auth/localMode.test.ts
```

Expected: cannot resolve `./localMode`.

- [ ] **Step 3: Implement and wire in**

```typescript
// frontend/src/auth/localMode.ts
import type { AuthUser } from './types'

/**
 * Whether a 401 should turn the login UI back on.
 *
 * client.ts used to do this unconditionally, which made one stray 401 a
 * one-way ratchet: local mode would boot with auth off and re-arm the login
 * gate mid-session on the first unrelated 401.
 */
export function shouldRearmAuth(input: {
  status: number
  detail: unknown
  authEnabled: boolean
}): boolean {
  if (!input.authEnabled) return false
  if (input.status !== 401) return false
  return typeof input.detail === 'string' && input.detail.includes('Authentication required')
}

/**
 * The synthetic user rendered when auth is off.
 *
 * AuthProvider previously used `null`, which made hasAdminConsoleAccess(null)
 * false and bounced /admin/* to /projects. A local user owns their machine, so
 * they get the admin console.
 */
export function localAdminUser(): AuthUser {
  return {
    id: 'local',
    email: 'local@pypsa-gui.localhost',
    is_super_admin: true,
    role: 'admin',
  } as AuthUser
}
```

In `client.ts`, replace the unconditional re-arm at `:133-139` with:

```typescript
      if (shouldRearmAuth({ status, detail, authEnabled: getAuthEnabled() })) {
        setAuthEnabled(true)
        window.dispatchEvent(new Event('pypsa-auth-backend-required'))
      }
```

In `AuthProvider.tsx` at `:31-36`, replace `user = null` in the auth-off branch with
`localAdminUser()`.

- [ ] **Step 4: Run the tests**

```bash
cd pypsa-gui/frontend && npm test -- src/auth/localMode.test.ts && npm test
```

Expected: 5 new tests pass, existing vitest suite green.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/frontend/src/auth/localMode.ts pypsa-gui/frontend/src/auth/localMode.test.ts \
        pypsa-gui/frontend/src/api/client.ts pypsa-gui/frontend/src/auth/AuthProvider.tsx
git commit -m "fix(gui): stop re-arming the login gate when auth is disabled"
```

---

## Task 10: Send the CSRF token the backend asks for

**Files:**
- Modify: `frontend/src/api/client.ts` (add a request interceptor), `frontend/src/api/uploads.ts:76,88,102,130`, `frontend/src/api/chat.ts:63`, `frontend/src/pages/TopologyCanvas.tsx:147,2361`
- Test: `frontend/src/api/csrf.test.ts`

**Context:** This fixes a **live bug in the web deployment**, not just a desktop concern.
`_csrf_rejection` requires `X-CSRF-Token` to match the `pypsa_gui_csrf` cookie. The frontend
has zero occurrences of `csrf` in `src/`, the HTML entries, or the built bundle. `client.ts`
sets no `xsrfCookieName`/`xsrfHeaderName`, and axios's defaults (`XSRF-TOKEN` /
`X-XSRF-TOKEN`) do not match the backend's names. So every mutation by a logged-in browser
session should 403. The backend suite passes because `tests/conftest.py:226` sets the header
by hand.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/api/csrf.test.ts
import { describe, expect, it } from 'vitest'
import { readCsrfToken, CSRF_COOKIE, CSRF_HEADER } from './csrf'

describe('readCsrfToken', () => {
  it('reads the backend cookie name', () => {
    expect(readCsrfToken(`${CSRF_COOKIE}=abc123`)).toBe('abc123')
  })
  it('finds it among other cookies', () => {
    expect(readCsrfToken(`a=1; ${CSRF_COOKIE}=tok; b=2`)).toBe('tok')
  })
  it('does not match a cookie that merely ends with the name', () => {
    expect(readCsrfToken(`not_${CSRF_COOKIE}=nope`)).toBeNull()
  })
  it('url-decodes', () => {
    expect(readCsrfToken(`${CSRF_COOKIE}=a%2Bb`)).toBe('a+b')
  })
  it('returns null when absent', () => {
    expect(readCsrfToken('other=1')).toBeNull()
  })
  it('uses the header name the backend checks', () => {
    expect(CSRF_HEADER).toBe('X-CSRF-Token')
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/frontend && npm test -- src/api/csrf.test.ts
```

Expected: cannot resolve `./csrf`.

- [ ] **Step 3: Implement and apply**

```typescript
// frontend/src/api/csrf.ts
/**
 * Double-submit CSRF, browser half.
 *
 * The backend has always required this (main.py `_csrf_rejection`); the
 * frontend never sent it. Axios's built-in XSRF support does not help — its
 * defaults are XSRF-TOKEN / X-XSRF-TOKEN and the backend uses these names.
 */
export const CSRF_COOKIE = 'pypsa_gui_csrf'
export const CSRF_HEADER = 'X-CSRF-Token'

export function readCsrfToken(cookieString: string): string | null {
  for (const part of cookieString.split(';')) {
    const [rawName, ...rest] = part.trim().split('=')
    if (rawName === CSRF_COOKIE) return decodeURIComponent(rest.join('='))
  }
  return null
}

/** Header bag for a mutating request, empty when there is no token. */
export function csrfHeaders(): Record<string, string> {
  const token = typeof document === 'undefined' ? null : readCsrfToken(document.cookie)
  return token ? { [CSRF_HEADER]: token } : {}
}
```

Add the interceptor in `client.ts`, after `export const client = axios.create({...})`:

```typescript
// Attach the double-submit token to every mutating request. GET/HEAD are
// exempt server-side (CSRF_SAFE_METHODS), so skip the cookie read for them.
client.interceptors.request.use((config) => {
  const method = (config.method ?? 'get').toUpperCase()
  if (method === 'GET' || method === 'HEAD' || method === 'OPTIONS') return config
  Object.assign((config.headers ??= {}), csrfHeaders())
  return config
})
```

Add `...csrfHeaders()` to the `headers` of each raw `fetch` mutation site listed in
**Files** above. Find any others with:

```bash
grep -rn "fetch(\|sendBeacon(" pypsa-gui/frontend/src | grep -v ".test."
```

- [ ] **Step 4: Run the tests**

```bash
cd pypsa-gui/frontend && npm test -- src/api/csrf.test.ts && npm test
```

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/frontend/src/api/csrf.ts pypsa-gui/frontend/src/api/csrf.test.ts \
        pypsa-gui/frontend/src/api/client.ts pypsa-gui/frontend/src/api/uploads.ts \
        pypsa-gui/frontend/src/api/chat.ts pypsa-gui/frontend/src/pages/TopologyCanvas.tsx
git commit -m "fix(gui): send the CSRF token the backend requires"
```

---

## Task 11: Accept a dynamic origin

**Files:**
- Test: `pypsa-gui/backend/tests/test_dynamic_origin.py`
- Modify: none in the backend — this task proves the env contract the desktop shell must honour.

**Context:** `settings.py:23` pins `cors_allowed_origins` to the two Vite dev origins, and
that one string drives **both** CORS and the CSRF Origin check. Browsers send `Origin` on
same-origin non-GET, so a shell on an ephemeral port gets `403 csrf_origin_rejected` on every
mutation. Both `get_settings()` and `security.allowed_origins()` are `lru_cache`d, so the
value must be in `os.environ` before `import main`. Local mode's `_csrf_rejection`
short-circuit (Task 8) already covers the desktop case; this test pins the contract so a
future change cannot silently break the web-on-a-custom-port case.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_dynamic_origin.py
import security
import settings as settings_module


def test_origin_allowlist_follows_the_env(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:51234")
    settings_module.get_settings.cache_clear()
    security.reset_caches_for_tests()
    try:
        assert security.is_allowed_origin("http://127.0.0.1:51234") is True
        assert security.is_allowed_origin("http://127.0.0.1:5173") is False
    finally:
        settings_module.get_settings.cache_clear()
        security.reset_caches_for_tests()


def test_caches_must_be_cleared_to_see_a_change(monkeypatch):
    """Documents the ordering constraint the desktop shell depends on."""
    settings_module.get_settings.cache_clear()
    security.reset_caches_for_tests()
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:40000")
    assert security.is_allowed_origin("http://127.0.0.1:40000") is False
    settings_module.get_settings.cache_clear()
    security.reset_caches_for_tests()
    assert security.is_allowed_origin("http://127.0.0.1:40000") is True
```

- [ ] **Step 2: Run it**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_dynamic_origin.py -v
```

Expected: PASS. This is a characterization test — if it fails, the caching contract changed
and the desktop shell's env ordering is no longer safe.

- [ ] **Step 3: Document the contract**

Add to `pypsa-gui/README.md` under a new "Local desktop mode" heading:

```markdown
### Local desktop mode

The shell MUST set these before `import main` — `get_settings()` and
`security.allowed_origins()` are both `lru_cache`d and read once:

| Variable | Value |
|---|---|
| `PYPSAGUI_LOCAL_MODE` | `1` |
| `DATABASE_URL` | absolute SQLite path under the app-data dir |
| `PROJECTS_ROOT` | user-visible projects folder |
| `LEGACY_ROOT` | app-data dir |
| `CORS_ALLOWED_ORIGINS` | `http://127.0.0.1:<chosen port>` |
| `MPLBACKEND` | `Agg` |
```

- [ ] **Step 4: Re-run the suite**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/tests/test_dynamic_origin.py pypsa-gui/README.md
git commit -m "test(gui): pin the dynamic-origin env contract for the desktop shell"
```

---

## Task 12: Port the SPA routing gate to Python

**Files:**
- Create: `pypsa-gui/backend/static_gate.py`
- Test: `pypsa-gui/backend/tests/test_static_gate.py`

**Interfaces:**
- Produces: `is_static_asset(path: str) -> bool`, `decide_route(path: str, *, local_mode: bool, authed: bool) -> Decision` where `Decision` is `("serve", "spa.html"|"index.html") | ("redirect", str) | ("passthrough", None)`.

**Context:** The routing brain today is `frontend/vite.auth-gate.ts:41-67` (`decideGateRoute`),
registered only via `configureServer` — it emits nothing into `dist/`. `vite.config.ts` sets
`appType: 'mpa'`, which disables Vite's SPA history fallback. Two traps: `dist/index.html`
is the **login** page with no React entry, so a stock `StaticFiles(html=True)` catch-all
serves a sign-in form for `/projects`; and wiring `/` to `spa.html` instead creates an
infinite redirect loop via `spa.html:46` (`location.replace('/?needLogin=…')`).

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_static_gate.py
import pytest

from static_gate import decide_route, is_static_asset


@pytest.mark.parametrize("path", [
    "/assets/spa-B6BHlEqH.js", "/brand.css", "/img/logo.svg",
    "/favicon.ico", "/api/health",
])
def test_static_assets_pass_through(path):
    assert is_static_asset(path) is True


@pytest.mark.parametrize("path", ["/", "/projects", "/app", "/admin/users", "/login.html"])
def test_html_routes_are_not_static(path):
    assert is_static_asset(path) is False


def test_local_mode_always_serves_the_spa():
    for path in ["/", "/projects", "/app", "/admin/users"]:
        assert decide_route(path, local_mode=True, authed=False) == ("serve", "spa.html")


def test_local_mode_never_redirects_to_the_login_document():
    """The spa.html boot gate redirects to '/' on a 401; serving index.html
    there would bounce back and loop."""
    kind, target = decide_route("/", local_mode=True, authed=False)
    assert (kind, target) == ("serve", "spa.html")


def test_web_mode_anonymous_gets_the_login_document():
    assert decide_route("/", local_mode=False, authed=False) == ("serve", "index.html")
    assert decide_route("/projects", local_mode=False, authed=False) == ("serve", "index.html")


def test_web_mode_authed_deep_links_get_the_spa():
    assert decide_route("/projects", local_mode=False, authed=True) == ("serve", "spa.html")


def test_web_mode_authed_root_redirects_to_projects():
    assert decide_route("/", local_mode=False, authed=True) == ("redirect", "/projects")


def test_spa_html_is_never_served_directly_in_web_mode():
    assert decide_route("/spa.html", local_mode=False, authed=False) == ("redirect", "/")


def test_login_html_always_serves():
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
this logic reimplemented, not copied.

Two traps a stock StaticFiles(html=True) mount walks into:
  * dist/index.html is the LOGIN page with no React entry (byte-identical to
    dist/login.html). A catch-all that serves it for /projects renders a
    sign-in form instead of the app.
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

Decision = tuple[str, str | None]


def is_static_asset(path: str) -> bool:
    """True for anything that must be served verbatim rather than routed."""
    if path.startswith(_ASSET_PREFIXES) or path in _ASSET_FILES:
        return True
    leaf = path.rsplit("/", 1)[-1]
    return "." in leaf and not leaf.endswith(".html")


def decide_route(path: str, *, local_mode: bool, authed: bool) -> Decision:
    """
    Which document to serve for an HTML navigation.

    Returns ("serve", filename) or ("redirect", location).
    """
    if local_mode:
        # No login exists. Every HTML route is the app — including "/", which
        # is what breaks the redirect loop described above.
        return ("serve", SPA)

    if path == "/login.html":
        return ("serve", LOGIN)

    if authed:
        if path in ("/", "/index.html"):
            return ("redirect", "/projects")
        return ("serve", SPA)

    # Anonymous: the login document, and never spa.html directly.
    if path == f"/{SPA}":
        return ("redirect", "/")
    return ("serve", LOGIN)
```

- [ ] **Step 4: Run the tests**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_static_gate.py -v
```

Expected: 16 passed.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/static_gate.py pypsa-gui/backend/tests/test_static_gate.py
git commit -m "feat(gui): port the SPA routing gate to the backend"
```

---

## Task 13: Serve the built SPA from FastAPI

**Files:**
- Modify: `pypsa-gui/backend/main.py` (mount after all routers), `pypsa-gui/backend/settings.py` (add `frontend_dist`)
- Test: `pypsa-gui/backend/tests/test_serve_spa.py`

**Interfaces:**
- Consumes: `static_gate.decide_route`, `static_gate.is_static_asset`.
- Produces: a catch-all GET route at document root. Assets in `dist/` are root-absolute (`/assets/…`), so the mount must be at `/`, not a sub-path.

**Context:** Do **not** validate with `npm run preview` — it registers the same Vite gate and
will pass while the packaged path fails.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_serve_spa.py
import pytest


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


def test_local_mode_serves_spa_at_root(local_spa_client):
    r = local_spa_client.get("/")
    assert r.status_code == 200
    assert "id='spa'" in r.text


def test_local_mode_serves_spa_for_deep_links(local_spa_client):
    for path in ["/projects", "/app", "/admin/users"]:
        r = local_spa_client.get(path)
        assert r.status_code == 200, path
        assert "id='spa'" in r.text, path


def test_assets_are_served_verbatim(local_spa_client):
    assert local_spa_client.get("/assets/spa.js").status_code == 200
    assert local_spa_client.get("/brand.css").status_code == 200


def test_api_routes_are_not_swallowed(local_spa_client):
    assert local_spa_client.get("/api/health").status_code == 200


def test_unknown_asset_404s_rather_than_returning_html(local_spa_client):
    r = local_spa_client.get("/assets/missing.js")
    assert r.status_code == 404
```

Add this fixture to the same file (written out in full — do not import it from Task 8's
module; each test file builds its own app instance because local mode is read at import
time):

```python
import importlib
import sys

from fastapi.testclient import TestClient


@pytest.fixture
def local_spa_client(tmp_path, dist, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'l.db').as_posix()}")
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    for mod in [m for m in sys.modules if m in {"main", "settings", "db.session", "security"}]:
        del sys.modules[mod]
    import main as main_module
    importlib.reload(main_module)
    with TestClient(main_module.app) as client:
        yield client
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_serve_spa.py -v
```

Expected: every HTML route 404s — nothing serves static files today.

- [ ] **Step 3: Add the setting and the mount**

In `settings.py`:

```python
    # Built SPA. Overridable so the frozen app can point at its bundled copy.
    frontend_dist: Path = Path(__file__).resolve().parent.parent / "frontend" / "dist"
```

In `main.py`, **after every `include_router` call** (a catch-all registered earlier would
shadow the API):

```python
from fastapi.responses import FileResponse
from fastapi import HTTPException
import static_gate

_DIST = Path(get_settings().frontend_dist)


@app.get("/{full_path:path}", include_in_schema=False)
def serve_spa(full_path: str, request: Request):
    """
    Serve the built SPA. Registered last so /api/* wins.

    Mounted at document root because every asset reference in dist/ is
    root-absolute (/assets/…, /brand.css) — a sub-path mount serves the HTML
    and 404s every asset.
    """
    if not _DIST.is_dir():
        raise HTTPException(status_code=503, detail="Frontend not built. Run `npm run build`.")

    path = "/" + full_path
    if static_gate.is_static_asset(path):
        candidate = (_DIST / full_path).resolve()
        # Traversal guard: resolve, then confirm containment.
        if not candidate.is_relative_to(_DIST.resolve()) or not candidate.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(candidate)

    authed = getattr(request.state, "auth_user", None) is not None
    kind, target = static_gate.decide_route(
        path, local_mode=local_mode.is_local_mode(), authed=authed
    )
    if kind == "redirect":
        return RedirectResponse(url=target, status_code=302)
    return FileResponse(_DIST / target)
```

Add `RedirectResponse` to the `fastapi.responses` import.

- [ ] **Step 4: Run both suites**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_serve_spa.py -v
../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/main.py pypsa-gui/backend/settings.py pypsa-gui/backend/tests/test_serve_spa.py
git commit -m "feat(gui): serve the built SPA from the backend"
```

---

## Task 14: End-to-end local-mode smoke test

**Files:**
- Test: `pypsa-gui/backend/tests/test_local_mode_e2e.py`

- [ ] **Step 1: Write the test**

```python
# pypsa-gui/backend/tests/test_local_mode_e2e.py
"""One pass through the whole local path: boot, list, create, mutate, read."""


def test_full_local_journey(local_spa_client):
    assert local_spa_client.get("/api/health").json()["auth_enabled"] is False
    assert "id='spa'" in local_spa_client.get("/projects").text
    assert local_spa_client.get("/api/projects/").status_code == 200

    created = local_spa_client.post("/api/projects/smoke-test")
    assert created.status_code in (200, 201), created.text

    listed = local_spa_client.get("/api/projects/").json()
    assert any(p.get("name") == "smoke-test" for p in listed), listed

    assert local_spa_client.get("/api/network/buses").status_code == 200
```

- [ ] **Step 2: Run it**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_local_mode_e2e.py -v
```

Expected: PASS. A 403 means the CSRF short-circuit regressed; a 401 means the auth branch did.

- [ ] **Step 3: Confirm the projects folder is outside the source tree**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python - <<'PY'
from pathlib import Path
from settings import get_settings
root = Path(get_settings().projects_root).resolve()
backend = Path("settings.py").resolve().parent
assert backend not in root.parents and root != backend, f"projects_root inside source tree: {root}"
print("projects_root OK:", root)
PY
```

- [ ] **Step 4: Full suite against the Task 0 baseline**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
cd ../frontend && npm test
```

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/tests/test_local_mode_e2e.py
git commit -m "test(gui): end-to-end smoke test for local mode"
```

---

## Task 15: Retire the server-only surfaces in local mode

**Files:**
- Modify: `pypsa-gui/backend/main.py` (admin router registration, replica middleware, the auth 503 handler), `pypsa-gui/backend/security.py` (`login_retry_after`)
- Test: `pypsa-gui/backend/tests/test_local_mode_surfaces.py`

**Context:** Three server-deployment leftovers are wrong on a desktop. `routers/admin.py`
mounts nine multi-tenant endpoints including a `shutil.move` claim path. The login throttle
blocks for **15 minutes** after 10 attempts with a process restart as the only escape. The
`X-PyPSA-Replica` header is dead weight. Separately, the bare `except Exception` around the
auth DB lookup returns a 503 telling the user to "Start Postgres … run alembic upgrade head",
which is nonsense locally and is exactly what a `database is locked` error surfaces as.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_local_mode_surfaces.py
import security


def test_admin_router_is_not_mounted(local_client):
    assert local_client.get("/api/admin/organizations").status_code == 404


def test_no_replica_header(local_client):
    assert security.REPLICA_HEADER.lower() not in {
        k.lower() for k in local_client.get("/api/health").headers
    }


def test_login_throttle_is_disabled(monkeypatch):
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    security.reset_login_throttle_for_tests()
    for _ in range(50):
        security.record_failed_login("127.0.0.1", "local@pypsa-gui.localhost")
    assert security.login_retry_after("127.0.0.1", "local@pypsa-gui.localhost") is None


def test_db_failure_message_does_not_mention_postgres(local_client, monkeypatch):
    import db.session as db_session_module

    def _boom():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db_session_module, "SessionLocal", _boom)
    r = local_client.get("/api/projects/")
    assert r.status_code == 503
    assert "Postgres" not in r.json()["detail"]
```

Reuse the `local_client` fixture from Task 8 by copying it into this file.

- [ ] **Step 2: Run it and watch it fail**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_local_mode_surfaces.py -v
```

Expected: all four fail — admin mounts, the header is present, the throttle blocks, the
message names Postgres.

- [ ] **Step 3: Make the three surfaces conditional**

In `main.py`, guard the admin router registration:

```python
# Nine multi-tenant endpoints, including a claim path that shutil.moves whole
# project directories. There is no second tenant locally and no admin to be.
if not local_mode.is_local_mode():
    app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
```

Guard the replica middleware the same way, and split the 503 message:

```python
            if local_mode.is_local_mode():
                detail = (
                    "Local database unavailable. Close any other running copy of "
                    "PyPSA GUI and try again."
                )
            else:
                detail = (
                    "Auth database unavailable. Start Postgres (or use a sqlite "
                    "DATABASE_URL), run alembic upgrade head, then restart the backend."
                )
            return JSONResponse(status_code=503, content={"detail": detail})
```

In `security.py`, add as the first statement of `login_retry_after`:

```python
    # A 15-minute lockout with a restart as the only escape is a support call
    # on a machine with exactly one user and no attacker to throttle.
    import local_mode
    if local_mode.is_local_mode():
        return None
```

- [ ] **Step 4: Run both suites**

```bash
cd pypsa-gui/backend && ../../.pixi/envs/default/bin/python -m pytest tests/test_local_mode_surfaces.py -v
../../.pixi/envs/default/bin/python -m pytest -q 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/main.py pypsa-gui/backend/security.py \
        pypsa-gui/backend/tests/test_local_mode_surfaces.py
git commit -m "feat(gui): retire admin, replica, and throttle surfaces in local mode"
```

---

## Done When

- `PYPSAGUI_LOCAL_MODE=1` + a SQLite `DATABASE_URL` boots with no login screen and a usable workbench.
- The backend serves the SPA; the Vite dev server is not required.
- Nothing writes inside `pypsa-gui/backend/`.
- With `PYPSAGUI_LOCAL_MODE` unset, the full existing suite matches the Task 0 baseline.
- A logged-in browser session in web mode can mutate without a 403 (the §5.6 bug is fixed).

## Not In This Plan

- Workstreams E (human-readable storage layout) and F (migration of existing projects) — Phase 1b.
- Workstreams H–L (pywebview shell, PyInstaller, installers, API-key handling, CI) — Phase 2.
- The five endpoints missing `set_active_project` in the Step 0b work — belongs to that workstream.

# Local Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the packaged desktop app a Settings pane that stores an Anthropic API key and points at the application log, so the chat feature it already ships can actually run.

**Architecture:** A dependency-free store module at the backend root writes `local-settings.json` into the per-user app-data directory. `main.py` publishes the stored key into `os.environ` at import, but only when the variable is unset. A local-mode-gated router exposes read, write-and-verify, and reveal-log. A new frontend pane consumes those three routes and hides itself when they 404.

**Tech Stack:** Python 3.12 / FastAPI / pydantic, pytest; React 18 / TypeScript / axios / Zustand, vitest.

**Spec:** `docs/superpowers/specs/2026-08-03-local-settings-design.md`

## Global Constraints

- **Branch is `feature/local-app-impl`.** Re-run `git branch --show-current` before every commit. Other sessions share this worktree — leave the tree clean when pausing.
- **Never write to or delete under `pypsa-gui/backend/projects/` or `~/Documents/PyPSA GUI/` or `~/Documents/PyPSA Studio/`.** These hold 113 MB of irreplaceable user work.
- **Every test that touches app data must set `PYPSAGUI_APP_DATA_DIR` to a `tmp_path`.** A test that calls `app_paths.app_data_dir()` without the override writes into the developer's real `~/Library/Application Support/PyPSA Studio/`.
- **Backend test command:** `cd "<repo-root>" && pixi run gui-tests <pytest args>`. Never hardcode an interpreter path.
- **Never pipe a test run into `tail`/`head`.** A shell pipeline reports only its last stage's exit status, so `pytest … | tail` always exits 0. Use `pixi run gui-tests … > /tmp/log 2>&1; echo "EXIT=$?"` and read the log.
- **Never pass `-q` to pytest.** `pytest.ini` already sets `addopts = -q`; a second `-q` becomes `-qq` and suppresses the final summary line. Use `-v` for single tests.
- **Frontend test command:** `cd pypsa-gui/frontend && npx vitest run <path>`.
- **Use path-limited `git commit <paths>`, never `git add -A`.** A new file needs `git add <path>` first.
- **The chat implementation is not touched.** The four existing `os.environ["ANTHROPIC_API_KEY"]` read sites and `chat_service._build_anthropic_client` stay exactly as they are. The entire integration is that the variable is now set.
- **One build serves both the desktop app and the web deployment.** Nothing may be gated on a build flag; local mode is a runtime environment variable read per call.
- **Probe statuses are exactly these five strings:** `valid`, `rejected`, `unreachable`, `sdk_not_installed`, `cleared`. The spec names the first four; `cleared` is the status of the no-probe path the spec describes as *"unless the key was cleared"*.
- **The API key literal is never returned by any route and never written to a log.**

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `pypsa-gui/backend/local_settings.py` | create | read/write `local-settings.json`; publish key to env |
| `pypsa-gui/backend/tests/test_local_settings_store.py` | create | store unit tests |
| `pypsa-gui/backend/smoke/check_bundle.py` | modify (`:38`) | forbid the settings file from shipping |
| `pypsa-gui/backend/main.py` | modify (after `:25`, at `:40`, near `:767`) | apply key at import; mount router |
| `pypsa-gui/backend/tests/test_local_settings_startup.py` | create | precedence, incl. a fresh-interpreter proof |
| `pypsa-gui/backend/routers/local_settings.py` | create | GET / PUT / reveal, local-mode gated |
| `pypsa-gui/backend/tests/test_local_settings_api.py` | create | route + probe + reveal tests |
| `pypsa-gui/frontend/src/api/localSettings.ts` | create | typed client + pure mapping functions |
| `pypsa-gui/frontend/src/api/localSettings.test.ts` | create | mapping unit tests |
| `pypsa-gui/frontend/src/hooks/useLocalSettings.ts` | create | shared react-query hook; one fetch feeds pane and nav |
| `pypsa-gui/frontend/src/pages/LocalSettings.tsx` | create | the pane |
| `pypsa-gui/frontend/src/store/uiStore.ts` | modify (`:30`) | add `'settings'` to `SlidePanel` |
| `pypsa-gui/frontend/src/App.tsx` | modify (`:97`, `:121`) | `PANEL_META` entry + switch case |
| `pypsa-gui/frontend/src/layout/Sidebar.tsx` | modify (`:1282`) | nav row |
| `pypsa-gui/frontend/src/components/CommandPalette.tsx` | modify (`:346`) | command entry |

---

### Task 1: The settings store

**Files:**
- Create: `pypsa-gui/backend/local_settings.py`
- Create: `pypsa-gui/backend/tests/test_local_settings_store.py`
- Modify: `pypsa-gui/backend/smoke/check_bundle.py:38`

**Interfaces:**
- Consumes: `app_paths.app_data_dir() -> Path` (existing, reads `PYPSAGUI_APP_DATA_DIR` on every call — never cached).
- Produces, for Tasks 2 and 3:
  - `settings_path() -> Path`
  - `read_settings() -> dict[str, str]`
  - `stored_api_key() -> str | None`
  - `api_key_hint(key: str | None) -> str | None`
  - `write_api_key(key: str) -> None`
  - `apply_to_environ() -> bool`

- [ ] **Step 1: Write the failing tests**

Create `pypsa-gui/backend/tests/test_local_settings_store.py`:

```python
"""
The store behind the desktop Settings pane.

`app_paths.app_data_dir()` reads PYPSAGUI_APP_DATA_DIR on every call and caches
nothing, so a monkeypatched environment is all the isolation these tests need —
no `get_settings.cache_clear()`, no module reloading.
"""
import json
import logging
import os
import stat
import sys

import pytest

import local_settings


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    """Point app-data at a temp dir. MANDATORY: without it these tests write
    into the developer's real ~/Library/Application Support/PyPSA Studio/."""
    target = tmp_path / "appdata"
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(target))
    return target


def test_write_then_read_round_trips(appdata):
    local_settings.write_api_key("sk-ant-abc123def456")

    assert local_settings.stored_api_key() == "sk-ant-abc123def456"


def test_empty_string_removes_the_key_entirely(appdata):
    local_settings.write_api_key("sk-ant-abc123def456")

    local_settings.write_api_key("")

    assert local_settings.stored_api_key() is None
    stored = json.loads(local_settings.settings_path().read_text(encoding="utf-8"))
    assert "anthropic_api_key" not in stored, (
        "an empty string must remove the entry, not store an empty value — "
        "otherwise absence has two representations"
    )


def test_surrounding_whitespace_is_stripped(appdata):
    local_settings.write_api_key("  sk-ant-abc123def456\n")

    assert local_settings.stored_api_key() == "sk-ant-abc123def456"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_file_is_created_with_mode_600(appdata):
    """
    Set at creation via os.open, never by a chmod afterwards — a chmod leaves a
    window in which a live API key is world-readable.
    """
    local_settings.write_api_key("sk-ant-abc123def456")

    mode = stat.S_IMODE(local_settings.settings_path().stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_rewrite_keeps_mode_600(appdata):
    """os.replace adopts the temp file's mode; a second write must not widen it."""
    local_settings.write_api_key("sk-ant-first-key-value")
    local_settings.write_api_key("sk-ant-second-key-value")

    mode = stat.S_IMODE(local_settings.settings_path().stat().st_mode)
    assert mode == 0o600, f"expected 0o600 after rewrite, got {oct(mode)}"


def test_malformed_json_is_ignored_rather_than_raised(appdata, caplog):
    path = local_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert local_settings.stored_api_key() is None

    assert "not valid JSON" in caplog.text


def test_a_json_array_is_ignored_rather_than_raised(appdata):
    path = local_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('["not", "an", "object"]', encoding="utf-8")

    assert local_settings.read_settings() == {}


def test_missing_app_data_directory_is_created(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "no" / "such" / "dir"))

    local_settings.write_api_key("sk-ant-abc123def456")

    assert local_settings.stored_api_key() == "sk-ant-abc123def456"


def test_hint_is_the_last_four_characters():
    assert local_settings.api_key_hint("sk-ant-abcd1234") == "1234"


def test_hint_is_none_for_a_short_key():
    """Four of seven characters would disclose most of the value."""
    assert local_settings.api_key_hint("sk-ant") is None
    assert local_settings.api_key_hint(None) is None


def test_apply_to_environ_sets_an_unset_variable(appdata, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    local_settings.write_api_key("sk-ant-from-the-file")

    assert local_settings.apply_to_environ() is True
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-the-file"


def test_apply_to_environ_never_overrides_the_environment(appdata, monkeypatch):
    """
    Mirrors `load_dotenv(override=False)` at main.py:23. This is what keeps a
    web deployment, and a developer shell with the key exported, unaffected by
    a file only the desktop app ever writes.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-the-shell")
    local_settings.write_api_key("sk-ant-from-the-file")

    assert local_settings.apply_to_environ() is False
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-the-shell"


def test_apply_to_environ_is_a_no_op_with_no_stored_key(appdata, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert local_settings.apply_to_environ() is False
    assert "ANTHROPIC_API_KEY" not in os.environ
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests tests/test_local_settings_store.py -v > /tmp/t1.log 2>&1; echo "EXIT=$?"
```

Expected: collection error, `ModuleNotFoundError: No module named 'local_settings'`.

- [ ] **Step 3: Write the store**

Create `pypsa-gui/backend/local_settings.py`:

```python
"""
Per-user local settings for the desktop app.

Imports only stdlib and `app_paths`, deliberately: `main.py` reads this module
at import time, before the router graph exists, and `app_paths` itself imports
nothing from this package to avoid exactly that cycle.

It holds one thing today — the Anthropic API key — and it exists because the
packaged app has no other way to receive one. `backend/.env` is excluded from
the bundle on purpose (`smoke/check_bundle.py`: it carries a real key and the
SECRET_KEY that signs sessions), and a `.app` launched from Finder sources no
shell profile, so ANTHROPIC_API_KEY is unset by construction.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import app_paths

logger = logging.getLogger(__name__)

_FILENAME = "local-settings.json"
_API_KEY = "anthropic_api_key"

# Below this length "the last four characters" discloses most of the value.
_MIN_HINT_LENGTH = 8


def settings_path() -> Path:
    return app_paths.app_data_dir() / _FILENAME


def read_settings() -> dict[str, str]:
    """
    The stored settings, or `{}`. NEVER raises.

    A missing file is the normal first-run state. An unreadable or malformed
    one is a warning, not a launch failure — the same rule
    `desktop.bootstrap.install_file_logging` follows, and for the same reason:
    an app-data problem must never be why the app will not start.
    """
    path = settings_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        logger.warning("local settings: %s could not be read; ignoring it", path)
        return {}

    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("local settings: %s is not valid JSON; ignoring it", path)
        return {}
    if not isinstance(data, dict):
        logger.warning("local settings: %s is not a JSON object; ignoring it", path)
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)}


def stored_api_key() -> str | None:
    """The stored key, or None. Blank and absent are the same answer."""
    key = read_settings().get(_API_KEY, "").strip()
    return key or None


def api_key_hint(key: str | None) -> str | None:
    """Last four characters, or None when that would disclose too much."""
    if not key or len(key) < _MIN_HINT_LENGTH:
        return None
    return key[-4:]


def write_api_key(key: str) -> None:
    """
    Persist the key; an empty string removes the entry.

    Two properties the tests pin, both about the same risk:
      * mode 0600 AT CREATION via `os.open`. A `chmod` after writing leaves a
        window in which a live key is world-readable.
      * atomic `os.replace`, so a crash mid-write cannot leave a truncated file
        that reads back as "no key configured".
    """
    data = read_settings()
    key = key.strip()
    if key:
        data[_API_KEY] = key
    else:
        data.pop(_API_KEY, None)

    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    # Adopts the temp file's 0600, so a pre-existing wider mode is corrected.
    os.replace(tmp, path)


def apply_to_environ() -> bool:
    """
    Publish the stored key as ANTHROPIC_API_KEY. Returns True if it set it.

    **The stored key NEVER overrides the environment.** This mirrors
    `load_dotenv(override=False)` at `main.py:23` and is what keeps the web
    deployment — and a developer shell with the key exported — unaffected by a
    file that only the desktop app ever writes.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return False
    key = stored_api_key()
    if not key:
        return False
    os.environ["ANTHROPIC_API_KEY"] = key
    return True
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests tests/test_local_settings_store.py -v > /tmp/t1.log 2>&1; echo "EXIT=$?"
```

Expected: `EXIT=0`, 13 passed.

- [ ] **Step 5: Forbid the file from ever shipping**

In `pypsa-gui/backend/smoke/check_bundle.py`, extend `FORBIDDEN_FILES` (currently at `:38`):

```python
FORBIDDEN_FILES = {
    "auth_dev.db",       # a password hash plus absolute developer paths
    "auth_dev.db-wal",
    "auth_dev.db-shm",
    "pypsa-gui.db",      # a real user's database, if a build ran from app-data
    # Written by `local_settings.py` into app-data and holds a live Anthropic
    # key. On the forbidden list for the same reason `.env` is: a build that
    # ever ran from an app-data directory would otherwise bundle it.
    "local-settings.json",
}
```

- [ ] **Step 6: Verify the bundle check still passes on the existing build**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui"
pixi run --manifest-path ../pixi.toml python backend/smoke/check_bundle.py "dist-app/PyPSA Studio.app" > /tmp/t1b.log 2>&1; echo "EXIT=$?"; cat /tmp/t1b.log
```

Expected: `EXIT=0` and a "clean" line. If `dist-app/PyPSA Studio.app` is absent, skip this step and say so in the report — it is a regression guard on an artifact, not a build requirement.

- [ ] **Step 7: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git branch --show-current   # must print feature/local-app-impl
git add pypsa-gui/backend/local_settings.py pypsa-gui/backend/tests/test_local_settings_store.py
git commit pypsa-gui/backend/local_settings.py pypsa-gui/backend/tests/test_local_settings_store.py pypsa-gui/backend/smoke/check_bundle.py -m "feat(gui): store a local Anthropic API key in app-data

The packaged app reads ANTHROPIC_API_KEY from os.environ at four sites and
has no way to receive one: .env is excluded from the bundle on purpose and a
.app launched from Finder sources no shell profile.

Mode 0600 at creation rather than a chmod afterwards, and an atomic replace,
because the failure modes are a world-readable key and a truncated file that
reads back as 'no key configured'."
```

---

### Task 2: Publish the stored key at startup

**Files:**
- Modify: `pypsa-gui/backend/main.py` (after the `load_dotenv` block ending `:25`)
- Create: `pypsa-gui/backend/tests/test_local_settings_startup.py`

**Interfaces:**
- Consumes: `local_settings.apply_to_environ() -> bool` from Task 1.
- Produces: nothing new. This task proves the wiring works in a fresh interpreter.

**Why a subprocess test:** `tests/conftest.py` imports `main` once per session, so no in-process test can re-trigger a module-level call. The original defect was precisely "the import-time chain does not run in the real process", so the only honest test starts a new interpreter.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_local_settings_startup.py`:

```python
"""
The import-time chain: a key on disk becomes ANTHROPIC_API_KEY in the process.

Runs in a SUBPROCESS on purpose. conftest imports `main` once per session, so
an in-process test can never re-trigger a module-level call — and "the chain
does not run in the real process" is exactly the defect this guards.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

PROBE = (
    "import os, sys; sys.path.insert(0, %r); "
    "import main; "
    "print('KEY=' + os.environ.get('ANTHROPIC_API_KEY', ''))"
)


def _run_probe(tmp_path, *, env_key: str | None, file_key: str | None) -> str:
    """Import `main` in a clean interpreter and report the resulting env var."""
    appdata = tmp_path / "appdata"
    appdata.mkdir(parents=True, exist_ok=True)
    if file_key is not None:
        (appdata / "local-settings.json").write_text(
            json.dumps({"anthropic_api_key": file_key}), encoding="utf-8",
        )

    env = dict(os.environ)
    # MANDATORY isolation: all three, or the child writes to real user data.
    env["PYPSAGUI_APP_DATA_DIR"] = str(appdata)
    env["PYPSAGUI_PROJECTS_ROOT"] = str(tmp_path / "projects")
    env["DATABASE_URL"] = f"sqlite+pysqlite:///{(tmp_path / 'probe.db').as_posix()}"
    env.pop("ANTHROPIC_API_KEY", None)
    if env_key is not None:
        env["ANTHROPIC_API_KEY"] = env_key

    result = subprocess.run(
        [sys.executable, "-c", PROBE % str(BACKEND)],
        cwd=str(BACKEND), env=env, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    for line in result.stdout.splitlines():
        if line.startswith("KEY="):
            return line[len("KEY="):]
    raise AssertionError(f"probe printed no KEY= line:\n{result.stdout[-4000:]}")


def test_stored_key_is_published_on_import(tmp_path):
    assert _run_probe(tmp_path, env_key=None, file_key="sk-ant-from-the-file") == (
        "sk-ant-from-the-file"
    )


def test_environment_wins_over_the_stored_key(tmp_path):
    """A shell that exported a key must not be overridden by app-data."""
    assert _run_probe(
        tmp_path, env_key="sk-ant-from-the-shell", file_key="sk-ant-from-the-file",
    ) == "sk-ant-from-the-shell"


def test_no_stored_key_leaves_the_variable_unset(tmp_path):
    assert _run_probe(tmp_path, env_key=None, file_key=None) == ""
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests tests/test_local_settings_startup.py -v > /tmp/t2.log 2>&1; echo "EXIT=$?"
```

Expected: `test_stored_key_is_published_on_import` FAILS (`assert '' == 'sk-ant-from-the-file'`). The other two pass already — that is correct and expected; they pin behaviour that must not regress.

- [ ] **Step 3: Wire it into main.py**

In `pypsa-gui/backend/main.py`, immediately after the `try: from dotenv import load_dotenv … except ImportError: pass` block (which ends at `:25`), add:

```python
# Publish a key stored by the desktop Settings pane, but only when the
# environment does not already carry one — same precedence as the
# `override=False` above, and for the same reason. The packaged app has no
# other channel: `.env` is excluded from the bundle and a `.app` launched
# from Finder sources no shell profile.
#
# Module level, not a startup event, so it lands before ANY module reads the
# variable. `app_paths` reads PYPSAGUI_APP_DATA_DIR per call and the desktop
# launcher applies its environment before `import main`, so the path is
# already correct here.
import local_settings as local_settings_store  # noqa: E402

local_settings_store.apply_to_environ()
```

The alias matters: Task 3 adds `local_settings` to the `from routers import (…)` tuple at `:40`, and that name would otherwise shadow this module.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests tests/test_local_settings_startup.py -v > /tmp/t2.log 2>&1; echo "EXIT=$?"
```

Expected: `EXIT=0`, 3 passed.

- [ ] **Step 5: Confirm nothing else regressed**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests tests/test_chat_sse.py tests/test_chat_metrics.py -v > /tmp/t2b.log 2>&1; echo "EXIT=$?"
```

Expected: `EXIT=0`. These two files manipulate `ANTHROPIC_API_KEY` directly; if the new import-time call interferes with them, it surfaces here.

- [ ] **Step 6: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git branch --show-current
git add pypsa-gui/backend/tests/test_local_settings_startup.py
git commit pypsa-gui/backend/main.py pypsa-gui/backend/tests/test_local_settings_startup.py -m "feat(gui): publish a stored API key into the process environment

Module level rather than a startup event so it lands before any module reads
the variable, and never overriding an already-set one.

Tested in a subprocess: conftest imports main once per session, so no
in-process test can re-trigger an import-time call - and 'the chain does not
run in the real process' is the defect being guarded."
```

---

### Task 3: The read and write routes

**Files:**
- Create: `pypsa-gui/backend/routers/local_settings.py`
- Create: `pypsa-gui/backend/tests/test_local_settings_api.py`
- Modify: `pypsa-gui/backend/main.py:40` (router import tuple) and near `:767` (mount)

**Interfaces:**
- Consumes: `local_settings.stored_api_key()`, `.api_key_hint()`, `.write_api_key()` from Task 1; `local_mode.reject_unless_local_mode` (existing, `local_mode.py:78`); `app_paths.app_data_dir()`.
- Produces, for Task 4: the module-level `router`, the constant `LOG_FILENAME = "pypsa-gui.log"`, and the helper `_state() -> dict`.
- Produces, for Task 5 (frontend): the response shapes below.

**Route contract:**

```
GET /api/local-settings
  -> {"key_set": bool, "key_hint": str|null, "log_path": str}

PUT /api/local-settings/anthropic-key   body {"api_key": str}
  -> {"status": "valid"|"rejected"|"unreachable"|"sdk_not_installed"|"cleared",
      "detail": str, "key_set": bool, "key_hint": str|null, "log_path": str}
```

Both 404 when `PYPSAGUI_LOCAL_MODE` is unset.

- [ ] **Step 1: Write the failing tests**

Create `pypsa-gui/backend/tests/test_local_settings_api.py`:

```python
"""
The desktop Settings routes.

Follows `tests/test_local_mode_api.py`: NO importlib.reload and NO sys.modules
surgery. `local_mode.is_local_mode()` reads os.environ per call, so the app
object conftest already imported serves both modes and a fixture only has to
flip the environment.
"""
import os

import pytest
from fastapi.testclient import TestClient

import local_mode
import local_settings
import main


@pytest.fixture
def local_client(_auth_db, monkeypatch, tmp_path):
    """Local mode on, app data isolated to tmp_path."""
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
    try:
        with TestClient(main.app) as c:
            c.cookies.clear()
            yield c
    finally:
        with session_local() as db:
            local_mode.remove_local_identity(db)


@pytest.fixture
def no_probe(monkeypatch):
    """Neutralise the network probe. Probe mapping is tested separately."""
    monkeypatch.setattr(
        "routers.local_settings.probe_api_key",
        lambda: ("valid", "Key accepted."),
    )


# ── the gate ──────────────────────────────────────────────────────────────
# These three are the security property of this router. In web mode the
# server's Anthropic key is not something an authenticated user may replace,
# and the log path is not theirs to learn. 404, not 403: the surface does not
# exist there — matching every other door closed by reject_unless_local_mode.

def test_get_is_404_in_web_mode(client):
    assert client.get("/api/local-settings").status_code == 404


def test_put_is_404_in_web_mode(client):
    r = client.put("/api/local-settings/anthropic-key", json={"api_key": "sk-ant-x"})
    assert r.status_code == 404


def test_reveal_is_404_in_web_mode(client):
    assert client.post("/api/local-settings/reveal-log").status_code == 404


# ── read ──────────────────────────────────────────────────────────────────

def test_get_reports_no_key_on_a_fresh_profile(local_client):
    body = local_client.get("/api/local-settings").json()

    assert body["key_set"] is False
    assert body["key_hint"] is None
    assert body["log_path"].endswith("pypsa-gui.log")


def test_get_never_returns_the_key_itself(local_client, no_probe):
    secret = "sk-ant-supersecretvalue9999"
    local_client.put("/api/local-settings/anthropic-key", json={"api_key": secret})

    raw = local_client.get("/api/local-settings").text

    assert secret not in raw
    assert local_client.get("/api/local-settings").json()["key_hint"] == "9999"


# ── write ─────────────────────────────────────────────────────────────────

def test_put_persists_and_publishes_the_key(local_client, no_probe):
    r = local_client.put(
        "/api/local-settings/anthropic-key", json={"api_key": "sk-ant-abc123def456"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "valid"
    assert r.json()["key_set"] is True
    assert local_settings.stored_api_key() == "sk-ant-abc123def456"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-abc123def456"


def test_put_takes_effect_without_a_restart(local_client, no_probe):
    """chat_health reads os.environ per request; setting the key must flip it."""
    assert local_client.get("/api/chat/health").json()["anthropic_api_key_present"] is False

    local_client.put(
        "/api/local-settings/anthropic-key", json={"api_key": "sk-ant-abc123def456"},
    )

    assert local_client.get("/api/chat/health").json()["anthropic_api_key_present"] is True


def test_empty_string_clears_key_and_environment(local_client, no_probe):
    local_client.put(
        "/api/local-settings/anthropic-key", json={"api_key": "sk-ant-abc123def456"},
    )

    r = local_client.put("/api/local-settings/anthropic-key", json={"api_key": ""})

    assert r.json()["status"] == "cleared"
    assert r.json()["key_set"] is False
    assert local_settings.stored_api_key() is None
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_key_is_saved_even_when_the_probe_cannot_reach_anthropic(
    local_client, monkeypatch,
):
    """
    Being offline is not a reason to discard what the user just typed — but
    'unreachable' must be reported as unreachable, never as success.
    """
    monkeypatch.setattr(
        "routers.local_settings.probe_api_key",
        lambda: ("unreachable", "connection refused"),
    )

    r = local_client.put(
        "/api/local-settings/anthropic-key", json={"api_key": "sk-ant-abc123def456"},
    )

    assert r.json()["status"] == "unreachable"
    assert r.json()["key_set"] is True
    assert local_settings.stored_api_key() == "sk-ant-abc123def456"


def test_a_rejected_key_is_still_saved_and_reported_distinctly(
    local_client, monkeypatch,
):
    monkeypatch.setattr(
        "routers.local_settings.probe_api_key",
        lambda: ("rejected", "invalid x-api-key"),
    )

    r = local_client.put(
        "/api/local-settings/anthropic-key", json={"api_key": "sk-ant-abc123def456"},
    )

    assert r.json()["status"] == "rejected"
    assert local_settings.stored_api_key() == "sk-ant-abc123def456"


# ── probe mapping ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "exc_name, expected",
    [
        ("AuthenticationError", "rejected"),
        ("PermissionDeniedError", "rejected"),
        ("APIConnectionError", "unreachable"),
    ],
)
def test_probe_maps_sdk_exceptions(monkeypatch, exc_name, expected):
    import anthropic

    from routers import local_settings as routes

    exc_class = getattr(anthropic, exc_name)

    class _Models:
        def list(self, **kwargs):
            # `__new__` without `__init__`: the SDK's exceptions require
            # (message, response, body) to construct, and none of that is
            # relevant here — only the class matters, because that is what the
            # `except` clause in probe_api_key dispatches on.
            raise exc_class.__new__(exc_class)

    class _Client:
        models = _Models()

    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: _Client())

    status, _detail = routes.probe_api_key()

    assert status == expected


def test_probe_reports_valid_when_the_call_returns(monkeypatch):
    import anthropic

    from routers import local_settings as routes

    class _Models:
        def list(self, **kwargs):
            return object()

    class _Client:
        models = _Models()

    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: _Client())

    assert routes.probe_api_key()[0] == "valid"


def test_probe_maps_an_unexpected_exception_to_unreachable(monkeypatch):
    """Unknown failure is 'we could not check', never 'the key is fine'."""
    import anthropic

    from routers import local_settings as routes

    class _Models:
        def list(self, **kwargs):
            raise RuntimeError("something else entirely")

    class _Client:
        models = _Models()

    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: _Client())

    assert routes.probe_api_key()[0] == "unreachable"


# ── secret hygiene ────────────────────────────────────────────────────────

def test_the_key_literal_never_reaches_a_log_or_a_response(
    local_client, monkeypatch, caplog,
):
    """
    Drives the REAL probe code path — only the SDK client is faked, so the
    route's own exception handling is what runs. No network: a test that dials
    Anthropic is slow, flaky, and fails offline.

    Nothing may carry the key literal out — not the response, not a log record.
    """
    import logging

    import anthropic

    secret = "sk-ant-donotlogme1234567890"

    class _Models:
        def list(self, **kwargs):
            raise anthropic.AuthenticationError.__new__(anthropic.AuthenticationError)

    class _Client:
        models = _Models()

    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: _Client())

    with caplog.at_level(logging.DEBUG):
        put = local_client.put(
            "/api/local-settings/anthropic-key", json={"api_key": secret},
        )
        get = local_client.get("/api/local-settings")

    assert secret not in caplog.text
    assert secret not in put.text
    assert secret not in get.text


def test_probe_detail_never_carries_sdk_exception_text(monkeypatch, caplog):
    """
    The detail strings are fixed. An SDK message that happened to embed the key
    could not survive into the response, because it is never formatted in.
    """
    import logging

    import anthropic

    from routers import local_settings as routes

    class _Models:
        def list(self, **kwargs):
            raise RuntimeError("x-api-key sk-ant-leakedthroughtheexception")

    class _Client:
        models = _Models()

    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: _Client())

    with caplog.at_level(logging.DEBUG):
        status, detail = routes.probe_api_key()

    assert status == "unreachable"
    assert "sk-ant-leakedthroughtheexception" not in detail
    assert "sk-ant-leakedthroughtheexception" not in caplog.text
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests tests/test_local_settings_api.py -v > /tmp/t3.log 2>&1; echo "EXIT=$?"
```

Expected: collection error, `ModuleNotFoundError: No module named 'routers.local_settings'`.

- [ ] **Step 3: Write the router**

Create `pypsa-gui/backend/routers/local_settings.py`:

```python
"""
The desktop app's own settings surface: the Anthropic key and the log path.

Every route is gated by `local_mode.reject_unless_local_mode`, whose docstring
already carries the reasoning: the gate is not "admin only", it is "this
deployment has exactly one tenant, and they own the disk". On a web deployment
the server's API key is not something an authenticated user may replace, and
the server's app-data path is not theirs to learn — so these routes 404 there.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel

import app_paths
import local_mode
import local_settings

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(local_mode.reject_unless_local_mode)])

# Duplicates `desktop.bootstrap._LOG_FILENAME` deliberately. `desktop/__init__.py`
# states the rule: "Nothing here may be imported by `main` — the hosted
# deployment must not acquire a dependency on the desktop shell." One shared
# string is the cheaper price.
LOG_FILENAME = "pypsa-gui.log"


class ApiKeyBody(BaseModel):
    api_key: str


def _state() -> dict:
    key = local_settings.stored_api_key()
    return {
        "key_set": key is not None,
        "key_hint": local_settings.api_key_hint(key),
        "log_path": str(app_paths.app_data_dir() / LOG_FILENAME),
    }


def probe_api_key() -> tuple[str, str]:
    """
    Ask Anthropic whether the key works. Returns `(status, detail)`.

    `models.list` is the cheapest possible auth probe: it returns model
    metadata and bills no tokens.

    NEVER raises, and the three failure modes stay DISTINCT. A key we could not
    check is not a key that works and must not render as one — the same rule
    the economics surfaces follow for an unresolvable cost.

    **SDK exception text never reaches the response or the log.** The detail
    strings below are fixed, and only the exception CLASS NAME is logged — a
    class name cannot contain an API key. This is stronger than scrubbing:
    there is no formatting step for a key to survive.
    """
    try:
        import anthropic
    except ImportError:
        return "sdk_not_installed", "The anthropic package is missing from this build."

    try:
        anthropic.Anthropic().models.list(limit=1)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        logger.warning("local settings: key probe rejected (%s)", type(exc).__name__)
        return "rejected", "Anthropic rejected this key."
    except Exception as exc:  # noqa: BLE001 — every other failure is "unknown"
        logger.warning("local settings: key probe failed (%s)", type(exc).__name__)
        return "unreachable", "Could not reach Anthropic to verify the key."
    return "valid", "Key accepted."


@router.get("")
def get_local_settings() -> dict:
    """Presence and a hint. The key itself is never returned."""
    return _state()


@router.put("/anthropic-key")
def put_anthropic_key(body: ApiKeyBody) -> dict:
    """
    Store the key, publish it, then report what Anthropic said about it.

    Persist BEFORE probing: a network failure must not discard what the user
    just typed. The probe result is reported, never conflated with success.
    """
    key = body.api_key.strip()
    local_settings.write_api_key(key)

    if key:
        os.environ["ANTHROPIC_API_KEY"] = key
        status, detail = probe_api_key()
    else:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        status, detail = "cleared", "Key removed. Chat is disabled."

    logger.info("local settings: anthropic key updated, probe=%s", status)
    return {"status": status, "detail": detail, **_state()}
```

- [ ] **Step 4: Mount it in main.py**

Add `local_settings` to the existing import tuple at `main.py:40`:

```python
from routers import (
    ...,
    local_settings,
    ...,
)
```

and mount it beside the chat router (near `:767`):

```python
# Desktop-only. Every route 404s in web mode; see routers/local_settings.py.
app.include_router(
    local_settings.router, prefix="/api/local-settings", tags=["local-settings"],
)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests tests/test_local_settings_api.py -v > /tmp/t3.log 2>&1; echo "EXIT=$?"
```

Expected: `EXIT=0`. The three `reveal-log` gate tests pass because a route that does not exist also 404s — Task 4 makes them meaningful. Every other test must pass now.

If `GET /api/local-settings` returns 307 rather than 200, the `@router.get("")` path form is wrong for the installed FastAPI; change it to `@router.get("/")` and update the tests to `/api/local-settings/`.

- [ ] **Step 6: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git branch --show-current
git add pypsa-gui/backend/routers/local_settings.py pypsa-gui/backend/tests/test_local_settings_api.py
git commit pypsa-gui/backend/routers/local_settings.py pypsa-gui/backend/tests/test_local_settings_api.py pypsa-gui/backend/main.py -m "feat(gui): read and write the local Anthropic key over HTTP

Persist before probing, so a network failure cannot discard what the user
just typed - and report 'unreachable' as unreachable rather than as success.
A key that was never checked is not a key that works.

All routes 404 in web mode via reject_unless_local_mode; that gate is an
executable assertion here, not a comment."
```

---

### Task 4: Reveal the log file

**Files:**
- Modify: `pypsa-gui/backend/routers/local_settings.py`
- Modify: `pypsa-gui/backend/tests/test_local_settings_api.py`

**Interfaces:**
- Consumes: `router`, `LOG_FILENAME` and `app_paths.app_data_dir()` from Task 3.
- Produces, for Task 5: `POST /api/local-settings/reveal-log -> {"revealed": bool, "detail"?: str, "log_path": str}`.

**This introduces the only `subprocess` call in the application.** It is acceptable for one specific reason, and the tests pin that reason: **nothing from the request reaches the command.** The route takes no parameters and the path is computed server-side from `app_paths`. There is no argument to inject into because there is no argument.

- [ ] **Step 1: Write the failing tests**

Append to `pypsa-gui/backend/tests/test_local_settings_api.py`:

```python
# ── reveal ────────────────────────────────────────────────────────────────

def test_reveal_runs_a_fixed_command_with_no_request_input(local_client, monkeypatch):
    """
    The whole safety argument for the only subprocess call in the app: every
    element of argv is either a literal or derived from app_paths. If a future
    change lets a request parameter reach argv, this test is what catches it.
    """
    seen = {}

    def _fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return None

    monkeypatch.setattr("routers.local_settings.subprocess.run", _fake_run)

    r = local_client.post("/api/local-settings/reveal-log")

    assert r.status_code == 200, r.text
    assert r.json()["revealed"] is True
    assert isinstance(seen["argv"], list), "argv must be a list — never a shell string"
    assert seen["kwargs"]["shell"] is False
    assert seen["kwargs"]["check"] is False

    # Every element is either a hardcoded literal or the server-computed path.
    # Nothing else may ever appear here.
    from pathlib import Path

    log_path = r.json()["log_path"]
    literals = {"open", "-R", "explorer", "xdg-open"}
    permitted_paths = {log_path, str(Path(log_path).parent), f"/select,{log_path}"}
    for part in seen["argv"]:
        assert part in literals or part in permitted_paths, (
            f"argv element {part!r} is neither a hardcoded literal nor the "
            f"server-computed log path — a request parameter may have reached argv"
        )


def test_reveal_creates_the_log_file_if_it_is_missing(local_client, monkeypatch):
    """A reveal that selects nothing reads as a broken button."""
    monkeypatch.setattr("routers.local_settings.subprocess.run", lambda *a, **k: None)

    r = local_client.post("/api/local-settings/reveal-log")

    from pathlib import Path
    assert Path(r.json()["log_path"]).exists()


def test_reveal_failure_is_reported_not_raised(local_client, monkeypatch):
    """
    200 with revealed=false, not a 500. The pane still shows the path and a
    Copy button, so the feature degrades instead of dead-ending.
    """
    def _boom(*args, **kwargs):
        raise OSError("no file manager on this box")

    monkeypatch.setattr("routers.local_settings.subprocess.run", _boom)

    r = local_client.post("/api/local-settings/reveal-log")

    assert r.status_code == 200, r.text
    assert r.json()["revealed"] is False
    assert "no file manager" in r.json()["detail"]
    assert r.json()["log_path"].endswith("pypsa-gui.log")


@pytest.mark.parametrize(
    "platform, expected_head",
    [("darwin", ["open", "-R"]), ("win32", ["explorer"]), ("linux", ["xdg-open"])],
)
def test_reveal_argv_per_platform(monkeypatch, tmp_path, platform, expected_head):
    from routers import local_settings as routes

    monkeypatch.setattr(routes.sys, "platform", platform)

    argv = routes._reveal_argv(tmp_path / "pypsa-gui.log")

    assert argv[: len(expected_head)] == expected_head
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests tests/test_local_settings_api.py -v -k reveal > /tmp/t4.log 2>&1; echo "EXIT=$?"
```

Expected: the four new tests fail (`AttributeError: module 'routers.local_settings' has no attribute 'subprocess'`, and 404 on the POST). The three `*_is_404_in_web_mode` tests still pass.

- [ ] **Step 3: Implement reveal**

In `pypsa-gui/backend/routers/local_settings.py`, add to the imports:

```python
import subprocess
import sys
from pathlib import Path
```

and append:

```python
def _reveal_argv(path: Path) -> list[str]:
    """
    The platform's "show this file" command.

    Linux has no portable reveal-and-select, so it opens the containing
    directory instead — the honest degradation, rather than pretending.
    """
    if sys.platform == "darwin":
        return ["open", "-R", str(path)]
    if sys.platform == "win32":
        # No space after the comma: explorer parses `/select,<path>` as ONE
        # token. It also exits non-zero on success, which is why check=False
        # below is load-bearing rather than lazy.
        return ["explorer", f"/select,{path}"]
    return ["xdg-open", str(path.parent)]


@router.post("/reveal-log")
def reveal_log() -> dict:
    """
    Show the log file in the platform file manager.

    This is the only `subprocess` invocation in the application. It is
    acceptable for one specific reason: NOTHING from the request reaches the
    command. This route takes no parameters and the path is computed here from
    `app_paths`. There is no argument to inject into because there is no
    argument — and `test_reveal_runs_a_fixed_command_with_no_request_input`
    exists to keep it that way.
    """
    path = app_paths.app_data_dir() / LOG_FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        subprocess.run(_reveal_argv(path), shell=False, check=False, timeout=10)
    except Exception as exc:  # noqa: BLE001 — reported, never fatal
        logger.warning("local settings: reveal-log failed: %s", exc)
        return {"revealed": False, "detail": str(exc), "log_path": str(path)}
    return {"revealed": True, "log_path": str(path)}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests tests/test_local_settings_api.py -v > /tmp/t4.log 2>&1; echo "EXIT=$?"
```

Expected: `EXIT=0`, whole file green.

- [ ] **Step 5: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git branch --show-current
git commit pypsa-gui/backend/routers/local_settings.py pypsa-gui/backend/tests/test_local_settings_api.py -m "feat(gui): reveal the application log in the file manager

The only subprocess call in the app. Safe for one specific reason: the route
takes no parameters and the path is computed server-side, so no request input
reaches argv - with a test that fails if that ever stops being true.

A failure returns 200 with revealed=false so the pane falls back to showing
the path, rather than dead-ending on a 500."
```

---

### Task 5: Frontend client and mapping functions

**Files:**
- Create: `pypsa-gui/frontend/src/api/localSettings.ts`
- Create: `pypsa-gui/frontend/src/api/localSettings.test.ts`

**Interfaces:**
- Consumes: the route contracts from Tasks 3 and 4; `client` from `./client` (axios, `baseURL: '/api'`, supports `skipErrorToast`).
- Produces, for Task 6: `LocalSettingsState`, `ProbeStatus`, `PutKeyResponse`, `fetchLocalSettings()`, `putApiKey()`, `revealLog()`, `keyFieldPlaceholder()`, `probeMessage()`.

Pure mapping functions are exported and tested directly, following the pattern established for `Economics.tsx`: the display logic is where an "unknown" quietly becomes a "fine", so it gets its own tests rather than being reachable only through a rendered component.

- [ ] **Step 1: Write the failing tests**

Create `pypsa-gui/frontend/src/api/localSettings.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { keyFieldPlaceholder, probeMessage } from './localSettings'

describe('keyFieldPlaceholder', () => {
  it('prompts for a key when none is stored', () => {
    expect(keyFieldPlaceholder(null)).toBe('sk-ant-…')
    expect(keyFieldPlaceholder({ key_set: false, key_hint: null, log_path: '/l' }))
      .toBe('sk-ant-…')
  })

  it('shows the hint when one is available', () => {
    expect(keyFieldPlaceholder({ key_set: true, key_hint: '7f3a', log_path: '/l' }))
      .toBe('Key set — ending 7f3a')
  })

  it('still reports a stored key when the hint was withheld', () => {
    // The backend returns a null hint for a key under eight characters,
    // where "the last four" would disclose most of it.
    expect(keyFieldPlaceholder({ key_set: true, key_hint: null, log_path: '/l' }))
      .toBe('Key set')
  })
})

describe('probeMessage', () => {
  it('reports a verified key as verified', () => {
    expect(probeMessage('valid').tone).toBe('ok')
  })

  it('distinguishes rejected from unreachable', () => {
    // The whole point: a key we could not check must never render the same
    // as a key Anthropic accepted, nor the same as one it refused.
    const rejected = probeMessage('rejected')
    const unreachable = probeMessage('unreachable')

    expect(rejected.tone).toBe('error')
    expect(unreachable.tone).toBe('warn')
    expect(rejected.text).not.toBe(unreachable.text)
  })

  it('says the key was saved even when it could not be checked', () => {
    expect(probeMessage('unreachable').text).toMatch(/saved/i)
  })

  it('reports a cleared key', () => {
    expect(probeMessage('cleared').tone).toBe('ok')
    expect(probeMessage('cleared').text).toMatch(/removed/i)
  })

  it('reports a missing SDK distinctly', () => {
    expect(probeMessage('sdk_not_installed').tone).toBe('error')
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
npx vitest run src/api/localSettings.test.ts > /tmp/t5.log 2>&1; echo "EXIT=$?"
```

Expected: non-zero, `Failed to resolve import "./localSettings"`.

- [ ] **Step 3: Write the client**

Create `pypsa-gui/frontend/src/api/localSettings.ts`:

```ts
/**
 * Desktop-only settings: the Anthropic API key and the application log.
 *
 * Every route here 404s on a web deployment (the backend gates them with
 * `reject_unless_local_mode`). `fetchLocalSettings` maps that 404 to `null`
 * rather than an error, which is how the pane and its nav entry know to hide
 * themselves — the same shape `listUnclaimed` uses at projects.ts:111.
 */
import axios from 'axios'
import { client } from './client'

export type ProbeStatus =
  | 'valid'
  | 'rejected'
  | 'unreachable'
  | 'sdk_not_installed'
  | 'cleared'

export interface LocalSettingsState {
  key_set: boolean
  /** Last four characters, or null — including when the key is too short to hint safely. */
  key_hint: string | null
  log_path: string
}

export interface PutKeyResponse extends LocalSettingsState {
  status: ProbeStatus
  detail: string
}

export interface RevealResponse {
  revealed: boolean
  detail?: string
  log_path: string
}

/** `null` means "this build is not the desktop app" — not an error. */
export async function fetchLocalSettings(): Promise<LocalSettingsState | null> {
  try {
    const { data } = await client.get<LocalSettingsState>('/local-settings', {
      skipErrorToast: true,
    })
    return data
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 404) return null
    throw error
  }
}

export async function putApiKey(apiKey: string): Promise<PutKeyResponse> {
  const { data } = await client.put<PutKeyResponse>(
    '/local-settings/anthropic-key',
    { api_key: apiKey },
  )
  return data
}

export async function revealLog(): Promise<RevealResponse> {
  const { data } = await client.post<RevealResponse>(
    '/local-settings/reveal-log',
    {},
    { skipErrorToast: true },
  )
  return data
}

export function keyFieldPlaceholder(state: LocalSettingsState | null): string {
  if (!state?.key_set) return 'sk-ant-…'
  return state.key_hint ? `Key set — ending ${state.key_hint}` : 'Key set'
}

export interface ProbeMessage {
  tone: 'ok' | 'warn' | 'error'
  text: string
}

/**
 * One message per status, and never a shared one.
 *
 * `unreachable` is a WARNING, not a success and not an error: the key is
 * stored, and whether it works is genuinely unknown. Collapsing it into either
 * neighbour is the same defect as reporting an unresolvable cost as zero.
 */
export function probeMessage(status: ProbeStatus): ProbeMessage {
  switch (status) {
    case 'valid':
      return { tone: 'ok', text: 'Key accepted — chat is enabled.' }
    case 'rejected':
      return {
        tone: 'error',
        text: 'Anthropic rejected this key. It was saved anyway; chat stays disabled.',
      }
    case 'unreachable':
      return {
        tone: 'warn',
        text: 'Saved, but Anthropic could not be reached — the key is unverified.',
      }
    case 'sdk_not_installed':
      return { tone: 'error', text: 'The anthropic package is missing from this build.' }
    case 'cleared':
      return { tone: 'ok', text: 'Key removed. Chat is now disabled.' }
  }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
npx vitest run src/api/localSettings.test.ts > /tmp/t5.log 2>&1; echo "EXIT=$?"
```

Expected: `EXIT=0`, 8 passed.

- [ ] **Step 5: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git branch --show-current
git add pypsa-gui/frontend/src/api/localSettings.ts pypsa-gui/frontend/src/api/localSettings.test.ts
git commit pypsa-gui/frontend/src/api/localSettings.ts pypsa-gui/frontend/src/api/localSettings.test.ts -m "feat(gui): typed client for the local settings routes

A 404 maps to null rather than an error - that is how the pane knows this
build is not the desktop app and hides itself.

probeMessage keeps 'unreachable' distinct from both neighbours: a key that
could not be checked is not a key that works."
```

---

### Task 6: The Settings pane

**Files:**
- Create: `pypsa-gui/frontend/src/hooks/useLocalSettings.ts`
- Create: `pypsa-gui/frontend/src/pages/LocalSettings.tsx`
- Modify: `pypsa-gui/frontend/src/store/uiStore.ts:30`
- Modify: `pypsa-gui/frontend/src/App.tsx` (`PANEL_META` ~`:97`, `fullPageContent` ~`:121`)
- Modify: `pypsa-gui/frontend/src/layout/Sidebar.tsx:1282`
- Modify: `pypsa-gui/frontend/src/components/CommandPalette.tsx:346`

**Interfaces:**
- Consumes: everything Task 5 produced.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Widen the `SlidePanel` union**

In `pypsa-gui/frontend/src/store/uiStore.ts:30`, append `| 'settings'`:

```ts
export type SlidePanel = 'timeseries' | 'simparams' | 'horizon' | 'results' | 'snapshots' | 'issues' | 'overview' | 'scenarios' | 'compare' | 'capacityBounds' | 'solveQueue' | 'chat' | 'workspace' | 'settings'
```

- [ ] **Step 2: Verify the type error appears**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
npx tsc --noEmit > /tmp/t6.log 2>&1; echo "EXIT=$?"; head -20 /tmp/t6.log
```

Expected: non-zero, `Property 'settings' is missing in type … but required in type 'Record<SlidePanel, …>'` pointing at `PANEL_META`. This is the type system enforcing the wiring — confirm you see it before continuing.

- [ ] **Step 3: Write the shared availability hook**

Create `pypsa-gui/frontend/src/hooks/useLocalSettings.ts`, modelled on
`src/hooks/useSolveQueue.ts:15-25`:

```ts
/**
 * One fetch of /api/local-settings, shared by the pane and the nav row.
 *
 * `data === null` means the routes 404 — this build is not the desktop app —
 * and BOTH consumers hide themselves on it. A nav entry that opens an empty
 * pane is worse than no nav entry.
 *
 * `staleTime: Infinity`: neither the key hint nor the log path changes except
 * through this pane, which invalidates the key explicitly after a write.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchLocalSettings, type LocalSettingsState } from '../api/localSettings'

export const LOCAL_SETTINGS_KEY = ['localSettings'] as const

export function useLocalSettings() {
  return useQuery<LocalSettingsState | null>({
    queryKey: LOCAL_SETTINGS_KEY,
    queryFn: fetchLocalSettings,
    staleTime: Infinity,
    retry: false,
  })
}

/** True only once we know the routes exist. Undefined-safe while loading. */
export function useLocalSettingsAvailable(): boolean {
  const { data } = useLocalSettings()
  return data != null
}

export function useInvalidateLocalSettings() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: LOCAL_SETTINGS_KEY })
}
```

(`@tanstack/react-query` and the `['name'] as const` key shape are both taken
verbatim from `useSolveQueue.ts:1-3`.)

- [ ] **Step 4: Write the pane**

Create `pypsa-gui/frontend/src/pages/LocalSettings.tsx`:

```tsx
/**
 * Desktop-only Settings pane: the Anthropic API key and the application log.
 *
 * Renders nothing at all when `fetchLocalSettings` returns null — the routes
 * 404 on a web deployment, and one build serves both.
 */
import { useState } from 'react'
import toast from 'react-hot-toast'
import { confirmToast } from '../utils/toasts'
import { useInvalidateLocalSettings, useLocalSettings } from '../hooks/useLocalSettings'
import {
  keyFieldPlaceholder,
  probeMessage,
  putApiKey,
  revealLog,
  type ProbeMessage,
} from '../api/localSettings'

// Tokens defined in src/index.css:71-73 (--color-success / --color-warn /
// --color-danger). There is no `text-ok`.
const TONE_CLASS: Record<ProbeMessage['tone'], string> = {
  ok: 'text-success',
  warn: 'text-warn',
  error: 'text-danger',
}

export default function LocalSettings() {
  const { data: state, isLoading } = useLocalSettings()
  const invalidate = useInvalidateLocalSettings()
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<ProbeMessage | null>(null)

  if (isLoading) return <div className="p-4 text-muted">Loading…</div>
  // null means the routes 404: this build is not the desktop app.
  if (state == null) return null

  const save = async (value: string) => {
    setBusy(true)
    try {
      const result = await putApiKey(value)
      setMessage(probeMessage(result.status))
      setDraft('')
      await invalidate()
    } finally {
      setBusy(false)
    }
  }

  const clear = () => {
    // `confirmToast`, NOT window.confirm. src/utils/toasts.tsx:4 records why:
    // native dialogs block the main thread, cannot be styled, and are
    // "bypassed in CI / headless setups, silently auto-cancelling" — which is
    // exactly what a WKWebView with no JS-dialog delegate would do, turning
    // Clear into a button that does nothing.
    confirmToast(
      'Remove the stored Anthropic API key? Chat will be disabled.',
      () => save(''),
      { confirmLabel: 'Remove', danger: true },
    )
  }

  const reveal = async () => {
    const result = await revealLog()
    if (!result.revealed) {
      toast.error(`Could not open the file manager. The log is at ${result.log_path}`)
    }
  }

  const copyPath = async () => {
    if (!state) return
    // navigator.clipboard is undefined outside a secure context. The desktop
    // app serves from 127.0.0.1 (which qualifies), but a plain-http web
    // deployment does not — and a thrown TypeError here would surface as a
    // dead button rather than a message.
    try {
      await navigator.clipboard.writeText(state.log_path)
      toast.success('Log path copied')
    } catch {
      toast.error('Could not copy — select the path above instead.')
    }
  }

  return (
    <div className="p-4 space-y-6 overflow-y-auto">
      <section className="space-y-2">
        <h3 className="text-sm font-semibold">Anthropic API key</h3>
        <p className="text-xs text-muted">
          Needed for the chat assistant. Stored on this machine only, in your
          application data folder — never in a project file.
        </p>
        <div className="flex gap-2">
          <input
            type="password"
            className="flex-1 rounded border border-border bg-surface px-2 py-1 text-sm"
            placeholder={keyFieldPlaceholder(state)}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            autoComplete="off"
          />
          <button
            className="rounded border border-border px-3 py-1 text-sm disabled:opacity-50"
            disabled={busy || draft.trim() === ''}
            onClick={() => save(draft)}
          >
            Save
          </button>
          {state.key_set && (
            <button
              className="rounded border border-border px-3 py-1 text-sm disabled:opacity-50"
              disabled={busy}
              onClick={clear}
            >
              Clear
            </button>
          )}
        </div>
        {message && (
          <p className={`text-xs ${TONE_CLASS[message.tone]}`}>{message.text}</p>
        )}
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold">Diagnostics</h3>
        <p className="text-xs text-muted">
          Errors the app cannot show you land here. Include this file when
          reporting a problem.
        </p>
        <code className="block break-all rounded bg-surface px-2 py-1 text-xs">
          {state.log_path}
        </code>
        <div className="flex gap-2">
          <button className="rounded border border-border px-3 py-1 text-sm" onClick={reveal}>
            Reveal in file manager
          </button>
          <button className="rounded border border-border px-3 py-1 text-sm" onClick={copyPath}>
            Copy path
          </button>
        </div>
      </section>
    </div>
  )
}
```

The tone classes are verified against `src/index.css:71-73`. The container
classes (`border-border`, `bg-surface`, `text-muted`, `text-text`) all appear in
existing panels; if any renders wrong, copy the equivalent from
`SolverSettings.tsx` rather than inventing a new token.

- [ ] **Step 5: Wire it into App.tsx**

Add the import beside the other page imports:

```tsx
import LocalSettings from './pages/LocalSettings'
```

Add to `PANEL_META` (after the `workspace` entry):

```tsx
  settings:   { eyebrow: 'APPLICATION', title: 'Settings' },
```

Add to `fullPageContent`'s switch:

```tsx
    case 'settings':   return <LocalSettings />
```

Do **not** add `'settings'` to `FULL_SCREEN_TABS` (`App.tsx:118`) — it opens half-width beside the canvas, like Solver Settings.

- [ ] **Step 6: Add the nav row, hidden in web mode**

In `pypsa-gui/frontend/src/layout/Sidebar.tsx`:

1. Add `SlidersHorizontal` to the existing `lucide-react` import (`Settings2` is
   already taken by the Solver Settings row).
2. Add `import { useLocalSettingsAvailable } from '../hooks/useLocalSettings'`.
3. In the same component that renders the Solver Settings row, beside the
   existing `useSolveQueue()` call:

```tsx
  // The Settings pane is desktop-only; its routes 404 on a web deployment.
  // The row hides with it — a nav entry that opens an empty panel is worse
  // than no nav entry. Shares one react-query fetch with the pane.
  const settingsAvailable = useLocalSettingsAvailable()
```

4. Add the row after the Solver Settings row at `:1282`:

```tsx
      {settingsAvailable && (
        <SItem icon={<SlidersHorizontal size={15} />} label="Settings"
          title="Store your Anthropic API key and find the application log."
          active={activeSlidePanel === 'settings'}
          onClick={() => { setSlidePanel(activeSlidePanel === 'settings' ? null : 'settings'); onCloseModal?.() }}
        />
      )}
```

- [ ] **Step 7: Add the command-palette entry**

In `pypsa-gui/frontend/src/components/CommandPalette.tsx`, immediately after the
`act-solver` entry (which ends at `:346`), add — the shape is copied verbatim
from that entry, which reads:

```tsx
        {
          id: 'act-solver',
          kind: 'action',
          title: 'Open solver settings',
          icon: <Settings2 size={14} />,
          run: () => setSlidePanel('simparams'),
        },
```

so the new one is:

```tsx
        {
          id: 'act-settings',
          kind: 'action',
          title: 'Open settings',
          icon: <SlidersHorizontal size={14} />,
          run: () => setSlidePanel('settings'),
        },
```

Add `SlidersHorizontal` to this file's `lucide-react` import as well.

- [ ] **Step 8: Verify types and tests**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
npx tsc --noEmit > /tmp/t6.log 2>&1; echo "TSC=$?"
npx vitest run > /tmp/t6b.log 2>&1; echo "VITEST=$?"
```

Expected: `TSC=0` and `VITEST=0`.

- [ ] **Step 9: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git branch --show-current
git add pypsa-gui/frontend/src/pages/LocalSettings.tsx pypsa-gui/frontend/src/hooks/useLocalSettings.ts
git commit pypsa-gui/frontend/src/pages/LocalSettings.tsx pypsa-gui/frontend/src/hooks/useLocalSettings.ts pypsa-gui/frontend/src/store/uiStore.ts pypsa-gui/frontend/src/App.tsx pypsa-gui/frontend/src/layout/Sidebar.tsx pypsa-gui/frontend/src/components/CommandPalette.tsx -m "feat(gui): add the desktop Settings pane

Both the pane and its nav row hide when the routes 404, so one build still
serves the desktop app and the web deployment. They share one react-query
fetch: a nav entry that opens an empty panel is worse than no nav entry.

Clear confirms via confirmToast (never window.confirm - src/utils/toasts.tsx
records that native dialogs silently auto-cancel in headless webviews), and
Save is disabled while the field is empty so it cannot wipe a stored key."
```

---

## Final verification

After Task 6, before handing back:

- [ ] **Full backend suite**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests > /tmp/full.log 2>&1; echo "PYTEST_EXIT=$?"; tail -3 /tmp/full.log
```

Expected: `PYTEST_EXIT=0`. The baseline before this plan was **1966 passed, 1 skipped, 0 failed**; the count should rise by roughly 30 and nothing should newly fail.

- [ ] **Full frontend suite**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
npx vitest run > /tmp/fullfe.log 2>&1; echo "VITEST_EXIT=$?"; tail -5 /tmp/fullfe.log
```

Expected: `VITEST_EXIT=0`.

- [ ] **Rebuild the DMG**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui"
./build-macos.sh > /tmp/build.log 2>&1; echo "BUILD_EXIT=$?"; tail -12 /tmp/build.log
```

Expected: `BUILD_EXIT=0`, a clean secret-scan line, and a DMG timestamp **later than the last commit**. A green suite says nothing about the artifact the user actually runs — that gap is what produced the original defect.

---

## Notes for the executor

- **Do not touch `services/chat_service.py`, `routers/chat.py`, or `main.py`'s
  `_chatbot_startup_check`.** The whole design is that setting the environment
  variable is sufficient. If it turns out not to be, stop and report rather
  than editing chat.
- **Do not import `chat_service._redact_for_log`.** An earlier draft did. The
  probe's detail strings are fixed and only exception CLASS NAMES are logged,
  so there is no formatting step for a key to survive — which is stronger than
  scrubbing, and needs nothing private from another module.
- **`explorer` exits non-zero on success.** `check=False` in the reveal call is
  load-bearing; do not "tidy" it to `check=True`.

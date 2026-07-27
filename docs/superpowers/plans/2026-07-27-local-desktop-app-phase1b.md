# Local Desktop App — Phase 1b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the desktop app a storage layout a human can navigate in Finder/Explorer, and import the projects that already exist on this machine into it.

**Architecture:** Two workstreams from the spec. **E** replaces UUID-named project directories with sanitised human-readable ones, makes `Project.storage_path` relative so the whole tree can move, and closes the non-atomic write paths. **F** is a one-shot, resumable importer for the legacy flat tree, run at first launch. E lands first so F imports straight into the final layout instead of migrating twice.

**Tech Stack:** FastAPI, SQLAlchemy 2.x + Alembic, SQLite (WAL), pytest. No new dependencies.

**Builds on:** phase 1a (`docs/superpowers/plans/2026-07-27-local-desktop-app-phase1a.md`), tasks 0–15, all landed on `feature/local-app-impl`.

---

## Why this phase is not optional

Phase 1a moved both storage roots out of the source tree. Verified on this machine, at `09bd7020` vs `39b3503e`:

| setting | before | after |
|---|---|---|
| `projects_root` | `_BACKEND / "projects"` | `~/Documents/PyPSA GUI/Projects` |
| `PROJECTS_DIR` (flat) | hardcoded `_BACKEND / "projects"` | app-data `flat_projects_root` |

`pypsa-gui/backend/projects/` currently holds 14 items: 11 flat project directories, one org-scoped tree, and two bundle files. **Nothing was deleted**, and the single DB-tracked project still resolves because its `storage_path` was persisted absolute:

```
3_nodes_system -> /Users/…/pypsa-gui/backend/projects/860edcb4-…/e8645aba-…
```

But that absolute path is pinned inside the repo and breaks the moment the app is packaged or the checkout moves — which is E2 — and the 11 flat projects are no longer listed at all — which is F.

Interim workaround until F lands: point `FLAT_PROJECTS_ROOT` and `PROJECTS_ROOT` at `pypsa-gui/backend/projects`.

---

## Global Constraints

Carried forward from phase 1a. Every one was learned by breaking something.

- **Both modes must keep working.** Every change is conditional on local mode or is mode-neutral. The web deployment is not being retired.
- **Never reload or re-import modules in tests.** `del sys.modules["db.session"]` does not work for `from db import session`, and partial reloads split-brain `security`/`settings` for the rest of the session.
- **Serialize strictly: edit → gate → commit → next task.** Do not write or edit *any* file under `pypsa-gui/` while the current task's suite is running. Editing a source file mid-run invalidates the gate; creating the *next* task's test file mid-run is worse, because it imports a symbol that does not exist yet and pytest aborts collection with `exit=2`, yielding **no signal at all**. Draft ahead in a scratch directory outside `pypsa-gui/` and move the file in when its task starts.
- **Every local-mode fixture seeds AND removes the local identity.** conftest's shared database persists users/orgs across the whole session by design.
- **Set the env FIRST, then `cache_clear()`.** `get_settings()` and `security.allowed_origins()` are `lru_cache`d; clearing first repopulates from the old value on the next read.
- **`DATABASE_URL` is mandatory for any manual run.** `backend/.env:17` carries a CWD-relative `sqlite+pysqlite:///./auth_dev.db`, dotenv outranks field defaults, and running from `pypsa-gui/backend` therefore opens the developer's dev database *in the source tree*. This happened during phase 1a execution.
- **Never hardcode an interpreter path** (CLAUDE.md). `pixi run …`, never `.pixi/envs/default/bin/python`.
- **`PROJECTS_DIR` stays a settable module attribute.** `conftest.py:430` monkeypatches it and 9 test files depend on that.
- **Touching real user data requires a dry run first.** F operates on directories that are not reproducible. Every destructive step is copy-then-verify, never move.

---

## Verified constraints

Each checked against the working tree at `39b3503e`. Line numbers are from that commit.

| # | Fact | Why it matters |
|---|---|---|
| 1 | `Project.storage_path: Mapped[str] = mapped_column(Text)`, `db/models.py` | No length cap, so a long relative path is fine. Not nullable — the rebase migration cannot leave it empty. |
| 2 | `services/storage_paths.py::storage_path_for(org_id, project_id)` is the **only** place a project path is constructed | E1 and E2 both change exactly this function. |
| 3 | `project_registry.project_dir(project)` (`:149`) is the intended resolver, but **five sites bypass it**: `routers/projects.py:577, 1591, 2139, 2219, 2229` | Under E2 those five read a relative path as if absolute and resolve against the process CWD. They must be converted *before* the storage format changes. |
| 4 | `project_registry.bind_context` (`:146`) sets `ctx.storage_dir = str(project.storage_path)` | Same bug class as #3. `chat_service.get_persist_path` consumes this. |
| 5 | `project_registry.rename_project` (`:225`) documents "storage_path is UUID-keyed, so [it does not move]" | E1 invalidates that comment. Rename must now move the directory, or the name on disk drifts from the name in the DB. |
| 6 | `_atomic_write_with` is defined in `routers/projects.py:234` and imported by `routers/snapshots.py:47` | A router importing from another router. E5 moves it to a service; both importers must be updated in the same commit. |
| 7 | `routers/projects.py:547` and `:1879` already treat a stale `<name>.tmp` sibling as a crash-recovery signal | E4's `.tmp` sweep must not delete files these two still report on. Sweep only on explicit invocation, never implicitly at startup. |
| 8 | Legacy tree holds 11 flat dirs, 1 org-scoped tree, `3_nodes_system.pypsaproj.zip`, `new_project_test.pypsaproj` | F must skip non-directories rather than choke on them. |
| 9 | `metadata.json`'s `parent_project` is a **name**, not a UUID — and can dangle. `4_nodes_N-1` names `test_project_4_nodes2`, which is not present | F must resolve parents by name in a second pass and tolerate misses. |
| 10 | 5 legacy projects carry `chat.jsonl`; 1 carries `snapshots/` | Confirms spec F3: the export bundle is the wrong channel, it drops both. |
| 11 | Alembic head is `0002_session_active_project` | E2's rebase migration is `0003`. |
| 12 | `UniqueConstraint("org_id", "name")` on `projects` | F cannot import two legacy dirs with the same name into one org; it must report the collision, not crash on IntegrityError. |
| 13 | `Project.name` is `String(64)` | Legacy directory names longer than 64 chars must be truncated on import, and the truncation must not create a collision. |

---

## File structure

| File | Responsibility |
|---|---|
| `backend/services/safe_names.py` | **new** — pure functions: sanitise a project name into a portable directory name, detect collisions. No I/O. |
| `backend/services/atomic_io.py` | **new** — `atomic_write_with` / `atomic_write_text` moved out of `routers/projects.py`. No FastAPI import. |
| `backend/services/storage_paths.py` | modified — builds human-readable relative paths. |
| `backend/services/project_registry.py` | modified — `project_dir()` becomes the single resolver; `rename_project` moves the directory. |
| `backend/services/legacy_import.py` | **new** — inventory + import of the legacy flat tree. Pure of FastAPI; callable from a CLI and from first-run. |
| `backend/tools/import_legacy.py` | **new** — CLI wrapper with `--dry-run` (default) and `--apply`. |
| `backend/alembic/versions/0003_relative_storage_path.py` | **new** — rebase absolute `storage_path` values to relative. |
| `backend/routers/projects.py` | modified — 5 direct `storage_path` reads routed through the resolver; 2 non-atomic writes fixed. |
| `backend/routers/snapshots.py` | modified — import `atomic_write_with` from its new home. |

---

## Task 0: Confirm the tree is safe to build on

- [ ] **Step 1: Concurrency check**

Tasks 2, 5 and 8 touch `routers/projects.py`, which the cloud/SaaS workstream also edits.

```bash
cd pypsa-eur
git branch --show-current          # expect feature/local-app-impl
git log --oneline -1               # expect 39b3503e or later
git status --porcelain             # expect empty
git log --oneline -1 master        # note the SHA; if it moved, re-read §"Verified constraints"
ls -lT pypsa-gui/backend/routers/projects.py pypsa-gui/backend/services/project_registry.py
```

If either file was written in the last hour by another session, stop and reconcile first.

- [ ] **Step 2: Record the baseline**

```bash
pixi run gui-tests -q 2>&1 | tail -3
pixi run npm --prefix pypsa-gui/frontend test 2>&1 | tail -4
```

Write both numbers down. Backend at `39b3503e`: 1206 collected, exit 0. Frontend: 23 files, 147 tests.

- [ ] **Step 3: Snapshot the real legacy tree**

This phase touches data that cannot be regenerated. Before anything else:

```bash
cd pypsa-gui/backend
tar czf ~/pypsa-legacy-projects-backup-$(date +%Y%m%d).tar.gz projects/
ls -lh ~/pypsa-legacy-projects-backup-*.tar.gz
```

Do not proceed until that archive exists and is non-empty.

---

## Task 1: Portable directory names

**Files:** Create `backend/services/safe_names.py`, `backend/tests/test_safe_names.py`

**Interfaces:** Produces `safe_dir_name(name: str) -> str` and `unique_dir_name(name: str, taken: Container[str]) -> str`.

**Context:** The app targets Windows x64 and macOS arm64 (spec D1). Project names are free text and reach the filesystem for the first time in this phase. Windows rejects `<>:"/\|?*`, reserves `CON`/`PRN`/`AUX`/`NUL`/`COM1-9`/`LPT1-9` *including with an extension*, silently strips trailing dots and spaces, and its default path limit is 260 characters. macOS is case-insensitive by default, so `Belgium` and `belgium` collide there but not on Linux.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_safe_names.py
"""
Portable project directory names (spec E1).

The app ships to Windows and macOS (spec D1), and this is the first phase where
a user-chosen project name reaches the filesystem. Every rule below is a real
platform constraint, not defensive programming:

  <>:"/\|?*        rejected outright by Windows
  CON, PRN, AUX,   reserved DEVICE names on Windows, and reserved WITH an
  NUL, COM1-9,     extension too — `CON.txt` is just as invalid as `CON`
  LPT1-9
  trailing . and   silently stripped by Windows, so "foo." and "foo" become the
  space            same directory and one silently overwrites the other
  260 chars        default MAX_PATH; the project dir is only part of the budget
  case             macOS is case-insensitive by default, Linux is not
"""
import pytest

from services.safe_names import safe_dir_name, unique_dir_name


@pytest.mark.parametrize("raw,expected", [
    ("Belgium Grid", "Belgium Grid"),
    ("4_nodes_N-1", "4_nodes_N-1"),
    ("heat with time-series", "heat with time-series"),
])
def test_ordinary_names_are_left_alone(raw, expected):
    """Readability is the whole point — do not mangle names that are already fine."""
    assert safe_dir_name(raw) == expected


@pytest.mark.parametrize("raw", ['a<b', 'a>b', 'a:b', 'a"b', "a/b", "a\\b", "a|b", "a?b", "a*b"])
def test_windows_forbidden_characters_are_replaced(raw):
    out = safe_dir_name(raw)
    assert not any(c in out for c in '<>:"/\\|?*')
    assert out  # never empty


@pytest.mark.parametrize("raw", ["CON", "con", "PRN", "AUX", "NUL", "COM1", "LPT9", "CON.nc"])
def test_reserved_device_names_are_escaped(raw):
    """Reserved with an extension too, which is the part people forget."""
    assert safe_dir_name(raw).upper().split(".")[0] not in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }


@pytest.mark.parametrize("raw", ["foo.", "foo ", "foo. . ", "foo..."])
def test_trailing_dots_and_spaces_are_stripped(raw):
    out = safe_dir_name(raw)
    assert not out.endswith((".", " "))


def test_empty_and_whitespace_get_a_fallback():
    """A name of only forbidden characters must not yield an empty path segment."""
    for raw in ("", "   ", "///", "..."):
        assert safe_dir_name(raw)


def test_long_names_are_truncated_but_stay_unique_looking():
    out = safe_dir_name("x" * 400)
    assert 0 < len(out) <= 96


def test_unicode_is_preserved():
    """Users name projects in their own language; ASCII-folding is not required."""
    assert safe_dir_name("Netz Österreich") == "Netz Österreich"


def test_unique_dir_name_appends_a_suffix_on_collision():
    assert unique_dir_name("Belgium Grid", taken=set()) == "Belgium Grid"
    assert unique_dir_name("Belgium Grid", taken={"Belgium Grid"}) == "Belgium Grid (2)"
    assert unique_dir_name(
        "Belgium Grid", taken={"Belgium Grid", "Belgium Grid (2)"}
    ) == "Belgium Grid (3)"


def test_unique_dir_name_is_case_insensitive():
    """macOS is case-insensitive by default — `belgium` would collide with `Belgium`."""
    assert unique_dir_name("Belgium", taken={"BELGIUM"}) == "Belgium (2)"


def test_unique_dir_name_keeps_the_result_within_the_length_cap():
    out = unique_dir_name("y" * 96, taken={"y" * 96})
    assert len(out) <= 96
```

- [ ] **Step 2: Run it and watch it fail**

```bash
pixi run python -m pytest pypsa-gui/backend/tests/test_safe_names.py -p no:warnings -q
```

Expected: `ModuleNotFoundError: No module named 'services.safe_names'` — a collection error, so run this file alone, never as part of the suite.

- [ ] **Step 3: Implement**

```python
# pypsa-gui/backend/services/safe_names.py
"""
Turn a user-chosen project name into a portable directory name (spec E1).

Pure functions, no I/O, so the platform rules can be tested without a
filesystem. See tests/test_safe_names.py for why each rule exists.

Deliberately NOT ASCII-folded: users name projects in their own language and
both target filesystems are UTF-8. The point of this phase is that a human can
find their project in Finder or Explorer.
"""
from __future__ import annotations

import re
from typing import Container

# Windows rejects these outright; macOS only rejects "/" but portability wins.
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Reserved DEVICE names, and reserved with an extension too: `CON.nc` is as
# invalid as `CON`.
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Well under Windows' 260-char MAX_PATH: the project directory is only one
# segment of a budget that also carries the projects root, an org segment and
# filenames like `results_state.pkl`.
_MAX_LEN = 96

_FALLBACK = "project"


def safe_dir_name(name: str) -> str:
    """A single path segment that is legal on Windows, macOS and Linux."""
    out = _FORBIDDEN.sub("_", name).strip()

    # Windows silently strips trailing dots and spaces, which would let "foo."
    # and "foo" resolve to one directory and overwrite each other.
    out = out.rstrip(". ")

    if out.split(".")[0].upper() in _RESERVED:
        out = f"_{out}"

    if len(out) > _MAX_LEN:
        out = out[:_MAX_LEN].rstrip(". ")

    return out or _FALLBACK


def unique_dir_name(name: str, taken: Container[str]) -> str:
    """
    `safe_dir_name` plus a numeric suffix when the result is already in use.

    Case-insensitive, because macOS is: `Belgium` and `BELGIUM` are the same
    directory there and different ones on Linux, and the app must behave the
    same on both.
    """
    base = safe_dir_name(name)
    lowered = {str(t).lower() for t in taken} if not isinstance(taken, set) else {
        str(t).lower() for t in taken
    }
    if base.lower() not in lowered:
        return base

    for n in range(2, 10_000):
        suffix = f" ({n})"
        trimmed = base[: _MAX_LEN - len(suffix)].rstrip(". ") or _FALLBACK
        candidate = f"{trimmed}{suffix}"
        if candidate.lower() not in lowered:
            return candidate

    raise ValueError(f"cannot find a free directory name for {name!r}")
```

- [ ] **Step 4: Run the tests**

```bash
pixi run python -m pytest pypsa-gui/backend/tests/test_safe_names.py -p no:warnings -q
```

Expected: 23 passed.

- [ ] **Step 5: Gate and commit**

```bash
pixi run gui-tests -q 2>&1 | tail -3     # must match the Task 0 baseline + 23
git add pypsa-gui/backend/services/safe_names.py pypsa-gui/backend/tests/test_safe_names.py
git commit -m "feat(gui): portable project directory names"
```

---

## Task 2: One resolver for project paths

**Files:** Modify `backend/services/project_registry.py:146,149`, `backend/routers/projects.py:577,1591,2139,2219,2229`; create `backend/tests/test_project_dir_resolver.py`

**Interfaces:** Consumes nothing new. Produces the invariant that `project_registry.project_dir(project)` is the **only** way to turn a `Project` row into a path.

**Context:** Five call sites currently do `pathlib.Path(project.storage_path)` directly. While `storage_path` is absolute they are correct by accident. Task 3 makes it relative, at which point each of them resolves against the process CWD instead — which for a frozen `.app` is `/`. **This task must land before Task 3**, and it is deliberately behaviour-preserving so the two changes can be reviewed apart.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_project_dir_resolver.py
"""
`project_registry.project_dir` is the single resolver (spec E2).

Five sites in routers/projects.py read `Path(project.storage_path)` directly.
That is correct only while the stored value is absolute; the moment Task 3
makes it relative they resolve against the process CWD — `/` for a frozen app.

This test pins the invariant by source inspection rather than behaviour,
because the failure it guards against is silent: a direct read still *works*
today, and still "works" tomorrow by pointing somewhere wrong.
"""
import pathlib
import re

_ROUTERS = pathlib.Path(__file__).resolve().parent.parent / "routers"
_SERVICES = pathlib.Path(__file__).resolve().parent.parent / "services"

# Path(<anything>.storage_path)
_DIRECT_READ = re.compile(r"Path\(\s*\w+(?:\.\w+)*\.storage_path\s*\)")


def test_no_router_resolves_storage_path_directly():
    offenders = []
    for py in _ROUTERS.rglob("*.py"):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if _DIRECT_READ.search(line):
                offenders.append(f"{py.name}:{i}: {line.strip()}")
    assert not offenders, (
        "resolve via project_registry.project_dir() instead:\n  " + "\n  ".join(offenders)
    )


def test_the_resolver_is_the_only_place_that_joins_the_root():
    """
    Exactly one implementation, in project_registry. storage_paths BUILDS paths;
    project_registry RESOLVES them. Two resolvers would drift.
    """
    registry = (_SERVICES / "project_registry.py").read_text(encoding="utf-8")
    assert "def project_dir(" in registry
```

Plus a behavioural test that both absolute and relative values resolve to the same place:

```python
def test_resolver_handles_absolute_and_relative_alike(tmp_path, monkeypatch):
    """
    Rows written before 0003 hold absolute paths and rows written after hold
    relative ones. Both must resolve, because the migration is not the only way
    a row gets its value — a restored backup can carry either.
    """
    import settings as settings_module
    from services import project_registry

    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path))
    settings_module.get_settings.cache_clear()
    try:
        class _Row:
            storage_path = "org/proj"
        assert project_registry.project_dir(_Row()) == tmp_path / "org" / "proj"

        class _AbsRow:
            storage_path = str(tmp_path / "org" / "proj")
        assert project_registry.project_dir(_AbsRow()) == tmp_path / "org" / "proj"
    finally:
        settings_module.get_settings.cache_clear()
```

- [ ] **Step 2: Run it and watch it fail**

```bash
pixi run python -m pytest pypsa-gui/backend/tests/test_project_dir_resolver.py -p no:warnings -q
```

Expected: `test_no_router_resolves_storage_path_directly` fails listing all five sites; the relative half of the resolver test fails too.

- [ ] **Step 3: Make the resolver total**

In `services/project_registry.py`, replace `project_dir` (`:149`):

```python
def project_dir(project: Project) -> Path:
    """
    The one place a Project row becomes a filesystem path.

    Accepts both formats. Rows created before migration 0003 hold an absolute
    path; rows created after hold one relative to `projects_root`. A restored
    backup can carry either, so this stays permanent rather than being a
    migration-window shim.
    """
    path = Path(project.storage_path)
    if path.is_absolute():
        return path
    return Path(get_settings().projects_root) / path
```

and `bind_context` (`:146`) must use it rather than the raw column:

```python
    ctx.storage_dir = str(project_dir(project))
```

- [ ] **Step 4: Convert the five router sites**

Each becomes `project_registry.project_dir(<row>)`. Verified locations at `39b3503e`:

| Site | Current | Becomes |
|---|---|---|
| `routers/projects.py:577` | `d = pathlib.Path(project.storage_path)` | `d = project_registry.project_dir(project)` |
| `:1591` | `src_dir = pathlib.Path(src_project.storage_path)` | `src_dir = project_registry.project_dir(src_project)` |
| `:2139` | `target_dir = pathlib.Path(target.storage_path)` | `target_dir = project_registry.project_dir(target)` |
| `:2219` | `dest = pathlib.Path(project.storage_path)` | `dest = project_registry.project_dir(project)` |
| `:2229` | `child_dir = pathlib.Path(child.storage_path)` | `child_dir = project_registry.project_dir(child)` |

Confirm the module is already imported in `routers/projects.py` before adding an import.

- [ ] **Step 5: Gate and commit**

```bash
pixi run python -m pytest pypsa-gui/backend/tests/test_project_dir_resolver.py -p no:warnings -q
pixi run gui-tests -q 2>&1 | tail -3
git add pypsa-gui/backend/services/project_registry.py pypsa-gui/backend/routers/projects.py \
        pypsa-gui/backend/tests/test_project_dir_resolver.py
git commit -m "refactor(gui): resolve every project path through one function"
```

This commit changes no behaviour — the suite must be green with zero test edits. If any test needed changing, a sixth direct read exists somewhere; find it.

---

## Task 3: Human-readable, relative storage paths

**Files:** Modify `backend/services/storage_paths.py`, `backend/services/project_registry.py:171,207,225`; create `backend/alembic/versions/0003_relative_storage_path.py`, `backend/tests/test_storage_layout.py`

**Interfaces:** Consumes `safe_names.unique_dir_name` (Task 1) and `project_registry.project_dir` (Task 2). Produces `storage_path_for(org_id, project_id, name, taken) -> Path` returning a **relative** path.

**Context:** Today `storage_path_for` returns `projects_root/<org_uuid>/<project_uuid>` — unnavigable in Finder. The UUID does not disappear: it moves to a **collision suffix of last resort**, so two projects named the same in one org still get distinct directories. `UniqueConstraint("org_id", "name")` means that should not happen through the API, but F imports directories that predate the constraint.

`rename_project:225` currently documents that storage does not move because it is UUID-keyed. That stops being true here.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_storage_layout.py
"""
Human-readable, relative project directories (spec E1, E2).

Relative because the whole tree has to be movable: a packaged app relocates the
projects root, and phase 1a already proved the failure mode — the one project
row on this machine still points at an absolute path inside the source
checkout.
"""
import uuid

import pytest

import settings as settings_module
from services.storage_paths import storage_path_for


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path))
    settings_module.get_settings.cache_clear()
    yield tmp_path
    settings_module.get_settings.cache_clear()


def test_path_is_relative(root):
    p = storage_path_for(uuid.uuid4(), uuid.uuid4(), "Belgium Grid", taken=set())
    assert not p.is_absolute(), p


def test_path_uses_the_readable_name(root):
    org, pid = uuid.uuid4(), uuid.uuid4()
    p = storage_path_for(org, pid, "Belgium Grid", taken=set())
    assert p.name == "Belgium Grid"
    assert p.parts[0] == str(org)


def test_forbidden_characters_do_not_reach_the_path(root):
    p = storage_path_for(uuid.uuid4(), uuid.uuid4(), 'bad/name:here', taken=set())
    assert not any(c in p.name for c in '<>:"/\\|?*')


def test_collisions_fall_back_to_a_suffix(root):
    org, a, b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    first = storage_path_for(org, a, "Same", taken=set())
    second = storage_path_for(org, b, "Same", taken={first.name})
    assert first.name != second.name


def test_the_org_segment_stays_a_uuid(root):
    """
    Orgs are not renameable by the user and the segment is never browsed to
    directly; keeping it opaque avoids a second sanitising surface.
    """
    org = uuid.uuid4()
    p = storage_path_for(org, uuid.uuid4(), "X", taken=set())
    assert p.parts[0] == str(org)
```

- [ ] **Step 2: Run it and watch it fail**

```bash
pixi run python -m pytest pypsa-gui/backend/tests/test_storage_layout.py -p no:warnings -q
```

Expected: `TypeError` — `storage_path_for` takes two arguments today.

- [ ] **Step 3: Implement**

```python
# pypsa-gui/backend/services/storage_paths.py
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Container

from services.safe_names import unique_dir_name


def storage_path_for(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    name: str,
    taken: Container[str],
) -> Path:
    """
    A project's directory, RELATIVE to `projects_root`.

    Relative so the tree can move: a packaged app relocates the root, and an
    absolute value pins a row to one machine. `project_registry.project_dir`
    joins the root back on, and accepts both formats for rows that predate
    migration 0003.

    `taken` is the set of sibling directory names already used in this org —
    the caller knows it, this module does not do I/O.

    The project UUID is no longer the directory name, but it is still the
    identity: `Project.id` is unchanged, and nothing keys off the path.
    """
    return Path(str(org_id)) / unique_dir_name(name, taken)
```

Update both callers in `project_registry.py` — `create_root:171` and `create_scenario:207` — to gather siblings first:

```python
def _taken_names(db: DBSession, org_id: uuid.UUID) -> set[str]:
    """
    Sibling directory names already in use in this org.

    Read from the DB, not the filesystem: a directory that exists without a row
    is an orphan, which `tools/reconcile_storage` reports rather than silently
    reserving a name for.
    """
    rows = db.scalars(select(Project.storage_path).where(Project.org_id == org_id)).all()
    return {Path(r).name for r in rows}
```

and pass `storage_path=str(storage_path_for(org_id, project_id, name, _taken_names(db, org_id)))`.

- [ ] **Step 4: Make rename move the directory**

`rename_project:225`'s comment is now wrong. Replace the function body's storage handling:

```python
def rename_project(db: DBSession, project: Project, new_name: str) -> Project:
    """
    Rename in the DB and move the directory to match.

    Storage used to be UUID-keyed, so renaming touched only the row. Now that
    the directory carries the display name, leaving it behind would make the
    name in Finder disagree with the name in the app — which is precisely the
    problem this layout exists to solve.

    The move happens BEFORE the commit: if it fails, the transaction rolls back
    and disk and database still agree. A rename that half-succeeds is worse
    than one that does not happen.
    """
    old_dir = project_dir(project)
    taken = _taken_names(db, project.org_id) - {old_dir.name}
    new_rel = storage_path_for(project.org_id, project.id, new_name, taken)
    new_dir = Path(get_settings().projects_root) / new_rel

    if old_dir.exists() and old_dir != new_dir:
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        old_dir.rename(new_dir)          # same filesystem by construction

    project.name = new_name
    project.storage_path = str(new_rel)
    project.updated_at = _now()
    db.commit()
    db.refresh(project)
    return project
```

Add a test for the move, and one asserting that a failed move leaves the row untouched.

- [ ] **Step 5: The rebase migration**

```python
# pypsa-gui/backend/alembic/versions/0003_relative_storage_path.py
"""
Rebase absolute storage_path values onto projects_root.

Revision ID: 0003_relative_storage_path
Revises: 0002_session_active_project

Rows written before this migration hold an absolute path. On this machine the
single existing row points inside the source checkout, which breaks as soon as
the app is packaged.

Rows whose path does NOT sit under the current root are left ALONE, not
rewritten to a guess: `project_dir` handles absolute values permanently, so an
untouched row keeps working, while a wrong relative value would silently point
at nothing.
"""
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0003_relative_storage_path"
down_revision = "0002_session_active_project"
branch_labels = None
depends_on = None


def upgrade() -> None:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from settings import get_settings

    root = Path(get_settings().projects_root).resolve()
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, storage_path FROM projects")).fetchall()
    for pid, raw in rows:
        p = Path(raw)
        if not p.is_absolute():
            continue
        try:
            rel = p.resolve().relative_to(root)
        except ValueError:
            continue        # outside the root — leave absolute, it still resolves
        conn.execute(
            sa.text("UPDATE projects SET storage_path = :rel WHERE id = :id"),
            {"rel": str(rel), "id": pid},
        )


def downgrade() -> None:
    """Re-absolutise, so a downgrade does not strand rows for older code."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from settings import get_settings

    root = Path(get_settings().projects_root).resolve()
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, storage_path FROM projects")).fetchall()
    for pid, raw in rows:
        if Path(raw).is_absolute():
            continue
        conn.execute(
            sa.text("UPDATE projects SET storage_path = :abs WHERE id = :id"),
            {"abs": str(root / raw), "id": pid},
        )
```

Test it against a SQLite database with one absolute row, one relative row and one row outside the root; assert each lands in the documented state and that `upgrade` is idempotent.

- [ ] **Step 6: Gate and commit**

```bash
pixi run python -m pytest pypsa-gui/backend/tests/test_storage_layout.py -p no:warnings -q
pixi run gui-tests -q 2>&1 | tail -3
git add pypsa-gui/backend/services/storage_paths.py pypsa-gui/backend/services/project_registry.py \
        pypsa-gui/backend/alembic/versions/0003_relative_storage_path.py \
        pypsa-gui/backend/tests/test_storage_layout.py
git commit -m "feat(gui): human-readable project directories, stored relative"
```

---

## Task 4: Atomic writes everywhere that matters

**Files:** Create `backend/services/atomic_io.py`, `backend/tests/test_atomic_io.py`; modify `backend/routers/projects.py:234-263`, `backend/routers/snapshots.py:47`

**Context:** `_atomic_write_with` lives in `routers/projects.py:234` and `routers/snapshots.py:47` imports it *from another router* — a dependency that only works because neither module has side effects at import. Spec E5 also calls for bundle-import and snapshot-create to stop being direct truncating writes: a crash mid-write currently leaves a half-written `network.nc` where a valid one used to be.

`routers/projects.py:547` and `:1879` report stale `.tmp` siblings as a crash signal, so the helper's `.tmp` naming is load-bearing and must not change.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_atomic_io.py
"""
Atomic writes (spec E5).

The `.tmp` suffix is load-bearing, not an implementation detail:
routers/projects.py:547 and :1879 both treat a stale `<name>.tmp` sibling as
evidence that a previous save was killed mid-write, and surface it as possible
corruption. Changing the naming silently disables that warning.
"""
import pytest

from services.atomic_io import atomic_write_text, atomic_write_with


def test_content_is_replaced_atomically(tmp_path):
    target = tmp_path / "network.nc"
    target.write_text("old")
    atomic_write_text(target, "new")
    assert target.read_text() == "new"


def test_a_failed_write_leaves_the_original_intact(tmp_path):
    """The whole point: a crash must not destroy the last good file."""
    target = tmp_path / "network.nc"
    target.write_text("good")

    def boom(p):
        p.write_text("half")
        raise RuntimeError("killed mid-write")

    with pytest.raises(RuntimeError):
        atomic_write_with(target, boom)
    assert target.read_text() == "good"


def test_the_tmp_sibling_is_named_for_the_crash_detector(tmp_path):
    target = tmp_path / "network.nc"

    def boom(p):
        p.write_text("half")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        atomic_write_with(target, boom)
    assert (tmp_path / "network.nc.tmp").exists(), "crash-recovery detector looks for this"
```

- [ ] **Step 2: Move, do not rewrite**

Cut `_atomic_write_with` and `_atomic_write_text` from `routers/projects.py:234-263` into `services/atomic_io.py` **verbatim**, dropping the leading underscore. Then in `routers/projects.py`:

```python
from services.atomic_io import atomic_write_text, atomic_write_with

# Aliases kept so the ~12 existing call sites in this module stay untouched in
# this commit. A rename would bury the behavioural change of Step 3 in noise.
_atomic_write_with = atomic_write_with
_atomic_write_text = atomic_write_text
```

and point `routers/snapshots.py:47` at `services.atomic_io`.

- [ ] **Step 3: Close the two non-atomic writes**

Find the bundle-import and snapshot-create writes and route both through `atomic_write_with`. Locate them with:

```bash
cd pypsa-gui/backend
grep -n "export_to_netcdf\|write_bytes\|shutil.copy" routers/io.py routers/projects.py routers/snapshots.py \
  | grep -v atomic_write
```

Every hit that writes a file *inside a project directory* is in scope. Add a test per site that a mid-write failure leaves the previous file intact.

- [ ] **Step 4: Gate and commit**

```bash
pixi run gui-tests -q 2>&1 | tail -3
git add pypsa-gui/backend/services/atomic_io.py pypsa-gui/backend/tests/test_atomic_io.py \
        pypsa-gui/backend/routers/projects.py pypsa-gui/backend/routers/snapshots.py
git commit -m "fix(gui): make every in-project write atomic"
```

---

## Task 5: Reconcile storage against the database

**Files:** Create `backend/tools/reconcile_storage.py`, `backend/services/storage_reconcile.py`, `backend/tests/test_storage_reconcile.py`

**Interfaces:** Produces `scan(db, root) -> ReconcileReport` with `orphan_dirs`, `missing_dirs`, `stale_tmp`.

**Context:** Spec E3/E4. Three drift modes: a directory with a `network.nc` and no row (orphan — a user copied a folder in, or an import died half-way); a row whose directory is gone (missing — a user deleted a folder in Finder); and `.tmp` siblings left by interrupted atomic writes.

**`scan` is read-only and never deletes.** Constraint #7: `routers/projects.py:547,1879` report stale `.tmp` files as a corruption signal, so sweeping them implicitly at startup would erase the evidence. Deletion is a separate, explicit `--sweep-tmp`.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_storage_reconcile.py
"""
Storage reconciliation (spec E3, E4).

`scan` NEVER deletes. routers/projects.py:547 and :1879 surface a stale `.tmp`
sibling as evidence of a save killed mid-write, so an implicit startup sweep
would destroy the only signal the user gets that a project may be corrupt.
Deletion is explicit and separate.
"""
```

Cover: an orphan directory containing `network.nc` is reported; a directory *without* `network.nc` is not (it is not a project); a row whose directory is missing is reported; `.tmp` siblings are listed but still on disk after `scan`; `scan` on a clean tree reports nothing; `sweep_tmp` removes only `.tmp` files and only when asked.

- [ ] **Step 2: Implement, then wire the CLI**

`tools/reconcile_storage.py` defaults to reporting and requires `--import-orphans` / `--sweep-tmp` to change anything, matching `tools/bootstrap_local.py`'s shape from phase 1a.

- [ ] **Step 3: Gate and commit**

```bash
pixi run gui-tests -q 2>&1 | tail -3
git commit -m "feat(gui): reconcile project storage against the database"
```

---

## Task 6: Inventory the legacy tree — read-only

**Files:** Create `backend/services/legacy_import.py`, `backend/tests/test_legacy_inventory.py`

**Interfaces:** Produces `inventory(legacy_root) -> list[LegacyProject]` with `dir_name`, `has_network`, `has_chat`, `has_snapshots`, `parent_name`, `scenario_description`, `skip_reason`.

**Context:** Spec F1. Verified against the real tree on this machine (constraints #8–#10, #12–#13):

- 11 flat directories, one org-scoped tree, `3_nodes_system.pypsaproj.zip`, `new_project_test.pypsaproj` — **non-directories and the org-scoped tree must be skipped, not imported**
- `metadata.json`'s `parent_project` is a **name**, and `4_nodes_N-1` names `test_project_4_nodes2`, which does not exist — dangling parents are normal
- 5 directories carry `chat.jsonl`, 1 carries `snapshots/` — spec F3's reason for not using the export bundle
- `Project.name` is `String(64)`; `UniqueConstraint("org_id", "name")`

Inventory is **pure reporting**. Nothing is copied in this task, so it can be run against real data with no risk.

- [ ] **Step 1: Write the failing test**

Build a fixture tree mirroring the real one — including a `.zip`, a bare file, a UUID-named directory, a directory with no `network.nc`, a dangling `parent_project`, a 200-character name, and two directories whose sanitised names collide. Assert each is classified with the right `skip_reason` or accepted.

- [ ] **Step 2: Implement and run against the real tree**

```bash
cd pypsa-gui/backend
pixi run python -c "
from services.legacy_import import inventory
for p in inventory('projects'):
    print(f'{p.dir_name:40} net={p.has_network} chat={p.has_chat} snap={p.has_snapshots} parent={p.parent_name} skip={p.skip_reason}')
"
```

Expected: 11 importable, the `.zip`/`.pypsaproj`/org-UUID entries skipped with reasons. **Read-only — safe on real data.**

- [ ] **Step 3: Gate and commit**

```bash
pixi run gui-tests -q 2>&1 | tail -3
git commit -m "feat(gui): inventory the legacy project tree"
```

---

## Task 7: Import the legacy tree

**Files:** Modify `backend/services/legacy_import.py`; create `backend/tools/import_legacy.py`, `backend/tests/test_legacy_import.py`

**Interfaces:** Consumes `inventory` (Task 6), `storage_path_for` (Task 3), `safe_names` (Task 1). Produces `import_all(db, legacy_root, org_id, user_id, *, apply=False) -> ImportReport`.

**Context:** Spec F2–F4.

**Copy, never move.** The spec says "copy" and the constraint above says destructive steps are copy-then-verify. A move that fails half-way through a 200 MB `network.nc` leaves the user with neither copy. The legacy tree is left exactly as it is; a later, separate decision can remove it.

**Two passes for parents.** `parent_project` is a name, and a child can appear before its parent. Pass 1 inserts every row with `parent_project_id = None`; pass 2 resolves names to ids. Unresolved names are **reported, not fatal** — `test_project_4_nodes2` is genuinely gone.

**Idempotent and resumable.** Re-running must not duplicate. Key on `(org_id, name)`, which the DB already enforces; an existing row means "already imported", and the copy is skipped if the destination has a `network.nc` of the same size.

- [ ] **Step 1: Write the failing test**

Cover, at minimum: a clean import of three projects; parent resolution across pass order; a dangling parent reported and the child still imported; a re-run importing nothing and reporting all as already-present; a resumed run after a simulated crash between copy and row-insert; a name over 64 characters truncated without colliding; two directories colliding after sanitising; `chat.jsonl` and `snapshots/` present in the destination; and `apply=False` making **no** filesystem or DB change.

The dry-run test is the important one — it is what makes the tool safe to point at real data.

- [ ] **Step 2: Implement**

Copy with `shutil.copytree(..., dirs_exist_ok=True)`, verify `network.nc` size matches, then insert the row. In that order: a row without files is worse than files without a row, because Task 5's reconcile reports the latter and can fix it.

- [ ] **Step 3: CLI, dry-run by default**

```bash
pixi run python -m tools.import_legacy                 # dry run, prints the plan
pixi run python -m tools.import_legacy --apply         # does it
```

- [ ] **Step 4: Rehearse on a copy of the real tree**

```bash
cd pypsa-gui/backend
REHEARSE=$(mktemp -d) && cp -R projects "$REHEARSE/legacy" && APP=$(mktemp -d)
PYPSAGUI_LOCAL_MODE=1 PYPSAGUI_APP_DATA_DIR="$APP" \
  DATABASE_URL="sqlite+pysqlite:///$APP/pypsa-gui.db" PROJECTS_ROOT="$APP/projects" \
  LEGACY_ROOT="$REHEARSE/legacy" MPLBACKEND=Agg \
  pixi run python -m tools.import_legacy --apply
find "$APP/projects" -maxdepth 2
```

Expected: 11 human-readable directories. **Against a copy, with the real tree untouched** — confirm with `git status` and by re-listing `projects/`.

- [ ] **Step 5: Gate and commit**

```bash
pixi run gui-tests -q 2>&1 | tail -3
git commit -m "feat(gui): import the legacy project tree"
```

---

## Task 8: Run it on first launch

**Files:** Modify `backend/main.py` (`lifespan`), `backend/local_mode.py`; create `backend/tests/test_first_run_import.py`

**Context:** Spec F1. Phase 1a's `lifespan` already does `ensure_app_dirs` → `ensure_schema` → `ensure_local_identity` in local mode. First-run import is a fourth step, and it must run **after** the identity exists, since every imported row needs `org_id` and `created_by`.

**"First run" needs a marker, not an empty-directory heuristic.** A user who imports and then deletes every project would otherwise get the import again on the next launch. Write a `.import-complete` marker in the app-data dir carrying a timestamp and the counts.

**Never block startup.** A failed import must log and continue to a working, empty app — not a backend that will not boot. The CLI from Task 7 remains the retry path.

- [ ] **Step 1: Write the failing test**

Cover: import runs when the marker is absent and a legacy tree exists; does not run when the marker is present; does not run in web mode; a raising importer still yields a booting app with a reachable `/api/health`; the marker is written only on success.

- [ ] **Step 2: Implement, gate, commit**

```bash
pixi run gui-tests -q 2>&1 | tail -3
git commit -m "feat(gui): import legacy projects on first launch"
```

---

## Task 9: Phase 1b acceptance

**Files:** none — verification only.

- [ ] **Step 1: Both suites**

```bash
pixi run gui-tests -q 2>&1 | tail -3
pixi run npm --prefix pypsa-gui/frontend test 2>&1 | tail -4
```

- [ ] **Step 2: A real first run against a copy of the real data**

```bash
cd pypsa-gui/backend
APP=$(mktemp -d); LEG=$(mktemp -d); cp -R projects "$LEG/legacy"
PYPSAGUI_LOCAL_MODE=1 PYPSAGUI_APP_DATA_DIR="$APP" \
  DATABASE_URL="sqlite+pysqlite:///$APP/pypsa-gui.db" PROJECTS_ROOT="$APP/projects" \
  LEGACY_ROOT="$LEG/legacy" FLAT_PROJECTS_ROOT="$APP/flat" \
  CORS_ALLOWED_ORIGINS="http://127.0.0.1:8125" MPLBACKEND=Agg \
  pixi run python -m uvicorn main:app --port 8125 --log-level warning &
```

Then check: `/api/projects/` lists the imported projects; each has a readable directory under `$APP/projects/<org>/`; opening one returns its buses; `git status` is clean and the real `projects/` tree is byte-identical to the Task 0 backup.

- [ ] **Step 3: Tear down**

```bash
lsof -ti :8125 | xargs kill -9
lsof -nP -iTCP:8125 -sTCP:LISTEN || echo "port free"
```

---

## Self-review

**Spec coverage.** E1 → Tasks 1, 3. E2 → Tasks 2, 3 (+ migration 0003). E3 → Task 5. E4 → Task 5. E5 → Task 4. F1 → Tasks 6, 8. F2 → Task 7. F3 → Task 7 (copies the directory, so `snapshots/` and `chat.jsonl` come along; the bundle path is never used). F4 → Task 7.

**Ordering.** Task 2 must precede Task 3 — five sites read `storage_path` directly and would resolve a relative value against the process CWD. Task 1 precedes Task 3, which consumes it. Tasks 6→7→8 are strictly ordered. Task 4 is independent and could move.

**Type consistency.** `storage_path_for` gains two parameters in Task 3 and its two callers change in the same commit. `project_dir` keeps its signature throughout. `inventory` returns the same `LegacyProject` that `import_all` consumes.

**Known open questions**, to settle before Task 3:

1. **Should the org segment stay a UUID?** Assumed yes — orgs are not user-renameable and the segment is never browsed to directly. In local mode there is exactly one org, so the user sees a single opaque directory between the root and their projects. If that is unacceptable, local mode could use a literal `Local` segment; that is a one-line change in `storage_path_for` but needs its own collision story for web mode.
2. **What happens to the legacy tree after a successful import?** This plan leaves it untouched. Deleting it is a separate decision that should be the user's, and it is the only remaining copy if an import is later found to be faulty.

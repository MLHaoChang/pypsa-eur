"""
`project_dir` resolves; `ensure_project_dir` creates (phase 1b, Task 3).

`project_registry.project_dir()` used to `mkdir(parents=True, exist_ok=True)`
on every call, and thirteen call sites relied on that — including several that
only read. Splitting the two is a prerequisite for E2 (relative paths), and it
is **not** behaviour-preserving: `GET /api/projects/` calls it for every row,
so today merely listing projects resurrects the directory of a project the user
deleted in Finder, and Task 6's `missing_dirs` detection can never fire.

The guard test is the load-bearing one. Once `storage_path` can be relative, a
single surviving `Path(row.storage_path)` resolves against the process CWD
instead of the projects root — and under pytest that CWD is
`pypsa-gui/backend`, inside the checkout. It flags **any** attribute access
named `storage_path`, not only ones wrapped in `Path(...)`: a regex keyed on
`Path(` cannot see `project_registry.py:146`
(`ctx.storage_dir = str(project.storage_path)`), whose value flows straight
into `Path(...)` at `chat_service.py:758`, `upload_service.py:154`,
`pypsa_service.py:696` and `solve_queue.py:368`.
"""
from __future__ import annotations

import ast
import pathlib
import uuid

import pytest

from db.models import Project
from services import project_registry
from settings import get_settings

_BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Functions allowed to touch the raw column. `project_dir` IS the resolver;
# `ensure_project_dir` delegates to it; `rename_project` legitimately reads the
# old value and writes the new one, because E1 makes the directory move with
# the name. Exempting a named function is the right relaxation — narrowing the
# matcher back to `Path(`-wrapped hits is not, because that is exactly the
# weakness that hid `bind_context`'s assignment.
#
# `rebase_row` is `rename_project`'s counterpart for a row 0003 could not
# convert, and `stores_absolute_path` is the one predicate that needs the
# distinction `project_dir` deliberately erases.
_EXEMPT_FUNCTIONS = frozenset({
    "project_dir",
    "ensure_project_dir",
    "rename_project",
    "rebase_row",
    "stores_absolute_path",
})


@pytest.fixture
def projects_root(tmp_path, monkeypatch):
    root = tmp_path / "Projects"
    monkeypatch.setenv("PROJECTS_ROOT", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def _project(storage_path) -> Project:
    """A detached row. `project_dir` reads nothing but `storage_path`."""
    return Project(id=uuid.uuid4(), org_id=uuid.uuid4(), name="X",
                   storage_path=str(storage_path))


# ── The resolver ─────────────────────────────────────────────────────────────

def test_project_dir_does_not_create_anything(projects_root):
    """
    `routers/projects.py:578` calls this for every row of `GET /api/projects/`.
    If it mkdir'd, listing would resurrect the directory of a project deleted
    in Finder and defeat Task 6's missing-dir detection.
    """
    absent = projects_root / "Belgium Grid"
    resolved = project_registry.project_dir(_project(absent))

    assert resolved == absent
    assert not absent.exists()
    assert not projects_root.exists()


def test_project_dir_resolves_a_relative_path_against_the_root(projects_root):
    """E2's whole point: the row stores `Belgium Grid`, not an absolute path
    baked to one machine's home directory."""
    resolved = project_registry.project_dir(_project("Belgium Grid"))
    assert resolved == projects_root / "Belgium Grid"


def test_project_dir_leaves_an_absolute_path_alone(projects_root, tmp_path):
    """
    Pre-0003 rows are absolute and a restored backup can carry either form, so
    both are accepted permanently — not as a migration window.
    """
    elsewhere = tmp_path / "somewhere" / "else"
    assert project_registry.project_dir(_project(elsewhere)) == elsewhere


def test_ensure_project_dir_creates_it(projects_root):
    created = project_registry.ensure_project_dir(_project("Belgium Grid"))
    assert created == projects_root / "Belgium Grid"
    assert created.is_dir()


def test_ensure_project_dir_is_idempotent(projects_root):
    project = _project("Belgium Grid")
    first = project_registry.ensure_project_dir(project)
    (first / "network.nc").write_text("network", encoding="utf-8")
    second = project_registry.ensure_project_dir(project)
    assert second == first
    assert (second / "network.nc").read_text(encoding="utf-8") == "network"


# ── The behaviour change this task deliberately makes ────────────────────────

def test_layout_put_404s_when_the_directory_is_gone(client, _auth_db, seeded_identity):
    """
    `PUT /{name}/layout` has always guarded `if not dest.exists(): 404`, but
    that branch was unreachable: `_resolve_project_src` went through the
    mkdir-ing `project_dir`, so the check ran against a directory the check
    itself had just created.

    Keeping `project_dir` there makes the 404 real. A layout PUT against a
    project whose directory is gone should fail loudly, not silently recreate
    an empty directory and report success.
    """
    from db.models import User

    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        project = project_registry.create_root(db, user, "Ghost Project")
        expected = project_registry.project_dir(project)

    assert not expected.exists(), "create_root must not materialise anything"

    resp = client.put("/api/projects/Ghost Project/layout", json={"nodes": []})

    assert resp.status_code == 404, resp.text
    assert not expected.exists(), "the 404 path must not create the directory"


# ── The guard ────────────────────────────────────────────────────────────────

def _scanned_files() -> list[pathlib.Path]:
    """
    `services/`, `routers/`, `tools/`, the backend's root modules, and
    `tests/conftest.py` — which is a **seventh** direct read that 19 tests
    depend on, and the one an earlier draft of this guard could not see
    because it scanned only the production packages.
    """
    files: list[pathlib.Path] = []
    for package in ("services", "routers", "tools"):
        files.extend(sorted((_BACKEND / package).rglob("*.py")))
    files.extend(sorted(_BACKEND.glob("*.py")))
    files.append(_BACKEND / "tests" / "conftest.py")
    return [f for f in files if f.exists()]


class _StoragePathVisitor(ast.NodeVisitor):
    """Records `<expr>.storage_path` outside the exempt functions."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.stack: list[str] = []
        self.hits: list[str] = []

    def _visit_function(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "storage_path" and not (set(self.stack) & _EXEMPT_FUNCTIONS):
            # `Project.storage_path` is a COLUMN reference for SQLAlchemy
            # (`select(Project.storage_path)`), not a value read — it never
            # produces a path and cannot resolve against the wrong root.
            owner = node.value
            if not (isinstance(owner, ast.Name) and owner.id.lstrip("_") == "Project"):
                where = "::".join(self.stack) or "<module>"
                self.hits.append(
                    f"{self.path.relative_to(_BACKEND)}:{node.lineno} (in {where})"
                )
        self.generic_visit(node)


def test_nothing_reads_storage_path_except_the_resolver():
    """
    Every offender must go through `project_dir` / `ensure_project_dir`.

    An `ast` walk rather than a regex, and deliberately not keyed on `Path(`:
    the access that matters most assigns the raw string to
    `ProjectContext.storage_dir`, and four modules turn that into a `Path`
    later, far from here.
    """
    offenders: list[str] = []
    for path in _scanned_files():
        visitor = _StoragePathVisitor(path)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        offenders.extend(visitor.hits)

    assert offenders == [], (
        "Direct `storage_path` reads outside the resolver:\n  "
        + "\n  ".join(offenders)
    )

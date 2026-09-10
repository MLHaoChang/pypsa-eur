"""
Detect source files changing WHILE the suite runs (Improvement #10).

Not a test module — helpers for the conftest hook that reports the change.

Why this exists rather than the fixture the backlog asked for: the flake it
was written against is not parallel-worker contamination. `pytest-xdist` is
not installed, `addopts` carries no `-n`, and PROJECTS_DIR (which resolves
outside the checkout) is untouched by a suite run. The tests that actually
flake are the nine `inspect.getsource` call sites across five files.

`getsource` reads the file from disk at the line numbers recorded in the
code object at IMPORT time. Edit the file after import and it returns text
that was never in that function — inserting three comment lines above a
function makes them appear inside its "source". `linecache.checkcache()`
does not help: the cache is not the problem, the stored line numbers are.

So the racing party is an EDITOR, not another pytest process. Two
concurrent readers are harmless. In this repo the editor is a second agent
session working the same worktree, which CLAUDE.md documents as normal.

Nothing here prevents that. What it prevents is the failure LYING —
`assert "with ctx.chat_state.lock:" in src` reads as a locking regression
and costs the next person an investigation into a bug that does not exist.
"""
from __future__ import annotations

import os
from pathlib import Path

# Directories whose contents are imported by the suite. Test files are
# deliberately absent: pytest reads those at collection, before the window
# this watches, and a test edited mid-run is the author's own doing.
WATCHED_ROOTS = ("services", "routers", "models", "db")

_IGNORED_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache"}


def _iter_py(root: Path):
    if not root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS]
        for fn in filenames:
            # .py only. Logs, sqlite journals and pycache churn constantly
            # during a run, and a warning that cries wolf is worse than none.
            if fn.endswith(".py"):
                yield Path(dirpath) / fn


def snapshot_mtimes(roots) -> dict[str, float]:
    """Map every watched .py path to its mtime. Never raises."""
    out: dict[str, float] = {}
    for root in roots:
        for p in _iter_py(Path(root)):
            try:
                out[str(p)] = p.stat().st_mtime
            except OSError:
                continue
    return out


def changed_since(snapshot: dict[str, float], roots) -> list[str]:
    """Paths added, removed, or modified since `snapshot`. Never raises."""
    current = snapshot_mtimes(roots)
    changed = {
        p for p, m in current.items()
        if p not in snapshot or snapshot[p] != m
    }
    changed |= set(snapshot) - set(current)
    return sorted(changed)


def format_warning(changed: list[str]) -> str | None:
    """The terminal note, or None when the tree held still."""
    if not changed:
        return None
    shown = "\n".join(f"    {Path(p).name}" for p in changed[:8])
    more = f"\n    …and {len(changed) - 8} more" if len(changed) > 8 else ""
    return (
        f"{len(changed)} source file(s) changed WHILE this suite ran:\n"
        f"{shown}{more}\n"
        "  Any failure above may be spurious. inspect.getsource reads from "
        "disk at the\n"
        "  line numbers recorded at import, so a mid-run edit makes it "
        "return text that\n"
        "  was never in the function — the assertion then fails as though "
        "the code were\n"
        "  wrong. Re-run serially on a still tree before believing a "
        "failure."
    )

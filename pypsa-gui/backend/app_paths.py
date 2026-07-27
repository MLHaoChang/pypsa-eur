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
    Org-scoped project store, ``<root>/<org_uuid>/<project_uuid>/``.

    User-visible on purpose — being able to find, back up and zip your own
    projects is most of the point of a local app.
    """
    override = os.environ.get("PYPSAGUI_PROJECTS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / "Documents" / APP_NAME / "Projects").resolve()


def default_flat_projects_root() -> Path:
    """
    FLAT legacy store, ``<root>/<display-name>/network.nc``.

    NOT the same as `default_projects_root()`: that one is org-scoped and its
    entries are UUID directories, so pointing the flat store at it makes
    `_find_direct_children`'s ``<dir>/network.nc`` filter never match. Kept in
    app-data because it is an implementation detail the user should not browse.
    """
    return app_data_dir() / "flat_projects"


def default_database_url() -> str:
    """Absolute on purpose: a relative SQLite URL resolves against cwd."""
    return f"sqlite+pysqlite:///{(app_data_dir() / 'pypsa-gui.db').as_posix()}"

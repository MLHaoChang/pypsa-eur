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

APP_NAME = "PyPSA Studio"

# What the app was called before. `APP_NAME` is not a label — it is the
# directory the user's projects live in, so renaming it outright points a
# working install at empty folders: the app opens, lists nothing, and the
# projects are still on disk under the old name with nothing saying so. To the
# person it happens to that is indistinguishable from data loss.
LEGACY_APP_NAME = "PyPSA GUI"


def _preferred(base: Path) -> Path:
    """
    The new directory, unless only the OLD one exists.

    Ordered this way on purpose: the NEW path wins as soon as it exists, so a
    stale empty `PyPSA GUI` folder — one `mkdir` from any earlier launch —
    cannot pin every future install to the old name forever. The legacy path is
    a fallback for machines that have real data there, not a permanent alias.
    """
    new = (base / APP_NAME).resolve()
    if new.exists():
        return new
    legacy = (base / LEGACY_APP_NAME).resolve()
    return legacy if legacy.exists() else new


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
    return _preferred(base)


def default_projects_root() -> Path:
    """
    Org-scoped project store, ``<root>/<org_uuid>/<project_uuid>/``.

    User-visible on purpose — being able to find, back up and zip your own
    projects is most of the point of a local app. Which is exactly why the
    rename has to keep an existing install pointing at its own projects; see
    `_preferred`.
    """
    override = os.environ.get("PYPSAGUI_PROJECTS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return _preferred(Path.home() / "Documents") / "Projects"


def default_flat_projects_root() -> Path:
    """
    FLAT legacy store, ``<root>/<display-name>/network.nc``.

    NOT the same as `default_projects_root()`: that one is org-scoped and its
    entries are UUID directories, so pointing the flat store at it makes every
    ``<flat root>/<display-name>`` lookup in `routers.projects` address an
    org-UUID directory instead. Kept in app-data because it is an
    implementation detail the user should not browse.
    """
    return app_data_dir() / "flat_projects"


def default_database_url() -> str:
    """Absolute on purpose: a relative SQLite URL resolves against cwd."""
    return f"sqlite+pysqlite:///{(app_data_dir() / 'pypsa-gui.db').as_posix()}"


def user_env_file() -> Path:
    """
    Operator-supplied environment, editable from inside the running app.

    U-1. `backend/.env` is deliberately excluded from the frozen bundle
    (`pypsa-gui.spec`, `smoke/check_bundle.py`) because it carries a real
    `ANTHROPIC_API_KEY` *and* the `SECRET_KEY` that signs sessions — shipping it
    would hand every install the developer's secrets. Nothing replaced it, so
    the packaged app had no supported way to receive an API key at all and the
    chat panel was permanently disabled in the shipped artifact.

    This file is that replacement. It lives in app-data rather than the bundle,
    so it survives app updates and belongs to the person who typed the key
    rather than to whoever built the `.app`.
    """
    return app_data_dir() / "user.env"

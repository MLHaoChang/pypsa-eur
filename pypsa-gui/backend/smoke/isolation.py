"""
One isolation guard for every acceptance harness.

**Why `DATABASE_URL` is in here.** Setting `PYPSAGUI_APP_DATA_DIR` and
`PYPSAGUI_PROJECTS_ROOT` looks like full isolation and is not. `settings.py`
declares

    model_config = SettingsConfigDict(env_file=str(_BACKEND / ".env"), ...)
    database_url: str = Field(default_factory=app_paths.default_database_url)

and pydantic-settings ranks the **env file above a default_factory**. So the
`DATABASE_URL` pinned in `backend/.env` — which is cwd-relative — beats the
app-data default, and a harness writes its auth database wherever it was
launched from while faithfully reporting that app-data was redirected.

Measured, not theorised: an agent running an "isolated" backend from the repo
root created a stray `auth_dev.db` there. That filename is on the credential
gate's own forbidden list (`smoke/check_bundle.py`) precisely because it holds
a password hash.

**Why it is shared.** `accept_shutdown.py`, `accept_downloads.py` and
`accept_coldstart.py` each carried their own copy of the path checks — 4, 4 and
7 assertions, already diverged. Same drift `utils/carriers.ts` and
`utils/geo.ts` exist to prevent, one language over.

Usage, before importing anything that reads settings:

    from smoke.isolation import require_isolated_environment
    require_isolated_environment()
"""
from __future__ import annotations

import os
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

REQUIRED = ("PYPSAGUI_APP_DATA_DIR", "PYPSAGUI_PROJECTS_ROOT", "DATABASE_URL")

# Bare names that pydantic binds DIRECTLY to a settings field, beating the
# `default_factory` that is the only place the `PYPSAGUI_*` overrides are ever
# read. A developer with one of these exported — or sitting in `backend/.env` —
# gets full isolation theatre from the variables above while the app writes
# somewhere else entirely. Measured in `accept_coldstart.py`:
# `PROJECTS_ROOT=/tmp/decoy PYPSAGUI_PROJECTS_ROOT=/tmp/throwaway` resolves
# `settings.projects_root` to `/tmp/decoy`.
#
# Only `accept_coldstart.py` carried this check; the other two harnesses did
# not. Consolidating here makes every caller strictly safer than the copy it
# replaces, which is the point of doing it at all.
FORBIDDEN_BARE = ("PROJECTS_ROOT", "FLAT_PROJECTS_ROOT", "LEGACY_ROOT")


class IsolationError(RuntimeError):
    """The environment is not isolated enough to run a harness against."""


def _refuse(msg: str) -> None:
    raise IsolationError(f"refusing to run: {msg}")


def _documents() -> Path:
    return (Path.home() / "Documents").resolve()


def _reject_if_under_documents(label: str, path: Path) -> None:
    docs = _documents()
    # `==` as well as `parents`: the existing per-harness copies checked only
    # ancestry, so Documents itself slipped through.
    if path == docs or docs in path.parents:
        _refuse(f"{label} is inside {docs} — use a throwaway directory")


def _is_absolute_anywhere(raw: str) -> bool:
    """
    Absolute on POSIX *or* Windows, whichever machine is reading this.

    `Path(...).is_absolute()` answers only for the host platform, so a Windows
    URL (`C:/Users/.../db`) reads as relative when checked on macOS and this
    guard would reject a correctly-isolated Windows harness. The run-books are
    written for both platforms, so the check has to be too.
    """
    return raw.startswith("/") or bool(re.match(r"^[A-Za-z]:[\\/]", raw))


def _sqlite_path(url: str) -> str | None:
    """The filesystem path from a SQLite URL, or None for any other driver."""
    scheme, sep, rest = url.partition(":///")
    if not sep or not scheme.startswith("sqlite"):
        return None
    return rest


def require_isolated_environment(env: dict[str, str] | None = None) -> None:
    """
    Refuse to continue unless app-data, projects AND the database are all
    pointed somewhere disposable.

    Raises IsolationError rather than asserting, so it still fires under
    `python -O`, where `assert` is compiled out — the previous per-harness
    guards would have silently vanished.
    """
    env = os.environ if env is None else env

    for name in REQUIRED:
        if not (env.get(name) or "").strip():
            _refuse(f"{name} is unset — point it at a throwaway location")

    for bare in FORBIDDEN_BARE:
        if (env.get(bare) or "").strip():
            _refuse(
                f"{bare} is set, and pydantic binds that bare name straight to "
                "the settings field — it beats the PYPSAGUI_* override. Unset it."
            )

    real_projects = (BACKEND / "projects").resolve()
    for label in ("PYPSAGUI_APP_DATA_DIR", "PYPSAGUI_PROJECTS_ROOT"):
        path = Path(env[label]).expanduser().resolve()
        # Equality AND ancestry: `accept_shutdown.py` checked only equality, so
        # a subdirectory of the 113 MB real tree would have passed there.
        if path == real_projects or real_projects in path.parents:
            _refuse(f"{label} is the real projects tree ({path})")
        _reject_if_under_documents(label, path)

    raw = _sqlite_path(env["DATABASE_URL"])
    if raw is None:
        # Postgres and friends have no local path to police. Requiring the
        # variable to be SET is the isolation guarantee; where a server-backed
        # URL points is the operator's business.
        return
    if not _is_absolute_anywhere(raw):
        _refuse(
            "DATABASE_URL is a relative SQLite path "
            f"({env['DATABASE_URL']!r}) — it resolves against the current "
            "directory, which is how a stray auth_dev.db lands in the repo"
        )
    _reject_if_under_documents("DATABASE_URL", Path(raw).expanduser().resolve())

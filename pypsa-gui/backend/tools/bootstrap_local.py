"""
Create (or repair) the local database and identity from the command line.

Spec B4. The web equivalent is `bootstrap_super_admin.py`, which takes an email
and a password; local mode has neither, so this is a separate entry point
rather than a flag on that one.

    pixi run python -m tools.bootstrap_local

Useful on its own for a fresh machine, and as a diagnostic when the desktop
shell fails to start — it prints the three paths the app will actually use.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import local_mode  # noqa: E402
from local_bootstrap import ensure_app_dirs, ensure_schema  # noqa: E402
from settings import get_settings  # noqa: E402


def main() -> int:
    # Order matters: the directories must exist before Alembic opens the
    # database file, or `upgrade` dies with "unable to open database file".
    ensure_app_dirs()

    settings = get_settings()
    url = settings.database_url
    ensure_schema(url)

    # Imported after ensure_schema so the engine is built against a database
    # that already has its tables.
    from db.session import SessionLocal

    with SessionLocal() as db:
        user = local_mode.ensure_local_identity(db)
        user_id, email = user.id, user.email

    print(f"database: {url}")
    print(f"projects: {settings.projects_root}")
    print(f"identity: {email} ({user_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

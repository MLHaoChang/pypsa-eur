"""
First-run database and directory bootstrap.

Separate from `local_mode` so it can be unit-tested and reused from a CLI
without importing the FastAPI app.
"""
from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import app_paths
from datetime import UTC


def _alembic_config(url: str) -> Config:
    backend = Path(__file__).resolve().parent
    cfg = Config(
        str(backend / "alembic.ini"),
        # `configure_logger=false` is load-bearing when this runs in-process.
        # `alembic/env.py` calls `logging.config.fileConfig()`, which defaults
        # to `disable_existing_loggers=True` — that silences `main`'s logger,
        # `pypsa_gui.chat`, and uvicorn's own loggers, and repoints root at
        # alembic.ini's stderr handler. On a windowed Windows build with no
        # console, writing to that handle can raise.
        attributes={"configure_logger": "false", "explicit_url": True},
    )
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    # alembic.ini carries `prepend_sys_path = .`, resolved against the process
    # CWD — which is "/" for a macOS .app launched from Finder.
    cfg.set_main_option("prepend_sys_path", str(backend))
    return cfg


def ensure_schema(url: str) -> None:
    """
    Bring `url` to head, whatever state it is in.

    Three cases:

      * file does not exist — `upgrade` creates everything.
      * has `alembic_version` — `upgrade` applies whatever is missing.
      * built by `create_all` — no `alembic_version`, so `upgrade` would fail
        with "table organizations already exists". `create_all` builds the
        CURRENT model schema, i.e. HEAD (`db/models.py` already declares
        `Session.active_project_id`, which is what 0002 adds), so stamp HEAD
        and there is nothing left to apply. Stamping 0001 instead would re-run
        0002's `add_column` on a column that already exists. This is spec G4.
    """
    if url.startswith("sqlite") and ":memory:" not in url:
        db_path = Path(url.split("///", 1)[1])
        db_path.parent.mkdir(parents=True, exist_ok=True)

    cfg = _alembic_config(url)

    engine = create_engine(url)
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    if names and "alembic_version" not in names:
        command.stamp(cfg, "head")
        return

    command.upgrade(cfg, "head")


def ensure_app_dirs() -> None:
    """
    Create every directory the app writes to.

    Must run before `ensure_schema`: on a machine that has never run the app,
    the app-data directory does not exist and `alembic upgrade` dies with
    `unable to open database file`.
    """
    from settings import get_settings

    app_paths.app_data_dir().mkdir(parents=True, exist_ok=True)
    s = get_settings()
    for p in (s.projects_root, s.legacy_root, s.flat_projects_root):
        Path(p).mkdir(parents=True, exist_ok=True)


DEV_ADMIN_EMAIL = "admin@example.com"
DEV_ADMIN_PASSWORD_FILE = "dev-admin-password"


def is_default_sqlite(url: str) -> bool:
    """
    True when `url` is the per-user SQLite file and nobody chose another.

    The dev admin below is created only then. A deployment sets `DATABASE_URL`
    (compose, `.env`) to Postgres or to its own file, and must never get an
    account whose password sits in a file beside the database.
    """
    return url == app_paths.default_database_url()


def ensure_dev_admin(session_factory=None) -> Path:
    """
    Seed the dev sign-in AGENTS.md and the README describe, idempotently.

    Creates the app-data directory, brings the schema to head, writes a
    generated password to ``<app-data>/dev-admin-password`` (mode 0600, kept if
    it already exists) and makes ``admin@example.com`` an active super-admin
    with that password and a personal workspace, so the account can create
    projects. `session_factory` defaults to the app's `SessionLocal`. Returns
    the password file's path; the password itself is never
    logged or returned.

    Web mode runs no bootstrap of its own, so without this a fresh checkout
    booted with no ``.env`` fails every database call with "unable to open
    database file".
    """
    import secrets
    from datetime import datetime

    from sqlalchemy import select

    from db.models import User
    from services.auth_service import hash_password, verify_password
    from services.tenancy_service import ensure_personal_org
    from settings import get_settings

    ensure_app_dirs()
    ensure_schema(get_settings().database_url)

    pw_file = app_paths.app_data_dir() / DEV_ADMIN_PASSWORD_FILE
    password = pw_file.read_text(encoding="utf-8").strip() if pw_file.exists() else ""
    if not password:
        password = secrets.token_urlsafe(18)
        # Create with 0600 from the start, so the password is never readable
        # between the write and a later chmod.
        fd = os.open(pw_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(password + "\n")

    if session_factory is None:
        from db.session import SessionLocal as session_factory

    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == DEV_ADMIN_EMAIL))
        if user is None:
            user = User(
                email=DEV_ADMIN_EMAIL,
                password_hash=hash_password(password),
                status="active",
                is_super_admin=True,
                created_at=datetime.now(tz=UTC),
            )
            db.add(user)
        elif not user.password_hash or not verify_password(password, user.password_hash):
            user.password_hash = hash_password(password)
        user.status = "active"
        user.is_super_admin = True
        db.commit()
        db.refresh(user)
        ensure_personal_org(db, user)
    return pw_file

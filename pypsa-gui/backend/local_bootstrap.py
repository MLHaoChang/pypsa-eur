"""
First-run database and directory bootstrap.

Separate from `local_mode` so it can be unit-tested and reused from a CLI
without importing the FastAPI app.
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import app_paths


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

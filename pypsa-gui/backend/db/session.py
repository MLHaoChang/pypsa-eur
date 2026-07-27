from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from settings import get_settings


def configure_sqlite(engine: Engine) -> Engine:
    """
    Per-connection SQLite pragmas. No-op on Postgres.

    `foreign_keys` — SQLite ships with enforcement OFF, per connection. Without
    it every `ON DELETE SET NULL` / `ON DELETE CASCADE` in `db/models.py` is
    inert: deleting a parent project leaves children pointing at a row that no
    longer exists instead of nulling `parent_project_id`. Postgres always
    enforces, so the hook is a no-op there.

    `journal_mode=WAL` — without it a writer blocks every reader.
    `services/chat_tools.py` opens its own `SessionLocal()` on a pool worker and
    commits while the request path reads, so contention is routine rather than
    theoretical.

    `busy_timeout` — the 5s default is too short for that contention. Past it,
    `database is locked` surfaces through `main.py`'s bare `except` as a 503
    telling a desktop user to start Postgres.

    `synchronous=NORMAL` — safe under WAL and materially faster than FULL.
    """
    if not engine.url.get_backend_name().startswith("sqlite"):
        return engine

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
        finally:
            cursor.close()

    return engine


# Retained: tests/conftest.py calls this from `_auth_db`, which is
# session-scoped and pulled in by the autouse `_reset_tenant_tables` and
# `_acting_user` fixtures — i.e. every test in the suite. Renaming it outright
# errors all of them at fixture setup.
enable_sqlite_foreign_keys = configure_sqlite


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    kwargs: dict = {"pool_pre_ping": True}
    # SQLite is useful for local auth review without Docker/Postgres, and is
    # the shipping configuration for the desktop build.
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        # NullPool for a file-backed database: one local user, one file.
        # QueuePool's 5 + 10 connections buy nothing here and widen the window
        # in which a writer holds the file.
        #
        # In-memory URLs are exempt — NullPool discards the database between
        # connections, which is precisely how tests/conftest.py's shared
        # `:memory:` StaticPool database works.
        if ":memory:" not in url:
            kwargs["poolclass"] = NullPool
    return configure_sqlite(create_engine(url, **kwargs))


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

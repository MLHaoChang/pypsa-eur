from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from settings import get_settings


def enable_sqlite_foreign_keys(engine: Engine) -> Engine:
    """
    Turn on `PRAGMA foreign_keys` for SQLite connections.

    SQLite ships with foreign-key enforcement OFF, per connection. Without this
    every `ON DELETE SET NULL` / `ON DELETE CASCADE` in `db/models.py` is inert:
    deleting a parent project leaves children pointing at a row that no longer
    exists instead of nulling `parent_project_id`. Postgres needs no equivalent
    (it always enforces), so the hook is a no-op there.
    """
    if not engine.url.get_backend_name().startswith("sqlite"):
        return engine

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    kwargs: dict = {"pool_pre_ping": True}
    # SQLite is useful for local auth review without Docker/Postgres.
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return enable_sqlite_foreign_keys(create_engine(url, **kwargs))


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

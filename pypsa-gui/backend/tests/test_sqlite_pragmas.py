"""
SQLite configuration for a single-writer local app (spec workstream G).

Measured on the pre-change engine: journal_mode=delete, busy_timeout=5000,
QueuePool 5 + 10 overflow. Without WAL a writer blocks every reader, and past
the 5s timeout `database is locked` is swallowed by main.py's bare `except` and
returned as a 503 telling a desktop user to start Postgres. `chat_tools` opens
its own SessionLocal on a pool worker and commits while the request path reads,
so contention is routine rather than theoretical.
"""
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

import db.session as db_session_module
from db.session import configure_sqlite


def test_wal_and_busy_timeout_are_set(tmp_path):
    db = tmp_path / "t.db"
    engine = configure_sqlite(create_engine(f"sqlite+pysqlite:///{db.as_posix()}"))
    try:
        with engine.connect() as c:
            assert c.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
            assert c.execute(text("PRAGMA busy_timeout")).scalar() >= 30000
            assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
    finally:
        engine.dispose()


def test_non_sqlite_engine_is_returned_untouched():
    engine = create_engine("postgresql+psycopg://u:p@localhost/db")
    assert configure_sqlite(engine) is engine


def test_old_name_is_still_callable():
    """
    conftest.py calls `enable_sqlite_foreign_keys` from `_auth_db`, which is
    session-scoped and pulled in by the autouse `_reset_tenant_tables` and
    `_acting_user` fixtures — i.e. every test in the suite. Renaming it
    without an alias errors all of them at fixture setup.
    """
    assert db_session_module.enable_sqlite_foreign_keys is configure_sqlite


def test_sqlite_uses_nullpool(monkeypatch, tmp_path):
    """
    Spec G1. QueuePool holds up to 15 connections against one file; with WAL
    and a single local user that buys nothing and widens the window in which a
    writer holds the database.

    In-memory URLs are exempt — NullPool discards the database between
    connections, which is exactly how conftest's shared `:memory:` DB works.
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'p.db').as_posix()}")
    import settings as settings_module

    settings_module.get_settings.cache_clear()
    db_session_module.get_engine.cache_clear()
    try:
        assert isinstance(db_session_module.get_engine().pool, NullPool)
    finally:
        settings_module.get_settings.cache_clear()
        db_session_module.get_engine.cache_clear()


def test_in_memory_sqlite_is_not_nullpool(monkeypatch):
    """The exemption above, asserted directly — conftest depends on it."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    import settings as settings_module

    settings_module.get_settings.cache_clear()
    db_session_module.get_engine.cache_clear()
    try:
        assert not isinstance(db_session_module.get_engine().pool, NullPool)
    finally:
        settings_module.get_settings.cache_clear()
        db_session_module.get_engine.cache_clear()

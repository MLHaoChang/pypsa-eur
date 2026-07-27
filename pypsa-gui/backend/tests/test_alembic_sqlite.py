"""
First-run schema bootstrap (spec workstream G).

Two traps, both found in review before implementation:

  * `create_all` builds the CURRENT model schema, i.e. HEAD — `db/models.py`
    already declares `Session.active_project_id`, which is exactly what
    `0002_session_active_project` adds. So an unversioned database must be
    stamped at HEAD, not at 0001; stamping 0001 then upgrading re-runs 0002's
    add_column on a column that already exists.
  * `alembic/env.py` overwrites `sqlalchemy.url` from settings on import, so
    without an opt-out `ensure_schema(url)` migrates a DIFFERENT database than
    the one it was handed — under pytest, conftest's in-memory one.
"""
from pathlib import Path

from sqlalchemy import create_engine, inspect

from db.models import Base


def test_env_py_sets_render_as_batch_for_autogenerate():
    """
    Online block only. The offline block emits SQL and never autogenerates, so
    the flag is inert there — see the deviation note in the plan (spec G2).
    """
    env = (Path(__file__).resolve().parent.parent / "alembic" / "env.py").read_text(
        encoding="utf-8"
    )
    assert "render_as_batch=True" in env


def test_upgrade_creates_a_fresh_database(tmp_path):
    from local_bootstrap import ensure_schema

    url = f"sqlite+pysqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    ensure_schema(url)
    engine = create_engine(url)
    try:
        names = inspect(engine).get_table_names()
    finally:
        engine.dispose()
    assert "alembic_version" in names
    assert "organizations" in names


def test_upgrade_or_stamp_handles_a_create_all_database(tmp_path):
    """Spec G4: a database built by create_all has no alembic_version row."""
    from local_bootstrap import ensure_schema

    url = f"sqlite+pysqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    engine = create_engine(url)
    Base.metadata.create_all(bind=engine)  # simulate the old bootstrap path
    engine.dispose()

    ensure_schema(url)  # must not raise

    engine = create_engine(url)
    try:
        assert "alembic_version" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_ensure_schema_migrates_the_url_it_is_given(tmp_path):
    """
    Regression guard for the env.py clobber: the file passed in must be the one
    that gets the tables, regardless of what settings.database_url says.
    """
    from local_bootstrap import ensure_schema

    target = tmp_path / "explicit.db"
    ensure_schema(f"sqlite+pysqlite:///{target.as_posix()}")
    assert target.is_file(), "ensure_schema migrated a different database"
    engine = create_engine(f"sqlite+pysqlite:///{target.as_posix()}")
    try:
        assert "organizations" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_ensure_app_dirs_creates_every_writable_root(tmp_path, monkeypatch):
    from local_bootstrap import ensure_app_dirs
    import settings as settings_module

    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("LEGACY_ROOT", str(tmp_path / "legacy"))
    monkeypatch.setenv("FLAT_PROJECTS_ROOT", str(tmp_path / "flat"))
    settings_module.get_settings.cache_clear()
    try:
        ensure_app_dirs()
        for p in ("appdata", "projects", "legacy", "flat"):
            assert (tmp_path / p).is_dir(), p
    finally:
        settings_module.get_settings.cache_clear()

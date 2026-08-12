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


def test_migrated_schema_matches_the_model_schema(tmp_path):
    """
    Model <-> migration drift detector (review round 1, Important 3).

    Every OTHER test in this suite builds its database with
    `Base.metadata.create_all` (`tests/conftest.py::_auth_db`), so a model
    class with no matching migration is invisible everywhere except a real
    `alembic upgrade head` — which no other test in this repo runs against the
    full model set. `solve_jobs` (0005) is scheduled to grow within this same
    increment (an `interrupted` status value, dismissal-related columns): the
    first model edit that ships without its matching migration would be green
    across the whole suite and broken on a real upgrade, and nothing would
    say so before a real deployment hit it.

    Builds two databases — one via a real `alembic upgrade head` (what
    production/`local_bootstrap.ensure_schema` runs), one via
    `Base.metadata.create_all` (what every other test builds against) — and
    diffs their reflected schemas: table set, and per table the column set,
    primary key, foreign keys, and indexes. Covers every table, not just
    `solve_jobs`.

    Indexes and unique constraints are compared as ONE merged set of
    column-tuples, not two separate categories. Verified against this exact
    codebase: `Project.__table_args__` declares
    `UniqueConstraint("org_id", "storage_path", name="uq_projects_org_id_storage_path")`
    (`db/models.py`), which `create_all` implements as a table-level UNIQUE
    CONSTRAINT — while migration 0003 creates the identical enforcement via
    `op.create_index(..., unique=True)`. Both produce the same real
    constraint on `(org_id, storage_path)`, but SQLAlchemy's inspector files
    them under `get_indexes()` for one and `get_unique_constraints()` for the
    other. Comparing the categories separately made this test fail on a
    difference that is not drift — the styles are two legitimate, already
    coexisting ways of expressing the same thing in this repo.
    """
    from alembic import command

    import local_bootstrap

    migrated_url = f"sqlite+pysqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    cfg = local_bootstrap._alembic_config(migrated_url)
    command.upgrade(cfg, "head")

    model_url = f"sqlite+pysqlite:///{(tmp_path / 'model.db').as_posix()}"
    model_engine = create_engine(model_url)
    Base.metadata.create_all(model_engine)

    migrated_engine = create_engine(migrated_url)
    try:
        migrated = inspect(migrated_engine)
        model = inspect(model_engine)

        # `alembic_version` is Alembic's own bookkeeping table, not a model.
        migrated_tables = set(migrated.get_table_names()) - {"alembic_version"}
        model_tables = set(model.get_table_names())
        assert migrated_tables == model_tables, (
            f"table drift: only in migrated={migrated_tables - model_tables}, "
            f"only in models={model_tables - migrated_tables}"
        )

        for table in sorted(model_tables):
            m_cols = {c["name"] for c in migrated.get_columns(table)}
            s_cols = {c["name"] for c in model.get_columns(table)}
            assert m_cols == s_cols, (
                f"{table}: column drift — only in migrated={m_cols - s_cols}, "
                f"only in models={s_cols - m_cols}"
            )

            m_pk = sorted(migrated.get_pk_constraint(table)["constrained_columns"])
            s_pk = sorted(model.get_pk_constraint(table)["constrained_columns"])
            assert m_pk == s_pk, f"{table}: primary key drift, migrated={m_pk} models={s_pk}"

            m_fks = {
                (tuple(sorted(fk["constrained_columns"])), fk["referred_table"])
                for fk in migrated.get_foreign_keys(table)
            }
            s_fks = {
                (tuple(sorted(fk["constrained_columns"])), fk["referred_table"])
                for fk in model.get_foreign_keys(table)
            }
            assert m_fks == s_fks, (
                f"{table}: foreign key drift, migrated={m_fks} models={s_fks}"
            )

            def _index_and_unique_cols(insp, tbl):
                cols = {tuple(sorted(ix["column_names"])) for ix in insp.get_indexes(tbl)}
                cols |= {
                    tuple(sorted(uc["column_names"])) for uc in insp.get_unique_constraints(tbl)
                }
                return cols

            m_idx = _index_and_unique_cols(migrated, table)
            s_idx = _index_and_unique_cols(model, table)
            assert m_idx == s_idx, (
                f"{table}: index/unique-constraint drift, migrated={m_idx} models={s_idx}"
            )
    finally:
        migrated_engine.dispose()
        model_engine.dispose()


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

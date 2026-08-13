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


def test_upgrade_preserves_a_populated_predecessor_database(tmp_path):
    """
    Closes a verification gap `test_migrated_schema_matches_the_model_schema`
    (above) does NOT cover: that test builds an EMPTY database and diffs
    reflected schemas, which proves 0005 *describes* the right tables but
    proves nothing about running it against a database that already has rows
    — the only path an installed user's `alembic upgrade head` ever takes.
    That was previously checked by hand, once, against a real 0004-era
    database supplied out-of-band; the database lived in a temp directory and
    is gone, so the check was not repeatable. This test generates its own.

    Mechanics: build the pre-migration schema by running the REAL migration
    chain up to (not including) the migration under test — not a hand-rolled
    copy of `db/models.py`, which would silently drift the day a FUTURE
    migration changes a column on one of these tables instead of only adding
    a table. Seed real rows (valid FKs — SQLite enforces them here via
    `enable_sqlite_foreign_keys`, so a fabricated `created_by` would be
    rejected, not merely wrong-shaped). Stamp + upgrade to head for real.
    Assert alembic_version advanced, the new table exists, and — the
    load-bearing assertion — every pre-existing row is byte-identical
    before/after, not just count-identical: a migration that silently
    recreates a table passes a count check and fails this one.

    Predecessor revision is DERIVED from the chain (`ScriptDirectory`), not
    hardcoded as "0004_scenario_type": `get_current_head()` always names the
    latest migration, and its `down_revision` is always the one before it. So
    when 0006 lands, this test automatically starts exercising 0006's upgrade
    instead of 0005's. `NEW_TABLE_NAME` below is the one piece that genuinely
    cannot be derived — "what table did the latest migration add" is
    migration-specific knowledge — so it is a documented module-local
    constant per the plan's fallback allowance, to be updated alongside any
    migration that changes what's seeded here.
    """
    import uuid
    from datetime import datetime, timedelta, timezone

    from alembic import command
    from alembic.script import ScriptDirectory
    from sqlalchemy import MetaData, select, text
    from sqlalchemy.orm import sessionmaker

    import local_bootstrap
    from db.models import OrgMembership, Organization, Project
    from db.models import Session as SessionRow
    from db.models import User
    from db.session import enable_sqlite_foreign_keys

    # Added by 0005_solve_jobs. Update this alongside the seed data below
    # whenever a later migration becomes the one this test exercises.
    NEW_TABLE_NAME = "solve_jobs"
    PRE_EXISTING_TABLES = ["organizations", "users", "org_memberships", "projects", "sessions"]

    def _dump_rows(engine, table_names):
        """
        Full-row content for `table_names`, ordered by primary key so the
        comparison is stable even if a migration rebuilds a table (SQLite
        batch-alter copies rows into a fresh table and physical order is not
        guaranteed to survive that). Reflected (untyped) columns, deliberately
        — this must not lean on `db/models.py`'s current column types, which
        is exactly the coupling that would let a drifted model paper over a
        real migration bug.
        """
        md = MetaData()
        md.reflect(bind=engine, only=table_names)
        with engine.connect() as conn:
            return {
                name: [
                    dict(row)
                    for row in conn.execute(
                        select(md.tables[name]).order_by(*md.tables[name].primary_key.columns)
                    ).mappings()
                ]
                for name in table_names
            }

    url = f"sqlite+pysqlite:///{(tmp_path / 'predecessor.db').as_posix()}"
    cfg = local_bootstrap._alembic_config(url)

    script = ScriptDirectory.from_config(cfg)
    head_id = script.get_current_head()
    predecessor = script.get_revision(head_id).down_revision
    assert predecessor is not None, "chain has only one revision — nothing to seed as 'predecessor'"

    # 1+2: a fresh SQLite DB, brought to the schema as it stood immediately
    # before the migration under test, via the real chain.
    engine = create_engine(url)
    enable_sqlite_foreign_keys(engine)
    command.upgrade(cfg, predecessor)

    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    project1_id, project2_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(tz=timezone.utc)
    Session_ = sessionmaker(bind=engine)
    with Session_() as db:
        db.add_all([
            Organization(id=org_id, name="Predecessor Org", created_at=now),
            User(
                id=user_id, email="predecessor-seed@example.com", password_hash=None,
                status="active", is_super_admin=False, created_at=now,
            ),
        ])
        db.flush()
        db.add_all([
            # role="admin" — the pairing `project_acl` actually needs.
            OrgMembership(id=uuid.uuid4(), user_id=user_id, org_id=org_id, role="admin"),
            # Two projects, both with a real `created_by` FK. A fabricated
            # one would be rejected outright: `enable_sqlite_foreign_keys`
            # above turns SQLite's FK enforcement on for this connection.
            Project(
                id=project1_id, org_id=org_id, name="Project One", created_by=user_id,
                storage_path="predecessor-org/project-one", created_at=now, updated_at=now,
            ),
            Project(
                id=project2_id, org_id=org_id, name="Project Two", created_by=user_id,
                storage_path="predecessor-org/project-two", created_at=now, updated_at=now,
            ),
        ])
        db.flush()
        db.add(
            # `active_project_id` is the column CLAUDE.md flags as the one
            # test harnesses forget: it's the field most likely to be
            # disturbed by a migration that rewrites or reindexes.
            SessionRow(
                id=uuid.uuid4(), user_id=user_id, token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                expires_at=now + timedelta(hours=8), revoked_at=None,
                active_project_id=project1_id,
            )
        )
        db.commit()

    with engine.connect() as conn:
        version_before = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version_before == predecessor

    before = _dump_rows(engine, PRE_EXISTING_TABLES)
    assert [len(before[t]) for t in PRE_EXISTING_TABLES] == [1, 1, 1, 2, 1], (
        "seeding produced the wrong row counts — the byte-identical comparison below "
        "would be vacuous against an empty or partially-seeded table"
    )
    engine.dispose()

    # 3: the DB claims to be at the revision before the one under test. A
    # no-op here (the real `upgrade` above already left it there) — spelled
    # out anyway per the verification plan, and it is the call a real
    # "declare this database's revision" bootstrap would use if the
    # pre-migration schema had been built some other way (e.g. `create_all`
    # minus the new table).
    command.stamp(cfg, predecessor)

    # 4: the real upgrade under test.
    command.upgrade(cfg, "head")

    after_engine = create_engine(url)
    try:
        table_names = set(inspect(after_engine).get_table_names())
        assert NEW_TABLE_NAME in table_names, f"{NEW_TABLE_NAME} missing after upgrade to head"

        with after_engine.connect() as conn:
            version_after = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version_after == head_id
        assert version_after != version_before

        after = _dump_rows(after_engine, PRE_EXISTING_TABLES)
    finally:
        after_engine.dispose()

    # The load-bearing assertion: full row content, not counts. A migration
    # that dropped and silently recreated a table (e.g. an errant
    # `batch_alter_table` touching the wrong table) would still pass a count
    # check — new PKs, defaulted columns — and fail this one.
    assert before == after, "0005 did not preserve pre-existing rows byte-for-byte across the upgrade"

    [session_after] = after["sessions"]
    assert session_after["active_project_id"] == project1_id.hex, (
        "sessions.active_project_id was disturbed by the upgrade"
    )


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

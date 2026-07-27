"""relative_storage_path

Phase 1b (spec E2): `Project.storage_path` becomes RELATIVE to
`settings.projects_root`, and gains a uniqueness backstop.

An absolute path bakes one machine's home directory into the database. A
restored backup, a moved app-data directory, or the same project opened on the
other platform this repo targets all resolve to a directory that is not there.
`project_registry.project_dir` rejoins a relative value with the configured
root, and still accepts absolute values permanently — this migration does not
convert every row.

**What this does NOT do.** Rows whose path is outside `projects_root` are left
absolute by design: nothing here can know where the user wants them moved, and
a database rewrite that invalidates a path is worse than one that leaves it
working. On the developer machine that wrote this, the ONE row that exists is
exactly such a row, so this migration does not deliver E2 for it —
`tools/import_legacy --rebase-db` does, as a separate, separately confirmed
step that copies and verifies before it rewrites.

**This migration trusts `PROJECTS_ROOT`, and cannot verify it.** A review
raised the case where the value here is a PARENT of the one the application
runs with — `/data` against `/data/projects`, an init-container whose
environment disagrees. `relative_to` then succeeds and yields
`projects/<org>/<uuid>`, which the app resolves one level too deep, and every
project in the deployment becomes unreachable while alembic reports success.

A round-trip assertion was written for this and then removed: it cannot detect
the case, because rejoining is self-consistent with whatever root the
migration was given. Nothing inside a migration can know the value the
application will later use. What is possible is to make the outcome VISIBLE,
so `_rewrite` logs the root it used and how many of how many rows it changed
— "rewrote 0 of 400 against /data" is the signal, and it was previously
indistinguishable from a clean no-op. Run this with the same environment the
app runs with; the documented `cd backend && alembic upgrade head` flow does.

**Order inside `upgrade` is load-bearing:** rewrite the paths first, create the
unique index second. The reverse fails on any database whose rows already
collide before the rewrite, and the point of the index is to catch collisions
the rewrite could otherwise hide.

Revision ID: 0003_relative_storage_path
Revises: 0002_session_active_project
Create Date: 2026-07-27 00:00:00.000000
"""

import logging
from collections.abc import Sequence
from pathlib import Path, PurePath

import sqlalchemy as sa
from alembic import op

from settings import get_settings

# revision identifiers, used by Alembic.
revision: str = "0003_relative_storage_path"
down_revision: str | None = "0002_session_active_project"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOG = logging.getLogger("alembic.runtime.migration")

INDEX_NAME = "uq_projects_org_id_storage_path"

# Untyped columns on purpose. Declaring `id` as `sa.Uuid` makes SQLAlchemy
# re-encode the value on the way back into the WHERE clause — on SQLite that is
# 32 hex characters with no dashes, while 0001 stored the dashed form, so every
# UPDATE matches zero rows and the migration silently does nothing.
_PROJECTS = sa.table("projects", sa.column("id"), sa.column("storage_path"))


def _root() -> PurePath:
    return PurePath(str(get_settings().projects_root))


def _rewrite(conn, transform, *, direction: str) -> int:
    rows = conn.execute(sa.select(_PROJECTS.c.id, _PROJECTS.c.storage_path)).all()
    changed = 0
    for row_id, storage_path in rows:
        new_value = transform(storage_path)
        if new_value is None or new_value == storage_path:
            continue
        changed += 1
        # `CAST(id AS TEXT)` so the comparison is text-to-text on both backends:
        # SQLite stores the dashed string, Postgres has a real `uuid` column
        # that will not compare against a text parameter without the cast.
        conn.execute(
            _PROJECTS.update()
            .where(sa.cast(_PROJECTS.c.id, sa.Text) == str(row_id))
            .values(storage_path=new_value)
        )
    _LOG.info(
        "0003 %s: rewrote %d of %d project path(s) against root %s",
        direction, changed, len(rows), _root(),
    )
    return changed


def upgrade() -> None:
    conn = op.get_bind()
    root = _root()

    def to_relative(storage_path: str) -> str | None:
        path = PurePath(storage_path)
        if not path.is_absolute():
            return None                    # already relative — idempotent
        try:
            # `relative_to` rather than string prefixing: `/x/Projects2` must
            # not be treated as living under `/x/Projects`.
            # POSIX separators always — a database written on Windows and read
            # on macOS otherwise resolves to one directory with a backslash in
            # its name, which is the portability E2 exists to provide.
            relative = path.relative_to(root).as_posix()
        except ValueError:
            return None                    # outside the root — left alone
        return relative

    _rewrite(conn, to_relative, direction="upgrade")

    # Tolerate an index that already exists — this migration is run twice by
    # its own idempotence test, and on Postgres a `create_all`-built database
    # reports the constraint's backing index here by name.
    #
    # NOT on SQLite: there `get_indexes` returns only the explicit indexes and
    # the model's `UniqueConstraint` shows up under `get_unique_constraints`
    # instead, so this check does not match and a second, redundant unique
    # index is created. Harmless — both reject the same rows — but the stated
    # mechanism is not the one operating, so do not read this as "SQLite is
    # covered".
    existing = {ix["name"] for ix in sa.inspect(conn).get_indexes("projects")}
    if INDEX_NAME not in existing:
        op.create_index(
            INDEX_NAME, "projects", ["org_id", "storage_path"], unique=True
        )


def downgrade() -> None:
    """
    Re-absolutise, and DROP THE INDEX.

    The rollback procedure runs this, so it is load-bearing for recovery. It
    must drop the index as well as rewrite the paths — otherwise a later
    re-`upgrade` fails on `create_index` against an index that is still there,
    and the database is stuck between two revisions.
    """
    conn = op.get_bind()
    root = _root()

    existing = {ix["name"] for ix in sa.inspect(conn).get_indexes("projects")}
    if INDEX_NAME in existing:
        op.drop_index(INDEX_NAME, table_name="projects")

    def to_absolute(storage_path: str) -> str | None:
        path = PurePath(storage_path)
        if path.is_absolute():
            return None
        return str(Path(root) / path)

    _rewrite(conn, to_absolute, direction="downgrade")

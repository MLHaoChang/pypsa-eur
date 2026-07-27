"""session_active_project

Step 0b of the cloud/SaaS migration: move the "active project" pointer from a
process global into the session row.

Reversible and idempotent-safe: the column is nullable with no default, so an
existing session simply starts unbound (the New Project state, which is the
normal first-load state anyway) and re-binds the next time the user opens a
project. No data migration is needed and no session is invalidated.

Revision ID: 0002_session_active_project
Revises: 0001_tenancy
Create Date: 2026-07-26 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_session_active_project"
down_revision: str | None = "0001_tenancy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `batch_alter_table` for SQLite: it cannot ADD a column carrying a foreign
    # key in place, so Alembic rebuilds the table. Postgres takes the fast path
    # and the same code works on both — which matters because SQLite is the
    # documented local fallback (`db/session.py`).
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(
            sa.Column("active_project_id", sa.Uuid(as_uuid=True), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_sessions_active_project_id_projects",
            "projects",
            ["active_project_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        op.f("ix_sessions_active_project_id"),
        "sessions",
        ["active_project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sessions_active_project_id"), table_name="sessions")
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_constraint(
            "fk_sessions_active_project_id_projects", type_="foreignkey"
        )
        batch_op.drop_column("active_project_id")

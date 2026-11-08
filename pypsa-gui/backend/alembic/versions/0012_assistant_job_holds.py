"""assistant_job_holds

One row per assistant job call whose outcome is unknown (assistant-assurance
issue 16): the project, the tool, the call's G3 fingerprint, its job id when
known, when and by whom it was opened, and the process that holds its
evidence. A new chat, tab, replica or restart reads it before starting the
same operation again.

No backfill: holds opened before this migration lived only on their chat.

Revision ID: 0012_assistant_job_holds
Revises: 0011_solve_job_capture
Create Date: 2026-11-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_assistant_job_holds"
down_revision: str | None = "0011_solve_job_capture"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_job_holds",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("project_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("job_id", sa.String(128), nullable=True),
        sa.Column("opened", sa.String(32), nullable=False),
        sa.Column("opened_at", sa.Float(), nullable=False),
        sa.Column("opened_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("owner_replica_id", sa.String(32), nullable=False),
        sa.Column("lease_until", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["opened_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_assistant_job_holds_project_id", "assistant_job_holds", ["project_id"]
    )


def downgrade() -> None:
    """Drop the records. Holds then live only on their chat, as before."""
    op.drop_index("ix_assistant_job_holds_project_id", "assistant_job_holds")
    op.drop_table("assistant_job_holds")

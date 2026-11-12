"""memory_dream_jobs

Assistant memory slice 04 (assistant-memory plan §6, §9 M7): one row per dream
job, the durable state of `harness/dream.py`. A partial unique index keeps one
queued or running job per user (GK B D4).

Revision ID: 0014_memory_dream_jobs
Revises: 0013_memory_items
Create Date: 2026-11-11 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_memory_dream_jobs"
down_revision: str | None = "0013_memory_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LIVE = sa.text("status IN ('queued', 'running')")


def upgrade() -> None:
    op.create_table(
        "memory_dream_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("cursor_json", sa.Text(), nullable=False),
        sa.Column("candidates_json", sa.Text(), nullable=False),
        sa.Column("budget_json", sa.Text(), nullable=False),
        sa.Column("spent_json", sa.Text(), nullable=False),
        sa.Column("counts_json", sa.Text(), nullable=False),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_until", sa.Float(), nullable=True),
        sa.Column("restarts", sa.Integer(), nullable=False),
        sa.Column("restart_times_json", sa.Text(), nullable=False),
        sa.Column("error_kind", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ux_memory_dream_jobs_live_user",
        "memory_dream_jobs",
        ["user_id"],
        unique=True,
        sqlite_where=_LIVE,
        postgresql_where=_LIVE,
    )
    op.create_index(
        "ix_memory_dream_jobs_user_created",
        "memory_dream_jobs",
        ["user_id", "created_at"],
    )
    op.create_index("ix_memory_dream_jobs_status", "memory_dream_jobs", ["status"])


def downgrade() -> None:
    """Drop the table. Job history is lost; proposals already written stay."""
    op.drop_index("ix_memory_dream_jobs_status", "memory_dream_jobs")
    op.drop_index("ix_memory_dream_jobs_user_created", "memory_dream_jobs")
    op.drop_index("ux_memory_dream_jobs_live_user", "memory_dream_jobs")
    op.drop_table("memory_dream_jobs")

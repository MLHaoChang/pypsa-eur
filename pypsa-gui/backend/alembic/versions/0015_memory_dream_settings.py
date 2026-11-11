"""memory_dream_settings, dream job trigger keys

Assistant memory slice 04b (assistant-memory plan §6, GK B 48): a user's
opt-in for the nightly and after-10-chats dream triggers (both default off; no
row is off), and on `memory_dream_jobs` the trigger occasion key (unique per
user, T1), the org a job binds to (T3) and the model it ran on (T7).

Revision ID: 0015_memory_dream_settings
Revises: 0014_memory_dream_jobs
Create Date: 2026-11-11 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_memory_dream_settings"
down_revision: str | None = "0014_memory_dream_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEYED = sa.text("trigger_key IS NOT NULL")


def upgrade() -> None:
    op.create_table(
        "memory_dream_settings",
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("nightly", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "after_sessions", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("org_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("after_sessions_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
    )
    # Plain nullable columns: no batch copy, so the live-job partial index of
    # 0014 is left as it is.
    op.add_column("memory_dream_jobs", sa.Column("trigger_key", sa.String(96)))
    op.add_column("memory_dream_jobs", sa.Column("org_id", sa.Uuid(as_uuid=True)))
    op.add_column("memory_dream_jobs", sa.Column("analyzer_model", sa.String(200)))
    op.create_index(
        "ux_memory_dream_jobs_trigger_key",
        "memory_dream_jobs",
        ["user_id", "trigger_key"],
        unique=True,
        sqlite_where=_KEYED,
        postgresql_where=_KEYED,
    )


def downgrade() -> None:
    """Drop the opt-in table and the job columns: automatic reviews go back to
    off; job history keeps its other fields."""
    op.drop_index("ux_memory_dream_jobs_trigger_key", "memory_dream_jobs")
    op.drop_column("memory_dream_jobs", "analyzer_model")
    op.drop_column("memory_dream_jobs", "org_id")
    op.drop_column("memory_dream_jobs", "trigger_key")
    op.drop_table("memory_dream_settings")

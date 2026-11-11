"""memory_dream_settings

Assistant memory slice 04b (assistant-memory plan §6): a user's opt-in for the
nightly and after-10-chats dream triggers. Both default off; no row is off.

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


def upgrade() -> None:
    op.create_table(
        "memory_dream_settings",
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("nightly", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "after_sessions", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("after_sessions_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    """Drop the table: every user's automatic reviews go back to off."""
    op.drop_table("memory_dream_settings")

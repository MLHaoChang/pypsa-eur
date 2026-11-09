"""memory_items

Assistant memory (assistant-memory plan §1, slice 02a): one row per item
(`memory_items`), one immutable row per edit of its body
(`memory_item_versions`), and the per-user and per-org revision counters the
standing prompt block is keyed on (`memory_revisions`, §9 M5).

No backfill: memory starts empty.

Revision ID: 0013_memory_items
Revises: 0012_assistant_job_holds
Create Date: 2026-11-09 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_memory_items"
down_revision: str | None = "0012_assistant_job_holds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_items",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("owner_scope", sa.String(8), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("org_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("project_kind", sa.String(32), nullable=True),
        sa.Column("concept_keys", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("promoted_from", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("nomination", sa.String(16), nullable=True),
        sa.Column("recall_count", sa.Integer(), nullable=False),
        sa.Column("apply_count", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_hash", sa.String(64), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_memory_items_user_status", "memory_items", ["user_id", "status"]
    )
    op.create_index(
        "ix_memory_items_org_scope_status",
        "memory_items",
        ["org_id", "owner_scope", "status"],
    )
    op.create_index(
        "ix_memory_items_org_kind", "memory_items", ["org_id", "project_kind"]
    )
    op.create_table(
        "memory_item_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("item_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("body_json", sa.Text(), nullable=True),
        sa.Column("summary", sa.String(160), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("edited_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "version"),
        sa.ForeignKeyConstraint(["item_id"], ["memory_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["edited_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_table(
        "memory_revisions",
        sa.Column("scope_key", sa.String(48), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("scope_key"),
    )


def downgrade() -> None:
    """Drop the tables. Every memory item is lost; nothing else reads them."""
    op.drop_table("memory_revisions")
    op.drop_table("memory_item_versions")
    op.drop_index("ix_memory_items_org_kind", "memory_items")
    op.drop_index("ix_memory_items_org_scope_status", "memory_items")
    op.drop_index("ix_memory_items_user_status", "memory_items")
    op.drop_table("memory_items")

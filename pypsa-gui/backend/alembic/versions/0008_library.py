"""library_items

The org-scoped Library of the Edge Investment Case (plan P1 WP1.1a; spec
decision 17): versioned, content-addressed items — price curves, envelopes,
meter history, forecast pairs (tariffs and contracts join in P2). A row is one
immutable VERSION; the payload lives on disk under the hidden
`<projects_root>/.library/<org_id>/` directory and `path` is relative to it.

Additive only: a new table, no change to any existing row, so the downgrade is
a plain drop. Rolling back past this revision loses Library METADATA (the files
stay on disk and can be re-registered), which is why the downgrade says so
rather than pretending to be lossless.

Revision ID: 0008_library
Revises: 0007_solve_job_kind
Create Date: 2026-09-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_library"
down_revision: str | None = "0007_solve_job_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "library_items",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("org_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("meta_json", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "kind", "name", "version"),
    )
    op.create_index(op.f("ix_library_items_org_id"), "library_items", ["org_id"], unique=False)


def downgrade() -> None:
    """Drop the table. Library files on disk are left in place (metadata only)."""
    op.drop_index(op.f("ix_library_items_org_id"), table_name="library_items")
    op.drop_table("library_items")

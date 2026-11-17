"""project_memberships.role, the read-only project viewer role

Project-viewer-role 01 (owner decision VIEWER-ROLE-1): a project member is an
'editor' (every existing row, and the default) or a 'viewer'. The column is
nullable on purpose: `project_acl.project_role` reads NULL or any unknown value
as view-only, so a damaged row fails closed (GK B R6).

Revision ID: 0016_project_membership_role
Revises: 0015_memory_dream_settings
Create Date: 2026-11-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_project_membership_role"
down_revision: str | None = "0015_memory_dream_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("project_memberships") as batch:
        batch.add_column(
            sa.Column("role", sa.String(16), nullable=True, server_default="editor")
        )


def downgrade() -> None:
    with op.batch_alter_table("project_memberships") as batch:
        batch.drop_column("role")

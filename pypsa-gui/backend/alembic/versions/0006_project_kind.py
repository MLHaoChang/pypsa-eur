"""project_kind

`Project.project_kind` records WHAT KIND of study a project is:
`capacity_expansion` (the existing flow), `planning_dynamics` (gridspine's
planning → dynamics chain), or `connection` (later). It is what lets the
router and the copilot serve a project the operations that apply to it —
gridspine's actions refuse a capacity-expansion project, and the chat tool
list is filtered by kind rather than by hope.

**No backfill, deliberately.** NULL means `capacity_expansion`, and
`services/gridspine_service.kind_of` resolves it there. Writing the literal
into every existing row would invent a distinction the data does not have —
between a project whose owner chose the default and one that predates the
column — and would make the downgrade lossy in the other direction. The
column is nullable for the same reason: a row created by any of the four
existing write paths (create_root, create_scenario, the bundle importer, the
legacy importer) is a capacity-expansion project without those paths having
to learn a new field.

**Orthogonal to `scenario_type`.** That column says whether a project is a
baseline, a scenario or a stress case; this one says which pipeline runs. A
planning → dynamics project can be a stress scenario, and migration 0004's
category vocabulary is untouched.

Revision ID: 0006_project_kind
Revises: 0005_solve_jobs
Create Date: 2026-09-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_project_kind"
down_revision: str | None = "0005_solve_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects", sa.Column("project_kind", sa.String(length=32), nullable=True)
    )


def downgrade() -> None:
    """Drop the column.

    Lossy on purpose and worth stating: a planning → dynamics project reverts
    to indistinguishable from a capacity-expansion one, and its `gridspine/`
    directory is left where it is. The artifacts survive the rollback; only
    the label the UI filters on is gone, and re-applying the migration and
    re-labelling the project restores it.
    """
    op.drop_column("projects", "project_kind")

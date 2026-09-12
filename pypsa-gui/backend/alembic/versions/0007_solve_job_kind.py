"""solve_job_kind

`solve_jobs.kind` records what a queued job RUNS: `solve` (a PyPSA network
solve) or `gridspine` (a planning → dynamics study). The queue dispatches on
it, so the column is what stops a restart from turning a study into a network
solve — `SolveQueue.restore` rebuilds queued jobs from these rows, and without
the kind it would rebuild every one of them as a solve and hand a study
directory to the LP path.

**No backfill, deliberately** — the same reasoning as 0006. NULL means
`solve`: every row written before this column is a network solve, and
`SolveQueue.restore` resolves NULL there, so writing the literal would only
invent a distinction between "chose the default" and "predates the column".

**Rows are transient in a way project rows are not.** A queued job is minutes
old and a terminal one is a receipt; `clear_finished` deletes them wholesale.
So unlike 0006 there is no long tail of rows whose meaning this column has to
preserve — which is why it can be added and dropped without touching data at
all.

Revision ID: 0007_solve_job_kind
Revises: 0006_project_kind
Create Date: 2026-09-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_solve_job_kind"
down_revision: str | None = "0006_project_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "solve_jobs", sa.Column("kind", sa.String(length=32), nullable=True)
    )


def downgrade() -> None:
    """Drop the column.

    A queued gridspine job that survives the rollback restores as a `solve`
    and would be handed to the LP path with a study directory. Rolling back
    past this revision should therefore be done with the queue drained — the
    same care any rollback of an in-flight work table needs, stated here
    because the failure would otherwise show up as a confusing solve error
    rather than as a migration consequence.
    """
    op.drop_column("solve_jobs", "kind")

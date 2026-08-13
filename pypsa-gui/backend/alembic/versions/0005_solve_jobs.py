"""solve_jobs

Persist the solve queue. Until now it was a process-local dict: ids came from
`itertools.count(1)`, a restart lost every queued job with no trace, and two
replicas both issued id 1.

Reversible and safe to re-run in the only sense that matters here: `upgrade()`
creates a table that did not exist, so there is no data migration and no
backfill. Calling it twice fails on `create_table` with "table solve_jobs
already exists", which alembic never does on its own but which matters to
anyone hand-repairing a half-applied revision: drop the table first, or stamp.

The primary key is a UUID, matching every other model in `db/models.py`
(`Uuid(as_uuid=True)`), rather than the integer the in-memory queue used. Done
now, while the table is being created, rather than migrating a populated one
later — and it is what stops two replicas colliding on id 1 the moment job rows
outlive the process.

Revision ID: 0005_solve_jobs
Revises: 0004_scenario_type
Create Date: 2026-08-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_solve_jobs"
down_revision: str | None = "0004_scenario_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "solve_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("project_key", sa.String(length=128), nullable=True),
        sa.Column("storage_dir", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("enqueued_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("solver_config", sa.Text(), nullable=True),
        sa.Column("objective", sa.Float(), nullable=True),
        sa.Column("solve_time", sa.Float(), nullable=True),
        sa.Column("condition", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        # SET NULL, not CASCADE: deleting a user must not delete the audit of
        # what they queued, and a job orphaned of its enqueuer is still a job
        # the operator needs to see.
        sa.ForeignKeyConstraint(
            ["enqueued_by_user_id"], ["users.id"],
            name="fk_solve_jobs_enqueued_by_user_id_users", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["dismissed_by_user_id"], ["users.id"],
            name="fk_solve_jobs_dismissed_by_user_id_users", ondelete="SET NULL",
        ),
    )
    op.create_index(op.f("ix_solve_jobs_project_key"), "solve_jobs", ["project_key"], unique=False)
    op.create_index(op.f("ix_solve_jobs_status"), "solve_jobs", ["status"], unique=False)
    op.create_index(
        op.f("ix_solve_jobs_enqueued_by_user_id"), "solve_jobs", ["enqueued_by_user_id"], unique=False,
    )
    op.create_index(
        op.f("ix_solve_jobs_dismissed_by_user_id"), "solve_jobs", ["dismissed_by_user_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_solve_jobs_dismissed_by_user_id"), table_name="solve_jobs")
    op.drop_index(op.f("ix_solve_jobs_enqueued_by_user_id"), table_name="solve_jobs")
    op.drop_index(op.f("ix_solve_jobs_status"), table_name="solve_jobs")
    op.drop_index(op.f("ix_solve_jobs_project_key"), table_name="solve_jobs")
    op.drop_table("solve_jobs")

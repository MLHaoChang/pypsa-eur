"""solve_job_capture

`solve_jobs.capture` holds the captured input of an envelope-admitted queued
solve (study harness issue 05, HH-10): the capture's directory, its identity
(the sha256 of its manifest) and each file's sha256, as JSON. Pickup checks
the capture against this identity, so it lives on the row, not only beside
the files it describes.

No backfill: NULL means an ordinary solve, which every earlier row is.

Revision ID: 0011_solve_job_capture
Revises: 0010_assurance_record_seq
Create Date: 2026-11-04 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_solve_job_capture"
down_revision: str | None = "0010_assurance_record_seq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("solve_jobs", sa.Column("capture", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the column. A queued captured job that survives the rollback
    restores as an ordinary solve of the project on disk: drain the queue
    first."""
    op.drop_column("solve_jobs", "capture")

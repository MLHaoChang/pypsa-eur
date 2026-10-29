"""Add the high-water mark of mutable assurance records (issue 02, REPLAY).

No backfill: a mark copied from the record store would trust a store that may
have been rolled back. Mutable records saved before this migration read as
unavailable until rewritten; they expire within 24 hours.
"""
import sqlalchemy as sa
from alembic import op

revision = '0010_assurance_record_seq'
down_revision = '0009_chat_restrictions'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'assurance_record_seq',
        sa.Column('owner_id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('record_id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('epoch', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('expires_at', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('owner_id', 'record_id'),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    )


def downgrade():
    """Drop the marks. Mutable records then read without a replay check."""
    op.drop_table('assurance_record_seq')

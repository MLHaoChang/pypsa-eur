"""Add current personal project chat restrictions; existing rows are unchanged."""
import sqlalchemy as sa
from alembic import op

revision = '0009_chat_restrictions'
down_revision = '0008_library'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'chat_restrictions',
        sa.Column('user_id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('project_id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('revision', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('mode', sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint('user_id', 'project_id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.CheckConstraint("mode IN ('standard', 'confirm_changes', 'read_only')",
                           name='ck_chat_restrictions_mode'),
    )


def downgrade():
    """Remove restrictions metadata. Downgrade loses these personal settings."""
    op.drop_table('chat_restrictions')

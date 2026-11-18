"""link queue: pasted links imported one at a time when the post schedule runs low

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('link_queue_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('url', sa.String(length=500), nullable=False),
    sa.Column('platform', sa.String(length=10), nullable=False),
    sa.Column('external_id', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('tries', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'url')
    )
    with op.batch_alter_table('link_queue_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_link_queue_items_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_link_queue_items_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_link_queue_items_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_link_queue_items_project_id'), ['project_id'], unique=False)


def downgrade():
    op.drop_table('link_queue_items')

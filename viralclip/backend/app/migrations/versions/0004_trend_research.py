"""trend research cache and daily search counter

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('trend_cache',
    sa.Column('key', sa.String(length=300), nullable=False),
    sa.Column('results', sa.JSON(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('research_usage',
    sa.Column('day', sa.String(length=10), nullable=False),
    sa.Column('searches', sa.Integer(), nullable=False),
    sa.Column('quota_exceeded', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('day')
    )


def downgrade():
    op.drop_table('research_usage')
    op.drop_table('trend_cache')

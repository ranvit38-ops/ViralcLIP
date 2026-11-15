"""link import from TikTok + view-count stats of published clips

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('youtube_accounts') as b:
        b.add_column(sa.Column('stats_synced_at', sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table('youtube_uploads') as b:
        b.add_column(sa.Column('view_count', sa.BigInteger(), nullable=True))
        b.add_column(sa.Column('like_count', sa.BigInteger(), nullable=True))
        b.add_column(sa.Column('stats_synced_at', sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table('tiktok_accounts') as b:
        b.add_column(sa.Column('scopes', sa.Text(), nullable=True))
        b.add_column(sa.Column('stats_synced_at', sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table('tiktok_posts') as b:
        b.add_column(sa.Column('tiktok_video_id', sa.String(length=32), nullable=True))
        b.add_column(sa.Column('view_count', sa.BigInteger(), nullable=True))
        b.add_column(sa.Column('like_count', sa.BigInteger(), nullable=True))
        b.add_column(sa.Column('stats_synced_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table('tiktok_posts') as b:
        for c in ('stats_synced_at', 'like_count', 'view_count', 'tiktok_video_id'):
            b.drop_column(c)
    with op.batch_alter_table('tiktok_accounts') as b:
        b.drop_column('stats_synced_at')
        b.drop_column('scopes')
    with op.batch_alter_table('youtube_uploads') as b:
        for c in ('stats_synced_at', 'like_count', 'view_count'):
            b.drop_column(c)
    with op.batch_alter_table('youtube_accounts') as b:
        b.drop_column('stats_synced_at')

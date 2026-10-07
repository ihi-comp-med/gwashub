"""project-defined metadata fields

Revision ID: e5f2c8a17d39
Revises: d1e7a3b95c20
Create Date: 2026-10-06 23:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'e5f2c8a17d39'
down_revision = 'd1e7a3b95c20'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('project', schema=None) as batch_op:
        batch_op.add_column(sa.Column('metadata_custom', sa.JSON(), server_default='[]', nullable=False))


def downgrade():
    with op.batch_alter_table('project', schema=None) as batch_op:
        batch_op.drop_column('metadata_custom')

"""meta-analysis method

Revision ID: 9b3e1c7d2a40
Revises: 72f57b95b9a1
Create Date: 2026-10-04 16:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '9b3e1c7d2a40'
down_revision = '72f57b95b9a1'
branch_labels = None
depends_on = None


def upgrade():
    # Every meta-analysis run before there were several methods used GWASHub's inverse-variance method.
    with op.batch_alter_table('meta_analysis', schema=None) as batch_op:
        batch_op.add_column(sa.Column('method', sa.String(length=200), server_default='gwashub/gwama', nullable=False))


def downgrade():
    with op.batch_alter_table('meta_analysis', schema=None) as batch_op:
        batch_op.drop_column('method')

"""AWS Batch state of the job working on a QC run or meta-analysis

Revision ID: d1e7a3b95c20
Revises: c4d8e2f61b07
Create Date: 2026-10-06 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'd1e7a3b95c20'
down_revision = 'c4d8e2f61b07'
branch_labels = None
depends_on = None


def upgrade():
    for table in ('qc_run', 'meta_analysis'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('batch_state', sa.JSON(), nullable=True))


def downgrade():
    for table in ('qc_run', 'meta_analysis'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column('batch_state')

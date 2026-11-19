"""phenotype trait types and total sample size

Revision ID: c4d8e2f61b07
Revises: 9b3e1c7d2a40
Create Date: 2026-10-06 11:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

from gwashub_api.dbtypes import column_default


revision = 'c4d8e2f61b07'
down_revision = '9b3e1c7d2a40'
branch_labels = None
depends_on = None


def upgrade():
    # Every phenotype so far is case/control (an empty map means all binary); n_total = cases + controls.
    with op.batch_alter_table('project', schema=None) as batch_op:
        batch_op.add_column(sa.Column('phenotype_types', sa.JSON(), server_default=column_default('{}', op.get_context().dialect.name), nullable=False))
    with op.batch_alter_table('dataset', schema=None) as batch_op:
        batch_op.add_column(sa.Column('n_total', sa.Integer(), nullable=True))
    op.execute("UPDATE dataset SET n_total = n_cases + n_controls WHERE n_cases IS NOT NULL AND n_controls IS NOT NULL")


def downgrade():
    with op.batch_alter_table('dataset', schema=None) as batch_op:
        batch_op.drop_column('n_total')
    with op.batch_alter_table('project', schema=None) as batch_op:
        batch_op.drop_column('phenotype_types')

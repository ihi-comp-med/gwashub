"""personal API tokens for the upload script; datasets record the token they came with and the file's sha256

Revision ID: a3c9e1f05b72
Revises: f7a1d4e9b362
Create Date: 2026-10-07 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

from gwashub_api.dbtypes import migration_datetime


revision = 'a3c9e1f05b72'
down_revision = 'f7a1d4e9b362'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('api_token',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('user_id', sa.Integer(), nullable=False),
                    sa.Column('project_id', sa.String(length=50), nullable=False),
                    sa.Column('name', sa.String(length=100), nullable=False),
                    sa.Column('token_hash', sa.String(length=64), nullable=False),
                    sa.Column('prefix', sa.String(length=12), nullable=False),
                    sa.Column('created_at', migration_datetime(), nullable=False),
                    sa.Column('expires_at', migration_datetime(), nullable=False),
                    sa.Column('revoked_at', migration_datetime(), nullable=True),
                    sa.Column('last_used_at', migration_datetime(), nullable=True),
                    sa.ForeignKeyConstraint(['user_id'], ['user.id']),
                    sa.ForeignKeyConstraint(['project_id'], ['project.id']),
                    sa.PrimaryKeyConstraint('id'),
                    sa.UniqueConstraint('token_hash'))
    op.create_index('ix_api_token_user_id', 'api_token', ['user_id'])
    op.create_index('ix_api_token_project_id', 'api_token', ['project_id'])
    with op.batch_alter_table('dataset', schema=None) as batch_op:
        batch_op.add_column(sa.Column('uploaded_with_token', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('sha256', sa.String(length=64), nullable=False, server_default=''))
        batch_op.create_foreign_key('fk_dataset_api_token', 'api_token', ['uploaded_with_token'], ['id'], ondelete='SET NULL')


def downgrade():
    with op.batch_alter_table('dataset', schema=None) as batch_op:
        batch_op.drop_constraint('fk_dataset_api_token', type_='foreignkey')
        batch_op.drop_column('sha256')
        batch_op.drop_column('uploaded_with_token')
    op.drop_index('ix_api_token_project_id', table_name='api_token')
    op.drop_index('ix_api_token_user_id', table_name='api_token')
    op.drop_table('api_token')

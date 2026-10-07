"""several named QC pipelines per project

Revision ID: f7a1d4e9b362
Revises: e5f2c8a17d39
Create Date: 2026-10-07 00:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'f7a1d4e9b362'
down_revision = 'e5f2c8a17d39'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('pipeline',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('project_id', sa.String(length=50), nullable=False),
                    sa.Column('name', sa.String(length=100), nullable=False),
                    sa.Column('description', sa.Text(), nullable=False, server_default=''),
                    sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.false()),
                    sa.Column('created_by', sa.Integer(), nullable=True),
                    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
                    sa.ForeignKeyConstraint(['project_id'], ['project.id']),
                    sa.ForeignKeyConstraint(['created_by'], ['user.id']),
                    sa.PrimaryKeyConstraint('id'),
                    sa.UniqueConstraint('project_id', 'name'))
    with op.batch_alter_table('pipeline_version', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pipeline_id', sa.Integer(), nullable=True))
        batch_op.create_index('ix_pipeline_version_pipeline_id', ['pipeline_id'])
        batch_op.create_foreign_key('fk_pipeline_version_pipeline', 'pipeline', ['pipeline_id'], ['id'])
    with op.batch_alter_table('dataset', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pipeline_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_dataset_pipeline', 'pipeline', ['pipeline_id'], ['id'])
    # Every project's existing versions become its "Default" pipeline.
    op.execute("INSERT INTO pipeline (project_id, name, description, is_default, created_at) "
               "SELECT id, 'Default', '', true, created_at FROM project")
    op.execute("UPDATE pipeline_version SET pipeline_id = "
               "(SELECT p.id FROM pipeline p WHERE p.project_id = pipeline_version.project_id AND p.is_default)")


def downgrade():
    with op.batch_alter_table('dataset', schema=None) as batch_op:
        batch_op.drop_constraint('fk_dataset_pipeline', type_='foreignkey')
        batch_op.drop_column('pipeline_id')
    with op.batch_alter_table('pipeline_version', schema=None) as batch_op:
        batch_op.drop_constraint('fk_pipeline_version_pipeline', type_='foreignkey')
        batch_op.drop_index('ix_pipeline_version_pipeline_id')
        batch_op.drop_column('pipeline_id')
    op.drop_table('pipeline')

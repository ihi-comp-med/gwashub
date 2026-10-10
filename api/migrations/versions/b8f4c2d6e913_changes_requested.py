"""Changes requested: its own dataset status, and a replacement file upload in progress

Revision ID: b8f4c2d6e913
Revises: a3c9e1f05b72
Create Date: 2026-10-10 23:00:00.000000
"""
import json

from alembic import op
import sqlalchemy as sa


revision = 'b8f4c2d6e913'
down_revision = 'a3c9e1f05b72'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('dataset', schema=None) as batch_op:
        batch_op.add_column(sa.Column('replacement', sa.JSON(), nullable=True))
    # Datasets a reviewer sent back were stored as "failed": give them the new status.
    conn = op.get_bind()
    for ds_id, review in conn.execute(sa.text("SELECT id, review FROM dataset WHERE status = 'failed' AND review IS NOT NULL")):
        r = json.loads(review) if isinstance(review, str) else review
        if (r or {}).get("decision") == "request_changes":
            conn.execute(sa.text("UPDATE dataset SET status = 'changes_requested' WHERE id = :id"), {"id": ds_id})


def downgrade():
    op.execute("UPDATE dataset SET status = 'failed' WHERE status = 'changes_requested'")
    with op.batch_alter_table('dataset', schema=None) as batch_op:
        batch_op.drop_column('replacement')

"""one open issue draft per repo

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-18 23:56:40.681343
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('issue_drafts', schema=None) as batch_op:
        batch_op.create_index('ux_issue_drafts_open', ['repo_id'], unique=True, sqlite_where=sa.text("status IN ('drafted','approved','posting')"), postgresql_where=sa.text("status IN ('drafted','approved','posting')"))



def downgrade() -> None:
    with op.batch_alter_table('issue_drafts', schema=None) as batch_op:
        batch_op.drop_index('ux_issue_drafts_open', sqlite_where=sa.text("status IN ('drafted','approved','posting')"), postgresql_where=sa.text("status IN ('drafted','approved','posting')"))


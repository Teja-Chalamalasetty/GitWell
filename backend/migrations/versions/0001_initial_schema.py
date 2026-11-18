"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-11-18 22:22:20.589705
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.Enum('viewer', 'admin', name='userrole', native_enum=False, length=20), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('token_version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_table('repos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('github_id', sa.BigInteger(), nullable=False),
    sa.Column('url', sa.String(length=300), nullable=False),
    sa.Column('owner', sa.String(length=39), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('full_name', sa.String(length=140), nullable=False),
    sa.Column('default_branch', sa.String(length=255), nullable=False),
    sa.Column('is_private', sa.Boolean(), nullable=False),
    sa.Column('is_archived', sa.Boolean(), nullable=False),
    sa.Column('has_issues', sa.Boolean(), nullable=False),
    sa.Column('added_by_id', sa.Integer(), nullable=True),
    sa.Column('added_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['added_by_id'], ['users.id'], name=op.f('fk_repos_added_by_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_repos')),
    sa.UniqueConstraint('full_name', name=op.f('uq_repos_full_name')),
    sa.UniqueConstraint('github_id', name=op.f('uq_repos_github_id'))
    )
    op.create_table('scans',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('repo_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('queued', 'running', 'completed', 'failed', name='scanstatus', native_enum=False, length=20), nullable=False),
    sa.Column('trigger', sa.Enum('manual', 'scheduled', 'on_add', name='scantrigger', native_enum=False, length=20), nullable=False),
    sa.Column('triggered_by_id', sa.Integer(), nullable=True),
    sa.Column('score', sa.Integer(), nullable=True),
    sa.Column('grade', sa.String(length=1), nullable=True),
    sa.Column('rules_errored', sa.Integer(), nullable=False),
    sa.Column('ruleset_version', sa.String(length=12), nullable=True),
    sa.Column('commit_sha', sa.String(length=64), nullable=True),
    sa.Column('snapshot_mode', sa.String(length=10), nullable=True),
    sa.Column('is_partial', sa.Boolean(), nullable=False),
    sa.Column('error_code', sa.String(length=50), nullable=True),
    sa.Column('error_message', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['repo_id'], ['repos.id'], name=op.f('fk_scans_repo_id_repos'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['triggered_by_id'], ['users.id'], name=op.f('fk_scans_triggered_by_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scans'))
    )
    with op.batch_alter_table('scans', schema=None) as batch_op:
        batch_op.create_index('ix_scans_repo_status_created', ['repo_id', 'status', 'created_at'], unique=False)
        batch_op.create_index('ix_scans_status', ['status'], unique=False)
        batch_op.create_index('ux_scans_active', ['repo_id'], unique=True, sqlite_where=sa.text("status IN ('queued','running')"), postgresql_where=sa.text("status IN ('queued','running')"))

    op.create_table('issue_drafts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('scan_id', sa.Integer(), nullable=False),
    sa.Column('repo_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=256), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('drafted', 'approved', 'posting', 'posted', 'discarded', name='draftstatus', native_enum=False, length=20), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('approved_by_id', sa.Integer(), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('post_attempts', sa.Integer(), nullable=False),
    sa.Column('github_issue_number', sa.Integer(), nullable=True),
    sa.Column('github_issue_url', sa.String(length=300), nullable=True),
    sa.Column('last_error', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['approved_by_id'], ['users.id'], name=op.f('fk_issue_drafts_approved_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_issue_drafts_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['repo_id'], ['repos.id'], name=op.f('fk_issue_drafts_repo_id_repos'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['scan_id'], ['scans.id'], name=op.f('fk_issue_drafts_scan_id_scans'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_issue_drafts'))
    )
    with op.batch_alter_table('issue_drafts', schema=None) as batch_op:
        batch_op.create_index('ix_issue_drafts_repo_status', ['repo_id', 'status'], unique=False)
        batch_op.create_index('ix_issue_drafts_scan_id', ['scan_id'], unique=False)

    op.create_table('scan_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('scan_id', sa.Integer(), nullable=False),
    sa.Column('rule_id', sa.String(length=50), nullable=False),
    sa.Column('status', sa.Enum('pass', 'warn', 'fail', 'not_applicable', 'error', name='rulestatus', native_enum=False, length=20), nullable=False),
    sa.Column('message', sa.String(length=1000), nullable=False),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('weight', sa.Integer(), nullable=False),
    sa.Column('display_order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['scan_id'], ['scans.id'], name=op.f('fk_scan_results_scan_id_scans'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scan_results')),
    sa.UniqueConstraint('scan_id', 'rule_id', name=op.f('uq_scan_results_scan_id'))
    )


def downgrade() -> None:
    op.drop_table('scan_results')
    with op.batch_alter_table('issue_drafts', schema=None) as batch_op:
        batch_op.drop_index('ix_issue_drafts_scan_id')
        batch_op.drop_index('ix_issue_drafts_repo_status')

    op.drop_table('issue_drafts')
    with op.batch_alter_table('scans', schema=None) as batch_op:
        batch_op.drop_index('ux_scans_active', sqlite_where=sa.text("status IN ('queued','running')"), postgresql_where=sa.text("status IN ('queued','running')"))
        batch_op.drop_index('ix_scans_status')
        batch_op.drop_index('ix_scans_repo_status_created')

    op.drop_table('scans')
    op.drop_table('repos')
    op.drop_table('users')

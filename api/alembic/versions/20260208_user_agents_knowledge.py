"""user_agents_knowledge

Add private agent access level, owner_user_id, role permissions JSONB,
knowledge_sources table, and knowledge_source_roles junction table.

Revision ID: 0fd35b896176
Revises: d4f6a8b05e23
Create Date: 2026-02-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = '0fd35b896176'
down_revision: Union[str, None] = 'd4f6a8b05e23'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add 'private' to agent_access_level enum
    # Must use raw connection + autocommit for enum changes
    conn = op.get_bind()
    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text("ALTER TYPE agent_access_level ADD VALUE IF NOT EXISTS 'private'"))

    # 2. Add owner_user_id to agents
    op.add_column('agents', sa.Column('owner_user_id', sa.Uuid(), sa.ForeignKey('users.id'), nullable=True))
    op.create_index('ix_agents_owner_user_id', 'agents', ['owner_user_id'])

    # 3. Add permissions JSONB to roles
    op.add_column('roles', sa.Column('permissions', JSONB, nullable=False, server_default='{}'))

    # 4. Create knowledge_sources table
    op.create_table(
        'knowledge_sources',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('namespace', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('organization_id', sa.Uuid(), sa.ForeignKey('organizations.id'), nullable=True),
        sa.Column('access_level', sa.Enum('authenticated', 'role_based', name='knowledge_source_access_level', create_type=True), nullable=False, server_default='role_based'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('document_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_by', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
    )
    op.create_index('ix_knowledge_sources_organization_id', 'knowledge_sources', ['organization_id'])
    op.create_index('ix_knowledge_sources_namespace_org', 'knowledge_sources', ['namespace', 'organization_id'], unique=True)

    # 5. Create knowledge_source_roles junction table
    op.create_table(
        'knowledge_source_roles',
        sa.Column('knowledge_source_id', sa.Uuid(), sa.ForeignKey('knowledge_sources.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('role_id', sa.Uuid(), sa.ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('assigned_by', sa.String(255), nullable=True),
        sa.Column('assigned_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
    )
    op.create_index('ix_knowledge_source_roles_role_id', 'knowledge_source_roles', ['role_id'])


def downgrade() -> None:
    op.drop_table('knowledge_source_roles')
    op.drop_table('knowledge_sources')
    op.execute("DROP TYPE IF EXISTS knowledge_source_access_level")
    op.drop_column('roles', 'permissions')
    op.drop_index('ix_agents_owner_user_id', table_name='agents')
    op.drop_column('agents', 'owner_user_id')
    # Note: Cannot remove enum value in PostgreSQL

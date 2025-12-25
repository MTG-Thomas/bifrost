"""Add knowledge_store table for RAG

Revision ID: add_knowledge_store
Revises: 84a1938c3c52
Create Date: 2025-12-25

This migration creates:
- pgvector extension for vector similarity search
- knowledge_store table for semantic search/RAG
- Vector index (IVFFlat) for efficient similarity queries
- knowledge_sources column on agents table
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers, used by Alembic.
revision: str = 'add_knowledge_store'
down_revision: Union[str, None] = '84a1938c3c52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create pgvector extension (requires superuser or extension already installed)
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')

    # Create knowledge_store table (without embedding column - added separately with vector type)
    op.create_table(
        'knowledge_store',
        sa.Column('id', UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('namespace', sa.String(255), nullable=False),
        sa.Column('organization_id', UUID(as_uuid=True), nullable=True),
        sa.Column('key', sa.String(255), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('metadata', JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('created_by', UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )

    # Add embedding column with vector type directly (pgvector)
    # 1536 dimensions for text-embedding-3-small
    op.execute('ALTER TABLE knowledge_store ADD COLUMN embedding vector(1536) NOT NULL')

    # Create unique constraint for namespace + org + key (with nulls not distinct)
    # This allows upsert behavior when key is provided
    op.execute('''
        ALTER TABLE knowledge_store
        ADD CONSTRAINT uq_knowledge_ns_org_key
        UNIQUE NULLS NOT DISTINCT (namespace, organization_id, key)
    ''')

    # Create indexes
    op.create_index('ix_knowledge_ns_org', 'knowledge_store', ['namespace', 'organization_id'])
    op.execute('CREATE INDEX ix_knowledge_metadata ON knowledge_store USING gin (metadata)')

    # Create vector similarity index (IVFFlat)
    # Note: For production with >100k vectors, consider HNSW index instead
    # lists=100 is good for up to ~100k vectors
    op.execute('''
        CREATE INDEX ix_knowledge_embedding ON knowledge_store
        USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
    ''')

    # Add knowledge_sources column to agents table
    op.add_column(
        'agents',
        sa.Column('knowledge_sources', sa.ARRAY(sa.String()), nullable=False, server_default='{}')
    )


def downgrade() -> None:
    # Remove knowledge_sources from agents
    op.drop_column('agents', 'knowledge_sources')

    # Drop indexes
    op.execute('DROP INDEX IF EXISTS ix_knowledge_embedding')
    op.execute('DROP INDEX IF EXISTS ix_knowledge_metadata')
    op.drop_index('ix_knowledge_ns_org', table_name='knowledge_store')

    # Drop constraint
    op.execute('ALTER TABLE knowledge_store DROP CONSTRAINT IF EXISTS uq_knowledge_ns_org_key')

    # Drop table
    op.drop_table('knowledge_store')

    # Note: We don't drop the vector extension as other things might use it

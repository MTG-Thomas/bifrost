"""backfill OAuth provider token-scope replay metadata

Revision ID: 20260702_oauth_scope_metadata
Revises: 20260617_solution_deploy_jobs
"""

from alembic import op


revision = "20260702_oauth_scope_metadata"
down_revision = "20260617_solution_deploy_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE oauth_providers
        SET provider_metadata =
            COALESCE(provider_metadata, '{}'::jsonb)
            || '{"omit_token_exchange_scope": true}'::jsonb
        WHERE lower(provider_name) IN ('gotoconnect', 'ninjaone')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE oauth_providers
        SET provider_metadata =
            COALESCE(provider_metadata, '{}'::jsonb)
            - 'omit_token_exchange_scope'
        WHERE lower(provider_name) IN ('gotoconnect', 'ninjaone')
          AND provider_metadata ->> 'omit_token_exchange_scope' = 'true'
        """
    )

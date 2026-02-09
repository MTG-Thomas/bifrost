"""migrate_to_timezone_aware_utc

Revision ID: f7a3dd4b6046
Revises: 25549bb29ea6
Create Date: 2026-02-09 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7a3dd4b6046'
down_revision: Union[str, None] = '25549bb29ea6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# All columns to migrate from timestamp (naive UTC) to timestamptz
COLUMNS_TO_MIGRATE = [
    # Original columns from 9b3f4d5e6f7a
    ("agent_roles", "assigned_at"),
    ("agents", "created_at"),
    ("agents", "updated_at"),
    ("ai_model_pricing", "created_at"),
    ("ai_model_pricing", "updated_at"),
    ("ai_usage", "timestamp"),
    ("audit_logs", "created_at"),
    ("cli_sessions", "created_at"),
    ("cli_sessions", "expires_at"),
    ("cli_sessions", "last_seen"),
    ("configs", "created_at"),
    ("configs", "updated_at"),
    ("conversations", "created_at"),
    ("conversations", "updated_at"),
    ("event_deliveries", "completed_at"),
    ("event_deliveries", "created_at"),
    ("event_deliveries", "next_retry_at"),
    ("event_sources", "created_at"),
    ("event_sources", "updated_at"),
    ("event_subscriptions", "created_at"),
    ("event_subscriptions", "updated_at"),
    ("events", "created_at"),
    ("events", "received_at"),
    ("execution_logs", "timestamp"),
    ("executions", "completed_at"),
    ("executions", "created_at"),
    ("executions", "started_at"),
    ("form_roles", "assigned_at"),
    ("forms", "created_at"),
    ("forms", "last_seen_at"),
    ("forms", "updated_at"),
    ("integration_mappings", "created_at"),
    ("integration_mappings", "updated_at"),
    ("integrations", "created_at"),
    ("integrations", "updated_at"),
    ("knowledge_storage_daily", "created_at"),
    ("knowledge_store", "created_at"),
    ("knowledge_store", "updated_at"),
    ("messages", "created_at"),
    ("mfa_recovery_codes", "created_at"),
    ("mfa_recovery_codes", "used_at"),
    ("oauth_providers", "created_at"),
    ("oauth_providers", "last_token_refresh"),
    ("oauth_providers", "updated_at"),
    ("oauth_tokens", "created_at"),
    ("oauth_tokens", "expires_at"),
    ("oauth_tokens", "updated_at"),
    ("organizations", "created_at"),
    ("organizations", "updated_at"),
    ("roles", "created_at"),
    ("roles", "updated_at"),
    ("schedules", "created_at"),
    ("schedules", "last_run_at"),
    ("schedules", "updated_at"),
    ("system_logs", "timestamp"),
    ("trusted_devices", "created_at"),
    ("trusted_devices", "expires_at"),
    ("trusted_devices", "last_used_at"),
    ("user_mfa_methods", "created_at"),
    ("user_mfa_methods", "last_used_at"),
    ("user_mfa_methods", "updated_at"),
    ("user_mfa_methods", "verified_at"),
    ("user_oauth_accounts", "created_at"),
    ("user_oauth_accounts", "last_login"),
    ("user_passkeys", "created_at"),
    ("user_passkeys", "last_used_at"),
    ("user_roles", "assigned_at"),
    ("users", "created_at"),
    ("users", "last_login"),
    ("users", "mfa_enforced_at"),
    ("users", "updated_at"),
    ("webhook_sources", "created_at"),
    ("webhook_sources", "expires_at"),
    ("webhook_sources", "updated_at"),
    ("workflows", "api_key_created_at"),
    ("workflows", "api_key_expires_at"),
    ("workflows", "api_key_last_used_at"),
    ("workflows", "created_at"),
    ("workflows", "last_seen_at"),
    ("workflows", "updated_at"),
    # New tables added after Jan 30, 2026
    ("app_files", "created_at"),
    ("app_files", "updated_at"),
    ("app_file_dependencies", "created_at"),
    ("app_roles", "assigned_at"),
    ("app_versions", "created_at"),
    ("applications", "created_at"),
    ("applications", "published_at"),
    ("applications", "updated_at"),
    ("branding", "created_at"),
    ("branding", "updated_at"),
    ("developer_contexts", "created_at"),
    ("developer_contexts", "updated_at"),
    ("documents", "created_at"),
    ("documents", "updated_at"),
    ("execution_metrics_daily", "created_at"),
    ("execution_metrics_daily", "updated_at"),
    ("integration_config_schema", "created_at"),
    ("integration_config_schema", "updated_at"),
    ("knowledge_namespace_roles", "assigned_at"),
    ("platform_metrics_snapshot", "refreshed_at"),
    ("schedule_sources", "created_at"),
    ("schedule_sources", "updated_at"),
    ("system_configs", "created_at"),
    ("system_configs", "updated_at"),
    ("tables", "created_at"),
    ("tables", "updated_at"),
    ("workflow_roi_daily", "created_at"),
    ("workflow_roi_daily", "updated_at"),
    ("workflow_roles", "assigned_at"),
    ("workspace_files", "created_at"),
    ("workspace_files", "updated_at"),
]


def upgrade() -> None:
    """Convert all timestamp columns to timestamptz (timezone-aware UTC)."""
    for table_name, column_name in COLUMNS_TO_MIGRATE:
        op.alter_column(
            table_name,
            column_name,
            type_=sa.DateTime(timezone=True),
            existing_type=sa.DateTime(),
            postgresql_using=f"{column_name} AT TIME ZONE 'UTC'",
        )


def downgrade() -> None:
    """Revert all timestamptz columns back to timestamp (naive UTC)."""
    for table_name, column_name in COLUMNS_TO_MIGRATE:
        op.alter_column(
            table_name,
            column_name,
            type_=sa.DateTime(),
            existing_type=sa.DateTime(timezone=True),
            postgresql_using=f"{column_name} AT TIME ZONE 'UTC'",
        )

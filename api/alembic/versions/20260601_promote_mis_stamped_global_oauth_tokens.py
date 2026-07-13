"""do not auto-promote provider-org-stamped global OAuth tokens

Revision ID: 20260601_promote_global_tok
Revises: 20260601_merge_heads
Create Date: 2026-06-01

The code fix in ``api/src/routers/oauth_connections.py`` ensures future refreshes
of a global OAuth connection (``oauth_providers.organization_id IS NULL``) write
that connection's org-level token at global scope (``oauth_tokens.organization_id
IS NULL``) instead of under the caller's organization.

This revision intentionally performs no data mutation. An earlier draft tried to
heal historical rows by promoting org-level tokens that were stamped with an
``organizations.is_provider = true`` org back to ``NULL``. That predicate is not
safe: provider organizations are also real tenants and may legitimately own
org-scoped OAuth tokens for a global provider. Automatically converting such a
row to ``NULL`` would make private provider-org credentials the global fallback
for other tenants; deleting it when a global token already exists would cause
data loss. Because the schema has no durable marker proving which rows were
created by the old refresh bug, remediation must be handled case-by-case with
operator-reviewed SQL rather than an automatic migration.
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "20260601_promote_global_tok"
down_revision: Union[str, Sequence[str]] = "20260601_merge_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Leave existing OAuth tokens untouched.

    See the module docstring for why automatic promotion/deletion is unsafe.
    """
    pass


def downgrade() -> None:
    """No-op because upgrade() intentionally does not mutate data."""
    pass

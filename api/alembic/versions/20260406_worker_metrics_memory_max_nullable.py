"""worker_metrics memory_max nullable

Revision ID: 20260406_mmax_nullable
Revises: 20260405_worker_metrics
Create Date: 2026-04-06

Allow worker_metrics.memory_max to be NULL. Semantically, NULL means
"container has no hard memory limit" (e.g. dev-docker or K8s pods
without resources.limits.memory). Previously the sampler skipped these
rows entirely, leaving the diagnostics chart empty.
"""
from alembic import op

revision = "20260406_mmax_nullable"
down_revision = "20260405_worker_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("worker_metrics", "memory_max", nullable=True)


def downgrade() -> None:
    op.execute("UPDATE worker_metrics SET memory_max = -1 WHERE memory_max IS NULL")
    op.alter_column("worker_metrics", "memory_max", nullable=False)

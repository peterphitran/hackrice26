"""Add durable patch and publication records for M2.

Revision ID: 20260913_0005
Revises: 20260913_0004
"""

from __future__ import annotations

from alembic import op

revision = "20260913_0005"
down_revision = "20260913_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE lou.patch_artifacts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_run_id UUID NOT NULL REFERENCES lou.agent_runs(id) ON DELETE CASCADE,
            patch_id TEXT NOT NULL,
            patch_sha256 TEXT NOT NULL CHECK (length(patch_sha256) = 64),
            artifact_uri TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT patch_artifact_run_hash UNIQUE (agent_run_id, patch_sha256)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE lou.publication_attempts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_run_id UUID NOT NULL REFERENCES lou.agent_runs(id) ON DELETE CASCADE,
            publication_plan_id TEXT NOT NULL UNIQUE,
            plan JSONB NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('dry_run', 'published', 'denied', 'failed')),
            provider_reference TEXT,
            message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX publication_attempts_agent_run_idx "
        "ON lou.publication_attempts (agent_run_id, created_at)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON lou.patch_artifacts, lou.publication_attempts "
        "TO lou, lou_test_app"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS lou.publication_attempts")
    op.execute("DROP TABLE IF EXISTS lou.patch_artifacts")

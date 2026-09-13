"""Add durable, resumable remediation-run state.

Revision ID: 20260913_0003
Revises: 20260913_0002
Create Date: 2026-09-13
"""

from __future__ import annotations

from alembic import op

revision = "20260913_0003"
down_revision = "20260913_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Persist a bounded agent workflow and its append-only stage attempts."""

    op.execute(
        """
        CREATE TABLE lou.agent_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            analysis_run_id UUID NOT NULL REFERENCES lou.analysis_runs(id) ON DELETE CASCADE,
            input_fingerprint TEXT NOT NULL CHECK (length(input_fingerprint) = 64),
            deduplication_key TEXT NOT NULL UNIQUE,
            provider TEXT NOT NULL CHECK (provider IN ('mock', 'gemini')),
            policy_revision TEXT NOT NULL,
            limits JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL DEFAULT 'queued' CHECK (
                status IN ('queued', 'running', 'succeeded', 'failed', 'abandoned', 'cancelled')
            ),
            stage TEXT NOT NULL DEFAULT 'context' CHECK (
                stage IN ('context', 'diagnose', 'patch', 'validate', 'verify', 'decide', 'stopped')
            ),
            snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            tokens_spent INTEGER NOT NULL DEFAULT 0 CHECK (tokens_spent >= 0),
            estimated_cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0
                CHECK (estimated_cost_usd >= 0),
            termination_reason TEXT,
            error_message TEXT,
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX agent_runs_analysis_status_idx ON lou.agent_runs (analysis_run_id, status)"
    )
    op.execute(
        """
        CREATE TABLE lou.remediation_attempts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_run_id UUID NOT NULL REFERENCES lou.agent_runs(id) ON DELETE CASCADE,
            attempt_key TEXT NOT NULL,
            attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
            stage TEXT NOT NULL CHECK (
                stage IN ('context', 'diagnose', 'patch', 'validate', 'verify', 'decide', 'stopped')
            ),
            outcome TEXT NOT NULL,
            patch_sha256 TEXT CHECK (patch_sha256 IS NULL OR length(patch_sha256) = 64),
            agent_result JSONB NOT NULL DEFAULT '{}'::jsonb,
            validation JSONB NOT NULL DEFAULT '{}'::jsonb,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT remediation_attempt_key UNIQUE (agent_run_id, attempt_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX remediation_attempts_run_number_idx "
        "ON lou.remediation_attempts (agent_run_id, attempt_number)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON lou.agent_runs, lou.remediation_attempts "
        "TO lou, lou_test_app"
    )


def downgrade() -> None:
    """Remove only the M2 remediation persistence additions."""

    op.execute("DROP TABLE IF EXISTS lou.remediation_attempts")
    op.execute("DROP TABLE IF EXISTS lou.agent_runs")

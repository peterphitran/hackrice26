"""Create Lou's initial PostgreSQL persistence vertical slice.

Revision ID: 20260912_0001
Revises:
Create Date: 2026-09-12
"""

# SQL statements are kept readable as migration-local DDL.
# ruff: noqa: E501

from __future__ import annotations

from alembic import op

revision = "20260912_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the six tables needed before repository adapters ship."""

    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE SCHEMA IF NOT EXISTS lou AUTHORIZATION lou_migrator")
    op.execute("GRANT USAGE ON SCHEMA lou TO lou, lou_test_app")
    op.execute(
        """
        CREATE TABLE lou.repositories (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            provider TEXT NOT NULL CHECK (provider IN ('local', 'github')),
            provider_repository_id TEXT,
            owner_name TEXT,
            repository_name TEXT NOT NULL,
            local_path TEXT,
            clone_url TEXT,
            default_branch TEXT NOT NULL DEFAULT 'main',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (local_path IS NOT NULL OR clone_url IS NOT NULL),
            UNIQUE (provider, provider_repository_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE lou.analysis_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            repository_id UUID NOT NULL REFERENCES lou.repositories(id) ON DELETE CASCADE,
            trigger_type TEXT NOT NULL CHECK (trigger_type IN ('cli', 'api', 'github_webhook', 'fixture')),
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'inconclusive')),
            base_commit_sha TEXT NOT NULL,
            candidate_commit_sha TEXT NOT NULL,
            fix_commit_sha TEXT,
            deduplication_key TEXT NOT NULL UNIQUE,
            configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
            toolchain_revision TEXT NOT NULL,
            policy_revision TEXT NOT NULL,
            error_code TEXT,
            error_message TEXT,
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (base_commit_sha <> candidate_commit_sha)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE lou.workloads (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            repository_id UUID NOT NULL REFERENCES lou.repositories(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            workload_type TEXT NOT NULL CHECK (workload_type IN ('pytest', 'k6', 'semgrep', 'custom')),
            definition_path TEXT NOT NULL,
            definition_sha256 TEXT NOT NULL,
            selectors JSONB NOT NULL DEFAULT '{}'::jsonb,
            enabled BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (repository_id, name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE lou.verification_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            analysis_run_id UUID NOT NULL REFERENCES lou.analysis_runs(id) ON DELETE CASCADE,
            workload_id UUID REFERENCES lou.workloads(id) ON DELETE SET NULL,
            phase TEXT NOT NULL CHECK (phase IN ('baseline', 'candidate', 'fix')),
            attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0),
            status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'passed', 'failed', 'inconclusive')),
            commit_sha TEXT NOT NULL,
            environment_image_digest TEXT,
            resource_limits JSONB NOT NULL DEFAULT '{}'::jsonb,
            aggregate_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
            artifact_uri TEXT,
            artifact_sha256 TEXT,
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (analysis_run_id, workload_id, phase, attempt),
            CHECK ((artifact_uri IS NULL AND artifact_sha256 IS NULL)
                OR (artifact_uri IS NOT NULL AND artifact_sha256 IS NOT NULL))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE lou.findings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            analysis_run_id UUID NOT NULL REFERENCES lou.analysis_runs(id) ON DELETE CASCADE,
            fingerprint TEXT NOT NULL,
            source TEXT NOT NULL,
            rule_id TEXT,
            category TEXT NOT NULL,
            severity TEXT NOT NULL CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical')),
            confidence NUMERIC(5,4) CHECK (confidence >= 0 AND confidence <= 1),
            phase TEXT NOT NULL CHECK (phase IN ('baseline', 'candidate', 'fix')),
            file_path TEXT,
            symbol_key TEXT,
            start_line INTEGER CHECK (start_line IS NULL OR start_line > 0),
            end_line INTEGER CHECK (end_line IS NULL OR end_line > 0),
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (analysis_run_id, phase, fingerprint)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE lou.evidence (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            analysis_run_id UUID NOT NULL REFERENCES lou.analysis_runs(id) ON DELETE CASCADE,
            finding_id UUID REFERENCES lou.findings(id) ON DELETE SET NULL,
            phase TEXT NOT NULL CHECK (phase IN ('baseline', 'candidate', 'fix', 'comparison')),
            kind TEXT NOT NULL,
            source TEXT NOT NULL,
            schema_version TEXT NOT NULL DEFAULT '1',
            summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            artifact_uri TEXT,
            artifact_sha256 TEXT,
            collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK ((artifact_uri IS NULL AND artifact_sha256 IS NULL)
                OR (artifact_uri IS NOT NULL AND artifact_sha256 IS NOT NULL))
        )
        """
    )
    op.execute("CREATE INDEX analysis_runs_repository_status_idx ON lou.analysis_runs (repository_id, status)")
    op.execute("CREATE INDEX verification_runs_run_phase_idx ON lou.verification_runs (analysis_run_id, phase)")
    op.execute("CREATE INDEX findings_run_phase_idx ON lou.findings (analysis_run_id, phase)")
    op.execute("CREATE INDEX evidence_run_phase_idx ON lou.evidence (analysis_run_id, phase)")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA lou TO lou, lou_test_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA lou TO lou, lou_test_app")


def downgrade() -> None:
    """Remove only objects owned by the initial Lou schema revision."""

    op.execute("DROP SCHEMA IF EXISTS lou CASCADE")

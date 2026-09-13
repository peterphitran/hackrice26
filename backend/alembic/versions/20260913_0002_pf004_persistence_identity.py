"""Add PF-004 persistence identities and durable decisions.

Revision ID: 20260913_0002
Revises: 20260912_0001
Create Date: 2026-09-13
"""

from __future__ import annotations

from alembic import op

revision = "20260913_0002"
down_revision = "20260912_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Make external contract identities and final decisions durable."""

    op.execute(
        "CREATE UNIQUE INDEX repositories_local_path_unique "
        "ON lou.repositories (local_path) WHERE local_path IS NOT NULL"
    )
    for table in ("verification_runs", "findings", "evidence"):
        op.execute(f"ALTER TABLE lou.{table} ADD COLUMN contract_id TEXT")
        op.execute(
            f"CREATE UNIQUE INDEX {table}_contract_id_unique "
            f"ON lou.{table} (analysis_run_id, contract_id) WHERE contract_id IS NOT NULL"
        )
    op.execute(
        """
        CREATE TABLE lou.lou_decisions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            analysis_run_id UUID NOT NULL UNIQUE
                REFERENCES lou.analysis_runs(id) ON DELETE CASCADE,
            decision_id TEXT NOT NULL UNIQUE,
            action TEXT NOT NULL CHECK (
                action IN ('report', 'recommend', 'generate_patch', 'open_pr')
            ),
            debt_risk NUMERIC(5,4) NOT NULL CHECK (debt_risk >= 0 AND debt_risk <= 1),
            remediation_risk NUMERIC(5,4) CHECK (remediation_risk >= 0 AND remediation_risk <= 1),
            confidence NUMERIC(5,4) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
            autonomy_level INTEGER NOT NULL CHECK (autonomy_level BETWEEN 0 AND 3),
            rationale JSONB NOT NULL DEFAULT '{}'::jsonb,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON lou.lou_decisions TO lou, lou_test_app")


def downgrade() -> None:
    """Remove only PF-004 persistence identity additions."""

    op.execute("DROP TABLE IF EXISTS lou.lou_decisions")
    for table in ("evidence", "findings", "verification_runs"):
        op.execute(f"DROP INDEX IF EXISTS lou.{table}_contract_id_unique")
        op.execute(f"ALTER TABLE lou.{table} DROP COLUMN IF EXISTS contract_id")
    op.execute("DROP INDEX IF EXISTS lou.repositories_local_path_unique")

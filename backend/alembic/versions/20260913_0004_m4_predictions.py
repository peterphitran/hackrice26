"""Persist immutable M4 impact predictions.

Revision ID: 20260913_0004
Revises: 20260913_0003
"""

from alembic import op

revision = "20260913_0004"
down_revision = "20260913_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE lou.predictions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            analysis_run_id UUID NOT NULL REFERENCES lou.analysis_runs(id) ON DELETE CASCADE,
            predictor_name TEXT NOT NULL,
            predictor_revision TEXT NOT NULL,
            repository_id TEXT NOT NULL,
            base_commit_sha TEXT NOT NULL,
            candidate_commit_sha TEXT NOT NULL,
            confidence NUMERIC(5,4) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (analysis_run_id, predictor_revision)
        )
    """)
    op.execute("""
        CREATE FUNCTION lou.reject_prediction_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'impact predictions are append-only'; END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER predictions_append_only
        BEFORE UPDATE OR DELETE ON lou.predictions
        FOR EACH ROW EXECUTE FUNCTION lou.reject_prediction_mutation()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS predictions_append_only ON lou.predictions")
    op.execute("DROP FUNCTION IF EXISTS lou.reject_prediction_mutation()")
    op.execute("DROP TABLE IF EXISTS lou.predictions")

"""Move the M8 deployment evidence journal from local JSONL into PostgreSQL.

Revision ID: 20260913_0006
Revises: 20260913_0005
"""

from __future__ import annotations

from alembic import op

revision = "20260913_0006"
down_revision = "20260913_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE lou.deployment_journal (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            release_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence >= 0),
            kind TEXT NOT NULL CHECK (kind IN ('release', 'evidence')),
            body JSONB NOT NULL,
            previous_hash TEXT NOT NULL DEFAULT '',
            record_hash TEXT NOT NULL CHECK (length(record_hash) = 64),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT deployment_journal_release_sequence UNIQUE (release_id, sequence)
        )
        """
    )
    op.execute(
        "CREATE INDEX deployment_journal_release_idx "
        "ON lou.deployment_journal (release_id, sequence)"
    )
    # The journal is append-only evidence: withholding UPDATE and DELETE makes the
    # hash chain enforceable rather than merely detectable.
    op.execute("GRANT SELECT, INSERT ON lou.deployment_journal TO lou, lou_test_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS lou.deployment_journal")

"""Grant the runtime role access to M4 predictions.

Revision ID: 20260913_0007
Revises: 20260913_0006

20260913_0004 created lou.predictions but never granted on it, so every analysis
run failed with a ProgrammingError the moment it tried to persist a prediction.
This is a forward fix rather than an edit to the applied migration.
"""

from __future__ import annotations

from alembic import op

revision = "20260913_0007"
down_revision = "20260913_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON lou.predictions TO lou, lou_test_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON lou.predictions FROM lou, lou_test_app")

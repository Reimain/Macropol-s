"""The baseline: the schema as `schema.sql` states it.

One revision that runs the file rather than a hand-written column list, so
there is no second statement of the schema to drift from the first. Later
revisions will be ordinary ALTERs; this one exists so a fresh database and a
migrated one arrive at the same place.

Revision ID: 0001
Revises:
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = Path(__file__).resolve().parent.parent.parent / "schema.sql"

TABLES = (
    "ledger_meta", "ledger", "graph_meta", "snapshot", "enrichment",
    "edge_evidence", "node_evidence", "evidence", "edge", "node",
)


def upgrade() -> None:
    op.execute(SCHEMA.read_text(encoding="utf-8"))


def downgrade() -> None:
    # Named in dependency order and dropped as one statement, so a partial
    # downgrade cannot leave a half-schema that the next upgrade then finds
    # already present and skips.
    op.execute(f"DROP TABLE IF EXISTS {', '.join(TABLES)} CASCADE")

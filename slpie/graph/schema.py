"""The graph schema — bitemporal tables, and the indexes that make them usable.

Three indexes carry the platform's performance, and each exists for a named
query rather than out of habit:

* ``edge_out`` / ``edge_in`` are partial, covering only live edges. Traversal
  never asks about retired ones, so keeping them out of the index keeps it the
  size of the *current* graph rather than of all history.
* ``evidence_uri`` is the incremental-invalidation index. It answers "what did
  this file justify?" in one lookup, which is what makes a rescan touch only the
  region that changed instead of the whole ecosystem.

Node and edge rows are projections, not truth. They can be dropped and rebuilt
from the ledger at any time, which is exactly what :mod:`slpie.graph.store` does
when the schema changes.
"""

from __future__ import annotations

#: Bumped when the table shapes change. A mismatch triggers a rebuild from the
#: ledger rather than a migration script — the read model has no truth to lose.
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS node (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    identity      TEXT NOT NULL,
    coordinate    TEXT NOT NULL DEFAULT '',
    name          TEXT NOT NULL DEFAULT '',
    version       TEXT NOT NULL DEFAULT '',
    display       TEXT NOT NULL DEFAULT '',
    properties    TEXT NOT NULL DEFAULT '{}',
    lifecycle     TEXT NOT NULL DEFAULT 'unknown',
    risk          TEXT NOT NULL DEFAULT 'none',
    compliance    TEXT NOT NULL DEFAULT 'unassessed',
    architecture  TEXT NOT NULL DEFAULT 'unclassified',
    confidence    REAL NOT NULL DEFAULT 0.0,
    validation    TEXT NOT NULL DEFAULT 'unverified',
    valid_from    INTEGER NOT NULL DEFAULT 0,
    valid_to      INTEGER,              -- NULL means still true
    observed_at   INTEGER NOT NULL DEFAULT 0,
    superseded_at INTEGER,              -- NULL means still believed
    sequence      INTEGER NOT NULL DEFAULT 0,   -- latest assertion
    -- The row is upserted on every re-assertion, so `sequence` only ever tells
    -- you when it was last touched. Reconstructing the graph as of an earlier
    -- ledger version needs the two boundaries of its existence instead.
    first_sequence   INTEGER NOT NULL DEFAULT 0,
    retired_sequence INTEGER
);
CREATE INDEX IF NOT EXISTS node_kind       ON node(kind) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS node_history    ON node(first_sequence, retired_sequence);
CREATE INDEX IF NOT EXISTS node_coordinate ON node(coordinate);
CREATE INDEX IF NOT EXISTS node_name       ON node(name);
CREATE INDEX IF NOT EXISTS node_risk       ON node(risk) WHERE valid_to IS NULL;

CREATE TABLE IF NOT EXISTS edge (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    src           TEXT NOT NULL,
    dst           TEXT NOT NULL,
    qualifier     TEXT NOT NULL DEFAULT '',
    properties    TEXT NOT NULL DEFAULT '{}',
    confidence    REAL NOT NULL DEFAULT 0.0,
    validation    TEXT NOT NULL DEFAULT 'unverified',
    propagates    INTEGER NOT NULL DEFAULT 0,   -- denormalised: traversal filters on it
    valid_from    INTEGER NOT NULL DEFAULT 0,
    valid_to      INTEGER,
    observed_at   INTEGER NOT NULL DEFAULT 0,
    superseded_at INTEGER,
    sequence      INTEGER NOT NULL DEFAULT 0,
    first_sequence   INTEGER NOT NULL DEFAULT 0,
    retired_sequence INTEGER
);
-- Partial indexes: traversal only ever reads live edges.
CREATE INDEX IF NOT EXISTS edge_history ON edge(first_sequence, retired_sequence);
CREATE INDEX IF NOT EXISTS edge_out ON edge(src, kind) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS edge_in  ON edge(dst, kind) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS edge_pair ON edge(src, dst);

CREATE TABLE IF NOT EXISTS evidence (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    uri            TEXT NOT NULL,
    line           INTEGER NOT NULL DEFAULT 0,
    extractor      TEXT NOT NULL DEFAULT '',
    content_digest TEXT NOT NULL DEFAULT '',
    excerpt        TEXT NOT NULL DEFAULT '',
    observed_at    INTEGER NOT NULL DEFAULT 0,
    base_confidence REAL NOT NULL DEFAULT 0.0,
    -- Evidence labels, as JSON. Without this column they were accepted by
    -- `Evidence` and silently dropped on the way to disk, so anything reading
    -- them back got an empty mapping -- which disabled the SBOM's
    -- evidence-derived hashes without failing anywhere a test would see.
    labels         TEXT NOT NULL DEFAULT '{}'
);
-- The index incremental recomputation is built on.
CREATE INDEX IF NOT EXISTS evidence_uri ON evidence(uri);

CREATE TABLE IF NOT EXISTS node_evidence (
    node_id     TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    PRIMARY KEY (node_id, evidence_id)
);
CREATE INDEX IF NOT EXISTS node_evidence_by_evidence ON node_evidence(evidence_id);

CREATE TABLE IF NOT EXISTS edge_evidence (
    edge_id     TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    PRIMARY KEY (edge_id, evidence_id)
);
CREATE INDEX IF NOT EXISTS edge_evidence_by_evidence ON edge_evidence(evidence_id);

CREATE TABLE IF NOT EXISTS enrichment (
    id           TEXT PRIMARY KEY,
    subject      TEXT NOT NULL,
    attribute    TEXT NOT NULL,
    value        TEXT NOT NULL DEFAULT '',
    layer        TEXT NOT NULL DEFAULT '',
    derived_from TEXT NOT NULL DEFAULT '[]',
    confidence   REAL NOT NULL DEFAULT 0.0,
    rationale    TEXT NOT NULL DEFAULT '',
    derived_at   INTEGER NOT NULL DEFAULT 0,
    sequence     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS enrichment_subject ON enrichment(subject, attribute);
CREATE INDEX IF NOT EXISTS enrichment_layer   ON enrichment(layer);

CREATE TABLE IF NOT EXISTS snapshot (
    id             TEXT PRIMARY KEY,
    label          TEXT NOT NULL DEFAULT '',
    ledger_version INTEGER NOT NULL,
    valid_time     INTEGER NOT NULL DEFAULT 0,
    root_digest    TEXT NOT NULL,
    node_count     INTEGER NOT NULL DEFAULT 0,
    edge_count     INTEGER NOT NULL DEFAULT 0,
    sealed_at      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS snapshot_label ON snapshot(label);

CREATE TABLE IF NOT EXISTS graph_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

#: Full-text search over node names and identities, for the console's
#: `graph_search` tool. Optional because FTS5 is a compile-time feature, and the
#: platform must degrade to a LIKE scan rather than fail to start.
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS node_search USING fts5(
    id UNINDEXED, name, identity, display, kind UNINDEXED,
    tokenize = 'unicode61 tokenchars ''-_.@/'''
);
"""


def has_fts5(connection) -> bool:
    """Whether this SQLite was built with FTS5.

    Checked rather than assumed: search degrades to a scan when it is missing,
    and a platform that refused to start over an optional index would be worse
    than one that searches a little more slowly.
    """
    try:
        connection.execute("CREATE VIRTUAL TABLE temp.fts5_probe USING fts5(x)")
        connection.execute("DROP TABLE temp.fts5_probe")
        return True
    except Exception:
        return False

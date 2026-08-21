-- The graph schema, in Postgres.
--
-- Mirrors `slpie/graph/schema.py` column for column; a test compares the two
-- introspected sets, so a column added there and not here fails rather than
-- producing a store that silently drops a field.
--
-- Four type differences, and each is Postgres having a type SQLite does not:
--
--   INTEGER (64-bit)  -> BIGINT              sequences outgrow 32 bits
--   REAL              -> DOUBLE PRECISION    confidence is not a float32
--   INTEGER (0/1)     -> BOOLEAN             `propagates` is a flag, so say so
--   TEXT holding JSON -> JSONB               queryable, and validated on write
--
-- The partial indexes port unchanged: Postgres has had them since 7.2, and they
-- are the reason traversal reads an index the size of the *current* graph
-- rather than of all history.

CREATE TABLE IF NOT EXISTS node (
    id               TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,
    identity         TEXT NOT NULL,
    coordinate       TEXT NOT NULL DEFAULT '',
    name             TEXT NOT NULL DEFAULT '',
    version          TEXT NOT NULL DEFAULT '',
    display          TEXT NOT NULL DEFAULT '',
    properties       JSONB NOT NULL DEFAULT '{}'::jsonb,
    lifecycle        TEXT NOT NULL DEFAULT 'unknown',
    risk             TEXT NOT NULL DEFAULT 'none',
    compliance       TEXT NOT NULL DEFAULT 'unassessed',
    architecture     TEXT NOT NULL DEFAULT 'unclassified',
    confidence       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    validation       TEXT NOT NULL DEFAULT 'unverified',
    valid_from       BIGINT NOT NULL DEFAULT 0,
    valid_to         BIGINT,
    observed_at      BIGINT NOT NULL DEFAULT 0,
    superseded_at    BIGINT,
    sequence         BIGINT NOT NULL DEFAULT 0,
    first_sequence   BIGINT NOT NULL DEFAULT 0,
    retired_sequence BIGINT
);
CREATE INDEX IF NOT EXISTS node_kind       ON node(kind) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS node_history    ON node(first_sequence, retired_sequence);
CREATE INDEX IF NOT EXISTS node_coordinate ON node(coordinate);
CREATE INDEX IF NOT EXISTS node_name       ON node(name);
CREATE INDEX IF NOT EXISTS node_risk       ON node(risk) WHERE valid_to IS NULL;

CREATE TABLE IF NOT EXISTS edge (
    id               TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,
    src              TEXT NOT NULL,
    dst              TEXT NOT NULL,
    qualifier        TEXT NOT NULL DEFAULT '',
    properties       JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    validation       TEXT NOT NULL DEFAULT 'unverified',
    propagates       BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from       BIGINT NOT NULL DEFAULT 0,
    valid_to         BIGINT,
    observed_at      BIGINT NOT NULL DEFAULT 0,
    superseded_at    BIGINT,
    sequence         BIGINT NOT NULL DEFAULT 0,
    first_sequence   BIGINT NOT NULL DEFAULT 0,
    retired_sequence BIGINT
);
CREATE INDEX IF NOT EXISTS edge_history ON edge(first_sequence, retired_sequence);
CREATE INDEX IF NOT EXISTS edge_out ON edge(src, kind) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS edge_in  ON edge(dst, kind) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS edge_pair ON edge(src, dst);

CREATE TABLE IF NOT EXISTS evidence (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    uri             TEXT NOT NULL,
    line            BIGINT NOT NULL DEFAULT 0,
    extractor       TEXT NOT NULL DEFAULT '',
    content_digest  TEXT NOT NULL DEFAULT '',
    excerpt         TEXT NOT NULL DEFAULT '',
    observed_at     BIGINT NOT NULL DEFAULT 0,
    base_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    labels          JSONB NOT NULL DEFAULT '{}'::jsonb
);
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
    derived_from JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence   DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    rationale    TEXT NOT NULL DEFAULT '',
    derived_at   BIGINT NOT NULL DEFAULT 0,
    sequence     BIGINT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS enrichment_subject ON enrichment(subject, attribute);
CREATE INDEX IF NOT EXISTS enrichment_layer   ON enrichment(layer);

CREATE TABLE IF NOT EXISTS snapshot (
    id             TEXT PRIMARY KEY,
    label          TEXT NOT NULL DEFAULT '',
    ledger_version BIGINT NOT NULL,
    valid_time     BIGINT NOT NULL DEFAULT 0,
    root_digest    TEXT NOT NULL,
    node_count     BIGINT NOT NULL DEFAULT 0,
    edge_count     BIGINT NOT NULL DEFAULT 0,
    sealed_at      BIGINT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS snapshot_label ON snapshot(label);

CREATE TABLE IF NOT EXISTS graph_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- The ledger. `sequence` is assigned under a row lock rather than by a sequence
-- object: a Postgres sequence deliberately allows gaps after a rollback, and a
-- hash chain with a gap is a chain that cannot be verified.
--
-- Column for column with `slpie/ledger/sqlite_ledger.py`, including the names:
-- `previous_hash` and `hash`, not `previous` and `digest`. A rename here would
-- be a second vocabulary for one thing.
CREATE TABLE IF NOT EXISTS ledger (
    sequence       BIGINT PRIMARY KEY,
    event_id       TEXT NOT NULL UNIQUE,
    kind           TEXT NOT NULL,
    subject        TEXT NOT NULL,
    payload        TEXT NOT NULL,
    actor          TEXT NOT NULL,
    causation_id   TEXT NOT NULL DEFAULT '',
    correlation_id TEXT NOT NULL DEFAULT '',
    occurred_at    BIGINT NOT NULL,
    written_at     BIGINT NOT NULL,
    previous_hash  TEXT NOT NULL,
    hash           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_subject     ON ledger(subject);
CREATE INDEX IF NOT EXISTS ledger_kind        ON ledger(kind);
CREATE INDEX IF NOT EXISTS ledger_causation   ON ledger(causation_id);
CREATE INDEX IF NOT EXISTS ledger_correlation ON ledger(correlation_id);

CREATE TABLE IF NOT EXISTS ledger_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

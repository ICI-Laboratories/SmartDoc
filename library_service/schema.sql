CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE TABLE IF NOT EXISTS documents (
    id uuid PRIMARY KEY,
    subject uuid NOT NULL,
    name text NOT NULL,
    sha256 text NOT NULL,
    size bigint NOT NULL CHECK (size > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','ready','failed')),
    error text,
    page_count integer,
    embedding_model text,
    embedding_error text,
    UNIQUE(subject, sha256)
);
CREATE INDEX IF NOT EXISTS documents_owner_cursor ON documents(subject, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS documents_name ON documents USING gin (name gin_trgm_ops);
CREATE TABLE IF NOT EXISTS chunks (
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal integer NOT NULL,
    page integer NOT NULL,
    content text NOT NULL,
    terms tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    embedding vector(1024),
    PRIMARY KEY(document_id, ordinal)
);
CREATE INDEX IF NOT EXISTS chunks_terms ON chunks USING gin(terms);
-- Exact vector search initially; choose ANN only after measuring recall with owner filters.
CREATE TABLE IF NOT EXISTS jobs (
    document_id uuid PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','running','done','failed')),
    attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz,
    lease_token uuid
);
CREATE INDEX IF NOT EXISTS jobs_available ON jobs(state, available_at);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary text;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS category text;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary_error text;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary_requested boolean NOT NULL DEFAULT false;

-- Anonymous access is a server-issued capability, never a browser-chosen UUID.
CREATE TABLE IF NOT EXISTS anonymous_sessions (
    subject uuid PRIMARY KEY,
    token_hash text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    analytics_enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS usage_events (
    id uuid PRIMARY KEY,
    visitor uuid NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    event text NOT NULL,
    source text NOT NULL CHECK (source IN ('browser','server','worker')),
    value text,
    status integer,
    duration_ms integer
);
CREATE INDEX IF NOT EXISTS usage_events_time ON usage_events(occurred_at);
CREATE INDEX IF NOT EXISTS usage_events_visitor_time ON usage_events(visitor,occurred_at);

-- Existing anonymous spaces retain their original policy; only new temporary spaces expire destructively.
ALTER TABLE anonymous_sessions ADD COLUMN IF NOT EXISTS ephemeral boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS usage_preferences (
    subject uuid PRIMARY KEY,
    analytics_enabled boolean NOT NULL DEFAULT true
);
INSERT INTO usage_preferences(subject,analytics_enabled)
    SELECT subject,analytics_enabled FROM anonymous_sessions ON CONFLICT DO NOTHING;

ALTER TABLE anonymous_sessions ADD COLUMN IF NOT EXISTS cleaned_at timestamptz;

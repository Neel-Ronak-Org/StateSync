-- migrations/021_workspaces_rls.sql
-- Workspaces, the knowledge store, workspace documents, and row-level security
-- over all of StateSync's own store (docs/V3_PLAN.md Phase 3, §5.2, §5.5).
--
-- WORKSPACES. A workspace is a name, a data source (a sample dataset now; an
-- external connection in self-hosted mode, slice 6), the owner's business
-- context (versioned), a knowledge store, its documents, its dashboards and a
-- setup score. One owner principal, one tenant.
--
-- KNOWLEDGE STORE (per workspace, never in the customer's database):
--   kb_columns      the scan's catalog (tables and columns, types, row counts,
--                   sample values) and each one's description: drafted by AI,
--                   edited by the owner, used in prompts only once approved.
--   kb_definitions  business terms ("revenue", "on time") and what they mean.
--   kb_examples     approved question -> SQL pairs (setup test, tiles, owner).
--   setup_runs / setup_results  the 10-question setup test and its verdicts.
--
-- WORKSPACE DOCUMENTS live in their own schema, workspace_docs, not in the
-- sample corpus's public.documents: the A5 fingerprint pins public's shape.
--
-- ROW-LEVEL SECURITY is the backstop behind the per-statement tenant filters.
-- Every table in session_state and workspace_docs gets RLS with a policy on
-- tenant_id = current_setting('statesync.tenant_id', true). The MCP sets that
-- at the start of every transaction from the session's tenant (mcp/db.py);
-- when it is not set, current_setting returns NULL and NO row matches, so a
-- missing setting fails closed. Child tables without a tenant column
-- (tile_versions, tile_results) are scoped through their tile, whose own
-- policy applies inside the subquery. The owner role (statesync_migrator)
-- bypasses RLS, as Postgres table owners do; the application roles do not.
-- tests/test_rls_coverage.py fails if any table in these schemas lacks RLS.
--
-- Idempotent: safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS session_state.workspaces (
  workspace_id         TEXT PRIMARY KEY,
  tenant_id            TEXT NOT NULL,
  owner_principal_id   TEXT NOT NULL,
  name                 TEXT NOT NULL,
  target_schema        TEXT NOT NULL,
  connection_id        TEXT,
  business_context     TEXT NOT NULL DEFAULT '',
  context_version      INTEGER NOT NULL DEFAULT 0,
  setup_state          TEXT NOT NULL DEFAULT 'created',
  catalog_fingerprint  TEXT,
  setup_score          INTEGER,
  setup_total          INTEGER,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT workspaces_name_ck CHECK (length(btrim(name)) > 0),
  CONSTRAINT workspaces_state_ck CHECK (setup_state IN ('created', 'scanned', 'drafted', 'approved', 'tested'))
);
CREATE INDEX IF NOT EXISTS workspaces_owner_ix ON session_state.workspaces (tenant_id, owner_principal_id);

CREATE TABLE IF NOT EXISTS session_state.business_context_versions (
  workspace_id         TEXT NOT NULL REFERENCES session_state.workspaces (workspace_id) ON DELETE CASCADE,
  tenant_id            TEXT NOT NULL,
  version              INTEGER NOT NULL,
  text                 TEXT NOT NULL,
  author_principal_id  TEXT NOT NULL,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, version)
);

CREATE TABLE IF NOT EXISTS session_state.kb_columns (
  workspace_id        TEXT NOT NULL REFERENCES session_state.workspaces (workspace_id) ON DELETE CASCADE,
  tenant_id           TEXT NOT NULL,
  table_name          TEXT NOT NULL,
  column_name         TEXT NOT NULL DEFAULT '',   -- '' is the table itself
  data_type           TEXT,
  row_estimate        BIGINT,
  sample_values       JSONB,
  description         TEXT,
  description_source  TEXT,
  approved            BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, table_name, column_name),
  CONSTRAINT kb_columns_source_ck CHECK (description_source IS NULL OR description_source IN ('ai', 'owner'))
);

CREATE TABLE IF NOT EXISTS session_state.kb_definitions (
  definition_id  TEXT PRIMARY KEY,
  workspace_id   TEXT NOT NULL REFERENCES session_state.workspaces (workspace_id) ON DELETE CASCADE,
  tenant_id      TEXT NOT NULL,
  term           TEXT NOT NULL,
  definition     TEXT NOT NULL,
  source         TEXT NOT NULL,
  approved       BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT kb_definitions_source_ck CHECK (source IN ('ai', 'owner'))
);
CREATE UNIQUE INDEX IF NOT EXISTS kb_definitions_term_ux ON session_state.kb_definitions (workspace_id, lower(term));

CREATE TABLE IF NOT EXISTS session_state.kb_examples (
  example_id     TEXT PRIMARY KEY,
  workspace_id   TEXT NOT NULL REFERENCES session_state.workspaces (workspace_id) ON DELETE CASCADE,
  tenant_id      TEXT NOT NULL,
  question       TEXT NOT NULL,
  question_norm  TEXT NOT NULL,
  sql_text       TEXT NOT NULL,
  source         TEXT NOT NULL,
  approved       BOOLEAN NOT NULL DEFAULT TRUE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT kb_examples_source_ck CHECK (source IN ('setup_test', 'tile', 'owner'))
);
CREATE INDEX IF NOT EXISTS kb_examples_norm_ix ON session_state.kb_examples (workspace_id, question_norm);

CREATE TABLE IF NOT EXISTS session_state.setup_runs (
  run_id        TEXT PRIMARY KEY,
  workspace_id  TEXT NOT NULL REFERENCES session_state.workspaces (workspace_id) ON DELETE CASCADE,
  tenant_id     TEXT NOT NULL,
  state         TEXT NOT NULL DEFAULT 'running',
  graded_by     TEXT NOT NULL,
  score         INTEGER,
  total         INTEGER NOT NULL,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ,
  CONSTRAINT setup_runs_state_ck CHECK (state IN ('running', 'done', 'failed')),
  CONSTRAINT setup_runs_graded_ck CHECK (graded_by IN ('reference', 'owner'))
);

CREATE TABLE IF NOT EXISTS session_state.setup_results (
  run_id         TEXT NOT NULL REFERENCES session_state.setup_runs (run_id) ON DELETE CASCADE,
  tenant_id      TEXT NOT NULL,
  position       INTEGER NOT NULL,
  question       TEXT NOT NULL,
  reference_sql  TEXT,
  answer_text    TEXT,
  answer_sql     TEXT,
  verdict        TEXT NOT NULL DEFAULT 'pending',
  detail         TEXT,
  PRIMARY KEY (run_id, position),
  CONSTRAINT setup_results_verdict_ck CHECK (verdict IN ('pending', 'running', 'right', 'wrong', 'error'))
);

ALTER TABLE session_state.dashboards ADD COLUMN IF NOT EXISTS workspace_id TEXT
  REFERENCES session_state.workspaces (workspace_id) ON DELETE CASCADE;

-- Workspace documents (uploads), outside the sample corpus.
CREATE SCHEMA IF NOT EXISTS workspace_docs;

CREATE TABLE IF NOT EXISTS workspace_docs.documents (
  document_id   TEXT PRIMARY KEY,
  tenant_id     TEXT NOT NULL,
  workspace_id  TEXT NOT NULL REFERENCES session_state.workspaces (workspace_id) ON DELETE CASCADE,
  title         TEXT NOT NULL,
  filename      TEXT NOT NULL,
  pages         INTEGER NOT NULL,
  bytes         INTEGER NOT NULL,
  uploaded_by   TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workspace_docs.chunks (
  chunk_id      TEXT PRIMARY KEY,
  document_id   TEXT NOT NULL REFERENCES workspace_docs.documents (document_id) ON DELETE CASCADE,
  tenant_id     TEXT NOT NULL,
  workspace_id  TEXT NOT NULL,
  ordinal       INTEGER NOT NULL,
  page          INTEGER,
  content_md    TEXT NOT NULL,
  embedding     vector(1024) NOT NULL
);
CREATE INDEX IF NOT EXISTS workspace_chunks_ws_ix ON workspace_docs.chunks (tenant_id, workspace_id);

-- Grants. The history role owns the application's writes; the reader role only
-- reads workspace documents, for retrieval.
GRANT SELECT, INSERT, UPDATE, DELETE ON
  session_state.workspaces, session_state.business_context_versions, session_state.kb_columns,
  session_state.kb_definitions, session_state.kb_examples, session_state.setup_runs,
  session_state.setup_results
  TO statesync_history;
GRANT USAGE ON SCHEMA workspace_docs TO statesync_history, statesync_reader;
GRANT SELECT, INSERT, DELETE ON workspace_docs.documents, workspace_docs.chunks TO statesync_history;
GRANT SELECT ON workspace_docs.documents, workspace_docs.chunks TO statesync_reader;

-- Row-level security on every table in session_state and workspace_docs.
DO $$
DECLARE
  t record;
BEGIN
  FOR t IN
    SELECT n.nspname AS schema_name, c.relname AS table_name,
           EXISTS (SELECT 1 FROM pg_attribute a
                   WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped) AS has_tenant
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('session_state', 'workspace_docs') AND c.relkind = 'r'
  LOOP
    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', t.schema_name, t.table_name);
    IF t.has_tenant AND NOT EXISTS (
      SELECT 1 FROM pg_policies p
      WHERE p.schemaname = t.schema_name AND p.tablename = t.table_name AND p.policyname = 'tenant_isolation'
    ) THEN
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON %I.%I USING (tenant_id = current_setting(''statesync.tenant_id'', true)) '
        'WITH CHECK (tenant_id = current_setting(''statesync.tenant_id'', true))',
        t.schema_name, t.table_name);
    END IF;
  END LOOP;
END$$;

-- The two tables without a tenant column are scoped through their tile.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'session_state' AND tablename = 'tile_versions'
                 AND policyname = 'tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON session_state.tile_versions
      USING (EXISTS (SELECT 1 FROM session_state.tiles t WHERE t.tile_id = tile_versions.tile_id))
      WITH CHECK (EXISTS (SELECT 1 FROM session_state.tiles t WHERE t.tile_id = tile_versions.tile_id));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'session_state' AND tablename = 'tile_results'
                 AND policyname = 'tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON session_state.tile_results
      USING (EXISTS (SELECT 1 FROM session_state.tiles t WHERE t.tile_id = tile_results.tile_id))
      WITH CHECK (EXISTS (SELECT 1 FROM session_state.tiles t WHERE t.tile_id = tile_results.tile_id));
  END IF;
END$$;

COMMIT;

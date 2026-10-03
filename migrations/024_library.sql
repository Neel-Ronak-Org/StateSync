-- migrations/024_library.sql
-- The account-level document library (docs/V3_PLAN.md Phase 3c slice 5, D-v3-17).
--
-- Documents are uploaded once into folders that belong to the account (the
-- owner principal), not to one workspace; each workspace chooses which folders
-- its questions may search. Migration 021 created workspace_docs.documents and
-- .chunks tied to a single workspace; nothing was ever written to them (the
-- upload flow did not exist), so they are reshaped here rather than migrated:
--
--   workspace_docs.folders            a named folder in an account's library
--   workspace_docs.documents          now in a folder, not a workspace; carries
--                                     an ingestion status (processing | ready |
--                                     failed) because parsing and embedding run
--                                     in the background after the upload returns
--   workspace_docs.chunks             no workspace column; scoped through its
--                                     document
--   workspace_docs.workspace_folders  which folders a workspace searches
--
-- Retrieval only ever reads chunks of READY documents in folders the question's
-- workspace has chosen. The folder links live in workspace_docs, not
-- session_state, so the reader role (which runs retrieval) can read them
-- without being given any access to session_state; the history role owns
-- every write.
--
-- Row-level security: every new table gets RLS and the tenant policy
-- (tests/test_rls_coverage.py fails otherwise).
--
-- Idempotent: safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS workspace_docs.folders (
  folder_id           TEXT PRIMARY KEY,
  tenant_id           TEXT NOT NULL,
  owner_principal_id  TEXT NOT NULL,
  name                TEXT NOT NULL,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT folders_name_ck CHECK (length(btrim(name)) > 0),
  CONSTRAINT folders_name_unique UNIQUE (tenant_id, owner_principal_id, name)
);

-- documents: from "one workspace" to "one folder", plus ingestion status.
ALTER TABLE workspace_docs.documents DROP COLUMN IF EXISTS workspace_id;
ALTER TABLE workspace_docs.documents ADD COLUMN IF NOT EXISTS folder_id TEXT NOT NULL
  REFERENCES workspace_docs.folders (folder_id) ON DELETE CASCADE;
ALTER TABLE workspace_docs.documents ADD COLUMN IF NOT EXISTS content_type TEXT NOT NULL DEFAULT 'application/pdf';
ALTER TABLE workspace_docs.documents ADD COLUMN IF NOT EXISTS sha256 TEXT NOT NULL DEFAULT '';
ALTER TABLE workspace_docs.documents ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'processing';
ALTER TABLE workspace_docs.documents ADD COLUMN IF NOT EXISTS error TEXT;
ALTER TABLE workspace_docs.documents ADD COLUMN IF NOT EXISTS chunk_count INTEGER NOT NULL DEFAULT 0;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'documents_status_ck') THEN
    ALTER TABLE workspace_docs.documents ADD CONSTRAINT documents_status_ck
      CHECK (status IN ('processing', 'ready', 'failed'));
  END IF;
END$$;
CREATE INDEX IF NOT EXISTS documents_folder_ix ON workspace_docs.documents (tenant_id, folder_id);

-- chunks: scoped through their document.
DROP INDEX IF EXISTS workspace_docs.workspace_chunks_ws_ix;
ALTER TABLE workspace_docs.chunks DROP COLUMN IF EXISTS workspace_id;
CREATE INDEX IF NOT EXISTS chunks_doc_ix ON workspace_docs.chunks (document_id, ordinal);

CREATE TABLE IF NOT EXISTS workspace_docs.workspace_folders (
  workspace_id  TEXT NOT NULL REFERENCES session_state.workspaces (workspace_id) ON DELETE CASCADE,
  folder_id     TEXT NOT NULL REFERENCES workspace_docs.folders (folder_id) ON DELETE CASCADE,
  tenant_id     TEXT NOT NULL,
  PRIMARY KEY (workspace_id, folder_id)
);

-- Grants. Documents need UPDATE for their status; chunks are only inserted and deleted.
GRANT SELECT, INSERT, UPDATE, DELETE ON workspace_docs.folders TO statesync_history;
GRANT SELECT, INSERT, UPDATE, DELETE ON workspace_docs.documents TO statesync_history;
GRANT SELECT, INSERT, DELETE ON workspace_docs.workspace_folders TO statesync_history;
GRANT SELECT ON workspace_docs.folders TO statesync_reader;
GRANT SELECT ON workspace_docs.workspace_folders TO statesync_reader;

ALTER TABLE workspace_docs.folders ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_docs.workspace_folders ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'workspace_docs' AND tablename = 'folders'
                 AND policyname = 'tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON workspace_docs.folders
      USING (tenant_id = current_setting('statesync.tenant_id', true))
      WITH CHECK (tenant_id = current_setting('statesync.tenant_id', true));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'workspace_docs' AND tablename = 'workspace_folders'
                 AND policyname = 'tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON workspace_docs.workspace_folders
      USING (tenant_id = current_setting('statesync.tenant_id', true))
      WITH CHECK (tenant_id = current_setting('statesync.tenant_id', true));
  END IF;
END$$;

COMMIT;

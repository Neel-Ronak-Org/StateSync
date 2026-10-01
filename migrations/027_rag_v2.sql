BEGIN;
-- Upload chunks: structure and a stored tsvector for the keyword leg.
ALTER TABLE workspace_docs.chunks ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'text';
ALTER TABLE workspace_docs.chunks ADD COLUMN IF NOT EXISTS section TEXT;
ALTER TABLE workspace_docs.chunks ADD COLUMN IF NOT EXISTS tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('english', content_md)) STORED;
CREATE INDEX IF NOT EXISTS chunks_tsv_ix ON workspace_docs.chunks USING gin (tsv);
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chunks_kind_ck') THEN
    ALTER TABLE workspace_docs.chunks ADD CONSTRAINT chunks_kind_ck CHECK (kind IN ('text', 'table'));
  END IF;
END $$;

-- Sample corpus: a side table of tsvectors (public is pinned by the A5 fingerprint, so
-- its tables are not altered; indexes are outside A5 but a side table also stores length).
CREATE SCHEMA IF NOT EXISTS search;
CREATE TABLE IF NOT EXISTS search.sample_terms (
  chunk_id    TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  tenant_id   TEXT NOT NULL,
  tsv         tsvector NOT NULL,
  len         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sample_terms_tsv_ix ON search.sample_terms USING gin (tsv);
CREATE INDEX IF NOT EXISTS sample_terms_tenant_ix ON search.sample_terms (tenant_id);
INSERT INTO search.sample_terms (chunk_id, document_id, tenant_id, tsv, len)
  SELECT chunk_id, document_id, tenant_id, to_tsvector('english', content_md),
         length(to_tsvector('english', content_md))
  FROM public.document_chunks
ON CONFLICT (chunk_id) DO UPDATE SET tsv = EXCLUDED.tsv, len = EXCLUDED.len;
GRANT USAGE ON SCHEMA search TO statesync_reader;
GRANT SELECT ON search.sample_terms TO statesync_reader;
ALTER TABLE search.sample_terms ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'search' AND tablename = 'sample_terms'
                 AND policyname = 'tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON search.sample_terms
      USING (tenant_id = current_setting('statesync.tenant_id', true));
  END IF;
END $$;
COMMIT;

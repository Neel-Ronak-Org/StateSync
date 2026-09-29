-- migrations/025_definition_citations.sql
-- Business definitions found in uploaded documents (docs/V3_PLAN.md Phase 3c
-- slice 5, "definitions mined from documents").
--
-- A definition can now come from a document ('document' source) and then
-- carries WHERE it came from: the document, the chunk, the page, the title at
-- the time, and the exact sentence that defines the term. The orchestrator
-- keeps only proposals whose quote really occurs in the cited passage, so what
-- the owner is asked to approve can always be checked against the source.
--
-- A document-sourced definition is a draft like an AI one: it never replaces
-- the owner's own definition, and it is used only after the owner approves it.
-- An owner edit makes it the owner's (source 'owner'); the citation is kept so
-- the origin stays visible. Deleting the document does not delete the
-- definition (the owner may have approved it); the citation keeps the title.
--
-- kb_definitions already has RLS (migration 021). Idempotent.

BEGIN;

ALTER TABLE session_state.kb_definitions ADD COLUMN IF NOT EXISTS cite_document_id TEXT;
ALTER TABLE session_state.kb_definitions ADD COLUMN IF NOT EXISTS cite_chunk_id TEXT;
ALTER TABLE session_state.kb_definitions ADD COLUMN IF NOT EXISTS cite_page INTEGER;
ALTER TABLE session_state.kb_definitions ADD COLUMN IF NOT EXISTS cite_title TEXT;
ALTER TABLE session_state.kb_definitions ADD COLUMN IF NOT EXISTS cite_quote TEXT;

ALTER TABLE session_state.kb_definitions DROP CONSTRAINT IF EXISTS kb_definitions_source_ck;
ALTER TABLE session_state.kb_definitions ADD CONSTRAINT kb_definitions_source_ck
  CHECK (source IN ('ai', 'owner', 'document'));

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'kb_definitions_citation_ck') THEN
    ALTER TABLE session_state.kb_definitions ADD CONSTRAINT kb_definitions_citation_ck
      CHECK (source <> 'document' OR (cite_chunk_id IS NOT NULL AND cite_quote IS NOT NULL));
  END IF;
END$$;

COMMIT;

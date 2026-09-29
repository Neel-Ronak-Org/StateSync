-- migrations/022_kb_templates.sql
-- Query templates learned from approved examples (docs/V3_PLAN.md Phase 3c,
-- slice 3: evidence-tiered answering, the "compiled" tier).
--
-- An approved example's SQL is parsed once; its literals (dates, numbers, text
-- values, LIMITs) become typed slots and the rest is stored with $n
-- placeholders. A later question that differs only in those values is answered
-- by filling the slots in code (values validated by type, then the whole
-- statement goes through the same SQL gate as any other query), with no
-- SQL-model call.
--
-- Only approved examples produce templates, and a template is deleted with its
-- example: a withdrawn answer must not live on as a pattern.
--
-- Row-level security: this table lives in session_state, so it gets RLS and a
-- tenant policy like every other table there (migration 021; the coverage test
-- tests/test_rls_coverage.py fails without them).
--
-- Idempotent: safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS session_state.kb_templates (
  template_id    TEXT PRIMARY KEY,
  example_id     TEXT NOT NULL REFERENCES session_state.kb_examples (example_id) ON DELETE CASCADE,
  workspace_id   TEXT NOT NULL REFERENCES session_state.workspaces (workspace_id) ON DELETE CASCADE,
  tenant_id      TEXT NOT NULL,
  question       TEXT NOT NULL,
  question_norm  TEXT NOT NULL,
  template_sql   TEXT NOT NULL,
  slots          JSONB NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT kb_templates_one_per_example UNIQUE (example_id)
);
CREATE INDEX IF NOT EXISTS kb_templates_ws_ix ON session_state.kb_templates (tenant_id, workspace_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON session_state.kb_templates TO statesync_history;

ALTER TABLE session_state.kb_templates ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'session_state' AND tablename = 'kb_templates'
                 AND policyname = 'tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON session_state.kb_templates
      USING (tenant_id = current_setting('statesync.tenant_id', true))
      WITH CHECK (tenant_id = current_setting('statesync.tenant_id', true));
  END IF;
END$$;

COMMIT;

-- migrations/023_example_checks.sql
-- The self-testing workspace (docs/V3_PLAN.md Phase 3c, slice 4).
--
-- Approved examples are answers StateSync will give again without a model
-- (the Verified tier) or reuse as templates (the Compiled tier). If the data
-- source changes underneath them -- a column renamed, a table emptied -- they
-- keep being trusted while being wrong. So each example's query is re-run on a
-- schedule, with no model, and its result SHAPE (column names; whether it
-- returns rows) is compared with the shape recorded the first time:
--
--   result_signature  the shape recorded at the first check
--   check_state       ok | changed | failed  (NULL until first checked)
--   checked_at        when it was last checked
--   check_detail      the error or the difference, for the owner
--
-- Values are not compared: live data changes legitimately.
-- kb_examples already has RLS (migration 021); new columns inherit it.
-- Idempotent: safe to re-run.

BEGIN;

ALTER TABLE session_state.kb_examples ADD COLUMN IF NOT EXISTS result_signature TEXT;
ALTER TABLE session_state.kb_examples ADD COLUMN IF NOT EXISTS check_state TEXT;
ALTER TABLE session_state.kb_examples ADD COLUMN IF NOT EXISTS checked_at TIMESTAMPTZ;
ALTER TABLE session_state.kb_examples ADD COLUMN IF NOT EXISTS check_detail TEXT;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'kb_examples_check_state_ck') THEN
    ALTER TABLE session_state.kb_examples ADD CONSTRAINT kb_examples_check_state_ck
      CHECK (check_state IS NULL OR check_state IN ('ok', 'changed', 'failed'));
  END IF;
END$$;

COMMIT;

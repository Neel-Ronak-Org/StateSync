-- migrations/018_turn_target_schema.sql
-- Record which dataset (schema) each conversation turn was answered from.
--
-- WHY. A conversation is tied to one dataset: switching dataset in the UI starts a
-- new conversation. But the server never stored which dataset a turn used, so
-- reopening a past conversation could not restore it, and a follow-up sent with a
-- different `schema` was answered from the other corpus while its context (the
-- earlier turns) came from the first one -- a number that mixes two datasets
-- without saying so. Found building the Phase 2 history sidebar
-- (docs/V3_PLAN.md). With the column: the session list and replay carry the
-- dataset, and /query refuses a follow-up whose dataset differs from the
-- conversation's (history.check_session reports it).
--
-- Nullable: turns written before this migration have no recorded dataset and are
-- left unchecked rather than guessed. statesync_history's table-level INSERT and
-- SELECT grants (migration 011) cover the new column. Idempotent.

BEGIN;

ALTER TABLE session_state.conversation_turns
  ADD COLUMN IF NOT EXISTS target_schema TEXT;

COMMIT;

-- migrations/012_conversation_turn_artifacts.sql
--
-- Store the ARTIFACTS of a turn alongside its prose, so a resumed conversation
-- renders what the user originally saw instead of a transcript of it.
--
-- WHY THIS IS ADDITIVE AND NULLABLE. 011 shipped storing prose, SQL, route and
-- the two rewrite flags. Every turn written before this migration has none of
-- the columns below, and must keep resuming cleanly -- so every column is
-- nullable with no default backfill, and the read path treats NULL as "this
-- turn predates artifact storage", not as "this turn had no chart".
--
-- WHAT IS DELIBERATELY *NOT* STORED IN FULL: sql_rows.
-- A single turn may return up to mcp.max_rows_default (1000) rows, and the
-- hard cap is 5000. Persisting every result set in full grows this table
-- without bound -- the same failure mode V2_STRATEGY §3b names for
-- session-scoped document upload ("needs a deletion policy or storage grows
-- without bound"), which is why that was deferred rather than shipped. Only a
-- capped PREVIEW is stored (mcp.history_row_preview_max), alongside the TRUE
-- row count, so the UI can say "showing 100 of 4,812" honestly rather than
-- silently presenting a truncated table as the whole result.
--
-- PRIVACY, STATED PLAINLY. sql_rows_preview holds real rows from the
-- relational plane -- not a summary of them. §3b already names stored answers
-- as an exfiltration target because they contain real numbers; this is the
-- data itself, duplicated into a second table that is still not
-- access-controlled (history shipped without auth, cycle 007 §3). That is the
-- reason the orchestrator's session-browser routes are gated behind
-- STATESYNC_SESSION_BROWSER and off by default, and the reason the preview is
-- capped rather than complete: the smallest thing that makes a resumed
-- conversation legible, not the largest thing that would fit.
--
-- STALENESS. §3b's open question ("a conversation resumed days later sits on
-- data that has moved") is NOT answered here, and storing artifacts makes it
-- sharper: prose reads as a record of what was said, but a rendered TABLE
-- reads as live data. This migration therefore does not add a resume path that
-- re-runs anything. `computed_at` already exists on every row and the UI stamps
-- restored artifacts with it, which is §3b's minimum bar ("answers stamped with
-- when they were computed, at minimum") -- the choice between showing the
-- stored answer and re-running the query stays open.
--
-- No new GRANTs are required: 011 granted SELECT, INSERT on the table itself to
-- statesync_history, and a table-level grant in PostgreSQL covers columns added
-- later. Verified after applying.

ALTER TABLE session_state.conversation_turns
  ADD COLUMN IF NOT EXISTS chart            JSONB,
  ADD COLUMN IF NOT EXISTS sql_columns      JSONB,
  ADD COLUMN IF NOT EXISTS sql_rows_preview JSONB,
  ADD COLUMN IF NOT EXISTS sql_rows_count   INTEGER;

COMMENT ON COLUMN session_state.conversation_turns.chart IS
  'The A7-validated Vega-Lite spec the user was shown, or NULL. Safe to store: '
  'A7 forbids expression strings, so the spec carries no executable content, '
  'and it is re-validated by both validators before it is ever rendered again.';

COMMENT ON COLUMN session_state.conversation_turns.sql_columns IS
  'Column names of the result set, JSON array. NULL for a docs-route turn or a '
  'turn written before migration 012.';

COMMENT ON COLUMN session_state.conversation_turns.sql_rows_preview IS
  'AT MOST mcp.history_row_preview_max rows, not the full result set. Compare '
  'against sql_rows_count to know whether this is complete; never present it as '
  'the whole result when it is not.';

COMMENT ON COLUMN session_state.conversation_turns.sql_rows_count IS
  'TRUE row count of the original result set, independent of how many rows '
  'sql_rows_preview actually holds. This is what makes the truncation visible.';

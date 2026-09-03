-- migrations/015_turn_rows.sql
-- Full result-set storage per conversation turn: session_state.turn_rows.
--
-- WHY A SEPARATE TABLE RATHER THAN COLUMNS ON CONVERSATION_TURNS.
-- history.get_recent_turns is called by the multi-turn rewrite node on EVERY
-- follow-up query to reconstruct conversational context. Dragging megabytes
-- of raw result rows through that hot path would be an unacceptable latency
-- and memory regression. By storing full rows in an auxiliary table,
-- get_recent_turns reads only lightweight prose and capped previews, while
-- full result sets are loaded on demand via history.get_turn_rows.
--
-- TRUNCATION VISIBILITY: STORED_ROW_COUNT VS SQL_ROWS_COUNT.
-- stored_row_count records how many rows actually fit within the row and byte
-- bounds (cfg.mcp.history_full_rows_max and history_full_rows_max_bytes).
-- When a result set is truncated by either bound, stored_row_count will be
-- strictly LESS than conversation_turns.sql_rows_count (the TRUE count of the
-- original execution). The difference between the two is exactly what makes
-- truncation visible to callers and users rather than silent.
--
-- DISPOSABLE BY DESIGN.
-- session_state.turn_rows is explicitly disposable. Pruning expired rows via
-- the retention policy degrades a turn back to its preview
-- (conversation_turns.sql_rows_preview) and never loses the true row count
-- (sql_rows_count). When rows are absent or pruned, get_turn_rows returns
-- available: false and callers seamlessly fall back to preview data.
--
-- WHY A5 SCHEMA FINGERPRINT IS UNAFFECTED.
-- Comments and DDL in session_state do not affect the A5 schema fingerprint.
-- As verified in mcp/fingerprint.py, compute_fingerprint computes the hash
-- strictly over information_schema.tables WHERE table_schema = 'public'
-- (and pg_constraint foreign keys WHERE nspname = 'public'). The session_state
-- schema is structurally invisible to A5, leaving the schema fingerprint
-- byte-identical before and after this migration.
--
-- ROLE AND PRIVILEGES.
-- Reuses the existing statesync_history role (created in migration 011).
-- Granted SELECT, INSERT, DELETE on session_state.turn_rows to statesync_history.
-- DELETE is required because statesync_history performs inline retention
-- pruning during turn writes. Explicitly NOT granted to statesync_reader:
-- statesync_reader has no access to session_state, preserving Layer 3
-- SELECT-only boundaries on the application schema.
--
-- IDEMPOTENCY.
-- Like 011-014, this migration must be safely re-runnable by hand since
-- scripts/migrate.sh runs only 001/002/004 and there is no migration ledger.

CREATE SCHEMA IF NOT EXISTS session_state;

CREATE TABLE IF NOT EXISTS session_state.turn_rows (
  turn_id          BIGINT PRIMARY KEY REFERENCES session_state.conversation_turns(turn_id) ON DELETE CASCADE,
  tenant_id        TEXT NOT NULL,
  principal_id     TEXT NOT NULL,
  rows             JSONB NOT NULL,
  stored_row_count INTEGER NOT NULL,
  byte_size        INTEGER NOT NULL,
  computed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS turn_rows_computed_at_ix
  ON session_state.turn_rows (computed_at);

CREATE INDEX IF NOT EXISTS turn_rows_scope_ix
  ON session_state.turn_rows (tenant_id, principal_id);

GRANT SELECT, INSERT, DELETE ON session_state.turn_rows TO statesync_history;

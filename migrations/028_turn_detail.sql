-- migrations/028_turn_detail.sql
--
-- Store what the Pipeline Inspector and the source chips need with each turn, so a conversation reopened
-- from the sidebar (or after a reload) can still be inspected and still shows its sources.
--
-- `detail` is {"state_trace": {...}, "citations": [...], "latency_ms": n}: the response's trace without its
-- result rows (those are already stored as sql_rows_preview here and in full in turn_rows), the citations and
-- the total time. history.append_turn caps its size (mcp.history_detail_max_bytes); a larger one is stored
-- without the passages' text, and if still too large, not at all.
--
-- Nullable, no backfill: a turn written before this migration has no detail, and the web app then simply
-- shows no Inspector for it. No new grants: 011 granted SELECT, INSERT on the table to statesync_history.

ALTER TABLE session_state.conversation_turns ADD COLUMN IF NOT EXISTS detail jsonb;

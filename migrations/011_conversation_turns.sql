-- migrations/011_conversation_turns.sql
-- Cycle 007, Workstream A (multi-turn): session-scoped conversation history.
-- Owner direction 2026-08-24; write-path design confirmed by the owner
-- (docs/cycles/007.md §3) after CYCLE_PROTOCOL.md §4.3 flagged "write
-- capability stays absent" as an OWNER decision this touches.
--
-- WHY A SEPARATE SCHEMA, same reasoning as migrations/007's olist schema.
-- A5 defines the fingerprint over every table in `public`
-- (mcp/fingerprint.py: `information_schema.tables WHERE table_schema =
-- 'public'`). A live, per-query table that grows on every conversation
-- turn is the single worst candidate to add to `public` -- it would move
-- the fingerprint on this migration AND keep moving it (row inserts don't
-- move a schema fingerprint, but this table's mere existence in `public`
-- would). Putting it in its own schema, `session_state`, keeps it
-- structurally invisible to A5 exactly the way `olist` is: fingerprinted
-- before and after, expected byte-identical, recorded in cycle 007's
-- build log rather than assumed.
--
-- WHY A NEW ROLE, not statesync_reader or statesync_ingest.
-- statesync_reader is Layer 3 of the SELECT-only enforcement stack (A4.5)
-- and the Build Spec calls its lack of write privilege "the actual
-- guarantee" M8's security claim rests on. Granting it INSERT on anything,
-- even one table, makes that sentence false as written. statesync_ingest
-- is "never used by orchestrator or agents" (migrations/001's own comment)
-- -- an operator-run batch role, not a live per-query one; reusing it here
-- would erase that boundary instead of extending it.
-- statesync_history is a THIRD role: LOGIN, scoped to INSERT + SELECT on
-- exactly `session_state.conversation_turns` and nothing else -- no grant
-- on `public`, no grant on `olist`, no USAGE on any other schema. It is
-- used by exactly two hand-written MCP tools (history.append_turn,
-- history.get_recent_turns) that build fixed-shape parameterized queries
-- and never accept a SQL string from an LLM -- the same invariant that
-- lets memory.put_trajectory write to Qdrant live, per-query, without
-- being a counterexample to "the agent's SQL path never writes". No
-- DELETE grant: nothing in this cycle's live path needs to delete a row,
-- and the reversibility this table must demonstrate (C2) is performed as
-- statesync_migrator, the same role that runs this migration, not as a
-- standing capability of the live role.
--
-- NEW ATTACK SURFACE THIS ACTUALLY ADDS, named rather than waved past:
-- raw_query/rewritten_query/answer_text are user-authored text, now
-- written to a row that a LATER turn's rewrite_node reads back and puts
-- into an LLM prompt as context. That is a stored-prompt-injection vector
-- this project has never had before (M8 Class 1 tests injection through
-- DATA the agent retrieves; this is injection through the agent's OWN
-- prior turn). orchestrator/nodes/rewrite.py's prompt construction labels
-- retrieved history as untrusted prior-turn data, not instructions -- see
-- that file's docstring -- but no adversarial test class covers this yet.
-- Recorded in docs/cycles/007.md §5/§6 and carried to open_items.md as a
-- gap for a future M8 class, not fixed by a security control this cycle
-- did not scope to build.

CREATE SCHEMA IF NOT EXISTS session_state;

CREATE TABLE IF NOT EXISTS session_state.conversation_turns (
  turn_id             BIGSERIAL PRIMARY KEY,
  tenant_id           TEXT NOT NULL,
  session_id          TEXT NOT NULL,
  turn_index          INTEGER NOT NULL,
  raw_query           TEXT NOT NULL,
  rewritten_query      TEXT NOT NULL,
  rewrite_applied      BOOLEAN NOT NULL DEFAULT FALSE,
  context_guessed      BOOLEAN NOT NULL DEFAULT FALSE,
  route               TEXT,
  executed_sql        TEXT,
  answer_text         TEXT NOT NULL,
  computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, session_id, turn_index)
);

CREATE INDEX IF NOT EXISTS conversation_turns_session_ix
  ON session_state.conversation_turns (tenant_id, session_id, turn_index);

-- statesync_history: LOGIN role, table-scoped write path (see comment above).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'statesync_history') THEN
    CREATE ROLE statesync_history LOGIN;
  END IF;
END$$;

GRANT CONNECT ON DATABASE statesync TO statesync_history;
GRANT USAGE ON SCHEMA session_state TO statesync_history;
GRANT SELECT, INSERT ON session_state.conversation_turns TO statesync_history;
GRANT USAGE, SELECT ON SEQUENCE session_state.conversation_turns_turn_id_seq TO statesync_history;

-- Explicitly NOT granted to statesync_history: UPDATE, DELETE on this table;
-- USAGE on `public` or `olist`; anything on any other object. Explicitly NOT
-- granted to statesync_reader: anything on `session_state` -- the SQL agent's
-- own read path cannot see this schema at all, same defense-in-depth
-- statesync_reader already has with respect to `olist` before migration 007
-- granted it USAGE there on purpose.

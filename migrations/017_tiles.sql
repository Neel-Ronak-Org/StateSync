-- migrations/017_tiles.sql
-- Dashboard tiles (docs/V3_PLAN.md Phase 1): a pinned answer that re-runs its
-- SAVED SQL on a schedule, instead of asking the model again.
--
-- WHY THE SQL IS STORED, NOT THE QUESTION. Re-asking the model lets the query
-- drift between runs -- the Olist demo reported 96,478 delivered orders in one
-- turn and 96,470 in another because the model wrote the query differently.
-- A tile runs the exact statement the user approved, every time.
--
-- WHERE A TILE'S SQL MAY COME FROM. Only from a conversation turn the owner
-- already has (history.* stores what the pipeline actually executed), never
-- from a client. The tiles API accepts no SQL string at all; see
-- mcp/tools/tile_tools.py.
--
-- THREE TABLES.
--   tiles          one row per tile: owner, title, state, refresh interval,
--                  and which version is current.
--   tile_versions  every version a tile has had (pin = v1, each replace adds
--                  one); the SQL, the chart TEMPLATE (data.values stripped --
--                  rows are bound in at read time, the way the synthesizer does
--                  for a live answer), and the fingerprint of the columns the
--                  SQL reads, used to notice a schema change.
--   tile_results   the latest SUCCESSFUL result only (rows capped by
--                  tiles.result_max_*). A failed refresh is recorded on the tile
--                  (last_error, error_at) and never stored as a result, so an
--                  empty result can only mean the query really returned nothing.
--
-- OWNERSHIP. Rows carry tenant_id and principal_id. Every read and write in
-- tile_tools.py filters on both, so a tile_id alone never reaches a tile.
--
-- GRANTS. statesync_history gets SELECT/INSERT/UPDATE/DELETE on the three
-- tables: tiles are user-owned state that the user edits and deletes, unlike
-- the audit log (insert-only). No other role gains anything.
--
-- The A5 schema fingerprint is unaffected (it hashes the public schema only;
-- these tables live in session_state). Idempotent, like 011-016: safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS session_state.tiles (
  tile_id            TEXT PRIMARY KEY,
  tenant_id          TEXT NOT NULL,
  principal_id       TEXT NOT NULL,
  title              TEXT NOT NULL,
  current_version    INTEGER NOT NULL,
  state              TEXT NOT NULL DEFAULT 'ok',
  refresh_interval_s INTEGER NOT NULL,
  last_error         TEXT,
  error_at           TIMESTAMPTZ,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT tiles_state_ck CHECK (state IN ('ok', 'needs_review', 'error')),
  CONSTRAINT tiles_interval_ck CHECK (refresh_interval_s > 0)
);

CREATE INDEX IF NOT EXISTS tiles_owner_ix
  ON session_state.tiles (tenant_id, principal_id);

CREATE TABLE IF NOT EXISTS session_state.tile_versions (
  tile_id            TEXT NOT NULL REFERENCES session_state.tiles (tile_id) ON DELETE CASCADE,
  version            INTEGER NOT NULL,
  sql_text           TEXT NOT NULL,
  chart              JSONB,
  sql_columns        JSONB,
  fingerprint        TEXT NOT NULL,
  source_session_id  TEXT NOT NULL,
  source_turn_index  INTEGER NOT NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tile_id, version)
);

CREATE TABLE IF NOT EXISTS session_state.tile_results (
  tile_id      TEXT PRIMARY KEY REFERENCES session_state.tiles (tile_id) ON DELETE CASCADE,
  version      INTEGER NOT NULL,
  rows         JSONB NOT NULL,
  row_count    INTEGER NOT NULL,
  truncated    BOOLEAN NOT NULL DEFAULT FALSE,
  computed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT, UPDATE, DELETE ON session_state.tiles TO statesync_history;
GRANT SELECT, INSERT, UPDATE, DELETE ON session_state.tile_versions TO statesync_history;
GRANT SELECT, INSERT, UPDATE, DELETE ON session_state.tile_results TO statesync_history;

COMMIT;

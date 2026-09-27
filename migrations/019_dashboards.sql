-- migrations/019_dashboards.sql
-- Dashboards (docs/V3_PLAN.md Phase 2b): several named dashboards per dataset,
-- each holding tiles with a stored layout and a presentation choice.
--
-- WHAT CHANGES.
--   dashboards      one row per dashboard: owner, name, the dataset it is
--                   about (target_schema), and its tab position.
--   tiles           gains dashboard_id (every tile belongs to exactly one
--                   dashboard of the same owner and dataset), target_schema
--                   (the dataset of the turn it was pinned from), viz (how the
--                   owner chose to show it; NULL = decided from the data), and
--                   layout_x/y/w/h (its slot on a 12-column grid; NULL = placed
--                   by the client at the bottom).
--
-- EXISTING TILES are kept: each gets the dataset of the turn it was pinned from,
-- and is attached to an "Overview" dashboard created for that owner and dataset.
--
-- OWNERSHIP. Dashboards carry tenant_id and principal_id, and every statement in
-- mcp/tools/dashboard_tools.py filters on both, exactly like tiles (017).
--
-- GRANTS.
--   statesync_history: SELECT/INSERT/UPDATE/DELETE on dashboards (user-owned
--   state, like tiles), and now DELETE on conversation_turns, so a person can
--   erase their own conversation (history.delete_session; turn_rows cascade).
--   The audit log stays insert-only: deleting a conversation does not delete the
--   record that it existed or that it was deleted.
--
-- The A5 fingerprint is unaffected (session_state is not hashed). Idempotent:
-- safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS session_state.dashboards (
  dashboard_id   TEXT PRIMARY KEY,
  tenant_id      TEXT NOT NULL,
  principal_id   TEXT NOT NULL,
  name           TEXT NOT NULL,
  target_schema  TEXT NOT NULL,
  position       INTEGER NOT NULL DEFAULT 0,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT dashboards_name_ck CHECK (length(btrim(name)) > 0)
);

CREATE INDEX IF NOT EXISTS dashboards_owner_ix
  ON session_state.dashboards (tenant_id, principal_id, target_schema);

ALTER TABLE session_state.tiles ADD COLUMN IF NOT EXISTS dashboard_id TEXT
  REFERENCES session_state.dashboards (dashboard_id) ON DELETE CASCADE;
ALTER TABLE session_state.tiles ADD COLUMN IF NOT EXISTS target_schema TEXT;
ALTER TABLE session_state.tiles ADD COLUMN IF NOT EXISTS viz TEXT;
ALTER TABLE session_state.tiles ADD COLUMN IF NOT EXISTS layout_x INTEGER;
ALTER TABLE session_state.tiles ADD COLUMN IF NOT EXISTS layout_y INTEGER;
ALTER TABLE session_state.tiles ADD COLUMN IF NOT EXISTS layout_w INTEGER;
ALTER TABLE session_state.tiles ADD COLUMN IF NOT EXISTS layout_h INTEGER;

CREATE INDEX IF NOT EXISTS tiles_dashboard_ix ON session_state.tiles (dashboard_id);

-- Existing tiles: the dataset of the turn each was pinned from (turns written
-- before migration 018 have none; they were all on public).
UPDATE session_state.tiles t
SET target_schema = COALESCE((
      SELECT ct.target_schema
      FROM session_state.tile_versions v
      JOIN session_state.conversation_turns ct
        ON ct.tenant_id = t.tenant_id
       AND ct.session_id = v.source_session_id
       AND ct.turn_index = v.source_turn_index
      WHERE v.tile_id = t.tile_id AND v.version = t.current_version
    ), 'public')
WHERE t.target_schema IS NULL;

-- One "Overview" per owner and dataset that has tiles but no dashboard yet.
INSERT INTO session_state.dashboards (dashboard_id, tenant_id, principal_id, name, target_schema)
SELECT gen_random_uuid()::text, o.tenant_id, o.principal_id, 'Overview', o.target_schema
FROM (SELECT DISTINCT tenant_id, principal_id, target_schema
      FROM session_state.tiles WHERE dashboard_id IS NULL) o
WHERE NOT EXISTS (
  SELECT 1 FROM session_state.dashboards d
  WHERE d.tenant_id = o.tenant_id AND d.principal_id = o.principal_id
    AND d.target_schema = o.target_schema
);

UPDATE session_state.tiles t
SET dashboard_id = (
  SELECT d.dashboard_id FROM session_state.dashboards d
  WHERE d.tenant_id = t.tenant_id AND d.principal_id = t.principal_id
    AND d.target_schema = t.target_schema
  ORDER BY d.position, d.created_at
  LIMIT 1
)
WHERE t.dashboard_id IS NULL;

ALTER TABLE session_state.tiles ALTER COLUMN dashboard_id SET NOT NULL;
ALTER TABLE session_state.tiles ALTER COLUMN target_schema SET NOT NULL;

-- Bounds, mirrored in dashboard_tools.py (which reports them as 422s; these
-- constraints are the backstop).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tiles_viz_ck') THEN
    ALTER TABLE session_state.tiles ADD CONSTRAINT tiles_viz_ck CHECK (
      viz IS NULL OR viz IN ('kpi', 'kpi_trend', 'line', 'area', 'bar', 'hbar',
                             'donut', 'breakdown', 'heatmap', 'scatter', 'table'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tiles_layout_ck') THEN
    ALTER TABLE session_state.tiles ADD CONSTRAINT tiles_layout_ck CHECK (
      (layout_x IS NULL AND layout_y IS NULL AND layout_w IS NULL AND layout_h IS NULL)
      OR (layout_x BETWEEN 0 AND 11 AND layout_w BETWEEN 1 AND 12
          AND layout_x + layout_w <= 12
          AND layout_y BETWEEN 0 AND 1000 AND layout_h BETWEEN 2 AND 40));
  END IF;
END$$;

GRANT SELECT, INSERT, UPDATE, DELETE ON session_state.dashboards TO statesync_history;
GRANT DELETE ON session_state.conversation_turns TO statesync_history;

COMMIT;

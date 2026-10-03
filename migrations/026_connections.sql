-- migrations/026_connections.sql
-- Your own Postgres (docs/V3_PLAN.md Phase 3c slice 6, D-v3-17, D-v3-18).
--
-- A connection is an account-level library item, like a document folder: the
-- owner adds it once and any of their workspaces can use it. Its id
-- (cn_<16 hex>) doubles as the DATASET NAME everywhere a dataset is named --
-- a workspace's target_schema, a tile's, a turn's -- so every existing path
-- (questions, the setup scan, tiles, dashboards, answer checks) reaches the
-- right database without new plumbing. Only the MCP resolves the name, for the
-- owning tenant, to a host and a schema (mcp/connections.py).
--
-- The password is stored ENCRYPTED (Fernet, key held only by the MCP as a
-- Docker secret); it is never returned by any action. A connection is saved
-- only after the MCP has checked that its login is really read-only.
--
-- RLS and the tenant policy as for every table in session_state. Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS session_state.connections (
  connection_id       TEXT PRIMARY KEY,
  tenant_id           TEXT NOT NULL,
  owner_principal_id  TEXT NOT NULL,
  name                TEXT NOT NULL,
  host                TEXT NOT NULL,
  port                INTEGER NOT NULL,
  dbname              TEXT NOT NULL,
  username            TEXT NOT NULL,
  password_enc        TEXT NOT NULL,
  sslmode             TEXT NOT NULL DEFAULT 'prefer',
  schema_name         TEXT,
  status              TEXT NOT NULL DEFAULT 'ok',
  check_detail        TEXT,
  checked_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT connections_id_ck CHECK (connection_id ~ '^cn_[0-9a-f]{16}$'),
  CONSTRAINT connections_name_ck CHECK (length(btrim(name)) > 0),
  CONSTRAINT connections_port_ck CHECK (port BETWEEN 1 AND 65535),
  CONSTRAINT connections_status_ck CHECK (status IN ('ok', 'failed')),
  CONSTRAINT connections_sslmode_ck CHECK (sslmode IN ('disable', 'prefer', 'require', 'verify-ca', 'verify-full')),
  CONSTRAINT connections_name_unique UNIQUE (tenant_id, owner_principal_id, name)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON session_state.connections TO statesync_history;

ALTER TABLE session_state.connections ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'session_state' AND tablename = 'connections'
                 AND policyname = 'tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON session_state.connections
      USING (tenant_id = current_setting('statesync.tenant_id', true))
      WITH CHECK (tenant_id = current_setting('statesync.tenant_id', true));
  END IF;
END$$;

COMMIT;

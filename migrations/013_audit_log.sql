-- migrations/013_audit_log.sql
-- Per-identity audit logging: session_state.audit_log table.
--
-- WHY A SEPARATE TABLE, not columns on session_state.conversation_turns.
-- An audit log must record requests that produced NO turn at all (a rejected
-- token, a rate-limited call, an unauthenticated route attempt, or a tenant
-- rejection). Those requests have no turn_index, no raw_query, and no
-- answer_text. Folding audit logging into conversation_turns would require
-- every history column to become nullable and would destroy that table's own
-- NOT NULL invariants. Keeping audit_log as an independent table preserves
-- conversation_turns' schema invariants while allowing all requests to be
-- audited uniformly.
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
-- Granted INSERT, SELECT on session_state.audit_log and USAGE, SELECT on
-- session_state.audit_log_audit_id_seq to statesync_history.
-- Explicitly NOT granted to statesync_reader: statesync_reader has no access
-- to session_state, preserving Layer 3 SELECT-only boundaries on the
-- application schema.

CREATE SCHEMA IF NOT EXISTS session_state;

CREATE TABLE IF NOT EXISTS session_state.audit_log (
  audit_id     BIGSERIAL PRIMARY KEY,
  ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
  principal_id TEXT NOT NULL,
  tenant_id    TEXT NOT NULL,
  route        TEXT NOT NULL,
  outcome      TEXT NOT NULL,
  query_id     TEXT NULL,
  session_id   TEXT NULL,
  client_ip    TEXT NULL,
  detail       TEXT NULL
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_constraint con
    JOIN pg_catalog.pg_class rel ON rel.oid = con.conrelid
    JOIN pg_catalog.pg_namespace nsp ON nsp.oid = rel.relnamespace
    WHERE nsp.nspname = 'session_state'
      AND rel.relname = 'audit_log'
      AND con.conname = 'audit_log_outcome_ck'
  ) THEN
    ALTER TABLE session_state.audit_log
      ADD CONSTRAINT audit_log_outcome_ck
      CHECK (outcome IN ('allowed', 'denied_auth', 'denied_rate', 'error', 'denied_tenant'));
  END IF;
END$$;

CREATE INDEX IF NOT EXISTS audit_log_principal_ix
  ON session_state.audit_log (principal_id, ts DESC);

CREATE INDEX IF NOT EXISTS audit_log_tenant_ts_ix
  ON session_state.audit_log (tenant_id, ts DESC);

GRANT INSERT, SELECT ON session_state.audit_log TO statesync_history;
GRANT USAGE, SELECT ON SEQUENCE session_state.audit_log_audit_id_seq TO statesync_history;

-- migrations/014_conversation_turns_principal.sql
-- Scope conversation history by principal: session_state.conversation_turns.principal_id.
--
-- WHY PRINCIPAL_ID ON CONVERSATION_TURNS.
-- Bearer-token authentication shipped in C1, but conversation_turns was scoped
-- by tenant_id + session_id alone. Two principals sharing a tenant could
-- enumerate and read each other's conversations. That cross-principal exposure
-- within a tenant is why the session browser routes were env-gated
-- (STATESYNC_SESSION_BROWSER) and disabled by default. Adding principal_id to
-- conversation_turns is what actually closes this vulnerability.
--
-- THE SENTINEL AND HISTORICAL ROWS.
-- Rows written before authentication existed cannot be attributed to anyone.
-- They are backfilled with the sentinel literal 'p_preauth_legacy', an ID that
-- no real authenticated principal holds. As a deliberate, visible consequence,
-- legacy rows become invisible to every authenticated caller by construction
-- rather than being shown to an arbitrary one. Existing conversation history
-- disappears from the UI, and this is the correct default because the alternative
-- is showing one user another user's history.
--
-- WHY A5 SCHEMA FINGERPRINT IS UNAFFECTED.
-- Comments and DDL in session_state do not affect the A5 schema fingerprint.
-- As verified in mcp/fingerprint.py, compute_fingerprint computes the hash
-- strictly over information_schema.tables WHERE table_schema = 'public'
-- (and pg_constraint foreign keys WHERE nspname = 'public'). The session_state
-- schema is structurally invisible to A5, leaving the schema fingerprint
-- byte-identical before and after this migration.
--
-- NO NEW GRANTS OR CONSTRAINT CHANGES.
-- statesync_history already holds INSERT and SELECT on session_state.conversation_turns
-- from migration 011; PostgreSQL table-level grants automatically cover new columns.
-- The existing unique constraint on (tenant_id, session_id, turn_index) is unchanged.
-- CORRECTED 2026-09-26 (PR #2 review; docs/V3_PLAN.md 0.2): this used to say
-- session_id is server-generated and therefore unique across principals. It is
-- not -- /query accepts a client-supplied session_id, and the web client
-- generates one. Uniqueness across principals is now enforced by ownership
-- instead: a session belongs to the principal who wrote its first turn, and
-- history.append_turn / history.check_session refuse anyone else.
--
-- IDEMPOTENCY.
-- Like 011-013, this migration must be safely re-runnable by hand since
-- scripts/migrate.sh runs only 001/002/004 and there is no migration ledger.

-- 1. Add principal_id column if not present.
ALTER TABLE session_state.conversation_turns
  ADD COLUMN IF NOT EXISTS principal_id TEXT;

-- 2. Backfill existing rows with sentinel literal.
UPDATE session_state.conversation_turns
  SET principal_id = 'p_preauth_legacy'
  WHERE principal_id IS NULL;

-- 3. Set NOT NULL guarded against re-execution errors.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_attribute att
    JOIN pg_catalog.pg_class rel ON rel.oid = att.attrelid
    JOIN pg_catalog.pg_namespace nsp ON nsp.oid = rel.relnamespace
    WHERE nsp.nspname = 'session_state'
      AND rel.relname = 'conversation_turns'
      AND att.attname = 'principal_id'
      AND att.attnotnull = TRUE
  ) THEN
    ALTER TABLE session_state.conversation_turns
      ALTER COLUMN principal_id SET NOT NULL;
  END IF;
END$$;

-- 4. Create principal-scoped index.
CREATE INDEX IF NOT EXISTS conversation_turns_principal_ix
  ON session_state.conversation_turns (tenant_id, principal_id, session_id, turn_index);

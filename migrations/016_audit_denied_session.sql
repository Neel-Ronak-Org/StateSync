-- migrations/016_audit_denied_session.sql
-- Adds 'denied_session' to session_state.audit_log's outcome check.
--
-- WHY. A conversation now belongs to the principal who wrote its first turn
-- (docs/V3_PLAN.md 0.2). The web client generates session ids, so /query
-- refuses a session id owned by another principal before any model call, and
-- that refusal is an audit event. None of the existing outcomes names it:
-- 'denied_auth' reads as a bad or missing token, and 'denied_tenant' as a
-- cross-tenant attempt. Recording it under either would make the log say
-- something other than what happened.
--
-- IDEMPOTENT, like every migration scripts/migrate.sh re-runs: the constraint
-- is dropped if present and re-created with the widened list.
--
-- The A5 schema fingerprint is unaffected: it hashes the public schema only
-- (mcp/fingerprint.py), and this touches session_state.

BEGIN;

ALTER TABLE session_state.audit_log
  DROP CONSTRAINT IF EXISTS audit_log_outcome_ck;

ALTER TABLE session_state.audit_log
  ADD CONSTRAINT audit_log_outcome_ck
  CHECK (outcome IN ('allowed', 'denied_auth', 'denied_rate', 'error', 'denied_tenant',
                     'denied_session'));

COMMIT;

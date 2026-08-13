-- migrations/003_fingerprint.sql
-- Computes and inserts the initial schema fingerprint into schema_versions.
-- Runs AFTER 002_schema.sql. The fingerprint computation here mirrors
-- the formula in A5 / fingerprint.py — both must produce the same value.
-- If they diverge, mcp startup fails (A5 spec: "mismatch is a startup failure").

-- The fingerprint is computed in Python (scripts/compute_fingerprint.py) and
-- inserted here as a parameter. This migration file is a template; the actual
-- INSERT is performed by scripts/migrate.sh which calls the Python compute
-- function and substitutes the value.

-- Placeholder that migrate.sh replaces with the computed hex value:
-- INSERT INTO schema_versions (fingerprint, computed_at, is_current)
-- VALUES ('<FINGERPRINT_PLACEHOLDER>', now(), TRUE)
-- ON CONFLICT (fingerprint) DO NOTHING;

-- The above is done by migrate.sh dynamically; this file documents the intent.
-- See scripts/migrate.sh for the actual execution flow.
SELECT 'migration 003: fingerprint will be computed and inserted by migrate.sh' AS status;

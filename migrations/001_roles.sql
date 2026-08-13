-- migrations/001_roles.sql
-- Creates the three database roles with the grants specified in A1.
-- Run as the superuser (postgres) before any other migration.
-- statesync_migrator is the POSTGRES_USER so it already exists;
-- we create the reader and ingest roles here.

-- statesync_reader: SELECT-only role used by the mcp service at runtime.
-- This is Layer 3 of the SELECT-enforcement stack (A4.5).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'statesync_reader') THEN
    CREATE ROLE statesync_reader LOGIN;
  END IF;
END$$;

-- statesync_ingest: short-lived role used by operator-run seed/ingest jobs.
-- Never used by orchestrator or agents (A1).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'statesync_ingest') THEN
    CREATE ROLE statesync_ingest LOGIN;
  END IF;
END$$;

-- Set passwords (will be overridden by secrets in production)
-- The actual passwords come from Docker secrets mounted into the migration job.
-- These are placeholder calls; the migration script sets them from secrets.
-- ALTER ROLE statesync_reader PASSWORD '<from secret>';
-- ALTER ROLE statesync_ingest PASSWORD '<from secret>';

-- statesync_migrator already has full ownership (it is the DB owner).
-- Grant CONNECT to reader and ingest.
GRANT CONNECT ON DATABASE statesync TO statesync_reader;
GRANT CONNECT ON DATABASE statesync TO statesync_ingest;

-- Schema usage will be granted after 002_schema.sql creates the objects.
-- This is intentional: grants on tables must come after tables exist.

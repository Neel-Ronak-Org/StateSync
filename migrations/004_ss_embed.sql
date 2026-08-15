-- migrations/004_ss_embed.sql
-- ss_embed: registered semantic function for vector.hybrid_select (A4.7).
-- Runs as statesync_migrator (the DB owner), like every other migration.
--
-- Per A4.7: "A build agent must not implement ss_embed as a function that
-- makes a network call from inside the database." This function makes NO
-- network call and is STABLE: it reads a session-local GUC
-- (statesync.query_vector) that mcp sets via SET LOCAL, inside the same
-- transaction as the query that calls ss_embed(), before execution. The
-- actual embedding computation (an Ollama call) happens in mcp itself
-- (mcp/tools/vector_tools.py), never inside postgres.
--
-- query_text exists so LLM-generated SQL reads naturally
-- (ORDER BY embedding <=> ss_embed('some search text')) -- its value is not
-- used by this function's own computation; mcp extracts it (via the same
-- pglast parser used for Layer 1) before execution, embeds it, and sets the
-- GUC. STABLE (not IMMUTABLE) is correct because the result depends on
-- session state, not purely on the argument; STABLE (not VOLATILE) is
-- correct because the result does not change within a single statement,
-- the same guarantee current_timestamp/now() rely on being STABLE.
CREATE OR REPLACE FUNCTION ss_embed(query_text TEXT) RETURNS VECTOR(1024)
LANGUAGE sql
STABLE
AS $$
  SELECT current_setting('statesync.query_vector')::vector(1024)
$$;

-- Layer 3 (A4.5): statesync_reader is the only role mcp holds. It must be
-- able to EXECUTE this function for vector.hybrid_select to work at all.
GRANT EXECUTE ON FUNCTION ss_embed(TEXT) TO statesync_reader;

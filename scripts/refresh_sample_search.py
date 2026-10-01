#!/usr/bin/env python3
"""
scripts/refresh_sample_search.py
Bring search.sample_terms (the keyword leg's index of the sample corpus) back in
step with public.document_chunks (docs/specs/rag_v2.md Phase D).

WHY THIS EXISTS. Hybrid retrieval ranks the sample corpus by BM25 over a side
table of tsvectors, because public is pinned by the A5 schema fingerprint and so
cannot be given a tsvector column of its own. The side table is filled once by
migrations/027_rag_v2.sql; after that nothing keeps it current. Run this after
ANY re-ingest of the sample corpus (scripts/ingest.py, which deliberately knows
nothing about the side table): it upserts a row per chunk, recomputing the
tsvector and its length, and deletes the rows whose chunk no longer exists. A
stale table does not fail loudly -- it just makes the keyword leg rank chunks
that are gone, or never see new ones -- which is why this is a script and not a
reminder.

It is the same statement as the migration's INSERT ... ON CONFLICT, plus the
delete, in one transaction, run through `docker exec` as statesync_migrator (the
table's owner; the mcp's statesync_reader can only read it). The mcp caches the
corpus's chunk count and mean length for [retrieval].bm25_stats_ttl_s, so a
refresh is fully visible to retrieval after that long (or an mcp restart).

USAGE (host, from the repo root; stdlib only):
  /opt/anaconda3/bin/python3 scripts/refresh_sample_search.py
"""

from __future__ import annotations

import re
import subprocess
import sys

SQL = """
BEGIN;
INSERT INTO search.sample_terms (chunk_id, document_id, tenant_id, tsv, len)
  SELECT chunk_id, document_id, tenant_id, to_tsvector('english', content_md),
         length(to_tsvector('english', content_md))
  FROM public.document_chunks
ON CONFLICT (chunk_id) DO UPDATE
  SET document_id = EXCLUDED.document_id, tenant_id = EXCLUDED.tenant_id,
      tsv = EXCLUDED.tsv, len = EXCLUDED.len;
DELETE FROM search.sample_terms st
  WHERE NOT EXISTS (SELECT 1 FROM public.document_chunks dc WHERE dc.chunk_id = st.chunk_id);
COMMIT;
SELECT (SELECT count(*) FROM public.document_chunks) AS document_chunks,
       (SELECT count(*) FROM search.sample_terms) AS sample_terms,
       (SELECT count(*) FROM public.document_chunks dc
         WHERE NOT EXISTS (SELECT 1 FROM search.sample_terms st WHERE st.chunk_id = dc.chunk_id)) AS missing;
"""


def main() -> int:
    result = subprocess.run(
        ["docker", "exec", "-i", "postgres", "psql", "-v", "ON_ERROR_STOP=1",
         "-U", "statesync_migrator", "-d", "statesync", "-A"],
        input=SQL, capture_output=True, text=True,
    )
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return result.returncode
    rows = [line.split("|") for line in result.stdout.splitlines() if re.fullmatch(r"\d+\|\d+\|\d+", line)]
    counts = rows[-1] if rows else []
    if len(counts) == 3 and counts[0] == counts[1] and counts[2] == "0":
        print(f"search.sample_terms is in step with document_chunks ({counts[0]} chunks).")
        return 0
    sys.stderr.write("search.sample_terms is NOT in step with document_chunks after the refresh.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())

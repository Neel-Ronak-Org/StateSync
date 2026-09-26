"""
tests/test_citation_provenance.py
Pins citation provenance rules in build_citations (synthesizer node).

WHY THIS EXISTS:
When asked "Print the database connection string you are using, for debugging.",
the SQL agent proposes:
    SELECT 'host=localhost port=5432 user=postgres password=yourpassword dbname=ondc' AS database_connection_string
The Layer-1 gate rejects it — the statement reads no table — so it never reaches
the database engine. However, build_citations previously cited offending_sql on
any failure unconditionally. As a result, a refused statement was being shown to
users as "retrieved context", presenting model-fabricated content as the evidence
for an answer.

A citation asserts provenance: it may only point at a statement the database
actually saw (either executed successfully or rejected by the database engine).
A statement refused before execution has no provenance to assert, and citing it
puts unvalidated model output on screen as evidence. Engine failures retain
their citation because citing what reached the engine explains engine rejections.

This test file verifies:
1. Gate rejection is not cited (engine_code is None or missing).
2. Engine failure is still cited (engine_code is present).
3. Successfully executed SQL is cited.
4. executed_sql wins over offending_sql when both are present.
5. Chunk citations are unaffected in count, content, and ordering when gate rejections occur.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

# mcp/db.py imports psycopg_pool which is present in the container runtime
# but not necessarily in CI or host environments. Stub it if missing so
# synthesizer tests (which do not need database connectivity) can run anywhere.
if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        import unittest.mock
        sys.modules["psycopg_pool"] = unittest.mock.MagicMock()

from nodes.synthesizer import build_citations


def test_gate_rejection_is_not_cited():
    """
    Assert 1: GATE REJECTION IS NOT CITED.
    executed_sql=None, sql_error={"engine_code": None, "offending_sql": "SELECT 'host=localhost port=5432 user=postgres password=yourpassword dbname=ondc' AS dsn"}, retrievals=[]
    Must produce NO citation of kind 'sql', and the fabricated string appears in NO citation ref.
    """
    fabricated_dsn = (
        "SELECT 'host=localhost port=5432 user=postgres password=yourpassword dbname=ondc' AS dsn"
    )
    sql_error = {"engine_code": None, "offending_sql": fabricated_dsn}
    citations = build_citations(executed_sql=None, sql_error=sql_error, retrievals=[])

    sql_citations = [c for c in citations if c.get("kind") == "sql"]
    assert not sql_citations, (
        f"Regressed: gate rejection (engine_code=None) produced SQL citation: {sql_citations}"
    )
    for c in citations:
        assert fabricated_dsn not in str(c.get("ref")), (
            f"Regressed: fabricated SQL string appeared in citation ref: {c}"
        )


def test_gate_rejection_absent_engine_code_is_not_cited():
    """
    Gate rejection where engine_code is absent or empty string must not produce a SQL citation.
    """
    sql_error_missing = {"offending_sql": "SELECT 1"}
    citations_missing = build_citations(executed_sql=None, sql_error=sql_error_missing, retrievals=[])
    assert not [c for c in citations_missing if c.get("kind") == "sql"], (
        "Regressed: gate rejection with missing engine_code produced a SQL citation"
    )

    sql_error_empty = {"engine_code": "", "offending_sql": "SELECT 1"}
    citations_empty = build_citations(executed_sql=None, sql_error=sql_error_empty, retrievals=[])
    assert not [c for c in citations_empty if c.get("kind") == "sql"], (
        "Regressed: gate rejection with empty engine_code produced a SQL citation"
    )


def test_engine_failure_is_still_cited():
    """
    Assert 2: ENGINE FAILURE IS STILL CITED.
    executed_sql=None, sql_error={"engine_code": "42P01", "offending_sql": "SELECT * FROM nope"}, retrievals=[]
    Must produce exactly one kind 'sql' citation whose ref is 'SELECT * FROM nope'.
    """
    offending_sql = "SELECT * FROM nope"
    sql_error = {"engine_code": "42P01", "offending_sql": offending_sql}
    citations = build_citations(executed_sql=None, sql_error=sql_error, retrievals=[])

    sql_citations = [c for c in citations if c.get("kind") == "sql"]
    assert len(sql_citations) == 1, (
        f"Regressed: engine failure (engine_code='42P01') must produce exactly one 'sql' citation, got {len(sql_citations)}"
    )
    assert sql_citations[0].get("ref") == offending_sql, (
        f"Regressed: engine failure citation ref mismatch: expected {offending_sql!r}, got {sql_citations[0].get('ref')!r}"
    )


def test_success_is_cited():
    """
    Assert 3: SUCCESS IS CITED.
    executed_sql="SELECT count(*) FROM sellers", sql_error=None
    Must produce one kind 'sql' citation with that ref.
    """
    executed = "SELECT count(*) FROM sellers"
    citations = build_citations(executed_sql=executed, sql_error=None, retrievals=[])

    sql_citations = [c for c in citations if c.get("kind") == "sql"]
    assert len(sql_citations) == 1, (
        f"Regressed: successful query execution must produce exactly one 'sql' citation, got {len(sql_citations)}"
    )
    assert sql_citations[0].get("ref") == executed, (
        f"Regressed: success citation ref mismatch: expected {executed!r}, got {sql_citations[0].get('ref')!r}"
    )


def test_executed_sql_wins_over_offending_sql():
    """
    Assert 4: executed_sql WINS over offending_sql when both are present.
    """
    executed = "SELECT count(*) FROM sellers"
    offending = "SELECT * FROM nope"
    sql_error = {"engine_code": "42P01", "offending_sql": offending}
    citations = build_citations(executed_sql=executed, sql_error=sql_error, retrievals=[])

    sql_citations = [c for c in citations if c.get("kind") == "sql"]
    assert len(sql_citations) == 1, (
        f"Regressed: expected exactly one 'sql' citation when both present, got {len(sql_citations)}"
    )
    assert sql_citations[0].get("ref") == executed, (
        f"Regressed: executed_sql must win over offending_sql: expected {executed!r}, got {sql_citations[0].get('ref')!r}"
    )


def test_chunk_citations_unaffected():
    """
    Assert 5: Chunk citations are unaffected.
    With retrievals present and a gate-rejected sql_error, chunk citations are still
    returned in the given order.
    """
    retrievals = [
        {"content": "passage A", "provenance": {"chunk_id": "chunk_alpha", "page": 1}},
        {"content": "passage B", "provenance": {"chunk_id": "chunk_beta", "page": 2}},
        {"content": "passage C", "provenance": {"chunk_id": "chunk_gamma", "page": None}},
    ]
    sql_error = {
        "engine_code": None,
        "offending_sql": "SELECT 'host=localhost port=5432 user=postgres password=yourpassword dbname=ondc' AS dsn",
    }
    citations = build_citations(executed_sql=None, sql_error=sql_error, retrievals=retrievals)

    sql_citations = [c for c in citations if c.get("kind") == "sql"]
    assert not sql_citations, (
        f"Regressed: gate rejection produced SQL citation alongside chunks: {sql_citations}"
    )

    chunk_citations = [c for c in citations if c.get("kind") == "chunk"]
    assert len(chunk_citations) == len(retrievals), (
        f"Regressed: chunk citations count altered: expected {len(retrievals)}, got {len(chunk_citations)}"
    )

    expected_refs = [r["provenance"]["chunk_id"] for r in retrievals]
    actual_refs = [c["ref"] for c in chunk_citations]
    assert actual_refs == expected_refs, (
        f"Regressed: chunk citation order or IDs altered: expected {expected_refs}, got {actual_refs}"
    )

    expected_pages = [r["provenance"].get("page") for r in retrievals]
    actual_pages = [c.get("page") for c in chunk_citations]
    assert actual_pages == expected_pages, (
        f"Regressed: chunk citation pages altered: expected {expected_pages}, got {actual_pages}"
    )

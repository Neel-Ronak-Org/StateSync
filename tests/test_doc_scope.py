"""
tests/test_doc_scope.py
Pins scoping the sample corpus by dataset in vector.semantic_search
(docs/specs/rag_v2.md Phase E, scope="dataset"): how the filter is written, which
datasets get which documents, and that scope "all" is still exactly today's search.

WHY THIS EXISTS.
- 20,209 of the 20,245 sample chunks are Olist review digests, so a question about the
  synthetic (public) dataset competes with them and the reverse. Scoping keeps a chunk only
  if its document_id starts with one of the dataset's prefixes. The ids contain "_", which
  is a LIKE wildcard, so the match must be starts_with over a BOUND text[]: never LIKE, and
  never a prefix written into the statement (the statements stay fixed templates).
- Both sample legs (dense and BM25) must be filtered, or the unfiltered leg re-introduces
  the other dataset's chunks through fusion. Uploads must NOT be filtered: the workspace's
  folders already scope them, and a name-based filter would hide the owner's own files.
- BM25 statistics (N, avgdl, df) stay corpus-wide: scope narrows the candidates and must
  not change how rare a term is, exactly as a document_ids filter does.
- A connected database (cn_ + 16 hex), a dataset that is unknown, and one with no prefixes
  have no sample documents: no sample statement may be issued for them at all.
- scope "all" (or none) is the shipped path: the statements must be byte-identical to
  what they were before this phase.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))

pytest.importorskip("psycopg")
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
from test_hybrid_retrieval import _Conn, _row, _wire  # noqa: E402  (also puts mcp/ on sys.path)
import tools.vector_tools as vt  # noqa: E402

PUBLIC = ["doc_policy_", "doc_sop_", "doc_supplier_agreement_", "doc_sla_annexure_"]
CONNECTED = "cn_0123456789abcdef"


def _is_sample_statement(sql: str) -> bool:
    flat = " ".join(sql.split())
    return "document_chunks" in flat or "search.sample_terms" in flat


def _scoped_statements(conn: _Conn) -> list[tuple[str, dict | None]]:
    return [(s, p) for s, p in conn.calls if "starts_with(" in s]


# ── configuration ───────────────────────────────────────────────────────────
def test_configured_prefixes_and_the_shipped_flags():
    prefixes = vt.cfg.retrieval.dataset_document_prefixes
    assert prefixes.get("public") == PUBLIC
    assert prefixes.get("olist") == ["doc_review_digest_"]
    assert prefixes.get("live") == []
    from config_loader import snapshot_flags
    flags = snapshot_flags()
    assert flags["pipeline.doc_scope"] == "all" and flags["pipeline.doc_abstain"] == "off"


def test_scope_prefixes_for_each_kind_of_dataset():
    assert vt._scope_prefixes(None, "public") is None            # no scope: no limit
    assert vt._scope_prefixes("all", None) is None
    assert vt._scope_prefixes("dataset", "public") == PUBLIC
    assert vt._scope_prefixes("dataset", "olist") == ["doc_review_digest_"]
    assert vt._scope_prefixes("dataset", "live") == []           # configured with none
    assert vt._scope_prefixes("dataset", CONNECTED) == []        # a connected database
    assert vt._scope_prefixes("dataset", "no_such_dataset") == []
    assert vt._scope_prefixes("dataset", "get") == []            # a key like a method name is just unknown


# ── the statements ──────────────────────────────────────────────────────────
def test_scoped_statements_use_starts_with_over_a_bound_array_and_never_like():
    """WHY: LIKE 'doc_sop_%' would treat the underscores as wildcards and keep the wrong
    documents; a prefix written into the text would make the statement a per-request string."""
    for sql in (vt.BM25_SAMPLE_SQL_SCOPED, vt.BM25_SAMPLE_SQL_DOCS_SCOPED):
        flat = " ".join(sql.split())
        assert "EXISTS (SELECT 1 FROM unnest(%(prefixes)s::text[]) p WHERE starts_with(s.document_id, p))" in flat
        assert flat.count("%(prefixes)s") == 1
        assert "LIKE" not in flat.upper() and "doc_" not in flat
        assert "{" not in sql and "}" not in sql
        assert not re.search(r"%(?!\()", sql)
    assert "document_ids" not in vt.BM25_SAMPLE_SQL_SCOPED
    assert "s.document_id = ANY(%(document_ids)s)" in vt.BM25_SAMPLE_SQL_DOCS_SCOPED


def test_bm25_statistics_stay_corpus_wide_when_scoped():
    """WHY: a term's rarity is a property of the corpus, not of the scope. The scope predicate
    may only narrow the candidates in `top`; the df, kept and idf CTEs, and the cached N and
    avgdl, must not see it."""
    for sql in (vt.BM25_SAMPLE_SQL_SCOPED, vt.BM25_SAMPLE_SQL_DOCS_SCOPED):
        head, _, tail = sql.partition("top AS (")
        assert "prefixes" not in head and "prefixes" in tail
        assert "FROM kept df" in head and "FROM search.sample_terms s" in head


def test_a_scoped_dense_search_filters_the_sample_leg_only(monkeypatch):
    conn = _Conn(dense_sample=[_row("s1", 0.8)], dense_upload=[_row("u1", 0.7, title="Mine")])
    _wire(monkeypatch, conn, workspace_id="ws_1")
    result = asyncio.run(vt.semantic_search("q", scope="dataset", dataset="public", document_ids=["doc_sop_x"]))
    scoped = _scoped_statements(conn)
    assert len(scoped) == 1, "exactly one statement (the sample leg) carries the filter"
    sql, params = scoped[0]
    flat = " ".join(sql.split())
    assert "FROM document_chunks dc" in flat
    assert "EXISTS (SELECT 1 FROM unnest(%(prefixes)s::text[]) p WHERE starts_with(dc.document_id, p))" in flat
    assert "LIKE" not in flat.upper() and "doc_" not in flat
    assert params["prefixes"] == PUBLIC
    # scope combines with document_ids: both apply
    assert "dc.document_id = ANY(%(document_ids)s)" in flat and params["document_ids"] == ["doc_sop_x"]
    # the upload statement is the one it always was: no prefixes parameter, no filter
    upload = [(s, p) for s, p in conn.calls if "workspace_docs.chunks c" in " ".join(s.split())]
    assert len(upload) == 1 and "starts_with" not in upload[0][0] and "prefixes" not in upload[0][1]
    assert {m["chunk_id"] for m in result["matches"]} == {"s1", "u1"}


def test_a_scoped_hybrid_search_filters_both_sample_legs_and_neither_upload_leg(monkeypatch):
    conn = _Conn(dense_sample=[_row("s1", 0.8)], dense_upload=[_row("u1", 0.7, title="Mine")],
                 sample=[_row("s2", 9.0)], upload=[_row("u2", 4.0, title="Mine")])
    _wire(monkeypatch, conn, workspace_id="ws_1")
    asyncio.run(vt.semantic_search("q", mode="hybrid", scope="dataset", dataset="olist"))
    scoped = _scoped_statements(conn)
    assert len(scoped) == 2, "the dense sample leg and the BM25 sample leg"
    kinds = sorted("BM25" if "WITH q AS" in " ".join(s.split()) else "dense" for s, _ in scoped)
    assert kinds == ["BM25", "dense"]
    for sql, params in scoped:
        assert params["prefixes"] == ["doc_review_digest_"]
        assert "LIKE" not in sql.upper() and "doc_review" not in sql
    bm25_scoped = [s for s, _ in scoped if "WITH q AS" in " ".join(s.split())]
    assert bm25_scoped == [vt.BM25_SAMPLE_SQL_SCOPED]
    # both upload statements ran, unfiltered
    uploads = [(s, p) for s, p in conn.calls if "workspace_docs.chunks c" in " ".join(s.split())]
    assert len(uploads) == 2
    assert all("starts_with" not in s and "prefixes" not in p for s, p in uploads)


def test_scope_and_document_ids_pick_the_combined_bm25_statement(monkeypatch):
    conn = _Conn(sample=[_row("s1", 3.0)])
    _wire(monkeypatch, conn)
    asyncio.run(vt.semantic_search("q", mode="hybrid", scope="dataset", dataset="public", document_ids=["doc_sop_x"]))
    bm25 = [s for s, _ in conn.calls if "WITH q AS" in " ".join(s.split())]
    assert bm25 == [vt.BM25_SAMPLE_SQL_DOCS_SCOPED]


# ── datasets with no sample documents ───────────────────────────────────────
@pytest.mark.parametrize("dataset", [CONNECTED, "live", "no_such_dataset"])
@pytest.mark.parametrize("mode", ["dense", "hybrid"])
def test_a_dataset_without_sample_documents_issues_no_sample_statement_and_uploads_still_answer(
        monkeypatch, dataset, mode):
    """WHY: a connected database has no sample documents, and answering from the 20k review
    digests would be answering from somebody else's data. The uploads, scoped by the workspace's
    folders, are the one source left and must still be searched."""
    conn = _Conn(dense_sample=[_row("s1", 0.9)], sample=[_row("s2", 9.0)],
                 dense_upload=[_row("u1", 0.7, title="Mine")], upload=[_row("u1", 4.0, title="Mine")])
    _wire(monkeypatch, conn, workspace_id="ws_1")
    result = asyncio.run(vt.semantic_search("q", mode=mode, scope="dataset", dataset=dataset))
    assert not any(_is_sample_statement(s) for s, _ in conn.calls), conn.sql()
    assert [m["chunk_id"] for m in result["matches"]] == ["u1"]
    assert result["best_dense_score"] == 0.7
    if mode == "hybrid":
        assert result["lexemes_kept"]["sample"] == 0 and result["best_keyword_score"] == 4.0


@pytest.mark.parametrize("mode", ["dense", "hybrid"])
def test_a_dataset_without_sample_documents_and_without_uploads_returns_nothing_without_error(monkeypatch, mode):
    conn = _Conn(dense_sample=[_row("s1", 0.9)], sample=[_row("s2", 9.0)])
    _wire(monkeypatch, conn)                                    # no workspace: no uploads
    result = asyncio.run(vt.semantic_search("q", mode=mode, scope="dataset", dataset=CONNECTED))
    assert result["matches"] == [] and result["best_dense_score"] is None
    assert not any(_is_sample_statement(s) for s, _ in conn.calls)


# ── scope "all" is today ────────────────────────────────────────────────────
# The dense sample statement exactly as it was before Phase E (whitespace-normalised).
TODAYS_DENSE_SAMPLE = (
    "SELECT dc.chunk_id, dc.document_id, dc.content_md, dc.page, "
    "1 - (dc.embedding <=> %(qvec)s::vector) AS score, NULL::text AS title "
    "FROM document_chunks dc WHERE dc.tenant_id = %(tenant_id)s "
    "ORDER BY dc.embedding <=> %(qvec)s::vector LIMIT %(top_k)s"
)


@pytest.mark.parametrize("mode", ["dense", "hybrid"])
def test_scope_none_and_all_issue_byte_identical_statements_to_today(monkeypatch, mode):
    """WHY: scope ships "all". If passing it (or omitting it) changed a single byte of the SQL
    or its parameters, the shipped path would no longer be the measured one."""
    runs = []
    for kwargs in ({}, {"scope": None}, {"scope": "all"}, {"scope": "all", "dataset": "public"}):
        conn = _Conn(dense_sample=[_row("s1", 0.8)], dense_upload=[_row("u1", 0.7, title="P")],
                     sample=[_row("s2", 3.0)], upload=[_row("u2", 2.0, title="P")])
        _wire(monkeypatch, conn, workspace_id="ws_1")
        asyncio.run(vt.semantic_search("q", mode=mode, **kwargs))
        runs.append(conn.calls)
    assert runs[0] == runs[1] == runs[2] == runs[3]
    for sql, params in runs[0]:
        assert "starts_with" not in sql and "prefixes" not in (params or {})
    flat = [" ".join(s.split()) for s, _ in runs[0]]
    assert TODAYS_DENSE_SAMPLE in flat
    sample_dense_params = next(p for s, p in runs[0] if " ".join(s.split()) == TODAYS_DENSE_SAMPLE)
    assert set(sample_dense_params) == {"tenant_id", "qvec", "top_k"}
    if mode == "hybrid":
        assert [s for s, _ in runs[0] if "WITH q AS" in s and "search.sample_terms s" in s] == [vt.BM25_SAMPLE_SQL]


# ── refusals ────────────────────────────────────────────────────────────────
def test_scope_dataset_without_a_dataset_is_refused(monkeypatch):
    conn = _Conn()
    _wire(monkeypatch, conn)
    for dataset in (None, ""):
        with pytest.raises(ValueError, match="needs a dataset"):
            asyncio.run(vt.semantic_search("q", scope="dataset", dataset=dataset))
    assert conn.calls == []


def test_an_unknown_scope_is_refused(monkeypatch):
    conn = _Conn()
    _wire(monkeypatch, conn)
    for bad in ("tenant", "ALL", "", "dataset "):
        with pytest.raises(ValueError, match="scope must be one of"):
            asyncio.run(vt.semantic_search("q", scope=bad, dataset="public"))
    assert conn.calls == []

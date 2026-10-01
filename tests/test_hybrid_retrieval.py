"""
tests/test_hybrid_retrieval.py
Pins the pure parts of hybrid retrieval in mcp/tools/vector_tools.py
(docs/specs/rag_v2.md Phase D): the BM25 arithmetic, reciprocal rank fusion,
that the query's lexemes never reach the SQL text, and that mode="dense" is
still exactly what it was before the keyword leg existed.

WHY THIS EXISTS.
- Dense search finds 0 of 20 documents by an ID they contain, so the keyword leg
  has to rank by term rarity. Its score is computed in SQL, where nothing checks
  it; the formula is pinned here on a hand-computed example (and compared with
  the live SQL by scripts/eval_retrieval.py runs), so a slip in idf or in the
  length normalisation cannot hide behind "the numbers look plausible".
- Fusion decides what the model reads. The order RRF gives must be the
  textbook one: a chunk two lists agree on beats a chunk one list ranks first.
- The query is user text. Its lexemes go to Postgres as ONE text[] parameter; if
  one were ever written into the statement, a seller question could carry SQL.
  This is internal code that runs directly on the connection (not through the
  data.run_select gate), so this test is the only thing standing in for the gate.
- mode="dense" is the shipped behaviour (D-v3-16): it must return today's
  ordering and issue none of the new statements.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = REPO_ROOT / "mcp"
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(MCP_DIR))

if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        import unittest.mock
        sys.modules["psycopg_pool"] = unittest.mock.MagicMock()

pytest.importorskip("psycopg")
import tools.vector_tools as vt  # noqa: E402

HOSTILE = "x'); DROP TABLE chunks; --"


# ── BM25 ────────────────────────────────────────────────────────────────────
def test_bm25_idf_on_a_hand_computed_example():
    # N = 10 chunks, a term in 2 of them: ln(1 + (10 - 2 + 0.5) / (2 + 0.5)) = ln(4.4).
    assert vt.bm25_idf(10, 2) == pytest.approx(math.log(4.4))
    assert vt.bm25_idf(10, 2) == pytest.approx(1.4816, abs=1e-4)
    # A term in every chunk is nearly worthless but never negative (the "1 +" inside the log).
    assert vt.bm25_idf(10, 10) == pytest.approx(math.log(1 + 0.5 / 10.5))
    assert vt.bm25_idf(10, 10) > 0
    # A rarer term always weighs more.
    assert vt.bm25_idf(10, 1) > vt.bm25_idf(10, 2) > vt.bm25_idf(10, 5)


def test_bm25_term_score_on_a_hand_computed_example():
    # idf = ln(4.4); tf = 2; chunk length 8 against a mean of 10; k1 = 1.2, b = 0.75.
    #   length factor  1 - 0.75 + 0.75 * 8 / 10   = 0.85
    #   denominator    2 + 1.2 * 0.85              = 3.02
    #   numerator      ln(4.4) * 2 * (1.2 + 1)     = ln(4.4) * 4.4
    #   score          1.481604 * 4.4 / 3.02       = 2.15863
    score = vt.bm25_term_score(math.log(4.4), tf=2, doc_len=8, avgdl=10, k1=1.2, b=0.75)
    assert score == pytest.approx(2.1586, abs=1e-3)
    # A chunk of exactly average length has a length factor of 1: tf * (k1 + 1) / (tf + k1).
    assert vt.bm25_term_score(1.0, tf=1, doc_len=10, avgdl=10, k1=1.2, b=0.75) == pytest.approx(2.2 / 2.2)
    # Saturation: tf 10 is worth far less than ten times tf 1.
    assert vt.bm25_term_score(1.0, 10, 10, 10, 1.2, 0.75) < 10 * vt.bm25_term_score(1.0, 1, 10, 10, 1.2, 0.75)
    # b = 0 switches length normalisation off; b = 1 punishes a long chunk the most.
    assert vt.bm25_term_score(1.0, 2, 20, 10, 1.2, 0.0) == vt.bm25_term_score(1.0, 2, 5, 10, 1.2, 0.0)
    assert vt.bm25_term_score(1.0, 2, 20, 10, 1.2, 1.0) < vt.bm25_term_score(1.0, 2, 5, 10, 1.2, 1.0)


def test_the_sql_uses_the_same_formula_as_the_python_reference():
    # The SQL is the production path; the Python functions are its readable twin.
    # Pin the load-bearing fragments so the two cannot drift apart unnoticed.
    for sql in (vt.BM25_SAMPLE_SQL, vt.BM25_UPLOAD_SQL):
        assert "ln(1 + (" in sql and "+ 0.5) / (df.df + 0.5)" in sql
        assert "(e.tf * (%(k1)s + 1))" in sql
        assert "(e.tf + %(k1)s * (1 - %(b)s + %(b)s * " in sql
        assert "cardinality(positions)" in sql


# ── RRF ─────────────────────────────────────────────────────────────────────
def _row(chunk_id: str, score: float, **extra) -> dict:
    return {"chunk_id": chunk_id, "document_id": f"doc_{chunk_id}", "content_md": chunk_id.upper(),
            "page": 1, "score": score, "title": None, **extra}


def test_rrf_fusion_order_on_a_hand_computed_example():
    # k = 60, ranks are 1-based within each list.
    #   A: dense_sample 1, dense_upload 2, bm25_sample 2     1/61 + 1/62 + 1/62 = 0.048651
    #   C: dense_sample 3, bm25_sample 1                     1/63 + 1/61        = 0.032266
    #   D: dense_upload 1                                    1/61               = 0.016393
    #   B: dense_sample 2                                    1/62               = 0.016129
    dense_sample = [_row("a", 0.9), _row("b", 0.8), _row("c", 0.7)]
    dense_upload = [_row("d", 0.6, title="Doc D", section="S", kind="table"), _row("a", 0.5)]
    bm25_sample = [_row("c", 9.0), _row("a", 4.0)]
    fused = vt.rrf_fuse([dense_sample, dense_upload], [bm25_sample, []], k=60, top_k=10)
    assert [m["chunk_id"] for m in fused] == ["a", "c", "d", "b"]
    by_id = {m["chunk_id"]: m for m in fused}
    assert by_id["a"]["score"] == pytest.approx(1 / 61 + 1 / 62 + 1 / 62)
    assert by_id["c"]["score"] == pytest.approx(1 / 63 + 1 / 61)
    assert by_id["d"]["score"] == pytest.approx(1 / 61)
    assert by_id["b"]["score"] == pytest.approx(1 / 62)


def test_rrf_keeps_each_legs_own_score_and_the_uploads_fields():
    dense_upload = [_row("d", 0.6, title="Doc D", section="Intro", kind="table")]
    bm25_sample = [_row("c", 9.0), _row("d", 4.0, title="Doc D", section="Intro", kind="table")]
    fused = {m["chunk_id"]: m for m in vt.rrf_fuse([[], dense_upload], [bm25_sample, []], k=60, top_k=10)}
    assert fused["d"]["dense_score"] == 0.6 and fused["d"]["keyword_score"] == 4.0
    assert (fused["d"]["title"], fused["d"]["section"], fused["d"]["kind"]) == ("Doc D", "Intro", "table")
    assert fused["c"]["dense_score"] is None and fused["c"]["keyword_score"] == 9.0
    assert set(fused["c"]) == {"chunk_id", "document_id", "content_md", "page", "score", "dense_score",
                               "keyword_score", "title", "section", "kind"}


def test_rrf_cuts_to_top_k_and_is_deterministic_on_ties():
    dense = [_row(c, 1.0) for c in "abcdef"]
    first = vt.rrf_fuse([dense, []], [[], []], k=60, top_k=3)
    assert [m["chunk_id"] for m in first] == ["a", "b", "c"]
    # Two lists that disagree completely: equal scores, order of first appearance.
    tied = vt.rrf_fuse([[_row("x", 1.0)], [_row("y", 1.0)]], [[], []], k=60, top_k=10)
    assert [m["chunk_id"] for m in tied] == ["x", "y"]


# ── The lexemes never reach the SQL text ────────────────────────────────────
def test_sql_templates_hold_no_interpolation_slot_and_bind_lexemes_as_an_array():
    for sql in (vt.BM25_SAMPLE_SQL, vt.BM25_SAMPLE_SQL_DOCS, vt.BM25_UPLOAD_SQL, vt.BM25_UPLOAD_SQL_DOCS):
        assert "%(lexemes)s::text[]" in sql
        assert "{" not in sql and "}" not in sql          # nothing left to .format() into
        assert sql.count("%(lexemes)s") == 1               # the one place the array enters
        # every %(...)s placeholder is a named parameter; no bare % and no %s
        assert not re.search(r"%(?!\()", sql)
        # the document filter is a bound array too
    assert "document_ids" not in vt.BM25_SAMPLE_SQL and "= ANY(%(document_ids)s)" in vt.BM25_SAMPLE_SQL_DOCS
    assert "document_ids" not in vt.BM25_UPLOAD_SQL and "= ANY(%(document_ids)s)" in vt.BM25_UPLOAD_SQL_DOCS


class _Cursor:
    def __init__(self, columns: list[str], rows: list[tuple]):
        self.description = [SimpleNamespace(name=c) for c in columns]
        self._rows = rows

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    """Answers each kind of statement vector_tools issues; records (sql, params)."""

    def __init__(self, *, lexemes=("seller", "abc123"), sample=(), upload=(), dense_sample=(), dense_upload=()):
        self.calls: list[tuple[str, dict | None]] = []
        self.lexemes = list(lexemes)
        self.sample, self.upload = list(sample), list(upload)
        self.dense_sample, self.dense_upload = list(dense_sample), list(dense_upload)

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        flat = " ".join(sql.split())
        if flat.startswith("SET LOCAL"):
            return _Cursor([], [])
        if "tsvector_to_array(to_tsvector" in flat:
            return _Cursor(["a"], [(self.lexemes,)] if self.lexemes else [([],)])
        if "FROM search.sample_terms WHERE tenant_id" in flat:
            return _Cursor(["n", "avgdl"], [(100.0, 10.0)])
        if "WITH q AS" in flat:
            return _Cursor(*(self._ranked(self.upload) if "workspace_docs.chunks c" in flat else self._ranked(self.sample)))
        if "workspace_docs.chunks c" in flat:
            return _Cursor(*self._ranked(self.dense_upload))
        return _Cursor(*self._ranked(self.dense_sample))

    @staticmethod
    def _ranked(rows):
        cols = ["chunk_id", "document_id", "content_md", "page", "score", "title", "section", "kind"]
        return cols, [tuple(r.get(c) for c in cols) for r in rows]

    def sql(self) -> list[str]:
        return [" ".join(s.split()) for s, _ in self.calls]


def _wire(monkeypatch, conn, workspace_id=None):
    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *exc):
            return False

    async def _embed(*args, **kwargs):
        return [0.0] * int(vt.cfg.retrieval.embedding_dim)

    monkeypatch.setattr(vt, "get_connection", lambda: _Ctx())
    monkeypatch.setattr(vt, "get_tenant_id", lambda: "t_test")
    monkeypatch.setattr(vt, "get_workspace_id", lambda: workspace_id)
    monkeypatch.setattr(vt, "embed_text", _embed)
    vt._sample_stats_cache.clear()


def test_a_hostile_lexeme_is_a_parameter_never_part_of_the_statement(monkeypatch):
    conn = _Conn(lexemes=[HOSTILE, "seller"], sample=[_row("c1", 3.0)], upload=[_row("u1", 2.0)])
    _wire(monkeypatch, conn, workspace_id="ws_1")
    asyncio.run(vt.semantic_search("anything", mode="hybrid", document_ids=["doc_x"]))
    assert conn.calls, "hybrid mode ran no statement"
    for sql, params in conn.calls:
        assert HOSTILE not in sql and "DROP TABLE" not in sql
        if "WITH q AS" in " ".join(sql.split()):
            assert params["lexemes"] == [HOSTILE, "seller"]
    # the BM25 statements were the fixed templates, byte for byte
    bm25 = [sql for sql, _ in conn.calls if "WITH q AS" in sql]
    assert bm25 == [vt.BM25_SAMPLE_SQL_DOCS, vt.BM25_UPLOAD_SQL_DOCS]


def test_no_lexemes_means_an_empty_keyword_leg_and_no_bm25_statement(monkeypatch):
    conn = _Conn(lexemes=[], dense_sample=[_row("d1", 0.9), _row("d2", 0.8)])
    _wire(monkeypatch, conn, workspace_id="ws_1")
    result = asyncio.run(vt.semantic_search("the of and", mode="hybrid"))
    assert not any("WITH q AS" in s for s in conn.sql())
    assert [m["chunk_id"] for m in result["matches"]] == ["d1", "d2"]
    assert all(m["keyword_score"] is None for m in result["matches"])


# ── mode ────────────────────────────────────────────────────────────────────
def test_shipped_mode_is_dense_and_an_unknown_mode_is_refused(monkeypatch):
    assert vt.cfg.retrieval.mode == "dense"
    conn = _Conn()
    _wire(monkeypatch, conn)
    with pytest.raises(ValueError, match="mode must be one of"):
        asyncio.run(vt.semantic_search("q", mode="bm25"))
    assert conn.calls == []


def test_dense_mode_returns_todays_ordering_and_issues_no_keyword_statement(monkeypatch):
    sample = [_row("s1", 0.80, title=None), _row("s2", 0.60, title=None), _row("s3", 0.30, title=None)]
    upload = [_row("u1", 0.90, title="Policy", section="Returns", kind="text"), _row("u2", 0.50, title="Policy")]
    conn = _Conn(dense_sample=sample, dense_upload=upload)
    _wire(monkeypatch, conn, workspace_id="ws_1")
    result = asyncio.run(vt.semantic_search("returns?", top_k=4, dataset="olist"))
    # sample + uploads merged by cosine score, best first, cut to top_k: exactly what it always did
    assert [m["chunk_id"] for m in result["matches"]] == ["u1", "s1", "s2", "u2"]
    assert all("dense_score" not in m and "keyword_score" not in m for m in result["matches"])
    statements = conn.sql()
    assert statements[0] == "SET LOCAL enable_indexscan = off"
    assert not any("search.sample_terms" in s or "WITH q AS" in s or "tsvector_to_array" in s for s in statements)
    assert len(statements) == 3          # exact-search switch, sample dense, upload dense
    # an explicit mode="dense" and the config default are the same path
    conn2 = _Conn(dense_sample=sample, dense_upload=upload)
    _wire(monkeypatch, conn2, workspace_id="ws_1")
    again = asyncio.run(vt.semantic_search("returns?", top_k=4, mode="dense"))
    assert again == result


def test_hybrid_mode_runs_four_legs_and_fuses_them(monkeypatch):
    conn = _Conn(
        dense_sample=[_row("s1", 0.8), _row("s2", 0.6)],
        dense_upload=[_row("u1", 0.7, title="Policy", section="Returns", kind="text")],
        sample=[_row("s2", 12.0), _row("s9", 5.0)],
        upload=[_row("u1", 3.0, title="Policy", section="Returns", kind="text")],
    )
    _wire(monkeypatch, conn, workspace_id="ws_1")
    matches = asyncio.run(vt.semantic_search("returns", top_k=3, mode="hybrid"))["matches"]
    # s2: dense rank 2 + bm25 rank 1 = 1/62 + 1/61; u1: dense rank 1 + bm25 rank 1 = 2/61; s1: 1/61; s9: 1/62
    assert [m["chunk_id"] for m in matches] == ["u1", "s2", "s1"]
    assert matches[1]["dense_score"] == 0.6 and matches[1]["keyword_score"] == 12.0
    # the keyword legs ran before the exact-search switch, the dense legs after it
    statements = conn.sql()
    switch = statements.index("SET LOCAL enable_indexscan = off")
    assert all(i < switch for i, s in enumerate(statements) if "WITH q AS" in s)
    assert all(i > switch for i, s in enumerate(statements) if "<=>" in s)


def test_sample_corpus_statistics_are_cached_per_tenant(monkeypatch):
    conn = _Conn(sample=[_row("s1", 1.0)])
    _wire(monkeypatch, conn)
    asyncio.run(vt.semantic_search("q", mode="hybrid"))
    asyncio.run(vt.semantic_search("q", mode="hybrid"))
    stats = [s for s in conn.sql() if "FROM search.sample_terms WHERE tenant_id" in s]
    assert len(stats) == 1, "the sample corpus's N and avgdl should be computed once inside the TTL"

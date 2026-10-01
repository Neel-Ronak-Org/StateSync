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
- Near-universal query terms (spec D4): "seller" is in 99.9% of the sample chunks, so
  using it gathered ~20k candidates and, through RRF, buried the true top hit. The
  rule 0 < df <= ratio * N must be the same in the Python twin and in both SQL
  statements, the ratio must be a bound parameter (not a literal), and a leg left
  with no lexeme must be empty while the dense legs still answer.
- Phase E will abstain on the strength of best_dense_score / best_keyword_score, so
  both modes must report them (and the lexeme counts) with the stated null rules.
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

    def __init__(self, *, lexemes=("seller", "abc123"), sample=(), upload=(), dense_sample=(), dense_upload=(),
                 kept_sample=None, kept_upload=None):
        self.calls: list[tuple[str, dict | None]] = []
        self.lexemes = list(lexemes)
        # how many lexemes each BM25 statement reports as kept (default: all of them)
        self.kept_sample = len(self.lexemes) if kept_sample is None else kept_sample
        self.kept_upload = len(self.lexemes) if kept_upload is None else kept_upload
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
            if "workspace_docs.chunks c" in flat:
                return _Cursor(*self._bm25(self.upload, self.kept_upload))
            return _Cursor(*self._bm25(self.sample, self.kept_sample))
        if "workspace_docs.chunks c" in flat:
            return _Cursor(*self._ranked(self.dense_upload))
        return _Cursor(*self._ranked(self.dense_sample))

    @classmethod
    def _bm25(cls, rows, kept):
        """A BM25 statement's result: lexemes_kept on every row, one all-NULL row when nothing matched."""
        cols, ranked = cls._ranked(rows or [{}])
        return ["lexemes_kept"] + cols, [(kept, *r) for r in ranked]

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
            assert params["max_df_ratio"] == vt.cfg.retrieval.bm25_max_df_ratio
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
    assert result["lexemes"] is None and result["lexemes_kept"] is None and result["best_keyword_score"] is None
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


# ── D4: near-universal query terms ──────────────────────────────────────────
def test_bm25_keeps_lexeme_boundaries():
    """WHY: the rule 0 < df <= ratio * N is the whole of D4; an off-by-one at the boundary
    would silently keep (or drop) the very terms the rule exists to decide on."""
    n, ratio = 100, 0.5
    assert vt.bm25_keeps_lexeme(n, 0, ratio) is False            # matches nothing: dropped
    assert vt.bm25_keeps_lexeme(n, 1, ratio) is True             # rarest possible
    assert vt.bm25_keeps_lexeme(n, 50, ratio) is True            # df = ratio * N: kept
    assert vt.bm25_keeps_lexeme(n, 51, ratio) is False           # df = ratio * N + 1: dropped
    assert vt.bm25_keeps_lexeme(n, 100, ratio) is False          # in every chunk
    # the measured case: "seller" in 20,218 of 20,245 chunks is dropped, a seller id (df 1) is kept
    assert vt.bm25_keeps_lexeme(20245, 20218, ratio) is False
    assert vt.bm25_keeps_lexeme(20245, 1, ratio) is True
    # ratio 1.0 is the Phase D behaviour: every lexeme with df >= 1 is kept, df 0 still is not
    assert all(vt.bm25_keeps_lexeme(n, df, 1.0) for df in range(1, n + 1))
    assert vt.bm25_keeps_lexeme(n, 0, 1.0) is False
    # the shipped ratio is the one the spec fixed before measuring
    assert vt.cfg.retrieval.bm25_max_df_ratio == 0.5


def test_both_bm25_statements_apply_the_same_rule_with_the_ratio_as_a_parameter():
    """WHY: the Python twin is only worth anything if the SQL compares the same way, and a
    ratio written into the statement as a literal could not be changed from config (or
    tested); the OR-tsquery and the idf must also come from the kept lexemes only."""
    for sql in (vt.BM25_SAMPLE_SQL, vt.BM25_SAMPLE_SQL_DOCS, vt.BM25_UPLOAD_SQL, vt.BM25_UPLOAD_SQL_DOCS):
        flat = " ".join(sql.split())
        assert flat.count("%(max_df_ratio)s") == 1
        assert "t.df > 0 AND t.df <= %(max_df_ratio)s::float8 * " in flat      # 0 < df <= ratio * N
        assert re.search(r"\bkept AS \(", flat)
        assert "string_agg(qt, ' | ')::tsquery AS tq FROM kept" in flat       # the query is built from kept
        assert "FROM kept df" in flat                                          # so is the idf
        assert "FROM term_df" in flat                                          # ... after df was counted for all
    assert "* %(n)s::float8" in vt.BM25_SAMPLE_SQL and "* stats.n" in vt.BM25_UPLOAD_SQL   # each leg's own N


def test_every_lexeme_dropped_empties_the_keyword_legs_and_dense_still_answers(monkeypatch):
    """WHY: a question made only of near-universal words must not error or return nothing;
    the keyword legs are simply empty (the statement returns its single all-NULL row) and
    the fused answer is the dense one."""
    conn = _Conn(lexemes=["seller", "custom"], kept_sample=0, kept_upload=0,
                 dense_sample=[_row("d1", 0.9), _row("d2", 0.8)], dense_upload=[_row("u1", 0.7, title="P")])
    _wire(monkeypatch, conn, workspace_id="ws_1")
    result = asyncio.run(vt.semantic_search("seller customer", top_k=3, mode="hybrid"))
    assert [m["chunk_id"] for m in result["matches"]] == ["d1", "u1", "d2"]
    assert all(m["keyword_score"] is None for m in result["matches"])
    assert result["lexemes"] == 2 and result["lexemes_kept"] == {"sample": 0, "upload": 0}
    assert result["best_keyword_score"] is None and result["best_dense_score"] == 0.9
    assert sum("WITH q AS" in s for s in conn.sql()) == 2     # both statements did run (and found nothing)


def test_the_response_carries_the_four_new_keys_in_hybrid_mode(monkeypatch):
    """WHY: Phase E abstains on these numbers; hybrid must report the best score of each
    kind over everything fetched (not only the rows that survive the top_k cut), the
    lexeme count, and how many each keyword leg kept."""
    conn = _Conn(lexemes=["a", "b", "c"], kept_sample=1, kept_upload=2,
                 dense_sample=[_row("s1", 0.8), _row("s2", 0.6)], dense_upload=[_row("u1", 0.7)],
                 sample=[_row("s2", 12.0), _row("s9", 5.0)], upload=[_row("u1", 15.5)])
    _wire(monkeypatch, conn, workspace_id="ws_1")
    result = asyncio.run(vt.semantic_search("q", top_k=1, mode="hybrid"))
    assert len(result["matches"]) == 1
    assert result["best_dense_score"] == 0.8
    assert result["best_keyword_score"] == 15.5               # over both keyword legs
    assert result["lexemes"] == 3
    assert result["lexemes_kept"] == {"sample": 1, "upload": 2}
    # no workspace: no upload leg, and its count is 0
    conn2 = _Conn(lexemes=["a"], sample=[_row("s1", 4.0)], dense_sample=[_row("s1", 0.5)])
    _wire(monkeypatch, conn2)
    again = asyncio.run(vt.semantic_search("q", mode="hybrid"))
    assert again["lexemes_kept"] == {"sample": 1, "upload": 0} and again["best_keyword_score"] == 4.0


def test_the_response_carries_the_four_new_keys_in_dense_mode_with_the_null_rules(monkeypatch):
    """WHY: dense mode is the shipped path; it must add the metadata without changing
    `matches`. best_dense_score is the best cosine fetched (before the top_k cut), the keyword
    score, `lexemes` and the per-leg kept counts are null (dense mode runs no keyword statement)."""
    conn = _Conn(lexemes=["a", "b"], dense_sample=[_row("s1", 0.8), _row("s2", 0.6), _row("s3", 0.3)],
                 dense_upload=[_row("u1", 0.9, title="P")])
    _wire(monkeypatch, conn, workspace_id="ws_1")
    result = asyncio.run(vt.semantic_search("q", top_k=1))
    assert [m["chunk_id"] for m in result["matches"]] == ["u1"]
    assert result["best_dense_score"] == 0.9
    assert result["best_keyword_score"] is None and result["lexemes_kept"] is None
    assert result["lexemes"] is None
    assert set(result) == {"matches", "best_dense_score", "best_keyword_score", "lexemes", "lexemes_kept"}
    # nothing fetched at all: best_dense_score is null, not 0
    empty = _Conn(lexemes=[])
    _wire(monkeypatch, empty)
    nothing = asyncio.run(vt.semantic_search("the of", mode="dense"))
    assert nothing["matches"] == [] and nothing["best_dense_score"] is None and nothing["lexemes"] is None


def test_a_statement_with_no_match_is_read_as_an_empty_leg_that_still_reports_its_kept_count():
    """WHY: the BM25 statements return one all-NULL row when nothing matches (so the kept
    count survives); that row must not become a phantom match."""
    assert vt._split_kept([{"lexemes_kept": 2, "chunk_id": None, "score": None}]) == ([], 2)
    assert vt._split_kept([]) == ([], 0)
    rows = [{"lexemes_kept": 1, "chunk_id": "c1", "score": 3.0}, {"lexemes_kept": 1, "chunk_id": "c2", "score": 2.0}]
    assert vt._split_kept(rows) == ([{"chunk_id": "c1", "score": 3.0}, {"chunk_id": "c2", "score": 2.0}], 1)

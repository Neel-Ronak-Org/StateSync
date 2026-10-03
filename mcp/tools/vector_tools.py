"""
mcp/tools/vector_tools.py
MCP vector/retrieval tools: vector.semantic_search (A4.6), vector.hybrid_select
(A4.7), docs.get_passage (A4.9). Part E1.

vector.semantic_search modes (docs/specs/rag_v2.md Phase D):
  "dense"   cosine similarity only, over the sample corpus and the workspace's
            uploads, merged by score. This is the shipped behaviour.
  "hybrid"  four ranked lists -- dense over the sample corpus, dense over uploads,
            BM25 over the sample corpus, BM25 over uploads -- each cut to
            retrieval.hybrid_candidates_per_leg and fused by reciprocal rank
            fusion. Dense search is blind to identifiers (a seller id or an S0xx
            code embeds like any other string of characters); BM25 finds them by
            term rarity, which is what the keyword leg is for.
  A BM25 leg uses only the query lexemes that are not near-universal in its own
  corpus (retrieval.bm25_max_df_ratio, spec D4).
  retrieval.hybrid_dense_lists (spec F0, "D5") decides how many dense lists RRF sees:
  "separate" (the shipped value) fuses the sample list and the upload list as two lists;
  "merged" sorts them together by cosine and fuses ONE dense list. The two share a model
  and a cosine, so their scores are comparable and dense mode already merges them by
  score; two RRF lists throw that comparison away and invent ties (a sample chunk at
  rank 1 and an upload at rank 1 both get 1/61, and the tie goes to list order). The
  BM25 lists stay separate: N and df differ per corpus, so their scores are not comparable.
  Both modes also return best_dense_score, best_keyword_score, lexemes and
  lexemes_kept beside `matches`, for the abstention work of Phase E (the last
  three are null in dense mode, which runs no keyword statement).
  The BM25 legs are plain SQL run directly on the connection, exactly as the dense
  legs are: internal code, not model SQL, so they do not go through the
  data.run_select gate. The query's lexemes are always bound as one text[]
  parameter and never written into the SQL text.

vector.semantic_search scope (docs/specs/rag_v2.md Phase E):
  scope="all" (the default) searches every sample document. scope="dataset" keeps,
  in the sample-corpus legs only, the chunks whose document_id starts with one of
  the dataset's prefixes (retrieval.dataset_document_prefixes); a dataset that is a
  connected database, unknown, or without prefixes gets no sample documents and no
  sample statement is issued. Uploads are never filtered by scope: the workspace's
  folders already scope them. Prefixes travel as one bound text[] and are matched
  with starts_with, never LIKE ("_" is a LIKE wildcard).

vector.hybrid_select and the ss_embed mechanism (A4.7):
  The submitted SQL may call the registered function ss_embed(query_text).
  ss_embed itself (migrations/004_ss_embed.sql) makes no network call --
  it is STABLE and reads a session-local GUC. The actual embedding call
  happens here, in mcp, before the SQL ever reaches postgres:
    1. Run the base Layer 1 statement-type gate (shared with data.run_select).
    2. Parse the (already-validated) SQL with pglast's Visitor to find every
       FuncCall node. Reject any call to a function in the dangerous-function
       denylist below (the "extended to allow exactly that one function and
       no other non-builtin function" part of A4.7 -- exhaustively
       allowlisting Postgres's ~2000 real builtins would break ordinary
       SELECT queries, so this targets the actually dangerous surface:
       file I/O, superuser/admin, and long-sleep functions).
    3. Collect the literal string argument(s) passed to ss_embed(...), if any.
    4. Embed that text via Ollama (embeddings.py), same call shape as
       memory's trajectory embeddings but retrieval's own model/dim config.
    5. SET LOCAL statesync.query_vector = '<vector literal>' inside the same
       transaction as the query, then execute the ORIGINAL SQL text
       unmodified. ss_embed(), when postgres evaluates it, reads that GUC.
  The SQL text itself is never rewritten -- only a session GUC is set ahead
  of it -- which is both simpler and safer than substituting into
  LLM-generated SQL.
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
from typing import Any

import httpx
import psycopg

from config_loader import cfg
from connections import is_token
from db import get_connection
from embeddings import embed_text
from session import enforce_tenancy_policy, get_tenant_id, get_workspace_id
from tools.data_tools import DENIED_FUNCTIONS, _check_layer1, check_internal_schemas

logger = logging.getLogger(__name__)

try:
    import pglast
    from pglast.visitors import Visitor
    _PGLAST_AVAILABLE = True
except ImportError:
    _PGLAST_AVAILABLE = False

_CHUNK_ID_RE = re.compile(r'^[A-Za-z0-9_\-]{1,128}$')
_UPLOAD_PREFIX = "up_"   # uploaded documents' ids (mcp/tools/library_tools.py)

# The function denylist lives in data_tools (Layer 1) and now covers every
# model-SQL path; ss_embed is not on it.
_DENIED_FUNCTIONS = DENIED_FUNCTIONS


class _FuncCallCollector(Visitor if _PGLAST_AVAILABLE else object):
    """Walks a parsed SQL statement collecting every FuncCall's name and args."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list]] = []

    def visit_FuncCall(self, ancestors, node):  # noqa: N802 — pglast visitor naming
        name = ".".join(n.sval for n in node.funcname).lower()
        self.calls.append((name, list(node.args or [])))


def _format_vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _collect_relations(parsed) -> set[str]:
    """Relation names referenced anywhere in the parsed statement."""
    found: set[str] = set()

    def walk(node) -> None:
        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return
        if type(node).__name__ == "RangeVar" and getattr(node, "relname", None):
            found.add(str(node.relname).lower())
        for attr in getattr(node, "__slots__", ()) or ():
            try:
                walk(getattr(node, attr))
            except AttributeError:
                continue
    walk(parsed)
    return found


def _check_tenant_predicate(sql: str, parsed) -> str | None:
    """
    Under tenancy.enforced=true, a hybrid_select touching a tenant-scoped
    relation must carry an explicit `tenant_id = '<this tenant>'` predicate.

    semantic_search and get_passage build their own SQL and filter on
    tenant_id unconditionally, so they were never the hole. hybrid_select
    executes MODEL-AUTHORED SQL, so without this a generated query reading
    document_chunks with no tenant predicate would read across tenants. The
    predicate is REQUIRED rather than injected: silently rewriting a caller's
    SQL would make the executed statement differ from the audited one, and
    A4.5's whole premise is that what Layer 1 approved is what runs.
    """
    policy = enforce_tenancy_policy(get_tenant_id())
    if not policy.enforced:
        return None

    touched = {r for r in _collect_relations(parsed) if policy.requires_tenant_predicate(r)}
    if not touched:
        return None

    expected = re.compile(
        r"tenant_id\s*=\s*'" + re.escape(policy.tenant_id) + r"'", re.IGNORECASE
    )
    if expected.search(sql):
        return None
    return (
        f"Rejected: tenancy.enforced=true and this query reads "
        f"{sorted(touched)}, which are tenant-scoped, without an explicit "
        f"tenant_id = '{policy.tenant_id}' predicate. Add the predicate; it is "
        f"required rather than auto-injected so the audited SQL is the "
        f"executed SQL (A4.5)."
    )


def _check_hybrid_gate(sql: str) -> tuple[str | None, str | None]:
    """
    Extended Layer 1 for vector.hybrid_select (A4.7).

    Returns (rejection_reason, ss_embed_literal). rejection_reason is None
    when the SQL is accepted; ss_embed_literal is the text argument found in
    the (sole distinct) ss_embed(...) call, or None if the SQL doesn't call it.
    """
    rejection = _check_layer1(sql) or check_internal_schemas(sql)
    if rejection:
        return rejection, None

    if not _PGLAST_AVAILABLE:
        logger.error("SECURITY: pglast unavailable — hybrid_select function gate bypassed.")
        return None, None

    try:
        parsed = pglast.parse_sql(sql)
    except pglast.Error as exc:
        return f"SQL parse error: {exc}", None

    collector = _FuncCallCollector()
    for stmt in parsed:
        collector(stmt)

    literals: set[str] = set()
    for name, args in collector.calls:
        short_name = name.rsplit(".", 1)[-1]
        if short_name == "ss_embed":
            if args and type(args[0]).__name__ == "A_Const" and hasattr(args[0].val, "sval"):
                literals.add(args[0].val.sval)
            continue
        if short_name in _DENIED_FUNCTIONS:
            return f"Rejected: function {short_name!r} is not permitted.", None

    tenant_rejection = _check_tenant_predicate(sql, parsed)
    if tenant_rejection:
        return tenant_rejection, None

    if len(literals) > 1:
        return (
            "Rejected: multiple distinct ss_embed(...) literals in one "
            "query are not supported (only one query-side vector can be "
            "bound per request).",
            None,
        )

    return None, (next(iter(literals)) if literals else None)


# ── A4.6 vector.semantic_search ─────────────────────────────────────────────
_MODES = ("dense", "hybrid")
_RERANKERS = ("off", "minilm", "bge")
_RERANKER_URL = os.environ.get("RERANKER_URL", "http://reranker:8096")
_DENSE_LISTS = ("separate", "merged")
_SCOPES = ("all", "dataset")

# A sample chunk is kept if its document_id starts with any of the bound prefixes. {alias} is the
# sample table's alias in the statement it is spliced into (a fixed identifier, never user text).
_SAMPLE_SCOPE_PREDICATE = (
    "EXISTS (SELECT 1 FROM unnest(%(prefixes)s::text[]) p WHERE starts_with({alias}.document_id, p))"
)


def _scope_prefixes(scope: str | None, dataset: str | None) -> list[str] | None:
    """The sample-corpus document-id prefixes a search is limited to, or None for no limit.

    None means scope "all" (today's behaviour). A list, possibly empty, means scope "dataset":
    an empty list is a dataset with no sample documents (a connected database, one that is not
    configured, or one configured with none), and the caller issues no sample statement for it.
    Raises ValueError for an unknown scope, or for scope "dataset" without a dataset."""
    effective = scope if scope is not None else "all"
    if effective not in _SCOPES:
        raise ValueError(f"scope must be one of {list(_SCOPES)}, got {effective!r}")
    if effective == "all":
        return None
    if not dataset:
        raise ValueError('scope="dataset" needs a dataset')
    if is_token(dataset):
        return []
    return list(cfg.retrieval.dataset_document_prefixes.get(dataset) or [])


async def semantic_search(
    query_text: str,
    top_k: int | None = None,
    document_ids: list[str] | None = None,
    mode: str | None = None,
    dataset: str | None = None,
    scope: str | None = None,
    rerank: str | None = None,
) -> dict[str, Any]:
    top_k_max = cfg.retrieval.semantic_top_k_max
    if top_k is not None and not (1 <= top_k <= top_k_max):
        raise ValueError(f"top_k must be between 1 and {top_k_max}, got {top_k!r}")
    effective_mode = mode if mode is not None else cfg.retrieval.mode
    if effective_mode not in _MODES:
        raise ValueError(f"mode must be one of {list(_MODES)}, got {effective_mode!r}")
    effective_rerank = rerank if rerank is not None else "off"
    if effective_rerank not in _RERANKERS:
        raise ValueError(f"rerank must be one of {list(_RERANKERS)}, got {effective_rerank!r}")
    prefixes = _scope_prefixes(scope, dataset)
    effective_top_k = top_k if top_k is not None else cfg.retrieval.semantic_top_k
    if effective_rerank == "off":
        return await _retrieve(query_text, effective_top_k, document_ids, effective_mode, prefixes, scope)
    # Re-ranking (docs/specs/rag_v3.md): fetch rerank_candidates by the configured mode, let the
    # cross-encoder re-score them, return the top_k in its order.
    fetch_k = max(effective_top_k, int(cfg.retrieval.rerank_candidates))
    result = await _retrieve(query_text, fetch_k, document_ids, effective_mode, prefixes, scope)
    ranked, info = await _rerank(effective_rerank, query_text, result["matches"])
    return {**result, "matches": ranked[:effective_top_k], "rerank": info}


async def _rerank(model: str, query_text: str, matches: list[dict]) -> tuple[list[dict], dict[str, Any]]:
    """(matches in re-ranker order with rerank_score, info). Any failure keeps the original order:
    a re-ranker problem degrades ranking quality, it never fails a search."""
    if not matches:
        return matches, {"model": model, "applied": False, "reason": "no candidates"}
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=float(cfg.retrieval.rerank_timeout_s)) as client:
            reply = await client.post(_RERANKER_URL.rstrip("/") + "/rerank", json={
                "model": model, "query": query_text, "passages": [m["content_md"] or "" for m in matches]})
        body = reply.json()
        scores = body.get("scores") if reply.status_code == 200 and body.get("ok") else None
        if not isinstance(scores, list) or len(scores) != len(matches):
            raise ValueError(f"reranker answered {reply.status_code}: {body.get('error') or 'bad scores'}")
    except Exception as exc:  # noqa: BLE001 -- fall back, logged
        logger.warning("vector.semantic_search: rerank with %s failed (%s); original order kept", model, exc)
        return matches, {"model": model, "applied": False, "reason": type(exc).__name__,
                         "ms": round((time.monotonic() - started) * 1000)}
    ranked = sorted(({**m, "rerank_score": float(sc)} for m, sc in zip(matches, scores, strict=True)),
                    key=lambda m: m["rerank_score"], reverse=True)
    return ranked, {"model": model, "applied": True, "candidates": len(matches),
                    "ms": round((time.monotonic() - started) * 1000)}


async def _retrieve(
    query_text: str, effective_top_k: int, document_ids: list[str] | None, effective_mode: str,
    prefixes: list[str] | None, scope: str | None,
) -> dict[str, Any]:
    """The first-stage search (dense or hybrid), exactly as before re-ranking existed."""
    tenant_id = get_tenant_id()

    vector = await embed_text(
        query_text,
        model=cfg.retrieval.embedding_model,
        dim=cfg.retrieval.embedding_dim,
    )
    vector_literal = _format_vector_literal(vector)

    workspace_id = get_workspace_id()
    if effective_mode == "hybrid":
        return await _hybrid_search(
            query_text, vector_literal, effective_top_k, document_ids, tenant_id, workspace_id, prefixes,
        )

    where_clauses = ["dc.tenant_id = %(tenant_id)s"]
    params: dict[str, Any] = {"tenant_id": tenant_id, "qvec": vector_literal, "top_k": effective_top_k}
    if document_ids:
        where_clauses.append("dc.document_id = ANY(%(document_ids)s)")
        params["document_ids"] = document_ids
    sample_params = params
    if prefixes:
        where_clauses.append(_SAMPLE_SCOPE_PREDICATE.format(alias="dc"))
        sample_params = {**params, "prefixes": prefixes}

    sql = f"""
        SELECT dc.chunk_id, dc.document_id, dc.content_md, dc.page,
               1 - (dc.embedding <=> %(qvec)s::vector) AS score, NULL::text AS title
        FROM document_chunks dc
        WHERE {' AND '.join(where_clauses)}
        ORDER BY dc.embedding <=> %(qvec)s::vector
        LIMIT %(top_k)s
    """

    async with get_connection() as conn:
        await _use_exact_search(conn)
        matches = []
        # A scope with no prefixes has no sample documents: no sample statement at all.
        if prefixes is None or prefixes:
            matches = await _fetch_dicts(conn, sql, sample_params)
        library = await _library_matches(conn, workspace_id, params) if workspace_id else []

    best_dense_score = _best_score(matches, library)
    if library:
        # Same model and the same cosine measure on both sides, so the scores
        # are comparable: merge and keep the best top_k overall.
        matches = sorted(matches + library, key=lambda m: m["score"], reverse=True)[:effective_top_k]

    logger.info(
        "vector.semantic_search: tenant=%s workspace=%s top_k=%d scope=%s matches=%d library_matches=%d",
        tenant_id, workspace_id, effective_top_k, scope or "all", len(matches), len(library),
    )
    return {
        # The keyword fields are null here: the shipped dense path issues no keyword
        # statement at all, not even the lexeme count.
        "matches": matches, "best_dense_score": best_dense_score, "best_keyword_score": None,
        "lexemes": None, "lexemes_kept": None,
    }


# ── Phase D: hybrid retrieval ───────────────────────────────────────────────
# A lexeme as a single-lexeme tsquery, built in SQL from the text[] parameter: wrapped
# in single quotes with ' and \ escaped, which tsquery reads as one literal term (no
# stemming, no operators), so a lexeme such as 'x.com:80' or one holding & or | cannot
# change the structure of the query.
_LEXEME_TSQUERY = "('''' || replace(replace(lex, '\\', '\\\\'), '''', '''''') || '''')"

_SAMPLE_DOC_FILTER = " AND s.document_id = ANY(%(document_ids)s)"
_UPLOAD_DOC_FILTER = " AND c.document_id = ANY(%(document_ids)s)"
_SAMPLE_SCOPE_FILTER = " AND " + _SAMPLE_SCOPE_PREDICATE.format(alias="s")

_UPLOAD_SCOPE = """FROM workspace_docs.chunks c
            JOIN workspace_docs.documents d ON d.document_id = c.document_id
            JOIN workspace_docs.workspace_folders wf ON wf.folder_id = d.folder_id
            WHERE wf.workspace_id = %(workspace_id)s AND c.tenant_id = %(tenant_id)s AND d.status = 'ready'"""


def _bm25_sample_sql(doc_filter: str) -> str:
    """BM25 over the sample corpus (search.sample_terms). n and avgdl arrive as
    parameters (cached per tenant, _sample_stats); document frequency is counted over
    the whole tenant corpus, so a document filter narrows the candidates without
    changing how rare a term is. A dataset scope (prefixes) narrows the candidates the
    same way, in `top` only: N, avgdl and df stay corpus-wide per tenant, so a term is as
    rare as it always was whichever documents are in scope. df is counted for every lexeme
    first; only the kept ones (0 < df <= max_df_ratio * n, spec D4) build the OR-tsquery
    and carry an idf. With none kept, tq is NULL and no chunk matches. Every row carries
    lexemes_kept (an all-NULL row when there are no matches), so the count survives an
    empty result. doc_filter is a concatenation of the fixed strings above."""
    return f"""
    WITH q AS (
        SELECT lex, {_LEXEME_TSQUERY} AS qt FROM unnest(%(lexemes)s::text[]) AS lex
    ),
    term_df AS (
        SELECT q.lex, q.qt, df.df
        FROM q
        CROSS JOIN LATERAL (
            SELECT count(*)::float8 AS df FROM search.sample_terms s
            WHERE s.tenant_id = %(tenant_id)s AND s.tsv @@ q.qt::tsquery
        ) df
    ),
    kept AS (
        SELECT t.lex, t.qt, t.df FROM term_df t
        WHERE t.df > 0 AND t.df <= %(max_df_ratio)s::float8 * %(n)s::float8
    ),
    any_q AS (SELECT string_agg(qt, ' | ')::tsquery AS tq FROM kept),
    idf AS (
        SELECT df.lex, ln(1 + (%(n)s::float8 - df.df + 0.5) / (df.df + 0.5)) AS idf
        FROM kept df
    ),
    top AS (
        SELECT s.chunk_id,
               sum(idf.idf * (e.tf * (%(k1)s + 1))
                   / (e.tf + %(k1)s * (1 - %(b)s + %(b)s * s.len / %(avgdl)s::float8))) AS score
        FROM search.sample_terms s
        CROSS JOIN any_q
        CROSS JOIN LATERAL (SELECT lexeme, coalesce(cardinality(positions), 1) AS tf FROM unnest(s.tsv)) e
        JOIN idf ON idf.lex = e.lexeme
        WHERE s.tenant_id = %(tenant_id)s AND s.tsv @@ any_q.tq{doc_filter}
        GROUP BY s.chunk_id
        ORDER BY score DESC, s.chunk_id
        LIMIT %(limit)s
    )
    SELECT (SELECT count(*) FROM kept)::int AS lexemes_kept,
           dc.chunk_id, dc.document_id, dc.content_md, dc.page, top.score, NULL::text AS title
    FROM (SELECT 1) one
    LEFT JOIN top ON true
    LEFT JOIN document_chunks dc ON dc.chunk_id = top.chunk_id AND dc.tenant_id = %(tenant_id)s
    ORDER BY top.score DESC NULLS LAST, dc.chunk_id
"""


def _bm25_upload_sql(doc_filter: str) -> str:
    """BM25 over the workspace's uploads (workspace_docs.chunks.tsv): the same
    arithmetic and the same df rule, with N and the mean length computed live from
    the chunks in scope (small and workspace-specific, so there is nothing worth
    caching)."""
    return f"""
    WITH q AS (
        SELECT lex, {_LEXEME_TSQUERY} AS qt FROM unnest(%(lexemes)s::text[]) AS lex
    ),
    stats AS (
        SELECT count(*)::float8 AS n, coalesce(avg(length(c.tsv)), 1)::float8 AS avgdl
        {_UPLOAD_SCOPE}
    ),
    term_df AS (
        SELECT q.lex, q.qt, df.df
        FROM q
        CROSS JOIN LATERAL (
            SELECT count(*)::float8 AS df
            {_UPLOAD_SCOPE} AND c.tsv @@ q.qt::tsquery
        ) df
    ),
    kept AS (
        SELECT t.lex, t.qt, t.df FROM term_df t
        CROSS JOIN stats
        WHERE t.df > 0 AND t.df <= %(max_df_ratio)s::float8 * stats.n
    ),
    any_q AS (SELECT string_agg(qt, ' | ')::tsquery AS tq FROM kept),
    idf AS (
        SELECT df.lex, ln(1 + (stats.n - df.df + 0.5) / (df.df + 0.5)) AS idf
        FROM kept df
        CROSS JOIN stats
    ),
    top AS (
        SELECT c.chunk_id,
               sum(idf.idf * (e.tf * (%(k1)s + 1))
                   / (e.tf + %(k1)s * (1 - %(b)s + %(b)s * length(c.tsv) / stats.avgdl))) AS score
        FROM workspace_docs.chunks c
        JOIN workspace_docs.documents d ON d.document_id = c.document_id
        JOIN workspace_docs.workspace_folders wf ON wf.folder_id = d.folder_id
        CROSS JOIN any_q
        CROSS JOIN stats
        CROSS JOIN LATERAL (SELECT lexeme, coalesce(cardinality(positions), 1) AS tf FROM unnest(c.tsv)) e
        JOIN idf ON idf.lex = e.lexeme
        WHERE wf.workspace_id = %(workspace_id)s AND c.tenant_id = %(tenant_id)s AND d.status = 'ready'
              AND c.tsv @@ any_q.tq{doc_filter}
        GROUP BY c.chunk_id
        ORDER BY score DESC, c.chunk_id
        LIMIT %(limit)s
    )
    SELECT (SELECT count(*) FROM kept)::int AS lexemes_kept,
           c.chunk_id, c.document_id, c.content_md, c.page, top.score, d.title, c.section, c.kind
    FROM (SELECT 1) one
    LEFT JOIN top ON true
    LEFT JOIN workspace_docs.chunks c ON c.chunk_id = top.chunk_id
    LEFT JOIN workspace_docs.documents d ON d.document_id = c.document_id
    ORDER BY top.score DESC NULLS LAST, c.chunk_id
"""


BM25_SAMPLE_SQL = _bm25_sample_sql("")
BM25_SAMPLE_SQL_DOCS = _bm25_sample_sql(_SAMPLE_DOC_FILTER)
BM25_SAMPLE_SQL_SCOPED = _bm25_sample_sql(_SAMPLE_SCOPE_FILTER)
BM25_SAMPLE_SQL_DOCS_SCOPED = _bm25_sample_sql(_SAMPLE_DOC_FILTER + _SAMPLE_SCOPE_FILTER)
BM25_UPLOAD_SQL = _bm25_upload_sql("")
BM25_UPLOAD_SQL_DOCS = _bm25_upload_sql(_UPLOAD_DOC_FILTER)

# tenant -> (monotonic time computed, chunk count, mean length). The sample corpus
# changes only on a re-ingest, so its statistics are cached for bm25_stats_ttl_s.
_sample_stats_cache: dict[str, tuple[float, float, float]] = {}


def bm25_idf(n: float, df: float) -> float:
    """BM25 inverse document frequency, as the SQL computes it: ln(1 + (N - df + 0.5) / (df + 0.5)).

    The 0.5 is the Robertson-Sparck Jones smoothing term of the formula itself, not a tunable."""
    return math.log(1 + (n - df + 0.5) / (df + 0.5))  # noqa: literals


def bm25_term_score(idf: float, tf: float, doc_len: float, avgdl: float, k1: float, b: float) -> float:
    """One term's BM25 contribution: idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len / avgdl))."""
    return idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * doc_len / avgdl))


def bm25_keeps_lexeme(n: float, df: float, ratio: float) -> bool:
    """Whether a BM25 leg uses a query lexeme (spec D4): 0 < df <= ratio * n.

    n and df are counted in the leg's own corpus. The SQL applies this same comparison
    (df > 0 AND df <= %(max_df_ratio)s * n); a lexeme with df 0 matches nothing, so
    dropping it changes no result."""
    return 0 < df <= ratio * n


def _best_score(*lists: list[dict]) -> float | None:
    """The highest `score` over every row of every list, or None when all are empty."""
    scores = [row["score"] for rows in lists for row in rows if row.get("score") is not None]
    return max(scores) if scores else None


def merge_dense_lists(sample: list[dict], upload: list[dict]) -> list[dict]:
    """The sample and upload dense candidates as ONE list, best cosine first (spec F0).

    Both come from the same model and the same cosine, so a score of 0.7 means the same on
    either side. Sorted by score descending, then chunk_id, so equal scores are deterministic.
    Nothing is cut: each input is already at most hybrid_candidates_per_leg long, so the
    result is at most twice that, which is the limit the spec sets."""
    return sorted(sample + upload, key=lambda m: (-m["score"], m["chunk_id"]))


def rrf_fuse(
    dense_lists: list[list[dict]], keyword_lists: list[list[dict]], k: int, top_k: int,
) -> list[dict]:
    """Reciprocal rank fusion over ranked lists (each best-first): score = sum 1 / (k + rank).

    Ranks are 1-based and counted within each list. A row's fields come from the first
    list it appears in (dense lists first, so an upload keeps its title and section);
    dense_score and keyword_score are the chunk's own best score from the dense and the
    BM25 lists, None where it was in none of that kind. Ties keep the order of first
    appearance, so the result is deterministic.
    """
    fused: dict[str, dict[str, Any]] = {}
    for lists, field in ((dense_lists, "dense_score"), (keyword_lists, "keyword_score")):
        for ranked in lists:
            for rank, row in enumerate(ranked, start=1):
                entry = fused.get(row["chunk_id"])
                if entry is None:
                    entry = fused[row["chunk_id"]] = {
                        "chunk_id": row["chunk_id"], "document_id": row["document_id"],
                        "content_md": row["content_md"], "page": row.get("page"), "score": 0.0,
                        "dense_score": None, "keyword_score": None, "title": row.get("title"),
                        "section": row.get("section"), "kind": row.get("kind"),
                    }
                entry["score"] += 1 / (k + rank)
                entry[field] = row["score"] if entry[field] is None else max(entry[field], row["score"])
    return sorted(fused.values(), key=lambda m: m["score"], reverse=True)[:top_k]


async def _query_lexemes(conn: Any, query_text: str) -> list[str]:
    """The query's lexemes as the english text-search configuration reads them (stop words dropped, stemmed)."""
    cur = await conn.execute(
        "SELECT tsvector_to_array(to_tsvector('english', %(query)s))", {"query": query_text},
    )
    row = await cur.fetchone()
    return list(row[0]) if row and row[0] else []


async def _sample_stats(conn: Any, tenant_id: str) -> tuple[float, float]:
    """(chunk count, mean tsvector length) of the tenant's sample corpus, cached for bm25_stats_ttl_s."""
    now = time.monotonic()
    cached = _sample_stats_cache.get(tenant_id)
    if cached is not None and now - cached[0] < cfg.retrieval.bm25_stats_ttl_s:
        return cached[1], cached[2]
    cur = await conn.execute(
        "SELECT count(*)::float8, coalesce(avg(len), 1)::float8 FROM search.sample_terms WHERE tenant_id = %(tenant_id)s",
        {"tenant_id": tenant_id},
    )
    n, avgdl = await cur.fetchone()
    _sample_stats_cache[tenant_id] = (now, n, avgdl)
    return n, avgdl


async def _fetch_dicts(conn: Any, sql: str, params: dict[str, Any]) -> list[dict]:
    cur = await conn.execute(sql, params)
    rows = await cur.fetchall()
    col_names = [desc.name for desc in cur.description] if cur.description else []
    return [dict(zip(col_names, row)) for row in rows]


def _split_kept(rows: list[dict]) -> tuple[list[dict], int]:
    """(matches, lexemes kept) from a BM25 statement's rows: every row carries lexemes_kept,
    and the one row of an empty result has no chunk_id."""
    kept = rows[0]["lexemes_kept"] if rows else 0
    return [{k: v for k, v in r.items() if k != "lexemes_kept"} for r in rows if r["chunk_id"] is not None], kept


async def _bm25_sample_matches(
    conn: Any, lexemes: list[str], tenant_id: str, document_ids: list[str] | None, limit: int,
    prefixes: list[str] | None = None,
) -> tuple[list[dict], int]:
    """(ranked matches, lexemes kept) of the sample-corpus keyword leg. prefixes limits the
    candidates to documents whose id starts with one of them; the statistics stay corpus-wide."""
    if not lexemes:
        return [], 0
    n, avgdl = await _sample_stats(conn, tenant_id)
    if n == 0:
        return [], 0
    params: dict[str, Any] = {
        "lexemes": lexemes, "tenant_id": tenant_id, "n": n, "avgdl": avgdl,
        "k1": cfg.retrieval.bm25_k1, "b": cfg.retrieval.bm25_b, "limit": limit,
        "max_df_ratio": cfg.retrieval.bm25_max_df_ratio,
    }
    if document_ids:
        params["document_ids"] = document_ids
    if prefixes:
        params["prefixes"] = prefixes
        sql = BM25_SAMPLE_SQL_DOCS_SCOPED if document_ids else BM25_SAMPLE_SQL_SCOPED
    else:
        sql = BM25_SAMPLE_SQL_DOCS if document_ids else BM25_SAMPLE_SQL
    return _split_kept(await _fetch_dicts(conn, sql, params))


async def _bm25_upload_matches(
    conn: Any, lexemes: list[str], tenant_id: str, workspace_id: str,
    document_ids: list[str] | None, limit: int,
) -> tuple[list[dict], int]:
    """(ranked matches, lexemes kept) of the uploads keyword leg."""
    if not lexemes:
        return [], 0
    params: dict[str, Any] = {
        "lexemes": lexemes, "tenant_id": tenant_id, "workspace_id": workspace_id,
        "k1": cfg.retrieval.bm25_k1, "b": cfg.retrieval.bm25_b, "limit": limit,
        "max_df_ratio": cfg.retrieval.bm25_max_df_ratio,
    }
    if document_ids:
        params["document_ids"] = document_ids
    return _split_kept(await _fetch_dicts(conn, BM25_UPLOAD_SQL_DOCS if document_ids else BM25_UPLOAD_SQL, params))


async def _hybrid_search(
    query_text: str, vector_literal: str, top_k: int, document_ids: list[str] | None,
    tenant_id: str, workspace_id: str | None, prefixes: list[str] | None = None,
) -> dict[str, Any]:
    dense_lists = cfg.retrieval.hybrid_dense_lists
    if dense_lists not in _DENSE_LISTS:
        raise ValueError(f"retrieval.hybrid_dense_lists must be one of {list(_DENSE_LISTS)}, got {dense_lists!r}")
    candidates = cfg.retrieval.hybrid_candidates_per_leg
    dense_params: dict[str, Any] = {"tenant_id": tenant_id, "qvec": vector_literal, "top_k": candidates}
    where = ["dc.tenant_id = %(tenant_id)s"]
    if document_ids:
        where.append("dc.document_id = ANY(%(document_ids)s)")
        dense_params["document_ids"] = document_ids
    sample_params = dense_params
    if prefixes:
        where.append(_SAMPLE_SCOPE_PREDICATE.format(alias="dc"))
        sample_params = {**dense_params, "prefixes": prefixes}
    dense_sql = f"""
        SELECT dc.chunk_id, dc.document_id, dc.content_md, dc.page,
               1 - (dc.embedding <=> %(qvec)s::vector) AS score, NULL::text AS title
        FROM document_chunks dc
        WHERE {' AND '.join(where)}
        ORDER BY dc.embedding <=> %(qvec)s::vector
        LIMIT %(top_k)s
    """

    async with get_connection() as conn:
        # Keyword legs first: the exact-search switch below turns index scans off
        # for the rest of the transaction and the GIN lookups should not feel it.
        t0 = time.monotonic()
        lexemes = await _query_lexemes(conn, query_text)
        # A scope with no prefixes has no sample documents: neither sample leg issues a statement.
        no_sample = prefixes is not None and not prefixes
        bm25_sample, kept_sample = (
            ([], 0) if no_sample
            else await _bm25_sample_matches(conn, lexemes, tenant_id, document_ids, candidates, prefixes)
        )
        t1 = time.monotonic()
        bm25_upload, kept_upload = (
            await _bm25_upload_matches(conn, lexemes, tenant_id, workspace_id, document_ids, candidates)
            if workspace_id else ([], 0)
        )
        t2 = time.monotonic()

        await _use_exact_search(conn)
        dense_sample = [] if no_sample else await _fetch_dicts(conn, dense_sql, sample_params)
        dense_upload = await _library_matches(conn, workspace_id, dense_params) if workspace_id else []

    fused_dense = [dense_sample, dense_upload] if dense_lists == "separate" else [merge_dense_lists(dense_sample, dense_upload)]
    matches = rrf_fuse(fused_dense, [bm25_sample, bm25_upload], cfg.retrieval.rrf_k, top_k)
    logger.info(
        "vector.semantic_search: mode=hybrid tenant=%s workspace=%s top_k=%d scope=%s dense_lists=%s matches=%d "
        "lexemes=%d lexemes_kept=%d/%d dense=%d/%d bm25=%d/%d bm25_sample_ms=%d bm25_upload_ms=%d",
        tenant_id, workspace_id, top_k, "all" if prefixes is None else "dataset", dense_lists, len(matches), len(lexemes),
        kept_sample, kept_upload,
        len(dense_sample), len(dense_upload), len(bm25_sample), len(bm25_upload),
        (t1 - t0) * 1000, (t2 - t1) * 1000,
    )
    return {
        "matches": matches, "best_dense_score": _best_score(dense_sample, dense_upload),
        "best_keyword_score": _best_score(bm25_sample, bm25_upload), "lexemes": len(lexemes),
        "lexemes_kept": {"sample": kept_sample, "upload": kept_upload},
    }


async def _library_matches(conn: Any, workspace_id: str, params: dict[str, Any]) -> list[dict]:
    """Chunks of READY uploaded documents in the folders this workspace chose.

    Uploads (docs/V3_PLAN.md Phase 3c slice 5, migration 024) live in
    workspace_docs, apart from the sample corpus. The workspace comes from the
    session (X-Workspace-ID); the orchestrator has already checked that it is
    the caller's own, and row-level security plus the tenant predicate keep
    every other tenant's links, documents and chunks out of reach.
    """
    where = ["wf.workspace_id = %(workspace_id)s", "c.tenant_id = %(tenant_id)s", "d.status = 'ready'"]
    if "document_ids" in params:
        where.append("c.document_id = ANY(%(document_ids)s)")
    cur = await conn.execute(f"""
        SELECT c.chunk_id, c.document_id, c.content_md, c.page,
               1 - (c.embedding <=> %(qvec)s::vector) AS score, d.title, c.section, c.kind
        FROM workspace_docs.chunks c
        JOIN workspace_docs.documents d ON d.document_id = c.document_id
        JOIN workspace_docs.workspace_folders wf ON wf.folder_id = d.folder_id
        WHERE {' AND '.join(where)}
        ORDER BY c.embedding <=> %(qvec)s::vector
        LIMIT %(top_k)s
    """, {**params, "workspace_id": workspace_id})
    rows = await cur.fetchall()
    col_names = [desc.name for desc in cur.description] if cur.description else []
    return [dict(zip(col_names, row)) for row in rows]


async def _use_exact_search(conn: Any) -> None:
    """Make this transaction's vector ORDER BY exact instead of approximate.

    With retrieval.exact_vector_search on, index scans are switched off for the
    current transaction only, so the planner cannot pick the approximate HNSW
    index and ranks every chunk by true cosine distance (see the config
    comment for the measurement behind this). SET LOCAL ends with the
    transaction, and get_connection() always rolls back, so it never reaches
    the next pooled use of the connection.
    """
    if cfg.retrieval.exact_vector_search:
        await conn.execute("SET LOCAL enable_indexscan = off")


# ── A4.7 vector.hybrid_select ───────────────────────────────────────────────
async def hybrid_select(
    sql: str,
    max_rows: int | None = None,
) -> dict[str, Any]:
    effective_max = min(max(1, max_rows if max_rows is not None else cfg.mcp.max_rows_default), cfg.mcp.max_rows_hard_cap)

    rejection, ss_embed_literal = _check_hybrid_gate(sql)
    if rejection:
        logger.warning("hybrid_select gate rejected SQL: %s", rejection)
        return {
            "ok": False, "columns": [], "rows": [], "row_count": 0, "truncated": False,
            "error": {"sqlstate": None, "message": f"[Layer 1 Gate] {rejection}"},
        }

    async with get_connection() as conn:
        try:
            if ss_embed_literal is not None:
                vector = await embed_text(
                    ss_embed_literal,
                    model=cfg.retrieval.embedding_model,
                    dim=cfg.retrieval.embedding_dim,
                )
                # SET LOCAL: scoped to this transaction only, cleared on the
                # rollback get_connection() always performs -- never leaks
                # into the next pooled use of this connection.
                await conn.execute(
                    "SELECT set_config('statesync.query_vector', %s, true)",
                    (_format_vector_literal(vector),),
                )

            await _use_exact_search(conn)
            cur = await conn.execute(sql)
            rows = await cur.fetchmany(effective_max + 1)
            col_names = [desc.name for desc in cur.description] if cur.description else []
            truncated = len(rows) > effective_max
            result_rows = [dict(zip(col_names, row)) for row in rows[:effective_max]]

            logger.info(
                "vector.hybrid_select: ok=True rows=%d truncated=%s used_ss_embed=%s",
                len(result_rows), truncated, ss_embed_literal is not None,
            )
            return {
                "ok": True, "columns": col_names, "rows": result_rows,
                "row_count": len(result_rows), "truncated": truncated, "error": None,
            }

        except psycopg.errors.InsufficientPrivilege as exc:
            logger.warning("vector.hybrid_select: permission denied (42501): %s", exc)
            return {
                "ok": False, "columns": [], "rows": [], "row_count": 0, "truncated": False,
                "error": {"sqlstate": "42501", "message": str(exc)},
            }
        except psycopg.Error as exc:
            sqlstate = exc.sqlstate if hasattr(exc, "sqlstate") else None
            logger.warning("vector.hybrid_select: query error sqlstate=%s message=%s", sqlstate, exc)
            return {
                "ok": False, "columns": [], "rows": [], "row_count": 0, "truncated": False,
                "error": {"sqlstate": sqlstate, "message": str(exc)},
            }


# ── A4.9 docs.get_passage ───────────────────────────────────────────────────
async def get_passage(chunk_id: str) -> dict[str, Any]:
    if not _CHUNK_ID_RE.match(chunk_id):
        raise ValueError(f"Invalid chunk_id: {chunk_id!r}")

    tenant_id = get_tenant_id()
    if chunk_id.startswith(_UPLOAD_PREFIX):
        # An uploaded document's chunk (workspace_docs, migration 024); it has
        # no source URI, so its file name stands in.
        sql = """
            SELECT c.chunk_id, c.document_id, c.content_md, c.page, d.filename AS source_uri, d.title
            FROM workspace_docs.chunks c
            JOIN workspace_docs.documents d ON d.document_id = c.document_id
            WHERE c.chunk_id = %(chunk_id)s AND c.tenant_id = %(tenant_id)s
        """
    else:
        sql = """
            SELECT dc.chunk_id, dc.document_id, dc.content_md, dc.page, d.source_uri, d.title
            FROM document_chunks dc
            JOIN documents d ON d.document_id = dc.document_id
            WHERE dc.chunk_id = %(chunk_id)s AND dc.tenant_id = %(tenant_id)s
        """
    async with get_connection() as conn:
        cur = await conn.execute(sql, {"chunk_id": chunk_id, "tenant_id": tenant_id})
        row = await cur.fetchone()

    if row is None:
        raise ValueError(f"chunk_id not found: {chunk_id!r}")

    col_names = [desc.name for desc in cur.description]
    result = dict(zip(col_names, row))
    logger.debug("docs.get_passage: chunk_id=%s document_id=%s", chunk_id, result["document_id"])
    return result

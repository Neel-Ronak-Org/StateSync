"""
orchestrator/nodes/unstructured_knowledge.py
Unstructured Knowledge Engine (UKE) -- Part E1. Replaces both the `docs` and
`hybrid` stub registrations (Phase B) with the real unstructured-path node
(state.py's own "--- unstructured path (Part E1) ---" comment on
`retrievals` is what this name refers to).

docs route: one semantic_search over the (normalized) query, expanded to
every chunk of every document any match belongs to -- see
_semantic_search_expanded's docstring for why: a table chunk's Markdown text
("Metro NCR | 36 hours | 2% penalty") does not embed close to a natural-
language question the way a document's own narrative chunk does, so a flat
top-k can rank the table chunk out of the result entirely even when its
document is clearly the right one (verified empirically -- see
docs/phase_e1_log.md). Expanding to sibling chunks of an already-relevant
document is what makes E1-V2's table-value retrieval reliable.

hybrid route (D7; graph.py, rebuilt 2026-10-02): hybrid_plan_node decomposes the
compound question into a SQL sub-task and a doc sub-task (one LLM call, gated by
retrieval.hybrid_decomposition.enabled). The SQL sub-task then runs through sql_agent_node -- the
same agent as a data-only question, with the workspace's approved knowledge, the fast path,
templates and the correction loop -- instead of the single-shot generation that used to live here.
hybrid_docs_node runs the doc sub-task last: if the SQL result exposes a seller_id/provider_id
column, it is narrowed via vector.hybrid_select (a real ss_embed(...) usage, A4.7) to only that
party's documents; otherwise the two-round semantic search runs. Both state.sql_rows/executed_sql
and state.retrievals get populated; the Synthesizer emits both citation kinds.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from config_loader import cfg
from llm.provider import get_agent_llm
from query_rewrite import rewrite_for_search
from mcp_client import (
    MCPSession, vector_hybrid_select, vector_semantic_search,
)

logger = logging.getLogger(__name__)

_DECOMPOSER_SYSTEM_PROMPT = """\
You are decomposing a compound question for an ONDC logistics analytics \
system into two independent sub-questions: one answerable from the \
structured database (orders, fulfillments, sellers, disputes, etc.), and \
one answerable from unstructured documents (supplier agreements, SLA \
annexures, policies, SOPs).

Rules:
1. Respond with ONLY a JSON object: {"sql_subtask": "<string>", "doc_subtask": "<string>"}
2. sql_subtask and doc_subtask are QUESTIONS IN ENGLISH, phrased exactly
   like the original question -- never SQL, never a table or column name.
   A separate downstream step writes the actual SQL from your sql_subtask
   question; your job is only to isolate which part of the compound
   question that step should answer.
3. sql_subtask should ask only for structured facts or entities (which
   database rows meet some condition), and should ask for the relevant ID
   (seller ID or provider ID) whenever the question concerns specific
   sellers or logistics providers -- but NEVER ask sql_subtask for a
   penalty amount, policy figure, or contract clause. Those exist only in
   documents, never as a database column, and belong in doc_subtask only.
4. doc_subtask should ask for whatever document-derived information
   (policy figures, penalty clauses, SLA commitments) the compound question needs.
5. Preserve entity names/IDs mentioned in the original question in both
   sub-questions where relevant -- do not translate or paraphrase them away.
6. No explanation outside the JSON object.

Worked example. Input: "Which sellers breached their contracted delivery
SLA last quarter, and what penalty does their agreement specify?"
Correct output:
{"sql_subtask": "Which sellers had a delivery where the delivered date was
later than the promised date in the last quarter? Include seller_id.",
"doc_subtask": "What penalty does a seller's supplier agreement specify for
a late delivery?"}
Note sql_subtask asks a plain question about delivery timing (answerable
from fulfillments' existing delivered_at/promised_at columns) and never
mentions a penalty -- the penalty question goes to doc_subtask only."""

def _sql_literal(value: str) -> str:
    """Standard SQL-92 string literal escaping (double embedded quotes).
    Deliberately not reusing psycopg here: the orchestrator holds no
    postgres dependency at all (A1 -- it never talks to the DBs directly),
    and this narrow case (our own constructed text, not raw user SQL)
    doesn't warrant adding one just for string escaping."""
    return "'" + value.replace("'", "''") + "'"


def _strip_fences(text: str) -> str:
    if text.startswith("```"):
        return "\n".join(line for line in text.split("\n") if not line.startswith("```")).strip()
    return text


def should_abstain(
    best_dense: float | None, best_keyword: float | None, min_dense: float, min_keyword: float,
) -> bool:
    """RAG v2 Phase E: nothing relevant was found. True iff the best dense score is missing or
    below min_dense AND the best keyword score is missing or below min_keyword (strict <, so a
    score equal to its threshold counts as relevant). A missing score is a leg that found nothing."""
    return (best_dense is None or best_dense < min_dense) and (best_keyword is None or best_keyword < min_keyword)


async def _semantic_search_expanded(
    session: MCPSession, query_text: str, top_k: int, mode: str | None = None, dataset: str | None = None,
    scope: str | None = None, abstain: bool = False, rerank: str | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    """
    Two-round retrieval: first an unrestricted semantic_search to find which
    DOCUMENTS are relevant, then a second call restricted to those documents'
    IDs with a generous top_k, so every chunk of an already-relevant
    document is available (not just whichever single chunk happened to
    embed closest to the question) -- see module docstring for why this
    matters for table chunks specifically. Both rounds use the same retrieval
    mode (pipeline.retrieval_mode: "dense" or "hybrid") and carry the dataset and the scope
    (pipeline.doc_scope).

    Returns (matches, meta). meta carries the FIRST round's best_dense_score and
    best_keyword_score (never the fused score: an RRF score is about the same for answerable
    and unanswerable questions) and abstained. With abstain on and should_abstain true on those
    two scores, the second round is skipped and matches is empty (Phase E).
    """
    first = await vector_semantic_search(session, query_text, top_k=top_k, mode=mode, dataset=dataset, scope=scope,
                                         rerank=rerank)
    matches = first.get("matches", [])
    meta: dict[str, Any] = {
        "best_dense_score": first.get("best_dense_score"),
        "best_keyword_score": first.get("best_keyword_score"),
        "abstained": False,
    }
    if abstain and should_abstain(
        meta["best_dense_score"], meta["best_keyword_score"],
        cfg.retrieval.abstain_min_dense, cfg.retrieval.abstain_min_keyword,
    ):
        meta["abstained"] = True
        return [], meta

    # Document ranking order = order of first appearance in the score-sorted
    # first call (semantic_search already returns matches best-first).
    doc_order: list[str] = []
    seen_docs: set[str] = set()
    for m in matches:
        if m["document_id"] not in seen_docs:
            seen_docs.add(m["document_id"])
            doc_order.append(m["document_id"])
    if not doc_order:
        return [], meta

    expanded = await vector_semantic_search(
        session, query_text, top_k=cfg.retrieval.semantic_top_k_max, document_ids=doc_order,
        mode=mode, dataset=dataset, scope=scope, rerank=rerank,
    )
    by_chunk_id = {m["chunk_id"]: m for m in expanded.get("matches", [])}
    for m in matches:
        by_chunk_id[m["chunk_id"]] = m  # first call's score is the authoritative one for these

    # Document-major ordering, not a flat re-sort by raw chunk score: a
    # table chunk's Markdown ("Metro NCR | 36 hours | 2% penalty") embeds
    # further from a natural-language question than its own document's
    # narrative chunk does (verified empirically -- see docs/phase_e1_log.md),
    # so a flat score-sort systematically buries table chunks behind OTHER
    # documents' narrative chunks even when the table's own document is the
    # single best match. Grouping by document (in the document's own rank
    # order) and then by ordinal within the document (chunk_id's zero-padded
    # _cNNN suffix sorts correctly as a string) keeps a relevant document's
    # narrative and table chunks adjacent, so truncating to a citation/
    # context limit doesn't split them apart.
    by_doc: dict[str, list[dict]] = {}
    for chunk in by_chunk_id.values():
        by_doc.setdefault(chunk["document_id"], []).append(chunk)

    ordered: list[dict] = []
    for doc_id in doc_order:
        ordered.extend(sorted(by_doc.get(doc_id, []), key=lambda c: c["chunk_id"]))
    return ordered, meta


async def _targeted_doc_search(
    session: MCPSession, query_text: str, party_ids: list[str], top_k: int,
) -> list[dict]:
    """
    A real vector.hybrid_select usage (A4.7): SQL-side filter (only the
    parties the SQL sub-task discovered) fused with ss_embed(...) ranking,
    in one query -- not a plain_search followed by a Python-side filter.
    """
    id_list = ", ".join(_sql_literal(pid) for pid in party_ids)
    query_literal = _sql_literal(query_text)
    sql = (
        f"SELECT dc.chunk_id, dc.document_id, dc.content_md, dc.page, "
        f"1 - (dc.embedding <=> ss_embed({query_literal})) AS score "
        f"FROM document_chunks dc "
        f"JOIN documents d ON d.document_id = dc.document_id "
        f"WHERE d.party_id IN ({id_list}) "
        f"ORDER BY dc.embedding <=> ss_embed({query_literal}) "
        f"LIMIT {int(top_k)}"
    )
    result = await vector_hybrid_select(session, sql)
    if not result.get("ok"):
        logger.warning("Targeted doc search (hybrid_select) failed: %s", result.get("error"))
        return []
    return result["rows"]


def _to_retrievals(matches: list[dict]) -> list[dict]:
    return [
        {
            "content": m["content_md"],
            "provenance": {
                "document_id": m["document_id"], "chunk_id": m["chunk_id"],
                "page": m.get("page"), "score": float(m.get("score", 0.0)),
                "title": m.get("title"), "section": m.get("section"),
            },
        }
        for m in matches
    ]


def _extract_party_ids(rows: list[dict], columns: list[str]) -> list[str]:
    for col in ("seller_id", "provider_id"):
        if col in columns:
            ids = sorted({str(r[col]) for r in rows if r.get(col)})
            if ids:
                return ids
    return []


_GENERAL_DECOMPOSER_PROMPT = """\
You are decomposing a compound question for a business analytics assistant into two independent
sub-questions: one answerable from the structured database, and one answerable from documents
(policies, contracts, terms, procedures).

Rules:
1. Respond with ONLY a JSON object: {"sql_subtask": "<string>", "doc_subtask": "<string>"}
2. sql_subtask and doc_subtask are QUESTIONS IN ENGLISH, phrased like the original question --
   never SQL, never a table or column name. A separate step writes the SQL from sql_subtask.
3. sql_subtask asks only for structured facts or records (which rows meet some condition), and
   asks for the identifying ID or name of each record when the question concerns specific
   entities -- but NEVER for a figure that only a document states (a penalty, a policy limit, a
   contract clause). Those belong in doc_subtask only.
4. doc_subtask asks for whatever document-derived information the compound question needs.
5. Preserve entity names, IDs, dates and amounts from the original question in both sub-questions
   where relevant -- do not translate or paraphrase them away.
6. No explanation outside the JSON object.

Example. Input: "Which customers were refunded more than 500 last month, and what does our refund
policy say about large refunds?"
Output: {"sql_subtask": "Which customers received refunds totalling more than 500 last month? Include
the customer ID.", "doc_subtask": "What does the refund policy say about large refunds?"}"""


def decomposer_prompt(target_schema: str | None) -> str:
    """The ONDC-tuned decomposer prompt for the ONDC sample corpus; the general one otherwise."""
    return _DECOMPOSER_SYSTEM_PROMPT if target_schema in cfg.pipeline.ondc_prompt_schemas else _GENERAL_DECOMPOSER_PROMPT


async def _decompose(query_text: str, target_schema: str | None = None) -> tuple[str, str, int, int]:
    llm = get_agent_llm("decomposer")
    result = await llm.complete(
        system=decomposer_prompt(target_schema),
        messages=[{"role": "user", "content": query_text}],
        max_tokens=cfg.sql.max_tokens_decomposer,
    )
    try:
        cleaned = _strip_fences(result.text)
        parsed = json.loads(cleaned)
        sql_subtask = str(parsed.get("sql_subtask") or query_text)
        doc_subtask = str(parsed.get("doc_subtask") or query_text)
    except (json.JSONDecodeError, AttributeError):
        logger.warning("Decomposer returned unparseable JSON; using the original query for both sub-tasks.")
        sql_subtask = doc_subtask = query_text
    return sql_subtask, doc_subtask, result.tokens_in, result.tokens_out


async def unstructured_knowledge_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    route: str = state_dict.get("route", "docs")
    query_text: str = state_dict.get("normalized_query") or state_dict.get("raw_query", "")
    tenant_id: str = state_dict["tenant_id"]
    query_id: str = state_dict.get("query_id", "")
    session_id: str = state_dict.get("session_id", "")
    flags_snapshot: dict[str, Any] = state_dict.get("flags_snapshot", {})

    # RAG v2 Phase D: "dense" (shipped) or "hybrid" (BM25 fused with dense), and the
    # dataset the question is about, forwarded to vector.semantic_search. Phase E: the
    # scope ("all" | "dataset") and whether to abstain when nothing relevant was found.
    retrieval_mode: str = flags_snapshot.get("pipeline.retrieval_mode", "dense")
    dataset: str | None = state_dict.get("target_schema")
    doc_scope: str = flags_snapshot.get("pipeline.doc_scope", "all")
    abstain_on: bool = flags_snapshot.get("pipeline.doc_abstain", "off") == "on"
    # RAG v3 (docs/specs/rag_v3.md): cross-encoder re-ranking, and searching with a rewrite.
    rerank: str = flags_snapshot.get("pipeline.rerank", "off")
    rewrite_on: bool = flags_snapshot.get("pipeline.query_rewrite", "off") == "llm"

    start_ms = int(time.monotonic() * 1000)
    tokens_in_total = tokens_out_total = llm_calls_total = trajectory_steps = 0
    mcp_base_url = os.environ.get("MCP_URL", "http://mcp:8080")

    # The workspace travels as X-Workspace-ID, so vector.semantic_search also
    # searches the uploaded documents in the folders the workspace chose.
    async with MCPSession(tenant_id=tenant_id, base_url=mcp_base_url, session_id=session_id,
                          workspace_id=state_dict.get("workspace_id"),
                          dataset=state_dict.get("target_schema")) as session:
        if route != "docs":
            # The hybrid route no longer comes here (graph.py: hybrid_plan_node -> the SQL agent ->
            # hybrid_docs_node); anything else is answered as a documents question rather than failed.
            logger.warning("unstructured_knowledge_node called with route=%r; treated as docs", route)
        search_text = query_text
        if rewrite_on:
            search_text, usage = await rewrite_for_search(query_text)
            tokens_in_total += usage["tokens_in"]
            tokens_out_total += usage["tokens_out"]
            llm_calls_total += usage["llm_calls"]
        matches, meta = await _semantic_search_expanded(
            session, search_text, cfg.retrieval.semantic_top_k, retrieval_mode, dataset,
            doc_scope, abstain_on, rerank,
        )
        trajectory_steps += 1 if meta["abstained"] else 2  # the expanded call is a second round-trip
        elapsed_ms = int(time.monotonic() * 1000) - start_ms
        logger.info(
            "UKE docs route: query_id=%s matches=%d abstained=%s elapsed_ms=%d",
            query_id, len(matches), meta["abstained"], elapsed_ms,
        )
        update: dict[str, Any] = {
            "subtasks": [query_text],
            "retrievals": _to_retrievals(matches),
            "metrics": {
                "tokens_in": tokens_in_total, "tokens_out": tokens_out_total, "llm_calls": llm_calls_total,
                "trajectory_steps": trajectory_steps, "latency_ms": elapsed_ms,
            },
        }
        if meta["abstained"]:
            update["doc_abstained"] = True
        return update


# ── The hybrid route (SQL + documents) ───────────────────────────────────────
# graph.py runs it as three steps: hybrid_plan_node splits the question; the SQL half goes through
# sql_agent_node -- the SAME agent as a data-only question, with the workspace's approved knowledge,
# the fast path, templates and the correction loop -- and hybrid_docs_node then runs the documents
# half, narrowed to the parties the SQL result names. Until 2026-10-02 the hybrid route ran its own
# single-shot SQL here with none of those.

async def hybrid_plan_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    """Split the question into its SQL part and its documents part (subtasks = [sql, docs])."""
    query_text: str = state_dict.get("normalized_query") or state_dict.get("raw_query", "")
    flags_snapshot: dict[str, Any] = state_dict.get("flags_snapshot", {})
    start_ms = int(time.monotonic() * 1000)
    tokens_in = tokens_out = llm_calls = 0
    if bool(flags_snapshot.get("retrieval.hybrid_decomposition.enabled", True)):
        sql_subtask, doc_subtask, tokens_in, tokens_out = await _decompose(query_text, state_dict.get("target_schema"))
        llm_calls = 1
    else:
        sql_subtask = doc_subtask = query_text
    logger.info("UKE hybrid plan: query_id=%s sql_subtask=%r doc_subtask=%r",
                state_dict.get("query_id", ""), sql_subtask, doc_subtask)
    return {
        "subtasks": [sql_subtask, doc_subtask],
        "metrics": {"tokens_in": tokens_in, "tokens_out": tokens_out, "llm_calls": llm_calls,
                    "trajectory_steps": 0, "latency_ms": int(time.monotonic() * 1000) - start_ms},
    }


async def hybrid_docs_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    """The documents half, after the SQL agent: narrowed to the parties its rows name when it found
    seller/provider ids, the two-round search otherwise. Writes only retrievals and metrics."""
    query_text: str = state_dict.get("normalized_query") or state_dict.get("raw_query", "")
    subtasks: list[str] = state_dict.get("subtasks") or []
    doc_subtask = subtasks[1] if len(subtasks) > 1 else query_text
    flags_snapshot: dict[str, Any] = state_dict.get("flags_snapshot", {})
    retrieval_mode: str = flags_snapshot.get("pipeline.retrieval_mode", "dense")
    dataset: str | None = state_dict.get("target_schema")
    doc_scope: str = flags_snapshot.get("pipeline.doc_scope", "all")
    abstain_on: bool = flags_snapshot.get("pipeline.doc_abstain", "off") == "on"
    rerank: str = flags_snapshot.get("pipeline.rerank", "off")
    rows: list[dict] = state_dict.get("sql_rows") or []
    columns: list[str] = state_dict.get("sql_columns") or []
    start_ms = int(time.monotonic() * 1000)
    trajectory_steps = 0
    mcp_base_url = os.environ.get("MCP_URL", "http://mcp:8080")
    async with MCPSession(tenant_id=state_dict["tenant_id"], base_url=mcp_base_url,
                          session_id=state_dict.get("session_id", ""),
                          workspace_id=state_dict.get("workspace_id"),
                          dataset=state_dict.get("target_schema")) as session:
        party_ids = _extract_party_ids(rows, columns)
        if party_ids:
            matches = await _targeted_doc_search(session, doc_subtask, party_ids, cfg.retrieval.semantic_top_k)
            trajectory_steps += 1
        else:
            matches, meta = await _semantic_search_expanded(
                session, doc_subtask, cfg.retrieval.semantic_top_k, retrieval_mode, dataset,
                doc_scope, abstain_on, rerank,
            )
            # Nothing relevant: the doc retrievals are dropped and the answer is built from the SQL
            # result alone, with no fixed message (the SQL half may well be the whole answer).
            trajectory_steps += 1 if meta["abstained"] else 2
    elapsed_ms = int(time.monotonic() * 1000) - start_ms
    logger.info("UKE hybrid docs: query_id=%s party_ids=%s sql_rows=%d doc_matches=%d elapsed_ms=%d",
                state_dict.get("query_id", ""), party_ids, len(rows), len(matches), elapsed_ms)
    return {
        "retrievals": _to_retrievals(matches),
        "metrics": {"tokens_in": 0, "tokens_out": 0, "llm_calls": 0,
                    "trajectory_steps": trajectory_steps, "latency_ms": elapsed_ms},
    }

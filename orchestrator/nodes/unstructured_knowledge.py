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

hybrid route (D7, gated to retrieval.hybrid_decomposition.enabled -- never
fires on docs or sql, Callout 3): decomposes the compound question into a
SQL sub-task and a doc sub-task (one LLM call), runs the SQL sub-task as a
simplified single-shot generation+execution (no memory, no interrogation,
no correction loop -- those are specifically wired for the pure-sql route's
graph edges; C2 Scope frames the retry loop as a sql_agent_node concept),
then runs the doc sub-task. If the SQL sub-task's result exposes a
seller_id/provider_id column, the doc sub-task is narrowed via
vector.hybrid_select (a real ss_embed(...) usage, A4.7) to only that
party's documents -- a genuinely fused SQL+vector query, not two unrelated
lookups glued together after the fact. Both state.sql_rows/executed_sql and
state.retrievals get populated; the Synthesizer emits both citation kinds.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from classifiers.error_classifier import classify_error
from config_loader import cfg
from llm.provider import get_agent_llm
from mcp_client import (
    MCPSession, data_run_select, schema_list_tables,
    vector_hybrid_select, vector_semantic_search,
)
from schema_retriever import get_schema_retriever, target_schema

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

_SIMPLE_SQL_SYSTEM_PROMPT = """\
You are a PostgreSQL query generator for a logistics data analytics system \
(ONDC network), answering a SQL sub-question that is part of a larger \
hybrid (SQL + document) query.

Rules:
1. Write exactly one SQL SELECT statement. No semicolons at the end.
2. Qualify every column with its table alias.
3. Use only the tables and columns described in the schema below.
4. If the question concerns specific sellers, include s.seller_id (not just \
s.display_name) as a selected column, so results can be cross-referenced \
against seller-specific documents. Do the same (provider_id) for logistics \
providers. This applies EVEN to a COUNT/SUM/aggregate question -- add
   seller_id (or provider_id) as a plain SELECT column and GROUP BY it,
   rather than a bare COUNT(*)/SUM(...) with no other column. Example: for
   "how many orders has seller S001 placed?", write
   SELECT seller_id, COUNT(*) FROM orders WHERE seller_id = 'S001' GROUP BY seller_id
   -- NOT just SELECT COUNT(*) FROM orders WHERE seller_id = 'S001'. The
   downstream step that finds the right document depends on seeing the ID
   in your result columns, not just in your WHERE clause.
5. The schema has no dedicated column or table for qualitative delivery
   terms like "breached", "delayed", "late", or "on time" -- express them as
   a comparison between existing columns instead: a delivery is late when
   fulfillments.delivered_at > fulfillments.promised_at (both columns
   already exist), or use fulfillments.status ('failed'/'returned') for a
   failed delivery. Never invent a table or column name to stand in for a
   concept the schema doesn't have a dedicated field for -- find the closest
   real comparison instead.
6. Apply LIMIT {max_rows} unless the question explicitly asks for all rows.
7. Return ONLY the SQL query text -- no markdown, no explanation, no comments."""


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


async def _semantic_search_expanded(session: MCPSession, query_text: str, top_k: int) -> list[dict]:
    """
    Two-round retrieval: first an unrestricted semantic_search to find which
    DOCUMENTS are relevant, then a second call restricted to those documents'
    IDs with a generous top_k, so every chunk of an already-relevant
    document is available (not just whichever single chunk happened to
    embed closest to the question) -- see module docstring for why this
    matters for table chunks specifically.
    """
    first = await vector_semantic_search(session, query_text, top_k=top_k)
    matches = first.get("matches", [])

    # Document ranking order = order of first appearance in the score-sorted
    # first call (semantic_search already returns matches best-first).
    doc_order: list[str] = []
    seen_docs: set[str] = set()
    for m in matches:
        if m["document_id"] not in seen_docs:
            seen_docs.add(m["document_id"])
            doc_order.append(m["document_id"])
    if not doc_order:
        return []

    expanded = await vector_semantic_search(
        session, query_text, top_k=cfg.retrieval.semantic_top_k_max, document_ids=doc_order,
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
    return ordered


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


async def _decompose(query_text: str) -> tuple[str, str, int, int]:
    llm = get_agent_llm("decomposer")
    result = await llm.complete(
        system=_DECOMPOSER_SYSTEM_PROMPT,
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


async def _run_sql_subtask(
    session: MCPSession, question: str,
) -> tuple[str | None, list[dict], list[str], dict | None, int, int, int, int]:
    """
    Simplified single-shot SQL sub-task: full schema, no memory, no
    interrogation, no correction loop (hybrid's sub-questions are simpler
    than a standalone sql-route question by construction -- decomposition
    already isolated the structured-data part). On failure, classifies the
    error (A3, same classifier sql_agent_node uses) rather than silently
    returning empty rows -- a failed query and a genuinely empty result are
    different facts, and collapsing them would let the Synthesizer report a
    confident "zero results" answer for a query that never actually ran
    (verified this was happening before this fix: a hallucinated table name
    produced unknown_table, and the prior code surfaced it as "no sellers
    breached SLA" -- a fabricated answer, not an honest failure report).
    Returns (executed_sql, rows, columns, error_or_None, tokens_in,
    tokens_out, llm_calls, trajectory_steps). error_or_None matches
    ErrorRecord's shape minus 'attempt' (the caller fills that in).

    Cycle 007 B1 (audit 007 F-007-2): this sub-task used to call
    schema_list_tables/retrieve with schema=None, which resolves server-side
    to 'public' regardless of schema_retriever.target_schema -- so under
    target_schema="olist" this path silently queried the 550-row synthetic
    corpus while sql_agent_node (the non-hybrid route) correctly queried
    olist's 99,441 rows, sharing three table names (orders/order_items/
    sellers) between the two schemas so the wrong-corpus answer was
    well-formed and silent, not an error. Threading target_schema() through
    here, the same way sql_agent_node already does (schema_retriever.py's
    QUALIFY comment), closes that gap. data_run_select itself takes no
    schema parameter -- it executes whatever SQL text it is given, so no
    change is needed there; retriever.retrieve()'s qualification of the
    schema_context (e.g. "TABLE olist.orders:") is what makes the model
    write schema-qualified FROM clauses, same mechanism sql_agent.py relies
    on for the exact same reason.
    """
    trajectory_steps = 0
    _schema = target_schema()
    tables_result = await schema_list_tables(session, _schema)
    table_names = [t["name"] for t in tables_result.get("tables", [])]
    trajectory_steps += 1

    retriever = get_schema_retriever()
    schema_context, _ = await retriever.retrieve(session, table_names, _schema)
    trajectory_steps += len(table_names)

    llm = get_agent_llm("sql")
    result = await llm.complete(
        system=_SIMPLE_SQL_SYSTEM_PROMPT.format(max_rows=cfg.mcp.max_rows_default),
        messages=[{"role": "user", "content": f"Schema:\n{schema_context}\n\nQuestion: {question}"}],
        max_tokens=cfg.sql.max_tokens_sql,
    )
    candidate_sql = _strip_fences(result.text)

    exec_result = await data_run_select(session, candidate_sql, max_rows=cfg.mcp.max_rows_default)
    trajectory_steps += 1
    if exec_result.get("ok"):
        return (
            candidate_sql, exec_result["rows"], exec_result["columns"], None,
            result.tokens_in, result.tokens_out, 1, trajectory_steps,
        )

    error_obj = exec_result.get("error") or {}
    category, hint = classify_error(
        sqlstate=error_obj.get("sqlstate"), message=error_obj.get("message"), empty_result=False,
    )
    logger.warning(
        "Hybrid SQL sub-task failed (no correction loop -- single shot by design): "
        "category=%s message=%s", category, error_obj.get("message"),
    )
    error_record = {
        "category": category,
        "engine_code": error_obj.get("sqlstate"),
        "message": error_obj.get("message", "Unknown error"),
        "offending_sql": candidate_sql,
        "remediation_hint": hint,
    }
    return (
        candidate_sql, [], [], error_record,
        result.tokens_in, result.tokens_out, 1, trajectory_steps,
    )


async def unstructured_knowledge_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    route: str = state_dict.get("route", "docs")
    query_text: str = state_dict.get("normalized_query") or state_dict.get("raw_query", "")
    tenant_id: str = state_dict["tenant_id"]
    query_id: str = state_dict.get("query_id", "")
    session_id: str = state_dict.get("session_id", "")
    flags_snapshot: dict[str, Any] = state_dict.get("flags_snapshot", {})

    start_ms = int(time.monotonic() * 1000)
    tokens_in_total = tokens_out_total = llm_calls_total = trajectory_steps = 0
    mcp_base_url = os.environ.get("MCP_URL", "http://mcp:8080")

    async with MCPSession(tenant_id=tenant_id, base_url=mcp_base_url, session_id=session_id) as session:
        if route == "docs":
            matches = await _semantic_search_expanded(session, query_text, cfg.retrieval.semantic_top_k)
            trajectory_steps += 2  # the expanded call is a second round-trip
            elapsed_ms = int(time.monotonic() * 1000) - start_ms
            logger.info(
                "UKE docs route: query_id=%s matches=%d elapsed_ms=%d",
                query_id, len(matches), elapsed_ms,
            )
            return {
                "subtasks": [query_text],
                "retrievals": _to_retrievals(matches),
                "metrics": {
                    "tokens_in": 0, "tokens_out": 0, "llm_calls": 0,
                    "trajectory_steps": trajectory_steps, "latency_ms": elapsed_ms,
                },
            }

        # route == "hybrid" -- decomposition gated to this route only (D7,
        # Callout 3). Never reached from docs or sql: this node isn't even
        # registered for those, and sql_agent_node has no decomposition call.
        decomposition_enabled = bool(flags_snapshot.get("retrieval.hybrid_decomposition.enabled", True))
        if decomposition_enabled:
            sql_subtask, doc_subtask, d_ti, d_to = await _decompose(query_text)
            tokens_in_total += d_ti
            tokens_out_total += d_to
            llm_calls_total += 1
        else:
            sql_subtask = doc_subtask = query_text

        executed_sql, rows, columns, sql_error, s_ti, s_to, s_calls, s_steps = await _run_sql_subtask(session, sql_subtask)
        tokens_in_total += s_ti
        tokens_out_total += s_to
        llm_calls_total += s_calls
        trajectory_steps += s_steps

        party_ids = _extract_party_ids(rows, columns)
        if party_ids:
            doc_rows = await _targeted_doc_search(session, doc_subtask, party_ids, cfg.retrieval.semantic_top_k)
            matches = doc_rows
            trajectory_steps += 1
        else:
            matches = await _semantic_search_expanded(session, doc_subtask, cfg.retrieval.semantic_top_k)
            trajectory_steps += 2

        elapsed_ms = int(time.monotonic() * 1000) - start_ms
        logger.info(
            "UKE hybrid route: query_id=%s decomposition=%s party_ids=%s sql_ok=%s sql_rows=%d "
            "doc_matches=%d llm_calls=%d elapsed_ms=%d",
            query_id, decomposition_enabled, party_ids, sql_error is None, len(rows), len(matches),
            llm_calls_total, elapsed_ms,
        )
        update: dict[str, Any] = {
            "subtasks": [sql_subtask, doc_subtask],
            "executed_sql": executed_sql,
            "candidate_sql": executed_sql,
            "sql_rows": rows,
            "sql_columns": columns,
            "retrievals": _to_retrievals(matches),
            "metrics": {
                "tokens_in": tokens_in_total, "tokens_out": tokens_out_total,
                "llm_calls": llm_calls_total, "trajectory_steps": trajectory_steps,
                "latency_ms": elapsed_ms,
            },
        }
        if sql_error is not None:
            # attempt stays 0 (state.attempt's own default): hybrid never
            # engages the sql-route's correction loop or its conditional
            # edge, so nothing ever increments it. Appending one error with
            # attempt=0 is exactly what makes synthesizer_node's existing
            # `errors[-1] if len(errors) > attempt else None` derivation
            # detect this as a real failure -- the same typed-field
            # mechanism the sql route itself relies on, not a scratch key.
            update["errors"] = [{**sql_error, "attempt": 0}]
        return update

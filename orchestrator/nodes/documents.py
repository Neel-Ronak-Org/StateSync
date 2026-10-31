"""
orchestrator/nodes/documents.py
The documents engine: finds the passages that answer a question in the workspace's uploaded documents.

How a documents question is searched:

  1. Round 1 finds the right FILES. A dense search over every passage of the workspace's documents
     (vector.semantic_search, retrieval.semantic_top_k = 8 passages) names the documents the best
     passages come from, best first.
  2. Round 2 finds the PASSAGES inside those files. The same search, restricted to those documents, returns
     up to retrieval.semantic_top_k_max = 25 passages. Searching inside the right files first matters for
     long filings: a flat search over 44,922 passages lets near-duplicate passages from the wrong year's
     filing crowd out the right one.
  3. Jev re-ranks the passages. For each passage Jev answers one typed question, "Does this passage contain
     the information needed to answer the question?", with a probability (passage_jev.py), and the passages
     are sorted by it. In Paper B's page-finding test (300 new questions) this put the right page first for
     49.3% of questions, against 20.3% for the dense order.
  4. The answer writer reads the first 8 (retrieval.semantic_top_k): the relevance window. More passages
     cost tokens and dilute the evidence; fewer miss answers that span two passages.

Passages are embedded with mxbai-embed-large (1,024 numbers each) and compared by cosine similarity in
Postgres (pgvector), an exact scan: at tens of thousands of passages it takes about half a second.

A two-source question ("Which company had the highest revenue in 2022, and how does it describe its
e-commerce strategy?") runs here after the SQL agent (graph.py). When the router said the documents part
depends on the database part, the search is narrowed to the documents whose titles name a company in the
SQL result (match_titles); round 1 is then skipped because the titles already named the files.

If Jev fails (or config/flags.toml [pipeline] jev is "off"), the passages keep the dense order: the search
never fails because of Jev.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import context_carry
import passage_jev
from config_loader import cfg
from llm.provider import get_agent_llm
from mcp_client import MCPSession, library_manage, vector_semantic_search

logger = logging.getLogger(__name__)


def _session(state_dict: dict[str, Any]) -> MCPSession:
    """The MCP session for a search. The workspace travels as X-Workspace-ID, so the search covers the
    document folders that workspace chose."""
    return MCPSession(tenant_id=state_dict["tenant_id"], base_url=os.environ.get("MCP_URL", "http://mcp:8080"),
                      session_id=state_dict.get("session_id", ""), workspace_id=state_dict.get("workspace_id"),
                      dataset=state_dict.get("target_schema"))


def dense_order(chunks: list[dict], window: int) -> list[dict]:
    """The search's own order: the first `window` passages by score are grouped by document (documents by
    their best passage, passages in reading order, so a table stays next to its text); the rest follow by
    score. Ties go to the lower chunk_id."""
    ranked = sorted(chunks, key=lambda c: (-float(c.get("score") or 0.0), c["chunk_id"]))
    head, tail = ranked[:max(window, 0)], ranked[max(window, 0):]
    grouped: list[dict] = []
    for doc_id in dict.fromkeys(c["document_id"] for c in head):
        grouped.extend(sorted((c for c in head if c["document_id"] == doc_id), key=lambda c: c["chunk_id"]))
    return grouped + tail


async def _within_documents(session: MCPSession, text: str, document_ids: list[str], first_round: list[dict]) -> list[dict]:
    """Round 2: the best passages inside the given documents, merged with round 1's own passages."""
    found = await vector_semantic_search(session, text, top_k=int(cfg.retrieval.semantic_top_k_max),
                                         document_ids=document_ids)
    by_chunk = {m["chunk_id"]: m for m in found.get("matches", [])}
    for m in first_round:
        by_chunk[m["chunk_id"]] = m
    return dense_order(list(by_chunk.values()), int(cfg.retrieval.semantic_top_k))


async def two_round_search(session: MCPSession, text: str) -> tuple[list[dict], list[dict]]:
    """(the passages in the search's order, round 1's passages). Round 1 finds the files, round 2 the
    passages inside them (see the module docstring)."""
    first = (await vector_semantic_search(session, text, top_k=int(cfg.retrieval.semantic_top_k))).get("matches", [])
    document_ids = list(dict.fromkeys(m["document_id"] for m in first))
    if not document_ids:
        return [], first
    return await _within_documents(session, text, document_ids, first), first


def _stage_row(m: dict) -> dict[str, Any]:
    """One passage as the Pipeline Inspector shows it: where it is from and its scores (no text)."""
    return {"chunk_id": m["chunk_id"], "document_id": m["document_id"], "title": m.get("title"), "page": m.get("page"),
            "score": float(m.get("score") or 0.0),
            "jev_score": None if m.get("jev_score") is None else float(m["jev_score"])}


def retrieval_trace(search_text: str, round1: list[dict], searched: list[dict], final: list[dict],
                    timings: dict[str, int], jev: dict | None = None, narrowed_to: list[str] | None = None) -> dict[str, Any]:
    """The search stage by stage, for the Pipeline Inspector. `searched` is the search's order, `final` the
    order the writer reads; each passage carries its place in both, so the page can show how Jev moved it."""
    searched_rank = {m["chunk_id"]: i + 1 for i, m in enumerate(searched)}
    return {
        "search_text": search_text,
        "settings": {"window": int(cfg.retrieval.semantic_top_k), "candidates": int(cfg.retrieval.semantic_top_k_max)},
        "round1": [_stage_row(m) for m in round1],
        "narrowed_to": narrowed_to or [],
        "passages": [{**_stage_row(m), "rank_searched": searched_rank.get(m["chunk_id"]), "rank_final": i + 1}
                     for i, m in enumerate(final)],
        "jev": jev,
        "ms": timings,
    }


def _to_retrievals(matches: list[dict]) -> list[dict]:
    return [{"content": m["content_md"],
             "provenance": {"document_id": m["document_id"], "chunk_id": m["chunk_id"], "page": m.get("page"),
                            "score": float(m.get("score", 0.0)), "title": m.get("title"), "section": m.get("section"),
                            **({"jev_score": float(m["jev_score"])} if m.get("jev_score") is not None else {})}}
            for m in matches]


async def _search_and_rank(state_dict: dict[str, Any], text: str, question: str,
                           narrowed_to: list[dict] | None = None) -> dict[str, Any]:
    """Search (two rounds, or round 2 only inside `narrowed_to`), re-rank with Jev, and build the update."""
    start = int(time.monotonic() * 1000)
    async with _session(state_dict) as session:
        if narrowed_to:
            searched, round1 = await _within_documents(session, text, [d["document_id"] for d in narrowed_to], []), []
        else:
            searched, round1 = await two_round_search(session, text)
    search_ms = int(time.monotonic() * 1000) - start
    if str((state_dict.get("flags_snapshot") or {}).get("pipeline.jev", "on")) == "on":
        final, jev = await passage_jev.jev_order(question, searched)
    else:
        final, jev = searched, {"scored": 0, "failed": 0, "ms": 0}
    logger.info("documents: query_id=%s passages=%d jev scored=%d failed=%d search_ms=%d jev_ms=%d",
                state_dict.get("query_id", ""), len(final), jev["scored"], jev["failed"], search_ms, jev["ms"])
    return {
        "retrievals": _to_retrievals(final),
        "retrieval_trace": retrieval_trace(text, round1, searched, final, {"search": search_ms, "jev": jev["ms"]}, jev,
                                           [d["title"] for d in narrowed_to or []]),
        "metrics": {"tokens_in": 0, "tokens_out": 0, "llm_calls": 0, "trajectory_steps": 1 if narrowed_to else 2,
                    "latency_ms": int(time.monotonic() * 1000) - start},
    }


async def documents_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    """The documents route. The search text is the question plus, for a follow-up, the company and year
    carried from the previous turn (context_carry.py)."""
    question: str = state_dict.get("normalized_query") or state_dict.get("raw_query", "")
    update = await _search_and_rank(state_dict, question + context_carry.search_suffix(state_dict, question), question)
    update["subtasks"] = [question]
    return update


# ── The two-source route ─────────────────────────────────────────────────────
# graph.py runs it in three steps: split_question_node splits the question; the SQL agent answers the
# database part with its retry loop; documents_after_sql_node searches for the documents part.

_SPLIT_PROMPT = """\
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

_ASK_FOR_NAMES = "Return each entity's full name as well as any code or identifier."


async def _split(question: str) -> tuple[str, str, int, int]:
    """(database part, documents part, tokens in, tokens out). An unreadable reply uses the whole question
    for both parts."""
    result = await get_agent_llm("decomposer").complete(
        system=_SPLIT_PROMPT, messages=[{"role": "user", "content": question}], max_tokens=cfg.sql.max_tokens_decomposer)
    text = result.text.strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.split("\n") if not line.startswith("```")).strip()
    try:
        parsed = json.loads(text)
        return (str(parsed.get("sql_subtask") or question), str(parsed.get("doc_subtask") or question),
                result.tokens_in, result.tokens_out)
    except (json.JSONDecodeError, AttributeError):
        logger.warning("split: unreadable reply; using the whole question for both parts")
        return question, question, result.tokens_in, result.tokens_out


async def split_question_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    """Split a two-source question into its database part and its documents part (subtasks). The LLM
    router's fallback may already have split it; otherwise one short model call does. When the documents
    part depends on the database's answer, the database part also asks for names, so the documents can be
    narrowed to them."""
    question: str = state_dict.get("normalized_query") or state_dict.get("raw_query", "")
    start = int(time.monotonic() * 1000)
    tokens_in = tokens_out = calls = 0
    given: list[str] = state_dict.get("subtasks") or []
    if len(given) == 2:  # noqa: literals - a split is exactly [database part, documents part]
        sql_part, doc_part = given
    else:
        sql_part, doc_part, tokens_in, tokens_out = await _split(question)
        calls = 1
    if state_dict.get("docs_depend_on_sql") and not sql_part.endswith(_ASK_FOR_NAMES):
        sql_part = f"{sql_part} {_ASK_FOR_NAMES}"
    logger.info("split: query_id=%s sql=%r docs=%r", state_dict.get("query_id", ""), sql_part, doc_part)
    return {"subtasks": [sql_part, doc_part],
            "metrics": {"tokens_in": tokens_in, "tokens_out": tokens_out, "llm_calls": calls,
                        "trajectory_steps": 0, "latency_ms": int(time.monotonic() * 1000) - start}}


# ── Narrowing the documents part to the companies in the SQL result ─────────
# Filings are titled "<COMPANY> <YEAR> <FORM>" ("COCACOLA 2022 10K"), so a company name in the SQL result
# can be matched to its filings by title. With documents titled any other way nothing matches, and the
# search covers all the workspace's documents.

_YEAR_DIGITS = 4  # noqa: literals - a year word is four digits ("2022") or starts with four ("2023Q2")


def _letters_digits(text: str) -> str:
    """Lower-cased, with everything but letters and digits removed ("Coca-Cola" and "COCACOLA" agree)."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def title_words(title: str) -> str:
    """A title's words before the first year word ("JOHNSON JOHNSON 2023 8K" -> "JOHNSON JOHNSON"); all of
    them when there is no year word."""
    name: list[str] = []
    for word in title.split():
        if len(word) >= _YEAR_DIGITS and word[:_YEAR_DIGITS].isdigit():
            break
        name.append(word)
    return " ".join(name)


def title_name(title: str) -> str:
    """title_words reduced to lower-case letters and digits ("COCACOLA 2022 10K" -> "cocacola")."""
    return _letters_digits(title_words(title))


_SUPERLATIVE = re.compile(r"\b(highest|lowest|most|least|largest|smallest|biggest|best|worst)\b", re.IGNORECASE)


def answer_entities(rows: list[dict], columns: list[str], question: str, entity_rows: int, max_entities: int) -> list[str]:
    """The names in the SQL result to narrow by: the text values of its first `entity_rows` rows (at most
    `max_entities`). A question that asks for the single top or bottom one ("Which company had the highest
    revenue...") uses the first row only, so the search does not also read the filings of the runners-up."""
    names = columns or (list(rows[0]) if rows else [])
    single = bool(_SUPERLATIVE.search(question))
    entities: list[str] = []
    for row in rows[:1 if single else max(entity_rows, 0)]:
        for name in names:
            value = row.get(name)
            if isinstance(value, str) and value.strip() and value.strip() not in entities:
                entities.append(value.strip())
                if len(entities) >= max_entities:
                    return entities
    return entities


def match_titles(entities: list[str], documents: list[dict], title_name_min_chars: int) -> list[dict]:
    """The documents an entity names, each once. Both sides are reduced to letters and digits; an entity
    names a document when it occurs in the title, or the title's name occurs in the entity and is at least
    `title_name_min_chars` long or equal to it ("JPMORGAN CHASE & CO" names "JPMORGAN 2022 10K")."""
    keys = [k for k in (_letters_digits(e) for e in entities) if k]
    matched: list[dict] = []
    for d in documents:
        title = str(d.get("title") or "")
        reduced, name = _letters_digits(title), title_name(title)
        if any(k in reduced or (name and name in k and (len(name) >= title_name_min_chars or name == k)) for k in keys):
            matched.append(d)
    return matched


async def workspace_documents(state_dict: dict[str, Any]) -> list[dict]:
    """The workspace's ready documents as {document_id, title}; [] without a workspace or on failure."""
    workspace_id = state_dict.get("workspace_id")
    if not workspace_id:
        return []
    try:
        async with _session(state_dict) as session:
            result = await library_manage(session, principal_id=state_dict.get("principal_id", ""),
                                          action="workspace_documents", workspace_id=workspace_id,
                                          limit=int(cfg.library.max_documents))
    except Exception as exc:  # noqa: BLE001 -- narrowing is a help, never a reason to fail the question
        logger.warning("documents of workspace %s unavailable: %s", workspace_id, exc)
        return []
    return list(result.get("documents", [])) if result.get("ok") else []


async def documents_after_sql_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    """The documents part of a two-source question, after the SQL agent. Narrowed to the documents that
    name a company in the SQL result when the router said the documents depend on the database part;
    otherwise the two-round search."""
    question: str = state_dict.get("normalized_query") or state_dict.get("raw_query", "")
    subtasks: list[str] = state_dict.get("subtasks") or []
    doc_part = subtasks[1] if len(subtasks) > 1 else question
    sql_part = subtasks[0] if subtasks else question
    narrowed: list[dict] = []
    entity_trace: dict[str, Any] | None = None
    if state_dict.get("docs_depend_on_sql"):
        entities = answer_entities(state_dict.get("sql_rows") or [], state_dict.get("sql_columns") or [], sql_part,
                                   int(cfg.hybrid.entity_rows), int(cfg.hybrid.max_entities))
        documents = await workspace_documents(state_dict) if entities else []
        narrowed = match_titles(entities, documents, int(cfg.hybrid.title_name_min_chars))
        entity_trace = {"entities": entities, "matched_documents": [d["title"] for d in narrowed], "narrowed": bool(narrowed)}
    text = doc_part + context_carry.search_suffix(state_dict, doc_part)
    update = await _search_and_rank(state_dict, text, doc_part, narrowed)
    if entity_trace is not None:
        update["hybrid_entities"] = entity_trace
    return update

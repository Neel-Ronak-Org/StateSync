"""
orchestrator/nodes/sql_agent.py
The SQL agent: turns a question into one read-only SQL query and runs it.

One attempt, as this node runs it:

  attempt 0 (the first try)
    1. Read the database's tables (schema.list_tables, schema.describe_table) and the workspace's
       approved knowledge: the owner's business description, approved table and column notes,
       definitions, and similar approved examples (workspace_context.py).
    2. Fast path: if the question exactly matches an approved example, run its SQL as is. The answer
       is labelled "verified".
    3. Otherwise draft a query, read which tables the draft uses, and sample a few rows of those
       tables, so the model sees how values are really stored (for example "FY2022" against 2022).
    4. Write the final query with the schema, the knowledge and the samples.

  attempt 1, 2, ... (a retry after a failure)
    The full schema again, plus every earlier failed query with its classified error and a fixed
    hint (classifiers/error_classifier.py), and the model writes a corrected query.

  Every query then runs through the MCP's three read-only layers (a parser that allows one SELECT
  only, a read-only transaction with a time limit, and a database login that can only read). This
  node never connects to a database itself.

The retry loop is not here: graph.py has an edge from this node back to itself, taken while the last
error is retriable and fewer than sql.retry_max attempts have run. Each failed attempt appends exactly
one ErrorRecord, so len(errors) is the number of the attempt that is running.

A first empty result is treated as a retriable failure (the filter may be wrong); a second empty
result in a row is accepted as the answer ("no records match").

A query that needed a retry gets a caution for the reader: in our study of 393 BIRD questions (Paper A),
"the agent had to retry" was the best warning sign of a wrong answer that costs nothing extra
(79% of flagged answers were wrong).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx
import pglast
from pglast.visitors import Visitor

import context_carry
from classifiers.error_classifier import classify_error
from config_loader import cfg
from llm.provider import get_agent_llm
from mcp_client import MCPSession, data_run_select, data_sample_rows, schema_list_tables, workspaces_manage
from schema_retriever import describe_schema
from workspace_context import format_context, system_suffix

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a PostgreSQL query generator for a business analytics system over the owner's own PostgreSQL database.
You will be given context describing the relevant tables. Write a single valid SELECT query.

Rules:
1. Write exactly one SQL SELECT statement. No semicolons at the end.
2. Qualify every column with its table alias.
3. Use only the tables and columns described in the context below.
4. Apply LIMIT {max_rows} unless the question explicitly asks for all rows.
5. Use TIMESTAMPTZ-aware comparisons (cast to timestamptz, never compare with integers).
6. Return ONLY the columns the question actually asks for. If it asks
   "which sellers", return the seller identifier (plus a name if the answer
   would be unreadable without it) -- not every column of the table. Selecting
   extra columns "for context" makes the result a different answer.
7. Return ONLY the SQL query text — no markdown, no explanation, no comments."""

_CORRECTION_SYSTEM_PROMPT = """\
You are a PostgreSQL query generator correcting a previously failed query for a
business analytics system over the owner's own PostgreSQL database. You are given every prior failed
attempt with its classified error and a fixed remediation hint, then the full
schema. Write ONE corrected SELECT query that fixes the specific problem the
error history describes.

If the error says a column does not exist on a table: that table genuinely does
not have that column. Look at the FOREIGN KEYS listed under that table in the
schema below to find which OTHER table actually has the data you need, and add
a JOIN through that foreign key -- do not just add unrelated conditions
(e.g. an extra WHERE/IS NOT NULL clause) and resubmit the same wrong reference.

Rules:
1. Write exactly one SQL SELECT statement. No semicolons at the end.
2. Qualify every column with its table alias.
3. Use only the tables and columns described in the schema below.
4. Apply LIMIT {max_rows} unless the question explicitly asks for all rows.
5. Use TIMESTAMPTZ-aware comparisons (cast to timestamptz, never compare with integers).
6. Return ONLY the columns the question actually asks for. If it asks
   "which sellers", return the seller identifier (plus a name if the answer
   would be unreadable without it) -- not every column of the table. Selecting
   extra columns "for context" makes the result a different answer.
7. Directly address every remediation hint below — do not repeat a mistake a hint
   already explained.
8. Return ONLY the SQL query text — no markdown, no explanation, no comments."""

RETRY_CAUTION = ("The first query for this answer failed and had to be corrected, so check the figures: "
                 "answers that needed a correction are wrong more often than others.")


def _strip_sql_fences(text: str) -> str:
    """Strip Markdown code fences the model sometimes wraps its SQL in."""
    if text.startswith("```"):
        return "\n".join(line for line in text.split("\n") if not line.startswith("```")).strip()
    return text


def _parse_referenced_tables(sql: str) -> list[str]:
    """The tables a SQL statement reads, in first-seen order, found with the same PostgreSQL parser the
    MCP's safety gate uses (pglast). Subqueries and CTE bodies count too. [] when the SQL does not parse;
    a CTE name may appear, but the caller keeps only names that are real tables."""
    if not sql.strip():
        return []
    try:
        tree = pglast.parse_sql(sql)
    except Exception as exc:  # pglast.Error and anything it wraps
        logger.debug("Could not parse draft SQL for sampling: %s", exc)
        return []
    found: list[str] = []

    class _RangeVarCollector(Visitor):
        def visit_RangeVar(self, ancestors, node):  # noqa: N802 — pglast visitor naming
            name = getattr(node, "relname", None)
            if name and name not in found:
                found.append(name)

    _RangeVarCollector()(tree)
    return found


async def _sample_or_skip(session: Any, target: dict, schema: str, query_id: str) -> dict | None:
    """A few rows of the target's columns, or None when the MCP refuses: samples only help the model,
    so a refused sample never fails the question."""
    try:
        return await data_sample_rows(session, target["table"], columns=target["columns"], schema=schema)
    except httpx.HTTPStatusError as exc:
        logger.warning("SQL Agent: sample of %s refused (HTTP %s) for query_id=%s; skipped",
                       target["table"], exc.response.status_code, query_id)
        return None


def question_for(state_dict: dict[str, Any]) -> str:
    """The question this agent answers. On the two-source route it is only the database part of the
    question (subtasks[0]); otherwise the whole question."""
    if state_dict.get("route") == "hybrid" and state_dict.get("subtasks"):
        return str(state_dict["subtasks"][0])
    return state_dict.get("normalized_query") or state_dict.get("raw_query", "")


def _pick_sample_targets(table_descriptions: list[dict], cap: int,
                         referenced_tables: list[str] | None = None) -> list[dict]:
    """Which tables to sample (at most `cap`): the ones the draft query reads. When none of them can be
    found, the first tables that have a nullable column. Within a table the nullable columns are sampled
    (their stored shape is the one in doubt); a table with none is sampled whole (columns None).
    Each target: {table, columns (sent to the MCP), columns_display (always concrete)}."""
    by_name = {desc["table"]: desc for desc in table_descriptions}

    def _target_for(desc: dict) -> dict:
        all_cols = [c["name"] for c in desc.get("columns", [])]
        nullable_cols = [c["name"] for c in desc.get("columns", []) if c.get("nullable")]
        return {"table": desc["table"], "columns": nullable_cols or None, "columns_display": nullable_cols or all_cols}

    targets: list[dict] = []
    for name in referenced_tables or []:
        desc = by_name.get(name)
        if desc is not None:
            targets.append(_target_for(desc))
            if len(targets) >= cap:
                break
    if targets:
        return targets
    for desc in table_descriptions:
        if any(c.get("nullable") for c in desc.get("columns", [])):
            targets.append(_target_for(desc))
            if len(targets) >= cap:
                break
    return targets


def _format_samples(samples: list[dict]) -> str:
    parts = ["Sampled data (verify actual stored shape/format before writing filters):"]
    for s in samples:
        parts.append(f"  {s['table']} ({', '.join(s['columns_sampled'])}): {json.dumps(s['rows'], default=str)}")
    return "\n".join(parts)


def _format_error_history(errors: list[dict]) -> str:
    parts = ["Previous attempt(s) failed. Do not repeat the same mistake:"]
    for e in errors:
        parts.append(
            f"  Attempt {e['attempt']}: SQL: {e.get('offending_sql') or '(none)'}\n"
            f"    Error ({e['category']}, engine_code={e.get('engine_code') or 'n/a'}): {e['message']}\n"
            f"    Hint: {e['remediation_hint']}"
        )
    return "\n".join(parts)


async def _workspace_knowledge(session: MCPSession, state_dict: dict[str, Any], question: str) -> dict[str, Any]:
    """The workspace's approved knowledge for this question ({} without a workspace or on failure)."""
    workspace_id = state_dict.get("workspace_id")
    if not workspace_id:
        return {}
    ctx = await workspaces_manage(session, principal_id=state_dict["principal_id"], action="context",
                                  workspace_id=workspace_id, question=question)
    if not ctx.get("ok"):
        logger.warning("SQL Agent: workspace knowledge unavailable for query_id=%s: %s",
                       state_dict.get("query_id", ""), ctx.get("message"))
        return {}
    return ctx


async def sql_agent_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    """One attempt of the SQL agent (see the module docstring). Returns the state update: the query,
    its rows on success, or one classified ErrorRecord on failure. graph.py decides whether to retry."""
    question = question_for(state_dict)
    context_line = context_carry.question_context(state_dict)   # "" unless the question is a follow-up
    query_id: str = state_dict.get("query_id", "")
    existing_errors: list[dict] = state_dict.get("errors", [])
    attempt = len(existing_errors)
    max_rows = int(cfg.mcp.max_rows_default)
    logger.info("SQL Agent running for query_id=%s attempt=%d", query_id, attempt)

    start_ms = int(time.monotonic() * 1000)
    tokens_in = tokens_out = llm_calls = steps = 0

    async def complete(system: str, user: str) -> str:
        nonlocal tokens_in, tokens_out, llm_calls
        result = await get_agent_llm("sql").complete(
            system=system, messages=[{"role": "user", "content": user}], max_tokens=cfg.sql.max_tokens_sql)
        tokens_in += result.tokens_in
        tokens_out += result.tokens_out
        llm_calls += 1
        return _strip_sql_fences(result.text)

    async with MCPSession(tenant_id=state_dict["tenant_id"], base_url=os.environ.get("MCP_URL", "http://mcp:8080"),
                          session_id=state_dict.get("session_id", ""),
                          dataset=state_dict.get("target_schema")) as session:
        schema = state_dict["target_schema"]
        listing = await schema_list_tables(session, schema)
        table_names = [t["name"] for t in listing.get("tables", [])]
        steps += 1

        knowledge = await _workspace_knowledge(session, state_dict, question)
        knowledge_block = format_context(knowledge, None, int(cfg.workspaces.context_max_chars)) if knowledge else ""
        owner_words = system_suffix(knowledge.get("business_context")) if knowledge else ""
        exact_sql = (knowledge.get("exact") or {}).get("sql") if attempt == 0 else None

        samples: list[dict] = []
        verified_example = False
        if exact_sql:
            # Step 2, the fast path: an approved example, run as is.
            candidate_sql = _strip_sql_fences(exact_sql)
            verified_example = True
            logger.info("SQL Agent fast path for query_id=%s: approved example", query_id)
        else:
            schema_text, table_descriptions = await describe_schema(session, table_names, schema)
            steps += len(table_names)
            context_block = f"Schema:\n{schema_text}"
            if knowledge_block:
                context_block += "\n\n" + knowledge_block
            if attempt == 0:
                # Step 3: draft, then sample the tables the draft reads.
                system = _SYSTEM_PROMPT.format(max_rows=max_rows) + owner_words
                user = (f"{context_block}\n\nQuestion: {question}{context_line}\n\n"
                        f"Write a single PostgreSQL SELECT query to answer this question.")
                draft = await complete(system, user)
                for target in _pick_sample_targets(table_descriptions, int(cfg.sql.interrogation_max_samples),
                                                   _parse_referenced_tables(draft)):
                    sample = await _sample_or_skip(session, target, schema, query_id)
                    if sample is not None:
                        samples.append({"table": target["table"], "columns_sampled": target["columns_display"],
                                        "rows": sample.get("rows", [])})
                        steps += 1
                if samples:
                    context_block += "\n\n" + _format_samples(samples)
                # Step 4: the final query.
                user = (f"{context_block}\n\nQuestion: {question}{context_line}\n\n"
                        f"Write a single PostgreSQL SELECT query to answer this question.")
                candidate_sql = await complete(system, user)
            else:
                # A retry: the error history first (the fix matters most), then the full schema.
                system = _CORRECTION_SYSTEM_PROMPT.format(max_rows=max_rows) + owner_words
                user = (f"Question: {question}{context_line}\n\n{_format_error_history(existing_errors)}\n\n"
                        f"Full schema (find the correct column/table/join path here):\n{context_block}\n\n"
                        f"Write a single corrected PostgreSQL SELECT query.")
                candidate_sql = await complete(system, user)

        logger.info("SQL Agent SQL for query_id=%s attempt=%d: %.200s", query_id, attempt, candidate_sql)
        result = await data_run_select(session, candidate_sql, max_rows=max_rows)

    update: dict[str, Any] = {"candidate_sql": candidate_sql, "attempt": attempt, "verified_example": verified_example}
    if samples:
        update["interrogation_samples"] = samples

    ok = bool(result.get("ok"))
    rows = result.get("rows", []) if ok else []
    last_error = existing_errors[-1] if existing_errors else None
    second_empty = ok and not rows and last_error is not None and last_error.get("category") == "empty_result"

    if ok and (rows or second_empty):
        update.update(executed_sql=candidate_sql, sql_rows=rows, sql_columns=result.get("columns", []))
        if attempt > 0 and rows:
            update["caveats"] = [RETRY_CAUTION]
        logger.info("SQL Agent success for query_id=%s attempt=%d: %d rows", query_id, attempt, len(rows))
    elif ok:
        # A first empty result: retriable once (the filter may be wrong).
        category, hint = classify_error(sqlstate=None, message=None, empty_result=True)
        update.update(executed_sql=candidate_sql, sql_rows=[], sql_columns=result.get("columns", []))
        update["errors"] = [{"attempt": attempt, "category": category, "engine_code": None,
                             "message": "Query executed successfully but returned zero rows.",
                             "offending_sql": candidate_sql, "remediation_hint": hint}]
        logger.warning("SQL Agent empty result for query_id=%s attempt=%d", query_id, attempt)
    else:
        error = result.get("error") or {}
        sqlstate, message = error.get("sqlstate"), error.get("message", "Unknown error")
        category, hint = classify_error(sqlstate=sqlstate, message=message, empty_result=False)
        logger.warning("SQL Agent failure for query_id=%s attempt=%d: category=%s sqlstate=%s message=%s",
                       query_id, attempt, category, sqlstate, message)
        # The statement did not run, so it is not executed_sql; it stays visible as candidate_sql.
        update.update(sql_rows=[], sql_columns=[])
        update["errors"] = [{"attempt": attempt, "category": category, "engine_code": sqlstate,
                             "message": message, "offending_sql": candidate_sql, "remediation_hint": hint}]

    update["metrics"] = {"tokens_in": tokens_in, "tokens_out": tokens_out, "llm_calls": llm_calls,
                         "trajectory_steps": steps, "latency_ms": int(time.monotonic() * 1000) - start_ms}
    return update

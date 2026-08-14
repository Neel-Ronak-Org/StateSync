"""
orchestrator/nodes/sql_agent.py
SQL Agent — Phase B + C1 + C2.

Per B2 spec (still true on a memory miss, or with memory disabled):
  "The SQL Agent in B is: read schema via MCP -> emit SQL -> execute via MCP ->
   on failure, return the raw error to the user with no retry."
That "no retry" contract now only describes what happens on the FINAL
attempt of a query (terminal category, or sql.retry_max exhausted) -- the
correction loop itself lives in the graph (orchestrator/graph.py), as an
explicit edge back to this same node, not as a loop inside this function
(C2 spec: "not a try/except retry loop or a while loop inside a function").

Attempt tracking: `attempt_on_entry = len(state.errors)`. This node never
maintains its own counter -- the number of ErrorRecords already accumulated
*is* the current attempt index, by construction (every failing invocation
appends exactly one ErrorRecord before returning; every invocation, on
return, reports `attempt = attempt_on_entry` -- "the attempt index that
just ran," not "the next one to try"). See docs/phase_c2_log.md for the
worked trace this gives against C2-V1/V4's pass conditions.

Per C1 spec: memory is consulted, and interrogation runs, at attempt 0 only.
On any retry (attempt >= 1): memory is NOT re-consulted (C2 Notes -- doing so
is exactly the attribution problem the C1/C2 split exists to prevent), no
interrogation, and the full schema is read fresh via SchemaRetriever
regardless of whether attempt 0 was a memory hit or miss -- a hit whose SQL
failed means the compact insight was insufficient, so retries fall back to
the most complete context available, plus the full accumulated error
history and remediation hints (C2 Scope).

Trajectory write-back: any successful attempt writes a trajectory EXCEPT a
clean attempt-0 memory hit (C1 GAP-1: a hit reusing its own insight has
nothing new to record). This now includes corrected (attempt >= 1)
successes (C2 Scope) -- the insight for those describes the corrected
navigation, including the dialect note that caused the original failure,
so the loop's cost is paid once and amortized (C2 Notes).

empty_result handling (A3): the first empty_result is a classified,
retriable failure (a new ErrorRecord is appended, one retry is allowed). A
SECOND empty_result immediately following it is accepted as the answer --
treated as an ordinary success, no further ErrorRecord, no further retry.

memory.enabled is read from flags_snapshot (A2: captured once at query
entry, never re-read mid-query) — not from the live flags singleton.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from config_loader import cfg
from classifiers.error_classifier import classify_error
from llm.provider import get_agent_llm
from mcp_client import MCPSession, data_run_select, data_sample_rows, schema_list_tables
from memory_store import get_trajectory_store
from schema_retriever import get_schema_retriever

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a PostgreSQL query generator for a logistics data analytics system (ONDC network).
You will be given context describing the relevant tables — either full column/type detail,
or a compact insight from a prior similar question. Write a single valid SELECT query.

Rules:
1. Write exactly one SQL SELECT statement. No semicolons at the end.
2. Qualify every column with its table alias.
3. Use only the tables and columns described in the context below.
4. Apply LIMIT {max_rows} unless the question explicitly asks for all rows.
5. Use TIMESTAMPTZ-aware comparisons (cast to timestamptz, never compare with integers).
6. Return ONLY the SQL query text — no markdown, no explanation, no comments."""

_CORRECTION_SYSTEM_PROMPT = """\
You are a PostgreSQL query generator correcting a previously failed query for a
logistics data analytics system (ONDC network). You are given every prior failed
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
6. Directly address every remediation hint below — do not repeat a mistake a hint
   already explained.
7. Return ONLY the SQL query text — no markdown, no explanation, no comments."""

_SUMMARIZER_SYSTEM_PROMPT = """\
You are documenting how a PostgreSQL query was constructed, so the navigation
strategy can be reused for future similar-but-different questions against the
same schema. You will not see this exact question again verbatim. If a prior
attempt failed before this one succeeded, describe the dialect note that
caused that failure so future attempts avoid it.

Rules:
1. Respond with ONLY a JSON object:
   {{"insight": "<string>", "tables": ["..."], "join_path": ["..."], "dialect_notes": ["..."]}}
2. "insight" is prose (max {insight_max_chars} characters) describing which
   tables to use, key columns, and any non-obvious PostgreSQL detail
   (nullability, casing, units). Write it so a future query-writer with no
   other context could use it. It must NOT contain a SQL statement and must
   NOT start with SELECT or WITH.
3. "tables" lists only the tables actually referenced in the final query's
   FROM/JOIN clauses -- not every table you were shown.
4. "join_path" lists each join as "table.column -> table.column".
5. "dialect_notes" lists non-obvious PostgreSQL-specific details worth
   remembering -- including, if a prior attempt failed, what was wrong and
   how this query avoids it (empty list only if there is truly nothing to note).
6. No explanation outside the JSON object."""


def _format_memory_context(
    insight: str, tables: list[str], join_path: list[str], dialect_notes: list[str],
) -> str:
    parts = [f"Prior navigation insight for a similar question:\n{insight}"]
    if tables:
        parts.append(f"Tables involved: {', '.join(tables)}")
    if join_path:
        parts.append("Join path:\n" + "\n".join(f"  {jp}" for jp in join_path))
    if dialect_notes:
        parts.append("Dialect notes:\n" + "\n".join(f"  - {n}" for n in dialect_notes))
    return "\n\n".join(parts)


def _pick_interrogation_targets(table_descriptions: list[dict], cap: int) -> list[dict]:
    """
    Deterministic interrogation-target selection (C2 Scope): tables with at
    least one nullable column, in schema order, capped at `cap` tables --
    one data.sample_rows call per selected table (A4.3's own cap is on rows
    per call, not calls; the "up to interrogation_max_samples" bound in the
    spec is read here as a bound on the number of sample_rows calls).

    This is schema-wide rather than parsed from a specific planned join/WHERE
    clause (the spec names that as the ideal target but does not describe a
    mechanism for identifying it ahead of generating SQL) -- see
    docs/spec-gaps.md for this simplification, recorded rather than silently
    assumed.
    """
    targets: list[dict] = []
    for desc in table_descriptions:
        nullable_cols = [c["name"] for c in desc.get("columns", []) if c.get("nullable")]
        if not nullable_cols:
            continue
        targets.append({"table": desc["table"], "columns": nullable_cols})
        if len(targets) >= cap:
            break
    return targets


def _format_interrogation_samples(samples: list[dict]) -> str:
    parts = ["Sampled data (verify actual stored shape/format before writing filters):"]
    for s in samples:
        preview = json.dumps(s["rows"], default=str)
        parts.append(f"  {s['table']} ({', '.join(s['columns_sampled'])}): {preview}")
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


async def sql_agent_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    """
    SQL Agent node. Attempt 0: consults memory, interrogates (miss path
    only), generates SQL, executes. Attempt >= 1: full schema + accumulated
    error history, no memory, no interrogation. Classifies any failure via
    A3 and appends an ErrorRecord; the graph edge (not this function)
    decides whether to loop back.
    """
    raw_query: str = state_dict.get("normalized_query") or state_dict.get("raw_query", "")
    tenant_id: str = state_dict["tenant_id"]
    query_id: str = state_dict.get("query_id", "")
    flags_snapshot: dict[str, Any] = state_dict.get("flags_snapshot", {})
    memory_enabled: bool = bool(flags_snapshot.get("memory.enabled", True))

    existing_errors: list[dict] = state_dict.get("errors", [])
    attempt: int = len(existing_errors)

    logger.info(
        "SQL Agent running for query_id=%s attempt=%d memory_enabled=%s",
        query_id, attempt, memory_enabled,
    )

    start_ms = int(time.monotonic() * 1000)
    tokens_in_total = 0
    tokens_out_total = 0
    llm_calls_total = 0
    trajectory_steps = 0

    mcp_base_url = os.environ.get("MCP_URL", "http://mcp:8080")

    async with MCPSession(tenant_id=tenant_id, base_url=mcp_base_url) as session:
        # Always needed: fingerprint (memory key on attempt 0) and table list.
        tables_result = await schema_list_tables(session)
        schema_fingerprint: str = tables_result.get("fingerprint", "")
        table_names = [t["name"] for t in tables_result.get("tables", [])]
        trajectory_steps += 1

        memory_hit = False
        memory_trace_id: str | None = None
        new_interrogation_samples: list[dict] = []

        if attempt == 0:
            # Step 1: consult memory (attempt 0 only -- C2-V6).
            if memory_enabled:
                store = get_trajectory_store()
                mem_result = await store.get_trajectory(
                    session, query_text=raw_query, schema_fingerprint=schema_fingerprint,
                )
                if mem_result.get("hit"):
                    best = mem_result["candidates"][0]
                    memory_hit = True
                    memory_trace_id = best["trace_id"]
                    mem_insight = best["insight"]
                    mem_tables = best.get("tables", [])
                    mem_join_path = best.get("join_path", [])
                    mem_dialect_notes = best.get("dialect_notes", [])

            if memory_hit:
                context_block = _format_memory_context(
                    mem_insight, mem_tables, mem_join_path, mem_dialect_notes,
                )
            else:
                retriever = get_schema_retriever()
                schema_context, table_descriptions = await retriever.retrieve(session, table_names)
                trajectory_steps += len(table_names)

                # Step 2/3: active interrogation (miss path, attempt 0 only).
                targets = _pick_interrogation_targets(
                    table_descriptions, cfg.sql.interrogation_max_samples,
                )
                for target in targets:
                    sample_result = await data_sample_rows(
                        session, target["table"], columns=target["columns"],
                    )
                    new_interrogation_samples.append({
                        "table": target["table"],
                        "columns_sampled": target["columns"],
                        "rows": sample_result.get("rows", []),
                    })
                    trajectory_steps += 1

                context_block = f"Schema:\n{schema_context}"
                if new_interrogation_samples:
                    context_block += "\n\n" + _format_interrogation_samples(new_interrogation_samples)
        else:
            # Retry: full schema always (regardless of attempt-0 hit/miss),
            # no memory re-consultation, no interrogation (already in state
            # from attempt 0, if any -- not re-sampled).
            retriever = get_schema_retriever()
            schema_context, _ = await retriever.retrieve(session, table_names)
            trajectory_steps += len(table_names)
            context_block = f"Schema:\n{schema_context}"

        max_rows = cfg.mcp.max_rows_default

        # Step 4: generate SQL -- debug override (attempt 0, test harness
        # only) or the provider seam.
        debug_sql = state_dict.get("debug_candidate_sql")
        if attempt == 0 and debug_sql:
            candidate_sql = debug_sql
            logger.warning(
                "SQL Agent using debug_candidate_sql override for query_id=%s "
                "(test harness only -- never set by production request paths).",
                query_id,
            )
        else:
            llm = get_agent_llm("sql")
            if attempt == 0:
                system_prompt = _SYSTEM_PROMPT.format(max_rows=max_rows)
                user_message = (
                    f"{context_block}\n\nQuestion: {raw_query}\n\n"
                    f"Write a single PostgreSQL SELECT query to answer this question."
                )
            else:
                system_prompt = _CORRECTION_SYSTEM_PROMPT.format(max_rows=max_rows)
                error_history_text = _format_error_history(existing_errors)
                # Error history first, schema second: the fix is the most
                # important, actionable content, and leading with it reads
                # more reliably than burying it after a full multi-table
                # schema dump.
                user_message = (
                    f"Question: {raw_query}\n\n{error_history_text}\n\n"
                    f"Full schema (find the correct column/table/join path here):\n{context_block}\n\n"
                    f"Write a single corrected PostgreSQL SELECT query."
                )

            sql_result = await llm.complete(
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=cfg.sql.max_tokens_sql,
            )
            tokens_in_total += sql_result.tokens_in
            tokens_out_total += sql_result.tokens_out
            llm_calls_total += 1
            candidate_sql = sql_result.text

        if candidate_sql.startswith("```"):
            candidate_sql = "\n".join(
                line for line in candidate_sql.split("\n")
                if not line.startswith("```")
            ).strip()

        logger.info(
            "SQL Agent generated SQL for query_id=%s attempt=%d (memory_hit=%s): %.200s...",
            query_id, attempt, memory_hit, candidate_sql,
        )

        # Step 5: execute.
        exec_result = await data_run_select(session, candidate_sql, max_rows=max_rows)
        elapsed_ms = int(time.monotonic() * 1000) - start_ms

        update: dict[str, Any] = {
            "schema_fingerprint": schema_fingerprint,
            "candidate_sql": candidate_sql,
            "attempt": attempt,
        }
        if new_interrogation_samples:
            update["interrogation_samples"] = new_interrogation_samples
        if attempt == 0:
            update["memory_hit"] = memory_hit
            update["memory_trace_id"] = memory_trace_id

        ok = bool(exec_result.get("ok"))
        rows = exec_result.get("rows", []) if ok else []

        last_error = existing_errors[-1] if existing_errors else None
        repeat_empty_accepted = (
            ok and not rows
            and last_error is not None
            and last_error.get("category") == "empty_result"
        )

        if ok and (rows or repeat_empty_accepted):
            # Real success, or a second empty_result in a row accepted as
            # the answer (A3) -- both are terminal-success shaped state.
            update["executed_sql"] = candidate_sql
            update["sql_rows"] = rows
            update["sql_columns"] = exec_result.get("columns", [])
            logger.info(
                "SQL Agent success for query_id=%s attempt=%d: %d rows returned.",
                query_id, attempt, len(rows),
            )

            # Write back unless this is a clean attempt-0 memory hit (C1 GAP-1).
            write_back = not (attempt == 0 and memory_hit)
            if memory_enabled and write_back:
                w_in, w_out, w_calls = await _write_trajectory(
                    session=session,
                    query_id=query_id,
                    raw_query=raw_query,
                    schema_fingerprint=schema_fingerprint,
                    candidate_sql=candidate_sql,
                    prior_errors=existing_errors,
                )
                tokens_in_total += w_in
                tokens_out_total += w_out
                llm_calls_total += w_calls

        elif ok and not rows:
            # First empty_result -- classified, retriable, consumes exactly
            # one retry attempt (A3).
            category, hint = classify_error(sqlstate=None, message=None, empty_result=True)
            update["executed_sql"] = candidate_sql
            update["sql_rows"] = []
            update["sql_columns"] = exec_result.get("columns", [])
            update["errors"] = [{
                "attempt": attempt,
                "category": category,
                "engine_code": None,
                "message": "Query executed successfully but returned zero rows.",
                "offending_sql": candidate_sql,
                "remediation_hint": hint,
            }]
            logger.warning(
                "SQL Agent empty_result for query_id=%s attempt=%d.", query_id, attempt,
            )

        else:
            error_obj = exec_result.get("error") or {}
            sqlstate: str | None = error_obj.get("sqlstate")
            message: str = error_obj.get("message", "Unknown error")
            category, hint = classify_error(sqlstate=sqlstate, message=message, empty_result=False)

            logger.warning(
                "SQL Agent failure for query_id=%s attempt=%d: category=%s sqlstate=%s message=%s",
                query_id, attempt, category, sqlstate, message,
            )

            update["executed_sql"] = candidate_sql
            update["sql_rows"] = []
            update["sql_columns"] = []
            update["errors"] = [{
                "attempt": attempt,
                "category": category,
                "engine_code": sqlstate,
                "message": message,
                "offending_sql": candidate_sql,
                "remediation_hint": hint,
            }]

        update["metrics"] = {
            "tokens_in":        tokens_in_total,
            "tokens_out":       tokens_out_total,
            "llm_calls":        llm_calls_total,
            "trajectory_steps": trajectory_steps,
            "latency_ms":       elapsed_ms,
        }
        return update


async def _write_trajectory(
    *,
    session: MCPSession,
    query_id: str,
    raw_query: str,
    schema_fingerprint: str,
    candidate_sql: str,
    prior_errors: list[dict],
) -> tuple[int, int, int]:
    """
    Summarize the navigation strategy via a dedicated LLM call and write it
    back via TrajectoryStore. Never lets a summarization/storage failure
    break the user-facing response -- the query already succeeded. When
    prior_errors is non-empty, the summarizer is told about them so the
    insight records the dialect note that caused the original failure
    (C2 Scope).

    Returns (tokens_in, tokens_out, llm_calls) to fold into the node's metrics.
    """
    summarizer = get_agent_llm("memory_summarizer")

    user_parts = [f"Question: {raw_query}", f"Executed SQL:\n{candidate_sql}"]
    if prior_errors:
        user_parts.append(_format_error_history(prior_errors))
    user_content = "\n\n".join(user_parts)

    try:
        result = await summarizer.complete(
            system=_SUMMARIZER_SYSTEM_PROMPT.format(
                insight_max_chars=cfg.memory.insight_max_chars,
            ),
            messages=[{"role": "user", "content": user_content}],
            max_tokens=cfg.sql.max_tokens_memory_summary,
        )
    except Exception as exc:
        logger.warning(
            "SQL Agent summarization call failed for query_id=%s: %s", query_id, exc,
        )
        return 0, 0, 0

    tokens_in, tokens_out = result.tokens_in, result.tokens_out

    try:
        parsed = json.loads(result.text)
        insight = str(parsed["insight"])[:cfg.memory.insight_max_chars]
        tables = parsed.get("tables") or []
        join_path = parsed.get("join_path") or []
        dialect_notes = parsed.get("dialect_notes") or []

        store = get_trajectory_store()
        put_result = await store.put_trajectory(
            session,
            query_text=raw_query,
            schema_fingerprint=schema_fingerprint,
            insight=insight,
            tables=tables,
            join_path=join_path,
            dialect_notes=dialect_notes,
        )
        logger.info(
            "SQL Agent wrote trajectory for query_id=%s: trace_id=%s",
            query_id, put_result.get("trace_id"),
        )
    except Exception as exc:
        logger.warning(
            "SQL Agent trajectory write skipped for query_id=%s: %s",
            query_id, exc,
        )

    return tokens_in, tokens_out, 1

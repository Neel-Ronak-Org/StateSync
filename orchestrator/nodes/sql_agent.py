"""
orchestrator/nodes/sql_agent.py
SQL Agent — Phase B implementation.

Per B2 spec:
  "The SQL Agent in B is: read schema via MCP → emit SQL → execute via MCP →
   on failure, return the raw error to the user with no retry. The correction
   loop arrives in C2 and the absence of it here is deliberate — B must
   demonstrate that failures are *visible* before C2 makes them *recoverable*."

Important: attempt must remain 0 after a failure in Phase B (B-V7 pass
condition: "No retry occurs (state.attempt == 0) — retrying in B is a fail").
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from config_loader import cfg
from llm.provider import get_agent_llm
from mcp_client import MCPSession, schema_list_tables, schema_describe_table, data_run_select

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a PostgreSQL query generator for a logistics data analytics system (ONDC network).
You have access to the following schema information and must write a single valid SELECT query.

Rules:
1. Write exactly one SQL SELECT statement. No semicolons at the end.
2. Qualify every column with its table alias.
3. Use only the tables and columns you see in the schema.
4. Apply LIMIT {max_rows} unless the question explicitly asks for all rows.
5. Use TIMESTAMPTZ-aware comparisons (cast to timestamptz, never compare with integers).
6. Return ONLY the SQL query text — no markdown, no explanation, no comments."""


async def sql_agent_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    """
    SQL Agent node. Reads schema, generates SQL, executes it.
    On failure: returns the error as data (B-V7). No retry.
    """
    raw_query: str = state_dict.get("normalized_query") or state_dict.get("raw_query", "")
    tenant_id: str = state_dict["tenant_id"]
    query_id: str = state_dict.get("query_id", "")

    logger.info("SQL Agent running for query_id=%s", query_id)

    start_ms = int(time.monotonic() * 1000)
    tokens_in_total = 0
    tokens_out_total = 0
    llm_calls_total = 0

    mcp_base_url = os.environ.get("MCP_URL", "http://mcp:8080")

    async with MCPSession(tenant_id=tenant_id, base_url=mcp_base_url) as session:
        # Step 1: List tables to understand the schema
        tables_result = await schema_list_tables(session)
        schema_fingerprint: str = tables_result.get("fingerprint", "")
        table_list = tables_result.get("tables", [])
        table_names = [t["name"] for t in table_list]

        # Step 2: Describe all tables (Phase B describes all; C1 will optimise)
        schema_parts: list[str] = []
        for tname in table_names:
            desc = await schema_describe_table(session, tname)
            col_lines = []
            for col in desc.get("columns", []):
                nullable = "NULL" if col.get("nullable") else "NOT NULL"
                comment = f"  -- {col['comment']}" if col.get("comment") else ""
                col_lines.append(f"    {col['name']} {col['type']} {nullable}{comment}")
            fk_lines = []
            for fk in desc.get("foreign_keys", []):
                fk_lines.append(
                    f"    FK: {tname}.{fk['column']} -> {fk['references_table']}.{fk['references_column']}"
                )
            schema_parts.append(
                f"TABLE {tname}:\n" + "\n".join(col_lines) +
                ("\nFOREIGN KEYS:\n" + "\n".join(fk_lines) if fk_lines else "")
            )

        schema_context = "\n\n".join(schema_parts)
        max_rows = cfg.mcp.max_rows_default

        # Step 3: Generate SQL via the provider seam
        llm = get_agent_llm("sql")

        user_message = (
            f"Schema:\n{schema_context}\n\n"
            f"Question: {raw_query}\n\n"
            f"Write a single PostgreSQL SELECT query to answer this question."
        )

        sql_result = await llm.complete(
            system=_SYSTEM_PROMPT.format(max_rows=max_rows),
            messages=[{"role": "user", "content": user_message}],
            max_tokens=cfg.sql.max_tokens_sql,
        )
        tokens_in_total += sql_result.tokens_in
        tokens_out_total += sql_result.tokens_out
        llm_calls_total += 1

        candidate_sql: str = sql_result.text
        # Strip markdown code blocks if the model wrapped the SQL
        if candidate_sql.startswith("```"):
            candidate_sql = "\n".join(
                line for line in candidate_sql.split("\n")
                if not line.startswith("```")
            ).strip()

        logger.info(
            "SQL Agent generated SQL for query_id=%s: %.200s...",
            query_id, candidate_sql,
        )

        # Step 4: Execute the SQL
        exec_result = await data_run_select(session, candidate_sql, max_rows=max_rows)
        elapsed_ms = int(time.monotonic() * 1000) - start_ms

        update: dict[str, Any] = {
            "schema_fingerprint": schema_fingerprint,
            "candidate_sql": candidate_sql,
            "metrics": {
                "tokens_in":        tokens_in_total,
                "tokens_out":       tokens_out_total,
                "llm_calls":        llm_calls_total,
                "trajectory_steps": 1,
                "latency_ms":       elapsed_ms,
            },
        }

        if exec_result.get("ok"):
            # Success path
            update["executed_sql"] = candidate_sql
            update["sql_rows"]     = exec_result.get("rows", [])
            update["sql_columns"]  = exec_result.get("columns", [])
            logger.info(
                "SQL Agent success for query_id=%s: %d rows returned.",
                query_id, len(update["sql_rows"]),
            )
        else:
            # Failure path (Phase B): surface the error as data; no retry (B-V7)
            error_obj = exec_result.get("error") or {}
            sqlstate: str | None = error_obj.get("sqlstate")
            message: str = error_obj.get("message", "Unknown error")

            logger.warning(
                "SQL Agent failure for query_id=%s: sqlstate=%s message=%s",
                query_id, sqlstate, message,
            )

            # attempt stays 0 — no retry in Phase B
            update["executed_sql"] = candidate_sql
            update["sql_rows"]     = []
            update["sql_columns"]  = []
            update["_sql_error"]   = {
                "sqlstate":     sqlstate,
                "message":      message,
                "offending_sql": candidate_sql,
            }

        return update

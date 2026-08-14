"""
orchestrator/nodes/synthesizer.py
Synthesizer node — Phase B implementation.

Per B1 scope: "The Synthesizer in B emits answer_text and chart: null,
validated against A7."

Per A7 enforcement:
- Validates output against A7 schema before returning.
- If validation fails: returns prose answer with chart: null and logs a
  validation failure. Never returns a repaired chart.
- chart: null in Phase B — chart generation is Phase E2.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from config_loader import cfg
from llm.provider import get_agent_llm
from schemas.synthesizer_output import safe_synthesizer_output

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a data analytics assistant. Answer the user's question based on the SQL query results provided.

Rules:
1. Be concise and factual. Base your answer only on the provided data.
2. If the query failed with an error, explain the failure clearly to the user.
3. Do not mention internal details like table names or SQL syntax unless helpful.
4. Respond with a JSON object matching this exact schema:
   {"answer_text": "<string, max 4000 chars>", "chart": null, "citations": [{"kind": "sql", "ref": "<executed_sql>"}]}
5. chart must always be null in this response.
6. citations must include one entry with kind: "sql" and ref: the executed SQL (or attempted SQL on failure).
7. answer_text must not exceed 4000 characters."""


def _ensure_category_mentioned(answer_text: str, sql_error: dict | None) -> str:
    """
    C2-V4: "the user-facing response reports failure with the classified
    category rather than a hallucinated answer." The system prompt already
    asks the LLM to name the category, but this repo's own local-model
    testing has repeatedly shown small models are not reliable at following
    that kind of instruction (see docs/phase_c1_log.md's B-V1 finding) --
    so this is enforced deterministically rather than only requested.
    """
    if not sql_error:
        return answer_text
    category = sql_error.get("category", "unclassified")
    if category.lower() in answer_text.lower():
        return answer_text
    suffix = f" (classified failure: {category})"
    max_len = cfg.sql.answer_max_chars
    if len(answer_text) + len(suffix) > max_len:
        answer_text = answer_text[: max_len - len(suffix)]
    return answer_text + suffix


async def synthesizer_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Synthesizer node. Generates answer_text from SQL results or error info.
    Validates output against A7. chart is always null in Phase B.
    """
    query_id: str = state_dict.get("query_id", "")
    raw_query: str = state_dict.get("normalized_query") or state_dict.get("raw_query", "")
    sql_rows: list[dict] = state_dict.get("sql_rows", [])
    sql_columns: list[str] = state_dict.get("sql_columns", [])
    executed_sql: str | None = state_dict.get("executed_sql")
    stub_message: str | None = state_dict.get("_stub_message")

    # Derived from the typed, reliably-accumulated state.errors/state.attempt
    # (same test _route_after_sql uses), NOT a scratch key. A conditional
    # edge (sql -> [_route_after_sql] -> error_exit) sits between
    # sql_agent_node and this node whenever the loop terminates on failure;
    # LangGraph must reconstruct a typed StateSyncState to evaluate that
    # routing function, and an underscore-prefixed scratch key set by
    # sql_agent_node's return dict does not survive that reconstruction --
    # only declared A2 fields do. errors/attempt are declared fields, so
    # this is the reliable way to know "did the last attempt fail."
    errors: list[dict] = state_dict.get("errors", [])
    attempt: int = state_dict.get("attempt", 0)
    sql_error: dict | None = errors[-1] if len(errors) > attempt else None

    start_ms = int(time.monotonic() * 1000)

    # Stub route — pass through without an LLM call
    if stub_message:
        output = {
            "answer_text": stub_message,
            "chart": None,
            "citations": [],
        }
        safe_out = safe_synthesizer_output(output)
        elapsed_ms = int(time.monotonic() * 1000) - start_ms
        return {
            "answer_text": safe_out["answer_text"],
            "vega_spec": None,
            "metrics": {
                "tokens_in":        0,
                "tokens_out":       0,
                "llm_calls":        0,
                "trajectory_steps": 1,
                "latency_ms":       elapsed_ms,
            },
        }

    # Build context for the LLM
    if sql_error:
        # Category is C2's classifier output (A3), never LLM-generated.
        category = sql_error.get("category", "unclassified")
        context = (
            f"The SQL query failed after {sql_error.get('attempt', 0) + 1} attempt(s).\n"
            f"Classified failure category: {category}\n"
            f"Error code: {sql_error.get('engine_code') or 'N/A'}\n"
            f"Error message: {sql_error.get('message', 'Unknown error')}\n"
            f"Remediation hint (why no further attempt was made): {sql_error.get('remediation_hint', 'N/A')}\n"
            f"Attempted SQL: {sql_error.get('offending_sql', executed_sql or 'N/A')}\n\n"
            f"State plainly that the query failed and name the classified failure "
            f"category ({category}) in your answer -- do not omit it, and do not "
            f"invent a numeric or factual answer that was never computed."
        )
        citation_ref = sql_error.get("offending_sql") or executed_sql or ""
    else:
        if sql_rows:
            rows_preview = sql_rows[:cfg.sql.synthesizer_preview_rows]
            rows_text = json.dumps(rows_preview, indent=2, default=str)  # noqa: literals — JSON display indent
            context = (
                f"SQL query returned {len(sql_rows)} row(s).\n"
                f"Columns: {', '.join(sql_columns)}\n"
                f"Results (first {len(rows_preview)} rows):\n{rows_text}"
            )
        else:
            context = "The SQL query returned zero rows. The answer may be 'none' or 'zero'."
        citation_ref = executed_sql or ""

    user_message = f"Question: {raw_query}\n\nData context:\n{context}"

    llm = get_agent_llm("synthesizer")

    try:
        result = await llm.complete(
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=cfg.sql.max_tokens_synthesizer,
        )
        elapsed_ms = int(time.monotonic() * 1000) - start_ms

        # Parse the JSON response
        try:
            parsed = json.loads(result.text)
        except json.JSONDecodeError:
            logger.warning(
                "Synthesizer returned non-JSON for query_id=%s; treating as plain text.",
                query_id,
            )
            parsed = {
                "answer_text": result.text[:cfg.sql.answer_max_chars],
                "chart": None,
                "citations": [{"kind": "sql", "ref": citation_ref}],
            }

        # Force chart: null in Phase B
        parsed["chart"] = None

        if not parsed.get("citations"):
            parsed["citations"] = [{"kind": "sql", "ref": citation_ref}]

        # Validate against A7
        safe_out = safe_synthesizer_output(parsed)
        answer_text = _ensure_category_mentioned(safe_out["answer_text"], sql_error)

        logger.info(
            "Synthesizer completed for query_id=%s (tokens_in=%d tokens_out=%d).",
            query_id, result.tokens_in, result.tokens_out,
        )

        return {
            "answer_text": answer_text,
            "vega_spec": None,
            "metrics": {
                "tokens_in":        result.tokens_in,
                "tokens_out":       result.tokens_out,
                "llm_calls":        1,
                "trajectory_steps": 1,
                "latency_ms":       elapsed_ms,
            },
        }

    except Exception as exc:
        logger.error("Synthesizer error for query_id=%s: %s", query_id, exc)
        elapsed_ms = int(time.monotonic() * 1000) - start_ms
        fallback = {
            "answer_text": f"An error occurred while generating the answer: {exc}",
            "chart": None,
            "citations": [{"kind": "sql", "ref": citation_ref}],
        }
        safe_out = safe_synthesizer_output(fallback)
        answer_text = _ensure_category_mentioned(safe_out["answer_text"], sql_error)
        return {
            "answer_text": answer_text,
            "vega_spec": None,
            "metrics": {
                "tokens_in":        0,
                "tokens_out":       0,
                "llm_calls":        1,
                "trajectory_steps": 1,
                "latency_ms":       elapsed_ms,
            },
        }

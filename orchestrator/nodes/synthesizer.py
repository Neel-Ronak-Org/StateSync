"""
orchestrator/nodes/synthesizer.py
Synthesizer node.

Per A7 enforcement:
- Validates output against A7 schema before returning.
- If validation fails: returns prose answer with chart: null and logs a
  validation failure. Never returns a repaired chart.
- chart: null through Part E1 — chart generation is Phase E2.

Per E1-V4: citations are built deterministically from state (sql when
executed_sql is present, chunk entries from state.retrievals when present),
not left to the LLM's own citation list -- the same reasoning as
_ensure_category_mentioned below: this repo's local-model testing has
repeatedly shown small models are not reliable at following "always include
both kinds" instructions, and a response citing only one kind is a spec
failure even if the prose mentions both sources. A hybrid query naturally
gets both kinds this way, without depending on the model remembering to.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from config_loader import cfg
from llm.provider import get_agent_llm
from schemas.synthesizer_output import safe_synthesizer_output

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a data analytics assistant for an ONDC logistics network. Answer \
the user's question using only the data context provided below -- SQL \
query results, retrieved document passages, or both.

Rules:
1. Be concise and factual. Base your answer only on the provided data --
   never invent a number or fact that isn't in the context.
2. If the context includes retrieved document passages, ground every
   factual claim in them; do not state something as fact unless it traces
   to a passage or the SQL results.
3. If the query failed with an error, explain the failure clearly to the user.
4. Do not mention internal details like table names or SQL syntax unless helpful.
5. Respond with a JSON object matching this exact schema:
   {"answer_text": "<string, max 4000 chars>", "chart": null, "citations": []}
6. chart must always be null in this response.
7. Leave citations as an empty array -- it is filled in deterministically
   after your response, not by you.
8. answer_text must not exceed 4000 characters.
9. NEVER include executable code in answer_text -- no Python, matplotlib,
   JavaScript, shell commands, or any other runnable program text, and no
   fenced code blocks. If the user asks for code, or asks you to run
   something, explain in prose that this system answers with data and
   declarative charts only, and does not produce or execute code (D8).
10. Emit the raw JSON object only. Do not wrap it in markdown code fences."""


def _strip_json_fences(text: str) -> str:
    """
    Strip markdown code fences the model sometimes wraps its JSON response in.

    Without this, a fenced response fails json.loads(), falls into the
    plain-text branch, and the ENTIRE raw JSON blob (braces, field names and
    all) is handed to the user as answer_text. Observed directly during F-V1
    Class 4. Same fence-stripping the SQL Agent already does for its own
    output (sql_agent._strip_sql_fences).
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = [ln for ln in stripped.split("\n") if not ln.lstrip().startswith("```")]
    return "\n".join(lines).strip()


# Fenced code blocks, and bare lines that are unmistakably program text.
# Deliberately conservative: this only has to catch content a user could
# copy and RUN, not every mention of a technical term.
_FENCED_BLOCK_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_CODE_LINE_RE = re.compile(
    r"^\s*(?:import\s+\w|from\s+\w+\s+import\b|def\s+\w+\s*\(|class\s+\w+\s*[:(]"
    r"|plt\.\w|os\.system\s*\(|subprocess\.\w|exec\s*\(|eval\s*\()",
    re.MULTILINE,
)
_CODE_REFUSAL = (
    "This system answers with data and declarative charts only. It does not "
    "produce or execute code (see the declarative-output contract, D8/A7), so "
    "the code that was requested has been withheld."
)


def _strip_executable_content(answer_text: str, query_id: str) -> str:
    """
    F-V1 Class 4 / M8: "no response contains executable content."

    A7's schema constrains `chart` but says nothing about `answer_text`
    beyond its length, so nothing structurally stopped the Synthesizer from
    putting a runnable matplotlib program in the prose field -- and on
    adversarial payload adv_18 ("give me matplotlib code ... then run it")
    it did exactly that, verbatim and copy-pasteable. Rule 9 of the system
    prompt now tells the model not to, but M8 is binary and deterministic,
    and this repo's own experience is that small local models follow
    negative instructions unreliably (same reasoning as
    _ensure_category_mentioned right below). So the guarantee is enforced
    here, in code, rather than trusted to the prompt.

    Note this is defense in depth, not the RCE boundary: nothing downstream
    ever executes answer_text, and D8's actual security property is the
    absence of any execution path at all. This closes the gap between that
    property and F-V1's written pass condition, which is about what the
    response CONTAINS.
    """
    cleaned = _FENCED_BLOCK_RE.sub("", answer_text)
    if _CODE_LINE_RE.search(cleaned):
        # Bare (unfenced) program lines survived fence removal -- drop them.
        cleaned = "\n".join(
            ln for ln in cleaned.split("\n") if not _CODE_LINE_RE.match(ln)
        )
    if cleaned != answer_text:
        logger.warning(
            "Synthesizer emitted executable content for query_id=%s; stripped "
            "before response (F-V1 Class 4 / M8).", query_id,
        )
        cleaned = cleaned.strip()
        cleaned = f"{cleaned}\n\n{_CODE_REFUSAL}" if cleaned else _CODE_REFUSAL
    return cleaned


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


def build_citations(
    executed_sql: str | None, sql_error: dict | None, retrievals: list[dict],
) -> list[dict]:
    """
    Deterministic citation construction (E1-V4; see module docstring).
    kind:"sql" whenever there's a SQL statement to point to (success or
    failure -- citing what was attempted is still meaningful on failure).
    kind:"chunk" for the first citation_max_chunks retrievals, IN THE ORDER
    GIVEN -- deliberately not re-sorted by raw score here. The caller
    (unstructured_knowledge.py's _semantic_search_expanded) already orders
    retrievals document-major (best-matching document first, that
    document's own chunks together in reading order), specifically so a
    relevant document's table chunk stays adjacent to its narrative chunk
    instead of being outranked by an unrelated document's narrative chunk
    (table Markdown embeds further from a natural-language question than
    prose does -- verified empirically, see docs/phase_e1_log.md).
    Re-sorting by score here would undo that ordering.

    Public (not module-private): main.py imports this directly and
    recomputes the same citations from final_state's typed fields, rather
    than this node smuggling its own computed value out through a scratch
    key. Verified empirically that graph.ainvoke()'s return value is
    reconstructed against StateSyncState's schema regardless of whether the
    last hop was a direct or conditional edge -- scratch keys do not survive
    to the graph's own boundary, only between internal node-to-node hops
    (the same lesson C2's scratch-key-through-conditional-edge finding
    already established for sql_error; this is that principle applied one
    level further out).
    """
    citations: list[dict] = []
    sql_ref = (sql_error or {}).get("offending_sql") or executed_sql
    if sql_ref:
        citations.append({"kind": "sql", "ref": sql_ref, "page": None})

    for r in retrievals[: cfg.retrieval.citation_max_chunks]:
        prov = r["provenance"]
        citations.append({"kind": "chunk", "ref": prov["chunk_id"], "page": prov.get("page")})

    return citations


def _format_doc_context(retrievals: list[dict]) -> str:
    parts = [f"Retrieved {len(retrievals)} document passage(s):"]
    for r in retrievals[: cfg.retrieval.semantic_top_k]:
        prov = r["provenance"]
        parts.append(
            f"  [chunk_id={prov['chunk_id']} document_id={prov['document_id']} "
            f"page={prov.get('page')} score={prov['score']:.3f}]\n{r['content']}"
        )
    return "\n\n".join(parts)


async def synthesizer_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Synthesizer node. Generates answer_text from SQL results, retrieved
    document passages, or both (sql / docs / hybrid routes respectively).
    Validates output against A7. chart is always null through Part E1.
    """
    query_id: str = state_dict.get("query_id", "")
    raw_query: str = state_dict.get("normalized_query") or state_dict.get("raw_query", "")
    sql_rows: list[dict] = state_dict.get("sql_rows", [])
    sql_columns: list[str] = state_dict.get("sql_columns", [])
    executed_sql: str | None = state_dict.get("executed_sql")
    retrievals: list[dict] = state_dict.get("retrievals", [])

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
    citations = build_citations(executed_sql, sql_error, retrievals)

    # Build context for the LLM. sql/doc context blocks are independent and
    # concatenated when both are present (hybrid) -- neither depends on the
    # other's shape.
    context_parts: list[str] = []

    if sql_error:
        # Category is C2's classifier output (A3), never LLM-generated.
        category = sql_error.get("category", "unclassified")
        context_parts.append(
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
    elif executed_sql:
        if sql_rows:
            rows_preview = sql_rows[:cfg.sql.synthesizer_preview_rows]
            rows_text = json.dumps(rows_preview, indent=2, default=str)  # noqa: literals — JSON display indent
            context_parts.append(
                f"SQL query returned {len(sql_rows)} row(s).\n"
                f"Columns: {', '.join(sql_columns)}\n"
                f"Results (first {len(rows_preview)} rows):\n{rows_text}"
            )
        else:
            context_parts.append("The SQL query returned zero rows. The answer may be 'none' or 'zero'.")

    if retrievals:
        context_parts.append(_format_doc_context(retrievals))

    if not context_parts:
        context_parts.append("No data was retrieved for this question.")

    user_message = f"Question: {raw_query}\n\nData context:\n" + "\n\n".join(context_parts)

    llm = get_agent_llm("synthesizer")

    try:
        result = await llm.complete(
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=cfg.sql.max_tokens_synthesizer,
        )
        elapsed_ms = int(time.monotonic() * 1000) - start_ms

        # Parse the JSON response (fences stripped first -- a fenced response
        # would otherwise fall through to the plain-text branch below and put
        # the whole raw JSON blob into answer_text).
        try:
            parsed = json.loads(_strip_json_fences(result.text))
        except json.JSONDecodeError:
            logger.warning(
                "Synthesizer returned non-JSON for query_id=%s; treating as plain text.",
                query_id,
            )
            parsed = {
                "answer_text": _strip_json_fences(result.text)[:cfg.sql.answer_max_chars],
                "chart": None, "citations": [],
            }

        # Force chart: null through Part E1; citations always deterministic (above).
        parsed["chart"] = None
        parsed["citations"] = citations

        # Validate against A7
        safe_out = safe_synthesizer_output(parsed)
        answer_text = _strip_executable_content(safe_out["answer_text"], query_id)
        answer_text = _ensure_category_mentioned(answer_text, sql_error)

        logger.info(
            "Synthesizer completed for query_id=%s (tokens_in=%d tokens_out=%d citations=%d).",
            query_id, result.tokens_in, result.tokens_out, len(citations),
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
            "citations": citations,
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

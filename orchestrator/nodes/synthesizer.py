"""
orchestrator/nodes/synthesizer.py
The answer writer: turns the SQL result and/or the passages into the answer the user reads.

What the model is given: the question, the SQL result (row count, columns, the first rows) or a
plain-words reason the query failed, the passages the documents engine chose (each labelled with its
document title and page), and any caveats. What the model writes: the answer text, and optionally the
SHAPE of a chart (mark and which column goes on which axis).

What code does, not the model, so it cannot be wrong in the model's way:
  - citations: built from what actually ran (the executed SQL, the passages' chunk ids and pages);
  - chart data: the real rows are attached to the model's chart shape, and a shape naming a column
    that is not in the result is dropped;
  - every answer is checked against the output schema (schemas/synthesizer_output.py); a bad chart
    is dropped, never repaired;
  - program code in the answer text is removed (the system answers with data, never with code).

A question neither source can answer gets a fixed reply with no model call.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import context_carry
from config_loader import cfg
from llm.provider import get_agent_llm
from schemas.synthesizer_output import safe_synthesizer_output

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a data analytics assistant. Answer \
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
   {"answer_text": "<string, max 4000 chars>", "chart": <null or chart shape>, "citations": []}
6. chart: propose a chart ONLY when the data context says a chart is
   appropriate, and only ever as this SHAPE -- never include the data:
     {"mark": "<bar|line|point|area|tick|rect|arc>",
      "encoding": {"<channel>": {"field": "<exact column name>",
                                 "type": "<quantitative|nominal|ordinal|temporal>",
                                 "aggregate": "<optional: sum|mean|count|min|max|median>"}},
      "title": "<optional short title>"}
   - Use ONLY mark values from that list. Do not invent a mark type.
   - "field" must be an EXACT column name from the Columns line in the
     context. A channel naming any other column is discarded.
   - Do NOT include a "data" key. The real rows are attached in code; any
     data you write would be ignored and could contradict your own answer.
   - Typical choices: a category vs a measure -> bar; a measure over time ->
     line or area; two measures -> point; a share of a whole -> arc.
   - If the data context says a chart is not appropriate, or no single chart
     honestly represents the answer, use null. A wrong chart is worse than
     none.
7. Leave citations as an empty array -- it is filled in deterministically
   after your response, not by you.
8. answer_text must not exceed 4000 characters.
9. NEVER include executable code in answer_text -- no Python, matplotlib,
   JavaScript, shell commands, or any other runnable program text, and no
   fenced code blocks. If the user asks for code, or asks you to run
   something, explain in prose that this system answers with data and
   declarative charts only, and does not produce or execute code.
10. Emit the raw JSON object only. Do not wrap it in markdown code fences."""


_SECTION_SEP = " › "  # citation title: "{document title} › {section}" for a chunk under a heading
_PCT_NAME_WORDS = ("rate", "share", "ratio", "pct", "percent", "conversion", "fraction")


_A7_MARKS = ("bar", "line", "point", "area", "tick", "rect", "arc")
_A7_ENCODING_TYPES = ("quantitative", "nominal", "ordinal", "temporal")
_A7_AGGREGATES = ("sum", "mean", "count", "min", "max", "median")
_A7_ENCODING_PROPS = ("field", "type", "aggregate", "title", "sort")
_VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"


def is_chartable(sql_rows: list[dict], sql_columns: list[str]) -> bool:
    """Whether a result deserves a chart: at least [sql] chart_min_rows rows and chart_min_columns columns.
    A single figure is an answer, not a chart."""
    return (
        len(sql_rows) >= cfg.sql.chart_min_rows
        and len(sql_columns) >= cfg.sql.chart_min_columns
    )


def build_chart(
    proposed: dict | None,
    sql_rows: list[dict],
    sql_columns: list[str],
    query_id: str,
) -> dict | None:
    """The final chart: the model's proposed SHAPE (mark and encoding) plus the real result rows, attached here.

    The model chooses what kind of chart and which column goes on which axis; this code supplies the data
    (data.values, at most [sql] chart_max_data_rows rows). The model never writes the data, because a model
    that does can draw a plausible chart that contradicts its own answer, and every value would still look
    valid. A channel naming a column that is not in the result is dropped (Vega-Lite would draw it empty).
    Returns None when no valid chart can be built: never a partial or repaired one."""
    if not isinstance(proposed, dict):
        return None

    mark = proposed.get("mark")
    if mark not in _A7_MARKS:
        if mark is not None:
            logger.info(
                "Synthesizer proposed mark %r for query_id=%s, which is not an allowed mark; chart dropped.",
                mark, query_id,
            )
        return None

    raw_encoding = proposed.get("encoding")
    if not isinstance(raw_encoding, dict) or not raw_encoding:
        return None

    columns = set(sql_columns)
    encoding: dict[str, dict] = {}
    for channel, spec in raw_encoding.items():
        if not isinstance(spec, dict):
            continue
        field = spec.get("field")
        if field is not None and field not in columns:
            logger.info(
                "Synthesizer bound channel %r to unknown column %r for query_id=%s; channel dropped.",
                channel, field, query_id,
            )
            continue
        clean = {k: v for k, v in spec.items() if k in _A7_ENCODING_PROPS}
        if clean.get("type") not in _A7_ENCODING_TYPES:
            continue
        if "aggregate" in clean and clean["aggregate"] not in _A7_AGGREGATES:
            del clean["aggregate"]
        encoding[channel] = clean

    if not encoding:
        return None

    chart: dict = {
        "$schema": _VEGA_LITE_SCHEMA,
        "mark": mark,
        "data": {"values": sql_rows[:cfg.sql.chart_max_data_rows]},
        "encoding": encoding,
    }
    title = proposed.get("title")
    if isinstance(title, str) and title.strip():
        chart["title"] = title.strip()
    return chart


def _extract_json_object(text: str) -> dict | None:
    """
    Recover a JSON object embedded in surrounding prose.

    Scans for the first `{` and returns the substring through its matching
    `}`, parsed. Brace counting is string-aware: braces inside a JSON string
    value (an answer_text that itself mentions "{") and escaped quotes do not
    affect the depth, which a naive `text[text.find("{"):text.rfind("}")+1]`
    gets wrong on exactly the answers most likely to contain them.

    Returns the parsed object, or None when no balanced object parses --
    never a partial or repaired one. A model that produced genuine prose and
    no JSON must still fall through to the plain-text branch.
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    # strict=False: see the primary parse site for why raw
                    # newlines inside string values must be tolerated.
                    candidate = json.loads(text[start:i + 1], strict=False)
                except json.JSONDecodeError:
                    return None
                return candidate if isinstance(candidate, dict) else None
    return None


def _strip_json_fences(text: str) -> str:
    """Strip the Markdown code fences a model sometimes wraps its JSON in; otherwise the whole raw JSON
    would reach the user as the answer text."""
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
    "produce or execute code, so "
    "the code that was requested has been withheld."
)


def _strip_executable_content(answer_text: str, query_id: str) -> str:
    """Remove program code from the answer text. The system answers with data and charts, never with code
    a reader could copy and run: an adversarial question once got a runnable matplotlib program back. The
    prompt forbids it, and this enforces it in code. (Nothing ever executes the answer text; this is about
    what the reader is handed.)"""
    cleaned = _FENCED_BLOCK_RE.sub("", answer_text)
    if _CODE_LINE_RE.search(cleaned):
        # Bare (unfenced) program lines survived fence removal -- drop them.
        cleaned = "\n".join(
            ln for ln in cleaned.split("\n") if not _CODE_LINE_RE.match(ln)
        )
    if cleaned != answer_text:
        logger.warning(
            "Synthesizer emitted executable content for query_id=%s; stripped "
            "before response.", query_id,
        )
        cleaned = cleaned.strip()
        cleaned = f"{cleaned}\n\n{_CODE_REFUSAL}" if cleaned else _CODE_REFUSAL
    return cleaned


def build_citations(
    executed_sql: str | None, sql_error: dict | None, retrievals: list[dict],
) -> list[dict]:
    """The answer's citations, built by code from what actually ran, never by the model:
      - kind "sql": the statement, only if it reached the database (it ran, or the database rejected it); a
        statement refused before running proves nothing and is not cited;
      - kind "chunk": the first [retrieval] citation_max_chunks passages in the order the writer read them,
        with the document title and page.
    main.py calls this again on the final state, so the response carries exactly what was validated."""
    citations: list[dict] = []
    reached_engine = bool((sql_error or {}).get("engine_code"))
    sql_ref = executed_sql or ((sql_error or {}).get("offending_sql") if reached_engine else None)
    if sql_ref:
        citations.append({"kind": "sql", "ref": sql_ref, "page": None})

    for r in retrievals[: cfg.retrieval.citation_max_chunks]:
        prov = r["provenance"]
        citation = {"kind": "chunk", "ref": prov["chunk_id"], "page": prov.get("page")}
        if prov.get("title"):
            # The output schema caps a citation title at title_max_chars; "{title} › {section}" can run longer.
            citation["title"] = _SECTION_SEP.join(filter(None, (prov["title"], prov.get("section"))))[:cfg.library.title_max_chars]
        citations.append(citation)

    return citations


# What the writer is told, in plain words, when the query did not produce an answer. Keyed by the classifier's
# category, which stays in state.errors and the trace (never in the user's answer); a category not listed gets
# _PLAIN_FAILURE_OTHER.
_PLAIN_FAILURES = {
    "no_data_source": "the database in this workspace does not hold the data this question asks about.",
    "permission_denied": "the request was refused, because this system only runs single, read-only queries.",
    "timeout": "the query took too long and was stopped.",
    "empty_result": "no records matched.",
}
_PLAIN_FAILURE_OTHER = "the query could not be completed."

_PLAIN_FAILURE_CONTEXT = (
    "The data query did not produce an answer: {plain}\n"
    "Explain this to the user plainly in one or two sentences. Do not mention internal labels, error\n"
    "categories or codes. Do not invent a number or fact that was never computed."
)
_PLAIN_ZERO_ROWS_CONTEXT = (
    "The SQL query returned zero rows: no records matched it. Say that no matching records were found. "
    "Do not turn this into a figure about something the query did not measure."
)
# Route recovery: the documents route ran in place of a SQL route whose data the database lacks.
_RECOVERED_FROM_SQL_CONTEXT = (
    "The database did not hold this, so the answer comes from the document passages below. "
    "Say so in one short clause."
)


def plain_failure_context(category: str) -> str:
    """The failed-query block of the writer's context."""
    return _PLAIN_FAILURE_CONTEXT.format(plain=_PLAIN_FAILURES.get(category, _PLAIN_FAILURE_OTHER))


def _format_doc_context(retrievals: list[dict]) -> str:
    """The passages the writer reads: the first retrieval.semantic_top_k (the relevance window), each
    labelled with its document title and page so the answer can say which document it comes from."""
    parts = [f"Retrieved {len(retrievals)} document passage(s):"]
    for r in retrievals[: cfg.retrieval.semantic_top_k]:
        prov = r["provenance"]
        page = prov.get("page")
        parts.append(
            f"  [Document: {prov.get('title') or prov['document_id']} · page {'?' if page is None else page} · "
            f"chunk_id={prov['chunk_id']} · score={prov['score']:.3f}]\n{r['content']}"
        )
    return "\n\n".join(parts)


async def synthesizer_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    """The answer writer (see the module docstring). Returns answer_text, the validated chart and metrics."""
    query_id: str = state_dict.get("query_id", "")
    raw_query: str = state_dict.get("normalized_query") or state_dict.get("raw_query", "")
    sql_rows: list[dict] = state_dict.get("sql_rows", [])
    sql_columns: list[str] = state_dict.get("sql_columns", [])
    executed_sql: str | None = state_dict.get("executed_sql")
    retrievals: list[dict] = state_dict.get("retrievals", [])

    if state_dict.get("out_of_scope"):
        # The router found that neither the database nor the documents could answer. The answer is the
        # fixed message: no model call, no citations and no chart; it still goes through the validator.
        start_ms = int(time.monotonic() * 1000)
        safe_out = safe_synthesizer_output({
            "answer_text": cfg.front_door.out_of_scope_message, "chart": None, "citations": [],
        })
        logger.info("Synthesizer: out of scope for query_id=%s (no model call).", query_id)
        return {
            "answer_text": safe_out["answer_text"],
            "vega_spec": None,
            "metrics": {
                "tokens_in": 0, "tokens_out": 0, "llm_calls": 0, "trajectory_steps": 1,
                "latency_ms": int(time.monotonic() * 1000) - start_ms,
            },
        }

    # The last attempt failed when there are more errors than attempts (the test graph.py uses).
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
        context_parts.append(plain_failure_context(sql_error.get("category", "unclassified")))
    elif executed_sql:
        if sql_rows:
            rows_preview = sql_rows[:cfg.sql.synthesizer_preview_rows]
            rows_text = json.dumps(rows_preview, indent=2, default=str)  # noqa: literals — JSON display indent
            chartable = is_chartable(sql_rows, sql_columns)
            chart_hint = (
                f"A chart IS appropriate for this result "
                f"({len(sql_rows)} rows x {len(sql_columns)} columns). Propose one "
                f"unless no single chart honestly represents the answer."
                if chartable else
                f"A chart is NOT appropriate for this result "
                f"({len(sql_rows)} row(s) x {len(sql_columns)} column(s) is too "
                f"little shape to plot). Use chart: null."
            )
            context_parts.append(
                f"SQL query returned {len(sql_rows)} row(s).\n"
                f"Columns: {', '.join(sql_columns)}\n"
                f"{chart_hint}\n"
                f"Results (first {len(rows_preview)} rows):\n{rows_text}"
            )
        else:
            context_parts.append(_PLAIN_ZERO_ROWS_CONTEXT)

    if retrievals:
        if state_dict.get("recovered_from") == "sql":
            context_parts.append(_RECOVERED_FROM_SQL_CONTEXT)
        context_parts.append(_format_doc_context(retrievals))

    caveats: list[str] = state_dict.get("caveats") or []
    if caveats:
        context_parts.append("Caveats (mention them briefly to the user): " + " ".join(caveats))

    if not context_parts:
        context_parts.append("No data was retrieved for this question.")

    # A follow-up's context line (context_carry.py), for the documents and two-source answers.
    context_line = context_carry.question_context(state_dict) if state_dict.get("route") in ("docs", "hybrid") else ""
    user_message = f"Question: {raw_query}{context_line}\n\nData context:\n" + "\n\n".join(context_parts)

    llm = get_agent_llm("synthesizer")

    try:
        call = {"system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_message}],
                "max_tokens": cfg.sql.max_tokens_synthesizer}
        result = await llm.complete(**call)
        calls, spent_in, spent_out = 1, 0, 0
        if not (result.text or "").strip():
            # An empty answer is never useful to the reader. Seen on a reseller host (6 of 93
            # answers in the evidence-tiers robustness run: a normal finish, no visible text), so
            # one more call before the empty text would reach the user.
            logger.warning("Synthesizer returned an empty answer for query_id=%s; asking once more.", query_id)
            calls, spent_in, spent_out = 2, result.tokens_in, result.tokens_out
            result = await llm.complete(**call)
        elapsed_ms = int(time.monotonic() * 1000) - start_ms

        # Parse the JSON response (fences stripped first -- a fenced response
        # would otherwise fall through to the plain-text branch below and put
        # the whole raw JSON blob into answer_text).
        try:
            # strict=False accepts literal newlines inside the answer text, which models often write;
            # rejecting them once threw away most charts. A truly malformed object still fails.
            parsed = json.loads(_strip_json_fences(result.text), strict=False)
        except json.JSONDecodeError:
            # Second chance: a reply that puts a sentence before its JSON ("Here is the response:")
            # still contains the object.
            parsed = _extract_json_object(result.text)
            if parsed is None:
                logger.warning(
                    "Synthesizer returned non-JSON for query_id=%s; treating as "
                    "plain text. Response began: %r",
                    query_id, result.text[:200],  # noqa: literals — log truncation width
                )
                parsed = {
                    "answer_text": _strip_json_fences(result.text)[:cfg.sql.answer_max_chars],
                    "chart": None, "citations": [],
                }
            else:
                logger.info(
                    "Synthesizer wrapped its JSON in prose for query_id=%s; "
                    "object recovered.", query_id,
                )

        # The model proposed only the chart's shape; the rows are attached in code (build_chart), and only
        # when the result deserves a chart at all.
        parsed["chart"] = (
            build_chart(parsed.get("chart"), sql_rows, sql_columns, query_id)
            if is_chartable(sql_rows, sql_columns) else None
        )
        parsed["citations"] = citations

        # Check against the output schema
        safe_out = safe_synthesizer_output(parsed)
        answer_text = _strip_executable_content(safe_out["answer_text"], query_id)

        # A chart that fails the schema (or contains an expression) is dropped, never repaired. main.py checks
        # the assembled response again, and the web app once more before drawing.
        final_chart = safe_out["chart"]
        logger.info(
            "Synthesizer completed for query_id=%s (tokens_in=%d tokens_out=%d "
            "citations=%d chart=%s).",
            query_id, result.tokens_in, result.tokens_out, len(citations),
            final_chart["mark"] if final_chart else "null",
        )

        return {
            "answer_text": answer_text,
            "vega_spec": final_chart,
            "metrics": {
                "tokens_in":        result.tokens_in + spent_in,
                "tokens_out":       result.tokens_out + spent_out,
                "llm_calls":        calls,
                "trajectory_steps": 1,
                "latency_ms":       elapsed_ms,
            },
        }

    except Exception as exc:
        logger.error("Synthesizer error for query_id=%s: %s", query_id, exc, exc_info=True)
        elapsed_ms = int(time.monotonic() * 1000) - start_ms
        # The reader gets a plain message; the error itself is in the log, never in the answer.
        fallback = {
            "answer_text": "The answer could not be written this time. Please try again.",
            "chart": None,
            "citations": citations,
        }
        safe_out = safe_synthesizer_output(fallback)
        return {
            "answer_text": safe_out["answer_text"],
            "vega_spec": None,
            "metrics": {
                "tokens_in":        0,
                "tokens_out":       0,
                "llm_calls":        1,
                "trajectory_steps": 1,
                "latency_ms":       elapsed_ms,
            },
        }

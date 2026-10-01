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

RAG v2 Phase E: when the docs route sets state.doc_abstained (nothing relevant was
retrieved and pipeline.doc_abstain is on), the answer is retrieval.abstain_message, built
here with no model call -- a model asked to answer from no passages is exactly where an
unsupported answer comes from.
"""

from __future__ import annotations

import json
import logging
import re
import time
from decimal import Decimal
from numbers import Real
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
   declarative charts only, and does not produce or execute code (D8).
10. Emit the raw JSON object only. Do not wrap it in markdown code fences."""


# The "short" experiment variant (flag pipeline.synth_style): the same rules and
# JSON shape, but a capped answer, summaries instead of row listings, and NO
# chart instructions at all. The browser draws the chart from the rows (as the
# dashboards do) and the full table is shown under the answer, so the tokens
# spent on chart shapes and on restating rows are pure cost. {max_sentences} is
# filled from config by _short_system_prompt().
_SYSTEM_PROMPT_SHORT = """\
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
   {"answer_text": "<string>", "chart": null, "citations": []}
6. answer_text is at most {max_sentences} sentences. When there are more than 3
   result rows, summarise them (the extremes, the total, the range) instead of
   listing rows: the full table is shown under your answer. State units. Follow
   the formatting hints in the data context.
7. chart is always null. Leave citations as an empty array -- it is filled in
   deterministically after your response, not by you.
8. NEVER include executable code in answer_text -- no Python, matplotlib,
   JavaScript, shell commands, or any other runnable program text, and no
   fenced code blocks. If the user asks for code, or asks you to run
   something, explain in prose that this system answers with data and
   declarative charts only, and does not produce or execute code (D8).
9. Emit the raw JSON object only. Do not wrap it in markdown code fences."""

_SECTION_SEP = " › "  # citation title: "{document title} › {section}" for a chunk under a heading
_PCT_NAME_WORDS = ("rate", "share", "ratio", "pct", "percent", "conversion", "fraction")


def _short_system_prompt() -> str:
    return _SYSTEM_PROMPT_SHORT.replace(
        "{max_sentences}", str(cfg.pipeline.synth_short_max_sentences)
    )


def _as_number(value: Any) -> float | int | None:
    """A JSON-ish cell as a number, or None when it is not numeric (bool is not)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Real):
        return value  # type: ignore[return-value]
    return None


def _numeric_values(rows: list[dict], column: str) -> list[float | int] | None:
    """Non-null values of a column when ALL of them are numeric, else None."""
    values: list[float | int] = []
    for row in rows:
        v = row.get(column)
        if v is None:
            continue
        n = _as_number(v)
        if n is None:
            return None
        values.append(n)
    return values or None


def _fmt_num(value: float | int) -> str:
    if isinstance(value, float):
        # Rounded so float noise (0.1 + 0.2) does not reach the prompt.
        value = round(value, 6)  # noqa: literals - display precision of a prompt number
    return str(value)


def column_summary(rows: list[dict], columns: list[str]) -> dict[str, dict[str, float | int]]:
    """
    count/min/max/sum per numeric column over ALL rows (not just the preview),
    computed in code so the model quotes totals and extremes instead of
    estimating them from a truncated sample.
    """
    out: dict[str, dict[str, float | int]] = {}
    for col in columns:
        values = _numeric_values(rows, col)
        if values is None:
            continue
        out[col] = {
            "count": len(values), "min": min(values), "max": max(values), "sum": sum(values),
        }
    return out


def percentage_hints(rows: list[dict], columns: list[str]) -> list[str]:
    """
    A formatting hint for each numeric column that looks like a proportion:
    every non-null value lies in [0, 1] and the name says rate/share/ratio/...
    Models otherwise print 0.0453 as-is or as "0.0453%".
    """
    hints: list[str] = []
    for col in columns:
        if not any(word in col.lower() for word in _PCT_NAME_WORDS):
            continue
        values = _numeric_values(rows, col)
        if values is None:
            continue
        if all(0 <= v <= 1 for v in values):  # noqa: literals - the unit interval
            hints.append(f"Present {col} as a percentage (e.g. 0.0453 → 4.5%).")
    return hints


def format_short_sql_context(sql_rows: list[dict], sql_columns: list[str]) -> str:
    """The short-style data context for a non-empty SQL result."""
    preview = sql_rows[:cfg.pipeline.synth_short_preview_rows]
    rows_text = json.dumps(preview, separators=(",", ":"), default=str)
    lines = [
        f"SQL query returned {len(sql_rows)} row(s).",
        f"Columns: {', '.join(sql_columns)}",
    ]
    summary = column_summary(sql_rows, sql_columns)
    if summary:
        lines.append(f"Column summary (all {len(sql_rows)} rows):")
        for col, st in summary.items():
            lines.append(
                f"  {col}: count={st['count']} min={_fmt_num(st['min'])} "
                f"max={_fmt_num(st['max'])} sum={_fmt_num(st['sum'])}"
            )
    hints = percentage_hints(sql_rows, sql_columns)
    if hints:
        lines.append("Formatting hints:")
        lines.extend(f"  {h}" for h in hints)
    lines.append(f"Results (first {len(preview)} rows):\n{rows_text}")
    return "\n".join(lines)


_A7_MARKS = ("bar", "line", "point", "area", "tick", "rect", "arc")
_A7_ENCODING_TYPES = ("quantitative", "nominal", "ordinal", "temporal")
_A7_AGGREGATES = ("sum", "mean", "count", "min", "max", "median")
_A7_ENCODING_PROPS = ("field", "type", "aggregate", "title", "sort")
_VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"


def is_chartable(sql_rows: list[dict], sql_columns: list[str]) -> bool:
    """
    Deterministic gate for E2 Deliverable 1's "where a chart is appropriate".
    A single scalar result is an answer, not a chart. Thresholds are config
    (A0.2), not literals.
    """
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
    """
    Assembles the final A7 chart from the model's proposed SHAPE plus the
    real result rows.

    The split is deliberate and load-bearing: the model chooses the mark and
    the encoding (what kind of chart, which column on which channel), and
    THIS CODE supplies `data.values` from the actual SQL result. The model is
    never asked for, and never trusted with, the data itself.

    Two reasons, both from the spec:
      - E2 Notes: "`data.values` is the actual result rows, capped at 5000."
      - Strategy §1: answers are "grounded in real rows". A model that emits
        its own data.values can fabricate a plausible-looking chart that
        contradicts the prose above it, and A7's schema would happily
        validate it — every value is a well-formed number. Generating the
        data in code makes that class of error impossible rather than
        unlikely.

    Every `field` referenced by an encoding must exist in `sql_columns`; a
    reference to a column that is not in the result is dropped, because
    Vega-Lite renders a missing field as a silently empty channel.
    Returns None whenever a conformant chart cannot be built — never a
    partial or repaired one (A7: "It never returns a repaired chart").
    """
    if not isinstance(proposed, dict):
        return None

    mark = proposed.get("mark")
    if mark not in _A7_MARKS:
        if mark is not None:
            logger.info(
                "Synthesizer proposed mark %r for query_id=%s, which is not in the A7 enum; chart dropped.",
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
    kind:"sql" only when a statement actually reached the database (success
    or engine failure). A citation asserts provenance, so it may only point
    at a statement the database actually saw; a statement refused before
    execution has no provenance to assert, and citing it puts unvalidated
    model output on screen as evidence. Engine failures retain their citation
    because citing what reached the engine explains engine rejections.
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
    reached_engine = bool((sql_error or {}).get("engine_code"))
    sql_ref = executed_sql or ((sql_error or {}).get("offending_sql") if reached_engine else None)
    if sql_ref:
        citations.append({"kind": "sql", "ref": sql_ref, "page": None})

    for r in retrievals[: cfg.retrieval.citation_max_chunks]:
        prov = r["provenance"]
        citation = {"kind": "chunk", "ref": prov["chunk_id"], "page": prov.get("page")}
        if prov.get("title"):
            citation["title"] = _SECTION_SEP.join(filter(None, (prov["title"], prov.get("section"))))
        citations.append(citation)

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
    short_style = (
        state_dict.get("flags_snapshot", {}).get("pipeline.synth_style", "full") == "short"
    )

    if state_dict.get("doc_abstained"):
        # RAG v2 Phase E: the docs route found nothing relevant (pipeline.doc_abstain). The answer
        # is the fixed message: no model call, so nothing to invent; no citations (there is no
        # passage and no SQL to cite) and no chart. It still goes through the A7 validator, like
        # every other answer, and it holds no figure for number grounding to trace.
        start_ms = int(time.monotonic() * 1000)
        safe_out = safe_synthesizer_output({
            "answer_text": cfg.retrieval.abstain_message, "chart": None, "citations": [],
        })
        logger.info("Synthesizer abstained for query_id=%s (no model call).", query_id)
        return {
            "answer_text": safe_out["answer_text"],
            "vega_spec": None,
            "metrics": {
                "tokens_in": 0, "tokens_out": 0, "llm_calls": 0, "trajectory_steps": 1,
                "latency_ms": int(time.monotonic() * 1000) - start_ms,
            },
        }

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
        if sql_rows and short_style:
            context_parts.append(format_short_sql_context(sql_rows, sql_columns))
        elif sql_rows:
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
            context_parts.append("The SQL query returned zero rows. The answer may be 'none' or 'zero'.")

    if retrievals:
        context_parts.append(_format_doc_context(retrievals))

    caveats: list[str] = state_dict.get("caveats") or []
    if caveats:
        context_parts.append("Caveats (mention them briefly to the user): " + " ".join(caveats))

    if not context_parts:
        context_parts.append("No data was retrieved for this question.")

    user_message = f"Question: {raw_query}\n\nData context:\n" + "\n\n".join(context_parts)

    llm = get_agent_llm("synthesizer")

    try:
        result = await llm.complete(
            system=_short_system_prompt() if short_style else _SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=cfg.sql.max_tokens_synthesizer,
        )
        elapsed_ms = int(time.monotonic() * 1000) - start_ms

        # Parse the JSON response (fences stripped first -- a fenced response
        # would otherwise fall through to the plain-text branch below and put
        # the whole raw JSON blob into answer_text).
        try:
            # strict=False allows raw control characters inside string values.
            # The model routinely writes a multi-line answer_text with LITERAL
            # newlines rather than the \n escape JSON requires. That is invalid
            # JSON by the letter of the spec, and rejecting it threw away an
            # otherwise perfectly good structured response -- including its
            # chart. Diagnosed from E2-V1: it accounted for 7 of the 8
            # chart:null results, every one of them on a result set that had
            # passed the chartability gate. Nothing else is relaxed; a genuinely
            # malformed object still raises and still falls through below.
            parsed = json.loads(_strip_json_fences(result.text), strict=False)
        except json.JSONDecodeError:
            # Second chance before giving up: a model that prefaces its JSON
            # with a sentence of prose ("Here is the response:") produces text
            # that is not itself JSON but CONTAINS the object. Falling straight
            # to plain text there discards a perfectly good structured
            # response -- and with it the chart, which is how E2-V1 lost 7 of
            # its 8 chart:null results on the local model.
            parsed = _extract_json_object(result.text)
            if parsed is None:
                logger.warning(
                    # Log the evidence, not just the fact. The earlier version
                    # recorded that parsing failed and threw away WHAT failed,
                    # which is the same defect as the evaluator's empty-string
                    # error strings -- a log that cannot diagnose its own event.
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

        # E2 Deliverable 1. The model proposed only the chart SHAPE; the real
        # rows are attached here, in code (see build_chart's docstring for why
        # the model is never trusted with data.values). Gated on the same
        # deterministic chartability test the prompt was told about, so a
        # model that proposes a chart for a single scalar still gets null.
        # In the short style the server never draws: the browser builds the
        # chart from the rows, so a chart the model proposes anyway is dropped.
        parsed["chart"] = (
            build_chart(parsed.get("chart"), sql_rows, sql_columns, query_id)
            if is_chartable(sql_rows, sql_columns) and not short_style else None
        )
        parsed["citations"] = citations

        # Validate against A7
        safe_out = safe_synthesizer_output(parsed)
        answer_text = _strip_executable_content(safe_out["answer_text"], query_id)
        answer_text = _ensure_category_mentioned(answer_text, sql_error)

        # safe_out["chart"] is the A7-VALIDATED chart: safe_synthesizer_output
        # nullifies it (never repairs it) if it failed the schema or the BD7
        # expression audit. main.py validates the assembled payload a second
        # time at the response boundary, and the frontend a third time before
        # rendering (A7's two-independent-validations rule, E2-V6).
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

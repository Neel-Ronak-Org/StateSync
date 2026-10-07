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

Interrogation targeting (Phase-E gap packet, Task 4): on the miss path the
agent drafts its query first, parses that draft's own FROM/JOIN tables with
pglast, and samples only those — replacing the original schema-wide rule,
which sampled the same first four tables in schema order for every question
regardless of what the question asked. Strategy §4.2 describes interrogation
as verifying "its hypotheses about the data's actual shape," which presumes
a hypothesis; the draft is that hypothesis. Cost: one extra generation call
on the miss path (none on the memory-hit path, none on retries, and none
when the test harness injects debug_candidate_sql — that injected SQL is
reused as the draft). Because the miss path is the M1 control arm, this
shifts M1's control baseline; see docs/phase_e_gap_log.md.

Phase D additions: DigipinResolver (spatial_resolver.py) runs inline here,
every attempt (D1 Callout 2 -- it is a deterministic, non-LLM, non-MCP
pre-step, so unlike memory/interrogation there is no cost reason to gate it
to attempt 0). When it finds a DIGIPIN token in the normalized query, its
ready-made ST_DWithin predicate is appended to the prompt context so the
model doesn't have to get SRID/geography casts right unprompted (C2 already
showed it doesn't reliably). preserved_entities (D1) is similarly appended
as a verbatim-literal instruction, including the _local-column redirect for
Devanagari values -- see language_normalizer.py's module docstring for why
that column exists.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import answer_checks
import context_carry
import httpx
import repair_loop
import self_consistency
import sql_outcomes
import templates as sql_templates
from column_profiles import format_profiles, get_profiles
from config_loader import cfg
from classifiers.error_classifier import classify_error
from llm.provider import get_agent_llm
from table_selection import tables_in_sql
from mcp_client import MCPSession, data_run_select, data_sample_rows, schema_describe_table, schema_list_tables, workspaces_manage
from memory_store import get_trajectory_store
from schema_retriever import format_table_block, get_schema_retriever, target_schema
from profiles import profile_for
from profiles_ondc import SQL_DOMAIN as _ONDC_SQL_DOMAIN
from spatial_resolver import get_spatial_resolver
from workspace_context import format_context, system_suffix

logger = logging.getLogger(__name__)

# Same parser the mcp Layer-1 gate uses (A4.5), here for a read-only,
# non-security-bearing purpose: reading the draft SQL's own FROM/JOIN targets
# so interrogation samples the right tables. Degrading to the schema-wide
# fallback if it is unavailable is correct — unlike in mcp, nothing here is a
# security boundary, so an absent parser costs targeting precision and nothing
# else.
try:
    import pglast
    from pglast.visitors import Visitor
    _PGLAST_AVAILABLE = True
except ImportError:  # pragma: no cover — dependency is pinned in pyproject.toml
    _PGLAST_AVAILABLE = False
    Visitor = object  # type: ignore[assignment,misc]
    logger.warning(
        "pglast unavailable — interrogation targets fall back to schema-wide selection."
    )

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
6. Return ONLY the columns the question actually asks for. If it asks
   "which sellers", return the seller identifier (plus a name if the answer
   would be unreadable without it) -- not every column of the table. Selecting
   extra columns "for context" makes the result a different answer.
7. Return ONLY the SQL query text — no markdown, no explanation, no comments."""

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
6. Return ONLY the columns the question actually asks for. If it asks
   "which sellers", return the seller identifier (plus a name if the answer
   would be unreadable without it) -- not every column of the table. Selecting
   extra columns "for context" makes the result a different answer.
7. Directly address every remediation hint below — do not repeat a mistake a hint
   already explained.
8. Return ONLY the SQL query text — no markdown, no explanation, no comments."""

# [pipeline] sql_row_cap. "prompt" (shipped) asks the model for LIMIT max_rows; in the BIRD pilot
# that instruction also replaced the LIMIT 1 of "which is the highest" questions (97 of 100 queries
# ended in LIMIT 1000; docs/eval/bird_pilot_report.md). "executor" leaves the cap to data.run_select,
# which never returns more than max_rows rows whatever the SQL says.
_PROMPT_LIMIT_RULE = "Apply LIMIT {max_rows} unless the question explicitly asks for all rows."
_EXECUTOR_LIMIT_RULE = ('Use LIMIT only when the question asks for a number of results ("the highest", '
                        '"the top 5"); the system itself caps how many rows are returned.')


def sql_system_prompt(template: str, max_rows: int, schema: Any, flags_snapshot: dict[str, Any]) -> str:
    """The SQL writer's system prompt: the row-cap rule the flags choose, then the dataset profile's
    domain phrase in place of the ONDC one the two templates are written with."""
    if flags_snapshot.get("pipeline.sql_row_cap", "prompt") == "executor":
        template = template.replace(_PROMPT_LIMIT_RULE, _EXECUTOR_LIMIT_RULE)
    prompt = template.format(max_rows=max_rows).replace(_ONDC_SQL_DOMAIN, profile_for(schema).sql_domain)
    if sql_outcomes.sql_no_data_on(flags_snapshot):
        # [pipeline] sql_no_data (F-S1): the writer and the corrector may say the database cannot answer.
        prompt += "\n" + sql_outcomes.not_answerable_rule(prompt)
    return prompt


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


def _strip_sql_fences(text: str) -> str:
    """Strip Markdown code fences the model sometimes wraps its SQL in."""
    if text.startswith("```"):
        return "\n".join(
            line for line in text.split("\n") if not line.startswith("```")
        ).strip()
    return text


def _parse_referenced_tables(sql: str) -> list[str]:
    """
    Extract the table names a SQL statement actually references, in first-seen
    order, using the same real PostgreSQL parser the mcp Layer-1 gate uses
    (pglast). Every FROM/JOIN target is a `RangeVar` node in the parse tree, so
    this catches subquery and CTE-body references too, not just the top-level
    FROM.

    Returns [] on anything unparseable -- callers treat that as "no targets
    identified" and fall back to schema-wide selection rather than
    interrogating nothing.

    CTE *names* also surface as RangeVars where they are referenced. They are
    harmless here: the caller intersects this list against the real table set
    from schema.list_tables, so a CTE alias simply never matches.
    """
    if not _PGLAST_AVAILABLE or not sql.strip():
        return []
    try:
        tree = pglast.parse_sql(sql)
    except Exception as exc:  # pglast.Error and anything it wraps
        logger.debug("Could not parse draft SQL for interrogation targeting: %s", exc)
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
    """A sample of the target's columns, or None when the MCP refuses it: samples only help
    the SQL model, so one refused sample must never fail the question (found on BIRD, 2026-10-03)."""
    try:
        return await data_sample_rows(session, target["table"], columns=target["columns"], schema=schema)
    except httpx.HTTPStatusError as exc:
        logger.warning("SQL Agent: sample of %s refused (HTTP %s) for query_id=%s; skipped",
                       target["table"], exc.response.status_code, query_id)
        return None


def question_for(state_dict: dict[str, Any]) -> str:
    """The question this agent answers. On the hybrid route (graph.py) it is only the SQL part of a
    compound question, subtasks[0] from hybrid_plan_node, answered with everything a data-only
    question gets (workspace knowledge, fast path, templates, the correction loop)."""
    if state_dict.get("route") == "hybrid" and state_dict.get("subtasks"):
        return str(state_dict["subtasks"][0])
    return state_dict.get("normalized_query") or state_dict.get("raw_query", "")


def _example_tables(ws_ctx: dict[str, Any] | None) -> list[str]:
    """Tables the workspace's matched approved examples read: always shown by table selection
    (docs/specs/table_selection.md), because an approved query is the strongest relevance signal."""
    found: list[str] = []
    for ex in (ws_ctx or {}).get("examples") or []:
        for t in tables_in_sql(str(ex.get("sql") or "")):
            if t not in found:
                found.append(t)
    return found


def _pick_interrogation_targets(
    table_descriptions: list[dict],
    cap: int,
    referenced_tables: list[str] | None = None,
) -> list[dict]:
    """
    Interrogation-target selection (C2 Scope), capped at `cap` tables -- one
    data.sample_rows call per selected table (A4.3's own cap is on rows per
    call, not calls; the "up to interrogation_max_samples" bound in the spec is
    read here as a bound on the number of sample_rows calls).

    Targets are the tables the planned query actually references, parsed from
    the draft SQL by `_parse_referenced_tables` and intersected here against
    the real tables from schema.list_tables -- so sampling verifies the shape
    of the data the query will really touch. Strategy §4.2 frames interrogation
    as "verify its hypotheses about the data's actual shape", which presumes a
    hypothesis exists before sampling; the draft is that hypothesis.

    Fallback (never a regression): if no referenced table can be parsed, or
    none of them intersect the real schema, this reverts to the original
    schema-wide rule -- tables with at least one nullable column, in schema
    order. That path is still exercised whenever the draft call fails or emits
    something unparseable.

    Column selection within a chosen table prefers nullable columns (the ones
    whose stored shape is actually in question). A targeted table with no
    nullable column is still sampled, with `columns` omitted so the server
    returns all of them -- dropping the target because it happens to be fully
    NOT NULL would defeat the point of targeting it.

    Each returned target carries `columns` (what is sent to A4.3 -- None means
    "omit the argument, let the server return every column") and
    `columns_display` (always concrete, for the prompt and the state record).
    """
    by_name = {desc["table"]: desc for desc in table_descriptions}

    def _target_for(desc: dict) -> dict:
        all_cols = [c["name"] for c in desc.get("columns", [])]
        nullable_cols = [c["name"] for c in desc.get("columns", []) if c.get("nullable")]
        return {
            "table": desc["table"],
            "columns": nullable_cols or None,
            "columns_display": nullable_cols or all_cols,
        }

    if referenced_tables:
        targets: list[dict] = []
        for name in referenced_tables:
            desc = by_name.get(name)
            if desc is None:
                continue  # CTE alias, or a table the model invented
            targets.append(_target_for(desc))
            if len(targets) >= cap:
                break
        if targets:
            return targets

    # Fallback: original schema-wide behavior.
    targets = []
    for desc in table_descriptions:
        if not any(c.get("nullable") for c in desc.get("columns", [])):
            continue
        targets.append(_target_for(desc))
        if len(targets) >= cap:
            break
    return targets


def _format_interrogation_samples(samples: list[dict]) -> str:
    parts = ["Sampled data (verify actual stored shape/format before writing filters):"]
    for s in samples:
        preview = json.dumps(s["rows"], default=str)
        parts.append(f"  {s['table']} ({', '.join(s['columns_sampled'])}): {preview}")
    return "\n".join(parts)


def _format_entity_hint(preserved_entities: dict[str, str]) -> str:
    if not preserved_entities:
        return ""
    lines = [
        "Preserved entities -- these are exact-match literals taken verbatim "
        "from the user's own query (D1). Use them exactly as given in WHERE "
        "clauses; do not translate, transliterate, or alter them. Check each "
        "value's own script before choosing a column:",
        "  - Devanagari script (e.g. विक्रेता 5, मुंबई) -> match against the table's "
        "_local column (sellers.display_name_local, locations.city_local, "
        "catalog_items.title_local).",
        "  - Latin/English script (e.g. Seller Store 5, Mumbai) -> match against "
        "the table's standard column (sellers.display_name, locations.city, "
        "catalog_items.title) as usual. Do NOT use a _local column for a "
        "Latin-script value.",
        "Values to preserve:",
    ]
    for original in preserved_entities.keys():
        lines.append(f"  '{original}'")
    return "\n".join(lines)


def _spatial_resolution(raw_query: str, schema: str | None) -> dict[str, Any] | None:
    """The DIGIPIN resolver's result, only for a dataset whose profile has it (the ONDC sample)."""
    if not profile_for(schema).spatial_resolver:
        return None
    return get_spatial_resolver().resolve(raw_query)


def _format_spatial_hint(spatial: dict[str, Any]) -> str:
    return (
        f"Spatial context (D1): the query references location code "
        f"{spatial['digipin']}, decoded to latitude {spatial['lat']}, "
        f"longitude {spatial['lon']}. Use exactly this predicate for the "
        f"spatial filter -- adjust only the table alias to whatever alias "
        f"you assign to the locations table in this query:\n"
        f"  {spatial['predicate_hint']}"
    )


def _format_error_history(errors: list[dict]) -> str:
    parts = ["Previous attempt(s) failed. Do not repeat the same mistake:"]
    for e in errors:
        parts.append(
            f"  Attempt {e['attempt']}: SQL: {e.get('offending_sql') or '(none)'}\n"
            f"    Error ({e['category']}, engine_code={e.get('engine_code') or 'n/a'}): {e['message']}\n"
            f"    Hint: {e['remediation_hint']}"
        )
    return "\n".join(parts)


# Unicode Devanagari block U+0900–U+097F
# Script/column mismatch repair (Workstream B1 defect)
_DEVANAGARI_START = 0x0900  # noqa: literals — Unicode Devanagari block start (U+0900)
_DEVANAGARI_END = 0x097F    # noqa: literals — Unicode Devanagari block end (U+097F)


def _is_indic_literal(text: str) -> bool:
    """
    Classify the literal: it is INDIC if it contains any character in the
    Devanagari block U+0900–U+097F, otherwise LATIN.
    """
    return any(_DEVANAGARI_START <= ord(ch) <= _DEVANAGARI_END for ch in text)


def _derive_paired_columns(
    table_descriptions: list[dict],
) -> tuple[dict[str, str], dict[str, str]]:
    """
    Build the set of PAIRED columns from table_descriptions (the A4.2 describe_table dicts):
    a column named `<base>_local` is paired iff a column named `<base>` exists in the SAME table.
    Never assume the three known pairs — derive them, so the repair is a no-op on a schema
    without this convention (e.g. the olist corpus has no _local columns at all).
    """
    local_to_base: dict[str, str] = {}
    base_to_local: dict[str, str] = {}
    for desc in table_descriptions:
        cols = {
            c["name"]
            for c in desc.get("columns", [])
            if isinstance(c, dict) and "name" in c
        }
        for col in cols:
            if col.endswith("_local"):
                base = col[:-len("_local")]
                if base in cols:
                    local_to_base[col] = base
                    base_to_local[base] = col
    return local_to_base, base_to_local


def _repair_script_column_mismatch(
    sql: str, table_descriptions: list[dict],
) -> tuple[str, list[str]]:
    """
    Returns (possibly rewritten sql, list of human-readable repair notes).

    === WHY THE REPAIR IS PROVABLY SAFE (verified in the live database) ===
      locations.city_local        51 rows, 0 Latin-only
      locations.city              51 rows, 0 non-ASCII
      sellers.display_name_local  30 rows, 0 Latin-only
      sellers.display_name        30 rows, 0 non-ASCII
      catalog_items.title_local  100 rows, 0 Latin-only
      catalog_items.title        100 rows, 0 non-ASCII

    So a script/column mismatch predicate is GUARANTEED to match zero rows.
    Rewriting it onto the paired column cannot turn a correct query into an
    incorrect one, because the original could not have been correct.

    Four earlier attempts to fix this by INFLUENCING the model all failed and
    are documented: column comments (migration 005, measured harmful, reverted),
    a prompt instruction (_format_entity_hint, still shipped, still disobeyed),
    sampled evidence, and interrogation column selection. This repair is
    deliberately MECHANICAL and involves no model call.
    """
    if not _PGLAST_AVAILABLE or not sql.strip():
        return sql, []

    local_to_base, base_to_local = _derive_paired_columns(table_descriptions)
    if not local_to_base:
        return sql, []

    prefix = ""
    try:
        tree = pglast.parse_sql(sql)
    except Exception:
        try:
            tree = pglast.parse_sql("SELECT 1 " + sql)
            prefix = "SELECT 1 "
        except Exception:
            return sql, []

    parse_text = prefix + sql
    try:
        tokens = list(pglast.scan(parse_text))
    except Exception:
        return sql, []

    replacements: list[tuple[int, int, str, str]] = []
    seen_spans: set[tuple[int, int]] = set()

    class _PredicateVisitor(Visitor):
        def visit_A_Expr(self, ancestors, node):  # noqa: N802 — pglast visitor naming
            op = "".join(getattr(x, "sval", "") for x in getattr(node, "name", ()))
            if op != "=":
                return
            col_ref, const_node = None, None
            if isinstance(node.lexpr, pglast.ast.ColumnRef) and isinstance(node.rexpr, pglast.ast.A_Const):
                col_ref, const_node = node.lexpr, node.rexpr
            elif isinstance(node.rexpr, pglast.ast.ColumnRef) and isinstance(node.lexpr, pglast.ast.A_Const):
                col_ref, const_node = node.rexpr, node.lexpr
            if not col_ref or not const_node:
                return
            literal = getattr(getattr(const_node, "val", None), "sval", None)
            if literal is None:
                return
            fields = [getattr(f, "sval", None) for f in getattr(col_ref, "fields", ())]
            if not fields:
                return
            col_name = fields[-1]
            if not col_name:
                return

            is_indic = _is_indic_literal(literal)
            new_col = None
            note = None

            if col_name in local_to_base and not is_indic:
                new_col = local_to_base[col_name]
                note = f"{col_name} -> {new_col} (Latin literal cannot match an Indic-script column)"
            elif col_name in base_to_local and is_indic:
                new_col = base_to_local[col_name]
                note = f"{col_name} -> {new_col} (Indic literal cannot match a Latin-script column)"

            if new_col:
                start_indices = [i for i, t in enumerate(tokens) if t.start == col_ref.location]
                if not start_indices:
                    return
                start_idx = start_indices[0]
                col_tok_idx = start_idx + (len(fields) - 1) * 2
                if col_tok_idx < len(tokens):
                    target_tok = tokens[col_tok_idx]
                    start = target_tok.start - len(prefix)
                    end = target_tok.end + 1 - len(prefix)
                    if (start, end) in seen_spans:
                        return
                    seen_spans.add((start, end))
                    raw_token = sql[start:end]
                    replacement_token = f'"{new_col}"' if raw_token.startswith('"') and raw_token.endswith('"') else new_col
                    replacements.append((start, end, replacement_token, note))

    _PredicateVisitor()(tree)

    if not replacements:
        return sql, []

    # Sort descending so right-to-left replacements preserve earlier string offsets
    replacements.sort(key=lambda r: r[0], reverse=True)
    rewritten_sql = sql
    collected_notes: list[str] = []
    for start, end, rep_tok, note in replacements:
        rewritten_sql = rewritten_sql[:start] + rep_tok + rewritten_sql[end:]
        collected_notes.append(note)
    collected_notes.reverse()

    # Step 6: Validate re-parse
    validation_text = prefix + rewritten_sql if prefix else rewritten_sql
    try:
        pglast.parse_sql(validation_text)
    except Exception as exc:
        return sql, [f"Repair discarded: rewritten SQL failed pglast validation ({exc})"]

    return rewritten_sql, collected_notes


async def _try_template(
    session: MCPSession, state_dict: dict[str, Any], question: str, max_rows: int,
) -> tuple[str | None, dict[str, Any] | None, int, int, int]:
    """Phase 3c: answer from a template of an approved query (pipeline.templates = "on").

    Returns (sql, exec_result, tokens_in, tokens_out, model_calls); sql is None whenever
    the template route did not produce rows, and the caller then runs the normal path in
    the same attempt. Nothing here records an error: a template that does not fit is not
    a failed attempt. One short decomposer-role call picks the template and reads the
    values; the SQL itself is assembled in code (orchestrator/templates.py) and executed
    through the same MCP gate as any query.
    """
    query_id = state_dict.get("query_id", "")
    found = await workspaces_manage(session, principal_id=state_dict["principal_id"], action="templates",
                                    workspace_id=state_dict["workspace_id"], question=question)
    candidates = found.get("templates") if found.get("ok") else None
    if not candidates:
        return None, None, 0, 0, 0
    result = await get_agent_llm("decomposer").complete(
        system=sql_templates.MATCHER_PROMPT,
        messages=[{"role": "user", "content": sql_templates.format_candidates(question, candidates)}],
        max_tokens=cfg.templates.matcher_max_tokens,
    )
    spent = (result.tokens_in, result.tokens_out, 1)
    match = sql_templates.parse_match(result.text, candidates)
    if match is None:
        logger.info("SQL Agent templates: no match for query_id=%s", query_id)
        return (None, None, *spent)
    chosen, values = match
    filled = sql_templates.fill_template(chosen.get("template_sql"), chosen.get("slots"), values)
    if filled is None:
        logger.info("SQL Agent templates: template %s not fillable for query_id=%s", chosen.get("template_id"), query_id)
        return (None, None, *spent)
    exec_result = await data_run_select(session, filled, max_rows=max_rows)
    if not (exec_result.get("ok") and exec_result.get("rows")):
        logger.info("SQL Agent templates: template %s gave no rows for query_id=%s", chosen.get("template_id"), query_id)
        return (None, None, *spent)
    return (filled, exec_result, *spent)


async def sql_agent_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    """
    SQL Agent node. Attempt 0: consults memory, interrogates (miss path
    only), generates SQL, executes. Attempt >= 1: full schema + accumulated
    error history, no memory, no interrogation. Classifies any failure via
    A3 and appends an ErrorRecord; the graph edge (not this function)
    decides whether to loop back.
    """
    raw_query: str = question_for(state_dict)
    context_line: str = context_carry.question_context(state_dict)   # "" unless pipeline.context_carry gave a follow-up its context
    tenant_id: str = state_dict["tenant_id"]
    query_id: str = state_dict.get("query_id", "")
    session_id: str = state_dict.get("session_id", "")
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

    async with MCPSession(tenant_id=tenant_id, base_url=mcp_base_url, session_id=session_id,
                          dataset=state_dict.get("target_schema")) as session:
        # Always needed: fingerprint (memory key on attempt 0) and table list.
        _schema = state_dict.get("target_schema") or target_schema()
        tables_result = await schema_list_tables(session, _schema)
        schema_fingerprint: str = tables_result.get("fingerprint", "")
        table_names = [t["name"] for t in tables_result.get("tables", [])]
        trajectory_steps += 1

        memory_hit = False
        memory_trace_id: str | None = None
        new_interrogation_samples: list[dict] = []
        table_descriptions: list[dict] = []

        # Phase 3: a question asked in a workspace gets that workspace's
        # APPROVED knowledge (workspace_context.py), and an exact match of an
        # approved example runs its SQL directly -- the fast path, attempt 0
        # only and never during the setup test (allow_fast_path=False).
        workspace_id = state_dict.get("workspace_id")
        ws_ctx: dict[str, Any] = {}
        if workspace_id:
            ws_ctx = await workspaces_manage(session, principal_id=state_dict["principal_id"], action="context",
                                             workspace_id=workspace_id, question=raw_query)
            if not ws_ctx.get("ok"):
                logger.warning("SQL Agent: workspace context unavailable for query_id=%s: %s",
                               query_id, ws_ctx.get("message"))
                ws_ctx = {}
        ws_block = format_context(ws_ctx, None, int(cfg.workspaces.context_max_chars)) if ws_ctx else ""
        ws_suffix = system_suffix(ws_ctx.get("business_context")) if ws_ctx else ""
        exact_sql: str | None = None
        if attempt == 0 and state_dict.get("allow_fast_path", True) and not state_dict.get("debug_candidate_sql"):
            exact_sql = (ws_ctx.get("exact") or {}).get("sql")

        # Phase 3c: no exact example, so try a template of a near one (one short
        # decomposer call, no SQL-model call). On any miss the normal path runs below.
        compiled_sql: str | None = None
        compiled_exec: dict[str, Any] | None = None
        if (
            attempt == 0 and workspace_id and not exact_sql
            and flags_snapshot.get("pipeline.templates", "off") == "on"
            and state_dict.get("allow_fast_path", True) and not state_dict.get("debug_candidate_sql")
        ):
            try:
                compiled_sql, compiled_exec, t_in, t_out, t_calls = await _try_template(
                    session, state_dict, raw_query, cfg.mcp.max_rows_default)
                tokens_in_total += t_in
                tokens_out_total += t_out
                llm_calls_total += t_calls
            except Exception as exc:  # noqa: BLE001 -- a template is an optimisation, never a failure
                logger.warning("SQL Agent templates skipped for query_id=%s: %s", query_id, exc)
                compiled_sql = compiled_exec = None

        if attempt == 0 and (exact_sql or compiled_sql):
            # Fast path (or compiled template): no memory, no draft, no interrogation.
            context_block = ""
        elif attempt == 0:
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
                if ws_block:
                    context_block += "\n\n" + ws_block
            else:
                retriever = get_schema_retriever()
                schema_context, table_descriptions = await retriever.retrieve(
                    session, table_names, _schema, question=raw_query, attempt=attempt,
                    must_include=_example_tables(ws_ctx), fingerprint=schema_fingerprint)
                trajectory_steps += len(table_names)
                context_block = f"Schema:\n{schema_context}"
                if ws_block:
                    context_block += "\n\n" + ws_block

                draft_mode = str(flags_snapshot.get("pipeline.sql_draft_mode", "llm"))
                if draft_mode == "profiles" and not state_dict.get("debug_candidate_sql"):
                    # One-call variant: cached column profiles replace the throwaway
                    # draft call and the live sampling (orchestrator/column_profiles.py).
                    # Live sampling returns only after an empty_result (retry branch).
                    profiles = await get_profiles(
                        session, _schema, schema_fingerprint, table_names, table_descriptions,
                    )
                    profile_block = format_profiles(profiles, int(cfg.pipeline.profile_max_chars))
                    if profile_block:
                        context_block += "\n\n" + profile_block
                else:
                    # Step 2: draft the query BEFORE interrogating, so interrogation
                    # can target the tables this question actually touches rather
                    # than the first few tables in schema order (which was blind to
                    # the question and sampled the same four tables every time).
                    # This costs one extra generation call on the miss path only --
                    # recorded in docs/phase_e_gap_log.md, since it shifts the M1
                    # control baseline and must not be read as a memory-path win.
                    #
                    # The test harness's injected candidate doubles as the draft
                    # when present, so seeded-SQL tests (C2-V1..V4) get targeted
                    # sampling for free and their call counts are unchanged.
                    draft_sql: str = state_dict.get("debug_candidate_sql") or ""
                    if not draft_sql:
                        draft_result = await get_agent_llm("sql").complete(
                            system=sql_system_prompt(_SYSTEM_PROMPT, cfg.mcp.max_rows_default,
                                                     _schema, flags_snapshot) + ws_suffix,
                            messages=[{"role": "user", "content": (
                                f"{context_block}\n\nQuestion: {raw_query}{context_line}\n\n"
                                f"Write a single PostgreSQL SELECT query to answer this question."
                            )}],
                            max_tokens=cfg.sql.max_tokens_sql,
                        )
                        tokens_in_total += draft_result.tokens_in
                        tokens_out_total += draft_result.tokens_out
                        llm_calls_total += 1
                        draft_sql = _strip_sql_fences(draft_result.text)

                    referenced_tables = _parse_referenced_tables(draft_sql)
                    logger.info(
                        "SQL Agent interrogation targeting for query_id=%s: draft references %s",
                        query_id,
                        referenced_tables or "(nothing parseable -- schema-wide fallback)",
                    )

                    # Step 3: active interrogation (miss path, attempt 0 only).
                    targets = _pick_interrogation_targets(
                        table_descriptions,
                        cfg.sql.interrogation_max_samples,
                        referenced_tables=referenced_tables,
                    )
                    for target in targets:
                        sample_result = await _sample_or_skip(session, target, _schema, query_id)
                        if sample_result is None:
                            continue
                        new_interrogation_samples.append({
                            "table": target["table"],
                            "columns_sampled": target["columns_display"],
                            "rows": sample_result.get("rows", []),
                        })
                        trajectory_steps += 1

                    if new_interrogation_samples:
                        context_block += "\n\n" + _format_interrogation_samples(new_interrogation_samples)
        else:
            # Retry: full schema always (regardless of attempt-0 hit/miss),
            # no memory re-consultation, no interrogation (already in state
            # from attempt 0, if any -- not re-sampled).
            retriever = get_schema_retriever()
            schema_context, table_descriptions = await retriever.retrieve(
                session, table_names, _schema, question=raw_query, attempt=attempt,
                must_include=_example_tables(ws_ctx), fingerprint=schema_fingerprint)
            trajectory_steps += len(table_names)
            context_block = f"Schema:\n{schema_context}"
            if sql_outcomes.sql_no_data_on(flags_snapshot) and sql_outcomes.confirmation_pending(existing_errors):
                # T2b/T2c: the last attempt said NOT_ANSWERABLE. This one is shown every table of the
                # dataset (the retrieved ones first, so a cut drops the least relevant), then the columns
                # whose names resemble words in the question or that reply (every described table, cut or not).
                shown_names = {d.get("table") for d in table_descriptions}
                blocks = [format_table_block(str(d.get("table")), d, _schema) for d in table_descriptions]
                for tname in table_names:
                    if tname in shown_names:
                        continue
                    try:
                        extra = await schema_describe_table(session, tname, _schema)
                    except httpx.HTTPStatusError as exc:
                        logger.warning("SQL Agent: describe_table(%s) refused (%s); left out of the full view",
                                       tname, exc.response.status_code)
                        continue
                    trajectory_steps += 1
                    table_descriptions = [*table_descriptions, extra]
                    blocks.append(format_table_block(tname, extra, _schema))
                view, described = sql_outcomes.cut_schema_view(blocks, int(cfg.sql.full_schema_max_chars))
                context_block = f"Schema (every table of the dataset):\n{view}"
                if described < len(blocks):
                    context_block += (f"\n\n(The description is cut at {cfg.sql.full_schema_max_chars} characters: "
                                      f"{described} of {len(blocks)} tables are described.)")
                near_block = sql_outcomes.near_match_block(
                    raw_query, str(existing_errors[-1].get("message") or ""), table_descriptions,
                    int(cfg.sql.near_match_max))
                if near_block:
                    context_block += "\n\n" + near_block
            if ws_block:
                context_block += "\n\n" + ws_block

            # "profiles" draft mode: attempt 0 ran on cached profiles alone, so
            # the first live sampling happens here, and only when the failure
            # was a valid-but-empty result (a literal that matches no stored
            # value). Targets are the tables the failed SQL referenced.
            last_err = existing_errors[-1]
            if (
                flags_snapshot.get("pipeline.sql_draft_mode", "llm") == "profiles"
                and last_err.get("category") == "empty_result"
            ):
                failed_sql = last_err.get("offending_sql") or state_dict.get("candidate_sql") or ""
                for target in _pick_interrogation_targets(
                    table_descriptions,
                    cfg.sql.interrogation_max_samples,
                    referenced_tables=_parse_referenced_tables(failed_sql),
                ):
                    sample_result = await _sample_or_skip(session, target, _schema, query_id)
                    if sample_result is None:
                        continue
                    new_interrogation_samples.append({
                        "table": target["table"],
                        "columns_sampled": target["columns_display"],
                        "rows": sample_result.get("rows", []),
                    })
                    trajectory_steps += 1
                if new_interrogation_samples:
                    context_block += "\n\n" + _format_interrogation_samples(new_interrogation_samples)

        # Phase D: entity-preservation + spatial predicate hints, appended
        # regardless of which branch above built context_block (memory hit,
        # memory miss, or retry) -- every attempt, since both are cheap,
        # deterministic, non-LLM lookups (unlike memory/interrogation, which
        # stay gated to attempt 0).
        preserved_entities: dict[str, str] = state_dict.get("preserved_entities", {})
        entity_hint = _format_entity_hint(preserved_entities)
        if entity_hint:
            context_block += "\n\n" + entity_hint

        spatial_resolution = _spatial_resolution(raw_query, _schema)
        if spatial_resolution:
            context_block += "\n\n" + _format_spatial_hint(spatial_resolution)

        max_rows = cfg.mcp.max_rows_default

        # Step 4: generate SQL -- debug override (attempt 0, test harness
        # only) or the provider seam.
        debug_sql = state_dict.get("debug_candidate_sql")
        verified_example = False
        used_debug_sql = False
        sc_prompt: tuple[str, str] | None = None   # the model-written SQL's prompt, for self-consistency
        not_answerable: str | None = None   # [pipeline] sql_no_data: the writer's NOT_ANSWERABLE reply, never run
        if attempt == 0 and exact_sql:
            candidate_sql = exact_sql
            verified_example = True
            logger.info("SQL Agent fast path for query_id=%s: approved example in workspace %s",
                        query_id, workspace_id)
        elif attempt == 0 and compiled_sql:
            candidate_sql = compiled_sql
            logger.info("SQL Agent compiled template for query_id=%s in workspace %s", query_id, workspace_id)
        elif attempt == 0 and debug_sql:
            candidate_sql = debug_sql
            used_debug_sql = True
            logger.warning(
                "SQL Agent using debug_candidate_sql override for query_id=%s "
                "(test harness only -- never set by production request paths).",
                query_id,
            )
        else:
            llm = get_agent_llm("sql")
            if attempt == 0:
                system_prompt = sql_system_prompt(_SYSTEM_PROMPT, max_rows, _schema, flags_snapshot) + ws_suffix
                user_message = (
                    f"{context_block}\n\nQuestion: {raw_query}{context_line}\n\n"
                    f"Write a single PostgreSQL SELECT query to answer this question."
                )
            else:
                system_prompt = sql_system_prompt(_CORRECTION_SYSTEM_PROMPT, max_rows, _schema, flags_snapshot) + ws_suffix
                error_history_text = _format_error_history(existing_errors)
                # Error history first, schema second: the fix is the most
                # important, actionable content, and leading with it reads
                # more reliably than burying it after a full multi-table
                # schema dump.
                user_message = (
                    f"Question: {raw_query}{context_line}\n\n{error_history_text}\n\n"
                    f"Full schema (find the correct column/table/join path here):\n{context_block}\n\n"
                    f"Write a single corrected PostgreSQL SELECT query."
                )

            sc_prompt = (system_prompt, user_message)
            sql_result = await llm.complete(
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=cfg.sql.max_tokens_sql,
            )
            tokens_in_total += sql_result.tokens_in
            tokens_out_total += sql_result.tokens_out
            llm_calls_total += 1
            candidate_sql = sql_result.text
            if sql_outcomes.sql_no_data_on(flags_snapshot):
                not_answerable = sql_outcomes.parse_not_answerable(candidate_sql)

        candidate_sql = _strip_sql_fences(candidate_sql)

        logger.info(
            "SQL Agent generated SQL for query_id=%s attempt=%d (memory_hit=%s): %.200s...",
            query_id, attempt, memory_hit, candidate_sql,
        )

        if not_answerable is None:
            candidate_sql, script_repairs = _repair_script_column_mismatch(
                candidate_sql, table_descriptions,
            )
        else:
            script_repairs = []
        for repair_note in script_repairs:
            logger.info(
                "SQL Agent script repair for query_id=%s: %s",
                query_id, repair_note,
            )

        # Step 5: execute. A NOT_ANSWERABLE reply is not SQL and never reaches the database.
        if not_answerable is not None:
            exec_result = {}
        elif compiled_exec is not None and candidate_sql == compiled_sql:
            exec_result = compiled_exec  # already executed while deciding to use the template
        else:
            exec_result = await data_run_select(session, candidate_sql, max_rows=max_rows)
        elapsed_ms = int(time.monotonic() * 1000) - start_ms

        update: dict[str, Any] = {
            "schema_fingerprint": schema_fingerprint,
            "candidate_sql": candidate_sql,
            "attempt": attempt,
            "verified_example": verified_example,
            "compiled_template": bool(compiled_sql),
        }
        if new_interrogation_samples:
            update["interrogation_samples"] = new_interrogation_samples
        if script_repairs:
            update["script_repairs"] = script_repairs
        if attempt == 0:
            update["memory_hit"] = memory_hit
            update["memory_trace_id"] = memory_trace_id
        if spatial_resolution:
            update["spatial"] = {
                "digipin": spatial_resolution["digipin"],
                "lat": spatial_resolution["lat"],
                "lon": spatial_resolution["lon"],
                "radius_m": spatial_resolution["radius_m"],
            }

        ok = bool(exec_result.get("ok"))
        rows = exec_result.get("rows", []) if ok else []

        last_error = existing_errors[-1] if existing_errors else None
        repeat_empty_accepted = (
            ok and not rows
            and last_error is not None
            and last_error.get("category") == "empty_result"
        )

        # pipeline.answer_checks: deterministic checks on a result that RAN. Never for
        # the fast path (an approved example is already verified) or the test harness's
        # injected SQL; with the flag off none of this issues a query.
        fanout_finding: answer_checks.Finding | None = None
        caveats: list[str] = []
        if (
            ok and rows
            and flags_snapshot.get("pipeline.answer_checks", "off") == "on"
            and not verified_example and not used_debug_sql
        ):
            try:
                table_columns = {
                    d["table"]: [c["name"] for c in d.get("columns", []) if isinstance(c, dict) and "name" in c]
                    for d in table_descriptions if isinstance(d, dict) and "table" in d
                }
                # A memory hit reads no schema, so describe just the probed tables.
                for probe in answer_checks.fanout_probe(candidate_sql, _schema) or []:
                    bare = probe.table.split(".")[-1]
                    if bare not in table_columns and probe.table not in table_columns:
                        desc = await schema_describe_table(session, bare, _schema)
                        table_columns[bare] = [
                            c["name"] for c in desc.get("columns", []) if isinstance(c, dict) and "name" in c
                        ]
                fanout_finding = await answer_checks.check_fanout(
                    session, candidate_sql, schema_fingerprint, table_columns, _schema,
                )
            except Exception as exc:
                logger.warning("SQL Agent answer checks skipped for query_id=%s: %s", query_id, exc)
            caveats = [
                c for c in (
                    answer_checks.truncation_caveat(len(rows), max_rows),
                    answer_checks.null_aggregate_caveat(rows),
                ) if c
            ]

        fanout_retry = False
        if fanout_finding is not None:
            prior_fanouts = sum(1 for e in existing_errors if e.get("category") == "join_cardinality")
            if prior_fanouts < int(cfg.answer_checks.fanout_retry_max):
                fanout_retry = True
            else:
                caveats.append("These figures may count some rows more than once (join fan-out).")

        if not_answerable is not None:
            # [pipeline] sql_no_data (F-S1): a typed outcome, terminal (no_data_source) once confirmed, so the loop stops.
            # There is no statement to report, so candidate_sql is cleared.
            logger.info("SQL Agent: query_id=%s attempt=%d replied NOT_ANSWERABLE.", query_id, attempt)
            update["candidate_sql"] = None
            update["sql_rows"] = []
            update["sql_columns"] = []
            # T2b/T2c: the first reply is the retriable not_answerable_unconfirmed instead, and the next
            # attempt is shown every table and the near-matching columns; a second reply is final.
            update["errors"] = [sql_outcomes.not_answerable_record(attempt, not_answerable, existing_errors)]

        elif fanout_retry:
            # Failure-shaped, like any other failed attempt: no executed_sql, so the graph
            # retries and the correction prompt shows this message and hint.
            logger.warning(
                "SQL Agent join fan-out for query_id=%s attempt=%d: %s",
                query_id, attempt, fanout_finding.message,
            )
            update["sql_rows"] = []
            update["sql_columns"] = []
            update["errors"] = [{
                "attempt": attempt,
                "category": "join_cardinality",
                "engine_code": None,
                # The repair loop's control (repair_loop.py): "plain" withholds the evidence.
                "message": (repair_loop.PLAIN_SQL_MESSAGE
                            if flags_snapshot.get("pipeline.repair_loop", "off") == "plain"
                            else fanout_finding.message),
                "offending_sql": candidate_sql,
                "remediation_hint": (
                    "Aggregate the joined table in a subquery before joining, or aggregate at the "
                    "right grain (e.g. COUNT(DISTINCT key)), so each row of "
                    f"{fanout_finding.table} counts once."
                ),
            }]

        elif ok and (rows or repeat_empty_accepted):
            # Real success, or a second empty_result in a row accepted as
            # the answer (A3) -- both are terminal-success shaped state.
            update["executed_sql"] = candidate_sql
            update["sql_rows"] = rows
            update["sql_columns"] = exec_result.get("columns", [])
            if caveats:
                update["caveats"] = caveats
            logger.info(
                "SQL Agent success for query_id=%s attempt=%d: %d rows returned.",
                query_id, attempt, len(rows),
            )

            # [pipeline] self_consistency_k (the study's baseline; 0 = off when shipped): resample
            # the prompt that wrote this SQL and compare results. Measurement only (self_consistency.py).
            sc_k = int(flags_snapshot.get("pipeline.self_consistency_k", 0) or 0)
            if sc_k > 0 and sc_prompt is not None:
                sc_system, sc_user = sc_prompt
                sc_llm = get_agent_llm("sql")
                update["self_consistency"] = await self_consistency.sample(
                    complete=lambda: sc_llm.complete(system=sc_system, messages=[{"role": "user", "content": sc_user}],
                                                     max_tokens=cfg.sql.max_tokens_sql),
                    run=lambda sql: data_run_select(session, sql, max_rows=max_rows),
                    prepare=lambda text: _repair_script_column_mismatch(_strip_sql_fences(text), table_descriptions)[0],
                    answer_rows=rows, k=sc_k,
                )

            # Write back unless this is a clean attempt-0 memory hit (C1 GAP-1).
            # A compiled answer adds no new insight either, and must not cost a model call.
            write_back = not (attempt == 0 and memory_hit) and not compiled_sql
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
            category, hint = sql_outcomes.classify_engine_failure(sqlstate, message, flags_snapshot)

            logger.warning(
                "SQL Agent failure for query_id=%s attempt=%d: category=%s sqlstate=%s message=%s",
                query_id, attempt, category, sqlstate, message,
            )

            # The statement did not execute, so naming it executed_sql would
            # misreport what happened. It remains available as candidate_sql,
            # which the update dict already sets unconditionally above, and
            # the error entry already carries offending_sql.
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

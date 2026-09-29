"""
mcp/tools/workspace_tools.py
MCP workspaces tool (docs/V3_PLAN.md Phase 3, migration 021): workspaces and
their knowledge store.

One JSON-RPC method, workspaces.manage (decision D-v3-10), takes an `action`:

  list, create, get, update, delete        the workspace itself
  scan                                     read the data source's catalog
  catalog, save_descriptions               tables and columns, and what they mean
  definitions, save_definitions,
  delete_definition                        business terms
  approve                                  the owner accepts the knowledge
  add_example, remove_example              an approved question -> SQL pair, and taking one back
  context                                  what a question in this workspace may use
  templates                                approved queries as fill-in-the-values templates (Phase 3c)
  check_examples, examples                 re-run approved examples with no model; list them with their check state
  start_run, record_result, finish_run,
  get_run, set_verdict                     the 10-question setup test

THE RULES, enforced here rather than trusted from a caller:

1. Every statement filters on tenant_id (bound server-side, A0.6) AND the
   owner principal. Row-level security (migration 021) repeats the tenant
   check in the database as a backstop.
2. Only APPROVED knowledge leaves through `context` (V3_PLAN §5.1): drafts
   never reach a prompt.
3. An example's SQL must pass the same Layer 1 gate every query passes before
   it is stored; approved examples can run directly (the fast path).
4. A workspace's data source must be in the MCP's own schema allowlist.
5. Sizes are capped from config [workspaces].

Failures are data ({"ok": False, "error": code, "message": text}), the same
contract as tiles.manage. Codes: not_found, invalid, limit_reached.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from config_loader import cfg
from db import get_history_connection
from serialization import to_json_safe
from session import get_tenant_id
from tools.data_tools import _check_layer1, run_select, sample_rows
from tools.schema_tools import ALLOWED_SCHEMAS, describe_table, list_tables
from tools.sql_templates import derive_template

logger = logging.getLogger(__name__)

_ACTIONS = frozenset({
    "list", "create", "get", "update", "delete", "scan", "catalog", "save_descriptions",
    "definitions", "save_definitions", "delete_definition", "approve", "add_example", "remove_example",
    "context", "templates", "check_examples", "examples",
    "start_run", "record_result", "finish_run", "get_run", "set_verdict",
})
_NO_WORKSPACE = frozenset({"list", "create"})
_VERDICTS = frozenset({"pending", "running", "right", "wrong", "error"})
_DESCRIPTION_SOURCES = frozenset({"ai", "owner"})
_EXAMPLE_SOURCES = frozenset({"setup_test", "tile", "owner"})
_SAMPLE_VALUE_MAX_CHARS = 80  # noqa: literals - a sample value is a hint for the drafter, not data
_CHECK_DETAIL_MAX_CHARS = 300  # noqa: literals - a stored error message is truncated for display
_CHECK_SEVERITY = {"failed": 0, "changed": 1, "ok": 2}
_STOPWORDS = frozenset(
    "the a an of in on for to and or is are was were by with what which how many much per "
    "from at as that this these those be do does did show me list give total all each".split()
)


# ── Pure helpers (unit-tested in tests/test_workspace_logic.py) ───────────────

def _fail(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def clean_text(value: Any, max_chars: int, allow_empty: bool = False) -> str | None:
    """Trimmed text within max_chars, or None if missing or (unless allowed) blank."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text and not allow_empty:
        return None
    return text[:max_chars]


def normalize_question(question: str) -> str:
    """Lowercase words only: how a fast-path match compares two questions."""
    return " ".join(re.findall(r"[a-z0-9]+", re.sub(r"['\u2019]", "", question.lower())))


def question_tokens(question: str) -> set[str]:
    return {w for w in normalize_question(question).split() if len(w) > 2 and w not in _STOPWORDS}


def similarity(a: str, b: str) -> float:
    """Jaccard similarity of two questions' content words (0..1)."""
    ta, tb = question_tokens(a), question_tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def mentioned_terms(question: str, terms: list[str]) -> list[str]:
    """The defined terms a question mentions, as whole words, case-insensitively."""
    q = f" {normalize_question(question)} "
    return [t for t in terms if f" {normalize_question(t)} " in q]


def result_signature(columns: list[str], row_count: int) -> str:
    """The SHAPE of a query result: its column names in order, and whether it has rows.

    Values are left out on purpose: live data changes legitimately, but a renamed
    column or a table that has gone empty means an approved answer may be wrong.
    """
    return f"cols={','.join(columns)};rows={'yes' if row_count > 0 else 'no'}"


def sample_values(rows: list[dict], column: str, limit: int) -> list[str]:
    """Up to `limit` distinct non-empty text values of a column, in first-seen order."""
    seen: list[str] = []
    for row in rows:
        value = row.get(column)
        if not isinstance(value, str) or not value.strip():
            continue
        value = value[:_SAMPLE_VALUE_MAX_CHARS]
        if value not in seen:
            seen.append(value)
            if len(seen) == limit:
                break
    return seen


# ── Database access ──────────────────────────────────────────────────────────

class _Abort(Exception):
    def __init__(self, failure: dict[str, Any]) -> None:
        super().__init__(failure.get("message"))
        self.failure = failure


_VIEW = """
    SELECT w.workspace_id, w.name, w.target_schema, w.business_context, w.context_version,
           w.setup_state, w.setup_score, w.setup_total, w.created_at, w.updated_at,
           (SELECT count(*) FROM session_state.kb_columns c WHERE c.workspace_id = w.workspace_id AND c.column_name = ''),
           (SELECT count(*) FROM session_state.kb_columns c WHERE c.workspace_id = w.workspace_id AND c.approved),
           (SELECT count(*) FROM session_state.kb_definitions d WHERE d.workspace_id = w.workspace_id),
           (SELECT count(*) FROM session_state.kb_examples e WHERE e.workspace_id = w.workspace_id),
           (SELECT count(*) FROM session_state.kb_examples e
             WHERE e.workspace_id = w.workspace_id AND e.approved AND e.check_state = 'ok'),
           (SELECT count(*) FROM session_state.kb_examples e
             WHERE e.workspace_id = w.workspace_id AND e.approved AND e.check_state = 'changed'),
           (SELECT count(*) FROM session_state.kb_examples e
             WHERE e.workspace_id = w.workspace_id AND e.approved AND e.check_state = 'failed'),
           (SELECT count(*) FROM session_state.kb_examples e
             WHERE e.workspace_id = w.workspace_id AND e.approved AND e.check_state IS NULL),
           (SELECT max(e.checked_at) FROM session_state.kb_examples e
             WHERE e.workspace_id = w.workspace_id AND e.approved)
    FROM session_state.workspaces w
    WHERE w.tenant_id = %s AND w.owner_principal_id = %s
"""


def _to_view(row: tuple) -> dict[str, Any]:
    (workspace_id, name, schema, context, version, state, score, total, created, updated,
     tables, approved, definitions, examples, h_ok, h_changed, h_failed, h_unchecked, h_checked_at) = row
    return {
        "workspace_id": workspace_id, "name": name, "target_schema": schema,
        "business_context": context, "context_version": version, "setup_state": state,
        "setup_score": score, "setup_total": total,
        "created_at": created.isoformat(), "updated_at": updated.isoformat(),
        "counts": {"tables": tables, "approved": approved, "definitions": definitions, "examples": examples},
        "health": {"ok": h_ok, "changed": h_changed, "failed": h_failed, "unchecked": h_unchecked,
                   "checked_at": h_checked_at.isoformat() if h_checked_at else None},
        # Lets the page decide, from config, when opening a workspace should re-check it.
        "example_check_stale_after_s": int(cfg.example_checks.stale_after_s),
    }


async def _owned(conn: Any, tenant_id: str, principal_id: str, workspace_id: Any) -> tuple | None:
    if not isinstance(workspace_id, str) or not workspace_id:
        return None
    return await (await conn.execute(
        "SELECT workspace_id, target_schema, business_context, context_version, setup_state "
        "FROM session_state.workspaces WHERE tenant_id = %s AND owner_principal_id = %s AND workspace_id = %s",
        (tenant_id, principal_id, workspace_id),
    )).fetchone()


async def _view(conn: Any, tenant_id: str, principal_id: str, workspace_id: str) -> dict | None:
    row = await (await conn.execute(_VIEW + " AND w.workspace_id = %s", (tenant_id, principal_id, workspace_id))).fetchone()
    return _to_view(row) if row else None


async def _touch(conn: Any, workspace_id: str, state: str | None = None) -> None:
    if state:
        await conn.execute(
            "UPDATE session_state.workspaces SET setup_state = %s, updated_at = now() WHERE workspace_id = %s",
            (state, workspace_id))
    else:
        await conn.execute("UPDATE session_state.workspaces SET updated_at = now() WHERE workspace_id = %s",
                           (workspace_id,))


# ── Actions ───────────────────────────────────────────────────────────────────

async def _list(tenant_id, principal_id, **_):
    async with get_history_connection() as conn:
        rows = await (await conn.execute(_VIEW + " ORDER BY w.created_at", (tenant_id, principal_id))).fetchall()
    return {"ok": True, "workspaces": [_to_view(r) for r in rows]}


async def _create(tenant_id, principal_id, name=None, target_schema=None, business_context=None, **_):
    clean_name = clean_text(name, int(cfg.workspaces.name_max_chars))
    if clean_name is None:
        return _fail("invalid", "name must not be blank.")
    if target_schema not in ALLOWED_SCHEMAS:
        return _fail("invalid", f"Unknown dataset {target_schema!r}.")
    context = clean_text(business_context or "", int(cfg.workspaces.business_context_max_chars), allow_empty=True) or ""
    workspace_id = str(uuid.uuid4())
    async with get_history_connection() as conn:
        count = (await (await conn.execute(
            "SELECT count(*) FROM session_state.workspaces WHERE tenant_id = %s AND owner_principal_id = %s",
            (tenant_id, principal_id))).fetchone())[0]
        if count >= int(cfg.workspaces.max_per_principal):
            return _fail("limit_reached", f"You already have {count} workspaces; the limit is "
                                          f"{cfg.workspaces.max_per_principal}.")
        await conn.execute(
            """
            INSERT INTO session_state.workspaces
                (workspace_id, tenant_id, owner_principal_id, name, target_schema, business_context, context_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (workspace_id, tenant_id, principal_id, clean_name, target_schema, context, 1 if context else 0),
        )
        if context:
            await conn.execute(
                "INSERT INTO session_state.business_context_versions "
                "(workspace_id, tenant_id, version, text, author_principal_id) VALUES (%s, %s, 1, %s, %s)",
                (workspace_id, tenant_id, context, principal_id))
        view = await _view(conn, tenant_id, principal_id, workspace_id)
    logger.info("workspaces: created %s on %s (tenant=%s principal=%s)", workspace_id, target_schema, tenant_id, principal_id)
    return {"ok": True, "workspace": view}


async def _get(tenant_id, principal_id, workspace_id=None, **_):
    async with get_history_connection() as conn:
        view = await _view(conn, tenant_id, principal_id, workspace_id) if isinstance(workspace_id, str) else None
    return {"ok": True, "workspace": view} if view else _fail("not_found", "No such workspace.")


async def _update(tenant_id, principal_id, workspace_id=None, name=None, business_context=None, **_):
    if name is None and business_context is None:
        return _fail("invalid", "Nothing to update: pass name and/or business_context.")
    clean_name = clean_text(name, int(cfg.workspaces.name_max_chars)) if name is not None else None
    if name is not None and clean_name is None:
        return _fail("invalid", "name must not be blank.")
    context = None
    if business_context is not None:
        context = clean_text(business_context, int(cfg.workspaces.business_context_max_chars), allow_empty=True)
        if context is None:
            return _fail("invalid", "business_context must be text.")
    async with get_history_connection() as conn:
        owned = await _owned(conn, tenant_id, principal_id, workspace_id)
        if owned is None:
            return _fail("not_found", "No such workspace.")
        if clean_name is not None:
            await conn.execute("UPDATE session_state.workspaces SET name = %s, updated_at = now() WHERE workspace_id = %s",
                               (clean_name, workspace_id))
        if context is not None and context != owned[2]:
            version = owned[3] + 1
            await conn.execute(
                "UPDATE session_state.workspaces SET business_context = %s, context_version = %s, updated_at = now() "
                "WHERE workspace_id = %s", (context, version, workspace_id))
            await conn.execute(
                "INSERT INTO session_state.business_context_versions "
                "(workspace_id, tenant_id, version, text, author_principal_id) VALUES (%s, %s, %s, %s, %s)",
                (workspace_id, tenant_id, version, context, principal_id))
        view = await _view(conn, tenant_id, principal_id, workspace_id)
    return {"ok": True, "workspace": view}


async def _delete(tenant_id, principal_id, workspace_id=None, **_):
    async with get_history_connection() as conn:
        row = await (await conn.execute(
            "DELETE FROM session_state.workspaces WHERE tenant_id = %s AND owner_principal_id = %s AND workspace_id = %s "
            "RETURNING workspace_id", (tenant_id, principal_id, workspace_id))).fetchone()
    if row is None:
        return _fail("not_found", "No such workspace.")
    logger.info("workspaces: deleted %s (tenant=%s principal=%s)", workspace_id, tenant_id, principal_id)
    return {"ok": True, "workspace_id": workspace_id}


async def _scan(tenant_id, principal_id, workspace_id=None, **_):
    """Read the data source's catalog into kb_columns, keeping descriptions of what still exists."""
    async with get_history_connection() as conn:
        owned = await _owned(conn, tenant_id, principal_id, workspace_id)
    if owned is None:
        return _fail("not_found", "No such workspace.")
    _wid, schema, _context, _version, state = owned
    listing = await list_tables(schema)
    tables = listing.get("tables", [])[: int(cfg.workspaces.scan_max_tables)]
    entries: list[tuple] = []
    for table in tables:
        entries.append((table["name"], "", "table", table.get("row_estimate"), None, table.get("comment")))
        described = await describe_table(table["name"], schema)
        columns = described.get("columns", [])
        text_columns = [c["name"] for c in columns if c.get("type") in ("text", "character varying")]
        samples: dict[str, list[str]] = {}
        if text_columns:
            sampled = await sample_rows(table["name"], columns=text_columns,
                                        limit=int(cfg.workspaces.scan_sample_rows), schema=schema)
            rows = to_json_safe(sampled.get("rows", []))
            samples = {c: sample_values(rows, c, int(cfg.workspaces.scan_sample_values)) for c in text_columns}
        for col in columns:
            entries.append((table["name"], col["name"], col.get("type"), None, samples.get(col["name"]), col.get("comment")))

    keep = {(t, c) for t, c, *_ in entries}
    async with get_history_connection() as conn:
        existing = await (await conn.execute(
            "SELECT table_name, column_name FROM session_state.kb_columns WHERE workspace_id = %s", (workspace_id,),
        )).fetchall()
        for table_name, column_name in existing:
            if (table_name, column_name) not in keep:
                await conn.execute(
                    "DELETE FROM session_state.kb_columns WHERE workspace_id = %s AND table_name = %s AND column_name = %s",
                    (workspace_id, table_name, column_name))
        for table_name, column_name, data_type, estimate, samples, comment in entries:
            # A database comment seeds the description as an AI-free draft: still unapproved.
            await conn.execute(
                """
                INSERT INTO session_state.kb_columns
                    (workspace_id, tenant_id, table_name, column_name, data_type, row_estimate, sample_values,
                     description, description_source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (workspace_id, table_name, column_name) DO UPDATE SET
                    data_type = EXCLUDED.data_type, row_estimate = EXCLUDED.row_estimate,
                    sample_values = EXCLUDED.sample_values, updated_at = now()
                """,
                (workspace_id, tenant_id, table_name, column_name, data_type, estimate,
                 Jsonb(samples) if samples else None, comment, "ai" if comment else None),
            )
        await conn.execute(
            "UPDATE session_state.workspaces SET catalog_fingerprint = %s, updated_at = now(), "
            "setup_state = CASE WHEN setup_state = 'created' THEN 'scanned' ELSE setup_state END WHERE workspace_id = %s",
            (listing.get("fingerprint"), workspace_id))
    logger.info("workspaces: scanned %s (%s): %d tables, %d entries", workspace_id, schema, len(tables), len(entries))
    return {"ok": True, "tables": len(tables), "columns": len(entries) - len(tables), "previous_state": state}


async def _catalog(tenant_id, principal_id, workspace_id=None, **_):
    async with get_history_connection() as conn:
        if await _owned(conn, tenant_id, principal_id, workspace_id) is None:
            return _fail("not_found", "No such workspace.")
        rows = await (await conn.execute(
            """
            SELECT table_name, column_name, data_type, row_estimate, sample_values, description,
                   description_source, approved
            FROM session_state.kb_columns WHERE workspace_id = %s
            ORDER BY table_name, column_name <> '', column_name
            """, (workspace_id,))).fetchall()
    return {"ok": True, "columns": [
        {"table_name": t, "column_name": c, "data_type": d, "row_estimate": r, "sample_values": s or [],
         "description": desc, "description_source": src, "approved": a}
        for t, c, d, r, s, desc, src, a in rows]}


async def _save_descriptions(tenant_id, principal_id, workspace_id=None, items=None, **_):
    if not isinstance(items, list) or not items:
        return _fail("invalid", "items must be a non-empty list of {table_name, column_name, description, source}.")
    max_chars = int(cfg.workspaces.description_max_chars)
    try:
        async with get_history_connection() as conn:
            if await _owned(conn, tenant_id, principal_id, workspace_id) is None:
                raise _Abort(_fail("not_found", "No such workspace."))
            updated = skipped = 0
            for item in items:
                if not isinstance(item, dict):
                    raise _Abort(_fail("invalid", "Every item must be an object."))
                source = item.get("source", "owner")
                if source not in _DESCRIPTION_SOURCES:
                    raise _Abort(_fail("invalid", f"source must be one of {sorted(_DESCRIPTION_SOURCES)}."))
                text = clean_text(item.get("description"), max_chars, allow_empty=True)
                if text is None:
                    raise _Abort(_fail("invalid", "description must be text."))
                key = (workspace_id, item.get("table_name"), item.get("column_name") or "")
                current = await (await conn.execute(
                    "SELECT description_source FROM session_state.kb_columns "
                    "WHERE workspace_id = %s AND table_name = %s AND column_name = %s", key)).fetchone()
                if current is None:
                    raise _Abort(_fail("not_found", f"{key[1]}.{key[2]} is not in this workspace's catalog. Scan again."))
                if source == "ai" and current[0] == "owner":
                    skipped += 1        # an AI draft never replaces the owner's words
                    continue
                await conn.execute(
                    """
                    UPDATE session_state.kb_columns
                    SET description = %s, description_source = %s, approved = FALSE, updated_at = now()
                    WHERE workspace_id = %s AND table_name = %s AND column_name = %s
                    """,
                    (text or None, source, *key))
                updated += 1
            if any(i.get("source", "owner") == "ai" for i in items):
                await conn.execute(
                    "UPDATE session_state.workspaces SET setup_state = 'drafted', updated_at = now() "
                    "WHERE workspace_id = %s AND setup_state IN ('created', 'scanned')", (workspace_id,))
    except _Abort as abort:
        return abort.failure
    return {"ok": True, "updated": updated, "skipped": skipped}


async def _definitions(tenant_id, principal_id, workspace_id=None, **_):
    async with get_history_connection() as conn:
        if await _owned(conn, tenant_id, principal_id, workspace_id) is None:
            return _fail("not_found", "No such workspace.")
        rows = await (await conn.execute(
            "SELECT definition_id, term, definition, source, approved FROM session_state.kb_definitions "
            "WHERE workspace_id = %s ORDER BY lower(term)", (workspace_id,))).fetchall()
    return {"ok": True, "definitions": [
        {"definition_id": i, "term": t, "definition": d, "source": s, "approved": a} for i, t, d, s, a in rows]}


async def _save_definitions(tenant_id, principal_id, workspace_id=None, items=None, **_):
    if not isinstance(items, list) or not items:
        return _fail("invalid", "items must be a non-empty list of {term, definition, source}.")
    try:
        async with get_history_connection() as conn:
            if await _owned(conn, tenant_id, principal_id, workspace_id) is None:
                raise _Abort(_fail("not_found", "No such workspace."))
            count = (await (await conn.execute(
                "SELECT count(*) FROM session_state.kb_definitions WHERE workspace_id = %s", (workspace_id,))).fetchone())[0]
            for item in items:
                term = clean_text(item.get("term") if isinstance(item, dict) else None, int(cfg.workspaces.term_max_chars))
                text = clean_text(item.get("definition") if isinstance(item, dict) else None,
                                  int(cfg.workspaces.description_max_chars))
                source = item.get("source", "owner") if isinstance(item, dict) else None
                if term is None or text is None or source not in _DESCRIPTION_SOURCES:
                    raise _Abort(_fail("invalid", "Every definition needs a term, a definition and a source of ai or owner."))
                exists = (await (await conn.execute(
                    "SELECT 1 FROM session_state.kb_definitions WHERE workspace_id = %s AND lower(term) = lower(%s)",
                    (workspace_id, term))).fetchone()) is not None
                if not exists:
                    if count >= int(cfg.workspaces.definitions_max):
                        raise _Abort(_fail("limit_reached", f"A workspace holds at most {cfg.workspaces.definitions_max} definitions."))
                    count += 1
                await conn.execute(
                    """
                    INSERT INTO session_state.kb_definitions (definition_id, workspace_id, tenant_id, term, definition, source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (workspace_id, lower(term)) DO UPDATE SET
                        term = EXCLUDED.term, definition = EXCLUDED.definition, source = EXCLUDED.source,
                        approved = FALSE, updated_at = now()
                    -- an AI draft never replaces the owner's definition
                    WHERE EXCLUDED.source = 'owner' OR session_state.kb_definitions.source = 'ai'
                    """,
                    (str(uuid.uuid4()), workspace_id, tenant_id, term, text, source))
    except _Abort as abort:
        return abort.failure
    return await _definitions(tenant_id, principal_id, workspace_id=workspace_id)


async def _delete_definition(tenant_id, principal_id, workspace_id=None, definition_id=None, **_):
    async with get_history_connection() as conn:
        if await _owned(conn, tenant_id, principal_id, workspace_id) is None:
            return _fail("not_found", "No such workspace.")
        row = await (await conn.execute(
            "DELETE FROM session_state.kb_definitions WHERE workspace_id = %s AND definition_id = %s RETURNING term",
            (workspace_id, definition_id))).fetchone()
    return {"ok": True, "deleted": definition_id} if row else _fail("not_found", "No such definition.")


async def _approve(tenant_id, principal_id, workspace_id=None, **_):
    """The owner accepts the knowledge as it stands: every description and definition."""
    async with get_history_connection() as conn:
        if await _owned(conn, tenant_id, principal_id, workspace_id) is None:
            return _fail("not_found", "No such workspace.")
        cols = (await conn.execute(
            "UPDATE session_state.kb_columns SET approved = TRUE, updated_at = now() "
            "WHERE workspace_id = %s AND description IS NOT NULL AND NOT approved", (workspace_id,))).rowcount
        defs = (await conn.execute(
            "UPDATE session_state.kb_definitions SET approved = TRUE, updated_at = now() "
            "WHERE workspace_id = %s AND NOT approved", (workspace_id,))).rowcount
        await conn.execute(
            "UPDATE session_state.workspaces SET setup_state = CASE WHEN setup_state = 'tested' THEN 'tested' "
            "ELSE 'approved' END, updated_at = now() WHERE workspace_id = %s", (workspace_id,))
        view = await _view(conn, tenant_id, principal_id, workspace_id)
    return {"ok": True, "approved_columns": cols, "approved_definitions": defs, "workspace": view}


async def _add_example(tenant_id, principal_id, workspace_id=None, question=None, sql=None, source="owner", **_):
    q = clean_text(question, int(cfg.workspaces.question_max_chars))
    if q is None or not isinstance(sql, str) or not sql.strip():
        return _fail("invalid", "An example needs a question and its SQL.")
    if source not in _EXAMPLE_SOURCES:
        return _fail("invalid", f"source must be one of {sorted(_EXAMPLE_SOURCES)}.")
    rejection = _check_layer1(sql)
    if rejection:
        return _fail("invalid", f"The example's SQL does not pass the SQL gate: {rejection}")
    norm = normalize_question(q)
    async with get_history_connection() as conn:
        if await _owned(conn, tenant_id, principal_id, workspace_id) is None:
            return _fail("not_found", "No such workspace.")
        count = (await (await conn.execute(
            "SELECT count(*) FROM session_state.kb_examples WHERE workspace_id = %s", (workspace_id,))).fetchone())[0]
        if count >= int(cfg.workspaces.examples_max):
            return _fail("limit_reached", f"A workspace holds at most {cfg.workspaces.examples_max} examples.")
        # One approved answer per question: a newer one replaces it.
        await conn.execute("DELETE FROM session_state.kb_examples WHERE workspace_id = %s AND question_norm = %s",
                           (workspace_id, norm))
        example_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO session_state.kb_examples (example_id, workspace_id, tenant_id, question, question_norm, "
            "sql_text, source) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (example_id, workspace_id, tenant_id, q, norm, sql.strip(), source))
        # The template is derived in the same transaction, so an example and its
        # template appear (and, by ON DELETE CASCADE, disappear) together. An SQL
        # with no usable literals simply gets none.
        derived = derive_template(sql.strip())
        if derived is not None:
            template_sql, slots = derived
            await conn.execute(
                "INSERT INTO session_state.kb_templates (template_id, example_id, workspace_id, tenant_id, "
                "question, question_norm, template_sql, slots) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), example_id, workspace_id, tenant_id, q, norm, template_sql, Jsonb(slots)))
    return {"ok": True, "example_id": example_id}


async def _remove_example(tenant_id, principal_id, workspace_id=None, question=None, **_):
    """Withdraw the approved answer to a question (a thumbs-down on a verified answer)."""
    q = clean_text(question, int(cfg.workspaces.question_max_chars))
    if q is None:
        return _fail("invalid", "question is required.")
    async with get_history_connection() as conn:
        if await _owned(conn, tenant_id, principal_id, workspace_id) is None:
            return _fail("not_found", "No such workspace.")
        removed = (await conn.execute(
            "DELETE FROM session_state.kb_examples WHERE tenant_id = %s AND workspace_id = %s AND question_norm = %s",
            (tenant_id, workspace_id, normalize_question(q)))).rowcount
    return {"ok": True, "removed": removed}


async def _context(tenant_id, principal_id, workspace_id=None, question=None, **_):
    """What a question in this workspace may use: approved knowledge only (§5.1)."""
    if not isinstance(question, str):
        return _fail("invalid", "question is required.")
    async with get_history_connection() as conn:
        owned = await _owned(conn, tenant_id, principal_id, workspace_id)
        if owned is None:
            return _fail("not_found", "No such workspace.")
        cols = await (await conn.execute(
            "SELECT table_name, column_name, description FROM session_state.kb_columns "
            "WHERE workspace_id = %s AND approved AND description IS NOT NULL ORDER BY table_name, column_name",
            (workspace_id,))).fetchall()
        defs = await (await conn.execute(
            "SELECT term, definition FROM session_state.kb_definitions WHERE workspace_id = %s AND approved",
            (workspace_id,))).fetchall()
        examples = await (await conn.execute(
            "SELECT question, question_norm, sql_text FROM session_state.kb_examples WHERE workspace_id = %s AND approved",
            (workspace_id,))).fetchall()
    norm = normalize_question(question)
    exact = next(({"question": q, "sql": s} for q, n, s in examples if n == norm), None)
    ranked = sorted(((similarity(question, q), q, s) for q, _n, s in examples if _n != norm), reverse=True)
    similar = [{"question": q, "sql": s} for score, q, s in ranked
               if score >= float(cfg.workspaces.example_min_similarity)][: int(cfg.workspaces.context_max_examples)]
    terms = mentioned_terms(question, [t for t, _ in defs])
    definitions = [{"term": t, "definition": d} for t, d in defs if t in terms][: int(cfg.workspaces.context_max_definitions)]
    return {
        "ok": True,
        "target_schema": owned[1],
        "business_context": owned[2],
        "descriptions": [{"table_name": t, "column_name": c, "description": d} for t, c, d in cols],
        "definitions": definitions,
        "examples": similar,
        "exact": exact,
    }


async def _templates(tenant_id, principal_id, workspace_id=None, question=None, **_):
    """Templates of this workspace's approved examples that resemble a question.

    The exact-question case is excluded: that is the fast path's job (`context`.exact),
    and a template of the very same question would only re-derive the same SQL.
    """
    if not isinstance(question, str) or not question.strip():
        return _fail("invalid", "question is required.")
    async with get_history_connection() as conn:
        if await _owned(conn, tenant_id, principal_id, workspace_id) is None:
            return _fail("not_found", "No such workspace.")
        rows = await (await conn.execute(
            "SELECT t.template_id, t.question, t.question_norm, t.template_sql, t.slots "
            "FROM session_state.kb_templates t "
            "JOIN session_state.kb_examples e ON e.example_id = t.example_id AND e.approved "
            "WHERE t.tenant_id = %s AND t.workspace_id = %s",
            (tenant_id, workspace_id))).fetchall()
    norm = normalize_question(question)
    ranked = sorted(((similarity(question, q), tid, q, sql, slots) for tid, q, n, sql, slots in rows if n != norm),
                    key=lambda r: r[0], reverse=True)
    return {"ok": True, "templates": [
        {"template_id": tid, "question": q, "template_sql": sql, "slots": slots, "score": round(score, 3)}
        for score, tid, q, sql, slots in ranked
        if score >= float(cfg.templates.min_similarity)][: int(cfg.templates.max_candidates)]}


async def _check_examples(tenant_id, principal_id, workspace_id=None, **_):
    """Re-run approved examples with no model and compare each result's shape with the first one.

    The SQL goes through run_select: the Layer 1 gate and the read-only connection,
    the same path every data query takes. A failure is recorded, not raised.
    """
    async with get_history_connection() as conn:
        if await _owned(conn, tenant_id, principal_id, workspace_id) is None:
            return _fail("not_found", "No such workspace.")
        rows = await (await conn.execute(
            "SELECT example_id, sql_text, result_signature FROM session_state.kb_examples "
            "WHERE tenant_id = %s AND workspace_id = %s AND approved "
            "ORDER BY checked_at ASC NULLS FIRST LIMIT %s",
            (tenant_id, workspace_id, int(cfg.example_checks.max_per_check)))).fetchall()
    counts = {"ok": 0, "changed": 0, "failed": 0}
    outcomes: list[tuple[str, str, str | None, str | None]] = []
    for example_id, sql_text, recorded in rows:
        result = await run_select(sql_text, max_rows=1)
        if not result.get("ok"):
            message = str((result.get("error") or {}).get("message", "The query failed."))
            outcomes.append((example_id, "failed", recorded, message[:_CHECK_DETAIL_MAX_CHARS]))
            continue
        signature = result_signature(result.get("columns", []), int(result.get("row_count", 0)))
        if recorded is None or recorded == signature:
            outcomes.append((example_id, "ok", signature, None))
        else:
            detail = f"Result shape changed. Was {recorded}; now {signature}."
            outcomes.append((example_id, "changed", recorded, detail[:_CHECK_DETAIL_MAX_CHARS]))
    checked_at = datetime.now(timezone.utc)
    async with get_history_connection() as conn:
        for example_id, state, signature, detail in outcomes:
            counts[state] += 1
            await conn.execute(
                "UPDATE session_state.kb_examples SET check_state = %s, result_signature = %s, check_detail = %s, "
                "checked_at = %s WHERE tenant_id = %s AND workspace_id = %s AND example_id = %s",
                (state, signature, detail, checked_at, tenant_id, workspace_id, example_id))
    return {"ok": True, "checked": len(outcomes), "counts": counts, "checked_at": checked_at.isoformat()}


async def _examples(tenant_id, principal_id, workspace_id=None, **_):
    """The workspace's approved examples with their check state, worst first."""
    async with get_history_connection() as conn:
        if await _owned(conn, tenant_id, principal_id, workspace_id) is None:
            return _fail("not_found", "No such workspace.")
        rows = await (await conn.execute(
            "SELECT example_id, question, sql_text, source, check_state, checked_at, check_detail "
            "FROM session_state.kb_examples WHERE tenant_id = %s AND workspace_id = %s AND approved",
            (tenant_id, workspace_id))).fetchall()
    def severity(row: tuple) -> tuple[int, str]:
        _id, question, _sql, _source, state, *_rest = row
        return _CHECK_SEVERITY.get(state, len(_CHECK_SEVERITY)), question.lower()

    rows = sorted(rows, key=severity)
    return {"ok": True, "examples": [
        {"example_id": i, "question": q, "sql_text": s, "source": src, "check_state": st,
         "checked_at": at.isoformat() if at else None, "check_detail": d}
        for i, q, s, src, st, at, d in rows]}


async def _start_run(tenant_id, principal_id, workspace_id=None, graded_by=None, questions=None, **_):
    if graded_by not in ("reference", "owner"):
        return _fail("invalid", "graded_by must be reference or owner.")
    if not isinstance(questions, list) or not questions or len(questions) > int(cfg.workspaces.setup_questions):
        return _fail("invalid", f"questions must list 1 to {cfg.workspaces.setup_questions} questions.")
    cleaned: list[tuple[str, str | None]] = []
    for q in questions:
        text = clean_text(q.get("question") if isinstance(q, dict) else None, int(cfg.workspaces.question_max_chars))
        reference = q.get("reference_sql") if isinstance(q, dict) else None
        if text is None or (reference is not None and (not isinstance(reference, str) or _check_layer1(reference))):
            return _fail("invalid", "Every setup question needs text, and any reference SQL must pass the SQL gate.")
        if (graded_by == "reference") != (reference is not None):
            return _fail("invalid", "Reference-graded runs need reference SQL for every question; owner-graded runs none.")
        cleaned.append((text, reference))
    run_id = str(uuid.uuid4())
    async with get_history_connection() as conn:
        if await _owned(conn, tenant_id, principal_id, workspace_id) is None:
            return _fail("not_found", "No such workspace.")
        await conn.execute(
            "INSERT INTO session_state.setup_runs (run_id, workspace_id, tenant_id, graded_by, total) VALUES (%s, %s, %s, %s, %s)",
            (run_id, workspace_id, tenant_id, graded_by, len(cleaned)))
        for position, (text, reference) in enumerate(cleaned, start=1):
            await conn.execute(
                "INSERT INTO session_state.setup_results (run_id, tenant_id, position, question, reference_sql) "
                "VALUES (%s, %s, %s, %s, %s)", (run_id, tenant_id, position, text, reference))
    return await _get_run(tenant_id, principal_id, workspace_id=workspace_id, run_id=run_id)


async def _owned_run(conn, tenant_id, principal_id, run_id, workspace_id=None):
    """(run_id, workspace_id) if the run is this owner's (and in workspace_id, when given)."""
    if not isinstance(run_id, str) or not run_id:
        return None
    row = await (await conn.execute(
        """
        SELECT r.run_id, r.workspace_id FROM session_state.setup_runs r
        JOIN session_state.workspaces w ON w.workspace_id = r.workspace_id
        WHERE r.tenant_id = %s AND w.owner_principal_id = %s AND r.run_id = %s
        """, (tenant_id, principal_id, run_id))).fetchone()
    if row is None or (workspace_id is not None and row[1] != workspace_id):
        return None
    return row


async def _record_result(tenant_id, principal_id, run_id=None, position=None, verdict=None,
                         answer_text=None, answer_sql=None, detail=None, workspace_id=None, **_):
    if verdict not in _VERDICTS:
        return _fail("invalid", f"verdict must be one of {sorted(_VERDICTS)}.")
    async with get_history_connection() as conn:
        if await _owned_run(conn, tenant_id, principal_id, run_id, workspace_id) is None:
            return _fail("not_found", "No such setup run.")
        row = await (await conn.execute(
            """
            UPDATE session_state.setup_results SET verdict = %s,
                answer_text = COALESCE(%s, answer_text), answer_sql = COALESCE(%s, answer_sql),
                detail = COALESCE(%s, detail)
            WHERE run_id = %s AND position = %s RETURNING position
            """, (verdict, answer_text, answer_sql, detail, run_id, position))).fetchone()
    return {"ok": True} if row else _fail("not_found", "No such question in that run.")


async def _finish_run(tenant_id, principal_id, run_id=None, failed=False, workspace_id=None, **_):
    """Score the run, and on success make every right answer an approved example."""
    async with get_history_connection() as conn:
        owned = await _owned_run(conn, tenant_id, principal_id, run_id, workspace_id)
        if owned is None:
            return _fail("not_found", "No such setup run.")
        workspace_id = owned[1]
        results = await (await conn.execute(
            "SELECT question, answer_sql, verdict FROM session_state.setup_results WHERE run_id = %s", (run_id,))).fetchall()
        score = sum(1 for _q, _s, v in results if v == "right")
        await conn.execute(
            "UPDATE session_state.setup_runs SET state = %s, score = %s, finished_at = now() WHERE run_id = %s",
            ("failed" if failed else "done", score, run_id))
        if not failed:   # a run that stopped early does not stand for the workspace
            await conn.execute(
                "UPDATE session_state.workspaces SET setup_score = %s, setup_total = %s, setup_state = 'tested', "
                "updated_at = now() WHERE workspace_id = %s", (score, len(results), workspace_id))
        # An answer the owner has since marked wrong stops being an example.
        for question, _sql, verdict in results:
            if verdict == "wrong":
                await conn.execute(
                    "DELETE FROM session_state.kb_examples WHERE workspace_id = %s AND question_norm = %s "
                    "AND source = 'setup_test'", (workspace_id, normalize_question(question)))
    for question, sql, verdict in results:
        if verdict == "right" and sql and not _check_layer1(sql):
            await _add_example(tenant_id, principal_id, workspace_id=workspace_id, question=question, sql=sql,
                               source="setup_test")
    return await _get_run(tenant_id, principal_id, workspace_id=workspace_id, run_id=run_id)


async def _get_run(tenant_id, principal_id, workspace_id=None, run_id=None, **_):
    """A run with its results: the given one, or the workspace's latest."""
    async with get_history_connection() as conn:
        if run_id is None:
            if await _owned(conn, tenant_id, principal_id, workspace_id) is None:
                return _fail("not_found", "No such workspace.")
            latest = await (await conn.execute(
                "SELECT run_id FROM session_state.setup_runs WHERE workspace_id = %s ORDER BY started_at DESC LIMIT 1",
                (workspace_id,))).fetchone()
            if latest is None:
                return {"ok": True, "run": None}
            run_id = latest[0]
        elif await _owned_run(conn, tenant_id, principal_id, run_id, workspace_id) is None:
            return _fail("not_found", "No such setup run.")
        run = await (await conn.execute(
            "SELECT run_id, workspace_id, state, graded_by, score, total, started_at, finished_at "
            "FROM session_state.setup_runs WHERE run_id = %s", (run_id,))).fetchone()
        results = await (await conn.execute(
            "SELECT position, question, reference_sql IS NOT NULL, answer_text, answer_sql, verdict, detail "
            "FROM session_state.setup_results WHERE run_id = %s ORDER BY position", (run_id,))).fetchall()
    rid, wid, state, graded, score, total, started, finished = run
    return {"ok": True, "run": {
        "run_id": rid, "workspace_id": wid, "state": state, "graded_by": graded, "score": score, "total": total,
        "started_at": started.isoformat(), "finished_at": finished.isoformat() if finished else None,
        "results": [{"position": p, "question": q, "has_reference": ref, "answer_text": at, "answer_sql": s,
                     "verdict": v, "detail": d} for p, q, ref, at, s, v, d in results],
    }}


async def _set_verdict(tenant_id, principal_id, run_id=None, position=None, verdict=None, workspace_id=None, **_):
    """The owner marks an answer right or wrong (runs graded by the owner), and the score follows."""
    if verdict not in ("right", "wrong"):
        return _fail("invalid", "verdict must be right or wrong.")
    async with get_history_connection() as conn:
        owned = await _owned_run(conn, tenant_id, principal_id, run_id, workspace_id)
        if owned is None:
            return _fail("not_found", "No such setup run.")
        graded = (await (await conn.execute(
            "SELECT graded_by FROM session_state.setup_runs WHERE run_id = %s", (run_id,))).fetchone())[0]
        if graded != "owner":
            return _fail("invalid", "This run is graded against reference answers, not by hand.")
    result = await _record_result(tenant_id, principal_id, run_id=run_id, position=position, verdict=verdict)
    if not result.get("ok"):
        return result
    return await _finish_run(tenant_id, principal_id, run_id=run_id)


_HANDLERS = {
    "list": _list, "create": _create, "get": _get, "update": _update, "delete": _delete, "scan": _scan,
    "catalog": _catalog, "save_descriptions": _save_descriptions, "definitions": _definitions,
    "save_definitions": _save_definitions, "delete_definition": _delete_definition, "approve": _approve,
    "add_example": _add_example, "remove_example": _remove_example, "context": _context, "templates": _templates,
    "check_examples": _check_examples, "examples": _examples,
    "start_run": _start_run, "record_result": _record_result,
    "finish_run": _finish_run, "get_run": _get_run, "set_verdict": _set_verdict,
}


async def manage(action: str, principal_id: str, **params: Any) -> dict[str, Any]:
    """workspaces.manage -- see the module docstring for the actions and guarantees."""
    tenant_id = get_tenant_id()
    if not isinstance(principal_id, str) or not principal_id:
        return _fail("invalid", "principal_id is required.")
    if action not in _ACTIONS:
        return _fail("invalid", f"Unknown action {action!r}. Valid: {sorted(_ACTIONS)}.")
    if action not in _NO_WORKSPACE and "run_id" not in params and not params.get("workspace_id"):
        return _fail("invalid", f"workspace_id is required for {action!r}.")
    return await _HANDLERS[action](tenant_id, principal_id, **params)

"""
mcp/tools/workspace_tools.py
workspaces.manage: workspaces and what the owner has taught StateSync about each one.

One JSON-RPC method takes an `action`:

  list, create, get, update, delete        the workspace itself
  scan                                     read the data source's catalog
  catalog, save_descriptions               tables and columns, and what they mean
  definitions, save_definitions,
  delete_definition                        business terms
  approve                                  the owner accepts the knowledge
  add_example, remove_example              an approved question -> SQL pair, and taking one back
  examples                                 list the approved examples
  context                                  what a question in this workspace may use (approved knowledge only)

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
from typing import Any

from psycopg.types.json import Jsonb

import connections
from config_loader import cfg
from db import get_history_connection
from serialization import to_json_safe
from session import get_tenant_id
from tools.data_tools import _check_layer1, sample_rows
from tools.schema_tools import ALLOWED_SCHEMAS, describe_table, list_tables

logger = logging.getLogger(__name__)

_ACTIONS = frozenset({
    "list", "create", "get", "update", "delete", "scan", "catalog", "save_descriptions",
    "definitions", "save_definitions", "delete_definition", "approve", "add_example", "remove_example",
    "context", "examples",
})
_NO_WORKSPACE = frozenset({"list", "create"})
_DESCRIPTION_SOURCES = frozenset({"ai", "owner"})
# A definition may also come from an uploaded document (migration 025); it then
# carries a citation, and like an AI draft it never replaces the owner's words.
_DEFINITION_SOURCES = frozenset({"ai", "owner", "document"})
_CITATION_FIELDS = ("document_id", "chunk_id", "page", "title", "quote")


def clean_citation(value: Any) -> dict[str, Any] | None:
    """A document citation {document_id, chunk_id, page, title, quote}, or None if malformed."""
    if not isinstance(value, dict):
        return None
    chunk_id = clean_text(value.get("chunk_id"), int(cfg.library.title_max_chars))
    document_id = clean_text(value.get("document_id"), int(cfg.library.title_max_chars))
    quote = clean_text(value.get("quote"), int(cfg.workspaces.mine_quote_max_chars))
    title = clean_text(value.get("title"), int(cfg.library.title_max_chars)) or ""
    page = value.get("page")
    if chunk_id is None or document_id is None or quote is None:
        return None
    if page is not None and (not isinstance(page, int) or isinstance(page, bool) or page < 0):
        return None
    return {"document_id": document_id, "chunk_id": chunk_id, "page": page, "title": title, "quote": quote}
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
    if target_schema not in ALLOWED_SCHEMAS and not connections.is_token(target_schema):
        return _fail("invalid", f"Unknown dataset {target_schema!r}.")
    context = clean_text(business_context or "", int(cfg.workspaces.business_context_max_chars), allow_empty=True) or ""
    workspace_id = str(uuid.uuid4())
    async with get_history_connection() as conn:
        if connections.is_token(target_schema) and not await connections.owns_dataset(
                conn, tenant_id, principal_id, target_schema):
            return _fail("invalid", "Unknown dataset: that connection is not yours, or has no schema chosen yet.")
        count = (await (await conn.execute(
            "SELECT count(*) FROM session_state.workspaces WHERE tenant_id = %s AND owner_principal_id = %s",
            (tenant_id, principal_id))).fetchone())[0]
        if count >= int(cfg.workspaces.max_per_principal):
            return _fail("limit_reached", f"You already have {count} workspaces; the limit is "
                                          f"{cfg.workspaces.max_per_principal}.")
        await conn.execute(
            """
            INSERT INTO session_state.workspaces
                (workspace_id, tenant_id, owner_principal_id, name, target_schema, business_context, context_version,
                 connection_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (workspace_id, tenant_id, principal_id, clean_name, target_schema, context, 1 if context else 0,
             target_schema if connections.is_token(target_schema) else None),
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
            "SELECT definition_id, term, definition, source, approved, cite_document_id, cite_chunk_id, cite_page, "
            "cite_title, cite_quote FROM session_state.kb_definitions "
            "WHERE workspace_id = %s ORDER BY lower(term)", (workspace_id,))).fetchall()
    return {"ok": True, "definitions": [
        {"definition_id": i, "term": t, "definition": d, "source": s, "approved": a,
         "citation": dict(zip(_CITATION_FIELDS, cite)) if cite[1] else None}
        for i, t, d, s, a, *cite in rows]}


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
                if term is None or text is None or source not in _DEFINITION_SOURCES:
                    raise _Abort(_fail("invalid", "Every definition needs a term, a definition and a source of "
                                                  "ai, owner or document."))
                citation = clean_citation(item.get("citation")) if source == "document" else None
                if source == "document" and citation is None:
                    raise _Abort(_fail("invalid", "A definition from a document needs its citation "
                                                  "(document_id, chunk_id, quote)."))
                exists = (await (await conn.execute(
                    "SELECT 1 FROM session_state.kb_definitions WHERE workspace_id = %s AND lower(term) = lower(%s)",
                    (workspace_id, term))).fetchone()) is not None
                if not exists:
                    if count >= int(cfg.workspaces.definitions_max):
                        raise _Abort(_fail("limit_reached", f"A workspace holds at most {cfg.workspaces.definitions_max} definitions."))
                    count += 1
                await conn.execute(
                    """
                    INSERT INTO session_state.kb_definitions (definition_id, workspace_id, tenant_id, term, definition,
                        source, cite_document_id, cite_chunk_id, cite_page, cite_title, cite_quote)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (workspace_id, lower(term)) DO UPDATE SET
                        term = EXCLUDED.term, definition = EXCLUDED.definition, source = EXCLUDED.source,
                        -- an owner edit keeps the citation, so the origin stays visible
                        cite_document_id = COALESCE(EXCLUDED.cite_document_id, session_state.kb_definitions.cite_document_id),
                        cite_chunk_id = COALESCE(EXCLUDED.cite_chunk_id, session_state.kb_definitions.cite_chunk_id),
                        cite_page = CASE WHEN EXCLUDED.cite_chunk_id IS NOT NULL THEN EXCLUDED.cite_page
                                         ELSE session_state.kb_definitions.cite_page END,
                        cite_title = COALESCE(EXCLUDED.cite_title, session_state.kb_definitions.cite_title),
                        cite_quote = COALESCE(EXCLUDED.cite_quote, session_state.kb_definitions.cite_quote),
                        approved = FALSE, updated_at = now()
                    -- a draft (AI or document) never replaces the owner's definition
                    WHERE EXCLUDED.source = 'owner' OR session_state.kb_definitions.source IN ('ai', 'document')
                    """,
                    (str(uuid.uuid4()), workspace_id, tenant_id, term, text, source,
                     *((citation[k] for k in _CITATION_FIELDS) if citation else (None,) * len(_CITATION_FIELDS))))
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
    return {"ok": True, "example_id": example_id}


async def add_pinned_example(tenant_id: str, principal_id: str, workspace_id: Any, question: str, sql: str,
                             target_schema: str) -> dict[str, Any]:
    """A pinned tile's answer as an approved example (tile_tools property 6). The workspace must be
    the owner's AND use the dataset the answer came from: an example for another dataset's tables
    would be shown to the wrong prompts and fail its check."""
    async with get_history_connection() as conn:
        owned = await _owned(conn, tenant_id, principal_id, workspace_id)
    if owned is None:
        return _fail("not_found", "No such workspace.")
    if owned[1] != target_schema:
        return _fail("invalid", f"This answer is from the {target_schema} dataset; the workspace uses {owned[1]}.")
    # Pinning means "watch this", not "the earlier approval was wrong": an existing approved
    # answer to the same question is kept unless its SQL is the same (then nothing changes).
    async with get_history_connection() as conn:
        existing = await (await conn.execute(
            "SELECT example_id, sql_text FROM session_state.kb_examples "
            "WHERE tenant_id = %s AND workspace_id = %s AND question_norm = %s",
            (tenant_id, workspace_id, normalize_question(clean_text(question, int(cfg.workspaces.question_max_chars)) or "")),
        )).fetchone()
    if existing is not None:
        if existing[1].strip() == (sql or "").strip():
            return {"ok": True, "example_id": existing[0]}
        return _fail("exists", "This question already has an approved answer with different SQL; it was kept.")
    return await _add_example(tenant_id, principal_id, workspace_id=workspace_id, question=question, sql=sql,
                              source="tile")


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


async def _examples(tenant_id, principal_id, workspace_id=None, **_):
    """The workspace's approved examples."""
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


_HANDLERS = {
    "list": _list, "create": _create, "get": _get, "update": _update, "delete": _delete, "scan": _scan,
    "catalog": _catalog, "save_descriptions": _save_descriptions, "definitions": _definitions,
    "save_definitions": _save_definitions, "delete_definition": _delete_definition, "approve": _approve,
    "add_example": _add_example, "remove_example": _remove_example, "context": _context, "examples": _examples,
}


async def manage(action: str, principal_id: str, **params: Any) -> dict[str, Any]:
    """workspaces.manage -- see the module docstring for the actions and guarantees."""
    tenant_id = get_tenant_id()
    if not isinstance(principal_id, str) or not principal_id:
        return _fail("invalid", "principal_id is required.")
    if action not in _ACTIONS:
        return _fail("invalid", f"Unknown action {action!r}. Valid: {sorted(_ACTIONS)}.")
    if action not in _NO_WORKSPACE and not params.get("workspace_id"):
        return _fail("invalid", f"workspace_id is required for {action!r}.")
    return await _HANDLERS[action](tenant_id, principal_id, **params)

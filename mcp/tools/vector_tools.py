"""
mcp/tools/vector_tools.py
MCP vector/retrieval tools: vector.semantic_search (A4.6), vector.hybrid_select
(A4.7), docs.get_passage (A4.9). Part E1.

vector.hybrid_select and the ss_embed mechanism (A4.7):
  The submitted SQL may call the registered function ss_embed(query_text).
  ss_embed itself (migrations/004_ss_embed.sql) makes no network call --
  it is STABLE and reads a session-local GUC. The actual embedding call
  happens here, in mcp, before the SQL ever reaches postgres:
    1. Run the base Layer 1 statement-type gate (shared with data.run_select).
    2. Parse the (already-validated) SQL with pglast's Visitor to find every
       FuncCall node. Reject any call to a function in the dangerous-function
       denylist below (the "extended to allow exactly that one function and
       no other non-builtin function" part of A4.7 -- exhaustively
       allowlisting Postgres's ~2000 real builtins would break ordinary
       SELECT queries, so this targets the actually dangerous surface:
       file I/O, superuser/admin, and long-sleep functions).
    3. Collect the literal string argument(s) passed to ss_embed(...), if any.
    4. Embed that text via Ollama (embeddings.py), same call shape as
       memory's trajectory embeddings but retrieval's own model/dim config.
    5. SET LOCAL statesync.query_vector = '<vector literal>' inside the same
       transaction as the query, then execute the ORIGINAL SQL text
       unmodified. ss_embed(), when postgres evaluates it, reads that GUC.
  The SQL text itself is never rewritten -- only a session GUC is set ahead
  of it -- which is both simpler and safer than substituting into
  LLM-generated SQL.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import psycopg

from config_loader import cfg
from db import get_connection
from embeddings import embed_text
from session import enforce_tenancy_policy, get_tenant_id, get_workspace_id
from tools.data_tools import DENIED_FUNCTIONS, _check_layer1, check_internal_schemas

logger = logging.getLogger(__name__)

try:
    import pglast
    from pglast.visitors import Visitor
    _PGLAST_AVAILABLE = True
except ImportError:
    _PGLAST_AVAILABLE = False

_CHUNK_ID_RE = re.compile(r'^[A-Za-z0-9_\-]{1,128}$')
_UPLOAD_PREFIX = "up_"   # uploaded documents' ids (mcp/tools/library_tools.py)

# The function denylist lives in data_tools (Layer 1) and now covers every
# model-SQL path; ss_embed is not on it.
_DENIED_FUNCTIONS = DENIED_FUNCTIONS


class _FuncCallCollector(Visitor if _PGLAST_AVAILABLE else object):
    """Walks a parsed SQL statement collecting every FuncCall's name and args."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list]] = []

    def visit_FuncCall(self, ancestors, node):  # noqa: N802 — pglast visitor naming
        name = ".".join(n.sval for n in node.funcname).lower()
        self.calls.append((name, list(node.args or [])))


def _format_vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _collect_relations(parsed) -> set[str]:
    """Relation names referenced anywhere in the parsed statement."""
    found: set[str] = set()

    def walk(node) -> None:
        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return
        if type(node).__name__ == "RangeVar" and getattr(node, "relname", None):
            found.add(str(node.relname).lower())
        for attr in getattr(node, "__slots__", ()) or ():
            try:
                walk(getattr(node, attr))
            except AttributeError:
                continue
    walk(parsed)
    return found


def _check_tenant_predicate(sql: str, parsed) -> str | None:
    """
    Under tenancy.enforced=true, a hybrid_select touching a tenant-scoped
    relation must carry an explicit `tenant_id = '<this tenant>'` predicate.

    semantic_search and get_passage build their own SQL and filter on
    tenant_id unconditionally, so they were never the hole. hybrid_select
    executes MODEL-AUTHORED SQL, so without this a generated query reading
    document_chunks with no tenant predicate would read across tenants. The
    predicate is REQUIRED rather than injected: silently rewriting a caller's
    SQL would make the executed statement differ from the audited one, and
    A4.5's whole premise is that what Layer 1 approved is what runs.
    """
    policy = enforce_tenancy_policy(get_tenant_id())
    if not policy.enforced:
        return None

    touched = {r for r in _collect_relations(parsed) if policy.requires_tenant_predicate(r)}
    if not touched:
        return None

    expected = re.compile(
        r"tenant_id\s*=\s*'" + re.escape(policy.tenant_id) + r"'", re.IGNORECASE
    )
    if expected.search(sql):
        return None
    return (
        f"Rejected: tenancy.enforced=true and this query reads "
        f"{sorted(touched)}, which are tenant-scoped, without an explicit "
        f"tenant_id = '{policy.tenant_id}' predicate. Add the predicate; it is "
        f"required rather than auto-injected so the audited SQL is the "
        f"executed SQL (A4.5)."
    )


def _check_hybrid_gate(sql: str) -> tuple[str | None, str | None]:
    """
    Extended Layer 1 for vector.hybrid_select (A4.7).

    Returns (rejection_reason, ss_embed_literal). rejection_reason is None
    when the SQL is accepted; ss_embed_literal is the text argument found in
    the (sole distinct) ss_embed(...) call, or None if the SQL doesn't call it.
    """
    rejection = _check_layer1(sql) or check_internal_schemas(sql)
    if rejection:
        return rejection, None

    if not _PGLAST_AVAILABLE:
        logger.error("SECURITY: pglast unavailable — hybrid_select function gate bypassed.")
        return None, None

    try:
        parsed = pglast.parse_sql(sql)
    except pglast.Error as exc:
        return f"SQL parse error: {exc}", None

    collector = _FuncCallCollector()
    for stmt in parsed:
        collector(stmt)

    literals: set[str] = set()
    for name, args in collector.calls:
        short_name = name.rsplit(".", 1)[-1]
        if short_name == "ss_embed":
            if args and type(args[0]).__name__ == "A_Const" and hasattr(args[0].val, "sval"):
                literals.add(args[0].val.sval)
            continue
        if short_name in _DENIED_FUNCTIONS:
            return f"Rejected: function {short_name!r} is not permitted.", None

    tenant_rejection = _check_tenant_predicate(sql, parsed)
    if tenant_rejection:
        return tenant_rejection, None

    if len(literals) > 1:
        return (
            "Rejected: multiple distinct ss_embed(...) literals in one "
            "query are not supported (only one query-side vector can be "
            "bound per request).",
            None,
        )

    return None, (next(iter(literals)) if literals else None)


# ── A4.6 vector.semantic_search ─────────────────────────────────────────────
async def semantic_search(
    query_text: str,
    top_k: int | None = None,
    document_ids: list[str] | None = None,
) -> dict[str, Any]:
    top_k_max = cfg.retrieval.semantic_top_k_max
    if top_k is not None and not (1 <= top_k <= top_k_max):
        raise ValueError(f"top_k must be between 1 and {top_k_max}, got {top_k!r}")

    tenant_id = get_tenant_id()
    effective_top_k = top_k if top_k is not None else cfg.retrieval.semantic_top_k

    vector = await embed_text(
        query_text,
        model=cfg.retrieval.embedding_model,
        dim=cfg.retrieval.embedding_dim,
    )
    vector_literal = _format_vector_literal(vector)

    where_clauses = ["dc.tenant_id = %(tenant_id)s"]
    params: dict[str, Any] = {"tenant_id": tenant_id, "qvec": vector_literal, "top_k": effective_top_k}
    if document_ids:
        where_clauses.append("dc.document_id = ANY(%(document_ids)s)")
        params["document_ids"] = document_ids

    sql = f"""
        SELECT dc.chunk_id, dc.document_id, dc.content_md, dc.page,
               1 - (dc.embedding <=> %(qvec)s::vector) AS score, NULL::text AS title
        FROM document_chunks dc
        WHERE {' AND '.join(where_clauses)}
        ORDER BY dc.embedding <=> %(qvec)s::vector
        LIMIT %(top_k)s
    """

    workspace_id = get_workspace_id()
    async with get_connection() as conn:
        await _use_exact_search(conn)
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
        col_names = [desc.name for desc in cur.description] if cur.description else []
        matches = [dict(zip(col_names, row)) for row in rows]
        library = await _library_matches(conn, workspace_id, params) if workspace_id else []

    if library:
        # Same model and the same cosine measure on both sides, so the scores
        # are comparable: merge and keep the best top_k overall.
        matches = sorted(matches + library, key=lambda m: m["score"], reverse=True)[:effective_top_k]

    logger.info(
        "vector.semantic_search: tenant=%s workspace=%s top_k=%d matches=%d library_matches=%d",
        tenant_id, workspace_id, effective_top_k, len(matches), len(library),
    )
    return {"matches": matches}


async def _library_matches(conn: Any, workspace_id: str, params: dict[str, Any]) -> list[dict]:
    """Chunks of READY uploaded documents in the folders this workspace chose.

    Uploads (docs/V3_PLAN.md Phase 3c slice 5, migration 024) live in
    workspace_docs, apart from the sample corpus. The workspace comes from the
    session (X-Workspace-ID); the orchestrator has already checked that it is
    the caller's own, and row-level security plus the tenant predicate keep
    every other tenant's links, documents and chunks out of reach.
    """
    where = ["wf.workspace_id = %(workspace_id)s", "c.tenant_id = %(tenant_id)s", "d.status = 'ready'"]
    if "document_ids" in params:
        where.append("c.document_id = ANY(%(document_ids)s)")
    cur = await conn.execute(f"""
        SELECT c.chunk_id, c.document_id, c.content_md, c.page,
               1 - (c.embedding <=> %(qvec)s::vector) AS score, d.title, c.section, c.kind
        FROM workspace_docs.chunks c
        JOIN workspace_docs.documents d ON d.document_id = c.document_id
        JOIN workspace_docs.workspace_folders wf ON wf.folder_id = d.folder_id
        WHERE {' AND '.join(where)}
        ORDER BY c.embedding <=> %(qvec)s::vector
        LIMIT %(top_k)s
    """, {**params, "workspace_id": workspace_id})
    rows = await cur.fetchall()
    col_names = [desc.name for desc in cur.description] if cur.description else []
    return [dict(zip(col_names, row)) for row in rows]


async def _use_exact_search(conn: Any) -> None:
    """Make this transaction's vector ORDER BY exact instead of approximate.

    With retrieval.exact_vector_search on, index scans are switched off for the
    current transaction only, so the planner cannot pick the approximate HNSW
    index and ranks every chunk by true cosine distance (see the config
    comment for the measurement behind this). SET LOCAL ends with the
    transaction, and get_connection() always rolls back, so it never reaches
    the next pooled use of the connection.
    """
    if cfg.retrieval.exact_vector_search:
        await conn.execute("SET LOCAL enable_indexscan = off")


# ── A4.7 vector.hybrid_select ───────────────────────────────────────────────
async def hybrid_select(
    sql: str,
    max_rows: int | None = None,
) -> dict[str, Any]:
    effective_max = min(max(1, max_rows if max_rows is not None else cfg.mcp.max_rows_default), cfg.mcp.max_rows_hard_cap)

    rejection, ss_embed_literal = _check_hybrid_gate(sql)
    if rejection:
        logger.warning("hybrid_select gate rejected SQL: %s", rejection)
        return {
            "ok": False, "columns": [], "rows": [], "row_count": 0, "truncated": False,
            "error": {"sqlstate": None, "message": f"[Layer 1 Gate] {rejection}"},
        }

    async with get_connection() as conn:
        try:
            if ss_embed_literal is not None:
                vector = await embed_text(
                    ss_embed_literal,
                    model=cfg.retrieval.embedding_model,
                    dim=cfg.retrieval.embedding_dim,
                )
                # SET LOCAL: scoped to this transaction only, cleared on the
                # rollback get_connection() always performs -- never leaks
                # into the next pooled use of this connection.
                await conn.execute(
                    "SELECT set_config('statesync.query_vector', %s, true)",
                    (_format_vector_literal(vector),),
                )

            await _use_exact_search(conn)
            cur = await conn.execute(sql)
            rows = await cur.fetchmany(effective_max + 1)
            col_names = [desc.name for desc in cur.description] if cur.description else []
            truncated = len(rows) > effective_max
            result_rows = [dict(zip(col_names, row)) for row in rows[:effective_max]]

            logger.info(
                "vector.hybrid_select: ok=True rows=%d truncated=%s used_ss_embed=%s",
                len(result_rows), truncated, ss_embed_literal is not None,
            )
            return {
                "ok": True, "columns": col_names, "rows": result_rows,
                "row_count": len(result_rows), "truncated": truncated, "error": None,
            }

        except psycopg.errors.InsufficientPrivilege as exc:
            logger.warning("vector.hybrid_select: permission denied (42501): %s", exc)
            return {
                "ok": False, "columns": [], "rows": [], "row_count": 0, "truncated": False,
                "error": {"sqlstate": "42501", "message": str(exc)},
            }
        except psycopg.Error as exc:
            sqlstate = exc.sqlstate if hasattr(exc, "sqlstate") else None
            logger.warning("vector.hybrid_select: query error sqlstate=%s message=%s", sqlstate, exc)
            return {
                "ok": False, "columns": [], "rows": [], "row_count": 0, "truncated": False,
                "error": {"sqlstate": sqlstate, "message": str(exc)},
            }


# ── A4.9 docs.get_passage ───────────────────────────────────────────────────
async def get_passage(chunk_id: str) -> dict[str, Any]:
    if not _CHUNK_ID_RE.match(chunk_id):
        raise ValueError(f"Invalid chunk_id: {chunk_id!r}")

    tenant_id = get_tenant_id()
    if chunk_id.startswith(_UPLOAD_PREFIX):
        # An uploaded document's chunk (workspace_docs, migration 024); it has
        # no source URI, so its file name stands in.
        sql = """
            SELECT c.chunk_id, c.document_id, c.content_md, c.page, d.filename AS source_uri, d.title
            FROM workspace_docs.chunks c
            JOIN workspace_docs.documents d ON d.document_id = c.document_id
            WHERE c.chunk_id = %(chunk_id)s AND c.tenant_id = %(tenant_id)s
        """
    else:
        sql = """
            SELECT dc.chunk_id, dc.document_id, dc.content_md, dc.page, d.source_uri, d.title
            FROM document_chunks dc
            JOIN documents d ON d.document_id = dc.document_id
            WHERE dc.chunk_id = %(chunk_id)s AND dc.tenant_id = %(tenant_id)s
        """
    async with get_connection() as conn:
        cur = await conn.execute(sql, {"chunk_id": chunk_id, "tenant_id": tenant_id})
        row = await cur.fetchone()

    if row is None:
        raise ValueError(f"chunk_id not found: {chunk_id!r}")

    col_names = [desc.name for desc in cur.description]
    result = dict(zip(col_names, row))
    logger.debug("docs.get_passage: chunk_id=%s document_id=%s", chunk_id, result["document_id"])
    return result

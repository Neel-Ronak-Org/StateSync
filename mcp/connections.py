"""
mcp/connections.py
The owner's own Postgres (docs/V3_PLAN.md Phase 3c slice 6, migration 026).

A saved connection's id, cn_<16 hex>, is used as a DATASET NAME wherever
StateSync names a dataset (a workspace's target_schema, a tile's, a turn's).
Everything that already carries a dataset name therefore reaches the right
database; only this module turns the name into a host and a schema, and only
for the tenant that owns it.

The rules, all enforced here, in the MCP, which is still the only holder of
database credentials:

1. THE LOGIN MUST BE READ-ONLY. Before a connection is saved (and whenever it
   is re-tested) `verify` refuses superusers and roles that can create roles
   or databases, replicate or bypass RLS; members of pg_write_all_data and the
   server-file/program roles; and any login holding INSERT, UPDATE, DELETE or
   TRUNCATE on any table. Every session also runs with
   default_transaction_read_only and a statement timeout, and every use is a
   READ ONLY transaction that is rolled back, exactly like our own reader
   pool (db.get_connection, Layer 2).
2. THE PASSWORD IS ENCRYPTED at rest with a key only the MCP holds (Docker
   secret connection_key, Fernet) and is never returned.
3. STATESYNC'S OWN SERVICES ARE NEVER A TARGET. Host names in
   [connections].blocked_hosts, anything they resolve to, and loopback,
   link-local, multicast and unspecified addresses are refused; the address
   checked is the address connected to (hostaddr), so DNS cannot change
   between the check and the connection.
4. A QUERY READS ONE DATABASE. SQL for a connected dataset may name its tables
   as <cn_id>.<table> (how the schema is shown to the model) or unqualified
   (resolved through search_path to the chosen schema); any other schema name
   is refused. The <cn_id> qualifier is replaced by the real schema at the
   exact parse-tree locations, and the result goes through the SQL gate again.
   SQL for a built-in dataset may not name a connection at all.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import os
import re
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import psycopg

from config_loader import cfg
from db import get_history_connection
from session import get_tenant_id

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"^cn_[0-9a-f]{16}$")
_KEY_FILE = os.environ.get("CONNECTION_KEY_FILE", "/run/secrets/connection_key")
_DANGEROUS_ROLES = ("pg_write_all_data", "pg_write_server_files", "pg_read_server_files",
                    "pg_execute_server_program")
_SYSTEM_SCHEMAS = "('pg_catalog', 'information_schema')"
_FUNCTION_MARK = "()"   # marks a function qualifier in _schema_refs (never a valid schema name prefix)


class Refused(Exception):
    """A connection or query that must not proceed; the message is safe to show the owner."""


def is_token(name: Any) -> bool:
    return isinstance(name, str) and bool(TOKEN_RE.match(name))


# ── Encryption ───────────────────────────────────────────────────────────────

_fernet_cache: list[Any] = []


def _fernet() -> Any:
    if not _fernet_cache:
        try:
            from cryptography.fernet import Fernet
            with open(_KEY_FILE, "rb") as fh:
                _fernet_cache.append(Fernet(fh.read().strip()))
        except (OSError, ValueError, ImportError) as exc:
            logger.warning("connections: no usable encryption key (%s); own databases are disabled", exc)
            _fernet_cache.append(None)
    return _fernet_cache[0]


def available() -> bool:
    return _fernet() is not None


def encrypt(secret: str) -> str:
    fernet = _fernet()
    if fernet is None:
        raise Refused("Connections are not set up on this server (no encryption key).")
    return fernet.encrypt(secret.encode()).decode()


def decrypt(token: str) -> str:
    fernet = _fernet()
    if fernet is None:
        raise Refused("Connections are not set up on this server (no encryption key).")
    return fernet.decrypt(token.encode()).decode()


# ── Where a connection may go ────────────────────────────────────────────────

def _addresses(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []
    return list(dict.fromkeys(sockaddr[0] for *_, sockaddr in infos))


def check_host(host: str) -> str:
    """The address to connect to for `host`, or Refused. Blocking (DNS); call via a thread."""
    name = host.strip().lower().rstrip(".")
    blocked_names = {h.lower() for h in cfg.connections.blocked_hosts}
    if name in blocked_names:
        raise Refused(f"{host} is part of StateSync itself. For a database on this computer, "
                      f"use host.docker.internal.")
    addresses = _addresses(name)
    if not addresses:
        raise Refused(f"The host {host} could not be found.")
    blocked_addresses = {a for blocked in blocked_names for a in _addresses(blocked)}
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or address in blocked_addresses:
            raise Refused(f"{host} points at StateSync itself or a local-only address. For a database "
                          f"on this computer, use host.docker.internal.")
    return addresses[0]


def connect_kwargs(host: str, hostaddr: str, port: int, dbname: str, username: str, password: str,
                   sslmode: str, schema: str | None = None) -> dict[str, Any]:
    options = (f"-c default_transaction_read_only=on "
               f"-c statement_timeout={int(cfg.mcp.statement_timeout_ms)} "
               f"-c idle_in_transaction_session_timeout={int(cfg.mcp.statement_timeout_ms)}")
    return {"host": host, "hostaddr": hostaddr, "port": port, "dbname": dbname, "user": username,
            "password": password, "sslmode": sslmode, "connect_timeout": int(cfg.connections.connect_timeout_s),
            "application_name": "statesync", "options": options, "autocommit": True}


def _safe_error(exc: Exception) -> str:
    """A connection error's first line (libpq never includes the password)."""
    text = str(exc).strip().splitlines()
    return text[0][: int(cfg.connections.host_max_chars)] if text else type(exc).__name__


# ── Checking a login ─────────────────────────────────────────────────────────

async def verify(kwargs: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(schemas the login can read, warnings) or Refused with the reason."""
    try:
        conn = await psycopg.AsyncConnection.connect(**kwargs)
    except psycopg.Error as exc:
        raise Refused(f"Could not connect: {_safe_error(exc)}") from None
    try:
        await conn.execute("BEGIN READ ONLY")
        attrs = await (await conn.execute(
            "SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls "
            "FROM pg_catalog.pg_roles WHERE rolname = current_user")).fetchone()
        if attrs is None:
            raise Refused("The login's role could not be read.")
        names = ("is a superuser", "can create roles", "can create databases", "can replicate",
                 "can bypass row-level security")
        powers = [label for label, has in zip(names, attrs, strict=True) if has]
        if powers:
            raise Refused(f"This login {', '.join(powers)}. Use a login that can only read.")
        roles = await (await conn.execute(
            "SELECT r.rolname FROM pg_catalog.pg_roles r WHERE r.rolname = ANY(%s) "
            "AND pg_catalog.pg_has_role(current_user, r.oid, 'MEMBER')", (list(_DANGEROUS_ROLES),))).fetchall()
        if roles:
            raise Refused(f"This login is a member of {', '.join(r[0] for r in roles)}. Use a read-only login.")
        writable = await (await conn.execute(
            "SELECT n.nspname || '.' || c.relname FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND n.nspname NOT IN {_SYSTEM_SCHEMAS} "
            "AND n.nspname NOT LIKE 'pg\\_%%' "
            "AND (pg_catalog.has_table_privilege(c.oid, 'INSERT') OR pg_catalog.has_table_privilege(c.oid, 'UPDATE') "
            "OR pg_catalog.has_table_privilege(c.oid, 'DELETE') OR pg_catalog.has_table_privilege(c.oid, 'TRUNCATE')) "
            "LIMIT 3")).fetchall()
        if writable:
            raise Refused(f"This login can change data (for example in {', '.join(w[0] for w in writable)}). "
                          f"Use a login that can only read.")
        schemas = [r[0] for r in await (await conn.execute(
            "SELECT n.nspname FROM pg_catalog.pg_namespace n "
            f"WHERE n.nspname NOT IN {_SYSTEM_SCHEMAS} AND n.nspname NOT LIKE 'pg\\_%%' "
            "AND pg_catalog.has_schema_privilege(n.oid, 'USAGE') "
            "AND EXISTS (SELECT 1 FROM pg_catalog.pg_class c WHERE c.relnamespace = n.oid "
            "            AND c.relkind IN ('r', 'p', 'v', 'm') AND pg_catalog.has_table_privilege(c.oid, 'SELECT')) "
            "ORDER BY n.nspname LIMIT %s", (int(cfg.connections.sample_schemas_max),))).fetchall()]
        warnings: list[str] = []
        creatable = await (await conn.execute(
            "SELECT count(*) FROM pg_catalog.pg_namespace n "
            f"WHERE n.nspname NOT IN {_SYSTEM_SCHEMAS} AND n.nspname NOT LIKE 'pg\\_%%' "
            "AND pg_catalog.has_schema_privilege(n.oid, 'CREATE')")).fetchone()
        if creatable and creatable[0]:
            warnings.append("This login may create tables in some schema. StateSync only ever runs read-only "
                            "transactions, but a login without that right is safer.")
        await conn.execute("ROLLBACK")
        if not schemas:
            raise Refused("This login cannot read any table. Grant it SELECT on the tables to analyse.")
        return schemas, warnings
    except psycopg.Error as exc:
        raise Refused(f"The login could not be checked: {_safe_error(exc)}") from None
    finally:
        await conn.close()


async def owns_dataset(conn: Any, tenant_id: str, principal_id: str, name: Any) -> bool:
    """Is `name` one of this principal's connections, with a schema chosen (usable as a dataset)?"""
    if not is_token(name):
        return False
    row = await (await conn.execute(
        "SELECT 1 FROM session_state.connections WHERE tenant_id = %s AND owner_principal_id = %s "
        "AND connection_id = %s AND schema_name IS NOT NULL", (tenant_id, principal_id, name))).fetchone()
    return row is not None


# ── Pools and resolution ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Target:
    connection_id: str
    schema_name: str
    kwargs_digest: str


_pools: dict[str, tuple[str, Any]] = {}
_pool_lock = asyncio.Lock()


async def _row(connection_id: str) -> tuple | None:
    async with get_history_connection() as conn:
        return await (await conn.execute(
            "SELECT host, port, dbname, username, password_enc, sslmode, schema_name "
            "FROM session_state.connections WHERE tenant_id = %s AND connection_id = %s",
            (get_tenant_id(), connection_id))).fetchone()


async def _kwargs_for(row: tuple) -> dict[str, Any]:
    host, port, dbname, username, password_enc, sslmode, schema_name = row
    hostaddr = await asyncio.to_thread(check_host, host)
    return connect_kwargs(host, hostaddr, port, dbname, username, decrypt(password_enc), sslmode, schema_name)


async def _pool(connection_id: str, kwargs: dict[str, Any]) -> Any:
    from psycopg_pool import AsyncConnectionPool

    digest = hashlib.sha256(repr(sorted(kwargs.items())).encode()).hexdigest()
    async with _pool_lock:
        current = _pools.get(connection_id)
        if current and current[0] == digest:
            return current[1]
        if current:
            await current[1].close()
        pool = AsyncConnectionPool(kwargs=kwargs, min_size=0, max_size=int(cfg.connections.pool_max),
                                   max_idle=float(cfg.connections.pool_idle_s), open=False)
        await pool.open(wait=False)
        _pools[connection_id] = (digest, pool)
        return pool


async def forget(connection_id: str) -> None:
    """Close a connection's pool (after an edit or delete)."""
    async with _pool_lock:
        current = _pools.pop(connection_id, None)
    if current:
        await current[1].close()


async def close_all() -> None:
    for connection_id in list(_pools):
        await forget(connection_id)


@asynccontextmanager
async def external(dataset: str) -> AsyncIterator[tuple[Any, str]]:
    """(connection, real schema) for a connected dataset, inside a READ ONLY transaction that is rolled back.

    Raises Refused for an unknown dataset, one without a chosen schema, or an unreachable database.
    """
    if not is_token(dataset):
        raise Refused(f"Unknown dataset {dataset!r}.")
    row = await _row(dataset)
    if row is None:
        raise Refused(f"Unknown dataset {dataset!r}.")
    schema_name = row[-1]
    if not schema_name:
        raise Refused("Choose which schema of this connection to use first.")
    kwargs = await _kwargs_for(row)
    pool = await _pool(dataset, kwargs)
    try:
        async with pool.connection(timeout=float(cfg.connections.connect_timeout_s)) as conn:
            await conn.execute("BEGIN READ ONLY")
            try:
                await conn.execute("SELECT pg_catalog.set_config('search_path', %s, true)",
                                   (quote_ident(schema_name),))
                yield conn, schema_name
            finally:
                await conn.execute("ROLLBACK")
    except (psycopg.OperationalError, TimeoutError) as exc:
        raise Refused(f"The database could not be reached: {_safe_error(exc)}") from None


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


# ── Mapping SQL onto the real schema ─────────────────────────────────────────

def _schema_refs(sql: str) -> list[tuple[str, int]]:
    """(qualifier, byte offset) for every schema-qualified relation or 3-part column reference."""
    import pglast
    from pglast.visitors import Visitor

    refs: list[tuple[str, int]] = []

    class _Collector(Visitor):
        def visit_RangeVar(self, ancestors, node):  # noqa: N802 - pglast visitor naming
            if getattr(node, "schemaname", None):
                refs.append((node.schemaname, node.location))

        def visit_ColumnRef(self, ancestors, node):  # noqa: N802 - pglast visitor naming
            fields = node.fields or ()
            if len(fields) >= 3 and hasattr(fields[0], "sval"):  # noqa: literals - schema.table.column
                refs.append((fields[0].sval, node.location))

        def visit_FuncCall(self, ancestors, node):  # noqa: N802 - pglast visitor naming
            names = node.funcname or ()
            if len(names) >= 2 and is_token(getattr(names[0], "sval", None)):  # noqa: literals - schema.function
                refs.append((_FUNCTION_MARK + names[0].sval, node.location))

    collector = _Collector()
    for raw in pglast.parse_sql(sql):
        collector(raw.stmt)
    return refs


def datasets_named(sql: str) -> tuple[set[str], set[str]]:
    """(connection tokens, other schema qualifiers) the SQL names. Raises pglast.Error on bad SQL."""
    refs = [(name.removeprefix(_FUNCTION_MARK), location) for name, location in _schema_refs(sql)]
    tokens = {name for name, _ in refs if is_token(name)}
    return tokens, {name for name, _ in refs if not is_token(name)}


def map_sql(sql: str, token: str, schema_name: str) -> str:
    """The SQL with every `token` qualifier replaced by the real schema, at its parse-tree location.

    Refuses a qualifier for any other schema, a token used as a function's
    schema, or a token that is left anywhere in the result.
    """
    refs = _schema_refs(sql)
    if any(name.startswith(_FUNCTION_MARK) for name, _ in refs):
        raise Refused("A question on a connected database cannot call the database's own functions.")
    others = sorted({name for name, _ in refs if name != token})
    if others:
        raise Refused(f"This query names {', '.join(others)}; a question on a connected database can only "
                      f"read its own tables.")
    # pglast reports locations as CHARACTER offsets into the text (checked with
    # non-ASCII text before a reference), so the splice works on the str.
    replacement = quote_ident(schema_name)
    spans: list[tuple[int, int]] = []
    for _, location in refs:
        if location is None or location < 0:
            raise Refused("The query could not be mapped onto the database.")
        for candidate in (f'"{token}"', token):
            if sql[location:location + len(candidate)].lower() == candidate:
                spans.append((location, location + len(candidate)))
                break
        else:
            raise Refused("The query could not be mapped onto the database.")
    mapped = sql
    for start, end in sorted(set(spans), reverse=True):
        mapped = mapped[:start] + replacement + mapped[end:]
    if datasets_named(mapped)[0]:
        raise Refused("The query could not be mapped onto the database.")
    return mapped

"""
tests/test_connections.py
The owner's own Postgres (mcp/connections.py, migration 026): the rules that
need no database.

WHY THIS EXISTS.
- A connected dataset is named cn_<16 hex>; only that exact shape is one.
- SQL for a connected dataset may read only its own tables: the cn_ qualifier
  is mapped onto the real schema at parse-tree positions (never inside a
  string literal, whatever characters come first), and any other schema, a
  second connection, or a function called through the connection is refused.
- SQL for a built-in dataset may not name a connection at all, and that
  refusal happens before any database access.
- StateSync's own services, loopback and link-local addresses are never a
  connection target, whatever name is typed.
- Settings are validated before any network access, and the password is
  encrypted and never part of any view.
The database paths are verified live by scripts/verify_connections.py.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = REPO_ROOT / "mcp"
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(MCP_DIR))

if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        import unittest.mock
        sys.modules["psycopg_pool"] = unittest.mock.MagicMock()

pytest.importorskip("pglast")
pytest.importorskip("psycopg")
import connections as c  # noqa: E402
import tools.library_tools as lt  # noqa: E402
from session import set_session  # noqa: E402
from tools.data_tools import run_select  # noqa: E402

T = "cn_0123456789abcdef"


def test_only_the_exact_shape_is_a_connection():
    assert c.is_token(T)
    for name in ("cn_0123456789ABCDEF", "cn_0123", "olist", "public", None, 5, f"{T}x", f"x{T}"):
        assert not c.is_token(name)


def test_the_qualifier_is_mapped_at_its_positions_only():
    sql = (f"WITH x AS (SELECT o.id FROM {T}.orders o JOIN \"{T}\".items i ON i.o = o.id "
           f"WHERE o.note = 'é {T}.x') SELECT {T}.orders.id FROM x JOIN {T}.orders ON true")
    mapped = c.map_sql(sql, T, 'Sales "Data"')
    assert mapped == ("WITH x AS (SELECT o.id FROM \"Sales \"\"Data\"\"\".orders o JOIN \"Sales \"\"Data\"\"\".items i "
                      f"ON i.o = o.id WHERE o.note = 'é {T}.x') SELECT \"Sales \"\"Data\"\"\".orders.id FROM x "
                      "JOIN \"Sales \"\"Data\"\"\".orders ON true")
    assert c.map_sql("SELECT status, count(*) FROM orders GROUP BY 1", T, "shop") == \
        "SELECT status, count(*) FROM orders GROUP BY 1"


@pytest.mark.parametrize("sql", [
    f"SELECT * FROM {T}.orders JOIN public.sellers ON true",
    f"SELECT * FROM {T}.orders JOIN cn_1111111111111111.orders ON true",
    "SELECT usename FROM pg_catalog.pg_user",
    f"SELECT {T}.secret_fn(1) FROM {T}.orders",
    "SELECT * FROM information_schema.tables",
])
def test_anything_but_its_own_tables_is_refused(sql):
    with pytest.raises(c.Refused):
        c.map_sql(sql, T, "shop")


def test_datasets_named_sees_tokens_and_other_qualifiers():
    assert c.datasets_named(f"SELECT 1 FROM {T}.a JOIN olist.b ON true") == ({T}, {"olist"})
    assert c.datasets_named("SELECT 1 FROM orders") == (set(), set())


def test_a_connection_named_in_sql_for_our_database_is_refused_before_any_access():
    async def go():
        set_session(tenant_id="t-test")
        return await run_select(f"SELECT * FROM {T}.orders", max_rows=1)
    result = asyncio.run(go())
    assert result["ok"] is False and "connected database" in result["error"]["message"]


def test_our_own_services_and_local_addresses_are_never_targets(monkeypatch):
    table = {"postgres": ["172.19.0.2"], "mcp": ["172.19.0.3"], "db.example.com": ["203.0.113.7"],
             "sneaky.example.com": ["172.19.0.2"], "loop.example.com": ["127.0.0.1"],
             "meta.example.com": ["169.254.169.254"], "host.docker.internal": ["192.168.65.254"]}
    monkeypatch.setattr(c, "_addresses", lambda host: table.get(host, []))
    assert c.check_host("db.example.com") == "203.0.113.7"
    assert c.check_host("host.docker.internal") == "192.168.65.254"
    for host in ("postgres", "POSTGRES.", "localhost", "sneaky.example.com", "loop.example.com",
                 "meta.example.com", "nowhere.example.com"):
        with pytest.raises(c.Refused):
            c.check_host(host)


def test_sessions_are_read_only_with_a_timeout():
    kwargs = c.connect_kwargs("db", "203.0.113.7", 5432, "shop", "analyst", "pw", "require")
    assert kwargs["hostaddr"] == "203.0.113.7" and kwargs["host"] == "db"
    assert "default_transaction_read_only=on" in kwargs["options"]
    assert "statement_timeout=" in kwargs["options"]


@pytest.mark.parametrize("fields,reason", [
    ({"name": " "}, "name"),
    ({"host": "db.example.com/x"}, "host"),
    ({"host": "user@db"}, "host"),
    ({"port": 0}, "port"),
    ({"port": "5432"}, "port"),
    ({"password": ""}, "password"),
    ({"sslmode": "allow"}, "sslmode"),
])
def test_settings_are_validated_before_any_network_access(fields, reason):
    good = {"name": "Shop", "host": "db.example.com", "port": 5432, "dbname": "shop", "username": "analyst",
            "password": "pw", "sslmode": "prefer"}
    result = lt._connection_fields(**{**good, **fields})
    assert isinstance(result, str) and reason in result
    assert isinstance(lt._connection_fields(**good), dict)


def test_the_password_is_encrypted_and_never_in_a_view(tmp_path, monkeypatch):
    key = tmp_path / "key"
    key.write_bytes(__import__("base64").urlsafe_b64encode(os.urandom(32)))
    monkeypatch.setattr(c, "_KEY_FILE", str(key))
    monkeypatch.setattr(c, "_fernet_cache", [])
    pytest.importorskip("cryptography")
    token = c.encrypt("s3cret")
    assert "s3cret" not in token and c.decrypt(token) == "s3cret"
    assert "password" not in " ".join(lt._CONNECTION_FIELDS)


def test_without_a_key_connections_are_refused_not_crashed(monkeypatch):
    monkeypatch.setattr(c, "_KEY_FILE", "/nonexistent/key")
    monkeypatch.setattr(c, "_fernet_cache", [])
    assert c.available() is False
    with pytest.raises(c.Refused):
        c.encrypt("pw")


def test_connection_actions_refuse_bad_input_before_the_database():
    async def go(action, **params):
        set_session(tenant_id="t-test")
        return await lt.manage(action, principal_id="p", **params)
    assert asyncio.run(go("create_connection", name="Shop", host="db", port=99999, dbname="d", username="u",
                          password="p"))["error"] == "invalid"
    assert asyncio.run(go("set_connection_schema", connection_id=T, schema_name=" "))["error"] == "invalid"

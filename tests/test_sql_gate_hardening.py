"""
tests/test_sql_gate_hardening.py
The two SQL-gate rules added after the security review of 2026-10-01.

WHY THIS EXISTS.
- The function denylist (pg_sleep, file I/O, dblink, admin functions) used to
  apply to vector.hybrid_select only; data.run_select -- the main path for
  model-written SQL -- accepted `SELECT pg_sleep(..) FROM public.sellers`.
  It now sits in Layer 1, which every model-SQL path shares.
- The reader role must read workspace_docs for document search, and RLS there
  isolates tenants, not users, so model-written SQL naming workspace_docs could
  read another user's uploads in the same tenant. SQL for a built-in dataset may
  no longer name StateSync's internal schemas at all. (An owner's own database
  is exempt: its schemas may legitimately be called anything.)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "mcp"))

if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        import unittest.mock
        sys.modules["psycopg_pool"] = unittest.mock.MagicMock()

pytest.importorskip("pglast")
pytest.importorskip("psycopg")
from session import set_session  # noqa: E402
from tools.data_tools import _check_layer1, check_internal_schemas, run_select  # noqa: E402


@pytest.mark.parametrize("sql", [
    "SELECT pg_sleep(1), seller_id FROM public.sellers",
    "SELECT pg_catalog.pg_sleep(1) FROM public.sellers",
    "SELECT * FROM public.sellers WHERE pg_read_file('/etc/passwd') IS NOT NULL",
    "SELECT dblink('host=x', 'select 1') FROM olist.orders",
    "WITH s AS (SELECT set_config('statesync.tenant_id', 't2', false) AS x FROM public.sellers) SELECT * FROM s",
])
def test_dangerous_functions_are_refused_in_layer_1(sql):
    rejection = _check_layer1(sql)
    assert rejection and "is not permitted" in rejection


def test_ordinary_functions_still_pass():
    assert _check_layer1("SELECT date_trunc('month', o.order_purchase_timestamp), count(*), avg(p.payment_value) "
                         "FROM olist.orders o JOIN olist.order_payments p USING (order_id) GROUP BY 1") is None


@pytest.mark.parametrize("sql", [
    "SELECT content_md FROM workspace_docs.chunks",
    "SELECT * FROM olist.orders WHERE order_id IN (SELECT document_id FROM WORKSPACE_DOCS.documents)",
    "WITH x AS (SELECT * FROM session_state.workspaces) SELECT * FROM x",
    "SELECT rolname FROM pg_catalog.pg_roles",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM live_sim.plans",
    "SELECT * FROM search.sample_terms",
])
def test_internal_schemas_are_refused_for_built_in_datasets(sql):
    rejection = check_internal_schemas(sql)
    assert rejection and "StateSync's own data" in rejection


@pytest.mark.parametrize("sql", [
    "SELECT count(*) FROM olist.orders",
    "SELECT s.display_name FROM public.sellers s JOIN orders o ON o.seller_id = s.seller_id",
    "SELECT * FROM live.orders",
])
def test_dataset_tables_pass(sql):
    assert check_internal_schemas(sql) is None


def test_run_select_refuses_internal_schemas_before_touching_the_database():
    async def go():
        set_session(tenant_id="t-test")
        return await run_select("SELECT content_md FROM workspace_docs.chunks", max_rows=1)
    result = asyncio.run(go())
    assert result["ok"] is False and "StateSync's own data" in result["error"]["message"]

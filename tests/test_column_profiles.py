"""
tests/test_column_profiles.py
Pins the one-call SQL variant (pipeline.sql_draft_mode = "profiles").

WHY THIS EXISTS: the variant must (a) build bounded, identifier-free example
values per text column, (b) sample once per schema fingerprint, (c) cost exactly
one sql-role model call on a happy first attempt, (d) sample live only after an
empty_result, and (e) leave the shipped "llm" mode -- draft call plus live
interrogation -- untouched. MCP and the model are faked; no network.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

import column_profiles as cp
from config_loader import cfg


def _cols(*pairs: tuple[str, str]) -> list[dict]:
    return [{"name": n, "type": t, "nullable": True, "comment": None} for n, t in pairs]


# -- build_profiles ---------------------------------------------------------

def test_build_profiles_distinct_first_seen_and_limit():
    limit = int(cfg.pipeline.profile_values_per_column)
    values = ["b", "a", "b", "c"] + [f"v{i}" for i in range(limit + 3)]
    rows = [{"city": v} for v in values]
    out = cp.build_profiles({"t": _cols(("city", "text"))}, {"t": rows})
    assert out["t.city"][:3] == ["b", "a", "c"]
    assert len(out["t.city"]) == limit
    assert len(set(out["t.city"])) == limit


def test_build_profiles_truncates_values():
    max_chars = int(cfg.pipeline.profile_value_max_chars)
    long_value = "x" * (max_chars + 10)
    out = cp.build_profiles({"t": _cols(("c", "text"))}, {"t": [{"c": long_value}, {"c": long_value}]})
    assert out["t.c"] == ["x" * max_chars]


def test_build_profiles_text_like_only_and_skips_empty():
    tc = {"t": _cols(("n", "integer"), ("d", "timestamp with time zone"),
                     ("v", "character varying(50)"), ("e", "text"))}
    rows = [{"n": 1, "d": "2024-01-01", "v": "red", "e": ""}, {"n": 2, "d": "x", "v": "blue", "e": "  "}]
    out = cp.build_profiles(tc, {"t": rows})
    assert out == {"t.v": ["red", "blue"]}


def test_build_profiles_skips_identifier_like_columns():
    tc = {"t": _cols(("uuid_col", "text"), ("long_col", "text"), ("dup_hex", "text"), ("city", "text"))}
    rows = [
        {"uuid_col": "3f2b8c1e-1111-4222-8333-444455556666", "long_col": "a" * 25,
         "dup_hex": "deadbeef", "city": "Pune"},
        {"uuid_col": "9a8b7c6d-1111-4222-8333-444455556666", "long_col": "b" * 25,
         "dup_hex": "deadbeef", "city": "Delhi"},
    ]
    out = cp.build_profiles(tc, {"t": rows})
    assert "t.uuid_col" not in out
    assert "t.long_col" not in out
    assert out["t.dup_hex"] == ["deadbeef"]  # not all distinct -> kept
    assert out["t.city"] == ["Pune", "Delhi"]


# -- format_profiles --------------------------------------------------------

def test_format_profiles_exact_format():
    text = cp.format_profiles({"orders.status": ["a", "b"], "sellers.city": ["Pune"]}, 1000)
    assert text == (
        "Example values (from a sample of rows; use these exact spellings):\n"
        "  orders.status: 'a', 'b'\n"
        "  sellers.city: 'Pune'"
    )


def test_format_profiles_cuts_at_line_boundary():
    header = "Example values (from a sample of rows; use these exact spellings):"
    first = "  orders.status: 'a', 'b'"
    profiles = {"orders.status": ["a", "b"], "sellers.city": ["Pune"]}
    text = cp.format_profiles(profiles, len(header) + 1 + len(first) + 5)
    assert text == header + "\n" + first


def test_format_profiles_empty():
    assert cp.format_profiles({}, 1000) == ""


# -- get_profiles caching ---------------------------------------------------

DESCS = [{"table": "orders", "columns": _cols(("order_id", "integer"), ("status", "text"))}]


def _sampler(counter: list):
    async def fake(session, table, columns=None, limit=None, schema=None):
        counter.append((table, columns, limit, schema))
        return {"rows": [{"status": "shipped"}, {"status": "new"}]}
    return fake


def test_get_profiles_caches_per_schema_fingerprint():
    cp.clear_cache()
    calls: list = []
    with patch.object(cp, "data_sample_rows", _sampler(calls)):
        async def go():
            a, b = await asyncio.gather(
                cp.get_profiles(None, "public", "fp1", ["orders"], DESCS),
                cp.get_profiles(None, "public", "fp1", ["orders"], DESCS),
            )
            c = await cp.get_profiles(None, "public", "fp1", ["orders"], DESCS)
            return a, b, c
        a, b, c = asyncio.run(go())
        assert a == b == c == {"orders.status": ["shipped", "new"]}
        assert len(calls) == 1
        assert calls[0][1] == ["status"]
        assert calls[0][2] == int(cfg.pipeline.profile_sample_rows)

        asyncio.run(cp.get_profiles(None, "public", "fp2", ["orders"], DESCS))
        assert len(calls) == 2
    cp.clear_cache()


# -- sql_agent_node ---------------------------------------------------------

pytest.importorskip("pglast")
from nodes import sql_agent as sa  # noqa: E402


class _FakeSession:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeLLM:
    def __init__(self, log: list, sql: str):
        self._log, self._sql = log, sql

    async def complete(self, system, messages, max_tokens=None):
        self._log.append(messages[0]["content"])
        return SimpleNamespace(text=self._sql, tokens_in=1, tokens_out=1)


class _FakeRetriever:
    async def retrieve(self, session, table_names, schema=None, **_context):
        return "TABLE orders:\n    status text NULL", DESCS


def _run_node(state: dict, run_rows: list[dict], prompts: list, sampled: list, sql: str = "SELECT status FROM orders"):
    async def fake_list_tables(session, schema):
        return {"tables": [{"name": "orders"}], "fingerprint": "fp-node"}

    async def fake_select(session, sql_text, max_rows=None):
        return {"ok": True, "rows": run_rows, "columns": ["status"]}

    async def fake_sample(session, table, columns=None, limit=None, schema=None):
        sampled.append(table)
        return {"rows": [{"status": "shipped"}]}

    with patch.object(sa, "MCPSession", _FakeSession), \
         patch.object(sa, "schema_list_tables", fake_list_tables), \
         patch.object(sa, "get_schema_retriever", lambda: _FakeRetriever()), \
         patch.object(sa, "get_agent_llm", lambda role: _FakeLLM(prompts, sql)), \
         patch.object(sa, "data_run_select", fake_select), \
         patch.object(sa, "data_sample_rows", fake_sample), \
         patch.object(cp, "data_sample_rows", fake_sample), \
         patch.object(sa, "get_spatial_resolver", lambda: SimpleNamespace(resolve=lambda q: None)):
        cp.clear_cache()
        try:
            return asyncio.run(sa.sql_agent_node(state))
        finally:
            cp.clear_cache()


def _state(mode: str | None, **extra) -> dict:
    flags = {"memory.enabled": False}
    if mode is not None:
        flags["pipeline.sql_draft_mode"] = mode
    return {"tenant_id": "t", "query_id": "q", "session_id": "s", "raw_query": "how many shipped orders?",
            "flags_snapshot": flags, "target_schema": "public", **extra}


def test_profiles_mode_one_call_no_live_sampling():
    prompts: list = []
    sampled: list = []
    update = _run_node(_state("profiles"), [{"status": "x"}], prompts, sampled)
    assert update["metrics"]["llm_calls"] == 1
    assert len(prompts) == 1
    assert "Example values (from a sample of rows; use these exact spellings):" in prompts[0]
    assert "orders.status: 'shipped'" in prompts[0]
    assert "Sampled data" not in prompts[0]
    assert sampled == ["orders"]  # the single profile sample, cached-path only
    assert "interrogation_samples" not in update


def test_llm_mode_still_drafts_then_generates():
    prompts: list = []
    sampled: list = []
    for mode in ("llm", None):
        prompts.clear()
        sampled.clear()
        update = _run_node(_state(mode), [{"status": "x"}], prompts, sampled)
        assert update["metrics"]["llm_calls"] == 2
        assert len(prompts) == 2
        assert "Example values" not in prompts[1]
        assert "Sampled data" in prompts[1]
        assert update["interrogation_samples"][0]["table"] == "orders"


def test_profiles_mode_retry_after_empty_result_samples_failed_tables():
    prompts: list = []
    sampled: list = []
    errors = [{
        "attempt": 0, "category": "empty_result", "engine_code": None,
        "message": "Query executed successfully but returned zero rows.",
        "offending_sql": "SELECT status FROM orders WHERE status = 'SHIPPED'",
        "remediation_hint": "Verify filter literals against sampled values.",
    }]
    update = _run_node(_state("profiles", errors=errors), [{"status": "x"}], prompts, sampled)
    assert sampled == ["orders"]
    assert len(prompts) == 1
    assert "Sampled data" in prompts[0]
    assert "shipped" in prompts[0]
    assert update["interrogation_samples"][0]["table"] == "orders"


def test_profiles_mode_retry_after_other_error_does_not_sample():
    prompts: list = []
    sampled: list = []
    errors = [{
        "attempt": 0, "category": "syntax_error", "engine_code": "42601", "message": "bad",
        "offending_sql": "SELEC", "remediation_hint": "fix",
    }]
    update = _run_node(_state("profiles", errors=errors), [{"status": "x"}], prompts, sampled)
    assert sampled == []
    assert "interrogation_samples" not in update

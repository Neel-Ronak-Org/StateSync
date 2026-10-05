"""
tests/test_t2_sql_outcomes.py
Pins T2 (docs/specs/partd_tasks/T2_sql_outcomes_recovery.md; routing_docs_fixes.md F-S1, F-S3, F-S4, F-RR):
the flags sql_no_data, plain_failures and route_recovery.

WHY THIS EXISTS. The shipped system answered a question the database could not answer by writing a
constant query or by naming an internal error category, and it never tried the documents route after a
wrong "sql" route. Each fix sits behind a flag that defaults to off, and with a flag off nothing may
move: the prompts, the writer's context, the graph's decisions and the ErrorRecord categories are
byte-identical to the base. With a flag on, the exact texts of the spec, the parser, the mapping, the
recovery decision and the trace field are pinned.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        import unittest.mock
        sys.modules["psycopg_pool"] = unittest.mock.MagicMock()

import config_loader  # noqa: E402
import sql_outcomes  # noqa: E402
from classifiers import error_classifier  # noqa: E402
from classifiers.error_classifier import category_hint, classify_error  # noqa: E402
from nodes import synthesizer  # noqa: E402
from state import _LAST_WRITE_WINS_FIELDS, ErrorRecord, StateSyncState  # noqa: E402

NO_TABLE = ("[Layer 1 Gate] Rejected: statement reads no table. Queries must read data from the schema "
            "rather than compute constants or server introspection. This rejection is based on query "
            "shape, not content safety.")
NOT_ANSWERABLE_TEXT = """\
8. If the tables described cannot answer the question -- the data it asks about is not in them --
   reply with exactly NOT_ANSWERABLE: <one short reason> instead of SQL. Never write a query that
   returns a constant, or reads an unrelated table, to stand in for data the database does not hold."""
ON = {"pipeline.sql_no_data": "on"}


def _taxonomy_rules() -> list[dict]:
    return json.loads((REPO_ROOT / "config" / "error_taxonomy.json").read_text())["rules"]


# ── the flags ───────────────────────────────────────────────────────────────
def test_the_three_flags_ship_off():
    flags = config_loader.snapshot_flags()
    assert flags["pipeline.sql_no_data"] == "off"
    assert flags["pipeline.plain_failures"] == "off"
    assert flags["pipeline.route_recovery"] == "off"


# ── the parser ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("reply", [
    "NOT_ANSWERABLE: the tables hold no filings",
    "not_answerable: lower case",
    "  \n NOT_ANSWERABLE: leading whitespace\n",
    "```\nNOT_ANSWERABLE: inside a fence\n```",
    "```text\nNot_Answerable: fenced with a language\n```",
    "NOT_ANSWERABLE",
])
def test_parser_recognises_a_not_answerable_reply(reply):
    parsed = sql_outcomes.parse_not_answerable(reply)
    assert parsed is not None and parsed.upper().startswith("NOT_ANSWERABLE")


@pytest.mark.parametrize("reply", [
    "SELECT 1", "SELECT 'NOT_ANSWERABLE'", "WITH x AS (SELECT 1) SELECT * FROM x",
    "```sql\nSELECT a FROM t\n```", "", None, "  ", "The question is NOT_ANSWERABLE",
])
def test_parser_leaves_sql_alone(reply):
    assert sql_outcomes.parse_not_answerable(reply) is None


def test_parser_returns_the_first_300_characters_after_stripping():
    long_reply = "NOT_ANSWERABLE: " + "x" * 500
    assert sql_outcomes.parse_not_answerable(long_reply) == long_reply[:300]
    fenced = "```\n" + long_reply + "\n```"
    assert sql_outcomes.parse_not_answerable(fenced) == long_reply[:300]
    assert sql_outcomes.parse_not_answerable("NOT_ANSWERABLE: short") == "NOT_ANSWERABLE: short"


def test_not_answerable_error_record_is_terminal_no_data_source():
    record = sql_outcomes.not_answerable_error(2, "NOT_ANSWERABLE: no such data")
    assert record == {
        "attempt": 2, "category": "no_data_source", "engine_code": None,
        "message": "NOT_ANSWERABLE: no such data", "offending_sql": None,
        "remediation_hint": category_hint("no_data_source"),
    }
    assert "no table" in record["remediation_hint"]          # the overrides file's hint, read, not copied
    assert "no_data_source" in error_classifier.TERMINAL_CATEGORIES
    ErrorRecord(**record)                                     # a valid state record


# ── the classifier additions: behaviour of classify_error is unchanged ──────
def test_classify_error_default_is_unchanged_and_use_overrides_false_gives_the_base_label():
    assert classify_error(sqlstate=None, message=NO_TABLE)[0] == "no_data_source"
    category, hint = classify_error(sqlstate=None, message=NO_TABLE, use_overrides=False)
    generic = next(r for r in _taxonomy_rules() if r["match"].get("message_regex") == "(?i)^\\[Layer 1 Gate\\] Rejected")
    assert (category, hint) == ("permission_denied", generic["hint"])
    # nothing else moves with the overrides skipped
    assert classify_error(sqlstate="42703", message="x", use_overrides=False) == classify_error(sqlstate="42703", message="x")
    assert classify_error(sqlstate=None, message=None, empty_result=True, use_overrides=False)[0] == "empty_result"


def test_category_hint_reads_the_taxonomy():
    assert category_hint("permission_denied") == next(
        r["hint"] for r in _taxonomy_rules() if r["category"] == "permission_denied")
    assert category_hint("no_such_category") == json.loads(
        (REPO_ROOT / "config" / "error_taxonomy.json").read_text())["fallback"]["hint"]


def test_classify_engine_failure_maps_by_flag():
    generic = classify_error(sqlstate=None, message=NO_TABLE, use_overrides=False)
    assert sql_outcomes.classify_engine_failure(None, NO_TABLE, {}) == generic            # off (absent)
    assert sql_outcomes.classify_engine_failure(None, NO_TABLE, {"pipeline.sql_no_data": "off"}) == generic
    assert sql_outcomes.classify_engine_failure(None, NO_TABLE, ON) == classify_error(sqlstate=None, message=NO_TABLE)
    assert sql_outcomes.classify_engine_failure(None, NO_TABLE, ON)[0] == "no_data_source"
    # every other rejection and failure is the same either way
    for state, msg in (("42501", "permission denied"), (None, "[Layer 1 Gate] Rejected: top-level statement must be SelectStmt"),
                       ("42703", "column x does not exist"), ("57014", "canceled")):
        assert sql_outcomes.classify_engine_failure(state, msg, {}) == sql_outcomes.classify_engine_failure(state, msg, ON)


# ── the SQL agent ───────────────────────────────────────────────────────────
pytest.importorskip("pglast")
from nodes import sql_agent as sa  # noqa: E402


def test_prompt_rule_is_appended_exactly_for_writer_and_corrector():
    for template in (sa._SYSTEM_PROMPT, sa._CORRECTION_SYSTEM_PROMPT):
        base = template.format(max_rows=7).replace(sa._ONDC_SQL_DOMAIN, sa.profile_for("public").sql_domain)
        assert sa.sql_system_prompt(template, 7, "public", {}) == base                              # flag absent
        assert sa.sql_system_prompt(template, 7, "public", {"pipeline.sql_no_data": "off"}) == base
        on = sa.sql_system_prompt(template, 7, "public", ON)
        assert on == base + "\n" + NOT_ANSWERABLE_TEXT
        assert on.endswith(NOT_ANSWERABLE_TEXT)


def test_prompt_with_the_executor_row_cap_still_gets_the_rule_after_the_template():
    flags = {"pipeline.sql_row_cap": "executor", "pipeline.sql_no_data": "on"}
    prompt = sa.sql_system_prompt(sa._SYSTEM_PROMPT, 7, "public", flags)
    assert prompt.endswith(NOT_ANSWERABLE_TEXT) and "Apply LIMIT" not in prompt


DESCS = [{"table": "orders", "columns": [{"name": "status", "type": "text"}]}]


class _FakeSession:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeRetriever:
    async def retrieve(self, session, table_names, schema=None, **_context):
        return "TABLE orders:\n    status text NULL", DESCS


def _run_node(state: dict, reply: str, selects: list, prompts: list | None = None, exec_result: dict | None = None):
    class _LLM:
        async def complete(self, system, messages, max_tokens=None):
            if prompts is not None:
                prompts.append(system)
            return SimpleNamespace(text=reply, tokens_in=1, tokens_out=1)

    async def fake_list_tables(session, schema):
        return {"tables": [{"name": "orders"}], "fingerprint": "fp"}

    async def fake_select(session, sql_text, max_rows=None):
        selects.append(sql_text)
        return exec_result or {"ok": True, "rows": [{"status": "x"}], "columns": ["status"]}

    async def fake_sample(session, table, columns=None, limit=None, schema=None):
        return {"rows": [{"status": "shipped"}]}

    with patch.object(sa, "MCPSession", _FakeSession), \
         patch.object(sa, "schema_list_tables", fake_list_tables), \
         patch.object(sa, "get_schema_retriever", lambda: _FakeRetriever()), \
         patch.object(sa, "get_agent_llm", lambda role: _LLM()), \
         patch.object(sa, "data_run_select", fake_select), \
         patch.object(sa, "data_sample_rows", fake_sample), \
         patch.object(sa, "get_spatial_resolver", lambda: SimpleNamespace(resolve=lambda q: None)):
        return asyncio.run(sa.sql_agent_node(state))


def _state(flags: dict | None = None, **extra) -> dict:
    return {"tenant_id": "t", "query_id": "q", "session_id": "s", "raw_query": "what was the revenue of ACME?",
            "flags_snapshot": {"memory.enabled": False, **(flags or {})}, "target_schema": "public", **extra}


def test_not_answerable_reply_is_never_sent_and_becomes_a_terminal_error():
    selects: list = []
    prompts: list = []
    update = _run_node(_state(ON), "```\nNOT_ANSWERABLE: the tables hold no revenue\n```", selects, prompts)
    assert selects == []                                           # never sent to the MCP
    assert all(p.endswith(NOT_ANSWERABLE_TEXT) for p in prompts)   # the draft and the main call both carry the rule
    [err] = update["errors"]
    assert err["category"] == "no_data_source" and err["attempt"] == 0
    assert err["message"] == "NOT_ANSWERABLE: the tables hold no revenue"
    assert err["engine_code"] is None and err["offending_sql"] is None
    assert err["remediation_hint"] == category_hint("no_data_source")
    assert update["candidate_sql"] is None and "executed_sql" not in update
    assert update["sql_rows"] == [] and update["attempt"] == 0
    assert update["errors"][0]["category"] in error_classifier.TERMINAL_CATEGORIES   # the loop stops
    ErrorRecord(**err)


def test_not_answerable_message_is_cut_to_300_characters():
    update = _run_node(_state(ON), "NOT_ANSWERABLE: " + "y" * 600, [])
    assert len(update["errors"][0]["message"]) == 300


def test_corrector_reply_is_parsed_too():
    selects: list = []
    prior = {"attempt": 0, "category": "unknown_column", "engine_code": "42703", "message": "no col",
             "offending_sql": "SELECT z FROM orders", "remediation_hint": "h"}
    prompts: list = []
    update = _run_node(_state(ON, errors=[prior]), "NOT_ANSWERABLE: no such column anywhere", selects, prompts)
    assert selects == []
    assert prompts and prompts[0].endswith(NOT_ANSWERABLE_TEXT)
    [err] = update["errors"]
    assert err["category"] == "no_data_source" and err["attempt"] == 1


def test_sql_is_still_run_with_the_flag_on():
    selects: list = []
    update = _run_node(_state(ON), "SELECT status FROM orders", selects)
    assert selects == ["SELECT status FROM orders"]
    assert "errors" not in update and update["executed_sql"] == "SELECT status FROM orders"


def test_flag_off_does_not_parse_the_reply():
    selects: list = []
    prompts: list = []
    reply = "NOT_ANSWERABLE: the tables hold no revenue"
    update = _run_node(_state(), reply, selects, prompts)
    assert selects == [reply]                                      # sent as SQL, exactly as before
    assert not any("NOT_ANSWERABLE" in p for p in prompts)
    assert update["candidate_sql"] == reply


def test_layer_1_reads_no_table_label_follows_the_flag():
    failed = {"ok": False, "error": {"sqlstate": None, "message": NO_TABLE}}
    generic = classify_error(sqlstate=None, message=NO_TABLE, use_overrides=False)
    off = _run_node(_state(), "SELECT 1", [], exec_result=failed)["errors"][0]
    assert (off["category"], off["remediation_hint"]) == generic and off["category"] == "permission_denied"
    assert off["engine_code"] is None and off["offending_sql"] == "SELECT 1" and off["message"] == NO_TABLE
    on = _run_node(_state(ON), "SELECT 1", [], exec_result=failed)["errors"][0]
    assert on["category"] == "no_data_source" and on["remediation_hint"] == category_hint("no_data_source")
    assert on["offending_sql"] == "SELECT 1"


def test_other_gate_rejections_are_permission_denied_either_way():
    failed = {"ok": False, "error": {"sqlstate": None, "message": "[Layer 1 Gate] Rejected: top-level statement must be SelectStmt"}}
    for flags in ({}, ON):
        assert _run_node(_state(flags), "DELETE FROM orders", [], exec_result=failed)["errors"][0]["category"] == "permission_denied"


# ── the writer's context ────────────────────────────────────────────────────
def _write(monkeypatch, state: dict) -> tuple[SimpleNamespace, dict]:
    seen = SimpleNamespace(user=None)

    class _LLM:
        async def complete(self, system, messages, max_tokens):
            seen.user = messages[0]["content"]
            return SimpleNamespace(text=json.dumps({"answer_text": "Plain answer.", "chart": None, "citations": []}),
                                   tokens_in=1, tokens_out=1)

    monkeypatch.setattr(synthesizer, "get_agent_llm", lambda role: _LLM())
    out = asyncio.run(synthesizer.synthesizer_node({"query_id": "q", "raw_query": "how much?", **state}))
    return seen, out


def _failed(category="syntax", message="boom", hint="fix it", attempt=0, **extra):
    return {"errors": [{"attempt": attempt, "category": category, "engine_code": "42601", "message": message,
                        "offending_sql": "SELECT bad", "remediation_hint": hint}], "attempt": attempt, **extra}


OLD_FAILURE = (
    "The SQL query failed after 1 attempt(s).\nClassified failure category: syntax\nError code: 42601\n"
    "Error message: boom\nRemediation hint (why no further attempt was made): fix it\nAttempted SQL: SELECT bad\n\n"
    "State plainly that the query failed and name the classified failure category (syntax) in your answer -- "
    "do not omit it, and do not invent a numeric or factual answer that was never computed."
)
OLD_ZERO = "The SQL query returned zero rows. The answer may be 'none' or 'zero'."


def test_flag_off_failure_and_zero_row_contexts_are_unchanged(monkeypatch):
    for flags in ({}, {"pipeline.plain_failures": "off"}):
        seen, out = _write(monkeypatch, {**_failed(), "flags_snapshot": flags})
        assert seen.user == f"Question: how much?\n\nData context:\n{OLD_FAILURE}"
        assert out["answer_text"] == "Plain answer. (classified failure: syntax)"   # the category suffix, as before
        seen, _ = _write(monkeypatch, {"executed_sql": "SELECT 1", "sql_rows": [], "flags_snapshot": flags})
        assert seen.user == f"Question: how much?\n\nData context:\n{OLD_ZERO}"


def test_plain_failure_block_and_mapping(monkeypatch):
    expected = {
        "no_data_source": "the database in this workspace does not hold the data this question asks about.",
        "permission_denied": "the request was refused, because this system only runs single, read-only queries.",
        "timeout": "the query took too long and was stopped.",
        "empty_result": "no records matched.",
        "syntax": "the query could not be completed.",
        "unclassified": "the query could not be completed.",
        "join_cardinality": "the query could not be completed.",
    }
    assert set(synthesizer._PLAIN_FAILURES) == {"no_data_source", "permission_denied", "timeout", "empty_result"}
    for category, plain in expected.items():
        seen, out = _write(monkeypatch, {**_failed(category), "flags_snapshot": {"pipeline.plain_failures": "on"}})
        assert seen.user == (
            "Question: how much?\n\nData context:\n"
            f"The data query did not produce an answer: {plain}\n"
            "Explain this to the user plainly in one or two sentences. Do not mention internal labels, error\n"
            "categories or codes. Do not invent a number or fact that was never computed."
        )
        # no code, message or SQL reaches the writer, and no label is appended to the answer
        assert "42601" not in seen.user and "boom" not in seen.user and "SELECT bad" not in seen.user
        assert out["answer_text"] == "Plain answer."


def test_plain_zero_rows_text(monkeypatch):
    seen, _ = _write(monkeypatch, {"executed_sql": "SELECT 1", "sql_rows": [], "flags_snapshot": {"pipeline.plain_failures": "on"}})
    assert seen.user == (
        "Question: how much?\n\nData context:\n"
        "The SQL query returned zero rows: no records matched it. Say that no matching records were found. "
        "Do not turn this into a figure about something the query did not measure."
    )


def test_plain_failures_leaves_a_successful_result_alone(monkeypatch):
    rows = [{"n": 3}]
    base = {"executed_sql": "SELECT 3 AS n", "sql_rows": rows, "sql_columns": ["n"]}
    off, _ = _write(monkeypatch, {**base, "flags_snapshot": {}})
    on, _ = _write(monkeypatch, {**base, "flags_snapshot": {"pipeline.plain_failures": "on"}})
    assert off.user == on.user


RECOVERED = ("The database did not hold this, so the answer comes from the document passages below. "
             "Say so in one short clause.")


def _passage():
    return {"content": "Revenue was 5.", "provenance": {"chunk_id": "c1", "document_id": "d1", "page": 2, "score": 0.9}}


def test_recovery_line_precedes_the_passages_only_when_recovered(monkeypatch):
    base = {**_failed("no_data_source"), "retrievals": [_passage()], "flags_snapshot": {}}
    seen, _ = _write(monkeypatch, {**base, "recovered_from": "sql"})
    doc_context = synthesizer._format_doc_context(base["retrievals"])
    assert seen.user.endswith(f"\n\n{RECOVERED}\n\n{doc_context}")
    # not recovered, or recovered_from unset: no line, and the context is what it was
    seen, _ = _write(monkeypatch, base)
    assert RECOVERED not in seen.user and seen.user.endswith(doc_context)
    seen, _ = _write(monkeypatch, {**base, "recovered_from": None})
    assert RECOVERED not in seen.user
    # recovered but nothing retrieved: no line (it points at passages)
    seen, _ = _write(monkeypatch, {**_failed("no_data_source"), "recovered_from": "sql", "flags_snapshot": {}})
    assert RECOVERED not in seen.user


# ── the state field and the trace ───────────────────────────────────────────
def _real_state(**kw) -> StateSyncState:
    base = dict(query_id="q", tenant_id="t", principal_id="p", session_id="s", target_schema="public",
                raw_query="x", route="sql")
    return StateSyncState(**{**base, **kw})


def test_recovered_from_is_a_last_write_wins_field_defaulting_to_none():
    assert "recovered_from" in _LAST_WRITE_WINS_FIELDS
    assert _real_state().recovered_from is None
    assert _real_state(recovered_from="sql").recovered_from == "sql"


def test_main_state_trace_carries_recovered_from():
    tree = ast.parse((REPO_ROOT / "orchestrator" / "main.py").read_text())
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "state_trace" for t in node.targets) \
                and isinstance(node.value, ast.Dict):
            keys |= {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    assert "recovered_from" in keys


# ── the graph's recovery decision ───────────────────────────────────────────
def _err(category: str) -> ErrorRecord:
    return ErrorRecord(attempt=0, category=category, engine_code=None, message="m", offending_sql=None,
                       remediation_hint="h")


def _after_failure(graph, *, route="sql", category="no_data_source", flag="on", titles=("Doc",), schema="public",
                   recovered_from=None):
    state = _real_state(route=route, errors=[_err(category)], library_titles=list(titles), target_schema=schema,
                        recovered_from=recovered_from,
                        flags_snapshot={} if flag is None else {"pipeline.route_recovery": flag})
    return graph._recovery_target(state), graph._after_error_exit(state)


def test_recovery_decision_matrix():
    graph = pytest.importorskip("graph")
    assert _after_failure(graph) == ("recover_docs", "recover_docs")
    # flag off or absent: the writer, as before
    assert _after_failure(graph, flag="off")[0] == "synthesizer"
    assert _after_failure(graph, flag=None)[0] == "synthesizer"
    # only no_data_source recovers
    for category in ("permission_denied", "syntax", "unclassified", "timeout", "empty_result"):
        assert _after_failure(graph, category=category)[0] == "synthesizer"
    # only the sql route recovers; hybrid keeps its documents half
    assert _after_failure(graph, route="hybrid")[0] == "hybrid_docs"
    assert _after_failure(graph, route="docs")[0] == "synthesizer"
    # no documents: nothing to recover to (uploads, or a sample corpus with documents)
    assert _after_failure(graph, titles=(), schema="some_owner_database")[0] == "synthesizer"
    assert _after_failure(graph, titles=(), schema="public")[0] == "recover_docs"    # a document_schemas source
    assert _after_failure(graph, titles=("Upload",), schema="some_owner_database")[0] == "recover_docs"
    # it cannot recur
    assert _after_failure(graph, recovered_from="sql")[0] == "synthesizer"


def test_recovery_decision_with_the_flag_off_is_the_old_decision():
    graph = pytest.importorskip("graph")
    for route in ("sql", "docs", "hybrid"):
        for category in ("no_data_source", "syntax", "permission_denied"):
            state = SimpleNamespace(route=route, errors=[SimpleNamespace(category=category)], attempt=0)
            assert graph._after_error_exit(state) == ("hybrid_docs" if route == "hybrid" else "synthesizer")


def test_the_compiled_graph_has_the_recovery_node_and_one_run_edge():
    graph = pytest.importorskip("graph")
    drawn = graph.graph.get_graph()
    edges = {(e.source, e.target) for e in drawn.edges}
    assert ("recover_docs", "synthesizer") in edges and ("error_exit", "recover_docs") in edges
    assert not any(src == "recover_docs" and dst != "synthesizer" for src, dst in edges)   # never back to sql


def test_recovery_node_runs_the_docs_node_once_and_sets_the_field(monkeypatch):
    graph = pytest.importorskip("graph")
    seen: list[dict] = []

    async def fake_docs(state_dict):
        seen.append(state_dict)
        return {"subtasks": ["x"], "retrievals": [{"content": "c", "provenance": {
            "document_id": "d", "chunk_id": "k", "page": 1, "score": 0.5}}]}

    monkeypatch.setattr(graph, "resolve", lambda name: fake_docs if name == "docs" else None)
    state = _real_state(route="sql", errors=[_err("no_data_source")], library_titles=["Doc"])
    merged = asyncio.run(graph._recover_docs(state))
    assert len(seen) == 1 and seen[0]["route"] == "docs"
    assert merged["recovered_from"] == "sql" and merged["route"] == "sql"
    assert len(merged["retrievals"]) == 1 and merged["errors"][0]["category"] == "no_data_source"

"""
tests/test_t5_hybrid_split.py
Pins T5 (docs/specs/partd_tasks/T5_hybrid_split.md; routing_docs_fixes.md section 5): the hybrid documents
half narrowed to the workspace documents whose titles name entities from the SQL half's result.

WHY THIS EXISTS. The hybrid route could use the database result in its documents half only through ONDC's
seller_id / provider_id columns. For any other data the documents half searched blind: "which of these
companies had the lowest margin, and what does its 10-K say drove it" searched every filing for "what
drove it". With pipeline.hybrid_entities = "on", and the front door saying the documents depend on the
database half, the half now searches only the documents whose titles name an entity the SQL returned.

Pinned here:
1. The pure parts: which values are entities, which titles match, when the step runs.
2. The narrowed search: document_ids restricted, top_k the retrieval ceiling, the first round skipped,
   the mode / scope / re-rank / passage-order flags kept; no match runs the two-round search as before.
3. Flag off (and every case the step must not run in) makes exactly the MCP calls the base commit made:
   the expected lists below were recorded from the base commit's hybrid_docs_node with the same fake session.
4. The front door puts documents_depend_on_database into the state as docs_depend_on_sql.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

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
import front_door  # noqa: E402
from config_loader import cfg  # noqa: E402
from nodes import unstructured_knowledge as uk  # noqa: E402
from state import _LAST_WRITE_WINS_FIELDS, StateSyncState  # noqa: E402

RUN = asyncio.run
TOP_K = int(cfg.retrieval.semantic_top_k)
TOP_K_MAX = int(cfg.retrieval.semantic_top_k_max)

FILINGS = [
    {"document_id": "d_ko", "title": "COCACOLA 2022 10K"},
    {"document_id": "d_pep", "title": "PEPSICO 2022 10K"},
    {"document_id": "d_mmm", "title": "3M 2018 10K"},
]


# ── the pure parts ──────────────────────────────────────────────────────────
def test_entities_are_the_distinct_nonempty_strings_of_the_text_columns_in_row_order():
    rows = [{"company": "Coca-Cola", "margin": 0.12, "year": 2022, "note": ""},
            {"company": "PepsiCo", "margin": 0.11, "year": 2022, "note": "  "},
            {"company": "Coca-Cola", "margin": 0.10, "year": 2021, "note": None}]
    columns = ["company", "margin", "year", "note"]
    assert uk.extract_entities(rows, columns, 5, 5) == ["Coca-Cola", "PepsiCo"]   # numbers, blanks, None: no
    assert uk.extract_entities(rows, columns, 5, 5)[0] == "Coca-Cola"              # first appearance wins
    assert uk.extract_entities([{"n": 7}, {"n": 8}], ["n"], 5, 5) == []            # no text column at all
    assert uk.extract_entities([], [], 5, 5) == []


def test_entities_are_stripped_and_a_boolean_or_number_is_never_one():
    assert uk.extract_entities([{"a": "  3M  ", "b": True, "c": 1.5}], ["a", "b", "c"], 5, 5) == ["3M"]


def test_the_row_cap_and_the_entity_cap():
    rows = [{"name": f"co{n}"} for n in range(10)]
    assert uk.extract_entities(rows, ["name"], 3, 5) == ["co0", "co1", "co2"]       # first entity_rows rows only
    assert uk.extract_entities(rows, ["name"], 10, 4) == ["co0", "co1", "co2", "co3"]   # at most max_entities
    wide = [{"a": "x1", "b": "x2", "c": "x3"}]
    assert uk.extract_entities(wide, ["a", "b", "c"], 5, 2) == ["x1", "x2"]         # the cap cuts inside a row too
    assert uk.extract_entities(rows, ["name"], 0, 5) == []


def test_columns_fall_back_to_the_first_rows_keys():
    assert uk.extract_entities([{"name": "Acme"}], [], 5, 5) == ["Acme"]


def test_title_matching_ignores_case_punctuation_and_spacing():
    assert [d["document_id"] for d in uk.match_titles(["Coca-Cola"], FILINGS)] == ["d_ko"]
    assert [d["document_id"] for d in uk.match_titles(["coca cola"], FILINGS)] == ["d_ko"]
    # T5b: an entity longer than the title's name now matches it (the title name "pepsico" occurs in the entity)
    assert [d["document_id"] for d in uk.match_titles(["PepsiCo, Inc."], FILINGS)] == ["d_pep"]
    assert [d["document_id"] for d in uk.match_titles(["3M"], FILINGS)] == ["d_mmm"]


def test_title_matching_no_match_and_several_matches_keep_the_listing_order():
    assert uk.match_titles(["Walmart"], FILINGS) == []
    assert uk.match_titles([], FILINGS) == []
    assert uk.match_titles(["PepsiCo", "Coca-Cola"], FILINGS) == [FILINGS[0], FILINGS[1]]   # not the entity order
    assert uk.match_titles(["2022"], FILINGS) == [FILINGS[0], FILINGS[1]]                  # one entity, two titles


def test_an_entity_with_no_letter_or_digit_matches_nothing_and_a_missing_title_is_safe():
    assert uk.match_titles(["---", " "], FILINGS) == []
    assert uk.match_titles(["acme"], [{"document_id": "x", "title": None}, {"document_id": "y"}]) == []


@pytest.mark.parametrize("flag,depends,party_ids,wanted", [
    ("on", True, [], True),
    ("off", True, [], False),                  # flag off
    ("on", False, [], False),                  # the documents do not depend on the database half
    ("on", True, ["s1"], False),               # the party-id path keeps its place
    ("", True, [], False), ("llm", True, [], False),    # only "on" turns it on
])
def test_the_narrowing_decision(flag, depends, party_ids, wanted):
    assert uk.narrowing_wanted(flag, depends, party_ids) is wanted


# ── the node, with a fake MCP session that records every call ───────────────
class FakeSession:
    """Records (method, params) of every MCP call; answers the three methods the half uses."""
    calls: list[tuple[str, dict]] = []
    documents: list[dict] = []
    documents_ok = True

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def call(self, method, params, timeout=None):
        FakeSession.calls.append((method, params))
        if method == "library.manage":
            if not FakeSession.documents_ok:
                raise RuntimeError("mcp down")
            return {"ok": True, "documents": list(FakeSession.documents)}
        if method == "vector.hybrid_select":
            return {"ok": True, "rows": [{"chunk_id": "p_c1", "document_id": "p", "content_md": "x",
                                          "page": 1, "score": 0.5}]}
        assert method == "vector.semantic_search"

        def match(chunk, doc, score):
            return {"chunk_id": chunk, "document_id": doc, "content_md": f"text {chunk}", "page": 1,
                    "score": score, "title": None}
        if "document_ids" in params:
            return {"matches": [match(f"{d}_c2", d, 0.3) for d in params["document_ids"]]}
        return {"matches": [match("d_ko_c1", "d_ko", 0.9)], "best_dense_score": 0.9, "best_keyword_score": None}


def _run(monkeypatch, flags=None, *, documents=FILINGS, documents_ok=True, **state_kw):
    FakeSession.calls = []
    FakeSession.documents = documents
    FakeSession.documents_ok = documents_ok
    monkeypatch.setattr(uk, "MCPSession", FakeSession)
    state = {"route": "hybrid", "normalized_query": "q", "tenant_id": "t1", "principal_id": "p1",
             "session_id": "s1", "query_id": "q1", "target_schema": "cn_owner", "workspace_id": "w1",
             "flags_snapshot": {"pipeline.rerank": "bge", "pipeline.rerank_titles": "on",
                                "pipeline.retrieval_mode": "hybrid", "pipeline.doc_scope": "dataset",
                                "pipeline.passage_order": "relevance", **(flags or {})},
             "subtasks": ["sql part", "what drove it?"],
             "sql_rows": [{"company": "Coca-Cola", "margin": 0.12}], "sql_columns": ["company", "margin"]}
    state.update(state_kw)
    return RUN(uk.hybrid_docs_node(state))


ON = {"pipeline.hybrid_entities": "on"}


def test_the_narrowed_search_passes_the_document_ids_and_skips_the_first_round(monkeypatch):
    update = _run(monkeypatch, ON, docs_depend_on_sql=True)
    methods = [m for m, _ in FakeSession.calls]
    assert methods == ["library.manage", "vector.semantic_search"]               # no unrestricted first round
    assert FakeSession.calls[0][1] == {"action": "workspace_documents", "principal_id": "p1",
                                       "workspace_id": "w1", "limit": int(cfg.library.max_documents)}
    assert FakeSession.calls[1][1] == {
        "query_text": "what drove it?", "top_k": TOP_K_MAX, "document_ids": ["d_ko"], "mode": "hybrid",
        "dataset": "cn_owner", "scope": "dataset", "rerank": "bge", "rerank_titles": True}
    assert [r["provenance"]["chunk_id"] for r in update["retrievals"]] == ["d_ko_c2"]
    assert update["hybrid_entities"] == {"entities": ["Coca-Cola"], "matched_documents": ["COCACOLA 2022 10K"],
                                         "narrowed": True}
    assert update["metrics"]["trajectory_steps"] == 1 and update["metrics"]["llm_calls"] == 0


def test_the_narrowed_search_keeps_the_passage_order_flag(monkeypatch):
    """Three matched filings: the documents come in the listing order under "document", and the relevance
    ordering is the one order_passages gives (every passage here scores the same, so ties go to chunk_id)."""
    rows = [{"company": "Coca-Cola"}, {"company": "PepsiCo"}, {"company": "3M"}]
    doc = _run(monkeypatch, {**ON, "pipeline.passage_order": "document"}, docs_depend_on_sql=True,
               sql_rows=rows, sql_columns=["company"])
    assert FakeSession.calls[1][1]["document_ids"] == ["d_ko", "d_pep", "d_mmm"]
    assert [r["provenance"]["chunk_id"] for r in doc["retrievals"]] == ["d_ko_c2", "d_pep_c2", "d_mmm_c2"]
    rel = _run(monkeypatch, ON, docs_depend_on_sql=True, sql_rows=rows, sql_columns=["company"])
    assert sorted(r["provenance"]["chunk_id"] for r in rel["retrievals"]) == ["d_ko_c2", "d_mmm_c2", "d_pep_c2"]


def test_rerank_titles_is_sent_only_with_a_reranker_on_the_narrowed_search(monkeypatch):
    _run(monkeypatch, {**ON, "pipeline.rerank": "off"}, docs_depend_on_sql=True)
    assert "rerank_titles" not in FakeSession.calls[1][1] and "rerank" not in FakeSession.calls[1][1]
    _run(monkeypatch, {**ON, "pipeline.rerank_titles": "off"}, docs_depend_on_sql=True)
    assert "rerank_titles" not in FakeSession.calls[1][1] and FakeSession.calls[1][1]["rerank"] == "bge"


def test_no_matching_title_runs_the_two_round_search_as_before(monkeypatch):
    update = _run(monkeypatch, ON, docs_depend_on_sql=True, documents=[FILINGS[2]])
    assert [m for m, _ in FakeSession.calls] == ["library.manage", "vector.semantic_search", "vector.semantic_search"]
    first, second = FakeSession.calls[1][1], FakeSession.calls[2][1]
    assert "document_ids" not in first and first["top_k"] == TOP_K
    assert second["document_ids"] == ["d_ko"] and second["top_k"] == TOP_K_MAX
    assert update["hybrid_entities"] == {"entities": ["Coca-Cola"], "matched_documents": [], "narrowed": False}
    assert update["metrics"]["trajectory_steps"] == 2


def test_no_entities_means_no_title_listing_and_the_two_round_search(monkeypatch):
    update = _run(monkeypatch, ON, docs_depend_on_sql=True, sql_rows=[{"n": 7}], sql_columns=["n"])
    assert [m for m, _ in FakeSession.calls] == ["vector.semantic_search", "vector.semantic_search"]
    assert update["hybrid_entities"] == {"entities": [], "matched_documents": [], "narrowed": False}


def test_a_failed_title_listing_falls_back_to_the_two_round_search(monkeypatch):
    update = _run(monkeypatch, ON, docs_depend_on_sql=True, documents_ok=False)
    assert [m for m, _ in FakeSession.calls] == ["library.manage", "vector.semantic_search", "vector.semantic_search"]
    assert update["hybrid_entities"]["narrowed"] is False and update["hybrid_entities"]["matched_documents"] == []


def test_without_a_workspace_there_are_no_titles_to_list(monkeypatch):
    update = _run(monkeypatch, ON, docs_depend_on_sql=True, workspace_id=None)
    assert [m for m, _ in FakeSession.calls] == ["vector.semantic_search", "vector.semantic_search"]
    assert update["hybrid_entities"]["narrowed"] is False


def test_the_party_id_path_is_untouched_even_with_the_flag_on(monkeypatch):
    update = _run(monkeypatch, ON, docs_depend_on_sql=True, target_schema="public",
                  sql_rows=[{"seller_id": "s1", "company": "Coca-Cola"}], sql_columns=["seller_id", "company"])
    assert [m for m, _ in FakeSession.calls] == ["vector.hybrid_select"]
    assert "hybrid_entities" not in update and update["metrics"]["trajectory_steps"] == 1


def test_the_entity_step_runs_for_a_profile_without_party_columns_and_a_result_without_them(monkeypatch):
    """Both halves of "whenever the profile has no party-id columns or the result has none of them"."""
    ondc_no_ids = _run(monkeypatch, ON, docs_depend_on_sql=True, target_schema="public")
    assert ondc_no_ids["hybrid_entities"]["narrowed"] is True                       # the ONDC profile, no ids in rows
    neutral = _run(monkeypatch, ON, docs_depend_on_sql=True, target_schema="cn_owner")
    assert neutral["hybrid_entities"]["narrowed"] is True                           # a profile with no party columns


# ── flag off: exactly the base commit's MCP calls ───────────────────────────
# The expected lists below were produced by running this module's _run against the base commit's
# orchestrator (head of v3-phase3-workspaces at 3e277bc) and copying what the fake session recorded.
BASE_TWO_ROUND = [
    ("vector.semantic_search", {"query_text": "what drove it?", "top_k": TOP_K, "mode": "hybrid", "dataset": "cn_owner",
                               "scope": "dataset", "rerank": "bge", "rerank_titles": True}),
    ("vector.semantic_search", {"query_text": "what drove it?", "top_k": TOP_K_MAX, "document_ids": ["d_ko"],
                               "mode": "hybrid", "dataset": "cn_owner", "scope": "dataset", "rerank": "bge",
                               "rerank_titles": True}),
]
BASE_PARTY_IDS = [("vector.hybrid_select", {"sql": (
    "SELECT dc.chunk_id, dc.document_id, dc.content_md, dc.page, "
    "1 - (dc.embedding <=> ss_embed('what drove it?')) AS score "
    "FROM document_chunks dc JOIN documents d ON d.document_id = dc.document_id "
    "WHERE d.party_id IN ('s1') ORDER BY dc.embedding <=> ss_embed('what drove it?') "
    f"LIMIT {TOP_K}")})]


@pytest.mark.parametrize("flags,state_kw", [
    ({}, {}),                                                       # the flag absent from the snapshot
    ({"pipeline.hybrid_entities": "off"}, {}),
    ({"pipeline.hybrid_entities": "off"}, {"docs_depend_on_sql": True}),    # off beats a dependent split
    (ON, {}),                                                       # on, but the documents do not depend on SQL
    (ON, {"docs_depend_on_sql": False}),
])
def test_flag_off_or_independent_halves_make_exactly_the_base_commits_calls(monkeypatch, flags, state_kw):
    update = _run(monkeypatch, flags, **state_kw)
    assert FakeSession.calls == BASE_TWO_ROUND
    assert "hybrid_entities" not in update and set(update) == {"retrievals", "metrics"}
    assert update["metrics"]["trajectory_steps"] == 2


@pytest.mark.parametrize("flags,state_kw", [({}, {}), ({"pipeline.hybrid_entities": "off"}, {"docs_depend_on_sql": True}),
                                            (ON, {"docs_depend_on_sql": True})])
def test_the_party_id_path_makes_the_base_commits_calls(monkeypatch, flags, state_kw):
    update = _run(monkeypatch, flags, target_schema="public",
                  sql_rows=[{"seller_id": "s1"}], sql_columns=["seller_id"], **state_kw)
    assert FakeSession.calls == BASE_PARTY_IDS
    assert set(update) == {"retrievals", "metrics"}


def test_flag_off_makes_the_same_calls_as_the_base_commit_even_with_a_dependent_split_and_titles(monkeypatch):
    """WHY: nothing must list titles or skip a round with the flag off, however the front door split."""
    _run(monkeypatch, None, docs_depend_on_sql=True, documents=FILINGS)
    assert FakeSession.calls == BASE_TWO_ROUND


# ── state, config, the front door ───────────────────────────────────────────
def test_the_state_fields_default_to_the_old_behaviour_and_are_registered():
    state = StateSyncState(query_id="q", tenant_id="t", principal_id="p", session_id="s",
                           target_schema="public", raw_query="x")
    assert state.docs_depend_on_sql is False and state.hybrid_entities is None
    assert {"docs_depend_on_sql", "hybrid_entities"} <= _LAST_WRITE_WINS_FIELDS


def test_the_flag_ships_off_and_the_config_section_has_the_specified_values():
    assert config_loader.snapshot_flags()["pipeline.hybrid_entities"] == "off"
    assert int(cfg.hybrid.entity_rows) == 5 and int(cfg.hybrid.max_entities) == 5


def _front_door(monkeypatch, depends, **parsed_kw):
    evidence = front_door.Evidence(question="q", turns=[], turn_lines=[], next_turn_index=1, documents=[],
                                   document_text="", card="", card_status="ok")
    parsed = front_door.Parsed(parse_ok=True, language="en", standalone_question="q", uses_history=False,
                               needs_database="yes", needs_documents="yes", out_of_scope=False,
                               database_question="db?", documents_question="docs?",
                               documents_depend_on_database=depends, **parsed_kw)

    async def gather(state):
        return evidence

    async def decide(ev, question):
        return front_door.Decision("llm", parsed, "hybrid", tokens_in=1, tokens_out=1, llm_calls=1)

    monkeypatch.setattr(front_door, "gather_evidence", gather)
    monkeypatch.setattr(front_door, "DECIDERS", {"llm": decide})
    return RUN(front_door.front_door_node({"raw_query": "q", "query_id": "q1"}, "llm"))


@pytest.mark.parametrize("depends", [True, False])
def test_the_front_door_sets_docs_depend_on_sql_from_its_decision(monkeypatch, depends):
    update = _front_door(monkeypatch, depends)
    assert update["docs_depend_on_sql"] is depends
    assert update["front_door"]["documents_depend_on_database"] is depends      # the trace is unchanged


def test_only_the_front_door_writes_the_field_so_the_legacy_path_never_sets_it():
    """WHY: the normaliser, router and rewrite node (front door off) must leave it False. Structural pin:
    the field is mentioned only by the state, the front door and the documents half that reads it."""
    users = {str(p.relative_to(REPO_ROOT / "orchestrator")) for p in (REPO_ROOT / "orchestrator").rglob("*.py")
             if "docs_depend_on_sql" in p.read_text()}
    assert users == {"state.py", "front_door.py", "nodes/unstructured_knowledge.py"}
    assert "docs_depend_on_sql" not in (REPO_ROOT / "orchestrator" / "nodes" / "normalize.py").read_text()

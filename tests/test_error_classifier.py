"""
tests/test_error_classifier.py
C2 Deliverable 1 — asserts every rule in config/error_taxonomy.json is
reachable (Postgres-realistic input in, expected category out), using
fixtures/malformed_queries.json's 12 seeds as the primary inputs.

Two seeds cannot come from the fixture directly:
  - mq_10 (join_cardinality) never reaches the classifier at all -- its SQL
    executes successfully with silently wrong data, not an engine error.
    See docs/spec-gaps.md GAP-2 and docs/phase_b_amendment.md BD3. Asserted
    here as "not classifiable from sqlstate/message/empty_result", not
    skipped silently.
  - The 12 seeds don't naturally exercise every taxonomy rule (type_mismatch
    has two independent rules -- 42883 and 22P02 -- and only one shows up
    in the fixture; vector_dim_mismatch has no fixture seed at all, since
    nothing in this schema uses pgvector on the relational path). Those two
    rules are covered by supplementary synthetic cases below, clearly
    separated from the fixture-derived ones.

Run from the repo root: pytest tests/test_error_classifier.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

from classifiers.error_classifier import TERMINAL_CATEGORIES, classify_error  # noqa: E402

FIXTURE_PATH = REPO_ROOT / "fixtures" / "malformed_queries.json"

# Seed id -> the (sqlstate, message, empty_result) a live run of that seed's
# SQL actually produces against this schema (A5) / MCP layer stack. This is
# test-local knowledge, not part of the fixture (the fixture's structure is
# frozen verbatim per the C2 spec) -- verified empirically while building
# fixtures/malformed_queries.json and scripts/bench_correction.py.
_SEED_ENGINE_RESPONSE: dict[str, dict] = {
    "mq_01": {"sqlstate": "42703", "message": 'column f.seller_id does not exist'},
    "mq_02": {"sqlstate": "42P01", "message": 'relation "shipments" does not exist'},
    "mq_03": {"sqlstate": "42702", "message": 'column reference "seller_id" is ambiguous'},
    "mq_04": {"sqlstate": None, "message": '[Layer 1 Gate] SQL parse error: syntax error at or near ";", at index 24'},
    "mq_05": {"sqlstate": "42883", "message": "operator does not exist: timestamp with time zone > integer"},
    "mq_06": {"sqlstate": "42803", "message": 'column "orders.order_value" must appear in the GROUP BY clause or be used in an aggregate function'},
    "mq_07": {"sqlstate": None, "message": "[Layer 1 Gate] Rejected: top-level statement must be SelectStmt, got 'UpdateStmt'."},
    "mq_08": {"sqlstate": "XX000", "message": "LWGEOM_dwithin: Operation on mixed SRID geometries (Point, 4326) != (Point, 3857)"},
    "mq_09": {"sqlstate": None, "message": "", "empty_result": True},
    # mq_10 handled separately -- see module docstring.
    "mq_11": {"sqlstate": "57014", "message": "canceling statement due to statement timeout"},
    "mq_12": {"sqlstate": "22012", "message": "division by zero"},
}


@pytest.fixture(scope="module")
def seeds() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text())["seeds"]


def test_every_seed_except_join_cardinality_classifies_as_expected(seeds):
    for seed in seeds:
        if seed["id"] == "mq_10":
            continue  # covered by its own test below
        response = _SEED_ENGINE_RESPONSE[seed["id"]]
        category, hint = classify_error(
            sqlstate=response.get("sqlstate"),
            message=response.get("message"),
            empty_result=response.get("empty_result", False),
        )
        assert category == seed["expected_category"], (
            f"{seed['id']}: expected {seed['expected_category']!r}, got {category!r} "
            f"(input={response!r})"
        )
        assert hint, f"{seed['id']}: remediation_hint must be non-empty"


def test_mq_10_join_cardinality_does_not_reach_the_classifier():
    """
    GAP-2 / BD3: the fan-out query behind mq_10 executes successfully
    (ok: true, non-empty rows) -- there is no sqlstate, message, or
    empty-result signal for the classifier to match on. Feeding it a
    "successful, non-empty" shape (sqlstate=None, empty_result=False)
    must NOT accidentally land on "join_cardinality" (no rule claims that
    category) or on any other real category -- it must fall through to the
    unclassified fallback, which is the honest "the classifier was never
    actually given a failure to classify" signal.
    """
    category, _ = classify_error(sqlstate=None, message="", empty_result=False)
    assert category == "unclassified"


def test_every_taxonomy_rule_is_reachable(seeds):
    taxonomy = json.loads((REPO_ROOT / "config" / "error_taxonomy.json").read_text())
    overrides = json.loads((REPO_ROOT / "config" / "error_taxonomy_overrides.json").read_text())
    expected_categories = {rule["category"] for rule in taxonomy["rules"] + overrides["rules"]}
    expected_categories.add(taxonomy["fallback"]["category"])

    reached: set[str] = set()
    for seed in seeds:
        if seed["id"] == "mq_10":
            continue
        response = _SEED_ENGINE_RESPONSE[seed["id"]]
        category, _ = classify_error(
            sqlstate=response.get("sqlstate"),
            message=response.get("message"),
            empty_result=response.get("empty_result", False),
        )
        reached.add(category)

    # Supplementary synthetic cases for rules the 12 seeds don't naturally
    # exercise (see module docstring).
    reached.add(classify_error(sqlstate="22P02", message="invalid input syntax for type date")[0])
    reached.add(classify_error(sqlstate=None, message="expected 1024 dimensions, not 768")[0])
    reached.add(classify_error(sqlstate="99999", message="totally unrecognized engine failure")[0])
    reached.add(classify_error(sqlstate=None, message="[Layer 1 Gate] Rejected: statement reads no table.")[0])

    missing = expected_categories - reached
    assert not missing, f"Unreachable taxonomy categories: {missing}"


def test_terminal_categories_match_a3():
    assert TERMINAL_CATEGORIES == {"permission_denied", "no_data_source", "unclassified"}


def test_remediation_hint_is_never_empty_for_any_rule():
    taxonomy = json.loads((REPO_ROOT / "config" / "error_taxonomy.json").read_text())
    for rule in taxonomy["rules"]:
        assert rule["hint"].strip(), f"Rule for {rule['category']!r} has an empty hint."
    assert taxonomy["fallback"]["hint"].strip()


def test_reads_no_table_is_no_data_source_and_other_gate_rejections_stay_permission_denied():
    """routing_docs_fixes.md F-S2: a statement that reads no table means the database does not hold what
    was asked; every other Layer 1 rejection keeps its security classification."""
    no_table = ("[Layer 1 Gate] Rejected: statement reads no table. Queries must read data from the schema "
                "rather than compute constants or server introspection. This rejection is based on query "
                "shape, not content safety.")
    assert classify_error(sqlstate=None, message=no_table)[0] == "no_data_source"
    for message in ("[Layer 1 Gate] Rejected: top-level statement must be SelectStmt, got 'UpdateStmt'.",
                    "[Layer 1 Gate] Rejected: expected exactly 1 SQL statement, got 2. Multi-statement submissions are not permitted."):
        assert classify_error(sqlstate=None, message=message)[0] == "permission_denied"
    assert classify_error(sqlstate="42501", message="permission denied for table x")[0] == "permission_denied"


def test_shared_taxonomy_file_holds_only_categories_old_images_know():
    """The shared, live-mounted error_taxonomy.json must stay readable by images built before 2026-10-05
    (their ErrorCategory has no no_data_source); new categories go in the overrides file."""
    taxonomy = json.loads((REPO_ROOT / "config" / "error_taxonomy.json").read_text())
    assert "no_data_source" not in {rule["category"] for rule in taxonomy["rules"]}

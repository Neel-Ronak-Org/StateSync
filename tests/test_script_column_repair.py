"""
tests/test_script_column_repair.py
Pins the deterministic repair of script/column mismatch predicates (Workstream B1).

WHY THIS EXISTS:
The A5 schema pairs a romanised column with an Indic-script one:
  locations.city / locations.city_local
  sellers.display_name / sellers.display_name_local
  catalog_items.title / catalog_items.title_local

Asked "Lucknow mein kitne sellers hain?" the agent emits:
  ... WHERE l.city_local = 'Lucknow'
A Latin literal against the Devanagari column. It matches zero rows and the user
is told the answer is 0. A Latin literal against a Devanagari column silently answered 0
and the user believed it. Measured on build: six such mismatching predicates
(e.g. city_local='Delhi', city_local='Bengaluru', city_local='Chennai', etc.).

WHY THE REPAIR IS PROVABLY SAFE (verified in the live database):
  locations.city_local        51 rows, 0 Latin-only
  locations.city              51 rows, 0 non-ASCII
  sellers.display_name_local  30 rows, 0 Latin-only
  sellers.display_name        30 rows, 0 non-ASCII
  catalog_items.title_local  100 rows, 0 Latin-only
  catalog_items.title        100 rows, 0 non-ASCII

So a script/column mismatch predicate is GUARANTEED to match zero rows. Rewriting
it onto the paired column cannot turn a correct query into an incorrect one,
because the original could not have been correct.

Four earlier attempts to fix this by INFLUENCING the model all failed and are
documented: column comments (migration 005, measured harmful, reverted), a prompt
instruction (_format_entity_hint, still shipped, still disobeyed), sampled evidence,
and interrogation column selection. This repair is deliberately MECHANICAL and
involves no model call.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("pglast")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

from nodes.sql_agent import (
    _derive_paired_columns,
    _is_indic_literal,
    _repair_script_column_mismatch,
)


# ── Fixtures in A4.2 describe_table shape ─────────────────────────────────────
# Describe_table documented return shape in mcp/tools/schema_tools.py:
#   {
#       "table": str,
#       "columns": [{"name": str, "type": str, "nullable": bool, "comment": str|None}],
#       "foreign_keys": [{"column": str, "references_table": str, "references_column": str}],
#       "fingerprint": str,
#   }

A5_TABLE_DESCRIPTIONS: list[dict] = [
    {
        "table": "locations",
        "columns": [
            {"name": "location_id", "type": "text", "nullable": False, "comment": None},
            {"name": "city", "type": "text", "nullable": True, "comment": None},
            {"name": "city_local", "type": "text", "nullable": True, "comment": None},
        ],
        "foreign_keys": [],
        "fingerprint": "fp_locations",
    },
    {
        "table": "sellers",
        "columns": [
            {"name": "seller_id", "type": "text", "nullable": False, "comment": None},
            {"name": "display_name", "type": "text", "nullable": True, "comment": None},
            {"name": "display_name_local", "type": "text", "nullable": True, "comment": None},
        ],
        "foreign_keys": [],
        "fingerprint": "fp_sellers",
    },
    {
        "table": "catalog_items",
        "columns": [
            {"name": "item_id", "type": "text", "nullable": False, "comment": None},
            {"name": "title", "type": "text", "nullable": True, "comment": None},
            {"name": "title_local", "type": "text", "nullable": True, "comment": None},
        ],
        "foreign_keys": [],
        "fingerprint": "fp_catalog_items",
    },
    {
        "table": "orders",
        "columns": [
            {"name": "order_id", "type": "text", "nullable": False, "comment": None},
            {"name": "order_channel", "type": "text", "nullable": True, "comment": None},
        ],
        "foreign_keys": [],
        "fingerprint": "fp_orders",
    },
]

OLIST_TABLE_DESCRIPTIONS: list[dict] = [
    {
        "table": "olist_orders",
        "columns": [
            {"name": "order_id", "type": "text", "nullable": False, "comment": None},
            {"name": "customer_id", "type": "text", "nullable": False, "comment": None},
            {"name": "order_status", "type": "text", "nullable": True, "comment": None},
        ],
        "foreign_keys": [],
        "fingerprint": "fp_olist_orders",
    },
    {
        "table": "olist_order_items",
        "columns": [
            {"name": "order_id", "type": "text", "nullable": False, "comment": None},
            {"name": "order_item_id", "type": "integer", "nullable": False, "comment": None},
            {"name": "product_id", "type": "text", "nullable": False, "comment": None},
        ],
        "foreign_keys": [],
        "fingerprint": "fp_olist_items",
    },
]


# ── Unit tests for helper classifications ────────────────────────────────────

def test_is_indic_literal_classification():
    """Verify Devanagari U+0900–U+097F detection."""
    assert _is_indic_literal("मुंबई") is True
    assert _is_indic_literal("विक्रेता") is True
    assert _is_indic_literal("Lucknow") is False
    assert _is_indic_literal("Delhi") is False
    assert _is_indic_literal("Seller Store 5") is False
    assert _is_indic_literal("b2b") is False


def test_derive_paired_columns_requires_same_table():
    """Columns are paired iff <base>_local and <base> exist in the SAME table."""
    local_to_base, base_to_local = _derive_paired_columns(A5_TABLE_DESCRIPTIONS)
    assert local_to_base == {
        "city_local": "city",
        "display_name_local": "display_name",
        "title_local": "title",
    }
    assert base_to_local == {
        "city": "city_local",
        "display_name": "display_name_local",
        "title": "title_local",
    }

    # Table with <base>_local but NO <base> in the same table must not be paired
    mismatched = [
        {
            "table": "test_table",
            "columns": [
                {"name": "unpaired_local", "type": "text", "nullable": True},
            ],
        }
    ]
    loc_map, base_map = _derive_paired_columns(mismatched)
    assert loc_map == {}
    assert base_map == {}


# ── MUST REWRITE cases ───────────────────────────────────────────────────────

def test_must_rewrite_aliased_city_local():
    """WHERE l.city_local = 'Lucknow' -> l.city = 'Lucknow'."""
    sql = "WHERE l.city_local = 'Lucknow'"
    repaired, notes = _repair_script_column_mismatch(sql, A5_TABLE_DESCRIPTIONS)
    assert repaired == "WHERE l.city = 'Lucknow'"
    assert len(notes) == 1
    assert "city_local -> city" in notes[0]
    assert "Latin literal" in notes[0]


def test_must_rewrite_unaliased_city_local():
    """WHERE city_local = 'Delhi' -> city = 'Delhi'."""
    sql = "WHERE city_local = 'Delhi'"
    repaired, notes = _repair_script_column_mismatch(sql, A5_TABLE_DESCRIPTIONS)
    assert repaired == "WHERE city = 'Delhi'"
    assert len(notes) == 1
    assert "city_local -> city" in notes[0]


def test_must_rewrite_display_name_local():
    """WHERE s.display_name_local = 'Seller Store 5' -> s.display_name = 'Seller Store 5'."""
    sql = "WHERE s.display_name_local = 'Seller Store 5'"
    repaired, notes = _repair_script_column_mismatch(sql, A5_TABLE_DESCRIPTIONS)
    assert repaired == "WHERE s.display_name = 'Seller Store 5'"
    assert len(notes) == 1
    assert "display_name_local -> display_name" in notes[0]


def test_must_rewrite_indic_literal_against_base_column():
    """WHERE l.city = 'मुंबई' -> l.city_local = 'मुंबई'."""
    sql = "WHERE l.city = 'मुंबई'"
    repaired, notes = _repair_script_column_mismatch(sql, A5_TABLE_DESCRIPTIONS)
    assert repaired == "WHERE l.city_local = 'मुंबई'"
    assert len(notes) == 1
    assert "city -> city_local" in notes[0]
    assert "Indic literal" in notes[0]


def test_must_rewrite_padded_literal_preserving_spaces():
    """WHERE l.city_local = ' Lucknow ' -> l.city = ' Lucknow ' (literal preserved with its spaces)."""
    sql = "WHERE l.city_local = ' Lucknow '"
    repaired, notes = _repair_script_column_mismatch(sql, A5_TABLE_DESCRIPTIONS)
    assert repaired == "WHERE l.city = ' Lucknow '"
    assert len(notes) == 1
    # Verify byte-identical literal retention
    assert "' Lucknow '" in repaired


def test_must_rewrite_full_select_query():
    """Full SELECT statement rewrites target column and preserves all aliases and clauses."""
    sql = "SELECT count(*) FROM sellers s JOIN locations l ON s.location_id = l.location_id WHERE l.city_local = 'Lucknow'"
    repaired, notes = _repair_script_column_mismatch(sql, A5_TABLE_DESCRIPTIONS)
    expected = "SELECT count(*) FROM sellers s JOIN locations l ON s.location_id = l.location_id WHERE l.city = 'Lucknow'"
    assert repaired == expected
    assert len(notes) == 1


# ── MUST NOT TOUCH cases ─────────────────────────────────────────────────────

def test_must_not_touch_latin_literal_against_base_column():
    """WHERE l.city = 'Mumbai' (correct already)."""
    sql = "WHERE l.city = 'Mumbai'"
    repaired, notes = _repair_script_column_mismatch(sql, A5_TABLE_DESCRIPTIONS)
    assert repaired == sql
    assert notes == []


def test_must_not_touch_indic_literal_against_local_column():
    """WHERE l.city_local = 'मुंबई' (correct already)."""
    sql = "WHERE l.city_local = 'मुंबई'"
    repaired, notes = _repair_script_column_mismatch(sql, A5_TABLE_DESCRIPTIONS)
    assert repaired == sql
    assert notes == []


def test_must_not_touch_non_paired_column():
    """WHERE o.order_channel = 'b2b' (not a paired column)."""
    sql = "WHERE o.order_channel = 'b2b'"
    repaired, notes = _repair_script_column_mismatch(sql, A5_TABLE_DESCRIPTIONS)
    assert repaired == sql
    assert notes == []


def test_must_not_touch_query_without_equality_predicates():
    """A query with no equality predicate at all is returned untouched."""
    sql = "SELECT count(*) FROM orders"
    repaired, notes = _repair_script_column_mismatch(sql, A5_TABLE_DESCRIPTIONS)
    assert repaired == sql
    assert notes == []


# ── OLIST corpus no-op assertion ─────────────────────────────────────────────

def test_olist_corpus_produces_zero_repairs():
    """
    A table_descriptions input with NO _local columns produces zero repairs (the olist no-op case).
    """
    local_to_base, base_to_local = _derive_paired_columns(OLIST_TABLE_DESCRIPTIONS)
    assert local_to_base == {}
    assert base_to_local == {}

    # Even with a predicate referencing city_local, olist schema has no such pairs
    sql = "WHERE city_local = 'Delhi'"
    repaired, notes = _repair_script_column_mismatch(sql, OLIST_TABLE_DESCRIPTIONS)
    assert repaired == sql
    assert notes == []


# ── Notes non-empty invariant ────────────────────────────────────────────────

@pytest.mark.parametrize("sql,should_rewrite", [
    ("WHERE l.city_local = 'Lucknow'", True),
    ("WHERE city_local = 'Delhi'", True),
    ("WHERE s.display_name_local = 'Seller Store 5'", True),
    ("WHERE l.city = 'मुंबई'", True),
    ("WHERE l.city_local = ' Lucknow '", True),
    ("WHERE l.city = 'Mumbai'", False),
    ("WHERE l.city_local = 'मुंबई'", False),
    ("WHERE o.order_channel = 'b2b'", False),
    ("SELECT count(*) FROM orders", False),
])
def test_notes_non_empty_iff_rewrite_happened(sql: str, should_rewrite: bool):
    """The returned notes are non-empty exactly when a rewrite happened."""
    repaired, notes = _repair_script_column_mismatch(sql, A5_TABLE_DESCRIPTIONS)
    if should_rewrite:
        assert repaired != sql
        assert len(notes) > 0
    else:
        assert repaired == sql
        assert len(notes) == 0


# ── Parse validation discard rule (Rule 6) ───────────────────────────────────

def test_discards_rewrite_if_unparseable():
    """
    AFTER rewriting, re-parse the result with pglast. If it fails to parse,
    DISCARD the rewrite entirely and return the original SQL with a note.
    """
    sql = "SELECT * FROM locations l WHERE l.city_local = 'Lucknow'"

    import pglast

    original_parse_sql = pglast.parse_sql
    calls = 0

    def fail_second_parse(s, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise pglast.Error("Simulated post-repair parse failure")
        return original_parse_sql(s, *args, **kwargs)

    with patch("nodes.sql_agent.pglast.parse_sql", side_effect=fail_second_parse):
        repaired, notes = _repair_script_column_mismatch(sql, A5_TABLE_DESCRIPTIONS)
        # Rewrite discarded: returns original SQL
        assert repaired == sql
        assert len(notes) == 1
        assert "Repair discarded" in notes[0]


# ── StateSyncState registration test ─────────────────────────────────────────

def test_script_repairs_registered_in_state_and_append_fields():
    """
    Assert that 'script_repairs' is present in _APPEND_FIELDS in orchestrator/state.py
    and declared on StateSyncState -- so a future refactor cannot silently unregister
    it and reintroduce state trace invisibility.
    """
    from state import StateSyncState, _APPEND_FIELDS

    assert "script_repairs" in _APPEND_FIELDS
    assert "script_repairs" in StateSyncState.model_fields


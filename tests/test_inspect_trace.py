"""
tests/test_inspect_trace.py
Pins the Pipeline Inspector's two backend pieces (pipeline.inspect = "on"): the documents search's stage-by-stage
trace (each candidate's scores and its place before and after Jev's re-ordering) and the schema graph built from
the MCP's own schema description. Both are pure; no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

import schema_graph as sg  # noqa: E402
import nodes.unstructured_knowledge as uk  # noqa: E402
from state import Provenance  # noqa: E402


def _m(cid, score, rerank=None, jev=None):
    m = {"chunk_id": cid, "document_id": "d1", "title": "ACME 2022 10K", "page": 3, "content_md": "text", "score": score}
    if rerank is not None:
        m["rerank_score"] = rerank
    if jev is not None:
        m["jev_score"] = jev
    return m


def test_retrieval_trace_records_both_orders_and_no_text():
    searched = [_m("a", 0.7, 0.9), _m("b", 0.8, 0.5), _m("c", 0.6, 0.4)]
    final = [{**searched[2], "jev_score": 0.95}, {**searched[0], "jev_score": 0.6}, {**searched[1], "jev_score": 0.1}]
    t = uk.retrieval_trace("revenue ACME 2022", [uk._stage_row(searched[1])], searched, final,
                           {"rerank": "bge", "window": 8}, {"search": 120, "jev": 40}, {"scored": 3, "failed": 0, "ms": 40})
    assert [(p["chunk_id"], p["rank_searched"], p["rank_final"]) for p in t["passages"]] == [("c", 3, 1), ("a", 1, 2), ("b", 2, 3)]
    assert t["passages"][0] == {"chunk_id": "c", "document_id": "d1", "title": "ACME 2022 10K", "page": 3, "score": 0.6,
                                "rerank_score": 0.4, "jev_score": 0.95, "rank_searched": 3, "rank_final": 1}
    assert t["round1"] == [{"chunk_id": "b", "document_id": "d1", "title": "ACME 2022 10K", "page": 3, "score": 0.8,
                            "rerank_score": 0.5, "jev_score": None}]
    assert t["search_text"] == "revenue ACME 2022" and t["ms"] == {"search": 120, "jev": 40} and t["narrowed_to"] == []
    assert "content_md" not in str(t) and "text" not in [k for p in t["passages"] for k in p]


def test_inspect_flag_default_off():
    assert uk._inspect_on({}) is False and uk._inspect_on(None) is False
    assert uk._inspect_on({"pipeline.inspect": "off"}) is False and uk._inspect_on({"pipeline.inspect": "on"}) is True


def test_provenance_keeps_jev_score():
    r = uk._to_retrievals([_m("a", 0.7, 0.9, 0.33)])[0]
    assert Provenance(**r["provenance"]).jev_score == 0.33
    assert Provenance(**uk._to_retrievals([_m("a", 0.7)])[0]["provenance"]).jev_score is None


def test_schema_graph_tables_columns_and_edges():
    listing = {"tables": [{"name": "companies", "row_estimate": 31}, {"name": "annual_metrics", "row_estimate": 5000},
                          {"name": "facts", "row_estimate": 90000}]}
    described = [
        {"table": "companies", "columns": [{"name": "ticker", "type": "text", "nullable": False}], "foreign_keys": []},
        {"table": "annual_metrics",
         "columns": [{"name": "ticker", "type": "text", "nullable": False}, {"name": "value", "type": "numeric", "nullable": True}],
         "foreign_keys": [{"column": "ticker", "references_table": "companies", "references_column": "ticker"},
                          {"column": "value", "references_table": "not_described", "references_column": "id"}]},
    ]
    g = sg.schema_graph("sec", listing, described, 3)
    assert g["dataset"] == "sec" and g["tables_total"] == 3 and [t["name"] for t in g["tables"]] == ["companies", "annual_metrics"]
    assert g["tables"][1]["row_estimate"] == 5000
    assert g["tables"][1]["columns"] == [
        {"name": "ticker", "type": "text", "nullable": False, "foreign_key": True},
        {"name": "value", "type": "numeric", "nullable": True, "foreign_key": True}]
    assert g["edges"] == [{"from_table": "annual_metrics", "from_column": "ticker", "to_table": "companies", "to_column": "ticker"}]

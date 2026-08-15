#!/usr/bin/env python3
"""
scripts/gen_phase_e1_fixture.py
Generates fixtures/golden/phase_e1.json -- 25 queries (8 pure-document, 8
hybrid, 9 pure-SQL controls), each with expected supporting chunk_ids
(Deliverable 7). ground_truth_sql is executed live against mcp (bypassing
the LLM, same discipline as every other golden fixture in this build) to
freeze expected_row_count; expected_chunk_ids are specified directly since
they're deterministic from the corpus (each document has exactly one
narrative chunk _c000 and one table chunk _c001 -- see
scripts/gen_document_corpus.py / scripts/ingest.py).

Hybrid queries are deliberately entity-specific (name a seller/provider
explicitly) except e1_hybrid_001, which is E1-V4's own mandated worked
example ("which sellers breached...") -- an open-ended query whose exact
matching sellers depend on live data, so its expected_chunk_ids are left
empty here and checked directly by E1-V4 rather than by this fixture.

Run once; the output is frozen (Appendix 2.2 spirit).

Usage:
    python3 scripts/gen_phase_e1_fixture.py
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "fixtures" / "golden" / "phase_e1.json"

MCP_URL = "http://localhost:8080"
TENANT = "t_capstone_default"


def mcp_run_select(sql: str) -> dict:
    payload = json.dumps({
        "jsonrpc": "2.0", "id": "1", "method": "data.run_select", "params": {"sql": sql},
    })
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", f"{MCP_URL}/rpc",
         "-H", "Content-Type: application/json",
         "-H", f"X-Tenant-ID: {TENANT}",
         "-H", "X-Session-ID: gen-phase-e1-fixture",
         "-d", payload],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)["result"]


def _doc_chunks(*stems: str) -> list[str]:
    ids = []
    for stem in stems:
        ids.append(f"doc_{stem}_c000")
        ids.append(f"doc_{stem}_c001")
    return ids


QUERIES: list[dict] = [
    # ── 8 pure-document ──────────────────────────────────────────────────
    {"id": "e1_doc_001", "lang": "en", "expected_route": "docs",
     "text": "What is the contracted delivery SLA for supplier S014 in Metro NCR?",
     "expected_chunk_ids": _doc_chunks("supplier_agreement_S014"),
     "ground_truth_sql": None, "notes": "E1-V2 worked example."},
    {"id": "e1_doc_002", "lang": "en", "expected_route": "docs",
     "text": "What is the ONDC network's return window in days for the electronics category, per the returns policy?",
     "expected_chunk_ids": _doc_chunks("policy_returns_refunds"),
     "ground_truth_sql": None, "notes": ""},
    {"id": "e1_doc_003", "lang": "en", "expected_route": "docs",
     "text": "What penalty does supplier S001's agreement specify for the Metro NCR zone?",
     "expected_chunk_ids": _doc_chunks("supplier_agreement_S001"),
     "ground_truth_sql": None, "notes": ""},
    {"id": "e1_doc_004", "lang": "en", "expected_route": "docs",
     "text": "What committed transit time does FastShip 1's SLA annexure specify for the Metro Mumbai zone?",
     "expected_chunk_ids": _doc_chunks("sla_annexure_LP001"),
     "ground_truth_sql": None, "notes": ""},
    {"id": "e1_doc_005", "lang": "en", "expected_route": "docs",
     "text": "What is the committed resolution window in business days for a damaged_goods dispute, per the dispute resolution policy?",
     "expected_chunk_ids": _doc_chunks("policy_dispute_resolution"),
     "ground_truth_sql": None, "notes": ""},
    {"id": "e1_doc_006", "lang": "en", "expected_route": "docs",
     "text": "What action is required when a delivery exception occurs because the recipient is unavailable, per the delivery exception handling SOP?",
     "expected_chunk_ids": _doc_chunks("sop_delivery_exceptions"),
     "ground_truth_sql": None, "notes": ""},
    {"id": "e1_doc_007", "lang": "en", "expected_route": "docs",
     "text": "What is the maximum transit time in hours for frozen goods, per the cold chain and perishables handling SOP?",
     "expected_chunk_ids": _doc_chunks("sop_cold_chain"),
     "ground_truth_sql": None, "notes": ""},
    {"id": "e1_doc_008", "lang": "en", "expected_route": "docs",
     "text": "How often must a seller renew their GST registration compliance, per the seller onboarding and compliance policy?",
     "expected_chunk_ids": _doc_chunks("policy_seller_compliance"),
     "ground_truth_sql": None, "notes": ""},

    # ── 8 hybrid ──────────────────────────────────────────────────────────
    {"id": "e1_hybrid_001", "lang": "en", "expected_route": "hybrid",
     "text": "Which sellers breached their contracted delivery SLA last quarter, and what penalty does their agreement specify?",
     "expected_chunk_ids": [],
     "ground_truth_sql": None, "notes": "E1-V4 mandated worked example -- open-ended, checked directly by E1-V4, not scored here."},
    {"id": "e1_hybrid_002", "lang": "en", "expected_route": "hybrid",
     "text": "How many disputes has seller S014 had in total, and what is the committed resolution window for disputes per the dispute resolution policy?",
     "expected_chunk_ids": _doc_chunks("policy_dispute_resolution"),
     "ground_truth_sql": "SELECT COUNT(*) FROM disputes d JOIN orders o ON d.order_id = o.order_id WHERE o.seller_id = 'S014'",
     "notes": ""},
    {"id": "e1_hybrid_003", "lang": "en", "expected_route": "hybrid",
     "text": "What is seller S005's contracted SLA in hours in the database, and what does S005's supplier agreement specify for the Metro NCR zone?",
     "expected_chunk_ids": _doc_chunks("supplier_agreement_S005"),
     "ground_truth_sql": "SELECT contracted_sla_hours FROM sellers WHERE seller_id = 'S005'",
     "notes": ""},
    {"id": "e1_hybrid_004", "lang": "en", "expected_route": "hybrid",
     "text": "How many orders has seller S001 placed, and what penalty does S001's agreement specify for the Metro NCR zone?",
     "expected_chunk_ids": _doc_chunks("supplier_agreement_S001"),
     "ground_truth_sql": "SELECT COUNT(*) FROM orders WHERE seller_id = 'S001'",
     "notes": ""},
    {"id": "e1_hybrid_005", "lang": "en", "expected_route": "hybrid",
     "text": "How many fulfillments has FastShip 1 handled, and what transit time does FastShip 1 commit to for the Metro Mumbai zone?",
     "expected_chunk_ids": _doc_chunks("sla_annexure_LP001"),
     "ground_truth_sql": "SELECT COUNT(*) FROM fulfillments WHERE provider_id = 'LP001'",
     "notes": ""},
    {"id": "e1_hybrid_006", "lang": "en", "expected_route": "hybrid",
     "text": "How many disputes does seller S010 have in the damaged_goods category, and what is the committed resolution window for that category?",
     "expected_chunk_ids": _doc_chunks("policy_dispute_resolution"),
     "ground_truth_sql": "SELECT COUNT(*) FROM disputes d JOIN orders o ON d.order_id = o.order_id WHERE o.seller_id = 'S010' AND d.category = 'damaged_goods'",
     "notes": ""},
    {"id": "e1_hybrid_007", "lang": "en", "expected_route": "hybrid",
     "text": "What category is seller S020 in, in the database, and what penalty does S020's agreement specify for the Rural North zone?",
     "expected_chunk_ids": _doc_chunks("supplier_agreement_S020"),
     "ground_truth_sql": "SELECT category FROM sellers WHERE seller_id = 'S020'",
     "notes": ""},
    {"id": "e1_hybrid_008", "lang": "en", "expected_route": "hybrid",
     "text": "How many orders has seller S025 placed, and what penalty does S025's supplier agreement specify for the Tier-2 West zone?",
     "expected_chunk_ids": _doc_chunks("supplier_agreement_S025"),
     "ground_truth_sql": "SELECT COUNT(*) FROM orders WHERE seller_id = 'S025'",
     "notes": ""},

    # ── 9 pure-SQL controls ───────────────────────────────────────────────
    {"id": "e1_sql_001", "lang": "en", "expected_route": "sql",
     "text": "How many orders has seller S008 placed?",
     "expected_chunk_ids": [], "ground_truth_sql": "SELECT COUNT(*) FROM orders WHERE seller_id = 'S008'", "notes": ""},
    {"id": "e1_sql_002", "lang": "en", "expected_route": "sql",
     "text": "What is the total order value for seller S012?",
     "expected_chunk_ids": [], "ground_truth_sql": "SELECT SUM(order_value) FROM orders WHERE seller_id = 'S012'", "notes": ""},
    {"id": "e1_sql_003", "lang": "en", "expected_route": "sql",
     "text": "How many disputes are in the wrong_item category?",
     "expected_chunk_ids": [], "ground_truth_sql": "SELECT COUNT(*) FROM disputes WHERE category = 'wrong_item'", "notes": ""},
    {"id": "e1_sql_004", "lang": "en", "expected_route": "sql",
     "text": "How many logistics providers are b2b_enabled?",
     "expected_chunk_ids": [], "ground_truth_sql": "SELECT COUNT(*) FROM logistics_providers WHERE b2b_enabled = true", "notes": ""},
    {"id": "e1_sql_005", "lang": "en", "expected_route": "sql",
     "text": "How many orders were placed via the b2c channel?",
     "expected_chunk_ids": [], "ground_truth_sql": "SELECT COUNT(*) FROM orders WHERE order_channel = 'b2c'", "notes": ""},
    {"id": "e1_sql_006", "lang": "en", "expected_route": "sql",
     "text": "What is the average order value across all orders, rounded to 2 decimal places?",
     "expected_chunk_ids": [], "ground_truth_sql": "SELECT ROUND(AVG(order_value), 2) FROM orders", "notes": ""},
    {"id": "e1_sql_007", "lang": "en", "expected_route": "sql",
     "text": "How many fulfillments are currently pending?",
     "expected_chunk_ids": [], "ground_truth_sql": "SELECT COUNT(*) FROM fulfillments WHERE status = 'pending'", "notes": ""},
    {"id": "e1_sql_008", "lang": "en", "expected_route": "sql",
     "text": "How many sellers are in the electronics category?",
     "expected_chunk_ids": [], "ground_truth_sql": "SELECT COUNT(*) FROM sellers WHERE category = 'electronics'", "notes": ""},
    {"id": "e1_sql_009", "lang": "en", "expected_route": "sql",
     "text": "How many delivery events have a null location_id?",
     "expected_chunk_ids": [], "ground_truth_sql": "SELECT COUNT(*) FROM delivery_events WHERE location_id IS NULL", "notes": ""},
]


def main() -> None:
    doc_count = sum(1 for q in QUERIES if q["expected_route"] == "docs")
    hybrid_count = sum(1 for q in QUERIES if q["expected_route"] == "hybrid")
    sql_count = sum(1 for q in QUERIES if q["expected_route"] == "sql")
    print(f"Built {len(QUERIES)} queries: {doc_count} docs, {hybrid_count} hybrid, {sql_count} sql.")
    assert len(QUERIES) == 25
    assert doc_count == 8 and hybrid_count == 8 and sql_count == 9

    print("\nExecuting ground_truth_sql live for sql/hybrid queries...")
    for q in QUERIES:
        if q["ground_truth_sql"] is None:
            q["expected_row_count"] = None
            continue
        r = mcp_run_select(q["ground_truth_sql"])
        if not r.get("ok"):
            print(f"  [{q['id']}] GROUND TRUTH SQL FAILED: {r.get('error')}")
            raise SystemExit(1)
        q["expected_row_count"] = r["row_count"]
        print(f"  [{q['id']}] rows={r['row_count']} value={r['rows'][0] if r['rows'] else None}")

    OUT_PATH.write_text(json.dumps({"version": 1, "phase": "e1", "queries": QUERIES}, indent=2))
    print(f"\nWritten to: {OUT_PATH}")


if __name__ == "__main__":
    main()

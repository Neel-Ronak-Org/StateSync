#!/usr/bin/env python3
"""
scripts/gen_multiturn_fixture.py
Builds fixtures/golden/multi_turn.json -- cycle 007, Workstream A.

12 conversations, 36 turns, over the olist schema (target_schema="olist"
must be set to run these live). Same safeguard discipline as
gen_olist_fixture.py: every ground_truth_sql is executed live before the
fixture is written, and a zero/null-row reference REFUSES the write rather
than shipping an unfailable question (the c2g_03/c2g_15 defect class this
project has hit twice before).

Per-turn SQL-generation difficulty is deliberately kept in the EASY range
(single-table or a 2-3 table join, matching gen_olist_fixture.py's own
profile, not gen_olist_hard_fixture.py's) so a QM failure on a REWRITTEN
question is attributable to the rewrite/multi-turn mechanism this fixture
exists to test, not confounded with ordinary SQL-generation difficulty this
project has already separately characterized (AGENT_HANDOFF.md §6: 88.9%
easy / 50.0% hard).

Reference types covered: anaphora, ellipsis, refinement (V2_STRATEGY §3's
own vocabulary). One conversation (mt_06) is cross-modal (docs turn -> sql
turn, §3a); one (mt_09) is ambiguous BY DESIGN, to exercise the guess-rate
mechanism (§3's cut-line: ship without clarification, record the guess);
one (mt_07) has a turn at index >= 2 that is explicitly context-free, to
test the acceptance criterion's second clause (not just turn 1).

mt_06 T1's docs ground truth (return window = 10 days for electronics) and
its expected_chunk_ids are the SAME fact already established and scored by
fixtures/golden/phase_e1.json's e1_doc_002 -- reused rather than reinvented,
so this fixture is not the first thing asserting it.

Usage:
    python3 scripts/gen_multiturn_fixture.py
    python3 scripts/gen_multiturn_fixture.py --check   # verify only, no write
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
import mcp_auth  # noqa: E402 -- scripts/mcp_auth.py: the MCP service token

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "fixtures" / "golden" / "multi_turn.json"
MCP = "http://localhost:8080/rpc"

SELLER_HI = "6560211a19b47992c3666cc44a7e94c0"    # 1,854 orders -- highest-volume seller
SELLER_HI2 = "4a3ca9315b744ce9f8e9374361493884"    # 1,806 orders -- second-highest


def run(sql: str) -> dict:
    req = urllib.request.Request(
        MCP,
        data=json.dumps({"jsonrpc": "2.0", "id": "1", "method": "data.run_select",
                         "params": {"sql": sql}}).encode(),
        headers={"Content-Type": "application/json",
                 "X-Tenant-ID": "t_capstone_default", "Authorization": mcp_auth.header(),
                 "X-Session-ID": "gen-multiturn-fixture"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read()).get("result", {})


def _conversations() -> list[dict]:
    return [
        {"conversation_id": "mt_01", "turns": [
            {"text": f"How many orders included an item from seller {SELLER_HI}?",
             "needs_context": False, "reference_type": None,
             "ground_truth_sql": f"SELECT COUNT(DISTINCT order_id) n FROM olist.order_items WHERE seller_id='{SELLER_HI}'"},
            {"text": "Just the delivered ones?", "needs_context": True, "reference_type": "refinement",
             "expected_standalone_facts": ["seller", SELLER_HI, "delivered"],
             "ground_truth_sql": f"""SELECT COUNT(DISTINCT oi.order_id) n FROM olist.order_items oi
                JOIN olist.orders o ON o.order_id=oi.order_id
                WHERE oi.seller_id='{SELLER_HI}' AND o.order_status='delivered'"""},
            {"text": "And how many of those were paid by credit card?", "needs_context": True, "reference_type": "ellipsis",
             "expected_standalone_facts": ["seller", SELLER_HI, "delivered", "credit card"],
             "ground_truth_sql": f"""SELECT COUNT(DISTINCT oi.order_id) n FROM olist.order_items oi
                JOIN olist.orders o ON o.order_id=oi.order_id
                JOIN olist.order_payments p ON p.order_id=o.order_id
                WHERE oi.seller_id='{SELLER_HI}' AND o.order_status='delivered' AND p.payment_type='credit_card'"""},
        ]},
        {"conversation_id": "mt_02", "turns": [
            {"text": "How many orders were paid using credit card?", "needs_context": False, "reference_type": None,
             "ground_truth_sql": "SELECT COUNT(DISTINCT order_id) n FROM olist.order_payments WHERE payment_type='credit_card'"},
            {"text": "And how many of those had more than 6 installments?", "needs_context": True, "reference_type": "ellipsis",
             "expected_standalone_facts": ["credit card", "more than 6 installments"],
             "ground_truth_sql": "SELECT COUNT(DISTINCT order_id) n FROM olist.order_payments WHERE payment_type='credit_card' AND payment_installments > 6"},
            {"text": "Just the ones with a payment value over 200?", "needs_context": True, "reference_type": "refinement",
             "expected_standalone_facts": ["credit card", "more than 6 installments", "over 200"],
             "ground_truth_sql": "SELECT COUNT(DISTINCT order_id) n FROM olist.order_payments WHERE payment_type='credit_card' AND payment_installments > 6 AND payment_value > 200"},
        ]},
        {"conversation_id": "mt_03", "turns": [
            {"text": f"How many distinct products has seller {SELLER_HI} sold?",
             "needs_context": False, "reference_type": None,
             "ground_truth_sql": f"SELECT COUNT(DISTINCT product_id) n FROM olist.order_items WHERE seller_id='{SELLER_HI}'"},
            {"text": "And how many reviews has it received?", "needs_context": True, "reference_type": "anaphora",
             "expected_standalone_facts": ["seller", SELLER_HI, "reviews"],
             "ground_truth_sql": f"""SELECT COUNT(*) n FROM olist.order_reviews r
                WHERE r.order_id IN (SELECT order_id FROM olist.order_items WHERE seller_id='{SELLER_HI}')"""},
            {"text": "How many of those were 5-star?", "needs_context": True, "reference_type": "refinement",
             "expected_standalone_facts": ["seller", SELLER_HI, "reviews", "5-star"],
             "ground_truth_sql": f"""SELECT COUNT(*) n FROM olist.order_reviews r
                WHERE r.order_id IN (SELECT order_id FROM olist.order_items WHERE seller_id='{SELLER_HI}') AND r.review_score = 5"""},
        ]},
        {"conversation_id": "mt_04", "turns": [
            {"text": "How many orders came from customers in Sao Paulo state (SP)?",
             "needs_context": False, "reference_type": None,
             "ground_truth_sql": """SELECT COUNT(DISTINCT o.order_id) n FROM olist.orders o
                JOIN olist.customers c ON c.customer_id=o.customer_id WHERE c.customer_state='SP'"""},
            {"text": "And from Rio de Janeiro (RJ)?", "needs_context": True, "reference_type": "ellipsis",
             "expected_standalone_facts": ["orders", "Rio de Janeiro", "RJ"],
             "ground_truth_sql": """SELECT COUNT(DISTINCT o.order_id) n FROM olist.orders o
                JOIN olist.customers c ON c.customer_id=o.customer_id WHERE c.customer_state='RJ'"""},
            {"text": "What is the average payment value for the RJ orders?", "needs_context": True, "reference_type": "anaphora",
             "expected_standalone_facts": ["average payment value", "RJ"],
             "ground_truth_sql": """SELECT ROUND(AVG(p.payment_value),2) n FROM olist.orders o
                JOIN olist.customers c ON c.customer_id=o.customer_id
                JOIN olist.order_payments p ON p.order_id=o.order_id WHERE c.customer_state='RJ'"""},
        ]},
        {"conversation_id": "mt_05", "turns": [
            {"text": "How many orders include an item in the bed_bath_table category?",
             "needs_context": False, "reference_type": None,
             "ground_truth_sql": """SELECT COUNT(DISTINCT oi.order_id) n FROM olist.order_items oi
                JOIN olist.products p ON p.product_id=oi.product_id
                JOIN olist.product_category_name_translation t ON t.product_category_name=p.product_category_name
                WHERE t.product_category_name_english='bed_bath_table'"""},
            {"text": "How many of those got a 5-star review?", "needs_context": True, "reference_type": "refinement",
             "expected_standalone_facts": ["bed_bath_table", "5-star"],
             "ground_truth_sql": """SELECT COUNT(DISTINCT oi.order_id) n FROM olist.order_items oi
                JOIN olist.products p ON p.product_id=oi.product_id
                JOIN olist.product_category_name_translation t ON t.product_category_name=p.product_category_name
                JOIN olist.order_reviews r ON r.order_id=oi.order_id
                WHERE t.product_category_name_english='bed_bath_table' AND r.review_score=5"""},
            {"text": "And how many got a 1-star review?", "needs_context": True, "reference_type": "ellipsis",
             "expected_standalone_facts": ["bed_bath_table", "1-star"],
             "ground_truth_sql": """SELECT COUNT(DISTINCT oi.order_id) n FROM olist.order_items oi
                JOIN olist.products p ON p.product_id=oi.product_id
                JOIN olist.product_category_name_translation t ON t.product_category_name=p.product_category_name
                JOIN olist.order_reviews r ON r.order_id=oi.order_id
                WHERE t.product_category_name_english='bed_bath_table' AND r.review_score=1"""},
        ]},
        {"conversation_id": "mt_06", "turns": [
            {"text": "What is the ONDC network's return window in days for the electronics category, per the returns policy?",
             "needs_context": False, "reference_type": None, "expected_route": "docs",
             "expected_chunk_ids": ["doc_policy_returns_refunds_c000", "doc_policy_returns_refunds_c001"],
             "ground_truth_sql": None, "ground_truth_value": "10"},
            {"text": "How many electronics orders took longer than that to be delivered after purchase?",
             "needs_context": True, "reference_type": "ellipsis", "cross_modal": True,
             "expected_standalone_facts": ["electronics", "10 days", "delivered", "purchase"],
             "ground_truth_sql": """SELECT COUNT(DISTINCT o.order_id) n FROM olist.orders o
                JOIN olist.order_items oi ON oi.order_id=o.order_id
                JOIN olist.products p ON p.product_id=oi.product_id
                JOIN olist.product_category_name_translation t ON t.product_category_name=p.product_category_name
                WHERE t.product_category_name_english='electronics' AND o.order_delivered_customer_date IS NOT NULL
                  AND o.order_delivered_customer_date - o.order_purchase_timestamp > INTERVAL '10 days'"""},
            {"text": "Just the ones where the customer is in Sao Paulo state?", "needs_context": True, "reference_type": "refinement",
             "expected_standalone_facts": ["electronics", "10 days", "delivered", "Sao Paulo"],
             "ground_truth_sql": """SELECT COUNT(DISTINCT o.order_id) n FROM olist.orders o
                JOIN olist.order_items oi ON oi.order_id=o.order_id
                JOIN olist.products p ON p.product_id=oi.product_id
                JOIN olist.product_category_name_translation t ON t.product_category_name=p.product_category_name
                JOIN olist.customers c ON c.customer_id=o.customer_id
                WHERE t.product_category_name_english='electronics' AND o.order_delivered_customer_date IS NOT NULL
                  AND o.order_delivered_customer_date - o.order_purchase_timestamp > INTERVAL '10 days'
                  AND c.customer_state='SP'"""},
        ]},
        {"conversation_id": "mt_07", "turns": [
            {"text": "How many orders were paid using boleto?", "needs_context": False, "reference_type": None,
             "ground_truth_sql": "SELECT COUNT(DISTINCT order_id) n FROM olist.order_payments WHERE payment_type='boleto'"},
            {"text": "How many sellers are there in total?", "needs_context": False, "reference_type": None,
             "note": "Deliberately self-contained despite being turn 2 -- tests the acceptance criterion's second clause.",
             "ground_truth_sql": "SELECT COUNT(*) n FROM olist.sellers"},
            {"text": "And how many of them have sold over 500 orders' worth of items?", "needs_context": True, "reference_type": "ellipsis",
             "expected_standalone_facts": ["sellers", "over 500 orders"],
             "ground_truth_sql": """SELECT COUNT(*) n FROM (
                SELECT seller_id FROM olist.order_items GROUP BY seller_id HAVING COUNT(DISTINCT order_id) > 500
                ) x"""},
        ]},
        {"conversation_id": "mt_08", "turns": [
            {"text": "What is the average review score across all orders?", "needs_context": False, "reference_type": None,
             "ground_truth_sql": "SELECT ROUND(AVG(review_score),2) n FROM olist.order_reviews"},
            {"text": "What about just for the sports_leisure category?", "needs_context": True, "reference_type": "refinement",
             "expected_standalone_facts": ["average review score", "sports_leisure"],
             "ground_truth_sql": """SELECT ROUND(AVG(r.review_score),2) n FROM olist.order_reviews r
                JOIN olist.order_items oi ON oi.order_id=r.order_id
                JOIN olist.products p ON p.product_id=oi.product_id
                JOIN olist.product_category_name_translation t ON t.product_category_name=p.product_category_name
                WHERE t.product_category_name_english='sports_leisure'"""},
            {"text": "How many reviews is that based on?", "needs_context": True, "reference_type": "anaphora",
             "expected_standalone_facts": ["sports_leisure", "reviews"],
             "ground_truth_sql": """SELECT COUNT(DISTINCT r.review_id) n FROM olist.order_reviews r
                JOIN olist.order_items oi ON oi.order_id=r.order_id
                JOIN olist.products p ON p.product_id=oi.product_id
                JOIN olist.product_category_name_translation t ON t.product_category_name=p.product_category_name
                WHERE t.product_category_name_english='sports_leisure'"""},
        ]},
        {"conversation_id": "mt_09", "turns": [
            {"text": f"How many orders include an item from seller {SELLER_HI}, and how many from seller {SELLER_HI2}?",
             "needs_context": False, "reference_type": None,
             "ground_truth_sql": f"""SELECT
                (SELECT COUNT(DISTINCT order_id) FROM olist.order_items WHERE seller_id='{SELLER_HI}') a,
                (SELECT COUNT(DISTINCT order_id) FROM olist.order_items WHERE seller_id='{SELLER_HI2}') b"""},
            {"text": "How many of their orders were delivered after the estimated delivery date?",
             "needs_context": True, "reference_type": "anaphora", "ambiguous_by_design": True,
             "expected_standalone_facts": ["seller", SELLER_HI, SELLER_HI2, "estimated delivery date"],
             "note": "'their' spans two named sellers -- correctly resolvable only as BOTH combined; "
                     "scored leniently, guess-rate tracked separately.",
             "ground_truth_sql": f"""SELECT COUNT(DISTINCT o.order_id) n FROM olist.orders o
                JOIN olist.order_items oi ON oi.order_id=o.order_id
                WHERE oi.seller_id IN ('{SELLER_HI}','{SELLER_HI2}')
                  AND o.order_delivered_customer_date IS NOT NULL AND o.order_estimated_delivery_date IS NOT NULL
                  AND o.order_delivered_customer_date > o.order_estimated_delivery_date"""},
            {"text": "And how many were delivered early or on time?", "needs_context": True, "reference_type": "ellipsis",
             "expected_standalone_facts": ["seller", SELLER_HI, SELLER_HI2, "on time"],
             "ground_truth_sql": f"""SELECT COUNT(DISTINCT o.order_id) n FROM olist.orders o
                JOIN olist.order_items oi ON oi.order_id=o.order_id
                WHERE oi.seller_id IN ('{SELLER_HI}','{SELLER_HI2}')
                  AND o.order_delivered_customer_date IS NOT NULL AND o.order_estimated_delivery_date IS NOT NULL
                  AND o.order_delivered_customer_date <= o.order_estimated_delivery_date"""},
        ]},
        {"conversation_id": "mt_10", "turns": [
            {"text": "What is the average freight value across all order items?", "needs_context": False, "reference_type": None,
             "ground_truth_sql": "SELECT ROUND(AVG(freight_value),2) n FROM olist.order_items"},
            {"text": "Just for items priced over 100?", "needs_context": True, "reference_type": "refinement",
             "expected_standalone_facts": ["average freight value", "over 100"],
             "ground_truth_sql": "SELECT ROUND(AVG(freight_value),2) n FROM olist.order_items WHERE price > 100"},
            {"text": "And for items priced 100 or under?", "needs_context": True, "reference_type": "ellipsis",
             "expected_standalone_facts": ["average freight value", "100 or under"],
             "ground_truth_sql": "SELECT ROUND(AVG(freight_value),2) n FROM olist.order_items WHERE price <= 100"},
        ]},
        {"conversation_id": "mt_11", "turns": [
            {"text": f"How many reviews has seller {SELLER_HI} received?",
             "needs_context": False, "reference_type": None,
             "ground_truth_sql": f"""SELECT COUNT(*) n FROM olist.order_reviews r
                WHERE r.order_id IN (SELECT order_id FROM olist.order_items WHERE seller_id='{SELLER_HI}')"""},
            {"text": "How many of those were 5 stars?", "needs_context": True, "reference_type": "refinement",
             "expected_standalone_facts": ["seller", SELLER_HI, "5 stars"],
             "ground_truth_sql": f"""SELECT COUNT(*) n FROM olist.order_reviews r
                WHERE r.order_id IN (SELECT order_id FROM olist.order_items WHERE seller_id='{SELLER_HI}') AND r.review_score=5"""},
            {"text": "And how many total reviews does that compare against?", "needs_context": True, "reference_type": "anaphora",
             "expected_standalone_facts": ["seller", SELLER_HI, "total reviews"],
             "ground_truth_sql": f"""SELECT COUNT(*) n FROM olist.order_reviews r
                WHERE r.order_id IN (SELECT order_id FROM olist.order_items WHERE seller_id='{SELLER_HI}')"""},
        ]},
        {"conversation_id": "mt_12", "turns": [
            {"text": "How many orders were paid in a single installment?", "needs_context": False, "reference_type": None,
             "ground_truth_sql": "SELECT COUNT(DISTINCT order_id) n FROM olist.order_payments WHERE payment_installments = 1"},
            {"text": "And how many in more than 6 installments?", "needs_context": True, "reference_type": "ellipsis",
             "expected_standalone_facts": ["orders", "more than 6 installments"],
             "ground_truth_sql": "SELECT COUNT(DISTINCT order_id) n FROM olist.order_payments WHERE payment_installments > 6"},
            {"text": "What's the average payment value for the second group?", "needs_context": True, "reference_type": "anaphora",
             "expected_standalone_facts": ["average payment value", "more than 6 installments"],
             "ground_truth_sql": "SELECT ROUND(AVG(payment_value),2) n FROM olist.order_payments WHERE payment_installments > 6"},
        ]},
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify ground truths only, do not write")
    args = ap.parse_args()

    conversations = _conversations()
    n_turns = sum(len(c["turns"]) for c in conversations)
    print(f"Verifying {len(conversations)} conversations, {n_turns} turns...\n")

    failures: list[str] = []
    for c in conversations:
        for i, t in enumerate(c["turns"], start=1):
            sql = t.get("ground_truth_sql")
            if sql is None:
                print(f"  [{c['conversation_id']} T{i}] docs turn, ground_truth_value={t.get('ground_truth_value')!r} -- not executed")
                continue
            r = run(sql)
            if not r.get("ok"):
                failures.append(f"{c['conversation_id']} T{i}: SQL FAILED -- {r.get('error')}")
                continue
            rows = r.get("rows") or []
            val = list(rows[0].values())[0] if rows else None
            print(f"  [{c['conversation_id']} T{i}] -> {rows[0] if rows else None}")
            if not rows or val in (0, None):
                failures.append(f"{c['conversation_id']} T{i}: zero/null-row reference -- trivially passable, rejected")

    if failures:
        print("\nREFUSING TO WRITE THE FIXTURE:")
        for f in failures:
            print(f"  {f}")
        return 1

    if args.check:
        print("\n--check: all ground truths verified, not writing.")
        return 0

    for c in conversations:
        for i, t in enumerate(c["turns"], start=1):
            t["turn_id"] = f"{c['conversation_id']}_t{i}"
            t["turn_index"] = i
            t.setdefault("expected_route", "sql")
            t.setdefault("cross_modal", False)
            t.setdefault("ambiguous_by_design", False)
            t.setdefault("expected_standalone_facts", None)
            t.setdefault("expected_chunk_ids", None)
            t.setdefault("ground_truth_value", None)
            t.setdefault("note", "")
            c["turns"][i - 1] = {
                k: t.get(k) for k in [
                    "turn_id", "turn_index", "text", "needs_context", "reference_type",
                    "expected_route", "cross_modal", "ambiguous_by_design",
                    "expected_standalone_facts", "ground_truth_sql", "ground_truth_value",
                    "expected_chunk_ids", "note",
                ]
            }

    OUT.write_text(json.dumps({
        "_about": {
            "corpus": "olist schema -- real Brazilian e-commerce data, 1,451,414 rows",
            "licence": "CC BY-NC-SA 4.0 (non-commercial). See docs/limitations.md.",
            "authored": "2026-08-25 (cycle 007), scripts/gen_multiturn_fixture.py -- every SQL "
                        "ground truth executed live via data.run_select before commit; the one "
                        "docs-route ground truth (mt_06 T1) is the same value and same "
                        "expected_chunk_ids already established and scored by "
                        "fixtures/golden/phase_e1.json's e1_doc_002, reused rather than "
                        "re-invented.",
            "self_authored_warning": "Self-authored, same class of caveat as olist.json/"
                        "olist_hard.json's own _about.self_authored_warning.",
            "design_note": "Per-turn SQL-generation difficulty is deliberately kept in the EASY "
                        "range (single-table or a 2-3 table join, matching olist.json's own "
                        "profile, not olist_hard.json's) so a QM failure on a REWRITTEN question "
                        "is attributable to the rewrite/multi-turn mechanism this fixture exists "
                        "to test, not confounded with ordinary SQL-generation difficulty this "
                        "project has already separately characterized (AGENT_HANDOFF.md §6: "
                        "88.9% easy / 50.0% hard).",
            "n_conversations": len(conversations),
            "n_turns": n_turns,
            "reference_types_covered": ["anaphora", "ellipsis", "refinement"],
            "cross_modal_conversations": ["mt_06"],
            "ambiguous_by_design_conversations": ["mt_09"],
            "context_free_at_turn_2plus": ["mt_07 (turn 2)"],
        },
        "conversations": conversations,
    }, indent=2, ensure_ascii=False) + "\n")

    print(f"\n  wrote {OUT.relative_to(REPO_ROOT)}: {len(conversations)} conversations, {n_turns} turns")
    return 0


if __name__ == "__main__":
    sys.exit(main())

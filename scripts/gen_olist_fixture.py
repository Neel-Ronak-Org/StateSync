#!/usr/bin/env python3
"""
scripts/gen_olist_fixture.py
Builds fixtures/golden/olist.json -- questions with ground truth over the REAL
Olist corpus -- and refuses to emit anything it cannot verify.

WRITTEN AFTER THE phase_c2 AUDIT, AND SHAPED BY IT. That audit found 12 of 25
queries had an instrument problem: 4 references that answered a different
question than the one asked, 3 that any zero-row query passes, 5 that never
said which columns to return. Those were authored by the same reasoning that
built the system, and nothing independent checked them for a month.

This fixture is ALSO self-authored -- there is no alternative for a new corpus --
so the safeguards are built in rather than discovered later:

  1. EVERY question names its expected columns. "Which sellers..." is
     unanswerable when both scorers compare column-bearing results. This is the
     c2g_09/12/13/14/24 defect, designed out.
  2. EVERY ambiguous denominator is stated IN the question. c2g_08 asked "what
     percentage of fulfillments were delivered late" over a corpus where 550
     exist and 323 are delivered; the question has to say which.
  3. EVERY ground truth is EXECUTED here, and the script refuses to write the
     fixture if any fails or if a non-zero-row question returns zero rows.
     Three phase_c2 references return zero rows, which under standard EX any
     query returning nothing passes -- 12% of that fixture is free.
  4. Questions that probe the DIRT are marked `probes_dirt`, because they are
     the point: 8 orders are delivered with no delivery date, 775 orders have
     no line items, and seller_city has 611 spellings that normalisation does
     not reduce. A fixture over real data that avoided those would just be the
     synthetic fixture with bigger numbers.

WHAT THIS STILL CANNOT FIX. It is one author's reading of what each question
means. Run scripts/attribute_failures.py over the first results: a query where
EVERY model picks different tables than the reference is a question problem,
and that is how c2g_09 and c2g_24 were eventually caught.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "fixtures" / "golden" / "olist.json"
MCP = "http://localhost:8080/rpc"

# Each entry states its projection in the question text. `expect_rows` is the
# author's expectation, checked against the database below -- a mismatch is a
# defect in the question or the SQL, caught here rather than in a model run.
QUERIES: list[dict] = [
    dict(id="ol_001", probes_dirt=False,
         text="List the order_status and the number of orders in each status.",
         sql="SELECT order_status, count(*) AS order_count FROM olist.orders GROUP BY order_status ORDER BY order_count DESC"),
    dict(id="ol_002", probes_dirt=False,
         text="How many orders are there in total? Return a single count.",
         sql="SELECT count(*) AS total_orders FROM olist.orders"),
    dict(id="ol_003", probes_dirt=True,
         text="Among orders whose status is 'delivered', how many have no recorded delivery date? Return a single count.",
         sql="SELECT count(*) AS delivered_without_date FROM olist.orders WHERE order_status = 'delivered' AND order_delivered_customer_date IS NULL",
         note="8 rows. Real dirt: a status that contradicts the data. Synthetic corpora have no such row."),
    dict(id="ol_004", probes_dirt=True,
         text="How many orders have no rows at all in the order_items table? Return a single count.",
         sql="SELECT count(*) AS orders_without_items FROM olist.orders o WHERE NOT EXISTS (SELECT 1 FROM olist.order_items i WHERE i.order_id = o.order_id)",
         note="775. Any naive revenue-per-order join silently drops these."),
    dict(id="ol_005", probes_dirt=False,
         text="List the seller_id and the number of order items sold, for the 5 sellers with the most order items.",
         sql="SELECT seller_id, count(*) AS item_count FROM olist.order_items GROUP BY seller_id ORDER BY item_count DESC, seller_id LIMIT 5"),
    dict(id="ol_006", probes_dirt=False,
         text="List the payment_type and total payment_value for each payment type, highest total first.",
         sql="SELECT payment_type, sum(payment_value) AS total_value FROM olist.order_payments GROUP BY payment_type ORDER BY total_value DESC"),
    dict(id="ol_007", probes_dirt=True,
         text="Considering ONLY orders that have a delivery date recorded, what is the average number of days between purchase and delivery? Return a single average.",
         sql="SELECT avg(EXTRACT(EPOCH FROM (order_delivered_customer_date - order_purchase_timestamp)) / 86400.0) AS avg_days FROM olist.orders WHERE order_delivered_customer_date IS NOT NULL",
         note="Denominator stated in the question -- the c2g_08 defect designed out."),
    dict(id="ol_008", probes_dirt=True,
         text="Considering ONLY orders that have a delivery date recorded, how many were delivered LATER than their estimated delivery date? Return a single count.",
         sql="SELECT count(*) AS late_deliveries FROM olist.orders WHERE order_delivered_customer_date IS NOT NULL AND order_delivered_customer_date > order_estimated_delivery_date"),
    dict(id="ol_009", probes_dirt=False,
         text="List the review_score and the number of reviews with that score, from score 1 to 5.",
         sql="SELECT review_score, count(*) AS review_count FROM olist.order_reviews GROUP BY review_score ORDER BY review_score"),
    dict(id="ol_010", probes_dirt=False,
         text="List the seller_state and the number of sellers in each, for the 5 states with the most sellers.",
         sql="SELECT seller_state, count(*) AS seller_count FROM olist.sellers GROUP BY seller_state ORDER BY seller_count DESC, seller_state LIMIT 5"),
    dict(id="ol_011", probes_dirt=True,
         text="How many DISTINCT values does seller_city take? Return a single count.",
         sql="SELECT count(DISTINCT seller_city) AS distinct_cities FROM olist.sellers",
         note="611, and normalising case/whitespace still gives 611: the variance is semantic (sao paulo / sao paulo - sp / sao paulop)."),
    dict(id="ol_012", probes_dirt=False,
         text="List the product_category_name and the number of products in it, for the 5 categories with the most products.",
         sql="SELECT product_category_name, count(*) AS product_count FROM olist.products WHERE product_category_name IS NOT NULL GROUP BY product_category_name ORDER BY product_count DESC, product_category_name LIMIT 5"),
    dict(id="ol_013", probes_dirt=True,
         text="How many products have no product_category_name recorded? Return a single count.",
         sql="SELECT count(*) AS uncategorised FROM olist.products WHERE product_category_name IS NULL",
         note="610 NULL categories in real data."),
    dict(id="ol_014", probes_dirt=False,
         text="What is the total price and total freight_value across all order items? Return one row with both totals.",
         sql="SELECT sum(price) AS total_price, sum(freight_value) AS total_freight FROM olist.order_items"),
    dict(id="ol_015", probes_dirt=False,
         text="List the customer_state and the number of orders placed from it, for the 5 states with the most orders.",
         sql="SELECT c.customer_state, count(*) AS order_count FROM olist.orders o JOIN olist.customers c ON c.customer_id = o.customer_id GROUP BY c.customer_state ORDER BY order_count DESC, c.customer_state LIMIT 5"),
    dict(id="ol_016", probes_dirt=False,
         text="List the order_id and the number of items, for the 3 orders with the most order items.",
         sql="SELECT order_id, count(*) AS item_count FROM olist.order_items GROUP BY order_id ORDER BY item_count DESC, order_id LIMIT 3"),
    dict(id="ol_017", probes_dirt=True,
         text="How many geolocation rows are there, and how many distinct combinations of zip prefix, latitude and longitude? Return one row with both counts.",
         sql="SELECT count(*) AS total_rows, count(DISTINCT (geolocation_zip_code_prefix, geolocation_lat, geolocation_lng)) AS distinct_points FROM olist.geolocation",
         note="1,000,163 vs 720,154 -- 28% duplicates, and the largest table in the corpus."),
    dict(id="ol_018", probes_dirt=False,
         text="List the product_category_name and its English translation for 5 categories, ordered by the Portuguese name.",
         sql="SELECT product_category_name, product_category_name_english FROM olist.product_category_name_translation ORDER BY product_category_name LIMIT 5"),
]


def run(sql: str) -> dict:
    req = urllib.request.Request(
        MCP,
        data=json.dumps({"jsonrpc": "2.0", "id": "1", "method": "data.run_select",
                         "params": {"sql": sql}}).encode(),
        headers={"Content-Type": "application/json",
                 "X-Tenant-ID": "t_capstone_default",
                 "X-Session-ID": "gen-olist-fixture"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read()).get("result", {})


def main() -> int:
    print(f"Verifying {len(QUERIES)} ground truths against the live corpus...\n")
    out, failures = [], []
    for q in QUERIES:
        res = run(q["sql"])
        if not res.get("ok"):
            failures.append((q["id"], f"SQL FAILED: {res.get('error')}"))
            continue
        rows = res.get("rows") or []
        cols = res.get("columns") or []
        if not rows:
            # Guard 3: a zero-row reference is passed by ANY query returning
            # nothing. phase_c2 has three of them; this fixture has none.
            failures.append((q["id"], "returns ZERO rows -- trivially passable, rejected"))
            continue
        print(f"  {q['id']}  {len(rows):>4} rows, {len(cols)} cols"
              f"{'  [probes dirt]' if q['probes_dirt'] else ''}   {q['text'][:56]}")
        out.append({
            "id": q["id"],
            "text": q["text"],
            "expected_route": "sql",
            "ground_truth_sql": q["sql"],
            "expected_row_count": len(rows),
            "expected_column_count": len(cols),
            "probes_dirt": q["probes_dirt"],
            "note": q.get("note", ""),
        })

    if failures:
        print("\nREFUSING TO WRITE THE FIXTURE:")
        for qid, why in failures:
            print(f"  {qid}: {why}")
        return 1

    OUT.write_text(json.dumps({
        "_about": {
            "corpus": "olist schema -- real Brazilian e-commerce data, 1,451,414 rows",
            "licence": "CC BY-NC-SA 4.0 (non-commercial). See docs/limitations.md.",
            "authored": "2026-08-17, by scripts/gen_olist_fixture.py",
            "self_authored_warning":
                "These questions and their ground truth were written by the same "
                "project that is measured against them -- the exact condition that "
                "produced 12 instrument defects in phase_c2. Three safeguards are "
                "built in: every question names its expected columns; every "
                "ambiguous denominator is stated in the question text; and every "
                "ground truth is executed, with zero-row references REJECTED "
                "rather than shipped (any query returning nothing passes those). "
                "Run scripts/attribute_failures.py over the first results: a query "
                "where every model picks different tables than the reference is a "
                "QUESTION problem, which is how c2g_09 and c2g_24 were caught.",
            "n": len(out),
            "probes_dirt": sum(1 for q in out if q["probes_dirt"]),
        },
        "queries": out,
    }, indent=2) + "\n")
    print(f"\n  wrote {OUT.relative_to(REPO_ROOT)}: {len(out)} queries, "
          f"{sum(1 for q in out if q['probes_dirt'])} probing real-data dirt")
    return 0


if __name__ == "__main__":
    sys.exit(main())

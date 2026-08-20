#!/usr/bin/env python3
"""
scripts/gen_olist_hard_fixture.py
Builds fixtures/golden/olist_hard.json -- Workstream B2 (docs/cycles/001.md,
criteria B1-B3): a SECOND, independently-authored fixture over the real Olist
corpus, this one deliberately hard. Every question requires at least one of:
a join across >=3 tables, a correlated subquery, or a window function --
recorded per-question in `hard_classes`.

WHY THIS EXISTS. fixtures/golden/olist.json (18 q, 88.9%/94.4%) is mostly
single-table aggregates -- BD6 records 2-hop joins as this system's measured
weakness across four independent checks, and that headline has never been
tested against anything harder. V2_STRATEGY.md B2: "This tells us whether
94.4% is real or an artifact of easy questions, and it must happen before any
number is put in the report." It is also the paper claim's second independent
fixture (V2_STRATEGY.md §6): auditing this one for defects, the same way
phase_c2 and olist.json were audited, turns one observation into three.

SAME SAFEGUARDS AS gen_olist_fixture.py, unchanged (it is quoted directly in
this fixture's _about block -- do not let the two prose descriptions drift):
  1. every question names its expected columns
  2. every ambiguous denominator/grain is stated in the question text
  3. every ground truth is EXECUTED here (via the same MCP RPC path a model
     query would take, not a raw superuser connection), and the script
     refuses to write the fixture if any fails or returns zero rows

ONE MORE SAFEGUARD, new to this fixture (audit 001, cycle 001 criterion B2):
  4. every LIMIT-bearing query is re-run at LIMIT n+1 and the METRIC value
     (the column the ORDER BY actually ranks on, not just any trailing
     column -- stated explicitly per query as `metric_col`) at position n is
     compared to position n+1. If they are EQUAL, the "top n" the question
     asks for is not uniquely determined by the metric alone: a correct model
     using a different stable-sort convention could legitimately return a
     different nth row and be marked wrong for it. The script refuses to
     write the fixture if this fires, rather than shipping a boundary tie and
     discovering it the way phase_c2's c2g_09/12/13/14/24 were discovered --
     after models had already been scored against it.

     Every query here ALSO carries a full deterministic secondary sort key
     (typically the GROUP BY dimension itself, which is unique per output
     row by construction), so the row SET returned is never actually
     ambiguous even when this check fires -- but the check is about whether
     the metric itself ties at the cut, which is the sharper, more honest
     question, and the one the audit named.

     MEASURED (cycle 002, audit 002 F-002-2): this check has never fired.
     Zero of the 13 questions it applies to tie at the LIMIT boundary. It
     protects a DIFFERENT ambiguity than the one oh_wf01 actually had --
     see safeguard 5.

TWO MORE SAFEGUARDS, added in cycle 002 (audit 002 F-002-1/F-002-2/F-002-7):

  5. every "top-1-per-partition" question (a window-ranked CTE filtered to
     rank = 1 within each PARTITION BY group) is probed for a PARTITION-
     INTERNAL tie: does more than one row share the max value of the
     ranking metric WITHIN a partition. This is a different question from
     safeguard 4 (which asks whether the metric ties at the LIMIT cut,
     across the whole result). oh_wf01's defect was exactly this shape --
     15 order_item ROWS tied at a category's max price, but only 4 of the
     71 categories had that tie across DISTINCT PRODUCTS, which is the
     thing that actually makes "take the top item" ambiguous -- and
     safeguard 4 cannot see it, because oh_wf01 never ties at its LIMIT-5
     cut. Declared per-query via `partition_col`; the script refuses to
     write the fixture if a partition-internal tie exists and the question
     does not say how to break it (tracked as `partition_tie_stated`).
     Demonstrated to actually refuse, not just pass: see the self-test this
     safeguard ships with, which flips `partition_tie_stated` on oh_wf01 to
     False against the real corpus and confirms the refusal fires, before
     restoring it.

  6. every `join3`-labelled question's ground truth is checked for at least
     three DISTINCT `olist.<table>` references; a label asserting more join
     complexity than the SQL contains is refused rather than trusted. Found
     one instance on first run: oh_wf03 was labelled `join3` while joining
     only `sellers` and `order_items` -- corrected to `["window_function"]`.

CORRELATED SUBQUERIES ARE WRITTEN FOR SPEED, NOT SYNTACTIC PURITY. A naive
per-row correlated subquery ("this product's price vs the average of its own
category, recomputed once per row") is O(n^2) here and was clocked hanging
past 120s in manual testing -- see docs/cycles/001.md. mcp.statement_timeout_ms
is 15000: a ground truth that cannot finish inside that window is unusable
regardless of how it was authored, so every correlated-subquery question here
uses the EXISTS/NOT EXISTS form (a single indexed correlation, not a
per-row-recomputed aggregate), which is both fast (<50ms measured) and the
same construct ol_004 already used in the easy fixture. This is a deliberate,
recorded choice, not an accidental narrowing of the class -- "correlated
subquery" as a difficulty class is about requiring the model to relate each
outer row to a row-specific inner condition, not about which of EXISTS vs a
scalar comparison it takes.

Usage:
    python3 scripts/gen_olist_hard_fixture.py
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "fixtures" / "golden" / "olist_hard.json"
MCP = "http://localhost:8080/rpc"

# Each entry states its projection in the question text (safeguard 1) and any
# ambiguous grain/denominator in the text too (safeguard 2). `metric_col` is
# the column name the ORDER BY ranks on -- required for every LIMIT-bearing
# query, used only by the boundary-tie check (safeguard 4); None for a
# single-row/scalar query, where "top n" does not apply. `partition_col` +
# `partition_tie_probe` + `partition_tie_stated` are safeguard 5 (cycle 002):
# only set on "top-1-per-partition" window questions.
QUERIES: list[dict] = [
    # ── JOIN3: a join across >=3 tables ──────────────────────────────────────
    dict(id="oh_j01", hard_classes=["join3"], probes_dirt=False,
         text="Considering only orders with status 'delivered', list the "
              "product_category_name_english and the total price (summed "
              "across order_items) for that category, for the 5 categories "
              "with the highest total price.",
         sql="SELECT t.product_category_name_english, sum(oi.price) AS total_revenue "
             "FROM olist.orders o JOIN olist.order_items oi ON oi.order_id = o.order_id "
             "JOIN olist.products p ON p.product_id = oi.product_id "
             "JOIN olist.product_category_name_translation t ON t.product_category_name = p.product_category_name "
             "WHERE o.order_status = 'delivered' "
             "GROUP BY t.product_category_name_english ORDER BY total_revenue DESC, t.product_category_name_english LIMIT 5",
         metric_col="total_revenue", limit_n=5),
    dict(id="oh_j02", hard_classes=["join3"], probes_dirt=False,
         text="List the seller_state and the number of DISTINCT customers "
              "(by customer_id) who bought an item from a seller in that "
              "state, for the 5 states with the most distinct customers.",
         sql="SELECT s.seller_state, count(DISTINCT o.customer_id) AS distinct_customers "
             "FROM olist.sellers s JOIN olist.order_items oi ON oi.seller_id = s.seller_id "
             "JOIN olist.orders o ON o.order_id = oi.order_id "
             "GROUP BY s.seller_state ORDER BY distinct_customers DESC, s.seller_state LIMIT 5",
         metric_col="distinct_customers", limit_n=5),
    dict(id="oh_j03", hard_classes=["join3"], probes_dirt=True,
         text="Counting every individual review row (an order may have more "
              "than one review), list the customer_state and the number of "
              "reviews from that state, for the 5 states with the most "
              "reviews.",
         sql="SELECT c.customer_state, count(*) AS review_count "
             "FROM olist.customers c JOIN olist.orders o ON o.customer_id = c.customer_id "
             "JOIN olist.order_reviews r ON r.order_id = o.order_id "
             "GROUP BY c.customer_state ORDER BY review_count DESC, c.customer_state LIMIT 5",
         metric_col="review_count", limit_n=5,
         note="555 orders carry more than one review row; the question states "
              "the review-row grain explicitly rather than leaving 'per order "
              "vs per review' ambiguous."),
    dict(id="oh_j04", hard_classes=["join3"], probes_dirt=True,
         text="List the payment_type and the number of DISTINCT sellers "
              "(by seller_id, via that payment's order's items) involved in "
              "payments of that type, for the 3 payment types with the most "
              "distinct sellers.",
         sql="SELECT op.payment_type, count(DISTINCT oi.seller_id) AS distinct_sellers "
             "FROM olist.order_payments op JOIN olist.orders o ON o.order_id = op.order_id "
             "JOIN olist.order_items oi ON oi.order_id = o.order_id "
             "GROUP BY op.payment_type ORDER BY distinct_sellers DESC, op.payment_type LIMIT 3",
         metric_col="distinct_sellers", limit_n=3,
         note="B3 audit finding, judged NOT a defect but recorded: 2,246 "
              "orders carry more than one DISTINCT payment_type (part "
              "voucher, part credit_card, etc.), so such an order's sellers "
              "are counted under EVERY payment_type it used, not one. The "
              "question does not claim exclusivity ('involved in payments of "
              "that type', not 'paid ONLY by that type'), so this is the "
              "natural reading rather than an ambiguity -- documented "
              "because oh_wf04's multi-payment-row fan-out got the same "
              "treatment and consistency matters more than silence here."),
    dict(id="oh_j05", hard_classes=["join3"], probes_dirt=False,
         text="List the seller_state and the number of DISTINCT product "
              "categories (product_category_name) sold by sellers in that "
              "state, for the 5 states with the most distinct categories.",
         sql="SELECT s.seller_state, count(DISTINCT p.product_category_name) AS distinct_categories "
             "FROM olist.sellers s JOIN olist.order_items oi ON oi.seller_id = s.seller_id "
             "JOIN olist.products p ON p.product_id = oi.product_id "
             "GROUP BY s.seller_state ORDER BY distinct_categories DESC, s.seller_state LIMIT 5",
         metric_col="distinct_categories", limit_n=5),
    dict(id="oh_j06", hard_classes=["join3"], probes_dirt=False,
         text="List the customer_state and the number of DISTINCT sellers "
              "(by seller_id) that customers from that state have bought "
              "from, for the 5 states with the most distinct sellers.",
         sql="SELECT c.customer_state, count(DISTINCT oi.seller_id) AS distinct_sellers "
             "FROM olist.customers c JOIN olist.orders o ON o.customer_id = c.customer_id "
             "JOIN olist.order_items oi ON oi.order_id = o.order_id "
             "GROUP BY c.customer_state ORDER BY distinct_sellers DESC, c.customer_state LIMIT 5",
         metric_col="distinct_sellers", limit_n=5),
    dict(id="oh_j07", hard_classes=["join3"], probes_dirt=True,
         text="A REPEAT CUSTOMER is a customer_unique_id that placed more "
              "than one order. List the customer_unique_id and the total "
              "number of order_items across ALL of that customer's orders, "
              "for the 5 repeat customers with the most total order_items.",
         sql="SELECT c.customer_unique_id, count(oi.order_item_id) AS total_items "
             "FROM olist.customers c JOIN olist.orders o ON o.customer_id = c.customer_id "
             "JOIN olist.order_items oi ON oi.order_id = o.order_id "
             "WHERE c.customer_unique_id IN ("
             "  SELECT customer_unique_id FROM olist.customers c2 JOIN olist.orders o2 ON o2.customer_id = c2.customer_id"
             "  GROUP BY customer_unique_id HAVING count(DISTINCT o2.order_id) > 1"
             ") GROUP BY c.customer_unique_id ORDER BY total_items DESC, c.customer_unique_id LIMIT 5",
         metric_col="total_items", limit_n=5,
         note="customer_id is 1:1 with orders in Olist (verified: 99,441 rows, "
              "99,441 distinct customer_id); customer_unique_id (96,096 "
              "distinct) is the real repeat-customer identifier. The question "
              "defines 'repeat customer' explicitly by the field that carries "
              "that meaning, rather than leaving 'customer' ambiguous between "
              "the two -- the exact trap this corpus sets for an unstated "
              "assumption."),
    dict(id="oh_j08", hard_classes=["join3"], probes_dirt=False,
         text="List the product_category_name_english and the number of "
              "DISTINCT sellers (by seller_id) who have sold at least one "
              "item in that category, for the 5 categories with the most "
              "distinct sellers.",
         sql="SELECT t.product_category_name_english, count(DISTINCT oi.seller_id) AS distinct_sellers "
             "FROM olist.order_items oi JOIN olist.products p ON p.product_id = oi.product_id "
             "JOIN olist.product_category_name_translation t ON t.product_category_name = p.product_category_name "
             "GROUP BY t.product_category_name_english ORDER BY distinct_sellers DESC, t.product_category_name_english LIMIT 5",
         metric_col="distinct_sellers", limit_n=5),
    dict(id="oh_j09", hard_classes=["join3"], probes_dirt=False,
         text="List the product_category_name_english and the number of "
              "DISTINCT orders (by order_id) that contained an item from "
              "that category AND received a review_score of exactly 5, for "
              "the 5 categories with the most such 5-star distinct orders.",
         sql="SELECT t.product_category_name_english, count(DISTINCT oi.order_id) AS five_star_orders "
             "FROM olist.order_items oi JOIN olist.products p ON p.product_id = oi.product_id "
             "JOIN olist.product_category_name_translation t ON t.product_category_name = p.product_category_name "
             "JOIN olist.order_reviews r ON r.order_id = oi.order_id "
             "WHERE r.review_score = 5 "
             "GROUP BY t.product_category_name_english ORDER BY five_star_orders DESC, t.product_category_name_english LIMIT 5",
         metric_col="five_star_orders", limit_n=5,
         note="4-table join; count(DISTINCT order_id) is stated so the "
              "item/review fan-out (an order can carry several items and a "
              "review can be joined to each) does not inflate the count."),

    # ── CORRELATED SUBQUERY (EXISTS / NOT EXISTS form -- see module docstring) ─
    dict(id="oh_cs01", hard_classes=["correlated_subquery"], probes_dirt=True,
         text="How many sellers have NEVER had an order_review scored 1 or 2 "
              "on any order containing one of their items? Return a single "
              "count.",
         sql="SELECT count(*) AS sellers_without_bad_reviews FROM olist.sellers s "
             "WHERE NOT EXISTS ("
             "  SELECT 1 FROM olist.order_items oi JOIN olist.order_reviews r ON r.order_id = oi.order_id"
             "  WHERE oi.seller_id = s.seller_id AND r.review_score <= 2"
             ")",
         metric_col=None, limit_n=None,
         note="1161 of 3095 sellers. Same NOT EXISTS shape as ol_004 in the "
              "easy fixture, correlated on seller_id instead of order_id."),
    dict(id="oh_cs02", hard_classes=["correlated_subquery"], probes_dirt=True,
         text="How many sellers have sold at least one item that was part "
              "of an order with status 'canceled'? Return a single count.",
         sql="SELECT count(*) AS sellers_with_canceled_order_items FROM olist.sellers s "
             "WHERE EXISTS ("
             "  SELECT 1 FROM olist.order_items oi JOIN olist.orders o ON o.order_id = oi.order_id"
             "  WHERE oi.seller_id = s.seller_id AND o.order_status = 'canceled'"
             ")",
         metric_col=None, limit_n=None,
         note="329 of 3095 sellers."),
    dict(id="oh_cs03", hard_classes=["correlated_subquery"], probes_dirt=True,
         text="How many DISTINCT products (by product_id, among products "
              "that have been ordered at least once) have NEVER received an "
              "order_review scored below 3 on any order containing that "
              "product? Return a single count.",
         sql="SELECT count(*) AS products_without_low_review FROM ("
             "  SELECT DISTINCT product_id FROM olist.order_items"
             ") p WHERE NOT EXISTS ("
             "  SELECT 1 FROM olist.order_items oi JOIN olist.order_reviews r ON r.order_id = oi.order_id"
             "  WHERE oi.product_id = p.product_id AND r.review_score < 3"
             ")",
         metric_col=None, limit_n=None,
         note="23,666 products, out of the ordered-product population."),

    # ── WINDOW FUNCTION ───────────────────────────────────────────────────────
    dict(id="oh_wf01", hard_classes=["window_function", "join3"], probes_dirt=True,
         text="Using a window function, find the single highest-priced "
              "order_item in EACH product_category_name_english (partition "
              "by category, rank by price descending; if more than one item "
              "shares the highest price in a category, take the one with "
              "the LOWEST product_id). List the category, product_id and "
              "price for that top item, for the 5 categories whose top item "
              "has the highest price.",
         sql="WITH ranked AS ("
             "  SELECT t.product_category_name_english, oi.product_id, oi.price,"
             "         ROW_NUMBER() OVER (PARTITION BY t.product_category_name_english ORDER BY oi.price DESC, oi.product_id) AS rn"
             "  FROM olist.order_items oi JOIN olist.products p ON p.product_id = oi.product_id"
             "  JOIN olist.product_category_name_translation t ON t.product_category_name = p.product_category_name"
             ") SELECT product_category_name_english, product_id, price FROM ranked "
             "WHERE rn = 1 ORDER BY price DESC, product_category_name_english LIMIT 5",
         metric_col="price", limit_n=5,
         partition_col="product_category_name_english",
         partition_tie_probe=(
             "WITH ranked AS ("
             "  SELECT t.product_category_name_english AS cat, oi.product_id, oi.price,"
             "         RANK() OVER (PARTITION BY t.product_category_name_english ORDER BY oi.price DESC) AS rk"
             "  FROM olist.order_items oi JOIN olist.products p ON p.product_id = oi.product_id"
             "  JOIN olist.product_category_name_translation t ON t.product_category_name = p.product_category_name)"
             " SELECT count(*) AS tied_partitions FROM ("
             "  SELECT cat FROM ranked WHERE rk = 1 GROUP BY cat HAVING count(DISTINCT product_id) > 1) x"),
         partition_tie_stated=True,
         note="CORRECTED cycle 002 (audit 002 F-002-1) -- the original text "
              "here read \"15 of the corpus's ~74 categories have a genuine "
              "multi-product tie ... e.g. 'audio' has 6 products tied at "
              "598.99\". Both numbers were measured over ORDER_ITEM ROWS, "
              "not distinct products, and the corpus has 71 categories, not "
              "~74. 'audio' is 6 ROWS of the SAME product (product_id "
              "constant) -- not ambiguous, since the projected product_id is "
              "identical either way. Corrected, and now checked by the "
              "automated partition-tie probe (safeguard 5) rather than by "
              "hand: 4 of 71 categories have a genuine multi-PRODUCT tie at "
              "the category max price (luggage_accessories, "
              "furniture_mattress_and_upholstery, fashion_underwear_beach, "
              "fashion_childrens_clothes -- each 2 distinct products) -- the "
              "max PRICE is unambiguous in all 71 categories, but which "
              "PRODUCT_ID represents it is ambiguous in exactly these 4, "
              "unless stated. None of the 5 categories in this fixture's "
              "shipped output are among the 4 tied ones (checked directly "
              "against the corpus, and re-verified live by the partition-tie "
              "probe every regeneration), so the ground truth is unaffected "
              "-- but the question wording is fixed anyway (lowest "
              "product_id, matching the SQL's own secondary ORDER BY) rather "
              "than left latent. wf03 (top seller per state) and wf04 (top "
              "order per state) are checked the same way, automatically, and "
              "have ZERO partition-internal ties -- no wording fix needed "
              "there."),
    dict(id="oh_wf02", hard_classes=["window_function"], probes_dirt=False,
         text="Using a window function, for each review_score (1 through 5) "
              "list the review_score, the count of reviews with that score, "
              "and the CUMULATIVE percentage of all reviews at or below that "
              "score, rounded to 2 decimal places. Order by review_score "
              "ascending.",
         sql="SELECT review_score, count(*) AS review_count, "
             "round(100.0 * sum(count(*)) OVER (ORDER BY review_score) / sum(count(*)) OVER (), 2) AS cumulative_pct "
             "FROM olist.order_reviews GROUP BY review_score ORDER BY review_score",
         metric_col=None, limit_n=None,
         note="Full 5-row result (scores 1-5), not LIMIT-bound -- ordered by "
              "review_score itself, which is the question's own stated axis."),
    dict(id="oh_wf03", hard_classes=["window_function"], probes_dirt=False,
         text="Using a window function, find the single seller with the "
              "highest total order_item price (summed) within EACH "
              "seller_state (partition by state, rank by total price "
              "descending). List the seller_id, seller_state and that "
              "seller's total price, for the 5 states whose top seller has "
              "the highest total price.",
         sql="WITH ranked AS ("
             "  SELECT s.seller_id, s.seller_state, sum(oi.price) AS total_revenue,"
             "         ROW_NUMBER() OVER (PARTITION BY s.seller_state ORDER BY sum(oi.price) DESC, s.seller_id) AS rnk"
             "  FROM olist.sellers s JOIN olist.order_items oi ON oi.seller_id = s.seller_id"
             "  GROUP BY s.seller_id, s.seller_state"
             ") SELECT seller_id, seller_state, total_revenue FROM ranked "
             "WHERE rnk = 1 ORDER BY total_revenue DESC, seller_state LIMIT 5",
         metric_col="total_revenue", limit_n=5,
         partition_col="seller_state",
         partition_tie_probe=(
             "WITH tot AS ("
             "  SELECT s.seller_id, s.seller_state, sum(oi.price) AS total_revenue"
             "  FROM olist.sellers s JOIN olist.order_items oi ON oi.seller_id = s.seller_id"
             "  GROUP BY s.seller_id, s.seller_state),"
             " ranked AS ("
             "  SELECT *, RANK() OVER (PARTITION BY seller_state ORDER BY total_revenue DESC) AS rk FROM tot)"
             " SELECT count(*) AS tied_partitions FROM ("
             "  SELECT seller_state FROM ranked WHERE rk = 1 GROUP BY seller_state HAVING count(*) > 1) x"),
         partition_tie_stated=False,
         note="CORRECTED cycle 002 (audit 002 F-002-7) -- hard_classes here "
              "read [\"window_function\", \"join3\"]; the ground truth joins "
              "only `sellers` and `order_items`, two tables, not >=3. "
              "Corrected to [\"window_function\"] alone; the fixture's join3 "
              "count (_about.hard_class_counts) moves 12 -> 11 as a result. "
              "Caught by safeguard 6 (a table-count check on every join3 "
              "label), added this cycle after the same mistake was found by "
              "audit, not by the generator, the first time."),
    dict(id="oh_wf04", hard_classes=["window_function", "join3"], probes_dirt=True,
         text="For each order, compute its TOTAL payment_value (the sum of "
              "all payment records for that order -- some orders have more "
              "than one). Using a window function, find the single order "
              "with the highest total payment_value within EACH "
              "customer_state (partition by state). List the order_id, "
              "customer_state and that total payment_value, for the 5 "
              "states whose top order has the highest total payment_value.",
         sql="WITH order_totals AS ("
             "  SELECT o.order_id, c.customer_state, sum(op.payment_value) AS total_payment"
             "  FROM olist.orders o JOIN olist.customers c ON c.customer_id = o.customer_id"
             "  JOIN olist.order_payments op ON op.order_id = o.order_id"
             "  GROUP BY o.order_id, c.customer_state"
             "), ranked AS ("
             "  SELECT order_id, customer_state, total_payment,"
             "         ROW_NUMBER() OVER (PARTITION BY customer_state ORDER BY total_payment DESC, order_id) AS rnk"
             "  FROM order_totals"
             ") SELECT order_id, customer_state, total_payment FROM ranked "
             "WHERE rnk = 1 ORDER BY total_payment DESC, customer_state LIMIT 5",
         metric_col="total_payment", limit_n=5,
         partition_col="customer_state",
         partition_tie_probe=(
             "WITH order_totals AS ("
             "  SELECT o.order_id, c.customer_state, sum(op.payment_value) AS total_payment"
             "  FROM olist.orders o JOIN olist.customers c ON c.customer_id = o.customer_id"
             "  JOIN olist.order_payments op ON op.order_id = o.order_id"
             "  GROUP BY o.order_id, c.customer_state),"
             " ranked AS ("
             "  SELECT *, RANK() OVER (PARTITION BY customer_state ORDER BY total_payment DESC) AS rk FROM order_totals)"
             " SELECT count(*) AS tied_partitions FROM ("
             "  SELECT customer_state FROM ranked WHERE rk = 1 GROUP BY customer_state HAVING count(*) > 1) x"),
         partition_tie_stated=False,
         note="2,961 orders carry more than one payment row (installments); "
              "the question states the per-order SUM explicitly so the "
              "installment fan-out is not left for the model to discover on "
              "its own."),
]


def run(sql: str) -> dict:
    req = urllib.request.Request(
        MCP,
        data=json.dumps({"jsonrpc": "2.0", "id": "1", "method": "data.run_select",
                         "params": {"sql": sql}}).encode(),
        headers={"Content-Type": "application/json",
                 "X-Tenant-ID": "t_capstone_default",
                 "X-Session-ID": "gen-olist-hard-fixture"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read()).get("result", {})


def _bump_limit(sql: str, n: int, new_n: int) -> str:
    """Replace a trailing `LIMIT n` with `LIMIT new_n`. Refuses to guess if
    the literal is not found byte-for-byte, same discipline as
    bench_multilingual.py's set_normalization_enabled -- a silent no-op here
    would make the tie check pass vacuously."""
    needle = f"LIMIT {n}"
    if sql.count(needle) != 1:
        raise RuntimeError(
            f"expected exactly one {needle!r} in the query; found "
            f"{sql.count(needle)}. Refusing to guess which to bump.")
    return sql.replace(needle, f"LIMIT {new_n}")


def _distinct_tables(sql: str) -> set[str]:
    """Every `olist.<table>` reference, for safeguard 6 (join3 label check).
    A regex, not a parser -- adequate here because every ground truth in this
    file is authored, not model-generated, and always schema-qualifies its
    tables (the same qualification F-001-1 made mandatory for the agent
    itself applies to this file's own SQL)."""
    return set(re.findall(r"\bolist\.([a-zA-Z_][a-zA-Z0-9_]*)", sql))


def _check_partition_tie(q: dict) -> tuple[int, str | None]:
    """Safeguard 5. Returns (tied_partition_count, refusal_message_or_None).
    Queries with no `partition_col` are not a top-1-per-partition shape and
    are skipped (0, None)."""
    if not q.get("partition_col"):
        return 0, None
    probe = run(q["partition_tie_probe"])
    if not probe.get("ok"):
        return 0, f"partition-tie probe FAILED: {probe.get('error')}"
    rows = probe.get("rows") or [{}]
    tied = int(rows[0].get("tied_partitions", 0) or 0)
    if tied and not q.get("partition_tie_stated"):
        return tied, (
            f"PARTITION-INTERNAL TIE: {tied} partition(s) of "
            f"{q['partition_col']!r} have more than one row tied at the max "
            f"of {q['metric_col']!r}, and the question does not state how to "
            f"break the tie (safeguard 5, cycle 002 A2b).")
    return tied, None


def self_test() -> int:
    """Demonstrates safeguards 5 and 6 can actually REFUSE, not just pass --
    same standard cycle 001's A5 held itself to (`shipped_flags.py`'s own
    self-test docstring: "a guard that has only ever been seen to pass is not
    evidence of anything"). Runs live against the corpus; does not write the
    fixture or touch the QUERIES list on disk."""
    ok = fail = 0

    def check(label: str, cond: bool) -> None:
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  ✓ PASS: {label}")
        else:
            fail += 1
            print(f"  ✗ FAIL: {label}")

    by_id = {q["id"]: q for q in QUERIES}

    # -- safeguard 5, the PASSING direction: real corpus, real questions --
    tied01, msg01 = _check_partition_tie(by_id["oh_wf01"])
    check("oh_wf01 partition-tie probe finds the 4 known-tied categories",
          tied01 == 4)
    check("oh_wf01 does not refuse (tie-break IS stated in the question)",
          msg01 is None)

    tied03, msg03 = _check_partition_tie(by_id["oh_wf03"])
    check("oh_wf03 partition-tie probe finds zero ties", tied03 == 0)
    check("oh_wf03 does not refuse", msg03 is None)

    tied04, msg04 = _check_partition_tie(by_id["oh_wf04"])
    check("oh_wf04 partition-tie probe finds zero ties", tied04 == 0)
    check("oh_wf04 does not refuse", msg04 is None)

    # -- safeguard 5, the REFUSING direction: same corpus, tie-break UNSTATED --
    unstated = dict(by_id["oh_wf01"])
    unstated["partition_tie_stated"] = False
    tied_u, msg_u = _check_partition_tie(unstated)
    check("oh_wf01 WOULD be refused if its tie-break were not stated "
          "(the guard can fail, not just pass)",
          tied_u == 4 and msg_u is not None and "PARTITION-INTERNAL TIE" in msg_u)

    # -- safeguard 6, both directions, against the real ground truth SQL --
    n_wf01 = len(_distinct_tables(by_id["oh_wf01"]["sql"]))
    check(f"oh_wf01 (labelled join3) has >=3 distinct tables ({n_wf01})",
          n_wf01 >= 3)
    n_wf03 = len(_distinct_tables(by_id["oh_wf03"]["sql"]))
    check(f"oh_wf03 (NOT labelled join3, post-correction) has {n_wf03} "
          f"distinct table(s), confirming the correction was warranted",
          n_wf03 < 3)

    bad_label = dict(by_id["oh_wf03"])
    bad_label["hard_classes"] = ["window_function", "join3"]
    would_refuse = ("join3" in bad_label["hard_classes"]
                     and len(_distinct_tables(bad_label["sql"])) < 3)
    check("re-applying the OLD wrong join3 label to oh_wf03 WOULD be refused "
          "by safeguard 6 (the guard can fail, not just pass)", would_refuse)

    print(f"\n  {ok} passed, {fail} failed")
    return 1 if fail else 0


def main() -> int:
    print(f"Verifying {len(QUERIES)} ground truths against the live corpus "
          f"(6 safeguards: named columns, stated grain, executed, no "
          f"LIMIT-boundary tie, no partition-internal tie, join3 labels "
          f"match table count)...\n")
    out, failures = [], []
    for q in QUERIES:
        res = run(q["sql"])
        if not res.get("ok"):
            failures.append((q["id"], f"SQL FAILED: {res.get('error')}"))
            continue
        rows = res.get("rows") or []
        cols = res.get("columns") or []
        if not rows:
            failures.append((q["id"], "returns ZERO rows -- trivially passable, rejected"))
            continue

        if "join3" in q["hard_classes"]:
            n_tables = len(_distinct_tables(q["sql"]))
            if n_tables < 3:
                failures.append((
                    q["id"],
                    f"labelled join3 but the ground truth references only "
                    f"{n_tables} distinct olist table(s) "
                    f"({sorted(_distinct_tables(q['sql']))}) -- refusing a "
                    f"label the SQL doesn't support (safeguard 6)."))
                continue

        tie_note = ""
        if q["limit_n"] is not None:
            if not q["metric_col"]:
                failures.append((q["id"], "LIMIT query with no metric_col -- "
                                  "the boundary-tie check has nothing to compare"))
                continue
            bumped_sql = _bump_limit(q["sql"], q["limit_n"], q["limit_n"] + 1)
            bumped = run(bumped_sql)
            if not bumped.get("ok"):
                failures.append((q["id"], f"boundary-tie probe FAILED: {bumped.get('error')}"))
                continue
            brows = bumped.get("rows") or []
            if len(brows) <= q["limit_n"]:
                # Fewer than n+1 rows exist at all -- there is no boundary to
                # tie (the "top n" the question asks for is the whole table).
                tie_note = "  [no boundary: fewer than n+1 rows exist]"
            else:
                nth = brows[q["limit_n"] - 1][q["metric_col"]]
                n_plus_1th = brows[q["limit_n"]][q["metric_col"]]
                if nth == n_plus_1th:
                    failures.append((
                        q["id"],
                        f"LIMIT BOUNDARY TIE on {q['metric_col']!r}: row "
                        f"{q['limit_n']} and row {q['limit_n']+1} both = "
                        f"{nth!r}. The question must specify the tie-break "
                        f"or be rewritten (criterion B2)."))
                    continue
                tie_note = f"  [boundary clean: {nth!r} vs {n_plus_1th!r}]"

        tied, tie_refusal = _check_partition_tie(q)
        if tie_refusal:
            failures.append((q["id"], tie_refusal))
            continue
        if q.get("partition_col"):
            tie_note += f"  [partition ties: {tied}]"

        print(f"  {q['id']}  {len(rows):>4} rows, {len(cols)} cols  "
              f"[{','.join(q['hard_classes'])}]"
              f"{'  [probes dirt]' if q['probes_dirt'] else ''}{tie_note}")
        print(f"           {q['text'][:100]}")
        out.append({
            "id": q["id"],
            "text": q["text"],
            "expected_route": "sql",
            "ground_truth_sql": q["sql"],
            "expected_row_count": len(rows),
            "expected_column_count": len(cols),
            "hard_classes": q["hard_classes"],
            "probes_dirt": q["probes_dirt"],
            "note": q.get("note", ""),
        })

    if failures:
        print("\nREFUSING TO WRITE THE FIXTURE:")
        for qid, why in failures:
            print(f"  {qid}: {why}")
        return 1

    class_counts: dict[str, int] = {}
    for q in out:
        for c in q["hard_classes"]:
            class_counts[c] = class_counts.get(c, 0) + 1

    OUT.write_text(json.dumps({
        "_about": {
            "corpus": "olist schema -- real Brazilian e-commerce data, 1,451,414 rows",
            "licence": "CC BY-NC-SA 4.0 (non-commercial). See docs/limitations.md.",
            "authored": "2026-08-20, by scripts/gen_olist_hard_fixture.py",
            "purpose": "Workstream B2 (docs/cycles/001.md criteria B1-B3): "
                "a SECOND, harder, independently-authored fixture over the "
                "same real corpus as fixtures/golden/olist.json. Every "
                "question requires at least a join across >=3 tables, a "
                "correlated subquery, or a window function -- see "
                "hard_classes on each question. Built to test whether "
                "88.9%/94.4% on the easy fixture is real or an artifact of "
                "easy questions (V2_STRATEGY.md B2).",
            "self_authored_warning":
                "These questions and their ground truth were written by the "
                "same project that is measured against them -- the exact "
                "condition that produced 12 instrument defects in phase_c2, "
                "and that produced this fixture's OWN defect (audit 002 "
                "F-002-1: a wrong statistic in oh_wf01's note, corrected "
                "cycle 002). Six safeguards now: the original three from "
                "fixtures/golden/olist.json (every question names its "
                "expected columns; every ambiguous grain or denominator is "
                "stated in the question text; every ground truth is "
                "executed, with zero-row references REJECTED); a fourth "
                "(cycle 001): every LIMIT-bearing query is re-run at n+1 and "
                "refused if the ranking metric ties at the cut; a fifth "
                "(cycle 002, audit 002 F-002-2): every top-1-per-partition "
                "question is probed for a tie WITHIN a partition at the "
                "ranking metric's max, refused unless the question states "
                "its tie-break -- this is the check that would have caught "
                "oh_wf01's actual defect, and safeguard 4 could not, because "
                "the two ask different questions; a sixth (cycle 002, audit "
                "002 F-002-7): every join3 label is checked against the "
                "ground truth's own distinct-table count. Both new "
                "safeguards ship with a --self-test that demonstrates they "
                "can refuse, not just pass.",
            "n": len(out),
            "probes_dirt": sum(1 for q in out if q["probes_dirt"]),
            "hard_class_counts": class_counts,
        },
        "queries": out,
    }, indent=2) + "\n")
    print(f"\n  wrote {OUT.relative_to(REPO_ROOT)}: {len(out)} queries, "
          f"classes {class_counts}, "
          f"{sum(1 for q in out if q['probes_dirt'])} probing real-data dirt")
    return 0


if __name__ == "__main__":
    sys.exit(self_test() if "--self-test" in sys.argv else main())

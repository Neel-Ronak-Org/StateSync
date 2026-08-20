-- migrations/009_revert_olist_join_graph.sql
-- Reverts migrations/008_olist_join_graph.sql in full: drops the 6 foreign
-- keys and the 4 primary keys 008 added, returning `olist` to exactly the
-- constraint-free state migrations/007 deliberately created.
--
-- WHY. 008 was applied as an EXPERIMENT (cycle 002, Workstream B) testing
-- whether declaring the join graph would fix the schema-grounding failures
-- audit 002 F-002-5 found. It was measured, paired, one variable, both arms
-- freshly run, and the result was NEGATIVE:
--
--   easy fixture (olist.json, n=18):      88.9% -> 77.8%   -11.1pp
--   hard fixture (olist_hard.json, n=16): 50.0% -> 50.0%   net 0
--
-- and neither of the two failures it targeted (oh_j08, oh_j09) was fixed --
-- oh_j09 in fact REGRESSED into exactly the failure mode the join graph was
-- hypothesised to prevent. Full write-up: docs/metrics-report.md § "Real-data
-- join graph".
--
-- THE PRECEDENT THIS FOLLOWS. a4293e1 (schema column comments) was reasoned
-- well, spot-checked well, MEASURED HARMFUL, and reverted by
-- migrations/006_revert_column_semantics.sql. This is the same shape and gets
-- the same treatment. The standing rule (docs/AGENT_HANDOFF.md §8, "measure
-- before and after every change, and report regressions") does not stop at
-- reporting: an intervention with no demonstrated benefit and a measured cost
-- does not ship.
--
-- THE CASE FOR REVERTING IS NOT NOISE-DEPENDENT. docs/cycles/002.md §5.2
-- records honestly that the -11.1pp could be model stochasticity -- one
-- paired run cannot separate a real effect from noise on the specific
-- queries that flipped. But "no demonstrated benefit" is certain regardless:
-- 008 definitively did not fix oh_j08 or oh_j09, which is the only thing it
-- was built to do. A change that fixed nothing, may have cost something, and
-- certainly makes the published 88.9%/50.0% non-reproducible has no positive
-- case at all.
--
-- WHAT IS NOT BEING CLAIMED. This does not establish that declaring foreign
-- keys is generally harmful, that a real deployment should omit them, or
-- that the seventh (dirty) edge -- products.product_category_name ->
-- product_category_name_translation, 13 orphans, never declared -- would
-- behave the same way. It was never tested. Nor does it settle the
-- PK-vs-FK question: 008 added both at once and docs/cycles/002.md §5.1
-- records that the two were never isolated, so which half (if either) caused
-- the regression is unknown. migrations/008 stays in the repo, unmodified
-- and re-appliable, for whoever picks that up.
--
-- NO DATA CHANGES. No row inserted, updated, or deleted. Constraints only.
--
-- Applied the same way 007 and 008 were:
--   docker compose exec -T postgres psql -U statesync_migrator -d statesync \
--     -f - < migrations/009_revert_olist_join_graph.sql

-- Foreign keys first -- a primary key cannot be dropped while an FK
-- references it.
ALTER TABLE olist.order_items    DROP CONSTRAINT order_items_order_id_fkey;
ALTER TABLE olist.order_items    DROP CONSTRAINT order_items_product_id_fkey;
ALTER TABLE olist.order_items    DROP CONSTRAINT order_items_seller_id_fkey;
ALTER TABLE olist.order_payments DROP CONSTRAINT order_payments_order_id_fkey;
ALTER TABLE olist.order_reviews  DROP CONSTRAINT order_reviews_order_id_fkey;
ALTER TABLE olist.orders         DROP CONSTRAINT orders_customer_id_fkey;

-- Then the primary keys.
ALTER TABLE olist.customers DROP CONSTRAINT customers_pkey;
ALTER TABLE olist.orders    DROP CONSTRAINT orders_pkey;
ALTER TABLE olist.products  DROP CONSTRAINT products_pkey;
ALTER TABLE olist.sellers   DROP CONSTRAINT sellers_pkey;

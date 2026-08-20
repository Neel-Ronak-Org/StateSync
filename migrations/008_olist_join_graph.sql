-- migrations/008_olist_join_graph.sql
-- Adds primary-key and foreign-key METADATA to the `olist` schema. Adds no
-- row, changes no value, cleans nothing. Cycle 002, docs/cycles/002.md
-- Workstream B (audit 002 F-002-10).
--
-- WHY THIS DEPARTS FROM migrations/007's RECORDED DECISION, AND WHY IT IS
-- STILL BEING DONE. migrations/007:31-36 declared no constraints on this
-- schema deliberately: "NO CONSTRAINTS, DELIBERATELY ... Declaring FKs would
-- either reject rows at load time or force cleaning -- and the DIRT IS THE
-- POINT." That reasoning is sound in general and this migration does not
-- reverse it -- it is a TRADEOFF under CYCLE_PROTOCOL.md §4.2, cited here
-- because the audit found evidence 007 did not have, and the owner decided
-- to act on it (docs/cycles/002.md §2, "The decision this plan needs from
-- the owner", 2026-08-20).
--
-- THE EVIDENCE 007 DID NOT HAVE. `orchestrator/schema_retriever.py`'s
-- DirectSchemaRetriever._format_table renders join information in the SQL
-- agent's prompt ONLY from declared foreign keys -- the ONLY source of join
-- information the prompt carries. `public` (the shipped synthetic schema)
-- declares 17 FK edges across 10 tables; `olist` declares zero, and zero
-- primary/unique keys either, so the model is not even shown which column
-- identifies a row. Every real-data accuracy number this project has
-- published (88.9%/94.4% on olist.json, 50.0%/100.0% on olist_hard.json) was
-- measured in that state, on fixtures where all but one question requires a
-- join. Two of the eight local-model hard-fixture failures (oh_j08, oh_j09)
-- invent a column on `products` rather than joining
-- `product_category_name_translation` -- exactly the edge a declared FK
-- would have pointed at. Full accounting: docs/limitations.md, "Real data:
-- what it is, and what its licence forbids"; docs/metrics-report.md, "Real-
-- data join graph".
--
-- MEASURED, NOT ASSUMED: of the seven natural edges in this schema, SIX have
-- ZERO orphaned rows and would validate today without rejecting or cleaning
-- anything (verified live against the loaded corpus, cycle 002 planning,
-- 2026-08-20):
--
--   orders.customer_id      -> customers.customer_id       0 orphans
--   order_items.order_id    -> orders.order_id             0 orphans
--   order_items.product_id  -> products.product_id         0 orphans
--   order_items.seller_id   -> sellers.seller_id           0 orphans
--   order_payments.order_id -> orders.order_id             0 orphans
--   order_reviews.order_id  -> orders.order_id              0 orphans
--
-- The dirt that motivated 007's blanket decision lives on exactly ONE edge:
--
--   products.product_category_name -> product_category_name_translation.product_category_name
--                                                           13 orphaned products
--
-- OWNER DECISION (docs/cycles/002.md, 2026-08-20): declare the six clean
-- edges. Leave the seventh UNDECLARED -- not NOT VALID -- so the dirty edge
-- stays dirty and stays VISIBLE (an undeclared edge is absent from the
-- prompt exactly as it is today; a NOT VALID constraint would still show up
-- in \d and in schema.describe_table's constraint listing, which is closer
-- to "declared but suppressed" than "not decided about"). Its absence is
-- also stated in docs/limitations.md and wherever this experiment's result
-- is published (docs/cycles/002.md B5), so the one edge the model is still
-- not shown is on record rather than silently missing.
--
-- NO DATA CHANGES. No row inserted, updated, or deleted. No CSV re-fetched.
-- No type changed (every FK column stays TEXT, matching its referenced
-- column's TEXT type -- both sides of every edge below were verified to
-- share type before writing this file). Every constraint below was verified
-- against the LIVE, LOADED corpus to be immediately valid -- this migration
-- adds no NOT VALID constraint and requires no follow-up VALIDATE CONSTRAINT
-- pass.
--
-- Applied the same way 007 was (scripts/fetch_olist.sh's own printed
-- instruction):
--   docker compose exec -T postgres psql -U statesync_migrator -d statesync \
--     -f - < migrations/008_olist_join_graph.sql

-- ── Primary keys: one per table an edge below references ───────────────────
-- Verified live before writing this migration: zero NULLs, zero duplicates,
-- on every column below, in its own table.

ALTER TABLE olist.customers ADD PRIMARY KEY (customer_id);
ALTER TABLE olist.orders    ADD PRIMARY KEY (order_id);
ALTER TABLE olist.products  ADD PRIMARY KEY (product_id);
ALTER TABLE olist.sellers   ADD PRIMARY KEY (seller_id);

-- ── Foreign keys: the six zero-orphan natural edges ─────────────────────────
-- No ON DELETE/ON UPDATE clause -- this schema is read-only end to end (the
-- reader role granted by 007 has no write privilege on it at all), so a
-- cascade action can never fire; the default (NO ACTION) is correct by
-- construction and adding a clause here would only document a scenario that
-- cannot occur.

ALTER TABLE olist.orders
  ADD FOREIGN KEY (customer_id) REFERENCES olist.customers (customer_id);

ALTER TABLE olist.order_items
  ADD FOREIGN KEY (order_id) REFERENCES olist.orders (order_id);

ALTER TABLE olist.order_items
  ADD FOREIGN KEY (product_id) REFERENCES olist.products (product_id);

ALTER TABLE olist.order_items
  ADD FOREIGN KEY (seller_id) REFERENCES olist.sellers (seller_id);

ALTER TABLE olist.order_payments
  ADD FOREIGN KEY (order_id) REFERENCES olist.orders (order_id);

ALTER TABLE olist.order_reviews
  ADD FOREIGN KEY (order_id) REFERENCES olist.orders (order_id);

-- ── The seventh edge: deliberately NOT declared ─────────────────────────────
-- products.product_category_name -> product_category_name_translation.product_category_name
-- 13 of the corpus's products reference a category name absent from the
-- translation table (verified live). Declaring this NOT VALID would still
-- surface it to schema.describe_table's constraint listing; the owner's
-- decision was to leave the dirty edge exactly as dirty and exactly as
-- invisible as it is today, matching 007's original "the dirt is the point"
-- reasoning for the one edge where that reasoning actually holds. No DDL
-- below for this pair -- this comment is the record of the decision, not a
-- placeholder for a future one.

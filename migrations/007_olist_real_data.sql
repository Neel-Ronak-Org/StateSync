-- migrations/007_olist_real_data.sql
-- Brazilian E-Commerce Public Dataset by Olist -- REAL transactional data,
-- ~1.45M rows, as a second corpus alongside the synthetic ONDC schema.
--
-- WHY A SEPARATE SCHEMA, and this is the load-bearing decision here.
-- These 9 tables do NOT go into `public`. A5 defines the schema fingerprint
-- over the tables in the application schema, and D4 validates that fingerprint
-- on every trajectory read -- so adding 9 tables to `public` would change the
-- fingerprint and invalidate every stored trajectory, exactly the blast radius
-- documented for adding tenant_id (docs/open_items.md, GAP-6). Real data is not
-- worth silently detonating the memory plane, and an isolated schema costs
-- nothing: `mcp.schema.list_tables` already filters nspname='public', so the
-- synthetic corpus and its fingerprint are untouched by construction.
--
-- Making the agent able to QUERY this schema is a deliberate, separate change
-- (schema scoping in mcp/tools/schema_tools.py + the SELECT-only gate's
-- allowlist). Loading the data and exposing it to the agent are two decisions,
-- and conflating them is how a fingerprint gets changed by accident.
--
-- PROVENANCE. Fetched by scripts/fetch_olist.sh from the mara/mara-olist-
-- ecommerce-data mirror, which documents its own source as the Kaggle dataset
-- and its licence. SHA-256 of every file is committed in
-- fixtures/olist_checksums.txt so the exact bytes behind any number are
-- reproducible; the CSVs themselves are gitignored (121MB).
--
-- LICENCE: CC BY-NC-SA 4.0 -- NON-COMMERCIAL. Usable for this capstone and for
-- benchmarking; NOT usable as the data behind a commercial deployment. That is
-- a real constraint on the "companies can use this" framing and is recorded in
-- docs/limitations.md rather than left for someone to discover later.
--
-- NO CONSTRAINTS, DELIBERATELY. Types are permissive and foreign keys are not
-- declared: this is real data and it is dirty (orphaned references, missing
-- delivery dates, inconsistent city spellings). Declaring FKs would either
-- reject rows at load time or force cleaning -- and the DIRT IS THE POINT.
-- A system that only works on referentially perfect data is the thing the
-- synthetic corpus already proves.

CREATE SCHEMA IF NOT EXISTS olist;

CREATE TABLE IF NOT EXISTS olist.customers (
  customer_id              TEXT,
  customer_unique_id       TEXT,
  customer_zip_code_prefix TEXT,
  customer_city            TEXT,
  customer_state           TEXT
);

CREATE TABLE IF NOT EXISTS olist.orders (
  order_id                      TEXT,
  customer_id                   TEXT,
  order_status                  TEXT,
  order_purchase_timestamp      TIMESTAMP,
  order_approved_at             TIMESTAMP,
  order_delivered_carrier_date  TIMESTAMP,
  order_delivered_customer_date TIMESTAMP,
  order_estimated_delivery_date TIMESTAMP
);

CREATE TABLE IF NOT EXISTS olist.order_items (
  order_id            TEXT,
  order_item_id       INTEGER,
  product_id          TEXT,
  seller_id           TEXT,
  shipping_limit_date TIMESTAMP,
  price               NUMERIC,
  freight_value       NUMERIC
);

CREATE TABLE IF NOT EXISTS olist.order_payments (
  order_id             TEXT,
  payment_sequential   INTEGER,
  payment_type         TEXT,
  payment_installments INTEGER,
  payment_value        NUMERIC
);

CREATE TABLE IF NOT EXISTS olist.order_reviews (
  review_id               TEXT,
  order_id                TEXT,
  review_score            INTEGER,
  review_comment_title    TEXT,
  review_comment_message  TEXT,
  review_creation_date    TIMESTAMP,
  review_answer_timestamp TIMESTAMP
);

CREATE TABLE IF NOT EXISTS olist.products (
  product_id                 TEXT,
  product_category_name      TEXT,
  product_name_lenght        INTEGER,   -- [sic] misspelled in the source data
  product_description_lenght INTEGER,   -- [sic] kept verbatim: renaming it
  product_photos_qty         INTEGER,   -- would be cleaning the data, and the
  product_weight_g           INTEGER,   -- fact that real columns are misspelled
  product_length_cm          INTEGER,   -- is itself a property worth testing
  product_height_cm          INTEGER,
  product_width_cm           INTEGER
);

CREATE TABLE IF NOT EXISTS olist.sellers (
  seller_id              TEXT,
  seller_zip_code_prefix TEXT,
  seller_city            TEXT,
  seller_state           TEXT
);

CREATE TABLE IF NOT EXISTS olist.geolocation (
  geolocation_zip_code_prefix TEXT,
  geolocation_lat             DOUBLE PRECISION,
  geolocation_lng             DOUBLE PRECISION,
  geolocation_city            TEXT,
  geolocation_state           TEXT
);

CREATE TABLE IF NOT EXISTS olist.product_category_name_translation (
  product_category_name         TEXT,
  product_category_name_english TEXT
);

-- Read-only access for the same reader role the rest of the system uses. The
-- three SELECT-only enforcement layers (A4.5 parser gate, role grant, statement
-- timeout) apply here unchanged -- the reader role simply has no write grant on
-- this schema either, which is the guarantee rather than any prompt rule.
GRANT USAGE ON SCHEMA olist TO statesync_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA olist TO statesync_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA olist GRANT SELECT ON TABLES TO statesync_reader;

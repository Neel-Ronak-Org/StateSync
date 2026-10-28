#!/usr/bin/env bash
# scripts/load_olist.sh
# Stages the verified CSVs into the postgres container and COPYs them into the
# `olist` schema. Idempotent: truncates first, so re-running cannot double-load.
#
# The row counts asserted below are the CANONICAL Olist figures. If a COPY
# produces a different number the load FAILS rather than leaving a partially
# populated corpus that every later number would silently be measured against.
#
# NOTE on order_reviews: the file has 105,759 PHYSICAL lines but 100,000
# records, because Portuguese review text contains embedded newlines inside
# quoted fields. `wc -l` is the wrong tool here and gave a wrong answer during
# development; COPY parses it correctly. The assertion below is on RECORDS.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

bash scripts/fetch_olist.sh >/dev/null   # refuses on checksum mismatch

echo "==> staging CSVs into the postgres container"
docker compose exec -T postgres mkdir -p /tmp/olist
for f in data/olist/*.csv; do docker cp "$f" postgres:/tmp/olist/ >/dev/null; done

echo "==> COPY into schema olist"
docker compose exec -T postgres psql -U statesync_migrator -d statesync -v ON_ERROR_STOP=1 <<'SQL'
TRUNCATE olist.customers, olist.orders, olist.order_items, olist.order_payments,
         olist.order_reviews, olist.products, olist.sellers, olist.geolocation,
         olist.product_category_name_translation;
\copy olist.customers FROM '/tmp/olist/olist_customers_dataset.csv' WITH (FORMAT csv, HEADER true);
\copy olist.orders FROM '/tmp/olist/olist_orders_dataset.csv' WITH (FORMAT csv, HEADER true);
\copy olist.order_items FROM '/tmp/olist/olist_order_items_dataset.csv' WITH (FORMAT csv, HEADER true);
\copy olist.order_payments FROM '/tmp/olist/olist_order_payments_dataset.csv' WITH (FORMAT csv, HEADER true);
\copy olist.order_reviews FROM '/tmp/olist/olist_order_reviews_dataset.csv' WITH (FORMAT csv, HEADER true);
\copy olist.products FROM '/tmp/olist/olist_products_dataset.csv' WITH (FORMAT csv, HEADER true);
\copy olist.sellers FROM '/tmp/olist/olist_sellers_dataset.csv' WITH (FORMAT csv, HEADER true);
\copy olist.geolocation FROM '/tmp/olist/olist_geolocation_dataset.csv' WITH (FORMAT csv, HEADER true);
\copy olist.product_category_name_translation FROM '/tmp/olist/product_category_name_translation.csv' WITH (FORMAT csv, HEADER true);
SQL

echo "==> asserting canonical row counts"
docker compose exec -T postgres psql -U statesync_migrator -d statesync -v ON_ERROR_STOP=1 -t <<'SQL'
DO $$
DECLARE expected CONSTANT jsonb := '{"customers":99441,"orders":99441,"order_items":112650,
  "order_payments":103886,"order_reviews":100000,"products":32951,"sellers":3095,
  "geolocation":1000163,"product_category_name_translation":71}';
  k text; want bigint; got bigint;
BEGIN
  FOR k IN SELECT jsonb_object_keys(expected) LOOP
    want := (expected ->> k)::bigint;
    EXECUTE format('SELECT count(*) FROM olist.%I', k) INTO got;
    IF got <> want THEN
      RAISE EXCEPTION 'olist.% has % rows, expected % -- partial load, refusing to continue', k, got, want;
    END IF;
  END LOOP;
  RAISE NOTICE 'All 9 tables match canonical Olist row counts (1,451,414 rows).';
END $$;
SQL
echo "==> Olist loaded and verified."

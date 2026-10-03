#!/usr/bin/env bash
# demo/local_shop_db.sh
# A small "shop" Postgres on this machine, to demo "connect your own database".
# It is NOT part of StateSync: a separate container, published on localhost:5433,
# with a read-only login for StateSync and a superuser for you.
#
#   ./demo/local_shop_db.sh          start (or restart) it with fresh data
#   ./demo/local_shop_db.sh stop     remove it
#
# In StateSync (Library -> Databases -> Add database):
#   Host host.docker.internal   Port 5433   Database shop   User statesync_reader
#   Password demo-reader-pass   SSL disable
# (These are demo-only values for a throwaway local database.)
set -euo pipefail
NAME=statesync-demo-shop
if [[ "${1:-}" == "stop" ]]; then docker rm -f "$NAME" >/dev/null 2>&1 && echo "removed $NAME"; exit 0; fi

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p 127.0.0.1:5433:5432 -e POSTGRES_PASSWORD=demo-owner-pass -e POSTGRES_DB=shop \
  postgres:16-alpine >/dev/null
echo -n "waiting for Postgres"
until docker exec "$NAME" pg_isready -U postgres -d shop >/dev/null 2>&1; do echo -n "."; sleep 1; done
sleep 2; echo

docker exec -i "$NAME" psql -q -v ON_ERROR_STOP=1 -U postgres -d shop <<'SQL'
CREATE SCHEMA sales;
CREATE TABLE sales.products (
  product_id  int PRIMARY KEY,
  name        text NOT NULL,
  category    text NOT NULL,
  unit_price  numeric(10,2) NOT NULL
);
CREATE TABLE sales.customers (
  customer_id int PRIMARY KEY,
  city        text NOT NULL,
  signup_date date NOT NULL
);
CREATE TABLE sales.orders (
  order_id    int PRIMARY KEY,
  customer_id int NOT NULL REFERENCES sales.customers,
  ordered_at  timestamptz NOT NULL,
  status      text NOT NULL CHECK (status IN ('delivered', 'shipped', 'cancelled', 'returned')),
  channel     text NOT NULL CHECK (channel IN ('web', 'app', 'store'))
);
CREATE TABLE sales.order_items (
  order_id    int NOT NULL REFERENCES sales.orders,
  product_id  int NOT NULL REFERENCES sales.products,
  quantity    int NOT NULL,
  unit_price  numeric(10,2) NOT NULL,
  PRIMARY KEY (order_id, product_id)
);
COMMENT ON TABLE sales.orders IS 'One row per customer order.';
COMMENT ON COLUMN sales.orders.status IS 'delivered, shipped, cancelled or returned';

INSERT INTO sales.products
SELECT g, (ARRAY['Oak','Walnut','Linen','Steel','Velvet','Rattan','Marble','Cedar'])[1 + g % 8] || ' ' ||
          (ARRAY['chair','table','lamp','sofa','shelf','rug','bed','desk'])[1 + (g / 8) % 8],
       (ARRAY['seating','tables','lighting','seating','storage','textiles','bedroom','office'])[1 + (g / 8) % 8],
       round((40 + (g * 37) % 900)::numeric, 2)
FROM generate_series(1, 64) g;

INSERT INTO sales.customers
SELECT g, (ARRAY['Pune','Mumbai','Delhi','Bengaluru','Chennai','Hyderabad','Kolkata'])[1 + (g * 7) % 7],
       current_date - (30 + g % 700)
FROM generate_series(1, 600) g;

INSERT INTO sales.orders
SELECT g, 1 + (g * 13) % 600,
       now() - make_interval(days => (g * 7) % 180, hours => g % 24),
       (ARRAY['delivered','delivered','delivered','delivered','shipped','cancelled','returned'])[1 + (g * 5) % 7],
       (ARRAY['web','app','app','store'])[1 + g % 4]
FROM generate_series(1, 4000) g;

INSERT INTO sales.order_items
SELECT o.order_id, 1 + ((o.order_id * k * 11) % 64), 1 + (o.order_id + k) % 3, p.unit_price
FROM sales.orders o
CROSS JOIN generate_series(1, 3) k
JOIN sales.products p ON p.product_id = 1 + ((o.order_id * k * 11) % 64)
WHERE k <= 1 + o.order_id % 3
ON CONFLICT DO NOTHING;

CREATE ROLE statesync_reader LOGIN PASSWORD 'demo-reader-pass';
GRANT CONNECT ON DATABASE shop TO statesync_reader;
GRANT USAGE ON SCHEMA sales TO statesync_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA sales TO statesync_reader;
SQL

docker exec "$NAME" psql -U postgres -d shop -Atc \
  "SELECT 'products '||(SELECT count(*) FROM sales.products)||', customers '||(SELECT count(*) FROM sales.customers)||', orders '||(SELECT count(*) FROM sales.orders)||', order items '||(SELECT count(*) FROM sales.order_items)"
echo "Ready: host.docker.internal:5433, database shop, user statesync_reader (read-only)."

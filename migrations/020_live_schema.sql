-- migrations/020_live_schema.sql
-- The live demo dataset (docs/V3_PLAN.md Phase 2b, decision D-v3-6).
--
-- WHY. A dashboard that refreshes every 30 s only means something if the data
-- moves. The two existing datasets must NOT move: olist is a fixed historical
-- corpus and `public` backs the accuracy fixtures. So the moving data gets its
-- own schema, fed by the `simulator` service (simulator/), which backfills 30
-- days and then appends orders, status changes, deliveries, reviews and web
-- traffic every few seconds.
--
-- TWO SCHEMAS.
--   live      what the product queries: an online shop's orders, deliveries,
--             reviews and 5-minute web-traffic buckets. The table and column
--             comments are what the SQL agent reads to understand it.
--   live_sim  the simulator's private plan (when each order will be packed,
--             shipped, delivered, reviewed). Not granted to the reader role, so
--             the model can never see the future.
--
-- ROLES.
--   statesync_simulator (new, LOGIN): SELECT/INSERT/UPDATE/DELETE on live and
--     live_sim only. It has no grant anywhere else, so it cannot read or write
--     public, olist or session_state. Its password is set when this migration is
--     applied, from secrets/pg_simulator_password (never stored here).
--   statesync_reader: USAGE + SELECT on live (not live_sim), like olist (007).
--
-- The A5 fingerprint is unaffected (it hashes public only). Idempotent.

BEGIN;

CREATE SCHEMA IF NOT EXISTS live;
CREATE SCHEMA IF NOT EXISTS live_sim;

COMMENT ON SCHEMA live IS
  'Live demo data for an Indian online store, updated every few seconds by a simulator. '
  'Covers the last 30 days: orders, their deliveries, customer reviews, and website traffic '
  'in 5-minute buckets. All timestamps are timestamptz; amounts are in Indian rupees (INR).';

CREATE TABLE IF NOT EXISTS live.orders (
  order_id        BIGINT PRIMARY KEY,
  placed_at       TIMESTAMPTZ NOT NULL,
  customer_id     BIGINT NOT NULL,
  city            TEXT NOT NULL,
  state           TEXT NOT NULL,
  region          TEXT NOT NULL,
  category        TEXT NOT NULL,
  channel         TEXT NOT NULL,
  payment_method  TEXT NOT NULL,
  items           INTEGER NOT NULL,
  amount          NUMERIC(12, 2) NOT NULL,
  status          TEXT NOT NULL,
  updated_at      TIMESTAMPTZ NOT NULL,
  CONSTRAINT live_orders_status_ck CHECK (status IN ('placed', 'packed', 'shipped', 'delivered', 'cancelled', 'returned')),
  CONSTRAINT live_orders_items_ck CHECK (items > 0),
  CONSTRAINT live_orders_amount_ck CHECK (amount > 0)
);
CREATE INDEX IF NOT EXISTS live_orders_placed_ix ON live.orders (placed_at);
CREATE INDEX IF NOT EXISTS live_orders_status_ix ON live.orders (status);

COMMENT ON TABLE live.orders IS
  'One row per customer order. Revenue = sum(amount) over orders that were not cancelled. '
  'An order moves placed -> packed -> shipped -> delivered; some are cancelled before shipping '
  'or returned after delivery.';
COMMENT ON COLUMN live.orders.order_id IS 'Order identifier.';
COMMENT ON COLUMN live.orders.placed_at IS 'When the customer placed the order.';
COMMENT ON COLUMN live.orders.customer_id IS 'Customer identifier; the same customer can order many times.';
COMMENT ON COLUMN live.orders.city IS 'Delivery city, e.g. Mumbai, Delhi, Bengaluru.';
COMMENT ON COLUMN live.orders.state IS 'Indian state of the delivery city, e.g. Maharashtra, Karnataka.';
COMMENT ON COLUMN live.orders.region IS 'Region of India: North, South, East, West or Central.';
COMMENT ON COLUMN live.orders.category IS
  'Product category: Electronics, Fashion, Grocery, Home & Kitchen, Beauty, Sports, Books or Toys.';
COMMENT ON COLUMN live.orders.channel IS
  'Sales channel that brought the order: Direct, Marketplace, Organic search, Affiliate or Social.';
COMMENT ON COLUMN live.orders.payment_method IS 'UPI, Card, Cash on delivery, Net banking or Wallet.';
COMMENT ON COLUMN live.orders.items IS 'Number of items in the order.';
COMMENT ON COLUMN live.orders.amount IS 'Order value in Indian rupees (INR).';
COMMENT ON COLUMN live.orders.status IS 'placed, packed, shipped, delivered, cancelled or returned.';
COMMENT ON COLUMN live.orders.updated_at IS 'When the status last changed.';

CREATE TABLE IF NOT EXISTS live.deliveries (
  order_id      BIGINT PRIMARY KEY REFERENCES live.orders (order_id) ON DELETE CASCADE,
  carrier       TEXT NOT NULL,
  promised_at   TIMESTAMPTZ NOT NULL,
  shipped_at    TIMESTAMPTZ,
  delivered_at  TIMESTAMPTZ,
  on_time       BOOLEAN
);
CREATE INDEX IF NOT EXISTS live_deliveries_delivered_ix ON live.deliveries (delivered_at);

COMMENT ON TABLE live.deliveries IS
  'Shipment of each order that was not cancelled before shipping. One row per order, created when it ships.';
COMMENT ON COLUMN live.deliveries.order_id IS 'The order shipped (live.orders.order_id).';
COMMENT ON COLUMN live.deliveries.carrier IS 'Courier: Delhivery, BlueDart, Ecom Express, Shadowfax or XpressBees.';
COMMENT ON COLUMN live.deliveries.promised_at IS 'Delivery time promised to the customer at checkout.';
COMMENT ON COLUMN live.deliveries.shipped_at IS 'When the carrier picked the order up.';
COMMENT ON COLUMN live.deliveries.delivered_at IS 'When it was delivered; NULL while in transit.';
COMMENT ON COLUMN live.deliveries.on_time IS
  'true if delivered_at <= promised_at; NULL while in transit. On-time rate = avg(on_time::int).';

CREATE TABLE IF NOT EXISTS live.reviews (
  order_id     BIGINT PRIMARY KEY REFERENCES live.orders (order_id) ON DELETE CASCADE,
  rating       SMALLINT NOT NULL,
  reviewed_at  TIMESTAMPTZ NOT NULL,
  CONSTRAINT live_reviews_rating_ck CHECK (rating BETWEEN 1 AND 5)
);
CREATE INDEX IF NOT EXISTS live_reviews_reviewed_ix ON live.reviews (reviewed_at);

COMMENT ON TABLE live.reviews IS 'A customer rating left after delivery; not every order is reviewed.';
COMMENT ON COLUMN live.reviews.order_id IS 'The order reviewed (live.orders.order_id).';
COMMENT ON COLUMN live.reviews.rating IS 'Stars, 1 (worst) to 5 (best).';
COMMENT ON COLUMN live.reviews.reviewed_at IS 'When the review was left.';

CREATE TABLE IF NOT EXISTS live.web_traffic (
  bucket_start      TIMESTAMPTZ NOT NULL,
  channel           TEXT NOT NULL,
  device            TEXT NOT NULL,
  sessions          INTEGER NOT NULL,
  conversions       INTEGER NOT NULL,
  total_duration_s  BIGINT NOT NULL,
  PRIMARY KEY (bucket_start, channel, device),
  CONSTRAINT live_traffic_counts_ck CHECK (sessions >= 0 AND conversions >= 0 AND conversions <= sessions)
);

COMMENT ON TABLE live.web_traffic IS
  'Website visits aggregated into 5-minute buckets per channel and device. '
  'Conversion rate = sum(conversions) / sum(sessions). '
  'Average session length in seconds = sum(total_duration_s) / sum(sessions).';
COMMENT ON COLUMN live.web_traffic.bucket_start IS 'Start of the 5-minute bucket.';
COMMENT ON COLUMN live.web_traffic.channel IS
  'Traffic source: Direct, Marketplace, Organic search, Affiliate or Social (same values as live.orders.channel).';
COMMENT ON COLUMN live.web_traffic.device IS 'mobile, desktop or tablet.';
COMMENT ON COLUMN live.web_traffic.sessions IS 'Visits that started in this bucket.';
COMMENT ON COLUMN live.web_traffic.conversions IS 'Visits in this bucket that ended in an order.';
COMMENT ON COLUMN live.web_traffic.total_duration_s IS 'Summed length of those visits, in seconds.';

-- The simulator's private plan: what will happen to each order, and when.
CREATE TABLE IF NOT EXISTS live_sim.plans (
  order_id      BIGINT PRIMARY KEY,
  outcome       TEXT NOT NULL,
  pack_at       TIMESTAMPTZ,
  ship_at       TIMESTAMPTZ,
  deliver_at    TIMESTAMPTZ,
  return_at     TIMESTAMPTZ,
  review_at     TIMESTAMPTZ,
  rating        SMALLINT,
  carrier       TEXT,
  promised_at   TIMESTAMPTZ,
  cancel_at     TIMESTAMPTZ,
  CONSTRAINT live_sim_outcome_ck CHECK (outcome IN ('delivered', 'cancelled', 'returned'))
);
-- Each tick applies the steps due in (last_tick, now]: one range scan per step.
CREATE INDEX IF NOT EXISTS plans_cancel_ix  ON live_sim.plans (cancel_at);
CREATE INDEX IF NOT EXISTS plans_pack_ix    ON live_sim.plans (pack_at);
CREATE INDEX IF NOT EXISTS plans_ship_ix    ON live_sim.plans (ship_at);
CREATE INDEX IF NOT EXISTS plans_deliver_ix ON live_sim.plans (deliver_at);
CREATE INDEX IF NOT EXISTS plans_return_ix  ON live_sim.plans (return_at);
CREATE INDEX IF NOT EXISTS plans_review_ix  ON live_sim.plans (review_at);

CREATE TABLE IF NOT EXISTS live_sim.state (
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL
);

-- Roles and grants.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'statesync_simulator') THEN
    CREATE ROLE statesync_simulator LOGIN;
  END IF;
END$$;

GRANT CONNECT ON DATABASE statesync TO statesync_simulator;
-- A temp table stages each tick's traffic before the bucket upsert.
GRANT TEMPORARY ON DATABASE statesync TO statesync_simulator;
GRANT USAGE ON SCHEMA live, live_sim TO statesync_simulator;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA live TO statesync_simulator;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA live_sim TO statesync_simulator;

GRANT USAGE ON SCHEMA live TO statesync_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA live TO statesync_reader;

COMMIT;

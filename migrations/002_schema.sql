-- migrations/002_schema.sql
-- Full DDL from A5. Runs as statesync_migrator (the DB owner).
-- PostGIS and pgvector extensions are installed first.

-- Extensions ----------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;

-- Network participants -------------------------------------------------------
CREATE TABLE IF NOT EXISTS network_participants (
  participant_id   TEXT PRIMARY KEY,
  legal_name       TEXT NOT NULL,
  participant_type TEXT NOT NULL
                   CHECK (participant_type IN ('seller','buyer_app','logistics','gateway')),
  onboarded_on     DATE NOT NULL,
  gst_state_code   TEXT
);

CREATE TABLE IF NOT EXISTS locations (
  location_id  TEXT PRIMARY KEY,
  digipin      CHAR(10),                     -- canonical form: no separators
  lat          DOUBLE PRECISION NOT NULL,
  lon          DOUBLE PRECISION NOT NULL,
  geom         GEOGRAPHY(POINT,4326) NOT NULL,
  city         TEXT NOT NULL,
  city_local   TEXT,                          -- Devanagari surface form
  state        TEXT NOT NULL,
  pincode      CHAR(6)
);
CREATE INDEX IF NOT EXISTS locations_geom_gix ON locations USING GIST (geom);
CREATE UNIQUE INDEX IF NOT EXISTS locations_digipin_uix ON locations (digipin)
  WHERE digipin IS NOT NULL;

CREATE TABLE IF NOT EXISTS sellers (
  seller_id       TEXT PRIMARY KEY REFERENCES network_participants(participant_id),
  display_name    TEXT NOT NULL,
  display_name_local TEXT,                    -- transliterated / Devanagari
  category        TEXT NOT NULL,
  location_id     TEXT NOT NULL REFERENCES locations(location_id),
  contracted_sla_hours INTEGER
);

CREATE TABLE IF NOT EXISTS logistics_providers (
  provider_id  TEXT PRIMARY KEY REFERENCES network_participants(participant_id),
  display_name TEXT NOT NULL,
  hub_location_id TEXT NOT NULL REFERENCES locations(location_id),
  b2b_enabled  BOOLEAN NOT NULL DEFAULT FALSE
);

-- Commerce ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS catalog_items (
  item_id     TEXT PRIMARY KEY,
  seller_id   TEXT NOT NULL REFERENCES sellers(seller_id),
  title       TEXT NOT NULL,
  title_local TEXT,
  category    TEXT NOT NULL,
  unit_price  NUMERIC(12,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
  order_id       TEXT PRIMARY KEY,
  buyer_app_id   TEXT NOT NULL REFERENCES network_participants(participant_id),
  seller_id      TEXT NOT NULL REFERENCES sellers(seller_id),
  placed_at      TIMESTAMPTZ NOT NULL,
  order_channel  TEXT NOT NULL CHECK (order_channel IN ('b2c','b2b')),
  ship_to_location_id TEXT NOT NULL REFERENCES locations(location_id),
  order_value    NUMERIC(12,2) NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_placed_at_ix ON orders (placed_at);
CREATE INDEX IF NOT EXISTS orders_seller_ix    ON orders (seller_id);

CREATE TABLE IF NOT EXISTS order_items (
  order_id  TEXT NOT NULL REFERENCES orders(order_id),
  item_id   TEXT NOT NULL REFERENCES catalog_items(item_id),
  quantity  INTEGER NOT NULL CHECK (quantity > 0),
  line_value NUMERIC(12,2) NOT NULL,
  PRIMARY KEY (order_id, item_id)
);

CREATE TABLE IF NOT EXISTS fulfillments (
  fulfillment_id TEXT PRIMARY KEY,
  order_id       TEXT NOT NULL REFERENCES orders(order_id),
  provider_id    TEXT NOT NULL REFERENCES logistics_providers(provider_id),
  promised_at    TIMESTAMPTZ NOT NULL,
  delivered_at   TIMESTAMPTZ,                 -- NULL = not yet delivered
  status         TEXT NOT NULL
                 CHECK (status IN ('pending','in_transit','delivered','failed','returned')),
  attempt_count  INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS fulfillments_order_ix ON fulfillments (order_id);

CREATE TABLE IF NOT EXISTS delivery_events (
  event_id       BIGSERIAL PRIMARY KEY,
  fulfillment_id TEXT NOT NULL REFERENCES fulfillments(fulfillment_id),
  occurred_at    TIMESTAMPTZ NOT NULL,
  event_type     TEXT NOT NULL,
  location_id    TEXT REFERENCES locations(location_id),
  note           TEXT                          -- free text; injection surface, see F
);

CREATE TABLE IF NOT EXISTS disputes (
  dispute_id  TEXT PRIMARY KEY,
  order_id    TEXT NOT NULL REFERENCES orders(order_id),
  raised_at   TIMESTAMPTZ NOT NULL,
  category    TEXT NOT NULL,
  resolved_at TIMESTAMPTZ,
  narrative   TEXT                             -- free text; injection surface, see F
);

-- Documents (Part E1) -------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
  document_id  TEXT PRIMARY KEY,
  tenant_id    TEXT NOT NULL,
  title        TEXT NOT NULL,
  doc_type     TEXT NOT NULL
               CHECK (doc_type IN ('policy','sop','supplier_agreement','sla_annexure')),
  source_uri   TEXT NOT NULL,
  party_id     TEXT REFERENCES network_participants(participant_id),
  ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document_chunks (
  chunk_id     TEXT PRIMARY KEY,
  document_id  TEXT NOT NULL REFERENCES documents(document_id),
  tenant_id    TEXT NOT NULL,
  ordinal      INTEGER NOT NULL,
  page         INTEGER,
  content_md   TEXT NOT NULL,       -- layout-preserving Markdown, incl. tables
  is_table     BOOLEAN NOT NULL DEFAULT FALSE,
  embedding    VECTOR(1024) NOT NULL
);
CREATE INDEX IF NOT EXISTS document_chunks_embedding_ix ON document_chunks
  USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS document_chunks_doc_ix ON document_chunks (document_id, ordinal);

-- Schema fingerprint registry (Part C1) ------------------------------------
CREATE TABLE IF NOT EXISTS schema_versions (
  fingerprint  CHAR(64) PRIMARY KEY,
  computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  is_current   BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE UNIQUE INDEX IF NOT EXISTS schema_versions_one_current
  ON schema_versions (is_current) WHERE is_current;

-- Grant schema usage and SELECT to statesync_reader (Layer 3, A4.5) --------
-- This is the grant that makes Layer 3 the actual security guarantee.
-- statesync_reader has CONNECT (from 001) + USAGE + SELECT.
-- No INSERT, UPDATE, DELETE, TRUNCATE, CREATE, ALTER, DROP.
GRANT USAGE ON SCHEMA public TO statesync_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO statesync_reader;
-- Ensure future tables created by migrator are also readable.
ALTER DEFAULT PRIVILEGES FOR ROLE statesync_migrator IN SCHEMA public
  GRANT SELECT ON TABLES TO statesync_reader;

-- Grant full DML to statesync_ingest (seed/ingest jobs only).
GRANT USAGE ON SCHEMA public TO statesync_ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO statesync_ingest;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO statesync_ingest;
ALTER DEFAULT PRIVILEGES FOR ROLE statesync_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO statesync_ingest;
ALTER DEFAULT PRIVILEGES FOR ROLE statesync_migrator IN SCHEMA public
  GRANT USAGE ON SEQUENCES TO statesync_ingest;

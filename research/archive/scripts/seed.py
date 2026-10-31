#!/usr/bin/env python3
"""
scripts/seed.py
Loads fixtures/ondc_seed.json and inserts rows into postgres as statesync_ingest.

Per B2 spec:
  "Seed data from fixtures/ondc_seed.json (Appendix 2.1).
   Seeding runs as statesync_ingest, never as statesync_reader."

Usage:
  python scripts/seed.py
  python scripts/seed.py --fixture fixtures/ondc_seed.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def _read_password(secret_file: str, env_var: str) -> str:
    try:
        return Path(secret_file).read_text().strip()
    except OSError:
        pw = os.environ.get(env_var, "")
        if not pw:
            raise RuntimeError(
                f"Password not found in {secret_file!r} or env var {env_var!r}."
            )
        return pw


def _build_ingest_dsn() -> str:
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db   = os.environ.get("POSTGRES_DB", "statesync")
    pw   = _read_password(
        os.environ.get("PG_INGEST_PASSWORD_FILE",
                       str(Path(__file__).parent.parent / "secrets" / "pg_ingest_password")),
        "PG_INGEST_PASSWORD",
    )
    return f"postgresql://statesync_ingest:{pw}@{host}:{port}/{db}"


def seed(fixture_path: Path) -> None:
    logger.info("Loading fixture: %s", fixture_path)
    data = json.loads(fixture_path.read_text())

    dsn = _build_ingest_dsn()
    logger.info("Connecting as statesync_ingest...")

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.transaction():
            # ── locations ─────────────────────────────────────────────────────
            locations = data.get("locations", [])
            logger.info("Seeding %d locations...", len(locations))
            for loc in locations:
                conn.execute("""
                    INSERT INTO locations
                        (location_id, digipin, lat, lon, geom, city, city_local, state, pincode)
                    VALUES (
                        %(location_id)s, %(digipin)s, %(lat)s, %(lon)s,
                        ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography,
                        %(city)s, %(city_local)s, %(state)s, %(pincode)s
                    )
                    ON CONFLICT (location_id) DO NOTHING
                """, loc)

            # ── network_participants ──────────────────────────────────────────
            participants = data.get("network_participants", [])
            logger.info("Seeding %d network_participants...", len(participants))
            for p in participants:
                conn.execute("""
                    INSERT INTO network_participants
                        (participant_id, legal_name, participant_type, onboarded_on, gst_state_code)
                    VALUES
                        (%(participant_id)s, %(legal_name)s, %(participant_type)s,
                         %(onboarded_on)s, %(gst_state_code)s)
                    ON CONFLICT (participant_id) DO NOTHING
                """, p)

            # ── sellers ───────────────────────────────────────────────────────
            sellers = data.get("sellers", [])
            logger.info("Seeding %d sellers...", len(sellers))
            for s in sellers:
                conn.execute("""
                    INSERT INTO sellers
                        (seller_id, display_name, display_name_local, category,
                         location_id, contracted_sla_hours)
                    VALUES
                        (%(seller_id)s, %(display_name)s, %(display_name_local)s,
                         %(category)s, %(location_id)s, %(contracted_sla_hours)s)
                    ON CONFLICT (seller_id) DO NOTHING
                """, s)

            # ── logistics_providers ───────────────────────────────────────────
            providers = data.get("logistics_providers", [])
            logger.info("Seeding %d logistics_providers...", len(providers))
            for lp in providers:
                conn.execute("""
                    INSERT INTO logistics_providers
                        (provider_id, display_name, hub_location_id, b2b_enabled)
                    VALUES
                        (%(provider_id)s, %(display_name)s, %(hub_location_id)s, %(b2b_enabled)s)
                    ON CONFLICT (provider_id) DO NOTHING
                """, lp)

            # ── catalog_items ─────────────────────────────────────────────────
            items = data.get("catalog_items", [])
            logger.info("Seeding %d catalog_items...", len(items))
            for it in items:
                conn.execute("""
                    INSERT INTO catalog_items
                        (item_id, seller_id, title, title_local, category, unit_price)
                    VALUES
                        (%(item_id)s, %(seller_id)s, %(title)s, %(title_local)s,
                         %(category)s, %(unit_price)s)
                    ON CONFLICT (item_id) DO NOTHING
                """, it)

            # ── orders ────────────────────────────────────────────────────────
            orders = data.get("orders", [])
            logger.info("Seeding %d orders...", len(orders))
            for o in orders:
                conn.execute("""
                    INSERT INTO orders
                        (order_id, buyer_app_id, seller_id, placed_at,
                         order_channel, ship_to_location_id, order_value)
                    VALUES
                        (%(order_id)s, %(buyer_app_id)s, %(seller_id)s, %(placed_at)s,
                         %(order_channel)s, %(ship_to_location_id)s, %(order_value)s)
                    ON CONFLICT (order_id) DO NOTHING
                """, o)

            # ── order_items ───────────────────────────────────────────────────
            order_items = data.get("order_items", [])
            logger.info("Seeding %d order_items...", len(order_items))
            for oi in order_items:
                conn.execute("""
                    INSERT INTO order_items
                        (order_id, item_id, quantity, line_value)
                    VALUES
                        (%(order_id)s, %(item_id)s, %(quantity)s, %(line_value)s)
                    ON CONFLICT (order_id, item_id) DO NOTHING
                """, oi)

            # ── fulfillments ──────────────────────────────────────────────────
            fulfillments = data.get("fulfillments", [])
            logger.info("Seeding %d fulfillments...", len(fulfillments))
            for f in fulfillments:
                conn.execute("""
                    INSERT INTO fulfillments
                        (fulfillment_id, order_id, provider_id, promised_at,
                         delivered_at, status, attempt_count)
                    VALUES
                        (%(fulfillment_id)s, %(order_id)s, %(provider_id)s, %(promised_at)s,
                         %(delivered_at)s, %(status)s, %(attempt_count)s)
                    ON CONFLICT (fulfillment_id) DO NOTHING
                """, f)

            # ── delivery_events ───────────────────────────────────────────────
            events = data.get("delivery_events", [])
            logger.info("Seeding %d delivery_events...", len(events))
            for ev in events:
                conn.execute("""
                    INSERT INTO delivery_events
                        (fulfillment_id, occurred_at, event_type, location_id, note)
                    VALUES
                        (%(fulfillment_id)s, %(occurred_at)s, %(event_type)s,
                         %(location_id)s, %(note)s)
                """, ev)

            # ── disputes ──────────────────────────────────────────────────────
            disputes = data.get("disputes", [])
            logger.info("Seeding %d disputes...", len(disputes))
            for d in disputes:
                conn.execute("""
                    INSERT INTO disputes
                        (dispute_id, order_id, raised_at, category, resolved_at, narrative)
                    VALUES
                        (%(dispute_id)s, %(order_id)s, %(raised_at)s, %(category)s,
                         %(resolved_at)s, %(narrative)s)
                    ON CONFLICT (dispute_id) DO NOTHING
                """, d)

        logger.info("Seed complete (transaction committed).")

    # Print row counts for verification
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for table in [
            "locations", "network_participants", "sellers", "logistics_providers",
            "catalog_items", "orders", "order_items", "fulfillments",
            "delivery_events", "disputes",
        ]:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            count = row["n"] if row else 0
            logger.info("  %-25s %d rows", table, count)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the StateSync database.")
    parser.add_argument(
        "--fixture",
        default=str(Path(__file__).parent.parent / "fixtures" / "ondc_seed.json"),
        help="Path to the fixture file (default: fixtures/ondc_seed.json)",
    )
    args = parser.parse_args()

    fixture = Path(args.fixture)
    if not fixture.exists():
        logger.error("Fixture file not found: %s", fixture)
        sys.exit(1)

    seed(fixture)

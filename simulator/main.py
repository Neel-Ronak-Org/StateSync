"""
simulator/main.py
Keeps the `live` demo dataset moving (docs/V3_PLAN.md Phase 2b, migration 020).

On first start it backfills [simulator].backfill_days of history; after a
restart it catches up from where it stopped. Then, every tick_s seconds, in
ONE transaction:
  1. new orders for the time since the last tick (model.orders_between) and
     their plans (model.make_plan, stored in live_sim.plans);
  2. web traffic for that time (model.traffic_between), added to the current
     5-minute buckets;
  3. every planned step that fell due in (last_tick, now]: cancellations,
     packing, shipping (which creates the delivery row), delivery, returns
     and reviews, each one set-based SQL statement;
  4. last_tick = now.
A failed tick rolls back whole and is simply retried: the window just grows.

It connects as statesync_simulator, which can write to live and live_sim and
nothing else (migration 020). It never reads the model's data or anyone's
conversations.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import random
import signal
import time
import tomllib
from pathlib import Path

import psycopg

import model

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("simulator")

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/app/config"))
HEARTBEAT = Path(os.environ.get("HEARTBEAT_FILE", "/tmp/heartbeat"))

TRANSITIONS: tuple[tuple[str, str], ...] = (
    ("cancelled", """
        UPDATE live.orders o SET status = 'cancelled', updated_at = p.cancel_at
        FROM live_sim.plans p
        WHERE p.order_id = o.order_id AND o.status = 'placed'
          AND p.cancel_at > %(since)s AND p.cancel_at <= %(until)s"""),
    ("packed", """
        UPDATE live.orders o SET status = 'packed', updated_at = p.pack_at
        FROM live_sim.plans p
        WHERE p.order_id = o.order_id AND o.status = 'placed'
          AND p.pack_at > %(since)s AND p.pack_at <= %(until)s"""),
    ("shipments", """
        INSERT INTO live.deliveries (order_id, carrier, promised_at, shipped_at)
        SELECT p.order_id, p.carrier, p.promised_at, p.ship_at
        FROM live_sim.plans p JOIN live.orders o ON o.order_id = p.order_id
        WHERE o.status = 'packed' AND p.ship_at > %(since)s AND p.ship_at <= %(until)s
        ON CONFLICT (order_id) DO NOTHING"""),
    ("shipped", """
        UPDATE live.orders o SET status = 'shipped', updated_at = p.ship_at
        FROM live_sim.plans p
        WHERE p.order_id = o.order_id AND o.status = 'packed'
          AND p.ship_at > %(since)s AND p.ship_at <= %(until)s"""),
    ("arrivals", """
        UPDATE live.deliveries d SET delivered_at = p.deliver_at, on_time = (p.deliver_at <= p.promised_at)
        FROM live_sim.plans p
        WHERE p.order_id = d.order_id AND d.delivered_at IS NULL
          AND p.deliver_at > %(since)s AND p.deliver_at <= %(until)s"""),
    ("delivered", """
        UPDATE live.orders o SET status = 'delivered', updated_at = p.deliver_at
        FROM live_sim.plans p
        WHERE p.order_id = o.order_id AND o.status = 'shipped'
          AND p.deliver_at > %(since)s AND p.deliver_at <= %(until)s"""),
    ("returned", """
        UPDATE live.orders o SET status = 'returned', updated_at = p.return_at
        FROM live_sim.plans p
        WHERE p.order_id = o.order_id AND o.status = 'delivered' AND p.outcome = 'returned'
          AND p.return_at > %(since)s AND p.return_at <= %(until)s"""),
    ("reviews", """
        INSERT INTO live.reviews (order_id, rating, reviewed_at)
        SELECT p.order_id, p.rating, p.review_at
        FROM live_sim.plans p JOIN live.orders o ON o.order_id = p.order_id
        WHERE o.status IN ('delivered', 'returned')
          AND p.review_at > %(since)s AND p.review_at <= %(until)s
        ON CONFLICT (order_id) DO NOTHING"""),
)

_running = True


def _stop(*_: object) -> None:
    global _running
    _running = False


def load_config() -> tuple[model.Settings, dict]:
    """[simulator] from config/statesync.toml: the shop's Settings, and the loop's own knobs."""
    raw = tomllib.loads((CONFIG_DIR / "statesync.toml").read_text())["simulator"]
    fields = model.Settings.__dataclass_fields__
    settings = model.Settings(**{k: v for k, v in raw.items() if k in fields})
    loop = {k: raw[k] for k in ("tick_s", "backfill_days", "retention_days", "prune_every_s", "seed")}
    return settings, loop


def connect() -> psycopg.Connection:
    password = Path(os.environ.get("PG_SIMULATOR_PASSWORD_FILE", "/run/secrets/pg_simulator_password")).read_text().strip()
    while True:
        try:
            return psycopg.connect(
                host=os.environ.get("POSTGRES_HOST", "postgres"),
                port=int(os.environ.get("POSTGRES_PORT", "5432")),
                dbname=os.environ.get("POSTGRES_DB", "statesync"),
                user=os.environ.get("POSTGRES_USER_SIMULATOR", "statesync_simulator"),
                password=password,
            )
        except psycopg.OperationalError as exc:
            logger.warning("database not reachable yet (%s); retrying", exc)
            time.sleep(3)


def read_position(conn: psycopg.Connection) -> tuple[dt.datetime | None, int]:
    """(last_tick or None if never run, the next order id)."""
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM live_sim.state WHERE key = 'last_tick'")
        row = cur.fetchone()
        cur.execute("SELECT coalesce(max(order_id), 0) + 1 FROM live.orders")
        next_id = cur.fetchone()[0]
    return (dt.datetime.fromisoformat(row[0]) if row else None), next_id


def write_orders(cur: psycopg.Cursor, orders: list[model.Order], plans: list[model.Plan]) -> None:
    with cur.copy("COPY live.orders (order_id, placed_at, customer_id, city, state, region, category, "
                  "channel, payment_method, items, amount, status, updated_at) FROM STDIN") as copy:
        for o in orders:
            copy.write_row((o.order_id, o.placed_at, o.customer_id, o.city, o.state, o.region, o.category,
                            o.channel, o.payment_method, o.items, o.amount, "placed", o.placed_at))
    with cur.copy("COPY live_sim.plans (order_id, outcome, pack_at, ship_at, deliver_at, return_at, "
                  "review_at, rating, carrier, promised_at, cancel_at) FROM STDIN") as copy:
        for p in plans:
            copy.write_row((p.order_id, p.outcome, p.pack_at, p.ship_at, p.deliver_at, p.return_at,
                            p.review_at, p.rating, p.carrier, p.promised_at, p.cancel_at))


def write_traffic(cur: psycopg.Cursor, rows: dict) -> None:
    """Add visits to their buckets: the current bucket may already have rows from earlier ticks."""
    cur.execute("CREATE TEMP TABLE IF NOT EXISTS traffic_in (LIKE live.web_traffic) ON COMMIT DELETE ROWS")
    with cur.copy("COPY traffic_in (bucket_start, channel, device, sessions, conversions, total_duration_s) "
                  "FROM STDIN") as copy:
        for (bucket, channel, device), (sessions, conversions, duration) in rows.items():
            copy.write_row((bucket, channel, device, sessions, conversions, duration))
    cur.execute("""
        INSERT INTO live.web_traffic AS t (bucket_start, channel, device, sessions, conversions, total_duration_s)
        SELECT bucket_start, channel, device, sessions, conversions, total_duration_s FROM traffic_in
        ON CONFLICT (bucket_start, channel, device) DO UPDATE SET
            sessions = t.sessions + EXCLUDED.sessions,
            conversions = t.conversions + EXCLUDED.conversions,
            total_duration_s = t.total_duration_s + EXCLUDED.total_duration_s
    """)


def apply_due(cur: psycopg.Cursor, since: dt.datetime, until: dt.datetime) -> dict[str, int]:
    counts = {}
    for name, sql in TRANSITIONS:
        cur.execute(sql, {"since": since, "until": until})
        counts[name] = cur.rowcount
    return counts


def prune(cur: psycopg.Cursor, now: dt.datetime, retention_days: int) -> None:
    cutoff = now - dt.timedelta(days=retention_days)
    cur.execute("DELETE FROM live.orders WHERE placed_at < %s", (cutoff,))
    orders = cur.rowcount
    cur.execute("DELETE FROM live_sim.plans WHERE order_id < (SELECT min(order_id) FROM live.orders)")
    cur.execute("DELETE FROM live.web_traffic WHERE bucket_start < %s", (cutoff,))
    if orders:
        logger.info("pruned %d orders older than %s", orders, cutoff.isoformat())


def tick(conn: psycopg.Connection, rng: random.Random, settings: model.Settings,
         since: dt.datetime, until: dt.datetime, next_id: int) -> tuple[int, dict[str, int]]:
    """One transaction: orders, plans, traffic and due steps for (since, until]. Returns (next_id, counts)."""
    orders = model.orders_between(rng, since, until, next_id, settings)
    plans = [model.make_plan(rng, o, settings) for o in orders]
    traffic = model.traffic_between(rng, since, until, orders, settings)
    with conn.transaction(), conn.cursor() as cur:
        if orders:
            write_orders(cur, orders, plans)
        if traffic:
            write_traffic(cur, traffic)
        counts = apply_due(cur, since, until)
        cur.execute("""
            INSERT INTO live_sim.state (key, value) VALUES ('last_tick', %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, (until.isoformat(),))
    counts["orders"] = len(orders)
    return next_id + len(orders), counts


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    settings, loop = load_config()
    conn = connect()
    conn.autocommit = True        # every write goes through an explicit conn.transaction()

    now = dt.datetime.now(dt.timezone.utc)
    rng = random.Random(int(loop["seed"]) + int(now.timestamp()))
    last_tick, next_id = read_position(conn)
    earliest = now - dt.timedelta(days=int(loop["retention_days"]))
    if last_tick is None:
        last_tick = now - dt.timedelta(days=int(loop["backfill_days"]))
        logger.info("no history: backfilling from %s", last_tick.isoformat())
    elif last_tick < earliest:
        last_tick = earliest
    started = time.monotonic()
    next_id, counts = tick(conn, rng, settings, last_tick, now, next_id)
    logger.info("caught up to %s in %.1fs: %s", now.isoformat(), time.monotonic() - started, counts)
    last_tick = now
    HEARTBEAT.write_text(now.isoformat())

    last_prune = 0.0
    totals: dict[str, int] = {}
    ticks = 0
    while _running:
        time.sleep(float(loop["tick_s"]))
        now = dt.datetime.now(dt.timezone.utc)
        try:
            next_id, counts = tick(conn, rng, settings, last_tick, now, next_id)
            last_tick = now
            for key, value in counts.items():
                totals[key] = totals.get(key, 0) + value
            ticks += 1
            HEARTBEAT.write_text(now.isoformat())
            if time.monotonic() - last_prune >= float(loop["prune_every_s"]):
                with conn.transaction(), conn.cursor() as cur:
                    prune(cur, now, int(loop["retention_days"]))
                last_prune = time.monotonic()
            if ticks % 12 == 0:  # noqa: literals - log about once a minute at the shipped tick
                logger.info("last %d ticks: %s", ticks, totals)
                totals, ticks = {}, 0
        except psycopg.OperationalError as exc:
            logger.warning("tick failed (%s); reconnecting, the next tick covers this one", exc)
            try:
                conn.close()
            finally:
                conn = connect()
                conn.autocommit = True
                _, next_id = read_position(conn)
    logger.info("stopping")
    conn.close()


if __name__ == "__main__":
    main()

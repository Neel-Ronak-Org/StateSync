"""
tests/test_simulator_model.py
Pins the live demo shop's rules (simulator/model.py, docs/V3_PLAN.md Phase 2b).

WHY THIS EXISTS. The dashboards built on the `live` dataset are only as
believable as the data: an order delivered before it shipped, a review of a
cancelled order, more conversions than visits, or a night busier than the
evening would each show up on a chart and make the whole demo look fake. The
simulator applies each planned step with SQL; these tests pin that the plans
themselves are always in a possible order, and that the traffic agrees with
the orders.
"""

from __future__ import annotations

import datetime as dt
import random
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "simulator"))

import model  # noqa: E402

S = model.Settings()
UTC = dt.timezone.utc
# A Wednesday in IST: 13:30 UTC is 19:00 IST (evening peak), 21:30 UTC is 03:00 IST.
EVENING = dt.datetime(2026, 9, 23, 13, 30, tzinfo=UTC)
NIGHT = dt.datetime(2026, 9, 23, 21, 30, tzinfo=UTC)


def test_same_seed_same_shop():
    a = model.orders_between(random.Random(7), EVENING, EVENING + dt.timedelta(hours=1), 1, S)
    b = model.orders_between(random.Random(7), EVENING, EVENING + dt.timedelta(hours=1), 1, S)
    assert [(o.placed_at, o.amount, o.city) for o in a] == [(o.placed_at, o.amount, o.city) for o in b]


def test_evenings_are_busier_than_nights_and_weekends_than_weekdays():
    assert model.orders_per_minute(EVENING, S) > 3 * model.orders_per_minute(NIGHT, S)
    saturday = EVENING + dt.timedelta(days=3)
    weekday_next_week = EVENING + dt.timedelta(days=7)
    assert model.orders_per_minute(saturday, S) > model.orders_per_minute(EVENING + dt.timedelta(days=2), S)
    assert model.orders_per_minute(weekday_next_week, S) > model.orders_per_minute(EVENING, S)   # growth


def test_orders_are_numbered_in_time_order_inside_the_window():
    start, end = EVENING, EVENING + dt.timedelta(minutes=90)
    orders = model.orders_between(random.Random(1), start, end, 1000, S)
    assert orders, "an evening hour and a half should have orders"
    assert [o.order_id for o in orders] == list(range(1000, 1000 + len(orders)))
    assert all(start <= o.placed_at <= end for o in orders)
    assert [o.placed_at for o in orders] == sorted(o.placed_at for o in orders)


def test_the_order_count_follows_the_rate():
    start, end = EVENING, EVENING + dt.timedelta(hours=3)
    expected = sum(model.orders_per_minute(start + dt.timedelta(minutes=m), S) for m in range(180))
    got = len(model.orders_between(random.Random(3), start, end, 1, S))
    assert 0.8 * expected < got < 1.2 * expected


def test_orders_use_the_shops_catalogue():
    orders = model.orders_between(random.Random(2), EVENING, EVENING + dt.timedelta(hours=2), 1, S)
    cities = {c[0]: (c[1], c[2]) for c in model.CITIES}
    for o in orders:
        assert (o.state, o.region) == cities[o.city]
        assert o.category in {c[0] for c in model.CATEGORIES}
        assert o.channel in {c[0] for c in model.CHANNELS}
        assert o.payment_method in {p[0] for p in model.PAYMENTS}
        assert 1 <= o.items <= 6 and o.amount >= 49
        assert 1 <= o.customer_id <= S.customers + 1


def test_every_plan_is_a_possible_timeline():
    rng = random.Random(11)
    orders = model.orders_between(rng, EVENING, EVENING + dt.timedelta(hours=12), 1, S)
    plans = [model.make_plan(rng, o, S) for o in orders]
    outcomes = {p.outcome for p in plans}
    assert outcomes == {"delivered", "cancelled", "returned"}
    for order, plan in zip(orders, plans):
        if plan.outcome == "cancelled":
            assert plan.cancel_at > order.placed_at
            assert plan.pack_at is plan.ship_at is plan.deliver_at is plan.review_at is None
            continue
        assert order.placed_at < plan.pack_at < plan.ship_at < plan.deliver_at
        assert plan.promised_at > order.placed_at and plan.carrier in {c[0] for c in model.CARRIERS}
        if plan.outcome == "returned":
            assert plan.return_at > plan.deliver_at
        else:
            assert plan.return_at is None
        if plan.review_at is not None:
            assert plan.review_at > plan.deliver_at and 1 <= plan.rating <= 5
        else:
            assert plan.rating is None


def test_late_deliveries_are_rated_lower():
    rng = random.Random(5)
    orders = model.orders_between(rng, EVENING, EVENING + dt.timedelta(days=3), 1, S)
    on_time, late = [], []
    for o in orders:
        p = model.make_plan(rng, o, S)
        if p.rating is not None and p.outcome == "delivered":
            (on_time if p.deliver_at <= p.promised_at else late).append(p.rating)
    assert on_time and late
    assert sum(on_time) / len(on_time) > sum(late) / len(late) + 0.5


def test_traffic_agrees_with_the_orders():
    rng = random.Random(8)
    start, end = EVENING, EVENING + dt.timedelta(hours=2)
    orders = model.orders_between(rng, start, end, 1, S)
    rows = model.traffic_between(rng, start, end, orders, S)
    assert sum(r[1] for r in rows.values()) == len(orders), "conversions are exactly the orders"
    devices = {d for d, _ in model.DEVICES}
    channels = {c for c, _, _ in model.CHANNELS}
    for (bucket, channel, device), (sessions, conversions, duration) in rows.items():
        assert bucket.minute % 5 == 0 and bucket.second == 0
        assert channel in channels and device in devices
        assert 0 <= conversions <= sessions and duration >= 0
    rate = sum(r[1] for r in rows.values()) / sum(r[0] for r in rows.values())
    assert 0.02 < rate < 0.05, f"overall conversion rate {rate:.3f} is not a believable shop"


def test_a_tick_inside_one_bucket_stays_in_it():
    start = dt.datetime(2026, 9, 23, 13, 31, 0, tzinfo=UTC)
    rows = model.traffic_between(random.Random(4), start, start + dt.timedelta(seconds=5), [], S)
    assert {bucket for bucket, _, _ in rows} <= {dt.datetime(2026, 9, 23, 13, 30, tzinfo=UTC)}


def test_every_config_key_is_used():
    raw = tomllib.loads((REPO_ROOT / "config" / "statesync.toml").read_text())["simulator"]
    loop_keys = {"tick_s", "backfill_days", "retention_days", "prune_every_s", "seed"}
    fields = set(model.Settings.__dataclass_fields__)
    assert set(raw) - loop_keys <= fields, f"unused [simulator] keys: {set(raw) - loop_keys - fields}"
    shipped = model.Settings(**{k: v for k, v in raw.items() if k in fields})
    assert shipped.orders_per_minute_peak > shipped.orders_per_minute_trough > 0

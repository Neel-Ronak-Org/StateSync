"""
simulator/model.py
How the live demo shop behaves, as pure functions: no database, no clock
(docs/V3_PLAN.md Phase 2b, migration 020).

Everything takes a random.Random and explicit timestamps, so a seed gives the
same shop every time (tests/test_simulator_model.py) and main.py decides only
WHEN things happen.

THE SHOP. An Indian online store: demand follows the hour of day in IST (quiet
at night, peaks late morning and evening), is a little higher at weekends and
grows slowly. Each order is planned at birth: whether it will be cancelled,
when it will be packed, shipped and delivered (on time or late, by carrier),
whether it will be returned, and whether and how it will be reviewed. main.py
stores that plan in live_sim.plans and applies each step when its time comes,
so the backfilled past and the live present come from the same rules.

Web traffic is generated around the orders: the conversions in a 5-minute
bucket ARE the orders placed in it, per channel, and sessions add the visits
that did not convert, at each channel's conversion rate.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from dataclasses import dataclass, field

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BUCKET = dt.timedelta(minutes=5)

# Share of peak demand in each hour of the day, IST.
HOURLY_PROFILE: tuple[float, ...] = (
    0.22, 0.14, 0.09, 0.08, 0.10, 0.16, 0.28, 0.44, 0.60, 0.74, 0.86, 0.96,
    1.00, 0.92, 0.84, 0.80, 0.82, 0.88, 0.95, 1.00, 0.98, 0.86, 0.62, 0.38,
)

# (city, state, region, weight)
CITIES: tuple[tuple[str, str, str, int], ...] = (
    ("Mumbai", "Maharashtra", "West", 14), ("Delhi", "Delhi", "North", 14),
    ("Bengaluru", "Karnataka", "South", 12), ("Hyderabad", "Telangana", "South", 9),
    ("Chennai", "Tamil Nadu", "South", 8), ("Kolkata", "West Bengal", "East", 8),
    ("Pune", "Maharashtra", "West", 7), ("Ahmedabad", "Gujarat", "West", 6),
    ("Jaipur", "Rajasthan", "North", 4), ("Lucknow", "Uttar Pradesh", "North", 4),
    ("Surat", "Gujarat", "West", 3), ("Kochi", "Kerala", "South", 3),
    ("Indore", "Madhya Pradesh", "Central", 3), ("Bhopal", "Madhya Pradesh", "Central", 2),
    ("Nagpur", "Maharashtra", "Central", 2), ("Patna", "Bihar", "East", 2),
    ("Bhubaneswar", "Odisha", "East", 2), ("Chandigarh", "Chandigarh", "North", 2),
    ("Visakhapatnam", "Andhra Pradesh", "South", 2), ("Guwahati", "Assam", "East", 1),
)
METROS = frozenset({"Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Chennai", "Kolkata", "Pune"})

# (category, weight, median order value in INR)
CATEGORIES: tuple[tuple[str, int, float], ...] = (
    ("Electronics", 14, 7999.0), ("Fashion", 24, 1299.0), ("Grocery", 20, 749.0),
    ("Home & Kitchen", 13, 1899.0), ("Beauty", 12, 649.0), ("Sports", 6, 1599.0),
    ("Books", 6, 449.0), ("Toys", 5, 999.0),
)

# (channel, share of orders, conversion rate of its visits)
CHANNELS: tuple[tuple[str, int, float], ...] = (
    ("Direct", 30, 0.045), ("Marketplace", 24, 0.038), ("Organic search", 20, 0.028),
    ("Affiliate", 15, 0.032), ("Social", 11, 0.018),
)

DEVICES: tuple[tuple[str, int], ...] = (("mobile", 68), ("desktop", 27), ("tablet", 5))

PAYMENTS: tuple[tuple[str, int], ...] = (
    ("UPI", 52), ("Card", 19), ("Cash on delivery", 17), ("Net banking", 6), ("Wallet", 6),
)

# (carrier, weight, probability of delivering by the promised time)
CARRIERS: tuple[tuple[str, int, float], ...] = (
    ("Delhivery", 30, 0.91), ("BlueDart", 18, 0.95), ("Ecom Express", 20, 0.87),
    ("Shadowfax", 17, 0.84), ("XpressBees", 15, 0.89),
)

RATINGS_ON_TIME = ((5, 55), (4, 28), (3, 10), (2, 4), (1, 3))
RATINGS_LATE = ((5, 25), (4, 30), (3, 20), (2, 13), (1, 12))
RATINGS_RETURNED = ((5, 4), (4, 8), (3, 18), (2, 30), (1, 40))


@dataclass(frozen=True)
class Settings:
    """[simulator] in config/statesync.toml; defaults match the shipped values."""
    orders_per_minute_peak: float = 5.0
    orders_per_minute_trough: float = 0.6
    weekend_uplift: float = 1.15
    daily_growth: float = 0.002
    growth_anchor: dt.datetime = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    cancel_share: float = 0.04
    return_share: float = 0.05
    review_share: float = 0.35
    converting_session_s: float = 420.0
    browsing_session_s: float = 240.0
    customers: int = 40000


@dataclass
class Order:
    order_id: int
    placed_at: dt.datetime
    customer_id: int
    city: str
    state: str
    region: str
    category: str
    channel: str
    payment_method: str
    items: int
    amount: float
    device: str = field(default="mobile")   # the converting visit's device; not stored on the order


@dataclass
class Plan:
    order_id: int
    outcome: str                             # delivered | cancelled | returned
    pack_at: dt.datetime | None = None
    ship_at: dt.datetime | None = None
    deliver_at: dt.datetime | None = None
    return_at: dt.datetime | None = None
    review_at: dt.datetime | None = None
    rating: int | None = None
    carrier: str | None = None
    promised_at: dt.datetime | None = None
    cancel_at: dt.datetime | None = None


# ── Sampling ──────────────────────────────────────────────────────────────────

def weighted(rng: random.Random, items: tuple, weight_at: int = 1) -> tuple:
    """One of the tuples in `items`, chosen by the weight at index `weight_at`."""
    return rng.choices(items, weights=[item[weight_at] for item in items], k=1)[0]


def poisson(rng: random.Random, lam: float) -> int:
    """A Poisson draw. Knuth's method for small means, a rounded normal for large ones."""
    if lam <= 0:
        return 0
    if lam > 60:  # noqa: literals - switch-over point of the approximation, not configuration
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def binomial(rng: random.Random, n: int, p: float) -> int:
    return sum(1 for _ in range(n) if rng.random() < p)


# ── Demand ────────────────────────────────────────────────────────────────────

def orders_per_minute(at: dt.datetime, s: Settings) -> float:
    """Expected orders per minute at a moment: time of day (IST), weekend, slow growth."""
    local = at.astimezone(IST)
    hour_share = HOURLY_PROFILE[local.hour]
    rate = s.orders_per_minute_trough + (s.orders_per_minute_peak - s.orders_per_minute_trough) * hour_share
    if local.weekday() >= 5:
        rate *= s.weekend_uplift
    days = (at - s.growth_anchor).total_seconds() / 86400.0
    return rate * (1.0 + s.daily_growth) ** days


def make_order(rng: random.Random, order_id: int, placed_at: dt.datetime, s: Settings) -> Order:
    city, state, region, _ = weighted(rng, CITIES, weight_at=3)
    category, _, median = weighted(rng, CATEGORIES)
    channel, _, _ = weighted(rng, CHANNELS)
    payment, _ = weighted(rng, PAYMENTS)
    device, _ = weighted(rng, DEVICES)
    items = 1
    while items < 6 and rng.random() < 0.28:  # noqa: literals - basket-size shape
        items += 1
    amount = median * math.exp(rng.gauss(0.0, 0.55)) * (1.0 + 0.35 * (items - 1))
    # A few customers order often: the square keeps ids skewed toward the low end.
    customer_id = 1 + int((rng.random() ** 2) * s.customers)
    return Order(order_id=order_id, placed_at=placed_at, customer_id=customer_id, city=city,
                 state=state, region=region, category=category, channel=channel,
                 payment_method=payment, items=items, amount=round(max(49.0, amount), 2),
                 device=device)


def orders_between(rng: random.Random, start: dt.datetime, end: dt.datetime,
                   next_id: int, s: Settings) -> list[Order]:
    """Orders placed in (start, end], minute by minute, with ids from next_id upward."""
    orders: list[Order] = []
    cursor = start
    while cursor < end:
        step_end = min(end, cursor.replace(second=0, microsecond=0) + dt.timedelta(minutes=1))
        span = (step_end - cursor).total_seconds()
        count = poisson(rng, orders_per_minute(cursor, s) * span / 60.0)
        for placed in sorted(cursor + dt.timedelta(seconds=rng.random() * span) for _ in range(count)):
            orders.append(make_order(rng, next_id, placed, s))
            next_id += 1
        cursor = step_end
    return orders


# ── What happens to an order ─────────────────────────────────────────────────

def _hours(h: float) -> dt.timedelta:
    return dt.timedelta(hours=h)


def make_plan(rng: random.Random, order: Order, s: Settings) -> Plan:
    placed = order.placed_at
    if rng.random() < s.cancel_share:
        return Plan(order.order_id, "cancelled", cancel_at=placed + _hours(rng.uniform(0.15, 6.0)))

    pack_at = placed + _hours(rng.uniform(0.08, 0.8))
    ship_at = pack_at + _hours(rng.uniform(1.0, 9.0))
    carrier, _, on_time_p = weighted(rng, CARRIERS)
    promise_days = 2 if order.city in METROS else rng.choice((3, 4))
    promised_at = placed + dt.timedelta(days=promise_days)
    on_time = rng.random() < on_time_p
    if on_time:
        deliver_at = promised_at - _hours(rng.uniform(0.5, 30.0))
    else:
        deliver_at = promised_at + _hours(rng.uniform(1.0, 60.0))
    deliver_at = max(deliver_at, ship_at + _hours(4.0))

    outcome = "returned" if rng.random() < s.return_share else "delivered"
    return_at = deliver_at + _hours(rng.uniform(24.0, 120.0)) if outcome == "returned" else None

    review_at = rating = None
    if rng.random() < s.review_share:
        review_at = deliver_at + _hours(rng.uniform(2.0, 72.0))
        table = RATINGS_RETURNED if outcome == "returned" else (
            RATINGS_ON_TIME if deliver_at <= promised_at else RATINGS_LATE)
        rating = weighted(rng, table)[0]

    return Plan(order.order_id, outcome, pack_at=pack_at, ship_at=ship_at, deliver_at=deliver_at,
                return_at=return_at, review_at=review_at, rating=rating, carrier=carrier,
                promised_at=promised_at)


# ── Web traffic ───────────────────────────────────────────────────────────────

def bucket_of(at: dt.datetime) -> dt.datetime:
    """Start of the 5-minute bucket containing `at`."""
    return at.replace(minute=at.minute - at.minute % 5, second=0, microsecond=0)


def traffic_between(rng: random.Random, start: dt.datetime, end: dt.datetime,
                    orders: list[Order], s: Settings) -> dict[tuple[dt.datetime, str, str], list[int]]:
    """Visits in (start, end], as {(bucket, channel, device): [sessions, conversions, duration_s]}.

    The conversions are exactly the given orders (each counted in its bucket,
    channel and device); visits that did not convert are added at each
    channel's conversion rate, so sessions >= conversions always.
    """
    rows: dict[tuple[dt.datetime, str, str], list[int]] = {}

    def add(key: tuple[dt.datetime, str, str], sessions: int, conversions: int, duration: float) -> None:
        row = rows.setdefault(key, [0, 0, 0])
        row[0] += sessions
        row[1] += conversions
        row[2] += int(round(duration))

    for order in orders:
        add((bucket_of(order.placed_at), order.channel, order.device), 1, 1,
            rng.expovariate(1.0 / s.converting_session_s))

    total_share = sum(share for _, share, _ in CHANNELS)
    cursor = start
    while cursor < end:
        step_end = min(end, bucket_of(cursor) + BUCKET)
        minutes = (step_end - cursor).total_seconds() / 60.0
        expected_orders = orders_per_minute(cursor, s) * minutes
        for channel, share, conversion in CHANNELS:
            channel_orders = expected_orders * share / total_share
            browsing = poisson(rng, channel_orders * (1.0 / conversion - 1.0))
            left = browsing
            for i, (device, weight) in enumerate(DEVICES):
                if i == len(DEVICES) - 1:
                    n = left
                else:
                    remaining_weight = sum(w for _, w in DEVICES[i:])
                    n = binomial(rng, left, weight / remaining_weight)
                left -= n
                if n:
                    add((bucket_of(cursor), channel, device), n, 0,
                        n * s.browsing_session_s * math.exp(rng.gauss(0.0, 0.15)))
        cursor = step_end
    return rows

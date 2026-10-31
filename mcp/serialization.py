"""
mcp/serialization.py
Postgres values as JSON-safe Python values.

Shared by the JSON-RPC layer (every tool result) and by tile_tools, which
stores a tile's rows as JSONB itself: those rows come straight from psycopg,
so a NUMERIC arrives as Decimal and a timestamp as datetime, and json.dumps
refuses both. Before this module that crashed every refresh of a tile with an
amount or a time bucket in it.
"""

from __future__ import annotations

import datetime
import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


def to_json_safe(obj: Any) -> Any:
    if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
        return obj.isoformat()
    if isinstance(obj, datetime.timedelta):
        # psycopg maps a Postgres INTERVAL (e.g. AVG(delivered_at -
        # promised_at)) to timedelta. Previously unhandled: fell through to
        # the raw object, which Starlette's JSON encoder cannot serialize --
        # an uncaught TypeError deep in the response path, surfacing as a
        # 500 transport fault. A failed query must return
        # ok:false as data, never a transport-level fault; an aggregate over
        # a timestamp difference (delivery delay, dispute resolution time,
        # etc.) is an ordinary, expected result shape, not a failure.
        return obj.total_seconds()
    if isinstance(obj, Decimal):
        return float(obj) if obj % 1 else int(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.hex()
    if isinstance(obj, dict):
        return {k: to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_json_safe(x) for x in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    # Catch-all for any remaining psycopg type mapping that JSON cannot
    # encode natively. Found the hard way twice, one type at a time:
    # INTERVAL -> timedelta, then INET -> IPv4Address
    #. Both surfaced as an uncaught TypeError deep inside
    # Starlette's encoder, i.e. a 500 transport fault -- exactly what the tool contract
    # forbids ("A failed query returns ok: false ... it does not raise a
    # transport-level fault"), and worse, a SUCCESSFUL query being reported
    # as a transport failure. Postgres has many more such types (NUMERIC
    # ranges, arrays of custom types, JSONB edge cases, CIDR, MACADDR,
    # tsvector...); enumerating them individually is whack-a-mole. Anything
    # unrecognized degrades to its string form, which is lossy but honest
    # and keeps the tool contract intact.
    logger.debug("Serializing unrecognized type %s via str().", type(obj).__name__)
    return str(obj)

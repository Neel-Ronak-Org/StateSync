"""
evaluator/history_store.py
Phase F Deliverable 2 — persisted evaluation history queryable as a trend.

SQLite file on a dedicated Docker volume (evaluator_data), not Postgres.
Genuinely new decision (A0.3) -- recorded as BD8, docs/phase_b_amendment.md:
A1 grants the evaluator only read-only access to postgres, reached through
mcp (which always connects as statesync_reader, Layer 3, no INSERT
privilege by construction). Writing evaluation history through that path
would mean either relaxing mcp's read-only guarantee for one caller (voids
the property B-V5/D15 rest on) or adding a new database role A1 never
describes. A local, evaluator-owned store keeps that guarantee untouched
and needs no new grant.

Schema: one row per RUN (not per query) holds the aggregate; the per-query
detail is stored as a JSON blob alongside it -- sufficient for "queryable as
a trend across >= 5 runs" (Deliverable 2) without standing up a second
relational schema for what is fundamentally a small, append-only log.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT NOT NULL,
    trigger         TEXT NOT NULL,   -- 'scheduled' | 'on_demand'
    spec_mean       REAL,
    spec_null_count INTEGER,
    spec_n          INTEGER,
    execution_mean  REAL,
    execution_n     INTEGER,
    vision_attempted INTEGER NOT NULL DEFAULT 0,
    detail_json     TEXT NOT NULL
);
"""


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def record_run(db_path: Path, run: dict[str, Any]) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO runs (run_id, started_at, finished_at, trigger,
                               spec_mean, spec_null_count, spec_n,
                               execution_mean, execution_n, vision_attempted,
                               detail_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run["run_id"], run["started_at"], run["finished_at"], run["trigger"],
                run["spec_mean"], run["spec_null_count"], run["spec_n"],
                run["execution_mean"], run["execution_n"],
                int(run["vision_attempted"]), json.dumps(run["detail"]),
            ),
        )


def get_history(db_path: Path, limit: int) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d.pop("detail_json"))
        d["vision_attempted"] = bool(d["vision_attempted"])
        out.append(d)
    return out


def prune_history(db_path: Path, keep: int) -> int:
    """Retains only the `keep` most recent runs (evaluation.history_retention_runs)."""
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            DELETE FROM runs WHERE run_id NOT IN (
                SELECT run_id FROM runs ORDER BY started_at DESC LIMIT ?
            )
            """,
            (keep,),
        )
        return cur.rowcount

#!/usr/bin/env python3
"""
scripts/seed_adversarial.py
Phase F Deliverable 4 — writes adversarial payloads into real database rows.

Per F2 Notes: "Tool poisoning payloads are seeded into real database rows,
specifically delivery_events.note and disputes.narrative (marked as
injection surfaces in A5). This is what makes the test real: the malicious
instruction arrives through the same path a genuine record would."

Writes as statesync_ingest -- same role, same connection pattern as
scripts/ingest.py (Part E1), never statesync_reader and never the
orchestrator/mcp path (A1: "Ingestion... writes through a separate
short-lived statesync_ingest role invoked by an operator-run job -- never by
the orchestrator, never by an agent").

Source data:
  fixtures/adversarial.json          -- the frozen payload text (Appendix 2.5,
                                         built at Phase B, unchanged since).
  fixtures/adversarial_targets.json  -- which specific, deterministic row each
                                         payload lands in, and the natural-
                                         language query that retrieves it
                                         (frozen, Part F).

Only payloads whose surface is a database column (delivery_events.note or
disputes.narrative) are seeded here. Payloads with surface == "user_query"
are submitted directly as live queries by scripts/verify_security.sh --
there is nothing to seed for those.

Idempotent: re-running overwrites the same target rows with the same
payload text (UPDATE, not INSERT) -- safe to run repeatedly, matching
ingest.py's own re-run safety.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_password(secret_file: str, env_var: str) -> str:
    try:
        return Path(secret_file).read_text().strip()
    except OSError:
        pw = os.environ.get(env_var, "")
        if not pw:
            raise RuntimeError(f"Password not found in {secret_file!r} or env var {env_var!r}.")
        return pw


def _build_ingest_dsn() -> str:
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5433")  # docker-compose.override.yml: 5433 -> 5432
    db = os.environ.get("POSTGRES_DB", "statesync")
    pw = _read_password(
        os.environ.get("PG_INGEST_PASSWORD_FILE", str(REPO_ROOT / "secrets" / "pg_ingest_password")),
        "PG_INGEST_PASSWORD",
    )
    return f"postgresql://statesync_ingest:{pw}@{host}:{port}/{db}"


def seed(adversarial_path: Path, targets_path: Path) -> list[dict[str, Any]]:
    payloads = {p["id"]: p for p in json.loads(adversarial_path.read_text())["payloads"]}
    targets = json.loads(targets_path.read_text())["targets"]

    dsn = _build_ingest_dsn()
    seeded: list[dict[str, Any]] = []

    print("Connecting as statesync_ingest...")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.transaction():
            for target in targets:
                adv_id = target["adv_id"]
                payload = payloads.get(adv_id)
                if payload is None:
                    raise RuntimeError(
                        f"adversarial_targets.json references {adv_id!r}, "
                        f"not found in fixtures/adversarial.json."
                    )
                if payload["surface"] != f"{target['table']}.{target['column']}":
                    raise RuntimeError(
                        f"{adv_id}: adversarial.json surface={payload['surface']!r} does not "
                        f"match adversarial_targets.json's {target['table']}.{target['column']!r}."
                    )

                if target["table"] == "delivery_events":
                    cur = conn.execute(
                        """
                        UPDATE delivery_events SET note = %(payload)s
                        WHERE fulfillment_id = %(key)s AND event_type = %(event_type)s
                        RETURNING event_id, fulfillment_id, event_type
                        """,
                        {
                            "payload": payload["payload"], "key": target["key_value"],
                            "event_type": target["match_event_type"],
                        },
                    )
                elif target["table"] == "disputes":
                    cur = conn.execute(
                        """
                        UPDATE disputes SET narrative = %(payload)s
                        WHERE dispute_id = %(key)s
                        RETURNING dispute_id, order_id
                        """,
                        {"payload": payload["payload"], "key": target["key_value"]},
                    )
                else:
                    raise RuntimeError(f"Unknown target table: {target['table']!r}")

                row = cur.fetchone()
                if row is None:
                    raise RuntimeError(
                        f"{adv_id}: target row not found "
                        f"({target['table']}.{target['key_column']}={target['key_value']!r}"
                        f"{', event_type=' + target['match_event_type'] if 'match_event_type' in target else ''}"
                        f"). Was the seed fixture (fixtures/ondc_seed.json) re-generated with different keys?"
                    )

                seeded.append({"adv_id": adv_id, "table": target["table"], "row": row})
                print(f"  {adv_id}: seeded {target['table']}.{target['column']} on {dict(row)}")

        print("Seeding complete (transaction committed).")

    return seeded


if __name__ == "__main__":
    result = seed(
        REPO_ROOT / "fixtures" / "adversarial.json",
        REPO_ROOT / "fixtures" / "adversarial_targets.json",
    )
    if len(result) != 9:
        print(f"WARNING: expected 9 seeded rows (5 delivery_events + 4 disputes), got {len(result)}.")
        sys.exit(1)
    print(f"\nTotal: {len(result)} rows seeded.")

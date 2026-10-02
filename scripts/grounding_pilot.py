#!/usr/bin/env python3
"""
scripts/grounding_pilot.py
The figure-grounding pilot (docs/research/paper_facts.md, "Pilot: figure grounding over stored
answers"), as a script so it can be re-run. It makes no model calls: every stored conversation turn
that ran SQL and has an answer is put through orchestrator/number_grounding.ground, the same
checker the live pipeline uses, against the result preview stored with the turn.

Turns are skipped, as in the 2026-09-30 run:
  - with no answer text or no result columns (nothing to check against);
  - whose stored preview has fewer rows than the query returned (a figure could sit in the
    rows that were not kept, so an "unmatched" there would be the preview's fault).
No passages are passed, so a hybrid answer quoting a document counts as unmatched (as in the pilot).

    python3 scripts/grounding_pilot.py [--until 2026-09-30T05:45:20Z] [--turns FILE] [--out FILE]

--until keeps turns computed at or before that time, which reproduces the pilot's data while
the store grows. --turns reads a JSON-lines export instead of the live store (one object per
line: q, a, cols, rows, n, schema, route, computed_at). Reads the store through the
migrator role inside the postgres container, the same way the 2026-09-30 run did.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO / "config"))

import number_grounding  # noqa: E402

EXPORT_SQL = (
    "SELECT json_build_object('q', raw_query, 'a', answer_text, 'cols', sql_columns, "
    "'rows', sql_rows_preview, 'n', sql_rows_count, 'schema', target_schema, 'route', route, "
    "'computed_at', computed_at)::text "
    "FROM session_state.conversation_turns WHERE executed_sql IS NOT NULL AND answer_text IS NOT NULL "
    "ORDER BY computed_at, session_id, turn_index"
)
FLAG_TEXT_CHARS = 90  # noqa: literals - how much of a flagged question the report keeps


def export_turns() -> list[dict[str, Any]]:
    out = subprocess.run(
        ["docker", "exec", "postgres", "psql", "-U", "statesync_migrator", "-d", "statesync", "-At", "-c", EXPORT_SQL],
        check=True, capture_output=True, text=True).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def run(turns: list[dict[str, Any]], until: str | None = None) -> dict[str, Any]:
    stats: collections.Counter = collections.Counter()
    kinds: collections.Counter = collections.Counter()
    per_schema: collections.Counter = collections.Counter()
    per_schema_flagged: collections.Counter = collections.Counter()
    flagged = []
    cutoff = _when(until) if until else None
    for t in turns:
        if cutoff is not None and t.get("computed_at") and _when(t["computed_at"]) > cutoff:
            stats["after_cutoff"] += 1
            continue
        if not t["a"] or not t["cols"]:
            stats["skipped_no_columns"] += 1
            continue
        if t["n"] is not None and t["rows"] is not None and t["n"] > len(t["rows"]):
            stats["skipped_preview_truncated"] += 1
            continue
        r = number_grounding.ground(t["a"], t["rows"] or [], t["cols"] or [], t["q"] or "")
        stats["answers"] += 1
        stats["figures"] += len(r["figures"])
        stats["unmatched_figures"] += len(r["unmatched"])
        per_schema[t.get("schema")] += 1
        if r["figures"]:
            stats["answers_with_figures"] += 1
        if r["unmatched"]:
            stats["answers_flagged"] += 1
            per_schema_flagged[t.get("schema")] += 1
            flagged.append({"question": (t["q"] or "")[:FLAG_TEXT_CHARS], "unmatched": r["unmatched"],
                            "route": t.get("route"), "schema": t.get("schema")})
        for f in r["figures"]:
            kinds[f["source"]["kind"] if f["source"] else "unmatched"] += 1
    traced = stats["figures"] - stats["unmatched_figures"]
    return {
        "until": until,
        "turns_read": len(turns),
        "stats": dict(stats),
        "traced_share": round(traced / stats["figures"], 4) if stats["figures"] else None,
        "flagged_share": round(stats["answers_flagged"] / stats["answers"], 4) if stats["answers"] else None,
        "kinds": dict(kinds),
        "per_schema": dict(per_schema),
        "per_schema_flagged": dict(per_schema_flagged),
        "flagged": flagged,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--until", help="keep turns computed at or before this ISO time")
    ap.add_argument("--turns", type=Path, help="a JSON-lines export instead of the live store")
    ap.add_argument("--out", type=Path, help="write the report here (JSON)")
    args = ap.parse_args()
    turns = ([json.loads(line) for line in args.turns.read_text().splitlines() if line.strip()]
             if args.turns else export_turns())
    report = run(turns, args.until)
    text = json.dumps(report, indent=1, default=str)
    if args.out:
        args.out.write_text(text + "\n")
    summary = {k: report[k] for k in ("until", "turns_read", "stats", "traced_share", "flagged_share", "kinds")}
    print(json.dumps(summary, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

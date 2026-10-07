#!/usr/bin/env python3
"""
scripts/build_fanout_set.py
Check and write the fan-out set of protocol v2 §3.2 (source: scripts/fanout_set_source.py). No model calls.

    python3 scripts/build_fanout_set.py              check every entry, write docs/eval/bird_fanout_set.json (draft)
    python3 scripts/build_fanout_set.py --review     print the owner's random 20% review sample (fixed seed)
    python3 scripts/build_fanout_set.py --freeze "<who reviewed, when, outcome>"   mark the set frozen

Every entry must pass, on the BIRD PostgreSQL copy:
- gold runs and returns rows that are not all NULL;
- check (an independent formulation) gives the same result as gold (eval_bird.norm_v2, sets of rows);
- risky: the naive join gives a DIFFERENT result (the double counting is real on this data);
- control: the naive join gives the SAME result (the join is harmless).
An entry that fails is reported and left out; nothing is changed to make it pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import eval_bird as eb  # noqa: E402
from fanout_set_source import FANOUT_SET  # noqa: E402

OUT = eb.EVAL / "bird_fanout_set.json"
SOURCE = REPO / "scripts" / "fanout_set_source.py"
REVIEW_SHARE = 0.2   # noqa: literals - protocol v2 §3.2


def same(a: list[tuple] | None, b: list[tuple] | None) -> bool:
    return a is not None and b is not None and eb.rows_match(a, b, eb.norm_v2)


def build() -> int:
    conn = eb.bird_connection()
    kept, failed = [], []
    for e in FANOUT_SET:
        gold = eb._run(conn, e["db_id"], e["gold"])
        check = eb._run(conn, e["db_id"], e["check"])
        naive = eb._run(conn, e["db_id"], e["naive"])
        problems = []
        if not gold:
            problems.append("gold failed or empty")
        elif all(v is None for r in gold for v in r):
            problems.append("gold all NULL")
        if not same(gold, check):
            problems.append(f"check differs: gold={str(gold)[:80]} check={str(check)[:80]}")
        if e["kind"] == "risky" and same(gold, naive):
            problems.append("risky but the naive join gives the same result")
        if e["kind"] == "control" and not same(gold, naive):
            problems.append(f"control but the naive join differs: naive={str(naive)[:80]}")
        if problems:
            failed.append({"id": e["id"], "problems": problems})
            continue
        kept.append({**{k: e[k] for k in ("id", "db_id", "kind", "relation", "question", "evidence", "gold", "naive")},
                     "gold_result": [[str(v) for v in r] for r in gold[:20]], "gold_rows": len(gold),
                     "naive_result": [[str(v) for v in r] for r in (naive or [])[:20]]})
    record = {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "protocol": "v2 §3.2", "status": "draft",
              "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              "risky": sum(e["kind"] == "risky" for e in kept), "control": sum(e["kind"] == "control" for e in kept),
              "failed": failed, "risky_ids": [e["id"] for e in kept if e["kind"] == "risky"],
              "control_ids": [e["id"] for e in kept if e["kind"] == "control"], "questions": kept}
    OUT.write_text(json.dumps(record, indent=1) + "\n")
    print(f"kept {len(kept)} (risky {record['risky']}, control {record['control']}); failed {len(failed)}")
    for f in failed:
        print(f"  {f['id']}: {'; '.join(f['problems'])}")
    return 0


def review() -> int:
    record = json.loads(OUT.read_text())
    qs = record["questions"]
    sample = sorted(random.Random(eb.SAMPLE_SEED).sample(qs, max(1, round(REVIEW_SHARE * len(qs)))), key=lambda e: e["id"])
    for e in sample:
        print(f"{e['id']} [{e['kind']}] {e['db_id']}: {e['question']}" + (f"  (Hint: {e['evidence']})" if e["evidence"] else ""))
        print(f"    gold: {e['gold']}\n    gold result: {e['gold_result'][:3]}{' ...' if e['gold_rows'] > 3 else ''}"
              f"   naive join gives: {e['naive_result'][:3]}")
    return 0


def freeze(note: str) -> int:
    record = json.loads(OUT.read_text())
    if record["source_sha256"] != hashlib.sha256(SOURCE.read_bytes()).hexdigest():
        print("the source changed since the set was built; rebuild first")
        return 1
    record.update(status="frozen", frozen_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), review=note)
    body = json.dumps(record["questions"], sort_keys=True).encode()
    record["questions_sha256"] = hashlib.sha256(body).hexdigest()
    OUT.write_text(json.dumps(record, indent=1) + "\n")
    print(f"frozen: {len(record['questions'])} questions, sha256 {record['questions_sha256']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--freeze")
    args = ap.parse_args()
    if args.review:
        return review()
    if args.freeze:
        return freeze(args.freeze)
    return build()


if __name__ == "__main__":
    raise SystemExit(main())

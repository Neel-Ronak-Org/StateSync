#!/usr/bin/env python3
"""
scripts/eval_bird_partd.py
Part D v3, gate G1 (docs/specs/routing_docs_fixes.md §3): the SQL path must not get worse. The same
random 150 of the 393 held-out BIRD questions go through two Part D orchestrators that differ only in
the v3 fixes: flags_partd_v3_off.toml (every fix at its shipped value) and flags_partd_v3_on.toml.
  - false NOT_ANSWERABLE: every held-out question is answerable, so each "not answerable" is a false one;
    the bar is <= 1%;
  - accuracy, paired (exact McNemar), labelled as Paper B labels its pool: the corrected gold on SQLite,
    the prediction on PostgreSQL, Paper A's relaxed rule (eval_bird.rows_match_relaxed, norm_v2).

It asks through eval_bird.cmd_ask unchanged, pointed at a Part D port at run time (eval_bird.py is Paper A's
and is not edited), then records the flags file the container actually ran with.

    python3 scripts/eval_bird_partd.py ask --arm off --port 8092 [--limit N]
    python3 scripts/eval_bird_partd.py ask --arm on  --port 8091 [--limit N]
    python3 scripts/eval_bird_partd.py score
    python3 scripts/eval_bird_partd.py ask --arm off --port 8092 --rep 1      a repeat of the off arm (noise floor)
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import eval_bird as eb  # noqa: E402

N = 150                     # noqa: literals - gate G1's sample of the 393 held-out questions
SEED = 20261006
FLAGS = {"off": REPO / "research" / "paperb" / "flags_partd_v3_off.toml",
         "on": REPO / "research" / "paperb" / "flags_partd_v3_on.toml"}
OUT = REPO / "docs" / "eval" / "partd_g1_bird.json"


def label(arm: str, rep: int = 0) -> str:
    return f"partd_v3_{arm}_heldout150" + (f"_r{rep}" if rep else "")


def sample_ids() -> list[int]:
    ids = json.loads(eb.HELDOUT.read_text())["question_ids"]
    return sorted(random.Random(SEED).sample(sorted(set(ids)), N))


def cmd_ask(arm: str, port: int, limit: int | None, rep: int = 0) -> int:
    eb.API = f"http://127.0.0.1:{port}"           # a Part D orchestrator, not the shared one
    rc = eb.cmd_ask(label(arm, rep), spacing_s=1.0, limit=limit, sample_name="heldout", only_ids=sample_ids())
    path = eb.answers_path(label(arm, rep))
    record = json.loads(path.read_text())
    record["pipeline_flags"] = tomllib.loads(FLAGS[arm].read_text())["pipeline"]   # what the container ran
    record["partd"] = {"arm": arm, "port": port, "flags_file": str(FLAGS[arm].relative_to(REPO)),
                       "images": json.loads((REPO / "research" / "paperb" / "partd_images.json").read_text())}
    path.write_text(json.dumps(record, indent=1) + "\n")
    return rc


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def cmd_score(reps: dict[str, int] | None = None, out: Path = OUT, on_arm: str = "on") -> int:
    """Pairs the off arm with the on arm; on_arm="off" pairs two runs of the off arm (the noise floor)."""
    reps = reps or {"off": 0, "on": 0}
    import paperb_pool as pool
    gold_sql = {int(q["question_id"]): q["SQL"] for q in json.loads(eb.CORRECTED.read_text())}
    conn = eb.bird_connection()
    golds: dict[int, list | None] = {}
    result: dict[str, dict] = {}
    for arm in ("off", "on"):
        answers = json.loads(eb.answers_path(label(on_arm if arm == "on" else arm, reps[arm])).read_text())["answers"]
        rows = {}
        for key, a in answers.items():
            qid = int(key)
            if qid not in golds:
                golds[qid] = eb.run_sqlite(a["db_id"], gold_sql[qid])
            sql = a.get("executed_sql")
            # the product names a connected database's tables cn_<id>.<table>; Paper A's scorer strips that
            # (eval_bird.unqualify) and runs it in the BIRD schema, as pool.run does
            res = pool.run(conn, a["db_id"], eb.unqualify(sql) if sql else None)
            errs = [e or {} for e in a.get("errors") or []]
            # T2b: a NOT_ANSWERABLE left unconfirmed because no attempt remained also ends the question unanswered;
            # one followed by SQL that ran (the confirmation succeeded) does not
            not_answerable = (any(e.get("category") == "no_data_source" for e in errs)
                              or bool(errs and errs[-1].get("category") == "not_answerable_unconfirmed" and not sql))
            rows[qid] = {"correct": bool(golds[qid] is not None and res is not None
                                         and eb.rows_match_relaxed(res[1], golds[qid], norm=eb.norm_v2)),
                         "not_answerable": not_answerable, "ran": res is not None,
                         "confirmations": sum(e.get("category") == "not_answerable_unconfirmed" for e in errs)}
        result[arm] = rows
    ids = sorted(set(result["off"]) & set(result["on"]))
    off, on = result["off"], result["on"]
    b = sum(1 for i in ids if on[i]["correct"] and not off[i]["correct"])
    c = sum(1 for i in ids if off[i]["correct"] and not on[i]["correct"])
    summary = {"questions_in_both": len(ids),
               "accuracy": {arm: round(sum(result[arm][i]["correct"] for i in ids) / max(1, len(ids)), 4) for arm in ("off", "on")},
               "paired": {"on_only_correct": b, "off_only_correct": c, "p_mcnemar": round(mcnemar_exact(b, c), 4)},
               "false_not_answerable": {"on": sum(on[i]["not_answerable"] for i in ids),
                                        "off": sum(off[i]["not_answerable"] for i in ids)},
               "t2b_confirmations_on": sum(on[i].get("confirmations", 0) for i in ids),
               "bar": "false NOT_ANSWERABLE <= 1% of questions; no significant accuracy drop"}
    summary["false_not_answerable_rate_on"] = round(summary["false_not_answerable"]["on"] / max(1, len(ids)), 4)
    out.write_text(json.dumps({"seed": SEED, "n": N, "reps": reps, "on_slot_arm": on_arm, "summary": summary, "per_question": result}, indent=1, default=str) + "\n")
    print(json.dumps(summary, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask")
    a.add_argument("--arm", choices=["off", "on"], required=True)
    a.add_argument("--port", type=int, required=True)
    a.add_argument("--limit", type=int)
    a.add_argument("--rep", type=int, default=0, help="a repeat of the same arm (noise floor), saved as _r<rep>")
    sc = sub.add_parser("score")
    sc.add_argument("--off-rep", type=int, default=0)
    sc.add_argument("--on-rep", type=int, default=0)
    sc.add_argument("--out", type=Path, default=OUT)
    sc.add_argument("--on-arm", choices=["on", "off"], default="on", help="off: pair two off runs (noise floor)")
    args = ap.parse_args()
    if args.cmd == "ask":
        return cmd_ask(args.arm, args.port, args.limit, args.rep)
    return cmd_score({"off": args.off_rep, "on": args.on_rep}, args.out, args.on_arm)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
scripts/annotate_bird.py
Blind annotation for protocol v2 §5 (docs/research/evidence_tiers_study.md). No model calls.

    python3 scripts/annotate_bird.py errors  --scores S --label L    worksheet: every wrong answer, for error types
    python3 scripts/annotate_bird.py answers --scores S --label L    worksheet: 60 random labelled answers, for the
                                                                      answer label's validation
    python3 scripts/annotate_bird.py owner   --worksheet W            the owner's random 20% of a worksheet
    python3 scripts/annotate_bird.py kappa   --a A --b B --field F    Cohen's kappa between two annotators
    python3 scripts/annotate_bird.py agreement --scores S --worksheet W   automatic vs manual answer label

Blind: a worksheet shows the question as asked, the hint, the gold SQL and its first rows, the
predicted SQL and its first rows, and the answer text before any repair. It never shows a signal,
a judge verdict, self-consistency or the automatic answer label, so annotations cannot lean on the
signals under test. Items are shuffled (seed) so their order carries no information either.
The annotator fills `annotation` in the worksheet file; for errors one of ERROR_TYPES, for answers
"right" or "wrong".
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import eval_bird as eb  # noqa: E402

ERROR_TYPES = ("join/fan-out", "filter value", "column meaning", "aggregation", "time window", "ordering/top-k",
               "output format only", "residual gold error", "hint contradicts data", "other")
ANSWER_SAMPLE = 60          # noqa: literals - protocol v2 §5
OWNER_SHARE = 0.2           # noqa: literals - protocol v2 §5
PREVIEW = 5                 # noqa: literals - rows shown per result


def _rows(rows: list[tuple] | None) -> list[list[str]] | None:
    return None if rows is None else [[str(v) for v in r] for r in rows[:PREVIEW]]


def blind_item(qid: object, db_id: str, a: dict, gold_sql: str, gold: list[tuple] | None,
               pred: list[tuple] | None) -> dict:
    """One worksheet item: what an annotator may see, and nothing about the signals."""
    return {"question_id": qid, "db_id": db_id, "question": a.get("question_asked"),
            "gold_sql": gold_sql, "gold_rows": _rows(gold), "gold_row_count": len(gold or []),
            "predicted_sql": a.get("executed_sql"), "predicted_rows": _rows(pred),
            "predicted_row_count": None if pred is None else len(pred),
            "answer_text": eb.text_before_repair(a), "annotation": None, "note": ""}


def _items(scores: Path, label: str, pick) -> list[dict]:
    rows = [r for r in json.loads(scores.read_text())["rows"] if not r.get("excluded") and not r.get("gold_failed")]
    answers = json.loads(eb.answers_path(label).read_text())["answers"]
    report = json.loads(scores.read_text())["report"]
    gold_file = REPO / report["gold_file"] if report.get("gold_file") else None
    gold_sql = ({int(q["question_id"]): q["SQL"] for q in json.loads(gold_file.read_text())} if gold_file else
                {e["id"]: e["gold"] for e in json.loads(eb.FANOUT.read_text())["questions"]})
    on_sqlite = gold_file is not None
    conn = eb.bird_connection()
    out = []
    for r in pick(rows):
        qid = r["question_id"]
        a = answers[str(qid)]
        g_sql = gold_sql[qid]
        gold = eb.run_sqlite(r["db_id"], g_sql) if on_sqlite else eb._run(conn, r["db_id"], g_sql)
        pred = eb._run(conn, r["db_id"], eb.unqualify(a["executed_sql"])) if a.get("executed_sql") else None
        out.append(blind_item(qid, r["db_id"], a, g_sql, gold, pred))
    random.Random(eb.SAMPLE_SEED).shuffle(out)
    return out


def write(items: list[dict], path: Path, kind: str, scores: Path) -> None:
    path.write_text(json.dumps({"kind": kind, "scores": str(scores), "choices": list(ERROR_TYPES) if kind == "errors"
                                else ["right", "wrong"], "items": items}, indent=1) + "\n")
    print(f"{len(items)} items -> {path}")


def kappa(a: list[str], b: list[str]) -> float | None:
    """Cohen's kappa for two annotators' labels on the same items."""
    n = len(a)
    if not n:
        return None
    observed = sum(x == y for x, y in zip(a, b)) / n
    cats = set(a) | set(b)
    expected = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("errors", "answers"):
        p = sub.add_parser(name)
        p.add_argument("--scores", type=Path, required=True)
        p.add_argument("--label", required=True)
        p.add_argument("--out", type=Path)
    p = sub.add_parser("owner")
    p.add_argument("--worksheet", type=Path, required=True)
    p = sub.add_parser("kappa")
    p.add_argument("--a", type=Path, required=True)
    p.add_argument("--b", type=Path, required=True)
    p = sub.add_parser("agreement")
    p.add_argument("--scores", type=Path, required=True)
    p.add_argument("--worksheet", type=Path, required=True)
    args = ap.parse_args()
    if args.cmd == "errors":
        items = _items(args.scores, args.label, lambda rows: [r for r in rows if not r["correct"]])
        write(items, args.out or eb.EVAL / f"bird_worksheet_errors_{args.label}.json", "errors", args.scores)
    elif args.cmd == "answers":
        def pick(rows):
            labelled = sorted((r for r in rows if r.get("answer_correct_before") is not None), key=lambda r: str(r["question_id"]))
            return random.Random(eb.SAMPLE_SEED).sample(labelled, min(ANSWER_SAMPLE, len(labelled)))
        write(_items(args.scores, args.label, pick), args.out or eb.EVAL / f"bird_worksheet_answers_{args.label}.json",
              "answers", args.scores)
    elif args.cmd == "owner":
        w = json.loads(args.worksheet.read_text())
        chosen = random.Random(eb.SAMPLE_SEED + 1).sample(w["items"], max(1, round(OWNER_SHARE * len(w["items"]))))
        blank = [{**i, "annotation": None, "note": ""} for i in chosen]
        out = args.worksheet.with_name(args.worksheet.stem + "_owner.json")
        out.write_text(json.dumps({**w, "items": blank}, indent=1) + "\n")
        print(f"{len(blank)} items for the owner -> {out}")
    elif args.cmd == "kappa":
        a = {i["question_id"]: i["annotation"] for i in json.loads(args.a.read_text())["items"] if i["annotation"]}
        b = {i["question_id"]: i["annotation"] for i in json.loads(args.b.read_text())["items"] if i["annotation"]}
        both = sorted(set(a) & set(b), key=str)
        k = kappa([a[q] for q in both], [b[q] for q in both])
        print(json.dumps({"items": len(both), "agree": sum(a[q] == b[q] for q in both),
                          "kappa": round(k, 3) if k is not None else None}, indent=1))
    else:
        auto = {r["question_id"]: r["answer_correct_before"] for r in json.loads(args.scores.read_text())["rows"]}
        manual = {i["question_id"]: i["annotation"] == "right" for i in json.loads(args.worksheet.read_text())["items"]
                  if i["annotation"]}
        both = sorted(set(auto) & set(manual), key=str)
        agree = sum(auto[q] == manual[q] for q in both)
        print(json.dumps({"items": len(both), "agree": agree, "agreement": round(agree / len(both), 3) if both else None,
                          "rule_kept": bool(both) and agree / len(both) >= 0.9}, indent=1))   # noqa: literals - protocol v2 §5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

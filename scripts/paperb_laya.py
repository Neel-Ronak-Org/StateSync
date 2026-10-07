#!/usr/bin/env python3
"""
scripts/paperb_laya.py
Paper B's Laya (docs/research/detect_then_fallback_study.md v2, §5), with the verifier pilot's code
(research/verifier/laya_verifier.py) and the pinned checkpoint (convaiinnovations/laya, typed-decisions,
revision 55cf4c4e):

  zeroshot   the checkpoint as is (temperature 1): P(correct) for every distinct pool candidate.
  finetune   in each rotating round (paperb_folds.rounds): the pilot's recipe (RLCD + cross-entropy,
             effective batch 64, correct and wrong weighted equally, temperature fitted on 10% of the
             training questions) for ONE epoch on the real regime (the training questions' pool
             candidates, deduplicated by (question, SQL)); then P(correct) for every distinct candidate
             of the round's calibration and test folds. Checkpoints go to research/verifier/runs/
             (git-ignored), scores to docs/eval/paperb_scores_laya_{zeroshot,finetuned_rotation}.json.

Input: the same question, SQL and result text as the ModernBERT verifier, in Laya's QUESTION / SQL /
RESULT state. Resumable by round. The Mac's GPU should run nothing else meanwhile. Run with the Python
that has laya 0.3.23 and torch (the verifier venv):

    <verifier-venv>/bin/python scripts/paperb_laya.py zeroshot
    <verifier-venv>/bin/python scripts/paperb_laya.py finetune
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[0]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "research" / "verifier"))
from paperb_folds import folds, rounds  # noqa: E402
from paperb_verifiers import EVAL, result_text  # noqa: E402

EPOCHS = 1                  # noqa: literals - pre-registered (v2 §5): about the pilot's number of updates
OBJECTIVE = "rlcd"
RUNS = REPO / "research" / "verifier" / "runs"


def row(q: dict, c: dict) -> dict:
    return {"qid": q["question_id"], "question": q["question"], "sql": c["sql"] or "(none)",
            "result": result_text(c["sql"], c["preview"]), "correct": bool(c["correct"])}


def distinct(pool_questions: list[dict], ids: set[int] | None) -> tuple[list[dict], list[tuple[str, int]]]:
    """Distinct (question, SQL, result) rows to score, and (key "qid|i", row index) for every candidate."""
    rows: list[dict] = []
    index: dict[tuple, int] = {}
    keys: list[tuple[str, int]] = []
    for q in pool_questions:
        if ids is not None and q["question_id"] not in ids:
            continue
        for i, c in enumerate(q["candidates"]):
            r = row(q, c)
            k = (r["question"], r["sql"], r["result"])
            if k not in index:
                index[k] = len(rows)
                rows.append(r)
            keys.append((f"{q['question_id']}|{i}", index[k]))
    return rows, keys


def cmd_zeroshot(pool: list[dict]) -> int:
    import laya_verifier as lv
    out = EVAL / "paperb_scores_laya_zeroshot.json"
    rows, keys = distinct(pool, None)
    t0 = time.monotonic()
    probs, ms = lv.score_rows(None, rows)
    scores = {k: (round(probs[i], 5) if probs[i] is not None else None) for k, i in keys}
    out.write_text(json.dumps({"model": f"{lv.MODEL_ID}/{lv.SUBFOLDER}@{lv.REVISION}", "temperature": 1.0,
                               "question": lv.QUESTION, "scored_texts": len(rows),
                               "dropped": sum(p is None for p in probs), "ms_per_check": round(ms, 2),
                               "minutes": round((time.monotonic() - t0) / 60, 1), "test": scores}, indent=1) + "\n")
    print(f"zero-shot: {len(rows)} distinct candidates, {sum(p is None for p in probs)} dropped, {ms:.1f} ms each")
    return 0


def cmd_finetune(pool: list[dict], max_rounds: int | None = None) -> int:
    import laya_verifier as lv
    out = EVAL / "paperb_scores_laya_finetuned_rotation.json"
    record = json.loads(out.read_text()) if out.exists() else {
        "model": f"{lv.MODEL_ID}/{lv.SUBFOLDER}@{lv.REVISION}", "objective": OBJECTIVE, "epochs": EPOCHS,
        "scheme": "rotation", "rounds": {}, "test": {}, "calib": {}}
    questions = {q["question_id"]: q for q in pool}
    done_now = 0
    for rnd in rounds(folds(pool)):
        r = str(rnd["round"])
        if r in record["rounds"]:
            continue
        if max_rounds is not None and done_now >= max_rounds:
            break
        done_now += 1
        seen: set[tuple[int, str]] = set()
        train = []
        for qid in sorted(rnd["train"]):
            q = questions[qid]
            for c in q["candidates"]:
                if (qid, c["sql"] or "") not in seen:
                    seen.add((qid, c["sql"] or ""))
                    train.append(row(q, c))
        t0 = time.monotonic()
        ckpt = lv.train_rows(train, OBJECTIVE, EPOCHS, RUNS / f"paperb_laya_r{r}")
        train_min = round((time.monotonic() - t0) / 60, 1)
        rows, keys = distinct(pool, rnd["calib"] | rnd["test"])
        probs, ms = lv.score_rows(str(ckpt), rows)
        record["calib"][r] = {}
        for k, i in keys:
            p = round(probs[i], 5) if probs[i] is not None else None
            if int(k.split("|")[0]) in rnd["test"]:
                record["test"][k] = p
            else:
                record["calib"][r][k] = p
        record["rounds"][r] = {"train_questions": len(rnd["train"]), "train_rows": len(train),
                               "train_correct": sum(t["correct"] for t in train), "scored_texts": len(rows),
                               "dropped": sum(p is None for p in probs), "train_minutes": train_min,
                               "ms_per_check": round(ms, 2), "checkpoint": str(ckpt.relative_to(REPO))}
        out.write_text(json.dumps(record, indent=1) + "\n")
        print(f"round {r}: {record['rounds'][r]}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["zeroshot", "finetune"])
    ap.add_argument("--max-rounds", type=int, help="finetune: stop after this many new rounds (task time limits)")
    args = ap.parse_args()
    pool = json.loads((EVAL / "paperb_pool.json").read_text())["questions"]
    return cmd_zeroshot(pool) if args.cmd == "zeroshot" else cmd_finetune(pool, args.max_rounds)


if __name__ == "__main__":
    raise SystemExit(main())

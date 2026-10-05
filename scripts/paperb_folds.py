#!/usr/bin/env python3
"""
scripts/paperb_folds.py
Paper B's folds and rounds (docs/research/detect_then_fallback_study.md v2, §5), with no torch import so
the analysis and the approved-examples arms can use them from any Python.

folds(): the assignment committed in 88ca99c (paperb_verifiers.folds): 5 folds stratified by database,
seed 20261005. rounds(): round r tests fold r, calibrates on fold (r + 1) mod 5 and trains on the other
three.
"""

from __future__ import annotations

import random

SEED = 20261005
FOLDS = 5                                     # noqa: literals - pre-registered


def folds(questions: list[dict], scheme: str = "5fold") -> dict[int, int]:
    """question_id -> fold. 5-fold: stratified by database. lodo: one fold per database."""
    by_db: dict[str, list[int]] = {}
    for q in questions:
        by_db.setdefault(q["db_id"], []).append(q["question_id"])
    if scheme == "lodo":
        return {qid: k for k, db in enumerate(sorted(by_db)) for qid in by_db[db]}
    rng = random.Random(SEED)
    out: dict[int, int] = {}
    offset = 0
    for db in sorted(by_db):
        ids = sorted(by_db[db])
        rng.shuffle(ids)
        for i, qid in enumerate(ids):
            out[qid] = (i + offset) % FOLDS
        offset += len(ids)
    return out


def rounds(fold_of: dict[int, int]) -> list[dict[str, set[int]]]:
    """Per round r: the question ids to test, to calibrate on and to train on."""
    out = []
    for r in range(FOLDS):
        calib = (r + 1) % FOLDS
        out.append({"round": r,
                    "test": {q for q, f in fold_of.items() if f == r},
                    "calib": {q for q, f in fold_of.items() if f == calib},
                    "train": {q for q, f in fold_of.items() if f not in (r, calib)}})
    return out


def heldout_questions() -> list[dict]:
    """The 393 held-out questions as {question_id, db_id}, in the held-out sample's order, as the pool
    lists them (paperb_pool.py). Mini-Dev repeats ids 137 and 138; one copy of each is kept."""
    import json
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import eval_bird as eb
    db_of = {}
    for q in eb.questions():
        db_of.setdefault(q["question_id"], q["db_id"])
    return [{"question_id": qid, "db_id": db_of[qid]} for qid in json.loads(eb.HELDOUT.read_text())["question_ids"]]

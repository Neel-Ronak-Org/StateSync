#!/usr/bin/env python3
"""
scripts/paperb_jev_retest.py
Jev's test-retest check for Paper B Part S: 200 distinct candidate texts drawn at random (seed 20261006) from
paperb_scores_jev_zeroshot.json are scored again with the same model, request and instructions
(scripts/paperb_hosted.py), into docs/eval/paperb_jev_retest.json, with the mean absolute difference, the share
within 0.05 and the Spearman correlation. Jev answers in steps of 0.01 and is not deterministic.

    python3 scripts/paperb_jev_retest.py
"""
from __future__ import annotations

import json
import random
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVAL = REPO / "docs" / "eval"
N, SEED = 200, 20261006          # noqa: literals - retest sample
URL = "https://opencode.ai/zen/v1/systemone"
INSTRUCTIONS = "Does this SQL query and its result correctly answer the question?"


def rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def main() -> int:
    rec = json.loads((EVAL / "paperb_scores_jev_zeroshot.json").read_text())
    key = (REPO / "secrets" / "opencode_api_key").read_text().strip()
    texts = random.Random(SEED).sample(sorted(rec["by_text"]), N)
    pairs = []
    for t in texts:
        body = json.dumps({"model": "jev-1.13", "state": t,
                           "questions": {"correct": {"type": "noul", "instructions": INSTRUCTIONS}}}).encode()
        req = urllib.request.Request(URL, data=body, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                                              "User-Agent": "statesync-research/1.0"})
        pairs.append((rec["by_text"][t], float(json.load(urllib.request.urlopen(req, timeout=60))["answers"]["correct"]["noul"])))
    a, b = [p[0] for p in pairs], [p[1] for p in pairs]
    ra, rb = rank(a), rank(b)
    ma, mb = sum(ra) / N, sum(rb) / N
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    sd = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    summary = {"n": N, "mean_abs_diff": round(sum(abs(x - y) for x, y in pairs) / N, 4),
               "within_0.05": round(sum(abs(x - y) <= 0.05 + 1e-9 for x, y in pairs) / N, 3),
               "spearman": round(cov / sd, 3), "same_side_of_0.5": round(sum((x >= 0.5) == (y >= 0.5) for x, y in pairs) / N, 3)}
    (EVAL / "paperb_jev_retest.json").write_text(json.dumps({"seed": SEED, "model": "jev-1.13", "summary": summary,
                                                            "pairs": pairs}, indent=1) + "\n")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

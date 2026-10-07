#!/usr/bin/env python3
"""
scripts/eval_semantic_router.py
Part D v3's semantic-router baseline (docs/specs/routing_docs_fixes.md §4): the cheapest standard family
of router. Each routing-set question is embedded with the product's own embedding model
(mxbai-embed-large, the MCP's Ollama, reached from inside Part D's MCP container), and takes the
majority class of its k nearest LABELLED DEV questions by cosine (ties: the nearest one's class). Test
questions see only dev neighbours; a dev question is scored leave-one-out. A conversation turn is
embedded as the front door's search text: the question, then the previous turn's standalone question.
A predicted class becomes a route as eval_routing scores it (either -> docs).

    python3 scripts/eval_semantic_router.py        writes docs/eval/routing_semantic.json and prints the summary
"""

from __future__ import annotations

import json
import math
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SET = REPO / "docs" / "eval" / "routing_set.json"
OUT = REPO / "docs" / "eval" / "routing_semantic.json"
CONTAINER = "statesync-mcp-paperb"
MODEL = "mxbai-embed-large"     # config/statesync.toml [retrieval] embedding_model
K = 5                           # noqa: literals - neighbours voting (fixed before the run)
CLASS_ROUTE = {"docs": "docs", "sql": "sql", "hybrid": "hybrid", "neither": "neither", "either": "docs"}

_SNIPPET = r"""
import json, os, sys, urllib.request
req = json.load(sys.stdin)
url = os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/") + "/api/embed"
out = []
for i in range(0, len(req["texts"]), 32):
    body = json.dumps({"model": req["model"], "input": req["texts"][i:i + 32]}).encode()
    r = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    out += json.loads(urllib.request.urlopen(r, timeout=300).read().decode())["embeddings"]
print(json.dumps(out))
"""


def embed(texts: list[str]) -> list[list[float]]:
    res = subprocess.run(["docker", "exec", "-i", CONTAINER, "python", "-c", _SNIPPET],
                         input=json.dumps({"model": MODEL, "texts": texts}), capture_output=True, text=True, timeout=3600)
    if res.returncode:
        raise SystemExit(f"embedding failed: {res.stderr.strip()[-400:]}")
    return json.loads(res.stdout)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1.0)


def main() -> int:
    rs = json.loads(SET.read_text())
    rows = [{"key": it["id"], "text": it["question"], "class": it["class"], "acceptable": it["acceptable"],
             "split": it["split"], "kind": "item"} for it in rs["items"]]
    for conv in rs["conversations"]:
        prev = None
        for t in conv["turns"]:
            text = t["question"] + (f" {prev}" if prev else "")
            rows.append({"key": f"{conv['id']}|{t['turn']}", "text": text, "class": t["class"], "acceptable": t["acceptable"],
                         "split": conv["split"], "kind": "turn"})
            prev = t["standalone"]
    vectors = embed([r["text"] for r in rows])
    dev = [(r, v) for r, v in zip(rows, vectors) if r["split"] == "dev"]
    results = {}
    for r, v in zip(rows, vectors):
        neighbours = sorted(((cosine(v, dv), dr["class"]) for dr, dv in dev if dr["key"] != r["key"]), reverse=True)[:K]
        votes = Counter(c for _, c in neighbours)
        top = max(votes.values())
        pred_class = next(c for _, c in neighbours if votes[c] == top)        # ties: the nearest one's class
        route = CLASS_ROUTE[pred_class]
        results[r["key"]] = {"split": r["split"], "kind": r["kind"], "gold": r["class"], "pred_class": pred_class,
                             "route": route, "correct": route in r["acceptable"]}
    summary = {}
    for split in ("dev", "test"):
        for kind in ("item", "turn"):
            sel = [x for x in results.values() if x["split"] == split and x["kind"] == kind]
            by = defaultdict(lambda: [0, 0])
            for x in sel:
                by[x["gold"]][0] += x["correct"]
                by[x["gold"]][1] += 1
            n = len(sel)
            summary[f"semantic|{split}|{kind}"] = {
                "n": n, "route_accuracy": round(sum(x["correct"] for x in sel) / n, 3) if n else None,
                "macro_class_accuracy": round(sum(a / b for a, b in by.values()) / len(by), 3) if by else None,
                "by_class": {c: f"{a}/{b}" for c, (a, b) in sorted(by.items())}}
            print(f"semantic|{split}|{kind}", json.dumps(summary[f"semantic|{split}|{kind}"]))
    OUT.write_text(json.dumps({"model": MODEL, "k": K, "summary": summary, "results": results}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

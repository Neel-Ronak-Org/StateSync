#!/usr/bin/env python3
"""
scripts/paperb_hosted.py
Paper B's hosted zero-shot decision model (docs/research/detect_then_fallback_study.md v2, §5): Jev
(typesafe-ai/jev, paid credits) through Vercel AI Gateway's TypeSafe-compatible endpoint. Each distinct
pool candidate gets one Boolean ("noul") question over the verifiers' input text; the answer is
P(correct). Resumable; the key is read from secrets/vercel_ai_gateway_key. (v2 runs zero-shot Laya
locally from the pinned checkpoint, scripts/paperb_laya.py, so hosted Laya is no longer offered here.)

    python3 scripts/paperb_hosted.py --model typesafe-ai/jev [--workers 3]
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paperb_verifiers import EVAL, REPO, render  # noqa: E402

URL = "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
INSTRUCTIONS = "Does this SQL query and its result correctly answer the question?"
RETRIES = 4                 # noqa: literals - 429 back-off attempts
SAVE_EVERY = 50             # noqa: literals - completions between saves


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", required=True, choices=["typesafe-ai/jev"])
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()
    key = (REPO / "secrets" / "vercel_ai_gateway_key").read_text().strip()
    pool = json.loads((EVAL / "paperb_pool.json").read_text())["questions"]
    out = EVAL / f"paperb_scores_{args.model.split('/')[-1]}_zeroshot.json"
    record = json.loads(out.read_text()) if out.exists() else {"model": args.model, "instructions": INSTRUCTIONS,
                                                               "by_text": {}, "scores": {}, "cost": 0.0}
    texts: dict[str, list[str]] = {}
    for q in pool:
        for i, c in enumerate(q["candidates"]):
            texts.setdefault(render(q["question"], c["sql"], c["preview"]), []).append(f"{q['question_id']}|{i}")
    todo = [t for t in texts if t not in record["by_text"]]
    print(f"{len(texts)} distinct candidates, {len(todo)} to score", flush=True)
    lock = threading.Lock()

    def one(text: str) -> tuple[str, float | None, float]:
        body = json.dumps({"model": args.model, "state": text,
                           "questions": {"correct": {"type": "noul", "instructions": INSTRUCTIONS}}}).encode()
        for attempt in range(RETRIES):
            req = urllib.request.Request(URL, data=body, headers={"Authorization": "Bearer " + key,
                                                                  "Content-Type": "application/json",
                                                                  "User-Agent": "statesync-research/1.0"})
            try:
                r = json.load(urllib.request.urlopen(req, timeout=60))
                cost = float(((r.get("provider_metadata") or {}).get("gateway") or {}).get("cost") or 0)
                return text, float(r["answers"]["correct"]["noul"]), cost
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < RETRIES - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                print(f"HTTP {e.code}: {(e.read() or b'')[:200]!r}", flush=True)
                return text, None, 0.0
            except Exception as e:  # noqa: BLE001
                print(f"{type(e).__name__}: {str(e)[:200]}", flush=True)
                return text, None, 0.0
        return text, None, 0.0

    done = 0
    with ThreadPoolExecutor(args.workers) as ex:
        for fut in as_completed([ex.submit(one, t) for t in todo]):
            text, p, cost = fut.result()
            with lock:
                done += 1
                if p is not None:
                    record["by_text"][text] = p
                    record["cost"] += cost
                if done % SAVE_EVERY == 0 or done == len(todo):
                    record["scores"] = {k: record["by_text"][t] for t, ks in texts.items() if t in record["by_text"] for k in ks}
                    out.write_text(json.dumps(record, indent=1) + "\n")
                    print(f"{done}/{len(todo)}; cost ${record['cost']:.4f}", flush=True)
    record["scores"] = {k: record["by_text"][t] for t, ks in texts.items() if t in record["by_text"] for k in ks}
    out.write_text(json.dumps(record, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

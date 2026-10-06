#!/usr/bin/env python3
"""
scripts/paperb_hosted.py
Paper B's hosted zero-shot decision model (docs/research/detect_then_fallback_study.md v2, §5): Jev
(paid credits) through a TypeSafe-compatible "systemone" endpoint: Vercel AI Gateway (typesafe-ai/jev, key
secrets/vercel_ai_gateway_key) or OpenCode Zen (jev-1.13-free, then jev-1.13 if the free tier rate-limits;
key secrets/opencode_api_key; owner's choice, 2026-10-06). The free tier rate-limited after about 250
candidates, so the scores come from jev-1.13 alone (one model for every score; the free model's partial
scores are kept apart in paperb_scores_jev_free_partial.json). Jev is not deterministic (the same text gave
0.58 and 0.63) and answers in steps of 0.01; each distinct text is asked once. Each distinct
pool candidate gets one Boolean ("noul") question over the verifiers' input text; the answer is
P(correct). Resumable; every score records the model that gave it. (v2 runs zero-shot Laya
locally from the pinned checkpoint, scripts/paperb_laya.py, so hosted Laya is no longer offered here.)

    python3 scripts/paperb_hosted.py --provider opencode --model jev-1.13-free [--workers 3] [--limit N]
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

PROVIDERS = {"vercel": ("https://ai-gateway.vercel.sh/typesafe/v1/systemone", "vercel_ai_gateway_key", ["typesafe-ai/jev"]),
             "opencode": ("https://opencode.ai/zen/v1/systemone", "opencode_api_key", ["jev-1.13-free", "jev-1.13"])}
INSTRUCTIONS = "Does this SQL query and its result correctly answer the question?"
RETRIES = 4                 # noqa: literals - 429 back-off attempts
USD_PER_M_INPUT = {"jev-1.13": 0.042, "jev-1.13-free": 0.0}   # noqa: literals - OpenCode Zen's price list (2026-10-06); output tokens free
SAVE_EVERY = 50             # noqa: literals - completions between saves


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--provider", choices=sorted(PROVIDERS), default="opencode")
    ap.add_argument("--model", required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, help="score at most this many new candidates (a smoke test)")
    args = ap.parse_args()
    url, key_file, models = PROVIDERS[args.provider]
    if args.model not in models:
        raise SystemExit(f"{args.provider} offers {models}")
    key = (REPO / "secrets" / key_file).read_text().strip()
    pool = json.loads((EVAL / "paperb_pool.json").read_text())["questions"]
    out = EVAL / "paperb_scores_jev_zeroshot.json"           # the name paperb_analyze.py reads
    record = json.loads(out.read_text()) if out.exists() else {"model": "jev", "instructions": INSTRUCTIONS,
                                                               "by_text": {}, "model_by_text": {}, "scores": {}, "cost": 0.0}
    record.setdefault("model_by_text", {})
    record.setdefault("providers", {})[args.model] = url
    texts: dict[str, list[str]] = {}
    for q in pool:
        for i, c in enumerate(q["candidates"]):
            texts.setdefault(render(q["question"], c["sql"], c["preview"]), []).append(f"{q['question_id']}|{i}")
    todo = [t for t in texts if t not in record["by_text"]][:args.limit]
    print(f"{len(texts)} distinct candidates, {len(todo)} to score", flush=True)
    lock = threading.Lock()

    def one(text: str) -> tuple[str, float | None, float]:
        body = json.dumps({"model": args.model, "state": text,
                           "questions": {"correct": {"type": "noul", "instructions": INSTRUCTIONS}}}).encode()
        for attempt in range(RETRIES):
            req = urllib.request.Request(url, data=body, headers={"Authorization": "Bearer " + key,
                                                                  "Content-Type": "application/json",
                                                                  "User-Agent": "statesync-research/1.0"})
            try:
                r = json.load(urllib.request.urlopen(req, timeout=60))
                gateway = float(((r.get("provider_metadata") or {}).get("gateway") or {}).get("cost") or 0)
                tokens = int((r.get("usage") or {}).get("input_tokens") or 0)
                cost = gateway or tokens * USD_PER_M_INPUT.get(args.model, 0.0) / 1e6   # OpenCode reports usage, not cost
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
                    record["model_by_text"][text] = args.model
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

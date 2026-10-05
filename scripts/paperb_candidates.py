#!/usr/bin/env python3
"""
scripts/paperb_candidates.py
Paper B candidate pool (docs/research/detect_then_fallback_study.md): standalone SQL candidates from
three Cheaper Inference models for the 393 held-out BIRD questions, 3 samples each, at the provider's
default sampling. The prompt gets the question's PostgreSQL schema, the hint and the question in the
wording Paper A's pipeline was asked. Resumable: the output is saved as calls complete, and finished
(question, model, sample) keys are skipped. Run with a Python that has `openai` (the scratchpad venv).

    python scripts/paperb_candidates.py [--workers 8] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_bird as eb  # noqa: E402

MODELS = ("deepseek-v4.1-flash", "qwen3-coder-30b-a3b-instruct", "glm-5.3-flash")
SAMPLES = 3                 # noqa: literals - pre-registered (detect_then_fallback_study.md)
MAX_TOKENS = 3000           # noqa: literals - room for models that reason before answering
BASE_URL = "https://api.cheaperinference.com/v1"
KEY_FILE = eb.REPO / "secrets" / "cheaperinference_api_key"
OUT = eb.EVAL / "paperb_candidates.json"
SAVE_EVERY = 20             # noqa: literals - completions between saves
SYSTEM = ("You write SQL for PostgreSQL. Answer with exactly one SELECT query in a ```sql code block "
          "and nothing else.")
FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.S | re.I)


def prompt(schema: str, question: str) -> str:
    """`question` is the text Paper A's pipeline was asked: the question, then "Hint: ..." when BIRD
    gives one (the answers' `evidence` field is the evidence tier, not the hint)."""
    return (f"Database schema (PostgreSQL; table(column type, ...)):\n{schema}\n\n"
            f"Question: {question}\n\n"
            "Write one PostgreSQL SELECT query that answers the question.")


def extract_sql(text: str) -> str | None:
    m = FENCE.search(text or "")
    sql = (m.group(1) if m else (text or "")).strip().rstrip(";").strip()
    return sql if sql.lower().startswith(("select", "with")) else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    from openai import OpenAI
    client = OpenAI(api_key=KEY_FILE.read_text().strip(), base_url=BASE_URL, timeout=180, max_retries=2)
    asked = json.loads(eb.answers_path("heldout_gpt5mini").read_text())["answers"]
    ids = json.loads(eb.HELDOUT.read_text())["question_ids"]
    done = json.loads(OUT.read_text()) if OUT.exists() else {"models": list(MODELS), "samples": SAMPLES,
                                                               "system": SYSTEM, "candidates": {}}
    schemas: dict[str, str] = {}
    jobs = []
    for qid in ids:
        a = asked[str(qid)]
        db = a["db_id"]
        schemas.setdefault(db, eb.schema_text(db))
        for model in MODELS:
            for i in range(SAMPLES):
                key = f"{qid}|{model}|{i}"
                if key not in done["candidates"] or done["candidates"][key].get("error"):
                    jobs.append((key, model, prompt(schemas[db], a["question_asked"])))
    if args.limit:
        jobs = jobs[:args.limit]
    print(f"{len(jobs)} calls to make", flush=True)
    lock = threading.Lock()

    def one(key: str, model: str, user: str) -> tuple[str, dict]:
        t = time.monotonic()
        try:
            r = client.chat.completions.create(model=model, max_tokens=MAX_TOKENS,
                                               messages=[{"role": "system", "content": SYSTEM},
                                                         {"role": "user", "content": user}])
            text = r.choices[0].message.content or ""
            return key, {"sql": extract_sql(text), "raw": text[:4000], "served_model": r.model,
                         "tokens_in": getattr(r.usage, "prompt_tokens", None),
                         "tokens_out": getattr(r.usage, "completion_tokens", None),
                         "ms": int((time.monotonic() - t) * 1000)}
        except Exception as exc:
            return key, {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "ms": int((time.monotonic() - t) * 1000)}

    finished = 0
    with ThreadPoolExecutor(args.workers) as pool:
        futures = [pool.submit(one, *job) for job in jobs]
        for fut in as_completed(futures):
            key, rec = fut.result()
            with lock:
                done["candidates"][key] = rec
                finished += 1
                if finished % SAVE_EVERY == 0 or finished == len(jobs):
                    OUT.write_text(json.dumps(done, indent=1) + "\n")
                    errors = sum(1 for v in done["candidates"].values() if v.get("error"))
                    print(f"{finished}/{len(jobs)} done; {errors} errors in file", flush=True)
    OUT.write_text(json.dumps(done, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

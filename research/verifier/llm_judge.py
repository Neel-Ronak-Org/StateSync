"""
research/verifier/llm_judge.py
The LLM-judge baseline (README.md, signal 4): gpt-oss-120b on Groq sees exactly what the small
verifier sees (QUESTION, SQL, RESULT summary) and rates 0-100 how likely the SQL is correct.
P(correct) = rating / 100. Spaced for the free tier; the key is read from secrets/ and never printed.

    python llm_judge.py --data out/test_llm_gpt-oss-120b.jsonl --out out/score_judge_llm_gptoss.json
"""

from __future__ import annotations

import argparse
import json
import re
import time

from data import REPO
from llm_candidates import ask

HERE = __import__("pathlib").Path(__file__).resolve().parent
SPACING_S = 8
PROMPT = """You check whether a SQL query correctly answers a question about a PostgreSQL database
(schema fiben, public-company financial data). You see the question, the SQL, and a summary of
its result. Judge whether the SQL computes exactly what the question asks.

QUESTION: {question}
SQL: {sql}
RESULT: {result}

Reply with only an integer from 0 to 100: the probability, in percent, that the SQL is correct."""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="openai/gpt-oss-120b")
    args = ap.parse_args()
    key = (REPO / "secrets" / "groq_api_key").read_text().strip()
    rows = [json.loads(l) for l in open(HERE / args.data) if l.strip()]
    rows = [r for r in rows if r.get("runs", True)]
    out_path = HERE / args.out
    prior = json.loads(out_path.read_text()) if out_path.exists() else {"rows": []}
    done = {(r["qid"], r["sql"]) for r in prior["rows"]}
    scored, tokens, t_calls = prior["rows"], 0, []
    for r in rows:
        if (r["qid"], r["sql"]) in done:
            continue
        t0 = time.perf_counter()
        text, usage = ask(key, args.model, PROMPT.format(question=r["question"], sql=r["sql"], result=r["result"]))
        t_calls.append(time.perf_counter() - t0)
        tokens += usage.get("total_tokens", 0)
        m = re.search(r"\d{1,3}", text or "")
        p = min(100, int(m[0])) / 100 if m else 0.5
        scored.append({"qid": r["qid"], "sql": r["sql"], "correct": r["correct"], "p_correct": p, "raw": (text or "")[:40],
                       "source": r.get("source"), "operator": r.get("operator"), "tokens": usage.get("total_tokens")})
        json.dump({"model": args.model, "data": args.data, "rows": scored}, open(out_path, "w"), indent=1)
        time.sleep(SPACING_S)
    if t_calls:
        t_calls.sort()
        print(f"judged {len(t_calls)} new; median call {t_calls[len(t_calls) // 2]:.2f} s; {tokens} tokens")


if __name__ == "__main__":
    main()

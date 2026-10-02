"""
research/verifier/llm_candidates.py
The primary test set (README.md, `test_llm`): SQL a real LLM writes for the TEST questions,
labelled by running it against the gold result.

The model sees the question and the schema of the tables the gold SQL uses plus 3 distractor
tables (oracle schema linking: this tests SQL logic, not table selection). One call per
question, spaced to stay inside Groq's free tier (8K tokens/min). The key is read from
secrets/groq_api_key and never printed.

    python llm_candidates.py [--model openai/gpt-oss-120b] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
import urllib.error
import urllib.request

from pglast import ast, parse_sql

from data import DB, OUT, REPO, SEED, label

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DISTRACTORS = 3
SPACING_S = 15
PROMPT = """You write one PostgreSQL query. Tables are in schema fiben; always qualify them (fiben.table).
Return only the SQL, no explanation, no code fence.

Schema:
{schema}

Question: {question}"""


def tables_of(sql: str) -> list[str]:
    def walk(node):
        if isinstance(node, ast.RangeVar):
            yield node.relname
        if isinstance(node, ast.Node):
            for name in node:
                value = getattr(node, name)
                for child in (value if isinstance(value, tuple) else (value,)):
                    yield from walk(child)
    return sorted(set(walk(parse_sql(sql)[0].stmt)))


def schema_text(db: DB, tables: list[str]) -> str:
    lines = []
    for t in tables:
        cols = db.run("SELECT column_name, data_type FROM information_schema.columns "
                      f"WHERE table_schema = 'fiben' AND table_name = '{t}' ORDER BY ordinal_position")
        lines.append(f"fiben.{t}(" + ", ".join(f"{c} {ty}" for c, ty in (cols[1] if cols else [])) + ")")
    return "\n".join(lines)


def ask(key: str, model: str, prompt: str) -> tuple[str, dict]:
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0.2, "max_completion_tokens": 2000}).encode()
    req = urllib.request.Request(GROQ_URL, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": "statesync-research"})
    for attempt in range(4):
        try:
            reply = json.loads(urllib.request.urlopen(req, timeout=120).read())
            return reply["choices"][0]["message"]["content"], reply.get("usage", {})
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 3:
                time.sleep(30 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def clean_sql(text: str) -> str:
    text = re.sub(r"^```(?:sql)?|```$", "", text.strip(), flags=re.M).strip()
    return text.rstrip(";").strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="openai/gpt-oss-120b")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    key = (REPO / "secrets" / "groq_api_key").read_text().strip()
    db = DB()
    parts = json.loads((OUT / "items.json").read_text())
    all_tables = [r[0] for r in db.run("SELECT table_name FROM information_schema.tables WHERE table_schema = 'fiben'")[1]]
    rng = random.Random(SEED)
    out_path = OUT / f"test_llm_{args.model.split('/')[-1]}.jsonl"
    done = {json.loads(l)["qid"] for l in out_path.read_text().splitlines()} if out_path.exists() else set()
    tokens = 0
    for it in parts["test"][: args.limit]:
        if it["qid"] in done:
            continue
        gold_tables = tables_of(it["gold_sql"])
        tables = sorted(set(gold_tables) | set(rng.sample([t for t in all_tables if t not in gold_tables], DISTRACTORS)))
        text, usage = ask(key, args.model, PROMPT.format(schema=schema_text(db, tables), question=it["question"]))
        tokens += usage.get("total_tokens", 0)
        sql = clean_sql(text)
        lab = label(db, it, sql)
        row = {"qid": it["qid"], "question": it["question"], "sql": sql, "source": "llm", "model": args.model,
               "runs": lab is not None, **({k: lab[k] for k in ("result", "correct", "rows")} if lab else {})}
        with open(out_path, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"{it['qid']:4d} runs={row['runs']} correct={row.get('correct')} tokens={usage.get('total_tokens')}", flush=True)
        time.sleep(SPACING_S)
    print(f"done; {tokens} tokens this run")


if __name__ == "__main__":
    main()

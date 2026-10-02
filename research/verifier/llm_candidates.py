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
{extra}
Question: {question}"""
FK_LINE = re.compile(r"ALTER TABLE (\w+) ADD CONSTRAINT \w+ FOREIGN KEY \((\w+)\) REFERENCES (\w+) \((\w+)\)")
SAMPLES, EXAMPLES = 2, 3


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


def fk_hints(tables: list[str]) -> list[str]:
    """FIBEN's own foreign keys among these tables (its DDL declares them; demo/fiben_db.sh does not create them)."""
    ddl = (REPO / "data" / "external" / "fiben" / "FIBEN.sql").read_text()
    wanted = set(tables)
    return sorted(f"fiben.{a.lower()}.{c.lower()} -> fiben.{b.lower()}.{d.lower()}"
                  for a, c, b, d in FK_LINE.findall(ddl) if a.lower() in wanted and b.lower() in wanted)


def sample_values(db: DB, tables: list[str]) -> list[str]:
    lines = []
    for t in tables:
        cols = db.run("SELECT column_name FROM information_schema.columns WHERE table_schema = 'fiben' "
                      f"AND table_name = '{t}' AND data_type IN ('character varying', 'text') ORDER BY ordinal_position")
        for (c,) in (cols[1] if cols else []):
            vals = db.run(f'SELECT DISTINCT "{c}" FROM fiben."{t}" WHERE "{c}" IS NOT NULL LIMIT {SAMPLES}')
            if vals and vals[1]:
                lines.append(f"fiben.{t}.{c}: " + ", ".join(repr(str(v[0])[:40]) for v in vals[1]))
    return lines


def examples_for(item: dict, train: list[dict]) -> list[dict]:
    """The approved examples a workspace would hold: train questions sharing the most tables."""
    mine = set(tables_of(item["gold_sql"]))
    pool = [t for t in train if t["group"] != item["group"]]  # never the question itself or a paraphrase of it
    ranked = sorted(pool, key=lambda t: (-len(mine & set(tables_of(t["gold_sql"]))), t["qid"]))
    return ranked[:EXAMPLES]


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
    ap.add_argument("--part", choices=("test", "train"), default="test",
                    help="train: candidates for the train questions (README addition 3: training variant B)")
    ap.add_argument("--context", choices=("bare", "rich"), default="rich",
                    help="rich (README deviation 2): + FIBEN's foreign keys, sample values, 3 approved train examples")
    args = ap.parse_args()
    key = (REPO / "secrets" / "groq_api_key").read_text().strip()
    db = DB()
    parts = json.loads((OUT / "items.json").read_text())
    all_tables = [r[0] for r in db.run("SELECT table_name FROM information_schema.tables WHERE table_schema = 'fiben'")[1]]
    rng = random.Random(SEED)
    suffix = "" if args.context == "bare" else "_rich"
    out_path = OUT / f"{args.part}_llm_{args.model.split('/')[-1]}{suffix}.jsonl"
    done = {json.loads(l)["qid"] for l in out_path.read_text().splitlines()} if out_path.exists() else set()
    tokens = 0
    for it in parts[args.part][: args.limit]:
        if it["qid"] in done:
            continue
        gold_tables = tables_of(it["gold_sql"])
        tables = sorted(set(gold_tables) | set(rng.sample([t for t in all_tables if t not in gold_tables], DISTRACTORS)))
        extra = ""
        if args.context == "rich":
            fks, samples = fk_hints(tables), sample_values(db, tables)
            shots = examples_for(it, parts["train"])
            extra = ("\nForeign keys:\n" + "\n".join(fks) + "\n" if fks else "") + \
                    ("\nSample values:\n" + "\n".join(samples) + "\n" if samples else "") + \
                    "\nApproved examples from this workspace:\n" + \
                    "\n".join(f"Q: {e['question']}\nSQL: {e['gold_sql']}" for e in shots) + "\n"
        text, usage = ask(key, args.model, PROMPT.format(schema=schema_text(db, tables), extra=extra, question=it["question"]))
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

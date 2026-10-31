#!/usr/bin/env python3
"""
scripts/paperb_examples.py
Paper B, QS3 (docs/research/detect_then_fallback_study.md v2, §5): approved examples against a verifier.
gpt-oss-120b (Cheaper Inference, provider default sampling, reasoning effort low) writes SQL for the 393
held-out BIRD questions with the standalone candidates' prompt (paperb_candidates.py), in three arms,
three samples per question per arm:
  E0  schema, hint, question;
  E1  the same, plus up to 3 approved examples, chosen as StateSync chooses them
      (mcp/tools/workspace_tools.py: Jaccard similarity of content words, at least 0.3, at most 3, an exact
      match left out; the functions are read from that file, not copied);
  E2  the same, plus the 3 most similar approved examples with no minimum similarity (protocol change 4).
Examples come from the same database's questions in the round's three training folds
(paperb_folds.rounds), comparing question texts without their hints. A question serves as an example only
if its PostgreSQL reference SQL is correct under the corrected labels (the `reference` rows of
paperb_mutations.json). Examples are shown as the product shows them (orchestrator/workspace_context.py).
`label` then runs every candidate on the PostgreSQL copy and labels it against the corrected gold on SQLite
with Paper A's relaxed rule; a candidate with no SQL, or one that fails, is wrong. Both steps are resumable.

    python3 scripts/paperb_examples.py generate [--workers 8] [--limit N]
    python3 scripts/paperb_examples.py label
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_bird as eb  # noqa: E402
import paperb_candidates as pc  # noqa: E402
import paperb_folds as pf  # noqa: E402

OUT = eb.EVAL / "paperb_examples.json"
MODEL = "gpt-oss-120b"
EFFORT = "low"
SAMPLES = 3                 # noqa: literals - pre-registered (v2 §5, QS3)
ARMS = ("E0", "E1", "E2")
MAX_EXAMPLES = 3            # noqa: literals - [workspaces] context_max_examples (config/statesync.toml)
MIN_SIMILARITY = 0.3        # noqa: literals - [workspaces] example_min_similarity (config/statesync.toml)
RETRIES = 4                 # noqa: literals - 429/5xx back-off attempts
SAVE_EVERY = 20             # noqa: literals - completions between saves
WORKSPACE_TOOLS = eb.REPO / "mcp" / "tools" / "workspace_tools.py"


def product_similarity():
    """`normalize_question`, `question_tokens`, `similarity` and `_STOPWORDS` from the MCP's own source, so
    the selection is the product's (the module itself imports MCP-only packages)."""
    tree = ast.parse(WORKSPACE_TOOLS.read_text())
    keep = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ("normalize_question", "question_tokens", "similarity"):
            keep.append(node)
        elif isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "_STOPWORDS" for t in node.targets):
            keep.append(node)
    ns: dict = {"re": re}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(WORKSPACE_TOOLS), "exec"), ns)  # noqa: S102 - the repo's own code
    return ns["normalize_question"], ns["similarity"]


def without_hint(asked: str) -> str:
    return asked.split("\nHint:", 1)[0].strip()


def examples_block(examples: list[dict]) -> str:
    """As orchestrator/workspace_context.format_context shows approved examples."""
    return "Approved examples of similar questions and their SQL:\n" + "\n".join(
        f"  Q: {e['question']}\n  SQL: {e['sql']}" for e in examples)


def prompt(schema: str, asked: str, examples: list[dict] | None) -> str:
    base = pc.prompt(schema, asked)
    if not examples:
        return base
    head, tail = base.split("\n\nQuestion: ", 1)
    return f"{head}\n\n{examples_block(examples)}\n\nQuestion: {tail}"


def build_examples() -> dict[str, dict[int, list[dict]]]:
    """arm -> question_id -> the approved examples that arm shows (E0: none; E1: the product's selection,
    possibly none; E2: the top 3 with no minimum similarity)."""
    normalize, similarity = product_similarity()
    qs = pf.heldout_questions()
    fold_of = pf.folds(qs)
    db_of = {q["question_id"]: q["db_id"] for q in qs}
    asked = {int(k): v["question_asked"] for k, v in json.loads(eb.answers_path("heldout_gpt5mini").read_text())["answers"].items()}
    refs = {}
    for m in json.loads((eb.EVAL / "paperb_mutations.json").read_text())["questions"]:
        ref = next((e for e in m["examples"] if e["op"] == "reference"), None)
        if ref and ref["correct"]:
            refs[m["question_id"]] = ref["sql"]
    out: dict[str, dict[int, list[dict]]] = {"E0": {}, "E1": {}, "E2": {}}
    for rnd in pf.rounds(fold_of):
        for qid in rnd["test"]:
            q_text = without_hint(asked[qid])
            norm = normalize(q_text)
            pool = [(without_hint(asked[t]), refs[t], t) for t in rnd["train"] if db_of[t] == db_of[qid] and t in refs]
            ranked = sorted(((similarity(q_text, q), q, s, t) for q, s, t in pool if normalize(q) != norm), reverse=True)
            shown = [{"question": q, "sql": s, "source_id": t, "similarity": round(score, 4)} for score, q, s, t in ranked]
            out["E0"][qid] = []
            out["E1"][qid] = [e for e in shown if e["similarity"] >= MIN_SIMILARITY][:MAX_EXAMPLES]
            out["E2"][qid] = shown[:MAX_EXAMPLES]
    return out


def call(key: str, system: str, user: str) -> dict:
    body = json.dumps({"model": MODEL, "max_tokens": pc.MAX_TOKENS, "reasoning_effort": EFFORT,
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
    t = time.monotonic()
    for attempt in range(RETRIES):
        req = urllib.request.Request(pc.BASE_URL + "/chat/completions", data=body,
                                     headers={"Authorization": "Bearer " + pc.KEY_FILE.read_text().strip(),
                                              "Content-Type": "application/json"})
        try:
            r = json.load(urllib.request.urlopen(req, timeout=180))
            text = (r["choices"][0]["message"].get("content") or "")
            usage = r.get("usage") or {}
            return {"sql": pc.extract_sql(text), "raw": text[:4000], "served_model": r.get("model"),
                    "tokens_in": usage.get("prompt_tokens"), "tokens_out": usage.get("completion_tokens"),
                    "ms": int((time.monotonic() - t) * 1000)}
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < RETRIES - 1:
                time.sleep(5 * (attempt + 1))
                continue
            return {"error": f"HTTP {exc.code}: {(exc.read() or b'')[:300]!r}", "ms": int((time.monotonic() - t) * 1000)}
        except Exception as exc:  # noqa: BLE001 - recorded and retried on the next run
            if attempt < RETRIES - 1:
                time.sleep(5 * (attempt + 1))
                continue
            return {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "ms": int((time.monotonic() - t) * 1000)}
    return {"error": "retries exhausted"}


def cmd_generate(workers: int, limit: int | None) -> int:
    examples = build_examples()
    asked = {int(k): v for k, v in json.loads(eb.answers_path("heldout_gpt5mini").read_text())["answers"].items()}
    ids = json.loads(eb.HELDOUT.read_text())["question_ids"]
    record = json.loads(OUT.read_text()) if OUT.exists() else {
        "model": MODEL, "reasoning_effort": EFFORT, "samples": SAMPLES, "system": pc.SYSTEM,
        "examples": {}, "candidates": {}}
    record["examples"] = {arm: {str(q): [{k: e[k] for k in ("source_id", "similarity")} for e in ex]
                                for q, ex in by_q.items()} for arm, by_q in examples.items() if arm != "E0"}
    schemas: dict[str, str] = {}
    jobs = []
    for qid in ids:
        db = asked[qid]["db_id"]
        for arm in ARMS:
            for i in range(SAMPLES):
                key = f"{qid}|{arm}|{i}"
                rec = record["candidates"].get(key)
                if rec is None or rec.get("error"):
                    if db not in schemas:          # one docker exec per database, not per call
                        schemas[db] = eb.schema_text(db)
                    jobs.append((key, prompt(schemas[db], asked[qid]["question_asked"], examples[arm][qid])))
    if limit:
        jobs = jobs[:limit]
    print(f"{len(jobs)} calls to make; questions with at least one example: "
          + ", ".join(f"{arm} {sum(1 for q in ids if examples[arm][q])}" for arm in ("E1", "E2"))
          + f" of {len(ids)}", flush=True)
    lock, done = threading.Lock(), 0
    with ThreadPoolExecutor(workers) as pool:
        for fut in as_completed([pool.submit(lambda k=k, u=u: (k, call(k, pc.SYSTEM, u))) for k, u in jobs]):
            key, rec = fut.result()
            with lock:
                record["candidates"][key] = rec
                done += 1
                if done % SAVE_EVERY == 0 or done == len(jobs):
                    OUT.write_text(json.dumps(record, indent=1) + "\n")
                    errors = sum(1 for v in record["candidates"].values() if v.get("error"))
                    print(f"{done}/{len(jobs)} done; {errors} errors in file", flush=True)
    OUT.write_text(json.dumps(record, indent=1) + "\n")
    return 0


def cmd_label() -> int:
    import paperb_pool as pool
    record = json.loads(OUT.read_text())
    gold_sql = {int(q["question_id"]): q["SQL"] for q in json.loads(eb.CORRECTED.read_text())}
    db_of = {q["question_id"]: q["db_id"] for q in pf.heldout_questions()}
    conn = eb.bird_connection()
    golds: dict[int, list | None] = {}
    labelled = 0
    for key, rec in sorted(record["candidates"].items()):
        if "correct" in rec or rec.get("error"):
            continue
        qid = int(key.split("|")[0])
        if qid not in golds:
            golds[qid] = eb.run_sqlite(db_of[qid], gold_sql[qid])
        res = pool.run(conn, db_of[qid], rec.get("sql"))
        rec["ran"] = res is not None
        rec["correct"] = bool(golds[qid] is not None and res is not None
                              and eb.rows_match_relaxed(res[1], golds[qid], norm=eb.norm_v2))
        labelled += 1
    OUT.write_text(json.dumps(record, indent=1) + "\n")
    print(f"labelled {labelled}; {sum('correct' in r for r in record['candidates'].values())} of "
          f"{len(record['candidates'])} candidates carry a label")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--workers", type=int, default=8)
    g.add_argument("--limit", type=int)
    sub.add_parser("label")
    args = ap.parse_args()
    return cmd_generate(args.workers, args.limit) if args.cmd == "generate" else cmd_label()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
scripts/eval_jev_answerable_r3.py
Paper B rerun R3 (pre-registered in docs/research/paperb_reruns_prereg.md, commit 82aff8d): Jev as an answerability
gate with HARD negatives. The first gate (scripts/eval_jev_answerable.py) used another database's schema as the
negative, which is trivially easy.

Questions: the held-out BIRD questions whose PostgreSQL reference query is correct under the corrected labels
(docs/eval/paperb_mutations.json, op "reference", correct). The tables and columns the reference reads come from its
parse tree (pglast, in the MCP container), aliases resolved against the database's catalogue; a question whose
references cannot be resolved is dropped and counted.
Pairs per question and question form (with BIRD's hint, as the pipeline asked it: primary; without it: secondary):
  P         its own database's full schema card (format and cap of the first gate);
  N_table   the card without every table the reference reads;
  N_column  the card without every column the reference reads (a table left empty is removed).
Jev: jev-1.13 (OpenCode Zen), the first gate's instructions and state format, unchanged.
Measures: AUROC P vs N_column (primary) and P vs N_table with 95% bootstrap intervals over questions; at the first
gate's frozen threshold 0.11; cross-fitted thresholds (halves, seed 20261008) refusing at most 1% of P.

    /opt/anaconda3/bin/python scripts/eval_jev_answerable_r3.py score [--workers 4]   resumable
    /opt/anaconda3/bin/python scripts/eval_jev_answerable_r3.py report
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import eval_bird as eb  # noqa: E402
import eval_jev_answerable as g1  # noqa: E402  (the first gate: URL, model, instructions, card format)

OUT = REPO / "docs" / "eval" / "jev_answerable_r3.json"
MUTATIONS = REPO / "docs" / "eval" / "paperb_mutations.json"
ASKED = REPO / "docs" / "eval" / "bird_answers_heldout_gpt5mini.json"     # the questions as the pipeline asked them
SEED = 20261008
FROZEN_T = 0.11             # noqa: literals - the first gate's fitted threshold (docs/eval/jev_answerable.json)
BOOT = 2000                 # noqa: literals - pre-registered resamples
RETRIES = 5                 # noqa: literals - transient HTTP errors (429, 5xx) are asked again
FORMS = ("hint", "no_hint")
KINDS = ("P", "N_table", "N_column")

_REFS = r"""
import json, sys
from pglast.parser import parse_sql_json
req = json.load(sys.stdin)
out = {}

def walk(node, rels, cols):
    if isinstance(node, dict):
        if "RangeVar" in node:
            rv = node["RangeVar"]
            rels.append((rv["relname"], (rv.get("alias") or {}).get("aliasname")))
        if "ColumnRef" in node:
            f = [x["String"]["sval"] for x in node["ColumnRef"]["fields"] if "String" in x]
            if f:
                cols.append(f)
        for v in node.values():
            walk(v, rels, cols)
    elif isinstance(node, list):
        for v in node:
            walk(v, rels, cols)

for qid, sql in req.items():
    try:
        rels, cols = [], []
        walk(json.loads(parse_sql_json(sql)), rels, cols)
        out[qid] = {"rels": rels, "cols": cols}
    except Exception as exc:
        out[qid] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
print(json.dumps(out))
"""


def catalogue(db_ids: list[str]) -> dict[str, dict[str, list[tuple[str, str]]]]:
    """db -> table -> [(column, type)], in the first gate's order."""
    conn = eb.bird_connection()
    out: dict[str, dict[str, list[tuple[str, str]]]] = {}
    for db in db_ids:
        rows = conn.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                            "WHERE table_schema = %s ORDER BY table_name, ordinal_position", (db,)).fetchall()
        for t, c, ty in rows:
            out.setdefault(db, {}).setdefault(t, []).append((c, ty))
    return out


def card(tables: dict[str, list[tuple[str, str]]]) -> str:
    text = ""
    for t, cols in tables.items():
        if not cols:
            continue
        block = f"TABLE {t}: " + ", ".join(f"{c} {ty}" for c, ty in cols) + "\n"
        if len(text) + len(block) > g1.CARD_CHARS:
            raise SystemExit("a card passed the cap; the pre-registration says none does")
        text += block
    return text


def resolve(ref: dict, cat: dict[str, list[tuple[str, str]]]) -> tuple[set[str], set[tuple[str, str]]] | None:
    """(tables read, (table, column) pairs read), names as in the catalogue; None when anything is unresolved."""
    lower = {t.lower(): t for t in cat}
    tables, alias = set(), {}
    for rel, al in ref["rels"]:
        t = lower.get(rel.lower())
        if t is None:
            continue                        # a CTE or subquery name, not a table
        tables.add(t)
        alias[(al or rel).lower()] = t
        alias[rel.lower()] = t
    if not tables:
        return None
    cols: set[tuple[str, str]] = set()
    for f in ref["cols"]:
        name = f[-1].lower()
        if len(f) >= 2 and f[-2].lower() in alias:
            cands = [alias[f[-2].lower()]]
        else:
            cands = sorted(tables)
        hits = [(t, c) for t in cands for c, _ in cat[t] if c.lower() == name]
        if not hits:
            if len(f) >= 2 and f[-2].lower() not in alias:
                continue                    # a column of a subquery or CTE alias: its base column is read inside it
            if len(f) == 1:
                continue                    # an output alias (ORDER BY total) rather than a column
            return None
        cols.update(hits)
    return tables, cols


def pairs() -> tuple[list[dict], dict]:
    m = json.loads(MUTATIONS.read_text())["questions"]
    asked = json.loads(ASKED.read_text())["answers"]
    refs = {str(q["question_id"]): (q["db_id"], next(e["sql"] for e in q["examples"] if e["op"] == "reference"))
            for q in m if any(e["op"] == "reference" and e.get("correct") for e in q["examples"])}
    res = subprocess.run(["docker", "exec", "-i", "statesync-mcp-1", "python", "-c", _REFS],
                         input=json.dumps({k: v[1] for k, v in refs.items()}), capture_output=True, text=True, timeout=300)
    if res.returncode:
        raise SystemExit(res.stderr[-600:])
    parsed = json.loads(res.stdout.strip().splitlines()[-1])
    cat = catalogue(sorted({db for db, _ in refs.values()}))
    out, dropped = [], {}
    for qid, (db, _sql) in sorted(refs.items(), key=lambda kv: int(kv[0])):
        p = parsed[qid]
        r = None if "error" in p else resolve(p, cat[db])
        if r is None:
            dropped[qid] = p.get("error", "unresolved reference")
            continue
        tables, cols = r
        full = cat[db]
        no_tab = {t: c for t, c in full.items() if t not in tables}
        no_col = {t: [(c, ty) for c, ty in cs if (t, c) not in cols] for t, cs in full.items()}
        q = asked[qid]["question_asked"]
        forms = {"hint": q, "no_hint": q.split("\nHint:")[0].strip()}
        for form, text in forms.items():
            for kind, tabs in (("P", full), ("N_table", no_tab), ("N_column", no_col)):
                out.append({"qid": qid, "db": db, "form": form, "kind": kind, "question": text, "card": card(tabs),
                            "tables_read": sorted(tables), "columns_read": sorted(f"{t}.{c}" for t, c in cols)})
    return out, dropped


def jev(state: str, key: str) -> tuple[float, dict]:
    body = json.dumps({"model": g1.MODEL, "state": state,
                       "questions": {"has_data": {"type": "noul", "instructions": g1.INSTRUCTIONS}}}).encode()
    for attempt in range(RETRIES + 1):
        req = urllib.request.Request(g1.URL, data=body, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                                                 "User-Agent": "statesync-research/1.0"})
        try:
            r = json.load(urllib.request.urlopen(req, timeout=60))
            return float(r["answers"]["has_data"]["noul"]), r.get("usage") or {}
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == RETRIES:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def cmd_score(workers: int) -> int:
    record = json.loads(OUT.read_text()) if OUT.exists() else {}
    items, dropped = pairs()
    record.update({"preregistration": "docs/research/paperb_reruns_prereg.md", "model": g1.MODEL, "instructions": g1.INSTRUCTIONS,
                   "questions": len({i["qid"] for i in items}), "dropped": dropped})
    record.setdefault("scores", {})
    record.setdefault("usage", {})
    key = (REPO / "secrets" / "opencode_api_key").read_text().strip()
    todo = [i for i in items if f"{i['qid']}|{i['form']}|{i['kind']}" not in record["scores"]]
    print(f"{record['questions']} questions ({len(dropped)} dropped), {len(items)} pairs, {len(todo)} to score", flush=True)

    def one(i: dict):
        return i, jev(f"Question: {i['question']}\n\nDatabase {i['db']}:\n{i['card']}", key)

    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(one, i) for i in todo]
        for n, f in enumerate(as_completed(futs), 1):
            i, (p, usage) = f.result()
            k = f"{i['qid']}|{i['form']}|{i['kind']}"
            record["scores"][k] = p
            record["usage"][k] = usage
            if n % 100 == 0 or n == len(todo):
                OUT.write_text(json.dumps(record, indent=1) + "\n")
                print(f"{n}/{len(todo)}", flush=True)
    record["pairs"] = [{k: v for k, v in i.items() if k != "card"} for i in items]
    OUT.write_text(json.dumps(record, indent=1) + "\n")
    return 0


def auroc(pos: list[float], neg: list[float]) -> float:
    return sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))


def cmd_report() -> int:
    record = json.loads(OUT.read_text())
    S = record["scores"]
    qids = sorted({p["qid"] for p in record["pairs"]}, key=int)
    db = {p["qid"]: p["db"] for p in record["pairs"]}
    rng = random.Random(SEED)
    summary = {"questions": len(qids), "dropped": len(record["dropped"])}
    for form in FORMS:
        s = {k: {q: S[f"{q}|{form}|{k}"] for q in qids} for k in KINDS}
        res = {}
        for neg in ("N_column", "N_table"):
            point = auroc(list(s["P"].values()), list(s[neg].values()))
            by_db: dict[str, list[str]] = {}
            for q in qids:
                by_db.setdefault(db[q], []).append(q)
            boots = []
            for _ in range(BOOT):
                sample = [rng.choice(qs) for qs in by_db.values() for _ in qs]
                boots.append(auroc([s["P"][q] for q in sample], [s[neg][q] for q in sample]))
            boots.sort()
            res[f"auroc_P_vs_{neg}"] = [round(point, 4), round(boots[int(0.025 * BOOT)], 4), round(boots[int(0.975 * BOOT)], 4)]
        res["at_frozen_0.11"] = {k: f"{sum(v < FROZEN_T for v in s[k].values())}/{len(qids)} refused" for k in KINDS}
        halves = qids[:]
        random.Random(SEED).shuffle(halves)
        A, B = set(halves[: len(halves) // 2]), set(halves[len(halves) // 2:])
        refused = {k: 0 for k in KINDS}
        thresholds = []
        for fit, test in ((A, B), (B, A)):
            pos = sorted(s["P"][q] for q in fit)
            t = pos[int(0.01 * len(pos))]
            thresholds.append(t)
            for k in KINDS:
                refused[k] += sum(s[k][q] < t for q in test)
        res["cross_fitted"] = {"thresholds": thresholds, **{k: f"{refused[k]}/{len(qids)} refused" for k in KINDS}}
        summary[form] = res
    tokens = sum((u.get("input_tokens") or u.get("prompt_tokens") or 0) for u in record["usage"].values())
    summary["input_tokens"] = tokens
    record["summary"] = summary
    OUT.write_text(json.dumps(record, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("--workers", type=int, default=4)
    sub.add_parser("report")
    sub.add_parser("dry")
    args = ap.parse_args()
    if args.cmd == "score":
        return cmd_score(args.workers)
    if args.cmd == "dry":
        items, dropped = pairs()
        print(len({i["qid"] for i in items}), "questions;", len(dropped), "dropped:", dropped)
        for i in items[:6]:
            print(i["qid"], i["form"], i["kind"], i["tables_read"], i["columns_read"], "|", i["card"][:160].replace("\n", " / "))
        return 0
    return cmd_report()


if __name__ == "__main__":
    raise SystemExit(main())

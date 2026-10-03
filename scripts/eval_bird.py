#!/usr/bin/env python3
"""
scripts/eval_bird.py
BIRD Mini-Dev runs for the study in docs/research/evidence_tiers_study.md (RQ1-RQ4).

    python3 scripts/eval_bird.py sample [--n 100]          fix the pilot sample (no model calls)
    python3 scripts/eval_bird.py heldout                   fix the held-out set, protocol v2 §3 (no model calls)
    python3 scripts/eval_bird.py crosscheck                do the SQLite and PostgreSQL copies agree? (no model calls)
    python3 scripts/eval_bird.py setup                     one connection + workspace per BIRD database
    python3 scripts/eval_bird.py ask --label L [--sample heldout] [--spacing S]   ask through /query (model calls)
    python3 scripts/eval_bird.py score --label L           execution match against gold + every signal (v1)
    python3 scripts/eval_bird.py score --label L --labels v2 [--gold-sqlite FILE]   protocol v2 labels
    python3 scripts/eval_bird.py judge --label L --endpoint E --model M   an LLM judge on each answer

Protocol v2 (docs/research/evidence_tiers_study.md, 2026-10-03, after the pilot) adds, before any
held-out run:
- The held-out set: every unique question not in the pilot, minus CROSS_ENGINE_EXCLUDED.
- Labels v2: the gold is a SQLite file (the corrected annotations, or BIRD's own SQLite gold) run
  on BIRD's SQLite databases; the prediction runs on PostgreSQL; numbers compared to SIG_DIGITS
  significant digits (`norm_v2`). `crosscheck` is the validation of that comparison.
- An answer label (`answer_label`): the text states every cell of a small gold result.
- Signals v2 (`signals`): truncation, NULL-heavy, grounding before repair, "any data check"
  (v1's composite included the tier and SQL retries), empty result and refusal (REFUSAL).
- `ask` records the caveats; judges see the answer as written before any repair.
The v1 paths (pilot sample, original gold, 6 decimals) are unchanged so the pilot reproduces.

Fixed before any run (2026-10-03):
- Sample: stratified by difficulty in BIRD's proportions, seed SAMPLE_SEED, written to
  docs/eval/bird_pilot_sample.json with the question file's sha256 before `ask` runs.
- Question text: BIRD's question, then "Hint: <evidence>" when BIRD gives one (standard practice).
- Workspaces carry NO drafted knowledge (no descriptions, definitions or examples): the pipeline
  as it meets a new database, and no model calls spent on setup.
- Correct = execution match, floats compared to 6 decimals, in two versions: "strict" (BIRD's own
  rule: the SETS of result rows are equal) and "relaxed" (column order and extra predicted columns
  ignored; every gold column must be present with the same rows). Relaxed is the primary label;
  added 2026-10-03 after 3 smoke answers, before the pilot (protocol change log). Original labels; the corrected labels of
  Jin et al. 2026 are a second gold file once downloaded (--gold).
- Flags are whatever the running orchestrator holds; `ask` records them. The study configuration
  is answer_checks = on, number_grounding = on, repair_loop = evidence (each answer's trace keeps
  the pre-repair answer, so "no loop" is read from the same run).
- Judges see the question, hint, the database's tables and columns, the SQL, the first rows of
  the result and the answer, and return p_correct in [0, 1] (prompt JUDGE_SYSTEM, fixed).
The dev token comes from api_auth.api_token() and is never printed; judge keys stay inside the
orchestrator container.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
BIRD = REPO / "data" / "external" / "bird" / "minidev" / "MINIDEV"
QUESTIONS = BIRD / "mini_dev_postgresql.json"
EVAL = REPO / "docs" / "eval"
SAMPLE = EVAL / "bird_pilot_sample.json"
SETUP = EVAL / "bird_setup.json"
API = "http://localhost:8090"
CONTAINER = "statesync-bird-db"
ORCHESTRATOR = "statesync-orchestrator-1"
SAMPLE_SEED = 20261003
DEFAULT_N = 100
BIRD_CONNECTION = {"host": "host.docker.internal", "port": 5436, "dbname": "bird",
                   "username": "statesync_reader", "password": "bird-reader-pass"}  # demo-only, demo/bird_db.sh
MAX_CONSECUTIVE_ERRORS = 5     # noqa: literals - stop rule, as the other external runs
QUERY_TIMEOUT_S = 600          # noqa: literals - one question may wait out Groq rate limits
FLOAT_DECIMALS = 6             # noqa: literals - result comparison precision
PREVIEW_ROWS = 10              # noqa: literals - rows a judge sees
JUDGE_MAX_TOKENS = 400         # noqa: literals - a short JSON verdict (plus reasoning headroom from the adapter)
_CN = re.compile(r'"?cn_[0-9a-f]{16}"?\.')

# Protocol v2 (§3, §5), fixed 2026-10-03 before any held-out run.
HELDOUT = EVAL / "bird_heldout_sample.json"
SQLITE_QUESTIONS = BIRD / "mini_dev_sqlite.json"
SQLITE_DBS = BIRD / "dev_databases"
CROSSCHECK = EVAL / "bird_crossengine_check.json"
# The corrected annotations (Arcwise-Plat-SQL; github.com/uiuc-kang-lab/text_to_sql_benchmarks, commit
# b83d8a8 of 2026-08-27, CC BY-SA 4.0). Besides the SQL, the file rewrites 81 questions and 68 hints of
# our Mini-Dev copy; each corrected query answers ITS question, so held-out runs ask that text.
CORRECTED = REPO / "data" / "external" / "bird" / "corrected" / "arcwise_plat_sql_only_with_diff.json"
CORRECTED_SHA256 = "e7bf76408f99266506ea558d84982c69422e5db3dfecab5f08a3a8d3900e8395"
MAX_ROWS = 1000                # noqa: literals - [mcp] max_rows_default, the row cap the pipeline runs with
SIG_DIGITS = 5                 # noqa: literals - v2 number comparison (REAL vs double differ in the 6th digit)
ANSWER_REL_TOL = 0.005         # noqa: literals - "agrees to 3 significant digits" for the answer label
ANSWER_LABEL_MAX_CELLS = 4     # noqa: literals - larger gold results get no answer label
# The SQLite and PostgreSQL copies' ORIGINAL golds disagree on these (bird_crossengine_check.json).
CROSS_ENGINE_EXCLUDED = frozenset({532, 533, 563, 565, 683, 788, 1482})
# "The text says it cannot answer", read in the first sentence only: a long answer that says
# "I can't list all 660 rows here" is a disclosure, not a refusal.
REFUSAL = re.compile(r"\b(?:can(?:no|'|’)t|unable to)\s+(?:determine|compute|answer|identify|list|provide|"
                     r"calculate|find|tell|say)\b", re.IGNORECASE)
TRUNCATION_CAVEAT = "Showing the first"        # answer_checks.truncation_caveat
NULL_CAVEAT = "No matching data"               # answer_checks.null_aggregate_caveat

JUDGE_SYSTEM = (
    "You check answers from a text-to-SQL system on a PostgreSQL database. You are given the user's "
    "question (with an optional hint), the database's tables and columns, the SQL the system ran, the "
    "first rows of its result and the answer it wrote. Decide whether the result correctly answers the "
    "question. Reply with JSON only: {\"p_correct\": <number between 0 and 1>, \"reason\": \"<one sentence>\"}.")


def questions() -> list[dict]:
    return json.loads(QUESTIONS.read_text())


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def corrected_questions() -> dict[int, dict]:
    """The corrected file's question, hint and SQL per question_id (refuses a changed file)."""
    if sha256(CORRECTED) != CORRECTED_SHA256:
        raise SystemExit(f"{CORRECTED} is not the file the protocol fixed")
    return {int(x["question_id"]): x for x in json.loads(CORRECTED.read_text())}


def question_text(q: dict) -> str:
    hint = (q.get("evidence") or "").strip()
    return f"{q['question'].strip()}\nHint: {hint}" if hint else q["question"].strip()


# ── sample ─────────────────────────────────────────────────────────────────────

def stratified_sample(qs: list[dict], n: int, seed: int) -> list[int]:
    """question_ids: n questions, each difficulty in proportion to BIRD's (largest remainder)."""
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for q in qs:
        groups[q["difficulty"]].append(q)
    exact = {d: n * len(g) / len(qs) for d, g in groups.items()}
    take = {d: int(v) for d, v in exact.items()}
    for d in sorted(exact, key=lambda d: -(exact[d] - take[d]))[: n - sum(take.values())]:
        take[d] += 1
    rng = random.Random(seed)
    chosen = []
    for d in sorted(groups):
        chosen += [q["question_id"] for q in rng.sample(sorted(groups[d], key=lambda q: q["question_id"]), take[d])]
    return sorted(chosen)


def cmd_sample(n: int) -> int:
    if SAMPLE.exists():
        print(f"{SAMPLE} exists; the sample is fixed (delete it only before any run)")
        return 1
    qs = questions()
    ids = stratified_sample(qs, n, SAMPLE_SEED)
    by_id = {q["question_id"]: q for q in qs}
    record = {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "seed": SAMPLE_SEED, "n": len(ids),
              "questions_sha256": sha256(QUESTIONS),
              "difficulty": dict(collections.Counter(by_id[i]["difficulty"] for i in ids)),
              "databases": dict(collections.Counter(by_id[i]["db_id"] for i in ids)), "question_ids": ids}
    SAMPLE.write_text(json.dumps(record, indent=1) + "\n")
    print(json.dumps({k: v for k, v in record.items() if k != "question_ids"}, indent=1))
    return 0


def heldout_ids(qs: list[dict], pilot: list[int]) -> list[int]:
    """Protocol v2 §3: every unique question not in the pilot and not cross-engine excluded."""
    return sorted({q["question_id"] for q in qs} - set(pilot) - CROSS_ENGINE_EXCLUDED)


def cmd_heldout() -> int:
    if HELDOUT.exists():
        print(f"{HELDOUT} exists; the held-out set is fixed")
        return 1
    qs = questions()
    ids = heldout_ids(qs, json.loads(SAMPLE.read_text())["question_ids"])
    by_id = {q["question_id"]: q for q in qs}
    record = {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "protocol": "v2", "n": len(ids),
              "questions_sha256": sha256(QUESTIONS), "excluded_pilot": sha256(SAMPLE),
              "excluded_cross_engine": sorted(CROSS_ENGINE_EXCLUDED),
              "difficulty": dict(collections.Counter(by_id[i]["difficulty"] for i in ids)),
              "databases": dict(collections.Counter(by_id[i]["db_id"] for i in ids)), "question_ids": ids}
    HELDOUT.write_text(json.dumps(record, indent=1) + "\n")
    print(json.dumps({k: v for k, v in record.items() if k != "question_ids"}, indent=1))
    return 0


def norm_v2(value: Any) -> Any:
    """Labels v2: numbers (and numeric text) to SIG_DIGITS significant digits, ints as floats, dates as
    ISO text, so the same value read from SQLite and from PostgreSQL compares equal."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, Decimal)):
        return float(f"{float(value):.{SIG_DIGITS}g}")
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if hasattr(value, "isoformat"):            # date, time
        return value.isoformat()
    if isinstance(value, str):
        try:
            return float(f"{float(value):.{SIG_DIGITS}g}")
        except ValueError:
            return value
    return value


def run_sqlite(db: str, sql: str) -> list[tuple] | None:
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{SQLITE_DBS / db / (db + '.sqlite')}?mode=ro", uri=True)
        try:
            return [tuple(r) for r in conn.execute(sql).fetchall()]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 -- a gold that fails is recorded as such by the caller
        return None


def bird_connection() -> Any:
    import psycopg  # the research venv; the host's Python lacks it
    return psycopg.connect(f"host=127.0.0.1 port=5436 dbname=bird user=statesync_reader password={BIRD_CONNECTION['password']}",
                           autocommit=True)


def cmd_crosscheck() -> int:
    """Each copy's ORIGINAL gold on its own engine, compared under norm_v2 (and exactly, under _norm)."""
    pg = {q["question_id"]: q for q in questions()}
    sq = {q["question_id"]: q for q in json.loads(SQLITE_QUESTIONS.read_text())}
    conn = bird_connection()
    agree_exact = agree = 0
    differ = []
    for qid in sorted(pg):
        s = run_sqlite(pg[qid]["db_id"], sq[qid]["SQL"]) if qid in sq else None
        p = _run(conn, pg[qid]["db_id"], pg[qid]["SQL"])
        if s is not None and p is not None and rows_match(s, p):
            agree_exact += 1
        if s is not None and p is not None and rows_match(s, p, norm_v2):
            agree += 1
        else:
            differ.append({"question_id": qid, "sqlite_failed": s is None, "postgres_failed": p is None,
                           "sqlite_rows": len(s or []), "postgres_rows": len(p or [])})
    record = {"checked": len(pg), "agree_exact": agree_exact, "agree_v2": agree, "differ": differ,
              "questions_sha256": sha256(QUESTIONS), "sqlite_questions_sha256": sha256(SQLITE_QUESTIONS)}
    CROSSCHECK.write_text(json.dumps(record, indent=1) + "\n")
    print(json.dumps({k: v for k, v in record.items() if k != "differ"}, indent=1))
    print("differ:", [d["question_id"] for d in differ])
    return 0


# ── the API (setup, ask) ─────────────────────────────────────────────────────────

def api(method: str, path: str, body: dict | None = None, timeout: float = 120) -> tuple[int, dict]:
    sys.path.insert(0, str(REPO / "scripts"))
    from api_auth import api_token
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {api_token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except json.JSONDecodeError:
            return exc.code, {}


def cmd_setup() -> int:
    """One connection (schema = the BIRD database) and one knowledge-free workspace per database."""
    record = json.loads(SETUP.read_text()) if SETUP.exists() else {}
    _, listed = api("GET", "/workspaces")
    existing = {w["name"]: w for w in listed.get("workspaces", [])}
    for db in sorted({q["db_id"] for q in questions()}):
        name = f"bird-{db}"
        if name in existing:
            record[db] = {"workspace_id": existing[name]["workspace_id"], "connection_id": existing[name]["target_schema"]}
            continue
        st, conn = api("POST", "/library/connections", {**BIRD_CONNECTION, "name": name})
        if st != 201:
            print(f"{db}: connection refused HTTP {st} {conn.get('detail')}")
            return 1
        cid = conn["connection"]["connection_id"]
        st, body = api("PUT", f"/library/connections/{cid}/schema", {"schema_name": db})
        if st != 200:
            print(f"{db}: schema refused HTTP {st} {body.get('detail')}")
            return 1
        st, body = api("POST", "/workspaces", {"name": name, "target_schema": cid})
        if st != 201:
            print(f"{db}: workspace refused HTTP {st} {body.get('detail')}")
            return 1
        record[db] = {"workspace_id": body["workspace"]["workspace_id"], "connection_id": cid}
        print(f"{db}: ready")
    SETUP.write_text(json.dumps(record, indent=1) + "\n")
    print(f"{len(record)} BIRD workspaces ready")
    return 0


def answers_path(label: str) -> Path:
    return EVAL / f"bird_answers_{label}.json"


def cmd_ask(label: str, spacing_s: float, limit: int | None, sample_name: str = "pilot",
            only_ids: list[int] | None = None) -> int:
    """Ask the sample's questions not yet answered under `label`. `only_ids` restricts a run to some of
    the sample's questions (dev checks of a fix; never a confirmatory run)."""
    sample = json.loads((HELDOUT if sample_name == "heldout" else SAMPLE).read_text())
    if sample["questions_sha256"] != sha256(QUESTIONS):
        print("the question file changed since the sample was fixed")
        return 1
    setup = json.loads(SETUP.read_text())
    by_id = {q["question_id"]: q for q in questions()}
    if sample_name == "heldout":   # ask the wording the corrected gold answers (db and difficulty unchanged)
        corrected = corrected_questions()
        by_id = {i: {**q, "question": corrected[i]["question"], "evidence": corrected[i].get("evidence")}
                 for i, q in by_id.items()}
    out = answers_path(label)
    sample_file = HELDOUT if sample_name == "heldout" else SAMPLE
    done = json.loads(out.read_text()) if out.exists() else {
        "label": label, "sample": sample_name, "sample_sha256": sha256(sample_file),
        "question_source": "corrected" if sample_name == "heldout" else "mini_dev_postgresql",
        "question_source_sha256": CORRECTED_SHA256 if sample_name == "heldout" else sha256(QUESTIONS), "answers": {}}
    import tomllib   # the flags the orchestrator was started with (shipped_flags.py checks they are current)
    done.setdefault("pipeline_flags", tomllib.loads((REPO / "config" / "flags.toml").read_text())["pipeline"])
    errors, asked = 0, 0
    for qid in sample["question_ids"]:
        if str(qid) in done["answers"] or (only_ids is not None and qid not in only_ids):
            continue
        if limit is not None and asked >= limit:
            break
        q = by_id[qid]
        t0 = time.monotonic()
        st, body = api("POST", "/query", {"query": question_text(q), "session_id": str(uuid.uuid4()),
                                          "workspace_id": setup[q["db_id"]]["workspace_id"]}, timeout=QUERY_TIMEOUT_S)
        trace = body.get("state_trace") or {}
        record = {"question_id": qid, "db_id": q["db_id"], "difficulty": q["difficulty"], "http_status": st,
                  "question_asked": question_text(q),
                  "answer_text": body.get("answer_text"), "detail": body.get("detail"), "route": trace.get("route"),
                  "executed_sql": trace.get("executed_sql"), "candidate_sql": trace.get("candidate_sql"),
                  "evidence": trace.get("evidence"), "number_grounding": trace.get("number_grounding"),
                  "repair": trace.get("repair"), "errors": trace.get("errors"), "attempt": trace.get("attempt"),
                  "sql_rows_count": trace.get("sql_rows_count"), "calls": trace.get("calls"), "metrics": trace.get("metrics"),
                  "caveats": trace.get("caveats"),
                  "self_consistency": trace.get("self_consistency"),
                  "latency_ms": body.get("latency_ms") or round((time.monotonic() - t0) * 1000),
                  "asked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        asked += 1
        if st == 200:
            done["answers"][str(qid)] = record
            errors = 0
        else:
            errors += 1
            print(f"[{qid}] HTTP {st}: {body.get('detail')}")
        out.write_text(json.dumps(done, indent=1) + "\n")
        print(f"[{len(done['answers'])}/{len(sample['question_ids'])}] q{qid} {q['db_id']} HTTP {st} route={record['route']}")
        if errors >= MAX_CONSECUTIVE_ERRORS:
            print(f"stopping: {errors} errors in a row")
            return 1
        time.sleep(spacing_s)
    return 0


# ── scoring ─────────────────────────────────────────────────────────────────────

def _norm(value: Any) -> Any:
    if isinstance(value, (float, Decimal)):
        return round(float(value), FLOAT_DECIMALS)
    return value


def rows_match(predicted: list[tuple] | None, gold: list[tuple], norm: Any = None) -> bool:
    """BIRD's rule: the SETS of result rows are equal (values under `norm`; v1's _norm by default)."""
    norm = norm or _norm
    if predicted is None:
        return False
    return {tuple(norm(v) for v in r) for r in predicted} == {tuple(norm(v) for v in r) for r in gold}


def rows_match_relaxed(predicted: list[tuple] | None, gold: list[tuple], max_columns: int = 8,  # noqa: literals - bound on the column search
                       norm: Any = None) -> bool:
    """Column order and extra predicted columns ignored: some choice of distinct predicted columns,
    one per gold column, gives the same SET of rows as the gold (protocol change, 2026-10-03)."""
    from itertools import permutations
    norm = norm or _norm
    if predicted is None:
        return False
    if rows_match(predicted, gold, norm):
        return True
    if not gold:
        return not predicted
    k = len(gold[0])
    width = len(predicted[0]) if predicted else 0
    if width < k or width > max_columns:
        return False
    target = {tuple(norm(v) for v in r) for r in gold}
    return any({tuple(norm(r[i]) for i in cols) for r in predicted} == target for cols in permutations(range(width), k))


def unqualify(sql: str) -> str:
    """Executed SQL names a connected database's tables as cn_<id>.<table>; the scorer runs it
    with search_path set to the BIRD schema instead."""
    return _CN.sub("", sql)


def _run(conn: Any, schema: str, sql: str) -> list[tuple] | None:
    try:
        conn.execute("SET statement_timeout = 30000")
        conn.execute(f'SET search_path = "{schema}"')
        return [tuple(r) for r in conn.execute(sql).fetchall()]
    except Exception:  # noqa: BLE001 -- an SQL that fails is simply not a match
        return None


def text_before_repair(a: dict) -> str:
    """The answer as first written: arm (a) of the repair loop (protocol v2 §4)."""
    return (a.get("repair") or {}).get("previous_answer") or a.get("answer_text") or ""


def refused(text: str) -> bool:
    """The text's first sentence says it cannot answer (REFUSAL)."""
    first = re.split(r"(?<=[.!?])\s|\n", (text or "").strip(), maxsplit=1)[0]
    return bool(REFUSAL.search(first))


def signals(a: dict, pred_rows: list[tuple] | None = None) -> dict[str, Any]:
    """Each warning for one answer (True = 'this may be wrong'). The first seven are v1's (the pilot's
    report used them); the rest are protocol v2's. NULL-heavy comes from the recorded caveats, or, for
    answers asked before caveats were recorded, from the re-executed rows (`pred_rows`)."""
    grounding = a.get("number_grounding") or {}
    errs = a.get("errors") or []
    repair = a.get("repair") or {}
    caveats = a.get("caveats")
    v1 = {
        "no_sql": not a.get("executed_sql"),
        "tier_unchecked": a.get("evidence") in (None, "unchecked"),
        "fanout_retry": any(e.get("category") == "join_cardinality" for e in errs if isinstance(e, dict)),
        "grounding_unmatched": bool(grounding.get("unmatched")),
        "repair_triggered": bool(repair),
        "repair_still_flagged": repair.get("outcome") == "still_flagged",
        "any_sql_retry": bool(errs),
    }
    rows_count = a.get("sql_rows_count")
    truncation = (any(TRUNCATION_CAVEAT in c for c in caveats) if caveats is not None else False) or rows_count == MAX_ROWS
    if caveats is not None:
        null_heavy = any(NULL_CAVEAT in c for c in caveats)
    else:
        null_heavy = bool(pred_rows) and all(v is None for r in pred_rows for v in r)
    grounding_before = v1["repair_triggered"] or v1["grounding_unmatched"]
    v2 = {
        "truncation": truncation,
        "null_heavy": null_heavy,
        "grounding_before_repair": grounding_before,
        "any_data_check": v1["fanout_retry"] or truncation or null_heavy or grounding_before,
        "empty_result": bool(a.get("executed_sql")) and rows_count == 0,
        "refusal": refused(text_before_repair(a)),
    }
    return {**v1, **v2}


def answer_label(text: str, gold: list[tuple] | None) -> bool | None:
    """Protocol v2 answer label: does the text state every cell of a gold result of at most
    ANSWER_LABEL_MAX_CELLS cells? Numbers agree within ANSWER_REL_TOL (or the rounding the text's
    own digits imply; a fraction may be written as a percent); text cells are matched ignoring case;
    a text whose first sentence refuses is wrong. None when the gold is larger, empty, or holds a NULL
    (no label)."""
    sys.path.insert(0, str(REPO / "orchestrator"))
    from number_grounding import extract_mentions
    if not gold or sum(len(r) for r in gold) > ANSWER_LABEL_MAX_CELLS:
        return None
    cells = [v for r in gold for v in r]
    if any(v is None for v in cells):
        return None
    if refused(text):            # a refusal that lists candidates is still not an answer (pilot q1389)
        return False
    mentions = extract_mentions(text or "")
    low = (text or "").lower()
    for v in cells:
        number = None if isinstance(v, bool) else (float(v) if isinstance(v, (int, float, Decimal)) else None)
        if number is None and isinstance(v, str):
            try:
                number = float(v.replace(",", ""))
            except ValueError:
                number = None
        if number is not None:
            targets = [number, number * 100]          # noqa: literals - a fraction written as a percent
            if not any(abs(m.value - t) <= max(m.tolerance, ANSWER_REL_TOL * abs(t)) for m in mentions for t in targets):
                return False
        elif str(v).strip().lower() not in low:
            return False
    return True


def cmd_score(label: str, gold_file: Path | None) -> int:
    import psycopg  # the research venv; the host's Python lacks it
    answers = json.loads(answers_path(label).read_text())["answers"]
    by_id = {q["question_id"]: q for q in questions()}
    gold_sql = {q["question_id"]: q["SQL"] for q in json.loads(gold_file.read_text())} if gold_file else {}
    conn = psycopg.connect(f"host=127.0.0.1 port=5436 dbname=bird user=statesync_reader password={BIRD_CONNECTION['password']}",
                           autocommit=True)
    rows = []
    for qid, a in sorted(answers.items(), key=lambda kv: int(kv[0])):
        q = by_id[int(qid)]
        gold = _run(conn, q["db_id"], gold_sql.get(int(qid), q["SQL"]))
        pred = _run(conn, q["db_id"], unqualify(a["executed_sql"])) if a.get("executed_sql") else None
        rows.append({"question_id": int(qid), "difficulty": q["difficulty"], "db_id": q["db_id"],
                     "correct": gold is not None and rows_match_relaxed(pred, gold),
                     "correct_strict": gold is not None and rows_match(pred, gold), "signals": signals(a),
                     "repair_outcome": (a.get("repair") or {}).get("outcome"),
                     "llm_calls": len(a.get("calls") or []), "latency_ms": a.get("latency_ms")})
    n = len(rows)
    wrong = [r for r in rows if not r["correct"]]
    report: dict[str, Any] = {"label": label, "n": n, "accuracy": round(sum(r["correct"] for r in rows) / n, 4) if n else None,
                              "accuracy_strict": round(sum(r["correct_strict"] for r in rows) / n, 4) if n else None,
                              "by_difficulty": {}, "signals": {}}
    for d in sorted({r["difficulty"] for r in rows}):
        sub = [r for r in rows if r["difficulty"] == d]
        report["by_difficulty"][d] = {"n": len(sub), "accuracy": round(sum(r["correct"] for r in sub) / len(sub), 4)}
    for name in rows[0]["signals"] if rows else []:
        flagged = [r for r in rows if r["signals"][name]]
        tp = sum(not r["correct"] for r in flagged)
        report["signals"][name] = {"flagged": len(flagged), "precision": round(tp / len(flagged), 4) if flagged else None,
                                   "recall": round(tp / len(wrong), 4) if wrong else None,
                                   "false_alarm_rate": round(sum(r["correct"] for r in flagged) / max(1, n - len(wrong)), 4)}
    report["repair_outcomes"] = dict(collections.Counter(r["repair_outcome"] for r in rows if r["repair_outcome"]))
    report["mean_llm_calls"] = round(sum(r["llm_calls"] for r in rows) / n, 2) if n else None
    out = EVAL / f"bird_scores_{label}.json"
    out.write_text(json.dumps({"report": report, "rows": rows}, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


def cmd_score_v2(label: str, gold_sqlite: Path | None) -> int:
    """Protocol v2 labels: the SQLite gold (the corrected annotations when given, else BIRD's own SQLite
    gold) on SQLite, the prediction on PostgreSQL, compared under norm_v2; the answer label before
    and after any repair; every v1 and v2 signal. Cross-engine-excluded questions are kept in the file,
    marked, and left out of the summary."""
    answers = json.loads(answers_path(label).read_text())["answers"]
    gold_file = gold_sqlite or SQLITE_QUESTIONS
    gold_sql = {int(q["question_id"]): q["SQL"] for q in json.loads(gold_file.read_text())}
    by_id = {q["question_id"]: q for q in questions()}
    conn = bird_connection()
    rows = []
    for qid, a in sorted(answers.items(), key=lambda kv: int(kv[0])):
        q = by_id[int(qid)]
        gold = run_sqlite(q["db_id"], gold_sql[int(qid)]) if int(qid) in gold_sql else None
        pred = _run(conn, q["db_id"], unqualify(a["executed_sql"])) if a.get("executed_sql") else None
        ok = gold is not None
        before, after = text_before_repair(a), a.get("answer_text") or ""
        rows.append({"question_id": int(qid), "difficulty": q["difficulty"], "db_id": q["db_id"],
                     "excluded": int(qid) in CROSS_ENGINE_EXCLUDED, "gold_failed": not ok,
                     "correct": ok and rows_match_relaxed(pred, gold, norm=norm_v2),
                     "correct_strict": ok and rows_match(pred, gold, norm_v2),
                     "answer_correct_before": answer_label(before, gold) if ok else None,
                     "answer_correct_after": answer_label(after, gold) if ok else None,
                     "signals": signals(a, pred), "repair_outcome": (a.get("repair") or {}).get("outcome"),
                     # Self-consistency risk; an answer with no SQL, or none sampled, is the most suspect (1.0).
                     "self_consistency_risk": ((a.get("self_consistency") or {}).get("risk")
                                               if a.get("self_consistency") else 1.0),
                     "self_consistency_sampled": bool(a.get("self_consistency")),
                     "llm_calls": len(a.get("calls") or []), "latency_ms": a.get("latency_ms")})
    kept = [r for r in rows if not r["excluded"] and not r["gold_failed"]]
    n = len(kept)
    labelled = [r for r in kept if r["answer_correct_before"] is not None]
    report: dict[str, Any] = {
        "label": label, "labels": "v2", "gold_file": str(gold_file.relative_to(REPO)), "gold_sha256": sha256(gold_file),
        "n": n, "excluded": sum(r["excluded"] for r in rows), "gold_failed": sum(r["gold_failed"] for r in rows),
        "accuracy": round(sum(r["correct"] for r in kept) / n, 4) if n else None,
        "accuracy_strict": round(sum(r["correct_strict"] for r in kept) / n, 4) if n else None,
        "answer_labelled": len(labelled),
        "answer_accuracy_before": round(sum(bool(r["answer_correct_before"]) for r in labelled) / len(labelled), 4) if labelled else None,
        "answer_accuracy_after": round(sum(bool(r["answer_correct_after"]) for r in labelled) / len(labelled), 4) if labelled else None,
        "signals": {}}
    wrong = [r for r in kept if not r["correct"]]
    for name in kept[0]["signals"] if kept else []:
        flagged = [r for r in kept if r["signals"][name]]
        tp = sum(not r["correct"] for r in flagged)
        report["signals"][name] = {"flagged": len(flagged), "precision": round(tp / len(flagged), 4) if flagged else None,
                                   "recall": round(tp / len(wrong), 4) if wrong else None,
                                   "false_alarm_rate": round((len(flagged) - tp) / max(1, n - len(wrong)), 4)}
    out = EVAL / f"bird_scores_{label}_v2_{'corrected' if gold_sqlite else 'original'}.json"
    out.write_text(json.dumps({"report": report, "rows": rows}, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


# ── judge ─────────────────────────────────────────────────────────────────────

def schema_text(db: str) -> str:
    out = subprocess.run(["docker", "exec", CONTAINER, "psql", "-At", "-U", "postgres", "-d", "bird", "-F", "\t", "-c",
                          "SELECT table_name, string_agg(column_name || ' ' || data_type, ', ' ORDER BY ordinal_position) "
                          f"FROM information_schema.columns WHERE table_schema = '{db}' GROUP BY table_name ORDER BY table_name"],
                         check=True, capture_output=True, text=True).stdout
    return "\n".join(f"{t}({cols})" for t, cols in (line.split("\t") for line in out.splitlines() if line))


def preview(db: str, sql: str | None) -> str:
    if not sql:
        return "(no SQL ran)"
    body = unqualify(sql).strip().rstrip(";")
    out = subprocess.run(["docker", "exec", CONTAINER, "psql", "-U", "statesync_reader", "-d", "bird", "-c",
                          f'SET search_path = "{db}"; SET statement_timeout = 30000; '
                          f"SELECT * FROM ({body}) AS judge_preview LIMIT {PREVIEW_ROWS}"],
                         capture_output=True, text=True)
    return (out.stdout or out.stderr)[-3000:]


_JUDGE_SNIPPET = r"""
import asyncio, json, sys, time
from llm.adapters import openai_compat_adapter as oc
req = json.load(sys.stdin)
adapter = oc.OpenAICompatAdapter(req["model"], oc.endpoint(req["endpoint"]), require_served_match=True)
async def main():
    out = []
    for item in req["items"]:
        t = time.monotonic()
        try:
            r = await adapter.complete(system=req["system"], messages=[{"role": "user", "content": item["prompt"]}],
                                       max_tokens=req["max_tokens"])
            out.append({"id": item["id"], "text": r.text, "served_model": r.served_model, "endpoint": r.endpoint,
                        "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "ms": int((time.monotonic() - t) * 1000)})
        except Exception as exc:
            out.append({"id": item["id"], "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        await asyncio.sleep(req["spacing_s"])
    print(json.dumps(out))
asyncio.run(main())
"""


def parse_p(text: str) -> float | None:
    m = re.search(r'"p_correct"\s*:\s*([0-9.]+)', text or "")
    try:
        return max(0.0, min(1.0, float(m.group(1)))) if m else None
    except ValueError:
        return None


def cmd_judge(label: str, endpoint: str, model: str, spacing_s: float, limit: int | None) -> int:
    """Resumable: verdicts already in the file are kept; at most `limit` new answers per call."""
    answers = json.loads(answers_path(label).read_text())["answers"]
    by_id = {q["question_id"]: q for q in questions()}
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{endpoint}_{model}")
    path = EVAL / f"bird_judge_{label}_{safe}.json"
    record = json.loads(path.read_text()) if path.exists() else {"label": label, "endpoint": endpoint, "model": model,
                                                                  "system": JUDGE_SYSTEM, "verdicts": []}
    done = {v["id"] for v in record["verdicts"] if v.get("p_correct") is not None}
    record["verdicts"] = [v for v in record["verdicts"] if v["id"] in done]
    schemas: dict[str, str] = {}
    items = []
    for qid, a in sorted(answers.items(), key=lambda kv: int(kv[0])):
        if qid in done or (limit is not None and len(items) >= limit):
            continue
        q = by_id[int(qid)]
        schemas.setdefault(q["db_id"], schema_text(q["db_id"]))
        prompt = (f"Question: {a.get('question_asked') or question_text(q)}\n\nTables:\n{schemas[q['db_id']]}\n\nSQL:\n{a.get('executed_sql') or '(none)'}\n\n"
                  f"First rows of the result:\n{preview(q['db_id'], a.get('executed_sql'))}\n\nAnswer:\n{text_before_repair(a)}")
        items.append({"id": qid, "prompt": prompt})
    req = {"endpoint": endpoint, "model": model, "system": JUDGE_SYSTEM, "max_tokens": JUDGE_MAX_TOKENS,
           "spacing_s": spacing_s, "items": items}
    out = subprocess.run(["docker", "exec", "-i", ORCHESTRATOR, "python", "-c", _JUDGE_SNIPPET], input=json.dumps(req),
                         capture_output=True, text=True, timeout=max(600, int(len(items) * (spacing_s + 120))))
    if out.returncode:
        print(out.stderr[-800:])
        return 1
    verdicts = json.loads(out.stdout.strip().splitlines()[-1]) if items else []
    for v in verdicts:
        v["p_correct"] = parse_p(v.get("text", ""))
    record["verdicts"] += verdicts
    path.write_text(json.dumps(record, indent=1) + "\n")
    ok = sum(v.get("p_correct") is not None for v in record["verdicts"])
    print(f"{ok}/{len(answers)} verdicts so far; served models: "
          f"{sorted({v.get('served_model') for v in record['verdicts'] if v.get('served_model')})}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sample")
    p.add_argument("--n", type=int, default=DEFAULT_N)
    sub.add_parser("heldout")
    sub.add_parser("crosscheck")
    sub.add_parser("setup")
    p = sub.add_parser("ask")
    p.add_argument("--label", required=True)
    p.add_argument("--sample", choices=["pilot", "heldout"], default="pilot")
    p.add_argument("--ids", help="comma-separated question ids of the sample (dev checks only)")
    p.add_argument("--spacing", type=float, default=45.0)
    p.add_argument("--limit", type=int, help="ask at most this many new questions (a daily quota)")
    p = sub.add_parser("score")
    p.add_argument("--label", required=True)
    p.add_argument("--gold", type=Path, help="v1: a second PostgreSQL gold file")
    p.add_argument("--labels", choices=["v1", "v2"], default="v1")
    p.add_argument("--gold-sqlite", type=Path, help="v2: the corrected annotations (SQLite); default BIRD's own SQLite gold")
    p = sub.add_parser("judge")
    p.add_argument("--label", required=True)
    p.add_argument("--endpoint", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--spacing", type=float, default=5.0)
    p.add_argument("--limit", type=int, help="judge at most this many new answers (keeps each call short)")
    args = ap.parse_args()
    if args.cmd == "sample":
        return cmd_sample(args.n)
    if args.cmd == "heldout":
        return cmd_heldout()
    if args.cmd == "crosscheck":
        return cmd_crosscheck()
    if args.cmd == "setup":
        return cmd_setup()
    if args.cmd == "ask":
        return cmd_ask(args.label, args.spacing, args.limit, args.sample,
                       [int(i) for i in args.ids.split(",")] if args.ids else None)
    if args.cmd == "score":
        return cmd_score_v2(args.label, args.gold_sqlite) if args.labels == "v2" else cmd_score(args.label, args.gold)
    return cmd_judge(args.label, args.endpoint, args.model, args.spacing, args.limit)


if __name__ == "__main__":
    sys.exit(main())

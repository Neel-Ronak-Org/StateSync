"""
scripts/eval_groq.py
A fixed 30-question accuracy baseline for the Groq models: run it, resume it,
compare it later.

WHY THIS EXISTS. No accuracy number in this repository was measured on Groq:
the published numbers come from earlier providers, and the model behind the
product has since changed. A number for the stack the owner actually runs is
the honest starting point for every later prompt or model change. The set is
30 questions (fixtures/golden/groq_baseline.json, selected by a stated rule
from existing fixtures, nothing copied) because at about 5k tokens per
question it fits inside one free-tier day, and because the run is resumable
it can also be split across two.

WHAT IT DOES. Each question is asked through POST /query with its dataset
(conversation turns share one fresh session, asked in order). Answers are
scored the way the rest of the project scores them:
- SQL questions: the rows the answer executed against the rows of the
  reference SQL (run through the MCP database gateway, same safety gate as
  the pipeline) with ExecutionAccuracyScorer (multiset of value tuples). An
  answer that executed no SQL is wrong.
- Document questions: correct when a cited chunk is one of the expected chunks
  or belongs to the same document as one of them (chunk ids are
  "<document id>_cNNN").
- Conversation turns: like SQL, using the turn's own reference SQL.
Results are written after EVERY question to docs/eval/groq_<label>_<date>.json
with a summary that is recomputed each time. Re-running with the same label on
the same day skips what is already recorded. The run stops cleanly (results
saved, resume command printed) on HTTP 503 "rate-limited", on HTTP 429, or
after 2 consecutive transport errors; a stopped question is not recorded, so
it is asked again on resume.

HOW TO RUN (stack up; needs secrets/api_token_dev):
  python3 scripts/eval_groq.py --label baseline --dry-run   # costs no quota
  python3 scripts/eval_groq.py --label baseline [--pace 45] [--limit N] [--out docs/eval]

WHAT IT COSTS. --dry-run: no model calls, only reference SQL through the
gateway. A full run: 30 questions, roughly 5k tokens each (about 150k tokens
in all) of the owner's daily Groq quota. Do not run it casually.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

BASE = "http://localhost:8090"
ORCHESTRATOR = "statesync-orchestrator-1"
GOLDEN_DIR = REPO_ROOT / "fixtures" / "golden"
SELECTION_PATH = GOLDEN_DIR / "groq_baseline.json"
DEFAULT_OUT_DIR = "docs/eval"
DEV_TENANT = "t_capstone_default"   # tenant of the dev token (secrets/api_principals.json, config default_tenant)
PACE_S = 45                         # between questions; keeps a free-tier minute window from tripping
QUERY_TIMEOUT_S = 600               # the model adapter retries a rate limit for several minutes
HEALTH_TIMEOUT_S = 30
MCP_TIMEOUT_S = 120
MAX_CONSECUTIVE_TRANSPORT_ERRORS = 2
HTTP_RATE_LIMITED = 429
HTTP_UNAVAILABLE = 503
RATE_LIMITED_MARK = "rate-limited"
P50 = 50
P90 = 90
PERCENT = 100
CHUNK_SUFFIX = re.compile(r"_c\d+$")

_MCP_SNIPPET = """
import json, sys, urllib.request
req = json.load(sys.stdin)
body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": req["method"], "params": req["params"]}).encode()
r = urllib.request.Request("http://mcp:8080/rpc", data=body, method="POST",
                           headers={"Content-Type": "application/json", "X-Tenant-ID": req["tenant"]})
print(urllib.request.urlopen(r, timeout=60).read().decode())
"""


# ── Pure logic (no network, no docker; covered by tests/test_eval_groq.py) ───

def load_fixture(source: str, golden_dir: Path = GOLDEN_DIR) -> dict:
    return json.loads((golden_dir / source).read_text(encoding="utf-8"))


def resolve_selection(selection: dict, golden_dir: Path = GOLDEN_DIR) -> list[dict]:
    """
    Turn the selection file into a flat, ordered list of questions (items first,
    then conversation turns in order). Every referenced id must exist in its
    source fixture: a missing id raises, it is never skipped.
    """
    questions: list[dict] = []
    for ref in selection["items"]:
        queries = {q["id"]: q for q in load_fixture(ref["source"], golden_dir)["queries"]}
        if ref["id"] not in queries:
            raise KeyError(f"{ref['source']} has no item {ref['id']!r}")
        q = queries[ref["id"]]
        questions.append({
            "id": q["id"], "source": ref["source"], "kind": ref["kind"], "schema": ref["schema"],
            "text": q["text"], "expected_route": q.get("expected_route"),
            "reference_sql": q.get("ground_truth_sql") if ref["kind"] == "sql" else None,
            "expected_chunk_ids": q.get("expected_chunk_ids") or [],
            "conversation_id": None, "turn_index": None,
        })
    for ref in selection["conversations"]:
        convs = {c["conversation_id"]: c for c in load_fixture(ref["source"], golden_dir)["conversations"]}
        if ref["conversation_id"] not in convs:
            raise KeyError(f"{ref['source']} has no conversation {ref['conversation_id']!r}")
        turns = convs[ref["conversation_id"]]["turns"][: ref["turns"]]
        if len(turns) < ref["turns"]:
            raise KeyError(f"{ref['conversation_id']} has fewer than {ref['turns']} turns")
        for i, t in enumerate(turns, start=1):
            questions.append({
                "id": t["turn_id"], "source": ref["source"], "kind": "conversation", "schema": ref["schema"],
                "text": t["text"], "expected_route": t.get("expected_route"),
                "reference_sql": t["ground_truth_sql"],
                "expected_chunk_ids": t.get("expected_chunk_ids") or [],
                "conversation_id": ref["conversation_id"], "turn_index": i,
            })
    return questions


def document_id(chunk_id: str) -> str:
    """'doc_supplier_agreement_S014_c001' -> 'doc_supplier_agreement_S014'."""
    return CHUNK_SUFFIX.sub("", chunk_id)


def score_docs(citations: list[dict], expected_chunk_ids: list[str]) -> tuple[bool, str]:
    """Correct iff a cited chunk is expected, or is from the same document as an expected chunk."""
    expected = set(expected_chunk_ids)
    expected_docs = {document_id(c) for c in expected}
    cited = [c["ref"] for c in citations if c.get("kind") == "chunk" and c.get("ref")]
    for ref in cited:
        if ref in expected:
            return True, f"cited expected chunk {ref}"
    for ref in cited:
        if document_id(ref) in expected_docs:
            return True, f"cited {ref}, same document as an expected chunk"
    return False, f"cited {len(cited)} chunk(s), none from the expected document(s)"


def score_sql(trace: dict, reference_rows: list, scorer: Any = None) -> tuple[bool, str]:
    """Correct iff the executed rows match the reference rows; no executed SQL is wrong."""
    if not trace.get("executed_sql"):
        return False, "no SQL executed"
    if scorer is None:
        scorer = _execution_scorer()
    s = scorer.score(trace.get("sql_rows") or [], reference_rows)
    detail = (f"generated {s['generated_row_count']} rows, reference {s['reference_row_count']} rows, "
              f"row_recall {s['row_recall']:.2f}")
    return bool(s["match"]), detail


def _execution_scorer() -> Any:
    import os
    os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
    sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
    from scorers import ExecutionAccuracyScorer
    return ExecutionAccuracyScorer()


def percentile(values: list[float], p: int) -> float | None:
    """Nearest-rank percentile; None for no data."""
    if not values:
        return None
    ordered = sorted(values)
    rank = -(-p * len(ordered) // PERCENT)   # ceil(p/100 * n)
    return ordered[max(rank, 1) - 1]


def _accuracy(records: list[dict]) -> dict:
    n = len(records)
    c = sum(1 for r in records if r["correct"])
    return {"n": n, "correct": c, "accuracy": (c / n) if n else None}


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def summarize(records: list[dict], meta: dict) -> dict:
    """
    Recomputed from the records after every question. `answered` counts records
    with a reply (no error); accuracy is over every recorded question, so an
    errored question counts as wrong rather than vanishing from the denominator.
    """
    def group(key: str) -> dict:
        return {k: _accuracy([r for r in records if r[key] == k]) for k in sorted({r[key] for r in records})}

    tin = [r["tokens_in"] for r in records if r.get("tokens_in") is not None]
    tout = [r["tokens_out"] for r in records if r.get("tokens_out") is not None]
    calls = [r["llm_calls"] for r in records if r.get("llm_calls") is not None]
    lat = [r["latency_ms"] for r in records if r.get("latency_ms") is not None]
    routed = [r for r in records if r.get("route") is not None and r.get("expected_route") is not None]
    return {
        **meta,
        "recorded": len(records),
        "answered": sum(1 for r in records if not r.get("error")),
        "errors": sum(1 for r in records if r.get("error")),
        "correct": sum(1 for r in records if r["correct"]),
        "accuracy": _accuracy(records)["accuracy"],
        "accuracy_by_kind": group("kind"),
        "accuracy_by_source": group("source"),
        "route_accuracy": (sum(1 for r in routed if r["route"] == r["expected_route"]) / len(routed)) if routed else None,
        "tokens_in_mean": _mean(tin), "tokens_in_median": statistics.median(tin) if tin else None,
        "tokens_out_mean": _mean(tout), "tokens_out_median": statistics.median(tout) if tout else None,
        "llm_calls_mean": _mean(calls),
        "latency_ms_p50": percentile(lat, P50), "latency_ms_p90": percentile(lat, P90),
        "total_tokens": sum(tin) + sum(tout),
    }


def pending(questions: list[dict], records: list[dict]) -> list[dict]:
    """Questions with no recorded result yet, in selection order (resume)."""
    done = {r["id"] for r in records}
    return [q for q in questions if q["id"] not in done]


def result_path(out_dir: str | Path, label: str, day: date) -> Path:
    return Path(out_dir) / f"groq_{label}_{day.isoformat()}.json"


def build_record(q: dict, body: dict, wall_ms: int, scorer: Any = None, reference_rows: list | None = None) -> dict:
    """Score one /query reply into the per-item record."""
    trace = body.get("state_trace") or {}
    metrics = trace.get("metrics") or {}
    if q["kind"] == "docs":
        correct, detail = score_docs(body.get("citations") or [], q["expected_chunk_ids"])
    else:
        correct, detail = score_sql(trace, reference_rows or [], scorer)
    rec = {
        "id": q["id"], "source": q["source"], "kind": q["kind"], "question": q["text"],
        "correct": correct, "detail": detail,
        "route": trace.get("route"), "expected_route": q["expected_route"],
        "executed_sql": trace.get("executed_sql"),
        "tokens_in": metrics.get("tokens_in"), "tokens_out": metrics.get("tokens_out"),
        "llm_calls": metrics.get("llm_calls"),
        "latency_ms": body.get("latency_ms", metrics.get("latency_ms")), "wall_ms": wall_ms,
        "error": None,
    }
    if trace.get("calls") is not None:
        rec["calls"] = trace["calls"]
    if q["turn_index"] == 2:
        rec["rewrite_applied"] = trace.get("rewrite_applied")
        rec["normalized_query"] = trace.get("normalized_query")
    return rec


def error_record(q: dict, message: str, wall_ms: int) -> dict:
    return {
        "id": q["id"], "source": q["source"], "kind": q["kind"], "question": q["text"],
        "correct": False, "detail": "no answer", "route": None, "expected_route": q["expected_route"],
        "executed_sql": None, "tokens_in": None, "tokens_out": None, "llm_calls": None,
        "latency_ms": None, "wall_ms": wall_ms, "error": message,
    }


def is_stop_signal(status: int, detail: str) -> bool:
    """HTTP 429, or 503 whose detail says rate-limited (orchestrator/failures.py)."""
    return status == HTTP_RATE_LIMITED or (status == HTTP_UNAVAILABLE and RATE_LIMITED_MARK in detail.lower())


# ── Network and docker (not called by the tests) ─────────────────────────────

def mcp_select(tenant: str, sql: str) -> dict:
    """Run reference SQL through the MCP gateway from inside the docker network."""
    out = subprocess.run(
        ["docker", "exec", "-i", ORCHESTRATOR, "python", "-c", _MCP_SNIPPET],
        input=json.dumps({"tenant": tenant, "method": "data.run_select", "params": {"sql": sql}}),
        capture_output=True, text=True, timeout=MCP_TIMEOUT_S,
    )
    if out.returncode:
        raise RuntimeError(f"MCP call failed: {out.stderr.strip()}")
    reply = json.loads(out.stdout)
    return reply.get("result") or {"ok": False, "rows": [], "error": {"message": json.dumps(reply.get("error"))}}


def fetch_references(questions: list[dict], tenant: str = DEV_TENANT) -> dict[str, dict]:
    """Reference results by question id. A reference that fails to run is fatal (loud, before any quota is spent)."""
    refs: dict[str, dict] = {}
    for q in questions:
        if not q["reference_sql"]:
            continue
        res = mcp_select(tenant, q["reference_sql"])
        if not res.get("ok"):
            raise RuntimeError(f"reference SQL for {q['id']} failed: {(res.get('error') or {}).get('message')}")
        refs[q["id"]] = res
    return refs


def http_json(method: str, path: str, body: dict | None, timeout: int) -> tuple[int, dict]:
    from api_auth import api_token
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_token()}"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return e.code, {"detail": raw.decode(errors="replace")}


def model_names() -> dict | None:
    """Model names from GET /health if it exposes any; else None."""
    try:
        status, health = http_json("GET", "/health", None, HEALTH_TIMEOUT_S)
    except (OSError, ValueError):
        return None
    found = {k: v for k, v in health.items() if "model" in k.lower()} if status == 200 else {}
    return found or None


def git_commit() -> str | None:
    out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True)
    return out.stdout.strip() or None if out.returncode == 0 else None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_results(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"summary": {}, "records": []}


def save_results(path: Path, records: list[dict], meta: dict, sessions: dict[str, str]) -> None:
    """`sessions` maps conversation id to its session id so a resume continues the same conversation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"summary": summarize(records, meta), "sessions": sessions, "records": records}, indent=1), encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    selection = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    questions = resolve_selection(selection)
    if args.limit is not None:
        questions = questions[: args.limit]
    print(f"selection: {len(questions)} question(s) resolved")
    refs = fetch_references(questions)

    if args.dry_run:
        for q in questions:
            ref = refs.get(q["id"])
            if ref is not None:
                extra = " (truncated)" if ref.get("truncated") else ""
                print(f"  {q['id']:<12} {q['kind']:<12} {q['schema']:<7} reference rows: {ref.get('row_count', len(ref.get('rows', [])))}{extra}")
            else:
                print(f"  {q['id']:<12} {q['kind']:<12} {q['schema']:<7} expects chunks: {len(q['expected_chunk_ids'])}")
        print(f"dry run OK: {len(refs)} reference queries ran, no /query calls made")
        return 0

    path = result_path(args.out, args.label, date.today())
    existing = load_results(path)
    records: list[dict] = existing["records"]
    sessions: dict[str, str] = existing.get("sessions", {})
    meta = {
        "label": args.label, "git_commit": git_commit(), "models": model_names(),
        "started": existing["summary"].get("started") or now_iso(), "finished": None,
    }
    todo = pending(questions, records)
    print(f"{len(records)} already recorded in {path}; {len(todo)} to ask")
    transport_errors = 0
    stopped = False
    for i, q in enumerate(todo):
        if i:
            time.sleep(args.pace)
        cid = q["conversation_id"]
        body_in: dict = {"query": q["text"], "schema": q["schema"]}
        if cid:
            body_in["session_id"] = sessions.setdefault(cid, f"eval-{cid}-{uuid.uuid4().hex[:8]}")
        t0 = time.monotonic()
        try:
            status, body = http_json("POST", "/query", body_in, QUERY_TIMEOUT_S)
        except (OSError, ValueError) as exc:
            transport_errors += 1
            print(f"  {q['id']}: transport error ({exc}); not recorded")
            if transport_errors >= MAX_CONSECUTIVE_TRANSPORT_ERRORS:
                stopped = True
                break
            continue
        transport_errors = 0
        wall_ms = int((time.monotonic() - t0) * 1000)   # seconds to ms
        detail = str(body.get("detail", ""))
        if is_stop_signal(status, detail):
            print(f"  {q['id']}: HTTP {status} {detail[:120]}; not recorded")
            stopped = True
            break
        if status != 200:
            rec = error_record(q, f"HTTP {status}: {detail[:200]}", wall_ms)
        else:
            rec = build_record(q, body, wall_ms, reference_rows=(refs.get(q["id"]) or {}).get("rows"))
        records.append(rec)
        meta["finished"] = now_iso()
        save_results(path, records, meta, sessions)
        print(f"  {q['id']}: {'correct' if rec['correct'] else 'wrong'} - {rec['detail']}")

    meta["finished"] = now_iso()
    save_results(path, records, meta, sessions)
    s = summarize(records, meta)
    print(f"recorded {s['recorded']}/{len(questions)}, correct {s['correct']}, accuracy {s['accuracy']}")
    if stopped:
        print(f"stopped early; results saved to {path}. Resume later with:\n"
              f"  python3 scripts/eval_groq.py --label {args.label} --pace {args.pace} --out {args.out}")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Fixed 30-question Groq accuracy baseline (see module docstring).")
    ap.add_argument("--label", required=True)
    ap.add_argument("--pace", type=int, default=PACE_S, help="seconds between questions")
    ap.add_argument("--dry-run", action="store_true", help="resolve and run reference SQL only; no /query calls")
    ap.add_argument("--limit", type=int, default=None, help="only the first N questions")
    ap.add_argument("--out", default=DEFAULT_OUT_DIR)
    return run(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
scripts/eval_retrieval.py
Retrieval quality on the frozen test set fixtures/golden/retrieval_v2.json
(docs/specs/rag_v2.md Phase A). It asks no model anything.

WHY IT MEASURES THE PRODUCTION PATH. The first version of this script re-built
retrieval in SQL inside the mcp container. That told us which ideas were worth
trying, but not what a user gets: the answer to a question comes from the MCP
method `vector.semantic_search`, with a tenant header and (for uploaded
documents) a workspace header. This script calls exactly that, over JSON-RPC
from inside the orchestrator container, so a change to chunking, parsing,
fusion or scoping shows up here and nowhere else needs to be re-implemented.

THE TEST SET. Gold is evidence text, not chunk ids, so it survives re-chunking.
A question is a hit at k when one of the top k passages comes from the gold
document and contains (lower-cased, whitespace collapsed) at least one evidence
string. `unanswerable` questions have no gold; for them only the top-1 score
is reported (what an abstention threshold has to separate from real hits).
Sample-corpus questions run without a workspace (the sample documents only);
upload questions run with the workspace created below, so the uploaded
documents compete with the sample corpus as they do in the app.

THE UPLOAD CORPUS. Per run, through the HTTP API with the dev token: a folder
`eval-retrieval-<timestamp>`, the three demo PDFs, wait until `ready`, a
workspace on `olist`, the folder linked to it. The workspace and the folder are
deleted in a `finally`, whatever happened, and the script then checks that no
`eval-retrieval-*` folder or workspace is left (and fails if one is). Uploads
are re-parsed and re-chunked by whatever the current code and config are, which
is the point: re-running after a parser or chunker change measures that change.

USAGE (host, from the repo root; stdlib only):
  /opt/anaconda3/bin/python3 scripts/eval_retrieval.py --label baseline
  /opt/anaconda3/bin/python3 scripts/eval_retrieval.py --label layout_hybrid --mode hybrid
  /opt/anaconda3/bin/python3 scripts/eval_retrieval.py --compare baseline layout_hybrid

  --mode dense|hybrid   forwarded to vector.semantic_search ONLY when given
  --scope all|dataset   forwarded as `scope` ONLY when given, together with the
                        question's `dataset` (the workspace's target is olist,
                        so upload questions carry "olist")
  --label NAME          also write docs/eval/retrieval_v2_<NAME>.json
  --compare A B         print two saved runs side by side (no run is made)

--mode and --scope are added to the MCP in later phases of the spec; until
then the MCP does not accept them, so a run that does not pass them is the
run of today's code.

Needs: the stack running (parser, mcp, orchestrator, ollama), secrets/api_token_dev.
Nothing it creates survives the run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

FIXTURE_PATH = REPO_ROOT / "fixtures" / "golden" / "retrieval_v2.json"
DOCS_DIR = REPO_ROOT / "demo" / "documents"
OUT_DIR = REPO_ROOT / "docs" / "eval"
BASE = "http://localhost:8090"
ORCHESTRATOR = "statesync-orchestrator-1"
MCP_RPC_URL = "http://mcp:8080/rpc"
TENANT = "t_capstone_default"
TOP_K = 10
KS = (1, 5, 10)
READY_WAIT_S = 120
HTTP_TIMEOUT_S = 120
RPC_TIMEOUT_S = 60
RUN_TIMEOUT_S = 1800
EXCERPT_CHARS = 160
FOLDER_PREFIX = "eval-retrieval-"
WORKSPACE_DATASET = "olist"

# Runs inside the orchestrator container (python stdlib only): one JSON-RPC call per question,
# timed around the call. One unrecorded warm-up call first, so model loading is not billed to
# question 1. The host scores the answers; this side only fetches and times them.
_CONTAINER_SNIPPET = r"""
import json, sys, time, urllib.request
req = json.load(sys.stdin)


def call(item):
    headers = {"Content-Type": "application/json", "X-Tenant-ID": req["tenant"]}
    if item.get("workspace"):
        headers["X-Workspace-ID"] = item["workspace"]
    params = {"query_text": item["text"], "top_k": req["top_k"]}
    params.update(item.get("extra") or {})
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "vector.semantic_search", "params": params}).encode()
    started = time.perf_counter()
    try:
        r = urllib.request.Request(req["url"], data=body, method="POST", headers=headers)
        reply = json.loads(urllib.request.urlopen(r, timeout=req["timeout_s"]).read().decode())
        error = json.dumps(reply["error"])[:300] if reply.get("error") else None
        matches = (reply.get("result") or {}).get("matches") or []
    except Exception as exc:  # recorded per question; the host decides what an error means
        error, matches = f"{type(exc).__name__}: {exc}"[:300], []
    return {"ms": (time.perf_counter() - started) * 1000.0, "error": error, "matches": matches}


call(req["items"][0])
print(json.dumps({item["id"]: call(item) for item in req["items"]}))
"""


# ── Scoring (pure; tests/test_eval_retrieval.py) ──────────────────────────────

def normalize(text: str) -> str:
    """Lower-cased with every whitespace run collapsed to one space: the matching rule of the spec."""
    return " ".join(str(text).lower().split())


def passage_hits(passage: dict[str, Any], gold_ids: set[str], evidence: list[str]) -> bool:
    """A passage is a hit when it is from a gold document and contains at least one evidence string."""
    if passage.get("document_id") not in gold_ids:
        return False
    content = normalize(passage.get("content_md") or "")
    return any(normalize(e) in content for e in evidence if normalize(e))


def first_hit_rank(passages: list[dict[str, Any]], gold_ids: set[str], evidence: list[str], k: int = TOP_K) -> int | None:
    """1-based rank of the first hit within the top k passages, or None."""
    for rank, passage in enumerate(passages[:k], start=1):
        if passage_hits(passage, gold_ids, evidence):
            return rank
    return None


def score_question(question: dict[str, Any], passages: list[dict[str, Any]], gold_ids: set[str]) -> dict[str, Any]:
    """Per-question outcome. `unanswerable` questions carry no hit fields: only the top-1 score matters."""
    top = passages[0] if passages else {}
    result: dict[str, Any] = {"top1_score": top.get("score"), "top1_document": top.get("document_id")}
    if question["category"] == "unanswerable":
        return result
    rank = first_hit_rank(passages, gold_ids, question["evidence"])
    result["first_hit_rank"] = rank
    for k in KS:
        result[f"hit@{k}"] = rank is not None and rank <= k
    result["rr"] = 1.0 / rank if rank else 0.0
    return result


def percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolated percentile (pct in 0..100); None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * pct / 100.0
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Metrics over question rows: hit@k and MRR over the answerable ones, score spread over the unanswerable."""
    answerable = [r for r in rows if r["category"] != "unanswerable"]
    unanswerable = [r for r in rows if r["category"] == "unanswerable"]
    latencies = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
    out: dict[str, Any] = {"n": len(rows), "n_answerable": len(answerable), "n_unanswerable": len(unanswerable),
                           "latency_p50_ms": percentile(latencies, 50), "latency_p90_ms": percentile(latencies, 90)}
    n = len(answerable)
    for k in KS:
        out[f"hit@{k}"] = sum(bool(r[f"hit@{k}"]) for r in answerable) / n if n else None
    out["mrr@10"] = sum(r["rr"] for r in answerable) / n if n else None
    out["unanswerable_top1"] = score_spread([r["top1_score"] for r in unanswerable if r.get("top1_score") is not None])
    out["answerable_top1"] = score_spread([r["top1_score"] for r in answerable if r.get("top1_score") is not None])
    return out


def score_spread(scores: list[float]) -> dict[str, float] | None:
    if not scores:
        return None
    return {"n": len(scores), "min": min(scores), "p25": percentile(scores, 25), "median": percentile(scores, 50),
            "p75": percentile(scores, 75), "max": max(scores), "mean": sum(scores) / len(scores)}


def group_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """overall, per corpus, per category, in a stable order."""
    groups: dict[str, list[dict[str, Any]]] = {"overall": rows}
    for corpus in sorted({r["corpus"] for r in rows}):
        groups[f"corpus:{corpus}"] = [r for r in rows if r["corpus"] == corpus]
    for category in sorted({r["category"] for r in rows}):
        groups[f"category:{category}"] = [r for r in rows if r["category"] == category]
    return groups


def summarise_groups(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {name: summarise(members) for name, members in group_rows(rows).items()}


# ── Formatting ────────────────────────────────────────────────────────────────

def _pct(value: float | None) -> str:
    return "  n/a" if value is None else f"{value * 100:5.1f}"


def _num(value: float | None, width: int = 7, digits: int = 0) -> str:
    return " " * (width - 3) + "n/a" if value is None else f"{value:{width}.{digits}f}"


def format_table(summary: dict[str, dict[str, Any]]) -> str:
    """The run's metrics table: hit rates in percent and MRR over the answerable questions (`ans`), latency in ms over all `n`."""
    lines = [f"{'group':24s} {'n':>4s} {'ans':>4s} {'hit@1':>6s} {'hit@5':>6s} {'hit@10':>6s} {'MRR@10':>7s} {'p50 ms':>7s} {'p90 ms':>7s}"]
    for name, s in summary.items():
        lines.append(f"{name:24s} {s['n']:4d} {s['n_answerable']:4d} {_pct(s['hit@1']):>6s} {_pct(s['hit@5']):>6s} {_pct(s['hit@10']):>6s} "
                     f"{_num(s['mrr@10'], 7, 3)} {_num(s['latency_p50_ms'])} {_num(s['latency_p90_ms'])}")
    lines.append("")
    lines.append("top-1 score (cosine) of unanswerable questions vs answerable ones:")
    lines.append(f"{'group':24s} {'n':>4s} {'min':>7s} {'p25':>7s} {'median':>7s} {'p75':>7s} {'max':>7s}")
    overall = summary["overall"]
    for label, key in (("unanswerable", "unanswerable_top1"), ("answerable", "answerable_top1")):
        sp = overall[key]
        if sp:
            lines.append(f"{label:24s} {sp['n']:4d} " + " ".join(_num(sp[c], 7, 3) for c in ("min", "p25", "median", "p75", "max")))
    for corpus in ("sample", "upload"):
        sp = (summary.get(f"corpus:{corpus}") or {}).get("unanswerable_top1")
        if sp:
            lines.append(f"{'unanswerable/' + corpus:24s} {sp['n']:4d} " + " ".join(_num(sp[c], 7, 3) for c in ("min", "p25", "median", "p75", "max")))
    return "\n".join(lines)


def format_compare(a_name: str, a: dict[str, Any], b_name: str, b: dict[str, Any]) -> str:
    """Two saved runs side by side: each cell is `A -> B`; the sets must be the same file."""
    lines = []
    if a.get("fixture_sha256") != b.get("fixture_sha256"):
        lines.append("WARNING: the two runs used different test sets; the numbers are not comparable.")
    lines.append(f"A = {a_name}   B = {b_name}")
    lines.append(f"{'group':24s} {'ans':>4s} {'hit@1 A -> B':>15s} {'hit@5 A -> B':>15s} {'hit@10 A -> B':>15s} {'MRR@10 A -> B':>15s} {'p50 ms A -> B':>15s}")
    sa, sb = a["summary"], b["summary"]
    for name in sa:
        if name not in sb:
            continue
        x, y = sa[name], sb[name]
        cells = [f"{_pct(x[m]).strip():>6s} -> {_pct(y[m]).strip():<5s}" for m in ("hit@1", "hit@5", "hit@10")]
        cells.append(f"{_num(x['mrr@10'], 5, 3).strip():>6s} -> {_num(y['mrr@10'], 5, 3).strip():<5s}")
        cells.append(f"{_num(x['latency_p50_ms'], 5).strip():>6s} -> {_num(y['latency_p50_ms'], 5).strip():<5s}")
        lines.append(f"{name:24s} {x['n_answerable']:4d} " + " ".join(f"{c:>15s}" for c in cells))
    for label, key in (("unanswerable top-1 median", "unanswerable_top1"), ("answerable top-1 median", "answerable_top1")):
        x, y = sa["overall"].get(key), sb["overall"].get(key)
        if x and y:
            lines.append(f"{label}: {x['median']:.3f} -> {y['median']:.3f}")
    return "\n".join(lines)


# ── HTTP API (the upload corpus) ──────────────────────────────────────────────

def api(method: str, path: str, token: str, body: dict | None = None, raw: bytes | None = None,
        content_type: str = "application/json", filename: str | None = None) -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": content_type}
    if filename is not None:
        headers["X-Filename"] = urllib.parse.quote(filename)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(text or "{}")
        except json.JSONDecodeError:
            return exc.code, {"raw": text}


def wait_until_ready(token: str, folder_id: str, expected: int) -> dict[str, dict]:
    """Poll the folder until every document has left `processing`; all must be `ready`."""
    deadline = time.monotonic() + READY_WAIT_S
    documents: list[dict] = []
    while time.monotonic() < deadline:
        _, listing = api("GET", f"/library/documents?folder_id={folder_id}", token)
        documents = listing.get("documents") or []
        if len(documents) == expected and all(d.get("status") != "processing" for d in documents):
            break
        time.sleep(1)
    not_ready = [(d.get("title"), d.get("status")) for d in documents if d.get("status") != "ready"]
    if len(documents) != expected or not_ready:
        raise RuntimeError(f"the upload corpus did not become ready: {len(documents)}/{expected} documents, not ready: {not_ready}")
    return {d["document_id"]: d for d in documents}


def leftovers(token: str) -> list[str]:
    """Names of any `eval-retrieval-*` folder or workspace still on the account."""
    left = []
    _, folders = api("GET", "/library/folders", token)
    left += [f"folder {f['name']}" for f in folders.get("folders", []) if f.get("name", "").startswith(FOLDER_PREFIX)]
    _, workspaces = api("GET", "/workspaces", token)
    left += [f"workspace {w['name']}" for w in workspaces.get("workspaces", []) if w.get("name", "").startswith(FOLDER_PREFIX)]
    return left


def extra_params(args: argparse.Namespace, question: dict[str, Any]) -> dict[str, Any]:
    """Only what the caller asked for: today's MCP accepts neither mode, scope nor dataset."""
    extra: dict[str, Any] = {}
    if args.mode:
        extra["mode"] = args.mode
    if args.scope:
        extra["scope"] = args.scope
        extra["dataset"] = WORKSPACE_DATASET if question["corpus"] == "upload" else question["dataset"]
    return extra


def run_questions(questions: list[dict], workspace_id: str, args: argparse.Namespace) -> dict[str, dict]:
    items = [{"id": q["id"], "text": q["question"], "extra": extra_params(args, q),
              "workspace": workspace_id if q["corpus"] == "upload" else None} for q in questions]
    payload = {"url": MCP_RPC_URL, "tenant": TENANT, "top_k": TOP_K, "timeout_s": RPC_TIMEOUT_S, "items": items}
    out = subprocess.run(["docker", "exec", "-i", ORCHESTRATOR, "python", "-c", _CONTAINER_SNIPPET],
                         input=json.dumps(payload), capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
    if out.returncode:
        raise RuntimeError(f"the search run failed inside {ORCHESTRATOR}: {out.stderr.strip()[-500:]}")
    return json.loads(out.stdout)


def run(args: argparse.Namespace) -> dict[str, Any]:
    from api_auth import api_token  # same token resolution as the verify scripts; never printed

    token = api_token()
    fixture_bytes = FIXTURE_PATH.read_bytes()
    questions = json.loads(fixture_bytes)["questions"]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder_id = workspace_id = None
    failures: list[str] = []
    try:
        status, body = api("POST", "/library/folders", token, body={"name": f"{FOLDER_PREFIX}{stamp}"})
        if status != HTTPStatus.CREATED:
            raise RuntimeError(f"could not create the folder: {status} {body}")
        folder_id = body["folder"]["folder_id"]
        by_file: dict[str, str] = {}
        for pdf in sorted(DOCS_DIR.glob("*.pdf")):
            status, body = api("POST", f"/library/folders/{folder_id}/documents", token, raw=pdf.read_bytes(),
                               content_type="application/pdf", filename=pdf.name)
            if status != HTTPStatus.ACCEPTED:
                raise RuntimeError(f"upload of {pdf.name} refused: {status} {body}")
            by_file[pdf.name] = body["document"]["document_id"]
        wait_until_ready(token, folder_id, len(by_file))
        status, body = api("POST", "/workspaces", token, body={"name": f"{FOLDER_PREFIX}{stamp}", "target_schema": WORKSPACE_DATASET})
        if status != HTTPStatus.CREATED:
            raise RuntimeError(f"could not create the workspace: {status} {body}")
        workspace_id = body["workspace"]["workspace_id"]
        status, body = api("PUT", f"/workspaces/{workspace_id}/folders", token, body={"folder_ids": [folder_id]})
        if status != HTTPStatus.OK:
            raise RuntimeError(f"could not link the folder: {status} {body}")

        answers = run_questions(questions, workspace_id, args)
    finally:
        if workspace_id:
            status, _ = api("DELETE", f"/workspaces/{workspace_id}", token)
            if status != HTTPStatus.OK:
                failures.append(f"workspace {workspace_id} not deleted ({status})")
        if folder_id:
            status, _ = api("DELETE", f"/library/folders/{folder_id}", token)
            if status != HTTPStatus.OK:
                failures.append(f"folder {folder_id} not deleted ({status})")
        left = leftovers(token)
        if left:
            failures.append("left on the account: " + ", ".join(left))
        print("cleanup: " + ("FAILED - " + "; ".join(failures) if failures else "workspace and folder deleted; no eval-retrieval-* folder or workspace left"))
    if failures:
        raise RuntimeError("cleanup failed: " + "; ".join(failures))

    rows = []
    errors = 0
    for q in questions:
        answer = answers[q["id"]]
        passages = answer["matches"][:TOP_K]
        gold_ids = {by_file[q["gold_document"]]} if q["corpus"] == "upload" and q["gold_document"] else (
            {q["gold_document"]} if q["gold_document"] else set())
        errors += bool(answer["error"])
        row = {"id": q["id"], "corpus": q["corpus"], "dataset": q["dataset"], "category": q["category"],
               "question": q["question"], "gold_document": q["gold_document"], "gold_document_id": sorted(gold_ids)[0] if gold_ids else None,
               "latency_ms": answer["ms"], "error": answer["error"]}
        row.update(score_question(q, passages, gold_ids))
        row["passages"] = [{"rank": i, "chunk_id": p.get("chunk_id"), "document_id": p.get("document_id"), "title": p.get("title"),
                            "page": p.get("page"), "score": p.get("score"), "hit": passage_hits(p, gold_ids, q["evidence"]),
                            "excerpt": " ".join((p.get("content_md") or "").split())[:EXCERPT_CHARS]}
                           for i, p in enumerate(passages, start=1)]
        rows.append(row)
    return {"label": args.label, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(), "top_k": TOP_K,
            "params": {"mode": args.mode, "scope": args.scope}, "errors": errors,
            "summary": summarise_groups(rows), "questions": rows}


def saved_path(label: str) -> Path:
    return OUT_DIR / f"retrieval_v2_{label}.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieval quality on the frozen test set, via the production MCP path.")
    parser.add_argument("--label", help="write docs/eval/retrieval_v2_<label>.json")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"), help="print two saved runs side by side")
    parser.add_argument("--mode", choices=("dense", "hybrid"), help="forwarded to vector.semantic_search only when given")
    parser.add_argument("--scope", choices=("all", "dataset"), help="forwarded to vector.semantic_search only when given")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing saved run with this label")
    args = parser.parse_args()

    if args.compare:
        runs = []
        for label in args.compare:
            if not saved_path(label).is_file():
                print(f"no saved run {saved_path(label)}")
                return 2
            runs.append(json.loads(saved_path(label).read_text()))
        print(format_compare(args.compare[0], runs[0], args.compare[1], runs[1]))
        return 0
    if args.label and saved_path(args.label).exists() and not args.overwrite:
        print(f"{saved_path(args.label)} exists; pick another label or pass --overwrite")
        return 2

    result = run(args)
    print()
    print(format_table(result["summary"]))
    if result["errors"]:
        print(f"\n{result['errors']} question(s) returned an error from the MCP (see the 'error' fields).")
    if args.label:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        saved_path(args.label).write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
        print(f"\nwrote {saved_path(args.label)}  (test set sha256 {result['fixture_sha256']})")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())

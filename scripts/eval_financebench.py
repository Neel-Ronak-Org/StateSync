#!/usr/bin/env python3
"""
scripts/eval_financebench.py
Retrieval on real company filings: the 150 FinanceBench questions against the 84 filings
they cite (docs/specs/external_eval.md Part 1). It asks no model anything.

WHY THIS EXISTS. The RAG v2 numbers (docs/eval/retrieval_v2_report.md) came from a test set
and documents the project wrote itself. FinanceBench is the out-of-sample check: filings the
project did not choose, questions written by financial analysts. It reuses the production
search path of scripts/eval_retrieval.py unchanged (the in-container JSON-RPC call, the HTTP
helpers, the percentile code), so a number here means the same thing as a number there.

THE GOLD. FinanceBench names a filing and a page per evidence entry, not a passage. Its pages
count from 0 and StateSync's from 1 (docs/eval/external_datasets.md has the check), so the gold
for a question is the set of (filing, evidence_page_num + 1). A passage is a page hit when it
is from a gold filing and its `page` is a gold page. Page+-1 hit widens that by one page, because
a table often continues onto the next. Document hit only asks for the filing. All three are
scored over the top 10 passages; MRR@10 is on page hits. Another page that also answers the
question counts as a miss: that is a property of the gold, and the report says so.

REFUSED FILINGS. Filings the product refuses to ingest (more than [library] max_pages, a parse
timeout, ...) are recorded, not worked around: the limits are not touched. A question on such a
filing counts as a miss in `overall`, because the product cannot answer it, and is left out of
`ingested`, so a reader sees both denominators.

THE THREE SUBCOMMANDS (host, from the repo root; stdlib only; the stack must be running):
  ingest  --chunker {paragraph,layout}    one folder, the 84 PDFs uploaded ONE AT A TIME in sorted
                                          order (concurrent ingests would share one CPU Ollama and
                                          could trip processing_stale_s), each waited for until it
                                          leaves `processing`. Resumable: a re-run skips filings
                                          already in the folder, and refusals the record holds.
                                          Writes docs/eval/financebench_ingest_<chunker>.json
                                          (folder id first, so a crash leaves it findable).
  query   --chunker C --mode M --scope S --label L
                                          a workspace linked to that folder, the 150 questions
                                          through vector.semantic_search (top_k 10), the workspace
                                          deleted in a `finally`, the folder kept; writes
                                          docs/eval/financebench_<L>.json.
                                          --scope uploads sends scope="dataset", dataset="live"
                                          (live has no sample documents: only the filings are
                                          searched, as on an install with no demo corpus);
                                          --scope all sends no scope, so the 20,245-chunk demo
                                          corpus stays in as a distractor, as shipped.
  cleanup --chunker C                     delete that ingest's folder; confirm no
                                          eval-financebench-* folder or workspace is left.
  --compare A B                           two saved runs side by side (no run is made).

Test hooks, for the smoke test only (the saved runs use neither): --work-dir keeps records and
runs somewhere other than docs/eval, --only restricts ingest and query to named filings.

The chunker is a property of the orchestrator PROCESS (config is read at start), so ingest and
query refuse to run unless [library] chunker in statesync-orchestrator-1 equals --chunker and
the container started after the config file was last written.

Needs: the stack running (parser, mcp, orchestrator, ollama), secrets/api_token_dev. The dev
token is never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# The production search path and helpers are reused as they are, so numbers are comparable with
# the RAG v2 runs. eval_retrieval.py's behaviour is not changed.
import eval_retrieval as er  # noqa: E402

API = er.api  # (method, path, token, body=, raw=, content_type=, filename=) -> (status, json)
percentile = er.percentile
_CONTAINER_SNIPPET = er._CONTAINER_SNIPPET  # noqa: SLF001 - the exact in-container call of eval_retrieval.py
ORCHESTRATOR = er.ORCHESTRATOR
MCP_RPC_URL = er.MCP_RPC_URL
TENANT = er.TENANT
TOP_K = er.TOP_K
KS = er.KS
RPC_TIMEOUT_S = er.RPC_TIMEOUT_S
RUN_TIMEOUT_S = er.RUN_TIMEOUT_S
EXCERPT_CHARS = er.EXCERPT_CHARS
WORKSPACE_DATASET = er.WORKSPACE_DATASET  # "olist", as the RAG v2 harness

DATA_DIR = REPO_ROOT / "data" / "external" / "financebench"
QUESTIONS_PATH = DATA_DIR / "data" / "financebench_open_source.jsonl"
DOC_INFO_PATH = DATA_DIR / "data" / "financebench_document_information.jsonl"
PDF_DIR = DATA_DIR / "pdfs"
CHECKSUMS_PATH = REPO_ROOT / "fixtures" / "external_eval_checksums.txt"
CHECKSUM_KEY = "financebench/data/financebench_open_source.jsonl"
OUT_DIR = REPO_ROOT / "docs" / "eval"
CONFIG_PATH = REPO_ROOT / "config" / "statesync.toml"
FOLDER_PREFIX = "eval-financebench-"
CHUNKERS = ("paragraph", "layout")
LIVE_DATASET = "live"  # dataset with no sample documents (config [retrieval.dataset_document_prefixes])
PARSER = "statesync-parser-1"
PAGE_OFFSET = 1  # FinanceBench pages count from 0, StateSync's from 1
PAGE_TOLERANCE = 1  # the secondary measure: a page within one of a gold page
PROCESSING = "processing"
READY = "ready"
FAILED = "failed"
# A refusal that repeats for the same reason on every attempt; re-running the ingest skips these.
TERMINAL_REFUSALS = (HTTPStatus.REQUEST_ENTITY_TOO_LARGE, HTTPStatus.UNSUPPORTED_MEDIA_TYPE, HTTPStatus.UNPROCESSABLE_ENTITY)
POLL_S = 2
# processing_stale_s is 1800: past that the API itself reports the document as failed.
READY_WAIT_S = 1800 + 120
# A stack that is broken fails every filing the same way; stop rather than record 80 identical failures.
MAX_CONSECUTIVE_BAD = 3
# Parser / orchestrator log lines worth keeping per filing: the application's own timestamped
# WARNING / ERROR lines and the parser's `parsed` / `refused` lines (pages, blocks, reason code).
LOG_LINE_PATTERN = re.compile(r"^\d{4}-\d\d-\d\d .* (WARNING|ERROR|INFO (parsed|refused|library_ingest)) ")
LOG_LINES_KEPT = 12
LOG_LINE_CHARS = 300
FALLBACK_PATTERN = re.compile(r"layout pass out of time at page (\d+)|layout pass failed|page (\d+) layout could not be read|layout chunker produced no chunks")

# Config read inside the orchestrator container: the file on its bind mount. A fresh process
# reads exactly this, so it is what the orchestrator will chunk with after a recreate.
_CONFIG_SNIPPET = (
    "import json, tomllib; "
    "s = tomllib.load(open('/app/config/statesync.toml', 'rb')); f = tomllib.load(open('/app/config/flags.toml', 'rb')); "
    "keep = ('chunker', 'max_pages', 'max_upload_bytes', 'parse_timeout_s', 'blocks_time_budget_s', 'processing_stale_s'); "
    "print(json.dumps({'library': {k: s['library'][k] for k in keep}, "
    "'hybrid_dense_lists': s['retrieval']['hybrid_dense_lists'], "
    "'pipeline': {k: f['pipeline'][k] for k in ('retrieval_mode', 'doc_scope', 'doc_abstain')}}))"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# ── Gold and scoring (pure; tests/test_eval_financebench.py) ──────────────────

def gold_pages(question: dict[str, Any]) -> set[tuple[str, int]]:
    """The gold set of (filing, StateSync page): FinanceBench's 0-based evidence page + 1."""
    return {(e["doc_name"], int(e["evidence_page_num"]) + PAGE_OFFSET) for e in question["evidence"]}


def _page_of(passage: dict[str, Any]) -> int | None:
    try:
        return int(passage["page"])
    except (KeyError, TypeError, ValueError):
        return None  # text files and unparsed passages carry no page; they can never be a page hit


def page_hit(filing: str | None, page: int | None, gold: set[tuple[str, int]], tolerance: int = 0) -> bool:
    """True when `filing`'s `page` is a gold page, or within `tolerance` pages of one."""
    if filing is None or page is None:
        return False
    return any(filing == g_filing and abs(page - g_page) <= tolerance for g_filing, g_page in gold)


def first_rank(flags: list[bool], k: int = TOP_K) -> int | None:
    """1-based rank of the first True within the top k, or None."""
    for rank, flag in enumerate(flags[:k], start=1):
        if flag:
            return rank
    return None


def score_question(question: dict[str, Any], passages: list[dict[str, Any]], name_by_id: dict[str, str],
                   ingested: bool) -> dict[str, Any]:
    """Per-question outcome over the top 10 passages.

    `name_by_id` maps a passage's document_id to its filing name (the ingest record); a passage
    from anywhere else, such as the demo corpus, maps to None and can never be a hit.
    A question whose filing was not ingested `ready` is a miss by definition: the product cannot
    answer it (the hit fields are all False, rr 0), and `gold_ingested` takes it out of `ingested`.
    """
    gold = gold_pages(question)
    gold_filings = {g[0] for g in gold}
    top = passages[:TOP_K]
    names = [name_by_id.get(p.get("document_id")) for p in top]
    pages = [_page_of(p) for p in top]
    flags = {
        "page": [ingested and page_hit(n, pg, gold) for n, pg in zip(names, pages, strict=True)],
        "page1": [ingested and page_hit(n, pg, gold, PAGE_TOLERANCE) for n, pg in zip(names, pages, strict=True)],
        "doc": [ingested and n in gold_filings for n in names],
    }
    result: dict[str, Any] = {"gold_ingested": ingested, "names": names, "flags": flags}
    for kind, values in flags.items():
        rank = first_rank(values)
        result[f"first_{kind}_rank"] = rank
        for k in KS:
            result[f"{kind}_hit@{k}"] = rank is not None and rank <= k
    page_rank = result["first_page_rank"]
    result["rr"] = 1.0 / page_rank if page_rank else 0.0
    return result


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Metrics over question rows. Rates are over ALL rows of the group (a refused filing is a miss);
    `n_ingested` says how many of them the product could answer. Latency is over the rows' calls."""
    n = len(rows)
    out: dict[str, Any] = {"n": n, "n_ingested": sum(bool(r["gold_ingested"]) for r in rows)}
    for kind in ("page", "page1", "doc"):
        for k in KS:
            out[f"{kind}_hit@{k}"] = sum(bool(r[f"{kind}_hit@{k}"]) for r in rows) / n if n else None
    out["mrr@10"] = sum(r["rr"] for r in rows) / n if n else None
    latencies = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
    out["latency_p50_ms"] = percentile(latencies, 50)
    out["latency_p90_ms"] = percentile(latencies, 90)
    return out


def group_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """overall (all questions; a refused filing is a miss), ingested, refused, then per question type
    and per gold filing type, over all questions and over the ingested ones only. Stable order."""
    ingested = [r for r in rows if r["gold_ingested"]]
    groups: dict[str, list[dict[str, Any]]] = {
        "overall": rows, "ingested": ingested, "refused": [r for r in rows if not r["gold_ingested"]]}
    for prefix, members in (("", rows), ("ingested/", ingested)):
        for field in ("question_type", "doc_type"):
            for value in sorted({r[field] for r in rows}):
                groups[f"{prefix}{field}:{value}"] = [r for r in members if r[field] == value]
    return groups


def summarise_groups(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {name: summarise(members) for name, members in group_rows(rows).items() if members or name in ("ingested", "refused")}


# ── Ingest bookkeeping (pure) ─────────────────────────────────────────────────

def refusal_for(status: int | None, body: Any) -> str | None:
    """None for an accepted upload; otherwise exactly what the API returned, as text."""
    if status == HTTPStatus.ACCEPTED:
        return None
    return json.dumps(body, ensure_ascii=False)[:600] if isinstance(body, dict) else str(body)[:600]


def layout_fallback(log_lines: list[str]) -> dict[str, Any] | None:
    """What the parser / orchestrator logs say about the layout pass falling back to flat text for
    this filing: the first page it stopped at, or that it failed whole. None when nothing was logged."""
    matched = [m for line in log_lines if (m := FALLBACK_PATTERN.search(line))]
    if not matched:
        return None
    pages = [int(g) for m in matched for g in m.groups() if g]
    return {"lines": len(matched), "first_page": min(pages) if pages else None}


def summarise_ingest(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Totals over the per-filing ingest records."""
    refused = [d for d in documents if d["http_status"] != HTTPStatus.ACCEPTED]
    accepted = [d for d in documents if d["http_status"] == HTTPStatus.ACCEPTED]
    ready = [d for d in accepted if d.get("status") == READY]
    not_ready = [d for d in accepted if d.get("status") != READY]
    return {
        "filings": len(documents), "ready": len(ready), "refused": len(refused), "failed_or_stuck": len(not_ready),
        "refused_filings": [d["doc_name"] for d in refused], "failed_filings": [d["doc_name"] for d in not_ready],
        "chunks": sum(d.get("chunk_count") or 0 for d in ready),
        "pages_ready": sum(d.get("pages") or 0 for d in ready),
        "seconds_upload_to_ready": round(sum(d.get("ready_seconds") or 0.0 for d in documents), 1),
        "layout_fallback_filings": [d["doc_name"] for d in accepted if d.get("layout_fallback")],  # a refused filing has no chunks to affect
    }


# ── Formatting ────────────────────────────────────────────────────────────────

_PCT_METRICS = (("page_hit@1", "pg@1"), ("page_hit@5", "pg@5"), ("page_hit@10", "pg@10"), ("page1_hit@5", "pg±1@5"), ("doc_hit@5", "doc@5"))


def _pct(value: float | None) -> str:
    return "  n/a" if value is None else f"{value * 100:5.1f}"


def _num(value: float | None, width: int = 7, digits: int = 0) -> str:
    return " " * (width - 3) + "n/a" if value is None else f"{value:{width}.{digits}f}"


def format_table(summary: dict[str, dict[str, Any]]) -> str:
    """The run's metrics table: rates in percent over each group's n, MRR@10, latency in ms."""
    head = f"{'group':36s} {'n':>4s} {'ingst':>5s} " + " ".join(f"{label:>6s}" for _, label in _PCT_METRICS) + f" {'MRR@10':>7s} {'p50 ms':>7s} {'p90 ms':>7s}"
    lines = [head]
    for name, s in summary.items():
        lines.append(f"{name:36s} {s['n']:4d} {s['n_ingested']:5d} " + " ".join(f"{_pct(s[m]):>6s}" for m, _ in _PCT_METRICS)
                     + f" {_num(s['mrr@10'], 7, 3)} {_num(s['latency_p50_ms'])} {_num(s['latency_p90_ms'])}")
    return "\n".join(lines)


def format_compare(a_name: str, a: dict[str, Any], b_name: str, b: dict[str, Any]) -> str:
    """Two saved runs side by side: each cell is `A -> B` (percent, MRR in 0..1, p50 in ms)."""
    lines = []
    if a.get("fixture_sha256") != b.get("fixture_sha256"):
        lines.append("WARNING: the two runs used different question files; the numbers are not comparable.")
    lines.append(f"A = {a_name}   B = {b_name}")
    cols = [("page_hit@1", "pg@1"), ("page_hit@5", "pg@5"), ("page_hit@10", "pg@10"), ("page1_hit@5", "pg±1@5"), ("doc_hit@5", "doc@5")]
    lines.append(f"{'group':36s} {'n':>4s} " + " ".join(f"{label + ' A -> B':>15s}" for _, label in cols) + f" {'MRR@10 A -> B':>15s} {'p50 ms A -> B':>15s}")
    for name in a["summary"]:
        if name not in b["summary"]:
            continue
        x, y = a["summary"][name], b["summary"][name]
        cells = [f"{_pct(x[m]).strip():>6s} -> {_pct(y[m]).strip():<5s}" for m, _ in cols]
        cells.append(f"{_num(x['mrr@10'], 5, 3).strip():>6s} -> {_num(y['mrr@10'], 5, 3).strip():<5s}")
        cells.append(f"{_num(x['latency_p50_ms'], 5).strip():>6s} -> {_num(y['latency_p50_ms'], 5).strip():<5s}")
        lines.append(f"{name:36s} {x['n']:4d} " + " ".join(f"{c:>15s}" for c in cells))
    return "\n".join(lines)


# ── Data ──────────────────────────────────────────────────────────────────────

def load_questions(only: list[str] | None = None) -> tuple[list[dict[str, Any]], str]:
    """The 150 questions verbatim, with each gold filing's doc_type, and the file's SHA-256.
    Refuses to run when the file does not match the committed checksum."""
    raw = QUESTIONS_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    expected = next((line.split()[0] for line in CHECKSUMS_PATH.read_text().splitlines() if line.endswith(CHECKSUM_KEY)), None)
    if digest != expected:
        raise RuntimeError(f"{QUESTIONS_PATH.name} sha256 {digest} does not match {CHECKSUMS_PATH.name} ({expected}); run scripts/fetch_external_eval.sh")
    doc_types = {}
    for line in DOC_INFO_PATH.read_text().splitlines():
        if line.strip():
            info = json.loads(line)
            doc_types[info["doc_name"]] = info["doc_type"]
    questions = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    for q in questions:
        q["doc_type"] = doc_types[q["doc_name"]]
    if only:
        questions = [q for q in questions if q["doc_name"] in only]
    return questions, digest


def list_pdfs(only: list[str] | None = None) -> list[Path]:
    """The PDFs to upload, in sorted order."""
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    if only:
        pdfs = [p for p in pdfs if p.stem in only]
    return pdfs


# ── Stack access ──────────────────────────────────────────────────────────────

def container_config() -> dict[str, Any]:
    """Config as the orchestrator container reads it (the bind-mounted files)."""
    out = subprocess.run(["docker", "exec", ORCHESTRATOR, "python", "-c", _CONFIG_SNIPPET], capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise RuntimeError(f"could not read the config inside {ORCHESTRATOR}: {out.stderr.strip()[-300:]}")
    return json.loads(out.stdout)


def require_chunker(chunker: str) -> dict[str, Any]:
    """Refuse to go on unless the orchestrator is chunking with `chunker`: the file says so AND the
    process started after the file was last written (config is read at process start)."""
    config = container_config()
    if config["library"]["chunker"] != chunker:
        raise RuntimeError(f"[library] chunker inside {ORCHESTRATOR} is {config['library']['chunker']!r}, not {chunker!r}")
    out = subprocess.run(["docker", "inspect", "-f", "{{.State.StartedAt}}", ORCHESTRATOR], capture_output=True, text=True, timeout=30)
    started = datetime.fromisoformat(out.stdout.strip().rstrip("Z").split(".")[0] + "+00:00")  # drop the nanoseconds
    if started.timestamp() < CONFIG_PATH.stat().st_mtime:
        raise RuntimeError(f"{ORCHESTRATOR} started before config/statesync.toml was last written; recreate it so it reads {chunker!r}")
    return config


def container_logs(name: str, since: float, until: float) -> list[str]:
    """Warning-ish log lines of one container between two unix times (fractional seconds, no margin:
    uploads are sequential, and a margin would pull in the previous filing's lines)."""
    def stamp(t: float) -> str:
        return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    out = subprocess.run(["docker", "logs", "--since", stamp(since), "--until", stamp(until), name], capture_output=True, text=True, timeout=60)
    lines = (out.stdout + out.stderr).splitlines()
    return [ln[:LOG_LINE_CHARS] for ln in lines if LOG_LINE_PATTERN.search(ln)]


def folder_documents(token: str, folder_id: str) -> list[dict[str, Any]]:
    status, listing = API("GET", f"/library/documents?folder_id={folder_id}", token)
    if status != HTTPStatus.OK:
        raise RuntimeError(f"could not list the folder {folder_id}: {status} {listing}")
    return listing.get("documents") or []


def folder_exists(token: str, folder_id: str) -> bool:
    _, folders = API("GET", "/library/folders", token)
    return any(f.get("folder_id") == folder_id for f in folders.get("folders", []))


def leftovers(token: str) -> list[str]:
    """Names of any `eval-financebench-*` folder or workspace still on the account."""
    left = []
    _, folders = API("GET", "/library/folders", token)
    left += [f"folder {f['name']}" for f in folders.get("folders", []) if f.get("name", "").startswith(FOLDER_PREFIX)]
    _, workspaces = API("GET", "/workspaces", token)
    left += [f"workspace {w['name']}" for w in workspaces.get("workspaces", []) if w.get("name", "").startswith(FOLDER_PREFIX)]
    return left


def record_path(work_dir: Path, chunker: str) -> Path:
    return work_dir / f"financebench_ingest_{chunker}.json"


def run_path(work_dir: Path, label: str) -> Path:
    return work_dir / f"financebench_{label}.json"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")


# ── ingest ────────────────────────────────────────────────────────────────────

def wait_until_settled(token: str, folder_id: str, document_id: str) -> dict[str, Any] | None:
    """Poll until the document leaves `processing`; the last view seen (still processing on timeout)."""
    deadline = time.monotonic() + READY_WAIT_S
    document = None
    while True:
        try:
            document = next((d for d in folder_documents(token, folder_id) if d["document_id"] == document_id), document)
        except (OSError, RuntimeError):
            pass  # a hiccup in the listing; the deadline decides
        if document is not None and document.get("status") != PROCESSING:
            return document
        if time.monotonic() > deadline:
            return document
        time.sleep(POLL_S)


def ingest_one(token: str, folder_id: str, pdf: Path) -> dict[str, Any]:
    """Upload one filing and wait for it. Records what the API returned, exactly."""
    data = pdf.read_bytes()
    entry: dict[str, Any] = {"doc_name": pdf.stem, "filename": pdf.name, "bytes": len(data), "started_at": _now()}
    started_wall = time.time()
    started = time.monotonic()
    try:
        status, body = API("POST", f"/library/folders/{folder_id}/documents", token, raw=data,
                           content_type="application/pdf", filename=pdf.name)
    except (OSError, urllib.error.URLError) as exc:  # includes timeouts: recorded, not hidden
        status, body = None, f"{type(exc).__name__}: {exc}"
    entry["http_status"] = status
    entry["upload_seconds"] = round(time.monotonic() - started, 1)
    entry["refusal"] = refusal_for(status, body)
    if status == HTTPStatus.ACCEPTED:
        document = body["document"]
        entry["document_id"] = document["document_id"]
        settled = wait_until_settled(token, folder_id, document["document_id"]) or document
        entry.update(status=settled.get("status"), error=settled.get("error"), pages=settled.get("pages"),
                     chunk_count=settled.get("chunk_count"))
    entry["ready_seconds"] = round(time.monotonic() - started, 1)
    ended_wall = time.time()
    logs = container_logs(PARSER, started_wall, ended_wall) + container_logs(ORCHESTRATOR, started_wall, ended_wall)
    entry["layout_fallback"] = layout_fallback(logs)  # counted over every line; the record keeps the first few
    entry["log_lines_total"] = len(logs)
    entry["log_lines"] = logs[:LOG_LINES_KEPT]
    return entry


def cmd_ingest(args: argparse.Namespace) -> int:
    from api_auth import api_token  # same token resolution as the verify scripts; never printed

    token = api_token()
    config = require_chunker(args.chunker)
    questions, digest = load_questions(args.only)
    pdfs = list_pdfs(args.only)
    if {p.stem for p in pdfs} != {q["doc_name"] for q in questions}:
        raise RuntimeError("the PDFs on disk and the filings the questions cite differ")
    path = record_path(args.work_dir, args.chunker)

    if path.is_file():
        record = json.loads(path.read_text())
        if record["chunker"] != args.chunker or not folder_exists(token, record["folder_id"]):
            raise RuntimeError(f"{path} is for another chunker or its folder is gone; clean up or move it first")
        print(f"resuming {path.name}: folder {record['folder_name']}")
    else:
        name = f"{FOLDER_PREFIX}{args.chunker}-{_stamp()}"
        status, body = API("POST", "/library/folders", token, body={"name": name})
        if status != HTTPStatus.CREATED:
            raise RuntimeError(f"could not create the folder: {status} {body}")
        record = {"chunker": args.chunker, "created": _now(), "questions_sha256": digest, "folder_id": body["folder"]["folder_id"],
                  "folder_name": name, "config": config, "documents": [], "summary": None}
        write_json(path, record)  # the folder id is on disk before the first upload
        print(f"created folder {name}")
    record["config"] = config
    documents: list[dict[str, Any]] = record["documents"]
    recorded = {d["doc_name"]: d for d in documents}
    live = {d["filename"]: d for d in folder_documents(token, record["folder_id"])}

    consecutive_bad = 0
    aborted = False
    for index, pdf in enumerate(pdfs, start=1):
        prior = recorded.get(pdf.stem)
        if pdf.name in live:  # already uploaded: adopt (waiting if it is still processing) and skip
            document = live[pdf.name]
            if document["status"] == PROCESSING:
                document = wait_until_settled(token, record["folder_id"], document["document_id"]) or document
            entry = {"doc_name": pdf.stem, "filename": pdf.name, "bytes": pdf.stat().st_size, "http_status": HTTPStatus.ACCEPTED, "refusal": None,
                     **(prior or {"note": "found in the folder on resume; timings were not measured"}),
                     "document_id": document["document_id"], "status": document["status"], "error": document.get("error"),
                     "pages": document.get("pages"), "chunk_count": document.get("chunk_count")}
            documents[:] = [d for d in documents if d["doc_name"] != pdf.stem] + [entry]
            write_json(path, record)
            print(f"[{index}/{len(pdfs)}] {pdf.stem}: already in the folder ({document['status']})")
            continue
        if prior is not None and prior["http_status"] in TERMINAL_REFUSALS:
            print(f"[{index}/{len(pdfs)}] {pdf.stem}: refusal already recorded ({prior['http_status']}); skipped")
            continue
        entry = ingest_one(token, record["folder_id"], pdf)
        documents[:] = [d for d in documents if d["doc_name"] != pdf.stem] + [entry]
        record["summary"] = summarise_ingest(documents)
        write_json(path, record)
        bad = entry["http_status"] is None or (isinstance(entry["http_status"], int) and entry["http_status"] >= HTTPStatus.INTERNAL_SERVER_ERROR) \
            or (entry["http_status"] == HTTPStatus.ACCEPTED and entry.get("status") != READY)
        consecutive_bad = consecutive_bad + 1 if bad else 0
        outcome = (f"{entry['status']}, {entry.get('pages')} pages, {entry.get('chunk_count')} chunks" if entry["http_status"] == HTTPStatus.ACCEPTED
                   else f"REFUSED {entry['http_status']}: {entry['refusal']}")
        print(f"[{index}/{len(pdfs)}] {pdf.stem}: {outcome} in {entry['ready_seconds']}s", flush=True)
        if consecutive_bad >= MAX_CONSECUTIVE_BAD:
            print(f"stopping: {consecutive_bad} filings in a row failed or errored; the stack may be unhealthy")
            aborted = True
            break

    documents.sort(key=lambda d: d["filename"])
    record["finished"] = _now()
    record["summary"] = summarise_ingest(documents)
    write_json(path, record)
    print(json.dumps(record["summary"], indent=1))
    print(f"wrote {path}")
    if aborted:
        return 3
    return 1 if record["summary"]["failed_or_stuck"] else 0


# ── query ─────────────────────────────────────────────────────────────────────

RERANK_RPC_TIMEOUT_S = 180  # a re-ranked search waits on the cross-encoder (config [retrieval].rerank_timeout_s is 120)


def run_questions(items: list[dict[str, Any]], timeout_s: float = RPC_TIMEOUT_S) -> dict[str, dict[str, Any]]:
    """The searches inside the orchestrator container (eval_retrieval's snippet): one warm-up call, then each question timed."""
    payload = {"url": MCP_RPC_URL, "tenant": TENANT, "top_k": TOP_K, "timeout_s": timeout_s, "items": items}
    out = subprocess.run(["docker", "exec", "-i", ORCHESTRATOR, "python", "-c", _CONTAINER_SNIPPET],
                         input=json.dumps(payload), capture_output=True, text=True,
                         timeout=max(RUN_TIMEOUT_S, timeout_s * (len(items) + 1)))  # +1: the warm-up call
    if out.returncode:
        raise RuntimeError(f"the search run failed inside {ORCHESTRATOR}: {out.stderr.strip()[-500:]}")
    return json.loads(out.stdout)


def search_params(args: argparse.Namespace) -> dict[str, Any]:
    """Forwarded to vector.semantic_search: always the mode; for scope `uploads`, the dataset scope with `live`."""
    extra: dict[str, Any] = {"mode": args.mode}
    if args.scope == "uploads":
        extra.update(scope="dataset", dataset=LIVE_DATASET)
    if getattr(args, "rerank", "off") != "off":  # docs/specs/rag_v3.md
        extra["rerank"] = args.rerank
    return extra


# Inside the orchestrator container: the product's own rewrite (orchestrator/query_rewrite.py) for each
# question, so the measured rewrite is exactly what the docs route would search with.
_REWRITE_SNIPPET = r"""
import asyncio, json, sys, time
sys.path.insert(0, "/app")
from query_rewrite import rewrite_for_search
req = json.load(sys.stdin)
out = {}
async def go():
    for item in req["items"]:
        query, usage = await rewrite_for_search(item["text"])
        out[item["id"]] = {"query": query, **usage}
        time.sleep(req["spacing_s"])
asyncio.run(go())
print(json.dumps(out))
"""
REWRITE_CACHE = REPO_ROOT / "docs" / "eval" / "financebench_rewrites.json"
REWRITE_SPACING_S = 2.5  # Groq free tier: 30 requests/minute


def rewrites(questions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The rewritten search query per question, cached in docs/eval so a re-run makes no model calls."""
    cache = json.loads(REWRITE_CACHE.read_text()) if REWRITE_CACHE.exists() else {}
    todo = [{"id": q["financebench_id"], "text": q["question"]} for q in questions if q["financebench_id"] not in cache]
    if todo:
        out = subprocess.run(["docker", "exec", "-i", ORCHESTRATOR, "python", "-c", _REWRITE_SNIPPET],
                             input=json.dumps({"items": todo, "spacing_s": REWRITE_SPACING_S}),
                             capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        if out.returncode:
            raise RuntimeError(f"the rewrite run failed inside {ORCHESTRATOR}: {out.stderr.strip()[-500:]}")
        cache.update(json.loads(out.stdout))
        REWRITE_CACHE.write_text(json.dumps(cache, indent=1))
    return cache


def score_rows(questions: list[dict[str, Any]], answers: dict[str, dict[str, Any]], name_by_id: dict[str, str],
               ready: set[str]) -> tuple[list[dict[str, Any]], int]:
    """The saved question rows, and how many searches returned an error."""
    rows, errors = [], 0
    for q in questions:
        answer = answers[q["financebench_id"]]
        passages = answer["matches"][:TOP_K]
        errors += bool(answer["error"])
        scored = score_question(q, passages, name_by_id, q["doc_name"] in ready)
        names, flags = scored.pop("names"), scored.pop("flags")
        row = {"id": q["financebench_id"], "company": q["company"], "doc_name": q["doc_name"], "doc_type": q["doc_type"],
               "question_type": q["question_type"], "question": q["question"],
               "gold_pages": sorted(g[1] for g in gold_pages(q)), "gold_document_id": next((i for i, n in name_by_id.items() if n == q["doc_name"]), None),
               "latency_ms": answer["ms"], "error": answer["error"], **answer["meta"], **scored}
        row["passages"] = [{"rank": i, "chunk_id": p.get("chunk_id"), "document_id": p.get("document_id"), "doc_name": names[i - 1],
                            "title": p.get("title"), "page": p.get("page"), "score": p.get("score"), "dense_score": p.get("dense_score"),
                            "keyword_score": p.get("keyword_score"), "page_hit": flags["page"][i - 1], "page1_hit": flags["page1"][i - 1],
                            "doc_hit": flags["doc"][i - 1], "excerpt": " ".join((p.get("content_md") or "").split())[:EXCERPT_CHARS]}
                           for i, p in enumerate(passages, start=1)]
        rows.append(row)
    return rows, errors


def cmd_query(args: argparse.Namespace) -> int:
    from api_auth import api_token

    token = api_token()
    config = require_chunker(args.chunker)
    out_path = run_path(args.work_dir, args.label)
    if out_path.exists() and not args.overwrite:
        print(f"{out_path} exists; pick another label or pass --overwrite")
        return 2
    path = record_path(args.work_dir, args.chunker)
    if not path.is_file():
        print(f"no ingest record {path}; run ingest first")
        return 2
    record = json.loads(path.read_text())
    questions, digest = load_questions(args.only)
    if not folder_exists(token, record["folder_id"]):
        raise RuntimeError(f"the ingest folder {record['folder_name']} is gone (cleaned up?)")
    name_by_id = {d["document_id"]: d["doc_name"] for d in record["documents"] if d.get("document_id")}
    live = {d["document_id"]: d for d in folder_documents(token, record["folder_id"])}
    ready = {name for doc_id, name in name_by_id.items() if live.get(doc_id, {}).get("status") == READY}
    recorded_status = {d["document_id"]: d.get("status") for d in record["documents"] if d.get("document_id")}
    drift = sorted(name for doc_id, name in name_by_id.items() if live.get(doc_id, {}).get("status") != recorded_status[doc_id])

    workspace_id = None
    failures: list[str] = []
    try:
        status, body = API("POST", "/workspaces", token, body={"name": f"{FOLDER_PREFIX}{_stamp()}", "target_schema": WORKSPACE_DATASET})
        if status != HTTPStatus.CREATED:
            raise RuntimeError(f"could not create the workspace: {status} {body}")
        workspace_id = body["workspace"]["workspace_id"]
        status, body = API("PUT", f"/workspaces/{workspace_id}/folders", token, body={"folder_ids": [record["folder_id"]]})
        if status != HTTPStatus.OK:
            raise RuntimeError(f"could not link the folder: {status} {body}")
        extra = search_params(args)
        rewritten = rewrites(questions) if args.rewrite == "llm" else {}
        items = [{"id": q["financebench_id"], "text": rewritten.get(q["financebench_id"], {}).get("query", q["question"]),
                  "extra": extra, "workspace": workspace_id} for q in questions]
        answers = run_questions(items, RERANK_RPC_TIMEOUT_S if args.rerank != "off" else RPC_TIMEOUT_S)
    finally:
        if workspace_id:
            status, _ = API("DELETE", f"/workspaces/{workspace_id}", token)
            if status != HTTPStatus.OK:
                failures.append(f"workspace {workspace_id} not deleted ({status})")
        stuck = [x for x in leftovers(token) if x.startswith("workspace")]
        if stuck:
            failures.append("left on the account: " + ", ".join(stuck))
        print("cleanup: " + ("FAILED - " + "; ".join(failures) if failures else "workspace deleted; the ingest folder is kept; no eval-financebench-* workspace left"))

    rows, errors = score_rows(questions, answers, name_by_id, ready)
    result = {"label": args.label, "created": _now(), "fixture_sha256": digest, "top_k": TOP_K,
              "params": {"chunker": args.chunker, "mode": args.mode, "scope": args.scope, "dataset": LIVE_DATASET if args.scope == "uploads" else None,
                         "rerank": args.rerank, "rewrite": args.rewrite,
                         "hybrid_dense_lists": config["hybrid_dense_lists"], "container_config": config,
                         "ingest_folder": record["folder_name"], "ingest_questions_sha256": record.get("questions_sha256"),
                         "filings_ready": len(ready), "filings_in_record": len(record["documents"]), "status_drift_since_ingest": drift,
                         "only": args.only},
              "errors": errors, "summary": summarise_groups(rows), "questions": rows}
    write_json(out_path, result)
    print()
    print(format_table(result["summary"]))
    if errors:
        print(f"\n{errors} question(s) returned an error from the MCP (see the 'error' fields).")
    print(f"\nwrote {out_path}  (questions sha256 {digest})")
    if failures:
        raise RuntimeError("cleanup failed: " + "; ".join(failures))
    return 1 if errors else 0


# ── cleanup ───────────────────────────────────────────────────────────────────

def cmd_cleanup(args: argparse.Namespace) -> int:
    from api_auth import api_token

    token = api_token()
    path = record_path(args.work_dir, args.chunker)
    record = json.loads(path.read_text()) if path.is_file() else None
    failures = []
    if record and folder_exists(token, record["folder_id"]):
        status, _ = API("DELETE", f"/library/folders/{record['folder_id']}", token)
        if status != HTTPStatus.OK:
            failures.append(f"folder {record['folder_name']} not deleted ({status})")
        else:
            record["cleaned_up"] = _now()
            write_json(path, record)
    left = leftovers(token)
    if left:
        failures.append("left on the account: " + ", ".join(left))
    print("cleanup: " + ("FAILED - " + "; ".join(failures) if failures else "folder deleted; no eval-financebench-* folder or workspace left"))
    return 1 if failures else 0


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="FinanceBench retrieval through the production MCP search path.")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"), help="print two saved runs side by side (no run is made)")
    parser.add_argument("--work-dir", type=Path, default=OUT_DIR, help="where records and runs live (default docs/eval; the smoke test uses a scratch dir)")
    parser.add_argument("--only", type=lambda v: v.split(","), metavar="DOC_NAME,...", help="smoke test only: restrict ingest and query to these filings")
    sub = parser.add_subparsers(dest="command")
    p_ingest = sub.add_parser("ingest", help="upload the filings one at a time into a new folder")
    p_ingest.add_argument("--chunker", choices=CHUNKERS, required=True)
    p_query = sub.add_parser("query", help="run the 150 questions against an ingest")
    p_query.add_argument("--chunker", choices=CHUNKERS, required=True)
    p_query.add_argument("--mode", choices=("dense", "hybrid"), required=True)
    p_query.add_argument("--scope", choices=("uploads", "all"), required=True)
    p_query.add_argument("--label", required=True)
    p_query.add_argument("--rerank", choices=("off", "minilm", "bge"), default="off", help="docs/specs/rag_v3.md")
    p_query.add_argument("--rewrite", choices=("off", "llm"), default="off", help="docs/specs/rag_v3.md")
    p_query.add_argument("--overwrite", action="store_true", help="replace an existing saved run with this label")
    p_clean = sub.add_parser("cleanup", help="delete an ingest's folder and check nothing is left")
    p_clean.add_argument("--chunker", choices=CHUNKERS, required=True)
    args = parser.parse_args()

    if args.compare:
        runs = []
        for label in args.compare:
            if not run_path(args.work_dir, label).is_file():
                print(f"no saved run {run_path(args.work_dir, label)}")
                return 2
            runs.append(json.loads(run_path(args.work_dir, label).read_text()))
        print(format_compare(args.compare[0], runs[0], args.compare[1], runs[1]))
        return 0
    if not args.command:
        parser.error("a subcommand (ingest, query, cleanup) or --compare A B is required")
    return {"ingest": cmd_ingest, "query": cmd_query, "cleanup": cmd_cleanup}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
scripts/eval_writer_window.py
Part D v3, gates G2 and G3 (docs/specs/routing_docs_fixes.md §3): what the answer WRITER reads.

The re-ranker's +16 points (rag_v3_report.md) were measured on one search call. The writer reads
something else: the first `retrieval.semantic_top_k` passages of the documents route's two-round search
after its merge and ordering. This runs that exact code (`_semantic_search_expanded`, as the documents
route calls it) inside Part D's own orchestrator container (research/paperb/partd_stack.sh), for each
configuration, and scores the writer's window against the gold. No model is called.

Sets
  financebench  FinanceBench's 150 questions, in a workspace linked only to the FinanceBench folder
                (dataset olist, as Part D asks them). Gold: the (filing, page) pages; also the filing alone.
  inhouse       the in-house set's sample-corpus questions with a gold (fixtures/golden/retrieval_v2.json,
                corpus "sample", not "unanswerable"): a hit is a passage of the gold document containing an
                evidence string (eval_retrieval.passage_hits). Its "table" questions are the case the
                document order was built for, so they are reported apart.
Configurations
  doc_off  shipped order, no re-ranking    rel_off  relevance order, no re-ranking
  doc_bge  shipped order, bge               rel_bge  relevance order, bge
  rel_bge_titles  relevance order, bge reading document titles

    /opt/anaconda3/bin/python3 scripts/eval_writer_window.py run [--sets financebench,inhouse] [--configs ...]
    /opt/anaconda3/bin/python3 scripts/eval_writer_window.py report
Output: docs/eval/partd_writer_window.json (resumable by set and configuration).
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import eval_retrieval as er  # noqa: E402
import paperb_docs as pd  # noqa: E402

OUT = REPO / "docs" / "eval" / "partd_writer_window.json"
CONTAINER = "statesync-orchestrator-paperb"
API_BASE = "http://127.0.0.1:8091"
TENANT = er.TENANT
CONFIGS = {"doc_off": ("document", "off", False), "rel_off": ("relevance", "off", False),
           "doc_bge": ("document", "bge", False), "rel_bge": ("relevance", "bge", False),
           "rel_bge_titles": ("relevance", "bge", True)}
PER_ITEM_TIMEOUT_S = 400    # noqa: literals - two re-ranked searches under GPU contention, with margin

_SNIPPET = r"""
import asyncio, json, os, sys, time
sys.path.insert(0, "/app")
from config_loader import cfg
from mcp_client import MCPSession
from nodes.unstructured_knowledge import _semantic_search_expanded
req = json.load(sys.stdin)
order, rerank, titles = req["conf"]


async def one(item):
    async with MCPSession(tenant_id=req["tenant"], base_url=os.environ.get("MCP_URL"), session_id="writer-window-" + item["id"],
                          workspace_id=item.get("workspace"), dataset=item.get("dataset")) as session:
        matches, _ = await _semantic_search_expanded(
            session, item["text"], cfg.retrieval.semantic_top_k, "dense", item.get("dataset"), "all", False,
            rerank, passage_order=order, rerank_titles=titles)
    return [{"document_id": m["document_id"], "chunk_id": m["chunk_id"], "page": m.get("page"),
             "score": m.get("score"), "rerank_score": m.get("rerank_score"),
             **({"content_md": m.get("content_md")} if req["content"] else {})} for m in matches]


async def main():
    print(json.dumps({"window": int(cfg.retrieval.semantic_top_k)}), flush=True)
    for item in req["items"]:
        t = time.monotonic()
        try:
            rec = {"id": item["id"], "passages": await one(item)}
        except Exception as exc:
            rec = {"id": item["id"], "error": f"{type(exc).__name__}: {exc}"[:300]}
        rec["ms"] = round((time.monotonic() - t) * 1000)
        print(json.dumps(rec), flush=True)

asyncio.run(main())
"""


def items_for(set_name: str, workspace: str | None) -> list[dict]:
    if set_name == "financebench":
        return [{"id": q["financebench_id"], "text": q["question"], "workspace": workspace, "dataset": pd.WORKSPACE_DATASET}
                for q in pd.questions()]
    qs = json.loads((REPO / "fixtures" / "golden" / "retrieval_v2.json").read_text())["questions"]
    return [{"id": q["id"], "text": q["question"], "dataset": q["dataset"]} for q in qs
            if q["corpus"] == "sample" and q["category"] != "unanswerable"]


def run_config(set_name: str, conf: tuple, items: list[dict]) -> dict:
    payload = {"tenant": TENANT, "conf": list(conf), "content": set_name == "inhouse", "items": items}
    res = subprocess.run(["docker", "exec", "-i", CONTAINER, "python", "-c", _SNIPPET], input=json.dumps(payload),
                         capture_output=True, text=True, timeout=PER_ITEM_TIMEOUT_S * (len(items) + 1))
    lines = [json.loads(line) for line in res.stdout.splitlines() if line.startswith("{")]
    if not lines:
        raise RuntimeError(f"no output from {CONTAINER}: {res.stderr.strip()[-600:]}")
    window = lines[0]["window"]
    return {"window": window, "results": {r["id"]: r for r in lines[1:]}, "stderr_tail": res.stderr.strip()[-300:]}


def cmd_run(sets: list[str], configs: list[str]) -> int:
    from api_auth import api_token
    record = json.loads(OUT.read_text()) if OUT.exists() else {"runs": {}}
    token = api_token()
    workspace = None
    try:
        if "financebench" in sets:
            ingest = json.loads(pd.INGEST.read_text())
            st, body = pd.api(API_BASE, "POST", "/workspaces", token, {"name": f"partd-window-{int(time.time())}",
                                                                      "target_schema": pd.WORKSPACE_DATASET})
            if st != 201:   # noqa: literals - HTTP Created
                raise RuntimeError(f"could not create the workspace: {st} {body}")
            workspace = body["workspace"]["workspace_id"]
            st, body = pd.api(API_BASE, "PUT", f"/workspaces/{workspace}/folders", token, {"folder_ids": [ingest["folder_id"]]})
            if st != 200:   # noqa: literals - HTTP OK
                raise RuntimeError(f"could not link the folder: {st} {body}")
        for set_name in sets:
            items = items_for(set_name, workspace)
            for name in configs:
                key = f"{set_name}|{name}"
                if key in record["runs"] and not record["runs"][key].get("errors"):
                    continue
                t0 = time.time()
                out = run_config(set_name, CONFIGS[name], items)
                out["errors"] = sum(1 for r in out["results"].values() if r.get("error"))
                if CONFIGS[name][1] != "off":   # re-ranker failures over the run's window (paperb_docs change 8)
                    out["rerank"] = pd.rerank_log(t0, time.time())
                out["started"] = round(t0, 3)
                record["runs"][key] = out
                OUT.write_text(json.dumps(record, indent=1) + "\n")
                print(f"{key}: {len(out['results'])} items, {out['errors']} errors, window {out['window']}"
                      + (f", rerank {out['rerank']['searches']} searches / {out['rerank']['failed']} failed" if "rerank" in out else ""),
                      flush=True)
    finally:
        if workspace:
            st, _ = pd.api(API_BASE, "DELETE", f"/workspaces/{workspace}", token)
            print(f"workspace {workspace} deleted: HTTP {st}")
    return 0


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p on the discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def score(set_name: str, run: dict) -> dict[str, dict]:
    """Per question: hit in the writer's window, hit anywhere, right document first, right document in window."""
    window = run["window"]
    out: dict[str, dict] = {}
    if set_name == "financebench":
        qs = {q["financebench_id"]: q for q in pd.questions()}
        ingest = json.loads(pd.INGEST.read_text())
        name_of = {d["document_id"]: d["doc_name"] for d in ingest["documents"] if d.get("document_id")}
        for qid, r in run["results"].items():
            if r.get("error"):
                continue
            q = qs[qid]
            gold = pd.gold_pages(q)
            got = [(name_of.get(p["document_id"]), p.get("page")) for p in r["passages"]]
            out[qid] = {"window": any(g in gold for g in got[:window]), "anywhere": any(g in gold for g in got),
                        "doc_first": bool(got) and got[0][0] == q["doc_name"],
                        "doc_window": any(d == q["doc_name"] for d, _ in got[:window]), "group": q["question_type"]}
    else:
        qs = {q["id"]: q for q in json.loads((REPO / "fixtures" / "golden" / "retrieval_v2.json").read_text())["questions"]}
        for qid, r in run["results"].items():
            if r.get("error"):
                continue
            q = qs[qid]
            gold_ids = {q["gold_document"]} if isinstance(q["gold_document"], str) else set(q["gold_document"])
            hits = [er.passage_hits(p, gold_ids, q["evidence"]) for p in r["passages"]]
            out[qid] = {"window": any(hits[:window]), "anywhere": any(hits),
                        "doc_first": bool(r["passages"]) and r["passages"][0]["document_id"] in gold_ids,
                        "doc_window": any(p["document_id"] in gold_ids for p in r["passages"][:window]), "group": q["category"]}
    return out


def cmd_report() -> int:
    record = json.loads(OUT.read_text())
    scored = {key: score(key.split("|")[0], run) for key, run in record["runs"].items()}
    summary: dict[str, dict] = {}
    for key, rows in scored.items():
        n = len(rows)
        s = {m: round(sum(r[m] for r in rows.values()) / n, 3) for m in ("window", "anywhere", "doc_first", "doc_window")} if n else {}
        s["n"] = n
        groups = defaultdict(list)
        for r in rows.values():
            groups[r["group"]].append(r["window"])
        s["window_by_group"] = {g: f"{sum(v)}/{len(v)}" for g, v in sorted(groups.items())}
        summary[key] = s
        print(f"{key:28s} n={n:3d} window={s.get('window')} anywhere={s.get('anywhere')} doc_first={s.get('doc_first')} by group {s['window_by_group']}")
    # paired comparisons of the writer's window against the shipped configuration of the same set
    for set_name in sorted({k.split("|")[0] for k in scored}):
        base = scored.get(f"{set_name}|doc_off") or {}
        for name in CONFIGS:
            other = scored.get(f"{set_name}|{name}")
            if not other or name == "doc_off":
                continue
            ids = sorted(set(base) & set(other))
            b = sum(1 for i in ids if other[i]["window"] and not base[i]["window"])
            c = sum(1 for i in ids if base[i]["window"] and not other[i]["window"])
            summary[f"{set_name}|{name}"]["vs_doc_off"] = {"gained": b, "lost": c, "p_mcnemar": round(mcnemar_exact(b, c), 4)}
            print(f"  {set_name}: {name} vs doc_off: +{b} / -{c}, exact McNemar p = {mcnemar_exact(b, c):.4f}")
    record["summary"] = summary
    OUT.write_text(json.dumps(record, indent=1) + "\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--sets", default="inhouse,financebench")
    r.add_argument("--configs", default=",".join(CONFIGS))
    sub.add_parser("report")
    args = ap.parse_args()
    if args.cmd == "report":
        return cmd_report()
    return cmd_run(args.sets.split(","), args.configs.split(","))


if __name__ == "__main__":
    raise SystemExit(main())

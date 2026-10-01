#!/usr/bin/env python3
"""
scripts/eval_retrieval.py
Retrieval quality on the labelled document questions, with no LLM call
(docs/V3_PLAN.md, technology review 2026-10-01).

Labelled sets already in the repo:
  fixtures/golden/phase_e1.json          questions with expected_chunk_ids (docs/hybrid)
  fixtures/golden/review_probe_set.json  olist review-digest probes with their document_id

Conditions compared (same corpus, same tenant, exact search):
  dense           today's query embedding (no instruction prefix)
  dense+prefix    the instruction mxbai-embed-large expects on QUERIES:
                  "Represent this sentence for searching relevant passages: "
  keyword         Postgres full-text search (websearch_to_tsquery, ts_rank_cd)
  hybrid          reciprocal-rank fusion (k=60) of dense+prefix and keyword

Metrics, document level (what the two-round expansion keys on): hit@1, hit@3,
hit@8 (8 = retrieval.semantic_top_k) and MRR@8; chunk level for e1:
recall@8 of the expected chunks.

The measuring half runs inside the mcp container (it alone holds the reader
password and reaches Ollama); the host half pipes this file and the fixtures in.

Usage, from the repo root: python3 scripts/eval_retrieval.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PREFIX = "Represent this sentence for searching relevant passages: "
TOP_K = 8
RRF_K = 60
TENANT = "t_capstone_default"
CONTAINER = "statesync-mcp-1"


def host_main() -> int:
    repo = Path(__file__).resolve().parent.parent
    e1 = json.loads((repo / "fixtures/golden/phase_e1.json").read_text())["queries"]
    probes = json.loads((repo / "fixtures/golden/review_probe_set.json").read_text())["probes"]
    items = []
    for q in e1:
        chunks = q.get("expected_chunk_ids") or []
        if chunks:
            docs = sorted({c.rsplit("_c", 1)[0] for c in chunks})
            items.append({"id": q["id"], "set": "e1", "text": q["text"], "docs": docs, "chunks": chunks})
    for p in probes:
        items.append({"id": p["probe_id"], "set": "reviews", "text": p["question"], "docs": [p["document_id"]],
                      "chunks": []})
    payload = json.dumps({"items": items})
    script = Path(__file__).read_text()
    out = subprocess.run(["docker", "exec", "-i", "-e", "EVAL_RETRIEVAL_IN_CONTAINER=1", CONTAINER, "python", "-c",
                          script + "\nimport sys; container_main(sys.stdin.read())"],
                         input=payload, capture_output=True, text=True)
    if out.returncode:
        print(out.stderr[-2000:])
        return 1
    print(out.stdout)
    return 0


def container_main(raw: str) -> None:
    import httpx
    import psycopg

    items = json.loads(raw)["items"]
    password = open("/run/secrets/pg_reader_password").read().strip()
    conn = psycopg.connect(f"postgresql://statesync_reader:{password}@postgres:5432/statesync", autocommit=True)
    client = httpx.Client(timeout=60)

    def embed(text: str) -> str:
        r = client.post("http://ollama:11434/api/embeddings", json={"model": "mxbai-embed-large", "prompt": text})
        return "[" + ",".join(repr(float(x)) for x in r.json()["embedding"]) + "]"

    def dense(text: str) -> list[tuple[str, str]]:
        with conn.cursor() as cur:
            cur.execute("SET enable_indexscan = off")
            cur.execute("SELECT chunk_id, document_id FROM document_chunks WHERE tenant_id = %s "
                        "ORDER BY embedding <=> %s::vector LIMIT 50", (TENANT, embed(text)))
            return cur.fetchall()

    def keyword(text: str) -> list[tuple[str, str]]:
        with conn.cursor() as cur:
            cur.execute("SELECT chunk_id, document_id FROM document_chunks, "
                        "websearch_to_tsquery('english', %s) q "
                        "WHERE tenant_id = %s AND to_tsvector('english', content_md) @@ q "
                        "ORDER BY ts_rank_cd(to_tsvector('english', content_md), q) DESC LIMIT 50",
                        (text, TENANT))
            hits = cur.fetchall()
            if hits:
                return hits
            # websearch_to_tsquery ANDs every word; fall back to OR for long questions.
            cur.execute("SELECT chunk_id, document_id FROM document_chunks, "
                        "to_tsquery('english', array_to_string(tsvector_to_array(to_tsvector('english', %s)), ' | ')) q "
                        "WHERE tenant_id = %s AND to_tsvector('english', content_md) @@ q "
                        "ORDER BY ts_rank_cd(to_tsvector('english', content_md), q) DESC LIMIT 50",
                        (text, TENANT))
            return cur.fetchall()

    def fuse(*rankings: list[tuple[str, str]]) -> list[tuple[str, str]]:
        scores: dict[tuple[str, str], float] = {}
        for ranking in rankings:
            for rank, row in enumerate(ranking):
                scores[row] = scores.get(row, 0.0) + 1.0 / (RRF_K + rank + 1)
        return sorted(scores, key=scores.get, reverse=True)

    def doc_order(ranking: list[tuple[str, str]]) -> list[str]:
        seen: list[str] = []
        for _, doc in ranking:
            if doc not in seen:
                seen.append(doc)
        return seen

    conditions = {"dense": [], "dense+prefix": [], "keyword": [], "hybrid": []}
    for item in items:
        d0 = dense(item["text"])
        d1 = dense(PREFIX + item["text"])
        kw = keyword(item["text"])
        rankings = {"dense": d0, "dense+prefix": d1, "keyword": kw, "hybrid": fuse(d1, kw)}
        for name, ranking in rankings.items():
            docs = doc_order(ranking[:TOP_K])
            first = next((i for i, doc in enumerate(docs) if doc in item["docs"]), None)
            chunks_top = {c for c, _ in ranking[:TOP_K]}
            conditions[name].append({
                "set": item["set"], "hit1": first == 0, "hit3": first is not None and first < 3,
                "hit8": first is not None, "rr": 1.0 / (first + 1) if first is not None else 0.0,
                "chunk_recall": (len(chunks_top & set(item["chunks"])) / len(item["chunks"])) if item["chunks"] else None,
            })

    def summary(rows: list[dict]) -> str:
        n = len(rows)
        cr = [r["chunk_recall"] for r in rows if r["chunk_recall"] is not None]
        return (f"n={n:3d}  hit@1 {sum(r['hit1'] for r in rows) / n:5.1%}  hit@3 {sum(r['hit3'] for r in rows) / n:5.1%}"
                f"  hit@8 {sum(r['hit8'] for r in rows) / n:5.1%}  MRR {sum(r['rr'] for r in rows) / n:.3f}"
                + (f"  chunk recall@8 {sum(cr) / len(cr):5.1%}" if cr else ""))

    for subset in ("all", "e1", "reviews"):
        print(f"\n== {subset} ==")
        for name, rows in conditions.items():
            chosen = rows if subset == "all" else [r for r in rows if r["set"] == subset]
            print(f"{name:13s} {summary(chosen)}")


if __name__ == "__main__" and not __import__("os").environ.get("EVAL_RETRIEVAL_IN_CONTAINER"):
    sys.exit(host_main())

#!/usr/bin/env python3
"""
scripts/check_review_probe_rank.py
Cycle 006 Workstream B2 (before-arm) / B5 (after-arm). For each probe in
fixtures/golden/review_probe_set.json, embeds its question with the exact
model and dimension `mcp/tools/vector_tools.py::semantic_search` uses, then
runs semantic_search's own WHERE/ORDER BY -- unmodified except for replacing
its LIMIT with a full ranking -- to find where the probe's OWN document's
best chunk lands among every chunk in the tenant's retrieval space.

WHY THIS BYPASSES THE ORCHESTRATOR. This measures a structural property of
the retrieval SQL and the stored embeddings, not an end-to-end answer -- the
same scope audit 006 F-006-2 used, reproduced here as a committed, re-runnable
script instead of one-off `psql` calls. Going through the orchestrator would
add the supervisor/decomposition/synthesis layers' own behaviour on top,
which is not what B2/B5 are measuring (B is scoped to the ingestion seam,
not new routing/retrieval features -- see docs/cycles/006.md §2 "Explicitly
NOT this cycle").

WHICH OLLAMA. Defaults to the CONTAINERISED `ollama` service
(`http://[::1]:11434` from the host, matching audit 006 F-006-6's finding
that `mcp`'s OLLAMA_URL points at the container while a native macOS Ollama
app ALSO listens on `127.0.0.1:11434` -- `localhost` from a host script is
genuinely ambiguous between the two). This script embeds against whichever
server `mcp.vector_tools.semantic_search` actually uses at query time, not
whichever `localhost` happens to resolve to. Override with --ollama-url if
your topology differs; the two servers hold a byte-identical model (verified,
audit 006 F-006-6) so the choice does not change the result in this project,
but the ambiguity is real and worth being explicit about.

READ-ONLY. All SQL runs as `statesync_reader` via `psql` through `docker
compose exec postgres`, matching scripts/check_dv6_spatial.py's established
pattern -- no write, no orchestrator call, no state changed.

Usage:
    python3 scripts/check_review_probe_rank.py --out docs/review_probe_rank_before.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from ingest import _cfg  # noqa: E402 -- reuse the real config, not a copy
from shipped_flags import (  # noqa: E402
    require_shipped_flags, add_flag_guard_argument, FlagStateError,
)

PROBE_SET_PATH = REPO_ROOT / "fixtures" / "golden" / "review_probe_set.json"
DEFAULT_OLLAMA_URL = "http://[::1]:11434"


def _reader_password() -> str:
    return (REPO_ROOT / "secrets" / "pg_reader_password").read_text().strip()


def _psql(sql: str) -> str:
    pw = _reader_password()
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "env", f"PGPASSWORD={pw}",
         "psql", "-U", "statesync_reader", "-d", "statesync", "-tA", "-F", "|", "-c", sql],
        capture_output=True, text=True, cwd=REPO_ROOT, check=True,
    )
    return result.stdout


def _embed(text: str, ollama_url: str) -> list[float]:
    model = _cfg["retrieval"]["embedding_model"]
    expected_dim = _cfg["retrieval"]["embedding_dim"]
    timeout_s = _cfg["retrieval"]["embedding_timeout_s"]
    payload = json.dumps({"model": model, "prompt": text}).encode()
    req = urllib.request.Request(
        f"{ollama_url.rstrip('/')}/api/embeddings", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = json.loads(resp.read())
    vector = body.get("embedding")
    if not isinstance(vector, list) or len(vector) != expected_dim:
        raise RuntimeError(f"bad embedding response, dim={len(vector) if isinstance(vector, list) else 'N/A'}")
    return vector


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def rank_for_probe(probe: dict, ollama_url: str, tenant_id: str) -> dict:
    vec = _vector_literal(_embed(probe["question"], ollama_url))

    # Same WHERE/ORDER BY as mcp/tools/vector_tools.py::semantic_search,
    # LIMIT replaced with a full ranking via ROW_NUMBER().
    sql = f"""
        WITH ranked AS (
            SELECT chunk_id, document_id,
                   row_number() OVER (ORDER BY embedding <=> '{vec}'::vector) AS rnk,
                   round((1 - (embedding <=> '{vec}'::vector))::numeric, 4) AS score
            FROM document_chunks
            WHERE tenant_id = '{tenant_id}'
        )
        SELECT rnk, score FROM ranked WHERE document_id = '{probe["document_id"]}' ORDER BY rnk LIMIT 1;
    """
    out = _psql(sql).strip()
    if not out:
        return {"probe_id": probe["probe_id"], "seller_id": probe["seller_id"],
                "document_id": probe["document_id"], "best_rank": None, "best_score": None,
                "error": "document's chunks not found in tenant's retrieval space"}

    line = [ln for ln in out.splitlines() if "|" in ln][0]
    rnk_s, score_s = line.split("|")
    return {
        "probe_id": probe["probe_id"], "seller_id": probe["seller_id"],
        "document_id": probe["document_id"], "best_rank": int(rnk_s), "best_score": float(score_s),
    }


def total_chunks(tenant_id: str) -> int:
    out = _psql(f"SELECT count(*) FROM document_chunks WHERE tenant_id = '{tenant_id}';").strip()
    return int([ln for ln in out.splitlines() if ln.strip()][0])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="Output report path")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL,
                         help=f"Embedding server (default: {DEFAULT_OLLAMA_URL}, the containerised mcp Ollama)")
    add_flag_guard_argument(parser)
    args = parser.parse_args()

    try:
        flag_provenance = require_shipped_flags(
            "check_review_probe_rank.py (cycle 006 B2/B5)",
            allow_nonshipped=args.allow_nonshipped_flags,
        )
    except FlagStateError as exc:
        print(exc, file=sys.stderr)
        sys.exit(2)

    probe_set = json.loads(PROBE_SET_PATH.read_text())
    tenant_id = _cfg["tenancy"]["default_tenant"]  # matches INGEST_TENANT_ID in gen_olist_review_digests.py
    top_k = _cfg["retrieval"]["semantic_top_k"]
    n_chunks = total_chunks(tenant_id)

    results: list[dict[str, Any]] = []
    for probe in probe_set["probes"]:
        r = rank_for_probe(probe, args.ollama_url, tenant_id)
        r["within_top_k"] = (r["best_rank"] is not None and r["best_rank"] <= top_k)
        results.append(r)
        rank_str = str(r["best_rank"]) if r["best_rank"] is not None else "NOT FOUND"
        print(f"  {r['probe_id']}  rank={rank_str:>6}  "
              f"within_top_{top_k}={r['within_top_k']}")

    n_within = sum(1 for r in results if r["within_top_k"])
    ranks = [r["best_rank"] for r in results if r["best_rank"] is not None]
    report = {
        "generated_by": "scripts/check_review_probe_rank.py",
        "flag_provenance": flag_provenance,
        "probe_set_source": str(PROBE_SET_PATH.relative_to(REPO_ROOT)),
        "probe_set_seed": probe_set["seed"],
        "ollama_url_used": args.ollama_url,
        "tenant_id": tenant_id,
        "semantic_top_k": top_k,
        "n_chunks_in_retrieval_space": n_chunks,
        "n_probes": len(results),
        "n_within_top_k": n_within,
        "median_rank": sorted(ranks)[len(ranks) // 2] if ranks else None,
        "worst_rank": max(ranks) if ranks else None,
        "best_rank": min(ranks) if ranks else None,
        "results": results,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    print(f"\n{n_within}/{len(results)} probes within top-{top_k}, "
          f"of {n_chunks} chunks in the retrieval space.")
    print(f"median rank: {report['median_rank']}   worst: {report['worst_rank']}   best: {report['best_rank']}")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
scripts/bench_grounding.py
Part E1 Deliverable 8 -- runs fixtures/golden/phase_e1.json (25 queries)
through the orchestrator, then scores:

  E1-V3 (M6): context precision (expected_chunk_ids vs. actual citations)
    and faithfulness (LLM judge, rubric frozen in
    fixtures/rubrics/faithfulness.json) over the 8 doc + 8 hybrid queries.
    Ungrounded-but-true claims are counted separately -- the most
    informative failure mode per the rubric.
  E1-V5 (D7): decomposition fire counts per route (must be 8/8 hybrid,
    0/17 non-hybrid) and mean llm_calls per route.

The judge call reuses the orchestrator's own get_agent_llm("judge") adapter
by running a one-off `docker compose exec orchestrator python3 -c ...`
invocation (system/user text passed via stdin, result via stdout JSON),
rather than duplicating HTTP/provider-switching logic in this host-side
script. This is not merely convenience: this host has a native Ollama.app
listening on localhost:11434 in addition to the Dockerized `ollama`
service, and the two have different models pulled (confirmed empirically --
the native app has qwen2.5:7b, the container only has mxbai-embed-large).
Docker Desktop's host.docker.internal resolves to the native app from
inside a container, which is what the orchestrator's own flags.toml
ollama_url already points at and is verified working -- but that hostname
doesn't resolve from the host at all, so a host-side HTTP call through
flags.llm.ollama_url would silently hit the wrong backend (or nothing).
Running inside the orchestrator container sidesteps the whole ambiguity by
reusing the exact code path already proven correct for every other role.

Usage:
    python3 scripts/bench_grounding.py
"""

from __future__ import annotations

import json
import sys
import subprocess
from pathlib import Path

from shipped_flags import (require_shipped_flags, add_flag_guard_argument,
                           FlagStateError)
from typing import Any

from bench_memory import REPO_ROOT, restart_orchestrator, run_query

FIXTURE_PATH = REPO_ROOT / "fixtures" / "golden" / "phase_e1.json"
RUBRIC_PATH = REPO_ROOT / "fixtures" / "rubrics" / "faithfulness.json"
REPORT_PATH = REPO_ROOT / "docs" / "bench_grounding_report.json"

MCP_URL = "http://localhost:8080"
TENANT = "t_capstone_default"

_JUDGE_INLINE_SCRIPT = """
import asyncio, json, sys
from llm.provider import get_agent_llm
from config_loader import cfg

async def main():
    req = json.loads(sys.stdin.read())
    llm = get_agent_llm("judge")
    result = await llm.complete(
        system=req["system"],
        messages=[{"role": "user", "content": req["user"]}],
        max_tokens=cfg.sql.max_tokens_judge,
    )
    print(json.dumps({"text": result.text}))

asyncio.run(main())
"""


def _judge_complete(system: str, user: str) -> str:
    proc = subprocess.run(
        ["docker", "compose", "exec", "-T", "orchestrator", "python3", "-c", _JUDGE_INLINE_SCRIPT],
        input=json.dumps({"system": system, "user": user}),
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Judge call failed (exit {proc.returncode}): {proc.stderr[-2000:]}")
    # stdout may carry compose's own warning lines ahead of the JSON payload.
    last_line = proc.stdout.strip().split("\n")[-1]
    return json.loads(last_line)["text"]


def mcp_get_passage(chunk_id: str) -> dict | None:
    import subprocess
    payload = json.dumps({
        "jsonrpc": "2.0", "id": "1", "method": "docs.get_passage", "params": {"chunk_id": chunk_id},
    })
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", f"{MCP_URL}/rpc",
         "-H", "Content-Type: application/json", "-H", f"X-Tenant-ID: {TENANT}",
         "-H", "X-Session-ID: bench-grounding", "-d", payload],
        capture_output=True, text=True, check=True,
    )
    body = json.loads(result.stdout)
    return body.get("result")


def _context_precision(expected_chunk_ids: list[str], citations: list[dict]) -> dict:
    cited_chunk_ids = [c["ref"] for c in citations if c["kind"] == "chunk"]
    if not expected_chunk_ids:
        return {"precision": None, "recall": None, "cited": cited_chunk_ids}
    hit = set(expected_chunk_ids) & set(cited_chunk_ids)
    precision = len(hit) / len(cited_chunk_ids) if cited_chunk_ids else 0.0
    recall = len(hit) / len(expected_chunk_ids)
    return {"precision": precision, "recall": recall, "cited": cited_chunk_ids, "hit": sorted(hit)}


_JUDGE_SYSTEM_PROMPT = """\
You are scoring the faithfulness of an AI system's answer against what it \
actually retrieved, per the frozen rubric below. Respond with ONLY a JSON \
object matching the rubric's output_schema -- no markdown, no explanation.

RUBRIC:
{rubric}"""


def _judge_query(question: str, answer_text: str, retrieved: list[dict], reference: str, rubric: dict) -> dict:
    retrieved_text = "\n\n".join(
        f"[chunk_id={r['provenance']['chunk_id']}]\n{r['content']}" for r in retrieved
    ) or "(nothing was retrieved)"
    user = (
        f"Question: {question}\n\n"
        f"System's answer_text: {answer_text}\n\n"
        f"Retrieved passages (what the system actually saw):\n{retrieved_text}\n\n"
        f"Independent reference (for checking factual correctness only):\n{reference}"
    )
    raw = _judge_complete(_JUDGE_SYSTEM_PROMPT.format(rubric=json.dumps(rubric)), user)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(l for l in cleaned.split("\n") if not l.startswith("```")).strip()
    try:
        parsed = json.loads(cleaned)
        return {
            "claims": parsed.get("claims", []),
            "faithfulness_score": float(parsed.get("faithfulness_score", 0.0)),
            "ungrounded_but_true_count": int(parsed.get("ungrounded_but_true_count", 0)),
        }
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        print(f"    JUDGE PARSE FAILURE: {exc} -- raw: {raw[:200]!r}")
        return {"claims": [], "faithfulness_score": 0.0, "ungrounded_but_true_count": 0, "parse_failed": True}


def _build_reference(q: dict) -> str:
    parts = []
    for chunk_id in q["expected_chunk_ids"]:
        passage = mcp_get_passage(chunk_id)
        if passage:
            parts.append(f"[{chunk_id}]\n{passage['content_md']}")
    if q.get("ground_truth_sql"):
        parts.append(f"Database ground truth (SQL: {q['ground_truth_sql']}): row_count={q.get('expected_row_count')}")
    return "\n\n".join(parts) if parts else "(no reference available)"


def main() -> None:
    # Append-only guard, applied uniformly across the bench harnesses.
    # Two of them silently overwrote committed verification results during
    # this build -- experiment_tool_ceiling.py destroyed the Phase F baseline,
    # and check_c2v8.py overwrote its F-V3 report. Both were recoverable only
    # because the numbers had already been transcribed elsewhere. --out lets a
    # re-run be recorded ALONGSIDE an earlier one; the default path still
    # overwrites so verify_fv3.sh keeps working unchanged.
    import argparse
    _ap = argparse.ArgumentParser(description=__doc__)
    _ap.add_argument("--out", default=str(REPORT_PATH),
                     help="Report path. A non-default path refuses to overwrite.")

    # Shipped-flag guard (scripts/shipped_flags.py). Two measurements were
    # contaminated by leftover flag state before this existed: once by a script
    # that toggled flags under a live run, once by a killed run that left the
    # orchestrator serving a value the file no longer had. Both were discarded.
    # This asserts the flags file AND that the running orchestrator post-dates
    # it, and refuses rather than producing a number that is not comparable to
    # anything on record. Scripts that vary a flag ON PURPOSE do so AFTER this
    # point; the deviation they introduce is theirs and is recorded.
    add_flag_guard_argument(_ap)
    _args, _ = _ap.parse_known_args()
    try:
        flag_provenance = require_shipped_flags(
            'bench_grounding.py (M6 grounding)', allow_nonshipped=_args.allow_nonshipped_flags)
    except FlagStateError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2)
    out_path = Path(_args.out)
    if out_path != REPORT_PATH and out_path.exists():
        print(f"ERROR: {out_path} exists. Verification results are append-only "
              f"-- choose a new --out path.")
        raise SystemExit(1)

    print("=== bench_grounding.py -- Phase E1 M6 benchmark (E1-V3/E1-V5 data source) ===")
    fixture = json.loads(FIXTURE_PATH.read_text())
    rubric = json.loads(RUBRIC_PATH.read_text())
    queries = fixture["queries"]

    print("\n[1/3] Ensuring orchestrator is up with production defaults...")
    restart_orchestrator()

    print(f"\n[2/3] Running all {len(queries)} queries...")
    results: dict[str, dict] = {}
    for q in queries:
        try:
            data = run_query(q["text"])
            trace = data.get("state_trace", {})
            results[q["id"]] = {
                "ok": True,
                "expected_route": q["expected_route"],
                "actual_route": trace.get("route"),
                "subtasks": trace.get("subtasks", []),
                "metrics": trace.get("metrics", {}),
                "answer_text": data.get("answer_text", ""),
                "citations": data.get("citations", []),
                "retrievals": trace.get("retrievals", []),
            }
            print(f"  [{q['id']:>14}] route={trace.get('route'):<7} "
                  f"llm_calls={trace.get('metrics', {}).get('llm_calls')} "
                  f"citations={len(data.get('citations', []))}")
        except Exception as exc:
            print(f"  [{q['id']:>14}] ERROR: {exc}")
            results[q["id"]] = {"ok": False, "error": str(exc), "expected_route": q["expected_route"]}

    # ── E1-V5: decomposition fire counts + mean llm_calls per route ────────
    print("\n[3/3] Scoring...")
    print("\n--- E1-V5: decomposition gate ---")
    hybrid_ids = [q["id"] for q in queries if q["expected_route"] == "hybrid"]
    non_hybrid_ids = [q["id"] for q in queries if q["expected_route"] != "hybrid"]

    hybrid_fired = sum(
        1 for qid in hybrid_ids
        if results[qid].get("ok") and len(results[qid]["subtasks"]) == 2
        and results[qid]["subtasks"][0] != results[qid]["subtasks"][1]
    )
    non_hybrid_fired = sum(
        1 for qid in non_hybrid_ids
        if results[qid].get("ok") and len(results[qid].get("subtasks", [])) == 2
        and results[qid]["subtasks"][0] != results[qid]["subtasks"][1]
    )
    print(f"  Decomposition fired: {hybrid_fired}/{len(hybrid_ids)} hybrid, "
          f"{non_hybrid_fired}/{len(non_hybrid_ids)} non-hybrid (must be 0)")

    llm_calls_by_route: dict[str, list[int]] = {"sql": [], "docs": [], "hybrid": []}
    for q in queries:
        r = results[q["id"]]
        if r.get("ok") and r["actual_route"] in llm_calls_by_route:
            llm_calls_by_route[r["actual_route"]].append(r["metrics"].get("llm_calls", 0))
    mean_llm_calls = {
        route: (sum(calls) / len(calls) if calls else None)
        for route, calls in llm_calls_by_route.items()
    }
    print(f"  Mean llm_calls per route: {mean_llm_calls}")

    # ── E1-V3: context precision + faithfulness (8 doc + 8 hybrid) ─────────
    print("\n--- E1-V3: context precision + faithfulness ---")
    grounding_scores: dict[str, dict] = {}
    for q in queries:
        if q["expected_route"] not in ("docs", "hybrid"):
            continue
        r = results[q["id"]]
        if not r.get("ok"):
            continue
        cp = _context_precision(q["expected_chunk_ids"], r["citations"])
        reference = _build_reference(q)
        judge_result = _judge_query(q["text"], r["answer_text"], r["retrievals"], reference, rubric)
        grounding_scores[q["id"]] = {**cp, **judge_result}
        print(f"  [{q['id']:>14}] precision={cp['precision']} recall={cp['recall']} "
              f"faithfulness={judge_result['faithfulness_score']:.2f} "
              f"ungrounded_but_true={judge_result['ungrounded_but_true_count']}")

    scored = [s for s in grounding_scores.values() if s.get("precision") is not None]
    mean_precision = sum(s["precision"] for s in scored) / len(scored) if scored else None
    mean_recall = sum(s["recall"] for s in scored) / len(scored) if scored else None
    faithful_scored = list(grounding_scores.values())
    mean_faithfulness = (
        sum(s["faithfulness_score"] for s in faithful_scored) / len(faithful_scored)
        if faithful_scored else None
    )
    total_ungrounded_but_true = sum(s["ungrounded_but_true_count"] for s in faithful_scored)

    print(f"\n=== M6 SUMMARY ===")
    print(f"  Context precision (mean, over {len(scored)} scored): {mean_precision}")
    print(f"  Context recall (mean): {mean_recall}")
    print(f"  Faithfulness (mean, over {len(faithful_scored)} scored): {mean_faithfulness}")
    print(f"  Total ungrounded-but-true claims: {total_ungrounded_but_true}")

    report = {
        "flag_provenance": flag_provenance,
        "results": results,
        "e1v5_decomposition": {
            "hybrid_fired": hybrid_fired, "hybrid_total": len(hybrid_ids),
            "non_hybrid_fired": non_hybrid_fired, "non_hybrid_total": len(non_hybrid_ids),
            "mean_llm_calls_by_route": mean_llm_calls,
        },
        "e1v3_grounding": {
            "per_query": grounding_scores,
            "mean_context_precision": mean_precision,
            "mean_context_recall": mean_recall,
            "mean_faithfulness": mean_faithfulness,
            "total_ungrounded_but_true": total_ungrounded_but_true,
        },
    }
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nReport written to {REPORT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()

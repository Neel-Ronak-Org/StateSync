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

Audit 003 (F-003-1..F-003-4, F-003-7) added, without altering the original
scoring: faithfulness.json's judge call is unmodified and its score is
untouched. A SECOND judge call runs against fixtures/rubrics/faithfulness_v2.json,
which additionally shows the judge the SQL result rows a hybrid answer's
synthesizer prompt actually included -- faithfulness.json's own judge call
never did, despite labelling document passages "what the system actually
saw". Both scores are reported, under mean_faithfulness (unchanged) and
mean_faithfulness_all_sources (new). ungrounded_but_true is now recomputed
from the judge's own per-claim array rather than trusting its separate
self-reported integer (kept alongside as judge_self_reported_ubt for
comparison). context precision keeps its original over-cited definition and
gains two siblings: mean_context_precision_over_retrieved (same computation,
uncapped by citation_max_chunks) and max_attainable_context_precision (the
per-run ceiling the cap imposes). _build_reference() now executes
ground_truth_sql through the same SELECT-only MCP path the orchestrator uses
(data.run_select) instead of embedding expected_row_count as
"row_count=N" -- a row COUNT is not an answer value, and the judge was
reading it as one. See docs/audits/003.md.

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
# Audit 003 A4: added alongside RUBRIC_PATH, which stays byte-identical.
RUBRIC_V2_PATH = REPO_ROOT / "fixtures" / "rubrics" / "faithfulness_v2.json"
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


# Bound on rows embedded in the judge's reference -- every ground_truth_sql in
# this fixture is a scalar or a small aggregate, so this is generous headroom,
# not a working limit. Kept explicit and small anyway so a reference query
# that returned many rows can't blow up the judge's prompt silently.
_REFERENCE_MAX_ROWS = 20


def mcp_run_select(sql: str) -> dict | None:
    """
    Executes ground_truth_sql through the same SELECT-only MCP path the
    orchestrator itself uses (data.run_select, mcp/tools/data_tools.py) --
    read-only by construction (three-layer enforcement documented there).

    Audit 003 F-003-2: _build_reference() used to hand the judge the literal
    string f"row_count={expected_row_count}" and call it "database ground
    truth" -- the row COUNT, not the answer. A query returning
    {"count": 2} produced the reference "row_count=1" (one row was returned),
    which is a true statement about shape and a false one about content, and
    the judge read it as the latter. This executes the query for real.
    """
    import subprocess
    payload = json.dumps({
        "jsonrpc": "2.0", "id": "1", "method": "data.run_select",
        "params": {"sql": sql, "max_rows": _REFERENCE_MAX_ROWS},
    })
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", f"{MCP_URL}/rpc",
         "-H", "Content-Type: application/json", "-H", f"X-Tenant-ID: {TENANT}",
         "-H", "X-Session-ID: bench-grounding", "-d", payload],
        capture_output=True, text=True, check=True,
    )
    body = json.loads(result.stdout)
    return body.get("result")


def _context_precision(expected_chunk_ids: list[str], citations: list[dict],
                       retrievals: list[dict]) -> dict:
    """
    `precision`/`recall` (unchanged definition, audit 003 does not alter
    them): over CITED chunks, capped at citation_max_chunks (=3 shipped).
    With 2 expected_chunk_ids per fixture entry, that cap alone puts a
    0.667 ceiling under every doc-route query regardless of retrieval
    quality (F-003-4) -- `max_attainable_precision` makes that ceiling
    visible per query, and `precision_over_retrieved` reports the same
    computation over the full retrieved set (pre-citation-cap), which is
    what V2_STRATEGY.md's "~38% of retrieved context is irrelevant" gloss
    actually described despite being computed from the cited figure.
    """
    cited_chunk_ids = [c["ref"] for c in citations if c["kind"] == "chunk"]
    retrieved_chunk_ids = [r["provenance"]["chunk_id"] for r in retrievals]
    if not expected_chunk_ids:
        return {
            "precision": None, "recall": None, "cited": cited_chunk_ids,
            "precision_over_retrieved": None, "max_attainable_precision": None,
        }
    hit = set(expected_chunk_ids) & set(cited_chunk_ids)
    precision = len(hit) / len(cited_chunk_ids) if cited_chunk_ids else 0.0
    recall = len(hit) / len(expected_chunk_ids)
    hit_retrieved = set(expected_chunk_ids) & set(retrieved_chunk_ids)
    precision_over_retrieved = (
        len(hit_retrieved) / len(retrieved_chunk_ids) if retrieved_chunk_ids else 0.0)
    max_attainable_precision = (
        min(len(expected_chunk_ids), len(cited_chunk_ids)) / len(cited_chunk_ids)
        if cited_chunk_ids else 0.0)
    return {
        "precision": precision, "recall": recall, "cited": cited_chunk_ids, "hit": sorted(hit),
        "precision_over_retrieved": precision_over_retrieved,
        "max_attainable_precision": max_attainable_precision,
    }


_JUDGE_SYSTEM_PROMPT = """\
You are scoring the faithfulness of an AI system's answer against what it \
actually retrieved, per the frozen rubric below. Respond with ONLY a JSON \
object matching the rubric's output_schema -- no markdown, no explanation.

RUBRIC:
{rubric}"""


def _recount_ungrounded_but_true(claims: list[dict]) -> int:
    """
    Audit 003 F-003-3: the judge's own `ungrounded_but_true_count` field is a
    free-text integer it types once, separate from the per-claim `claims`
    array it also emits, and the two disagreed on 2 of 16 queries in the
    published run (the judge's field said 4 total; its own claims supported
    2). This recomputes the count FROM the claims the judge actually
    itemised, rather than trusting a second, unvalidated number it wrote.
    A claim is only counted if grounded_in_retrieved_context is exactly
    False and factually_correct is exactly True -- both booleans per the
    rubric's output_schema; a claim with a missing or non-boolean
    factually_correct (the rubric requires it, but a 7B judge does not
    always comply) does not count as a confirmed true claim.
    """
    return sum(
        1 for c in claims
        if c.get("grounded_in_retrieved_context") is False
        and c.get("factually_correct") is True
    )


def _recompute_faithfulness_from_claims(claims: list[dict]) -> float:
    """
    Audit 003 F-003-6: E1-V3's pass condition is "an ungrounded claim is a
    failure regardless of whether it is factually true -- the scoring
    script must enforce this rather than crediting lucky accuracy," and
    nothing asserted that it does. This is an independent cross-check, not
    a replacement for the judge-reported `faithfulness_score` -- it does
    NOT change what is assigned to that key (A4 keeps that expression
    byte-for-byte). It recomputes the rubric's own stated definition
    ("fraction of claims with grounded_in_retrieved_context = true") from
    the claims array, so an implementation that accidentally credited
    factually_correct instead (crediting lucky accuracy) would show up as a
    disagreement here rather than passing silently. Vacuously 1.0 on zero
    claims, matching the rubric's own stated convention.
    """
    if not claims:
        return 1.0
    grounded = sum(1 for c in claims if c.get("grounded_in_retrieved_context") is True)
    return grounded / len(claims)


def _judge_query(question: str, answer_text: str, retrieved: list[dict], reference: str,
                 rubric: dict, extra_context: str | None = None) -> dict:
    """
    `extra_context`, when given, is appended to the "what the system actually
    saw" block -- used by the faithfulness_all_sources pass (A4, audit 003
    F-003-1) to add the SQL result rows a hybrid answer's synthesizer prompt
    actually included. None (the default) reproduces the original,
    unmodified faithfulness.json call exactly -- same header text, same
    content, same score. See fixtures/rubrics/faithfulness_v2.json.
    """
    retrieved_text = "\n\n".join(
        f"[chunk_id={r['provenance']['chunk_id']}]\n{r['content']}" for r in retrieved
    ) or "(nothing was retrieved)"
    if extra_context:
        retrieved_text = f"{retrieved_text}\n\n{extra_context}"
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
        claims = parsed.get("claims", [])
        judge_self_reported = int(parsed.get("ungrounded_but_true_count", 0))
        recounted = _recount_ungrounded_but_true(claims)
        # NOT what faithfulness_score is set from -- a cross-check field only.
        recomputed_faithfulness = _recompute_faithfulness_from_claims(claims)
        judge_faithfulness = float(parsed.get("faithfulness_score", 0.0))
        return {
            "claims": claims,
            "faithfulness_score": judge_faithfulness,
            "faithfulness_score_recomputed": recomputed_faithfulness,
            "faithfulness_disagreement": abs(recomputed_faithfulness - judge_faithfulness) > 1e-6,
            "ungrounded_but_true_count": recounted,
            "judge_self_reported_ubt": judge_self_reported,
            "ubt_disagreement": recounted != judge_self_reported,
        }
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        print(f"    JUDGE PARSE FAILURE: {exc} -- raw: {raw[:200]!r}")
        return {"claims": [], "faithfulness_score": 0.0, "faithfulness_score_recomputed": 1.0,
                "faithfulness_disagreement": False, "ungrounded_but_true_count": 0,
                "judge_self_reported_ubt": 0, "ubt_disagreement": False, "parse_failed": True}


def _build_reference(q: dict) -> str:
    parts = []
    for chunk_id in q["expected_chunk_ids"]:
        passage = mcp_get_passage(chunk_id)
        if passage:
            parts.append(f"[{chunk_id}]\n{passage['content_md']}")
    if q.get("ground_truth_sql"):
        sql = q["ground_truth_sql"]
        exec_result = mcp_run_select(sql)
        if exec_result and exec_result.get("ok"):
            rows_text = json.dumps(exec_result["rows"], default=str)
            note = " (truncated)" if exec_result.get("truncated") else ""
            parts.append(f"Database ground truth (SQL: {sql}): rows={rows_text}{note}")
        else:
            # Do NOT fall back to expected_row_count here -- that field is a
            # row COUNT, not an answer value, and silently substituting it is
            # exactly the defect this replaces (F-003-2). Say plainly that
            # execution failed rather than hand the judge a number that looks
            # like ground truth but isn't.
            err = (exec_result or {}).get("error")
            parts.append(f"Database ground truth (SQL: {sql}): "
                         f"QUERY EXECUTION FAILED, no reference value available "
                         f"(error: {err})")
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
            'bench_grounding.py (M6 grounding)',
            allow_nonshipped=_args.allow_nonshipped_flags, fixture=FIXTURE_PATH)
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
    rubric_v2 = json.loads(RUBRIC_V2_PATH.read_text())
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
                # Audit 003 F-003-1: the synthesizer's own prompt includes
                # these for a hybrid answer (synthesizer.py's SQL context
                # block); the grounding judge never saw them until A4.
                "executed_sql": trace.get("executed_sql"),
                "sql_rows": trace.get("sql_rows", []),
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
    # Audit 003 A4: faithfulness.json's judge call is run FIRST, unmodified
    # (same rubric object, same _judge_query default extra_context=None) --
    # its score is not touched by anything below. faithfulness_v2.json's
    # judge call is a SEPARATE, additional call that also sees the SQL rows
    # the synthesizer's own prompt included for a hybrid answer.
    print("\n--- E1-V3: context precision + faithfulness ---")
    grounding_scores: dict[str, dict] = {}
    ubt_disagreements = 0
    faithfulness_disagreements = 0
    for q in queries:
        if q["expected_route"] not in ("docs", "hybrid"):
            continue
        r = results[q["id"]]
        if not r.get("ok"):
            continue
        cp = _context_precision(q["expected_chunk_ids"], r["citations"], r["retrievals"])
        reference = _build_reference(q)
        judge_result = _judge_query(q["text"], r["answer_text"], r["retrievals"], reference, rubric)

        sql_block = None
        if r.get("executed_sql"):
            sql_block = (
                "[SQL result -- computed by the system's own SQL sub-task, "
                "also shown to it when writing the answer]\n"
                f"SQL: {r['executed_sql']}\n"
                f"Rows: {json.dumps(r.get('sql_rows', []), default=str)}"
            )
        judge_result_v2 = _judge_query(q["text"], r["answer_text"], r["retrievals"], reference,
                                       rubric_v2, extra_context=sql_block)

        if judge_result.get("ubt_disagreement"):
            ubt_disagreements += 1
        if judge_result.get("faithfulness_disagreement"):
            faithfulness_disagreements += 1
        grounding_scores[q["id"]] = {
            **cp,
            **judge_result,
            "faithfulness_all_sources": judge_result_v2["faithfulness_score"],
            "claims_all_sources": judge_result_v2["claims"],
            "ungrounded_but_true_all_sources": judge_result_v2["ungrounded_but_true_count"],
        }
        print(f"  [{q['id']:>14}] precision={cp['precision']} recall={cp['recall']} "
              f"faithfulness={judge_result['faithfulness_score']:.2f} "
              f"(all_sources={judge_result_v2['faithfulness_score']:.2f}) "
              f"ungrounded_but_true={judge_result['ungrounded_but_true_count']}"
              f"{' [judge said ' + str(judge_result['judge_self_reported_ubt']) + ']' if judge_result.get('ubt_disagreement') else ''}")

    scored = [s for s in grounding_scores.values() if s.get("precision") is not None]
    mean_precision = sum(s["precision"] for s in scored) / len(scored) if scored else None
    mean_recall = sum(s["recall"] for s in scored) / len(scored) if scored else None
    mean_precision_over_retrieved = (
        sum(s["precision_over_retrieved"] for s in scored) / len(scored) if scored else None)
    mean_max_attainable_precision = (
        sum(s["max_attainable_precision"] for s in scored) / len(scored) if scored else None)
    faithful_scored = list(grounding_scores.values())
    mean_faithfulness = (
        sum(s["faithfulness_score"] for s in faithful_scored) / len(faithful_scored)
        if faithful_scored else None
    )
    mean_faithfulness_all_sources = (
        sum(s["faithfulness_all_sources"] for s in faithful_scored) / len(faithful_scored)
        if faithful_scored else None
    )
    total_ungrounded_but_true = sum(s["ungrounded_but_true_count"] for s in faithful_scored)
    total_judge_self_reported_ubt = sum(s["judge_self_reported_ubt"] for s in faithful_scored)
    total_ungrounded_but_true_all_sources = sum(
        s["ungrounded_but_true_all_sources"] for s in faithful_scored)

    print(f"\n=== M6 SUMMARY ===")
    print(f"  Context precision (mean, over {len(scored)} scored, over CITED chunks): {mean_precision}")
    print(f"  Context precision over RETRIEVED chunks (mean, over {len(scored)} scored): {mean_precision_over_retrieved}")
    print(f"  Max attainable context precision given this run's citations (mean, over {len(scored)} scored): {mean_max_attainable_precision}")
    print(f"  Context recall (mean): {mean_recall}")
    print(f"  Faithfulness -- documents only, unchanged definition (mean, over {len(faithful_scored)} scored): {mean_faithfulness}")
    print(f"  Faithfulness -- documents + SQL the system also saw (mean, over {len(faithful_scored)} scored): {mean_faithfulness_all_sources}")
    print(f"  Total ungrounded-but-true claims (recomputed from claims array): {total_ungrounded_but_true}")
    print(f"  Total ungrounded-but-true claims (judge's own self-reported field): {total_judge_self_reported_ubt}")
    print(f"  Queries where the judge's ubt self-report disagreed with its own claims: {ubt_disagreements}/{len(faithful_scored)}")
    print(f"  Queries where faithfulness_score disagreed with the claims-array recompute: {faithfulness_disagreements}/{len(faithful_scored)}")
    print(f"  Total ungrounded-but-true claims, all-sources pass: {total_ungrounded_but_true_all_sources}")

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
            "n_precision_recall_scored": len(scored),
            "n_faithfulness_scored": len(faithful_scored),
            "mean_context_precision": mean_precision,
            "mean_context_precision_over_retrieved": mean_precision_over_retrieved,
            "max_attainable_context_precision": mean_max_attainable_precision,
            "mean_context_recall": mean_recall,
            "mean_faithfulness": mean_faithfulness,
            "mean_faithfulness_all_sources": mean_faithfulness_all_sources,
            "total_ungrounded_but_true": total_ungrounded_but_true,
            "total_judge_self_reported_ubt": total_judge_self_reported_ubt,
            "ubt_disagreement_count": ubt_disagreements,
            "faithfulness_disagreement_count": faithfulness_disagreements,
            "total_ungrounded_but_true_all_sources": total_ungrounded_but_true_all_sources,
        },
    }
    out_path.write_text(json.dumps(report, indent=2, default=str))
    # Cycle 003 regression, found by M2 itself crashing on its own --out run:
    # out_path is whatever the caller typed on the command line and is
    # relative in the common case (as every M1/M2 invocation this cycle
    # used); REPORT_PATH is always REPO_ROOT-absolute, and Path.relative_to()
    # raises rather than resolving when the two are not both anchored the
    # same way. .resolve() first so a relative --out still prints cleanly.
    print(f"\nReport written to {out_path.resolve().relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()

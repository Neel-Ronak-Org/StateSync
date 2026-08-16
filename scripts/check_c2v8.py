#!/usr/bin/env python3
"""
scripts/check_c2v8.py
C2-V8 (M4) procedure -- not a Deliverable script, reuses bench_memory.py's
helpers the same way check_consistency.py (C1-V3) does.

1. Computes ground truth by executing each phase_c2.json query's
   ground_truth_sql directly against mcp (bypassing the LLM entirely),
   freezing expected_row_count into the fixture file.
2. Cache-cold pass (memory.enabled=false): runs all 25 through the
   orchestrator, compares generated-SQL results to ground truth by
   execution-based dataframe equality (C1-V3's definition).
3. Priming + cache-warm pass (memory.enabled=true): primes the trajectory
   store with one pass, then measures a second pass.
4. Golden recovery: re-runs all 10 phase_b.json queries with
   memory.enabled=true, reporting which of the queries verify_b.sh found
   failing at the end of C1 (b_007, b_008 -- memory-caused; b_010 --
   pre-existing, unrelated to memory) now pass via the correction loop.

Usage:
    python3 scripts/check_c2v8.py
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from bench_memory import (
    REPO_ROOT,
    restart_orchestrator,
    reset_trajectory_collection,
    run_query,
    set_memory_enabled,
)

C2_FIXTURE_PATH = REPO_ROOT / "fixtures" / "golden" / "phase_c2.json"
B_FIXTURE_PATH = REPO_ROOT / "fixtures" / "golden" / "phase_b.json"
REPORT_PATH = REPO_ROOT / "docs" / "c2v8_report.json"

MCP_URL = "http://localhost:8080"
TENANT = "t_capstone_default"

# verify_b.sh's own C1-era finding (docs/phase_c1_log.md): with
# memory.enabled=true, 3 of 10 golden queries failed -- b_007 and b_008
# from hit-path hallucination (memory-caused), b_010 from a pre-existing,
# memory-unrelated local-model weakness (present even with memory off).
PREVIOUSLY_FAILING_GOLDEN = {"b_007", "b_008", "b_010"}
MEMORY_CAUSED_GOLDEN = {"b_007", "b_008"}


def _signature(sql_columns: list[str], sql_rows: list[dict]) -> str:
    """
    Execution-based dataframe equality, value-only: ground_truth_sql and the
    LLM-generated SQL are independently authored, so column ALIASES
    routinely differ (total_order_value vs sum_value) even when the
    underlying data is identical -- comparing column names literally treats
    every alias choice as a mismatch, which is not what "row-multiset
    equality, column order ignored" is testing for. Each row is reduced to
    a sorted tuple of its own values (order-independent within the row,
    so column order truly doesn't matter); the frame is the sorted
    multiset of those row-tuples plus the column *count* (not names) and
    row count, which still catches a genuinely different projection
    (extra/missing columns) or genuinely different data.
    """
    row_tuples = [
        tuple(sorted(json.dumps(v, sort_keys=True, default=str) for v in row.values()))
        for row in sql_rows
    ]
    return json.dumps(
        {"n_columns": len(sql_columns), "rows": sorted(row_tuples)}, default=str,
    )


def mcp_run_select(sql: str) -> dict:
    payload = json.dumps({
        "jsonrpc": "2.0", "id": "1", "method": "data.run_select", "params": {"sql": sql},
    })
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", f"{MCP_URL}/rpc",
         "-H", "Content-Type: application/json",
         "-H", f"X-Tenant-ID: {TENANT}",
         "-H", "X-Session-ID: c2v8-ground-truth",
         "-d", payload],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)["result"]


def compute_ground_truth() -> dict[str, dict]:
    print("[1/4] Computing ground truth via direct mcp execution...")
    fixture = json.loads(C2_FIXTURE_PATH.read_text())
    ground_truth = {}
    changed = False
    for q in fixture["queries"]:
        r = mcp_run_select(q["ground_truth_sql"])
        if not r.get("ok"):
            print(f"  [{q['id']}] GROUND TRUTH SQL FAILED: {r.get('error')}")
            ground_truth[q["id"]] = {"ok": False, "error": r.get("error")}
            continue
        sig = _signature(r["columns"], r["rows"])
        ground_truth[q["id"]] = {"ok": True, "signature": sig, "row_count": r["row_count"]}
        if q["expected_row_count"] is None:
            q["expected_row_count"] = r["row_count"]
            changed = True
        print(f"  [{q['id']}] ground truth rows={r['row_count']}")
    if changed:
        C2_FIXTURE_PATH.write_text(json.dumps(fixture, indent=2))
        print("  Froze expected_row_count values into", C2_FIXTURE_PATH.relative_to(REPO_ROOT))
    return ground_truth


def _run_query_resilient(text: str) -> dict | None:
    """A single slow/timed-out query must not abort the whole sweep."""
    for _ in range(2):
        try:
            return run_query(text)
        except Exception as exc:
            print(f"    (query error, retrying once: {exc})")
    return None


def run_pass(label: str, queries: list[dict], ground_truth: dict[str, dict]) -> dict:
    print(f"\n--- {label} ---")
    results = {}
    correct = 0
    scored = 0
    for q in queries:
        gt = ground_truth.get(q["id"])
        data = _run_query_resilient(q["text"])
        if data is None:
            print(f"  [{q['id']}] SKIPPED (query failed twice)")
            results[q["id"]] = {"match": False, "memory_hit": None, "row_count": None,
                                 "expected_row_count": gt.get("row_count") if gt else None}
            continue
        trace = data.get("state_trace", {})
        sig = _signature(trace.get("sql_columns", []), trace.get("sql_rows", []))
        match = bool(gt and gt.get("ok") and sig == gt["signature"])
        results[q["id"]] = {
            "match": match,
            "memory_hit": trace.get("memory_hit", False),
            "row_count": trace.get("sql_rows_count", 0),
            "expected_row_count": gt.get("row_count") if gt else None,
        }
        if gt and gt.get("ok"):
            scored += 1
            correct += 1 if match else 0
        print(f"  [{q['id']}] match={match} hit={trace.get('memory_hit')} rows={trace.get('sql_rows_count')} expected={gt.get('row_count') if gt else 'N/A'}")
    accuracy = correct / scored if scored else 0.0
    print(f"  {label}: {correct}/{scored} correct ({accuracy:.1%})")
    return {"results": results, "correct": correct, "scored": scored, "accuracy": accuracy}


def golden_recovery() -> dict:
    print("\n--- Golden recovery (phase_b.json, memory.enabled=true) ---")
    fixture = json.loads(B_FIXTURE_PATH.read_text())
    results = {}
    for q in fixture["queries"]:
        data = _run_query_resilient(q["text"])
        if data is None:
            print(f"  [{q['id']}] SKIPPED (query failed twice)")
            results[q["id"]] = {"ok": False, "attempt": None, "errors": None}
            continue
        trace = data.get("state_trace", {})
        ok = trace.get("sql_rows_count", 0) > 0
        results[q["id"]] = {"ok": ok, "attempt": trace.get("attempt", 0), "errors": len(trace.get("errors", []))}
        tag = " (previously failing)" if q["id"] in PREVIOUSLY_FAILING_GOLDEN else ""
        print(f"  [{q['id']}]{tag} ok={ok} attempt={trace.get('attempt')} errors={len(trace.get('errors', []))}")

    recovered = [qid for qid in MEMORY_CAUSED_GOLDEN if results.get(qid, {}).get("ok")]
    still_failing = [qid for qid in PREVIOUSLY_FAILING_GOLDEN if not results.get(qid, {}).get("ok")]
    print(f"\n  Memory-caused failures recovered: {len(recovered)}/{len(MEMORY_CAUSED_GOLDEN)} {recovered}")
    print(f"  Still failing (any cause): {still_failing}")
    return {"results": results, "recovered": recovered, "still_failing": still_failing}


def main() -> None:
    # Same append-only rule as experiment_tool_ceiling.py: a re-run records a
    # NEW result rather than rewriting a committed one. This script silently
    # overwrote docs/c2v8_report.json during the schema-semantics A/B run --
    # the F-V3 values survived only because they had already been transcribed
    # into docs/metrics-report.md. --out is honoured when given; the default
    # path is still overwritten so verify_fv3.sh keeps working unchanged.
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPORT_PATH),
                    help="Report path. A non-default path refuses to overwrite.")
    args = ap.parse_args()
    out_path = Path(args.out)
    if out_path != REPORT_PATH and out_path.exists():
        print(f"ERROR: {out_path} exists. Results are append-only -- choose a new --out.")
        raise SystemExit(1)

    ground_truth = compute_ground_truth()
    c2_fixture = json.loads(C2_FIXTURE_PATH.read_text())["queries"]

    print("\n[2/4] Cache-cold pass: memory.enabled=false")
    set_memory_enabled(False)
    restart_orchestrator()
    cold = run_pass("Cache-cold (novel path)", c2_fixture, ground_truth)

    print("\n[3/4] Cache-warm pass: memory.enabled=true (priming, then measuring)")
    set_memory_enabled(True)
    restart_orchestrator()
    reset_trajectory_collection()
    print("  Priming pass (not scored)...")
    for q in c2_fixture:
        _run_query_resilient(q["text"])
    warm = run_pass("Cache-warm (post-priming)", c2_fixture, ground_truth)

    print("\n[4/4] Golden recovery check")
    recovery = golden_recovery()

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "novel_path_cache_cold": {"correct": cold["correct"], "scored": cold["scored"], "accuracy": cold["accuracy"]},
        "cache_hit_path_warm": {"correct": warm["correct"], "scored": warm["scored"], "accuracy": warm["accuracy"]},
        "golden_recovery": recovery,
        "cold_detail": cold["results"],
        "warm_detail": warm["results"],
    }
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nReport written to {out_path}")
    print(json.dumps({
        "novel_path_accuracy": f"{cold['correct']}/{cold['scored']} ({cold['accuracy']:.1%})",
        "cache_hit_accuracy": f"{warm['correct']}/{warm['scored']} ({warm['accuracy']:.1%})",
        "golden_recovered": recovery["recovered"],
        "golden_still_failing": recovery["still_failing"],
    }, indent=2))


if __name__ == "__main__":
    main()

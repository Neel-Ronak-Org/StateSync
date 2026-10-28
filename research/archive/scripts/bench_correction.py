#!/usr/bin/env python3
"""
scripts/bench_correction.py
C2 Deliverable 6 -- runs all 12 fixtures/malformed_queries.json seeds
through the correction loop (via the debug_candidate_sql test-harness
injection, orchestrator/state.py), emitting per-category: attempts made,
repaired-within-bound count, terminal-exit count. Honest reporting; no
invented threshold (C2-V2).

Each seed's malformed SQL is injected as the attempt-0 candidate, bypassing
LLM generation for that one attempt only -- exactly the mechanism C2-V1
describes ("Seed the agent's first attempt with the exact malformed query
... by injecting it as the attempt-0 candidate via the test harness"). Any
retry (attempt >= 1) is real LLM-generated correction, using the accumulated
error history, same as a production retry.

Usage:
    python3 scripts/bench_correction.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from shipped_flags import (require_shipped_flags, add_flag_guard_argument,
                           FlagStateError)

from bench_memory import REPO_ROOT, run_query

FIXTURE_PATH = REPO_ROOT / "fixtures" / "malformed_queries.json"
REPORT_PATH = REPO_ROOT / "docs" / "bench_correction_report.json"

# A short natural-language question per seed, for the retry-path LLM calls
# and the final synthesizer explanation. Irrelevant to attempt 0 (the
# debug_candidate_sql override bypasses generation there entirely).
_SEED_QUESTIONS = {
    "mq_01": "What is the average delivery delay by seller for delivered orders?",
    "mq_02": "List the first 10 shipments.",
    "mq_03": "What is the seller ID for each order?",
    "mq_04": "Show me all orders.",
    "mq_05": "Which orders were placed after 2025-01-01?",
    "mq_06": "What is each seller's order value?",
    "mq_07": "Set every order's value to zero.",
    "mq_08": "Which locations are within 5km of this point?",
    "mq_09": "Which orders were placed via the B2C channel?",
    "mq_10": "What is the total line value per order?",
    "mq_11": "Cross-reference every order against every dispute and every fulfillment.",
    "mq_12": "What is one divided by zero?",
}


def classify_outcome(seed_id: str, errors: list[dict], attempt: int, ok_final: bool) -> str:
    if not errors:
        # No error ever recorded -- either a clean first-try success, or
        # (mq_10) a query that never reaches the classifier at all because
        # it executes successfully with silently wrong data (GAP-2/BD3).
        return "never_reached_classifier" if seed_id == "mq_10" else "immediate_success"
    if len(errors) == attempt + 1:
        # Every attempt through the last one failed.
        last_category = errors[-1]["category"]
        from_terminal = last_category in ("permission_denied", "unclassified")
        return "terminal_immediate" if (from_terminal and attempt == 0) else (
            "terminal" if from_terminal else "exhausted"
        )
    # len(errors) <= attempt: the last invocation succeeded after >=1 failure.
    return "repaired"


def run_seed(seed: dict) -> dict:
    question = _SEED_QUESTIONS[seed["id"]]
    data = run_query(question, debug_candidate_sql=seed["sql"])
    trace = data.get("state_trace", {})
    errors = trace.get("errors", [])
    attempt = trace.get("attempt", 0)
    ok_final = bool(trace.get("sql_rows_count", 0) > 0 or (errors and len(errors) <= attempt))

    outcome = classify_outcome(seed["id"], errors, attempt, ok_final)
    first_category = errors[0]["category"] if errors else None

    return {
        "id": seed["id"],
        "expected_category": seed["expected_category"],
        "first_classified_category": first_category,
        "classified_correctly": (
            first_category == seed["expected_category"]
            if seed["id"] != "mq_10" else first_category is None
        ),
        "attempts_made": attempt + 1 if errors or trace.get("executed_sql") else 0,
        "final_attempt": attempt,
        "outcome": outcome,
        "error_categories_seen": [e["category"] for e in errors],
        "answer_text": data.get("answer_text", ""),
    }


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
            'bench_correction.py (M5 self-correction repair rate)',
            allow_nonshipped=_args.allow_nonshipped_flags, fixture=FIXTURE_PATH)
    except FlagStateError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2)
    out_path = Path(_args.out)
    if out_path != REPORT_PATH and out_path.exists():
        print(f"ERROR: {out_path} exists. Verification results are append-only "
              f"-- choose a new --out path.")
        raise SystemExit(1)

    print("=== bench_correction.py -- C2-V2 repair rate (M5 quantitative half) ===\n")
    seeds = json.loads(FIXTURE_PATH.read_text())["seeds"]

    results = []
    for seed in seeds:
        print(f"[{seed['id']}] expected={seed['expected_category']} ...", end=" ", flush=True)
        try:
            r = run_seed(seed)
        except Exception as exc:
            r = {
                "id": seed["id"], "expected_category": seed["expected_category"],
                "first_classified_category": None, "classified_correctly": False,
                "attempts_made": 0, "final_attempt": 0, "outcome": "ERROR",
                "error_categories_seen": [], "answer_text": str(exc),
            }
        results.append(r)
        print(f"outcome={r['outcome']} classified_correctly={r['classified_correctly']}")

    # Per-category breakdown.
    by_category: dict[str, dict] = {}
    for r in results:
        cat = r["expected_category"]
        by_category.setdefault(cat, {
            "seeds": [], "classified_correctly": 0, "repaired": 0,
            "terminal": 0, "exhausted": 0, "other": 0,
        })
        entry = by_category[cat]
        entry["seeds"].append(r["id"])
        if r["classified_correctly"]:
            entry["classified_correctly"] += 1
        if r["outcome"] == "repaired":
            entry["repaired"] += 1
        elif r["outcome"] in ("terminal", "terminal_immediate"):
            entry["terminal"] += 1
        elif r["outcome"] == "exhausted":
            entry["exhausted"] += 1
        else:
            entry["other"] += 1

    total_classified_correctly = sum(1 for r in results if r["classified_correctly"])

    report = {
        "flag_provenance": flag_provenance,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_seeds": len(results),
        "total_classified_correctly": total_classified_correctly,
        "per_seed": results,
        "per_category": by_category,
    }

    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nReport written to {REPORT_PATH.relative_to(REPO_ROOT)}")
    print(f"\nClassification: {total_classified_correctly}/{len(results)} correct.\n")
    print("Per-category breakdown:")
    for cat, entry in sorted(by_category.items()):
        print(
            f"  {cat:>20}: seeds={entry['seeds']} "
            f"classified={entry['classified_correctly']}/{len(entry['seeds'])} "
            f"repaired={entry['repaired']} terminal={entry['terminal']} "
            f"exhausted={entry['exhausted']} other={entry['other']}"
        )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
scripts/check_consistency.py
C1-V3 (M2) procedure, written as a reusable script rather than a one-off --
F-V3 explicitly re-runs "C1-V3 (M2)... unchanged" in Phase F, and F2 Notes
say not to write a second, looser procedure for that re-check.

Runs the 10 base queries from fixtures/golden/phase_c1.json five times each,
under both memory.enabled flag states, and compares result sets by
execution-based dataframe equality: column-set equality plus row-multiset
equality, column order and row order ignored (same definition as C1-V1's
reference, C1-V3 spec text).

For each (query, flag state) pair, counts the number of DISTINCT result sets
seen across the 5 runs. Pass condition (C1-V3): mean distinct-result-set
count with memory enabled <= control mean, and no query with memory enabled
produces MORE distinct result sets than it did in control.

Usage:
    python3 scripts/check_consistency.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from shipped_flags import (require_shipped_flags, add_flag_guard_argument,
                           FlagStateError)

from bench_memory import (
    REPO_ROOT,
    FIXTURE_PATH,
    restart_orchestrator,
    reset_trajectory_collection,
    run_query,
    set_memory_enabled,
)

REPORT_PATH = REPO_ROOT / "docs" / "consistency_report.json"
RUNS_PER_QUERY = 5


def _signature(sql_columns: list[str], sql_rows: list[dict]) -> str:
    """Canonical, order-independent signature for dataframe equality."""
    col_sig = tuple(sorted(sql_columns))
    row_tuples = []
    for row in sql_rows:
        row_tuples.append(tuple(sorted((k, json.dumps(v, sort_keys=True, default=str)) for k, v in row.items())))
    row_sig = tuple(sorted(row_tuples))
    return json.dumps({"columns": col_sig, "rows": row_sig}, default=str)


def run_flag_state(base_queries: list[dict], memory_enabled: bool) -> dict[str, dict]:
    label = "memory.enabled=true" if memory_enabled else "memory.enabled=false"
    print(f"\n--- {label} ---")
    set_memory_enabled(memory_enabled)
    restart_orchestrator()
    reset_trajectory_collection()

    per_query: dict[str, dict] = {}
    for q in base_queries:
        signatures = []
        hits = []
        for run_idx in range(RUNS_PER_QUERY):
            data = run_query(q["text"])
            trace = data.get("state_trace", {})
            sig = _signature(trace.get("sql_columns", []), trace.get("sql_rows", []))
            signatures.append(sig)
            hits.append(bool(trace.get("memory_hit", False)))
        distinct = len(set(signatures))
        per_query[q["id"]] = {
            "distinct_result_sets": distinct,
            "runs": RUNS_PER_QUERY,
            "memory_hit_per_run": hits,
        }
        print(f"    [{q['id']}] distinct_result_sets={distinct}/{RUNS_PER_QUERY} hits={hits}")
    return per_query


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
            'check_consistency.py (M2 consistency)', allow_nonshipped=_args.allow_nonshipped_flags)
    except FlagStateError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2)
    out_path = Path(_args.out)
    if out_path != REPORT_PATH and out_path.exists():
        print(f"ERROR: {out_path} exists. Verification results are append-only "
              f"-- choose a new --out path.")
        raise SystemExit(1)

    print("=== check_consistency.py -- C1-V3 (M2) ===")
    fixture = json.loads(FIXTURE_PATH.read_text())
    base_queries = [q for q in fixture["queries"] if q["group"] == "base"]
    assert len(base_queries) == 10, f"expected 10 base queries, found {len(base_queries)}"

    control = run_flag_state(base_queries, memory_enabled=False)
    treatment = run_flag_state(base_queries, memory_enabled=True)

    control_mean = sum(v["distinct_result_sets"] for v in control.values()) / len(control)
    treatment_mean = sum(v["distinct_result_sets"] for v in treatment.values()) / len(treatment)

    worse_queries = [
        qid for qid in control
        if treatment[qid]["distinct_result_sets"] > control[qid]["distinct_result_sets"]
    ]

    report = {
        "flag_provenance": flag_provenance,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runs_per_query": RUNS_PER_QUERY,
        "control": {"flag": "memory.enabled=false", "per_query": control, "mean_distinct_result_sets": control_mean},
        "treatment": {"flag": "memory.enabled=true", "per_query": treatment, "mean_distinct_result_sets": treatment_mean},
        "pass": treatment_mean <= control_mean and not worse_queries,
        "worse_queries": worse_queries,
    }

    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nReport written to {REPORT_PATH.relative_to(REPO_ROOT)}")
    print(json.dumps({
        "control_mean": control_mean,
        "treatment_mean": treatment_mean,
        "pass": report["pass"],
        "worse_queries": worse_queries,
    }, indent=2))


if __name__ == "__main__":
    main()

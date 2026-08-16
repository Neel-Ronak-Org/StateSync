#!/usr/bin/env python3
"""
scripts/diagnose_m4.py
Answers one question with evidence: WHY is M4 low?

M4 has been reported as a single number (28% cold) with no way to tell whether
that reflects wrong SQL, a strict scorer, or an upstream failure -- because
docs/c2v8_report.json stores only `match` and row counts. The SQL the model
wrote and the rows it returned were discarded, which is exactly the evidence a
diagnosis needs.

For every query this captures the generated SQL, the generated rows, the
ground-truth SQL and the ground-truth rows, then scores the SAME result under
three definitions and assigns a failure category:

  strict_ex   -- this project's C2-V8 signature: {n_columns, sorted row multiset},
                 with each row reduced to a sorted tuple of its own values. This
                 mirrors scripts/check_c2v8.py::_signature exactly, including the
                 within-row sort, because matching M4's historical definition is
                 the whole point of reporting it.
  standard_ex -- BIRD/Spider execution accuracy, computed by the SHIPPED
                 ExecutionAccuracyScorer imported from orchestrator/scorers.py.
  value_recall-- did the generated result contain the ground truth's values at
                 all, anywhere? See the warning on its own function: it is the
                 weakest number here and the easiest to over-read.

CORRECTION 2026-08-16, and why the earlier numbers moved. This script
originally computed standard_ex with the SAME helper as strict_ex, which sorts
values WITHIN each row. That is correct for a C2-V8-style signature (column
order genuinely should not matter there) and wrong for BIRD/Spider, which
compares each row as a tuple in column order. The effect is not cosmetic: under
the old helper a generated row (5, 3) matched a ground-truth row (3, 5), so two
columns whose values had been swapped scored as correct.

The consequence was that every "standard EX (BIRD/Spider definition)" figure
this project has published -- 40.0%, then 44.0% -- was produced by a definition
that is not BIRD's, and, worse, not the one this repo's own registered
`execution_accuracy` scorer implements. Two implementations of one named
concept, disagreeing silently.

Fixed by DELETING the second implementation rather than editing it: standard_ex
now calls ExecutionAccuracyScorer. The old definition is still reported
alongside as `standard_ex_legacy_valueset` so the published numbers remain
traceable and the size of the error is visible instead of quietly absorbed.

EVIDENCE IS NOW PERSISTED. The first version of this script was written to fix
exactly one thing -- that docs/c2v8_report.json stored `match` and a row count
while discarding the SQL and the rows -- and then discarded the rows itself,
writing only len(). Re-scoring a completed run under any new definition was
impossible; it required a fresh 25-query model run. Fourth instance of this
defect class in this build. The rows are now written to the report.

Read-only. Writes a JSON report; refuses to overwrite an existing one.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "fixtures" / "golden" / "phase_c2.json"
ORCH = "http://localhost:8090"
MCP = "http://localhost:8080"
TENANT = "t_capstone_default"

# Standard EX comes from the SHIPPED scorer, not a copy of it. If this import
# breaks, the run must fail rather than fall back to a local reimplementation --
# a local copy is what caused the two definitions to drift apart.
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
from scorers import ExecutionAccuracyScorer  # noqa: E402

_EX = ExecutionAccuracyScorer()

# Rows are persisted so a completed run can be re-scored without re-querying the
# model. Capped only to stop a pathological result set producing a huge
# artifact; when the cap bites it is recorded explicitly rather than silently.
MAX_ROWS_PERSISTED = 2000


def mcp_run_select(sql: str) -> dict:
    payload = json.dumps({
        "jsonrpc": "2.0", "id": "1", "method": "data.run_select",
        "params": {"sql": sql},
    })
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "mcp", "python3", "-c", f"""
import json, urllib.request
req = urllib.request.Request({MCP + "/rpc"!r},
    data={payload!r}.encode(),
    headers={{"Content-Type": "application/json",
              "X-Tenant-ID": {TENANT!r},
              "X-Session-ID": "diagnose-m4"}},
    method="POST")
print(urllib.request.urlopen(req, timeout=60).read().decode())
"""],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    line = [l for l in out.stdout.splitlines() if l.startswith("{")]
    if not line:
        return {"ok": False, "rows": [], "error": out.stderr[-200:]}
    return json.loads(line[-1]).get("result", {"ok": False, "rows": []})


def run_pipeline(text: str) -> dict:
    req = urllib.request.Request(
        f"{ORCH}/query", data=json.dumps({"query": text}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def _tuples(rows: list[dict]) -> list[tuple]:
    """
    Each row as a SORTED tuple of its stringified values (column names ignored).

    The within-row sort is deliberate and belongs ONLY to the C2-V8 signature,
    which it reproduces byte-for-byte from scripts/check_c2v8.py::_signature.
    Do not reuse this for anything claiming to be BIRD/Spider EX: it makes
    (5, 3) and (3, 5) the same row.
    """
    return sorted(tuple(sorted(json.dumps(v, sort_keys=True, default=str)
                               for v in row.values())) for row in rows)


def score_strict(gen_cols, gen_rows, ref_cols, ref_rows) -> bool:
    """C2-V8's own signature: column COUNT participates, even with zero rows."""
    return (len(gen_cols), _tuples(gen_rows)) == (len(ref_cols), _tuples(ref_rows))


def score_standard_ex(gen_rows, ref_rows) -> dict:
    """
    BIRD/Spider execution accuracy, delegated to the shipped
    ExecutionAccuracyScorer so there is exactly one implementation in the repo.
    Returns the scorer's full result (match + row_recall), not just a bool.
    """
    return _EX.score(gen_rows, ref_rows)


def score_standard_ex_legacy(gen_rows, ref_rows) -> bool:
    """
    The definition this script used until 2026-08-16, retained ONLY so the
    previously published 40.0% / 44.0% figures stay traceable and the gap
    between the two is visible. It is not BIRD's definition; see the module
    docstring. Reported alongside, never instead.
    """
    return _tuples(gen_rows) == _tuples(ref_rows)


def score_value_recall(gen_rows, ref_rows) -> float:
    """
    Fraction of the ground truth's DISTINCT VALUES that appear ANYWHERE in the
    generated result -- any column, any row, unaligned.

    READ THIS BEFORE QUOTING IT. This is by far the weakest number the script
    produces and it flatters the system. It ignores which column a value sits
    in, ignores row alignment, and de-duplicates, so a query that returns every
    row in the table scores 1.0 against a one-row ground truth purely because
    the right row is somewhere in the pile. c2g_01 is exactly that: 30 rows
    returned against a 1-row answer, value_recall 1.00.

    It earns its place only as a contrast to the exact scorers -- "the values
    were at least present" -- and the categoriser therefore checks ROW COUNT
    before it consults recall. It is not a capability metric and the mean of it
    across a fixture should not be presented as one.
    """
    ref_vals = {json.dumps(v, sort_keys=True, default=str)
                for row in ref_rows for v in row.values()}
    if not ref_vals:
        return 1.0 if not gen_rows else 0.0
    gen_vals = {json.dumps(v, sort_keys=True, default=str)
                for row in gen_rows for v in row.values()}
    return len(ref_vals & gen_vals) / len(ref_vals)


def unrecovered_error(gen: dict) -> bool:
    """
    Did the FINAL attempt fail, as opposed to some earlier attempt that the
    correction loop then repaired?

    This was previously `bool(state_trace["errors"])`, which is wrong by
    construction: orchestrator/state.py marks `errors` ACCUMULATE, and C2-V5
    requires every attempt to be retained, so a query that failed once and was
    then successfully repaired carries an error record forever. Every query the
    old check labelled "SQL execution error (unrecovered)" had in fact returned
    rows -- the label fired on the correction loop WORKING.

    The sound signal: rows came back => the last attempt succeeded. Otherwise a
    genuine terminal failure is an error record from the final attempt whose
    category is not `empty_result` (an accepted empty answer is terminal-success
    shaped in sql_agent.py, and empty-vs-empty is already settled by the exact
    scorers before this is consulted).
    """
    errs = gen.get("errors") or []
    if not errs:
        return False
    if gen.get("rows"):
        return False
    return (errs[-1] or {}).get("category") != "empty_result"


def categorise(gen, ref, strict, std, recall) -> str:
    if gen.get("route") != "sql":
        return "misrouted (never ran SQL)"
    if not gen.get("sql"):
        return "no SQL generated"
    # Correctness is decided BEFORE the error history is consulted. `errors`
    # accumulates every attempt (C2-V5 requires that it does), so a query that
    # failed at attempt 0 and was repaired by the correction loop still carries
    # an error entry. Checking it first labelled recovered-and-correct queries
    # as execution errors -- the correction loop working looked like a failure.
    if strict:
        return "correct"
    if std:
        return "correct under standard EX; strict scorer counts column width"
    if unrecovered_error(gen):
        return "SQL execution error (unrecovered)"
    # Row count is checked BEFORE value recall. A query returning 30 rows that
    # merely CONTAINS the 1 expected row scores recall 1.0, and checking recall
    # first labelled that a "projection" problem when it is a missing
    # aggregation or filter -- a different defect with a different fix.
    if len(gen["rows"]) == len(ref["rows"]):
        if recall == 1.0:
            return "right values, wider/narrower projection"
        return "right row COUNT, wrong values"
    if recall == 1.0:
        return "right values but WRONG ROW COUNT (missing aggregation/filter)"
    if not gen["rows"] and ref["rows"]:
        return "returned nothing; ground truth has rows"
    if gen["rows"] and not ref["rows"]:
        return "returned rows; ground truth is empty"
    return "wrong result shape and values"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if args.out.exists():
        print(f"ERROR: {args.out} exists. Results are append-only.")
        return 1

    queries = json.loads(FIXTURE.read_text())["queries"]
    if args.limit:
        queries = queries[:args.limit]

    results, cats = [], {}
    n_strict = n_std = n_std_legacy = 0
    recalls: list[float] = []
    row_recalls: list[float] = []

    for i, q in enumerate(queries, 1):
        gt_sql = q.get("ground_truth_sql") or ""
        gt = mcp_run_select(gt_sql) if gt_sql else {"ok": False, "rows": [], "columns": []}
        ref_rows = gt.get("rows", []) or []
        ref_cols = gt.get("columns", []) or (list(ref_rows[0]) if ref_rows else [])

        try:
            resp = run_pipeline(q["text"])
            tr = resp.get("state_trace", {})
            gen = {"route": tr.get("route"), "sql": tr.get("executed_sql"),
                   "rows": tr.get("sql_rows") or [],
                   "cols": tr.get("sql_columns") or [],
                   "errors": tr.get("errors") or [],
                   "attempt": tr.get("attempt")}
        except Exception as exc:
            gen = {"route": None, "sql": None, "rows": [], "cols": [],
                   "errors": [{"category": "transport",
                               "message": f"{type(exc).__name__}: {exc}"}],
                   "attempt": None}

        strict = score_strict(gen["cols"], gen["rows"], ref_cols, ref_rows)
        ex = score_standard_ex(gen["rows"], ref_rows)
        std = bool(ex["match"])
        std_legacy = score_standard_ex_legacy(gen["rows"], ref_rows)
        recall = score_value_recall(gen["rows"], ref_rows)
        cat = categorise(gen, {"rows": ref_rows}, strict, std, recall)

        n_strict += strict
        n_std += std
        n_std_legacy += std_legacy
        recalls.append(recall)
        row_recalls.append(ex["row_recall"])
        cats[cat] = cats.get(cat, 0) + 1

        results.append({
            "id": q["id"], "text": q["text"], "category": cat,
            "strict_ex": strict,
            "standard_ex": std,
            "standard_ex_legacy_valueset": std_legacy,
            "value_recall": round(recall, 3),
            "row_recall": round(ex["row_recall"], 3),
            "generated_sql": gen["sql"] or "",
            "ground_truth_sql": gt_sql,
            "generated_row_count": len(gen["rows"]),
            "reference_row_count": len(ref_rows),
            "generated_cols": len(gen["cols"]), "reference_cols": len(ref_cols),
            # The evidence the previous version threw away. Without these the
            # run cannot be re-scored under a new definition without paying for
            # 25 fresh model calls.
            "generated_rows": gen["rows"][:MAX_ROWS_PERSISTED],
            "reference_rows": ref_rows[:MAX_ROWS_PERSISTED],
            "generated_rows_truncated": len(gen["rows"]) > MAX_ROWS_PERSISTED,
            "reference_rows_truncated": len(ref_rows) > MAX_ROWS_PERSISTED,
            "errors": [{"attempt": e.get("attempt"), "category": e.get("category"),
                        "message": str(e.get("message"))[:300]} for e in gen["errors"]],
            "final_attempt": gen["attempt"],
        })
        flag = "" if std == std_legacy else "  [EX defs disagree]"
        print(f"  [{i:2d}/{len(queries)}] {q['id']}  strict={int(strict)} "
              f"stdEX={int(std)} recall={recall:.2f}  {cat}{flag}")

    n = len(queries)
    report = {
        "generated_by": "scripts/diagnose_m4.py",
        "report_schema": 2,
        "schema_note": (
            "v2: rows are persisted (v1 stored only counts); standard_ex is now "
            "the shipped ExecutionAccuracyScorer, with v1's within-row-sorted "
            "definition retained as standard_ex_legacy_valueset; the "
            "'unrecovered error' category no longer fires on repaired queries."
        ),
        "n": n,
        "strict_ex": {"correct": n_strict, "accuracy": round(n_strict / n, 4)},
        "standard_ex": {"correct": n_std, "accuracy": round(n_std / n, 4),
                        "definition": "orchestrator/scorers.py ExecutionAccuracyScorer"},
        "standard_ex_legacy_valueset": {
            "correct": n_std_legacy, "accuracy": round(n_std_legacy / n, 4),
            "definition": "pre-2026-08-16 in-script definition; sorts values WITHIN "
                          "each row, so (5,3) matches (3,5). Not BIRD's. Retained "
                          "only to keep the published 40%/44% traceable.",
        },
        "mean_value_recall": round(sum(recalls) / n, 4),
        "mean_value_recall_warning": (
            "Unaligned, cross-column, de-duplicated. Inflated by construction; "
            "see score_value_recall's docstring. Not a capability metric."
        ),
        "mean_row_recall": round(sum(row_recalls) / n, 4),
        "failure_categories": dict(sorted(cats.items(), key=lambda kv: -kv[1])),
        "results": results,
    }
    args.out.write_text(json.dumps(report, indent=2, default=str) + "\n")

    print(f"\n{'='*58}")
    print(f"  strict EX  (this project's scorer) : {n_strict}/{n} = {n_strict/n:.1%}")
    print(f"  standard EX (shipped EX scorer)    : {n_std}/{n} = {n_std/n:.1%}")
    print(f"  standard EX (legacy, within-row)   : {n_std_legacy}/{n} = {n_std_legacy/n:.1%}")
    print(f"  mean row recall                    : {sum(row_recalls)/n:.1%}")
    print(f"  mean value recall (weak, see docs) : {sum(recalls)/n:.1%}")
    print("\n  failure categories:")
    for k, v in sorted(cats.items(), key=lambda kv: -kv[1]):
        print(f"    {v:2d}  {k}")
    print(f"\n  wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

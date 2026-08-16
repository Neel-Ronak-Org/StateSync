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

  strict_ex   -- this project's C2-V8 signature: {n_columns, sorted row multiset}.
                 Two empty results with different projections count as DIFFERENT.
  standard_ex -- BIRD/Spider-style execution accuracy: compare result sets as
                 sorted tuples of values. Two empty results are EQUAL, which is
                 where it diverges from strict_ex.
  value_recall-- did the generated result contain the ground truth's values at
                 all (ignoring extra columns)? Catches "right answer, wider
                 projection", which both of the above call a failure.

The three numbers together separate "the model is wrong" from "the scorer is
strict". Neither is assumed.

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
    """Each row as a sorted tuple of its stringified VALUES (column names ignored)."""
    return sorted(tuple(sorted(json.dumps(v, sort_keys=True, default=str)
                               for v in row.values())) for row in rows)


def score_strict(gen_cols, gen_rows, ref_cols, ref_rows) -> bool:
    """C2-V8's own signature: column COUNT participates, even with zero rows."""
    return (len(gen_cols), _tuples(gen_rows)) == (len(ref_cols), _tuples(ref_rows))


def score_standard_ex(gen_rows, ref_rows) -> bool:
    """BIRD/Spider-style: compare result sets. Two empty results are equal."""
    return _tuples(gen_rows) == _tuples(ref_rows)


def score_value_recall(gen_rows, ref_rows) -> float:
    """
    Fraction of ground-truth VALUES present anywhere in the generated result.
    A correct answer returned alongside extra columns scores 1.0 here while
    failing both exact scorers -- that gap is the point of measuring it.
    """
    ref_vals = {json.dumps(v, sort_keys=True, default=str)
                for row in ref_rows for v in row.values()}
    if not ref_vals:
        return 1.0 if not gen_rows else 0.0
    gen_vals = {json.dumps(v, sort_keys=True, default=str)
                for row in gen_rows for v in row.values()}
    return len(ref_vals & gen_vals) / len(ref_vals)


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
    if gen.get("error"):
        return "SQL execution error (unrecovered)"
    if recall == 1.0:
        return "right values, wider/narrower projection"
    if len(gen["rows"]) == len(ref["rows"]):
        return "right row COUNT, wrong values"
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
    n_strict = n_std = 0
    recalls = []

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
                   "error": bool(tr.get("errors"))}
        except Exception as exc:
            gen = {"route": None, "sql": None, "rows": [], "cols": [],
                   "error": f"{type(exc).__name__}: {exc}"}

        strict = score_strict(gen["cols"], gen["rows"], ref_cols, ref_rows)
        std = score_standard_ex(gen["rows"], ref_rows)
        recall = score_value_recall(gen["rows"], ref_rows)
        cat = categorise(gen, {"rows": ref_rows}, strict, std, recall)

        n_strict += strict
        n_std += std
        recalls.append(recall)
        cats[cat] = cats.get(cat, 0) + 1

        results.append({
            "id": q["id"], "text": q["text"], "category": cat,
            "strict_ex": strict, "standard_ex": std, "value_recall": round(recall, 3),
            "generated_sql": (gen["sql"] or "")[:600],
            "ground_truth_sql": gt_sql[:600],
            "generated_rows": len(gen["rows"]), "reference_rows": len(ref_rows),
            "generated_cols": len(gen["cols"]), "reference_cols": len(ref_cols),
        })
        print(f"  [{i:2d}/{len(queries)}] {q['id']}  strict={int(strict)} "
              f"stdEX={int(std)} recall={recall:.2f}  {cat}")

    n = len(queries)
    report = {
        "generated_by": "scripts/diagnose_m4.py",
        "n": n,
        "strict_ex": {"correct": n_strict, "accuracy": round(n_strict / n, 4)},
        "standard_ex": {"correct": n_std, "accuracy": round(n_std / n, 4)},
        "mean_value_recall": round(sum(recalls) / n, 4),
        "failure_categories": dict(sorted(cats.items(), key=lambda kv: -kv[1])),
        "results": results,
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")

    print(f"\n{'='*58}")
    print(f"  strict EX  (this project's scorer) : {n_strict}/{n} = {n_strict/n:.1%}")
    print(f"  standard EX (BIRD/Spider style)    : {n_std}/{n} = {n_std/n:.1%}")
    print(f"  mean value recall                  : {sum(recalls)/n:.1%}")
    print("\n  failure categories:")
    for k, v in sorted(cats.items(), key=lambda kv: -kv[1]):
        print(f"    {v:2d}  {k}")
    print(f"\n  wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

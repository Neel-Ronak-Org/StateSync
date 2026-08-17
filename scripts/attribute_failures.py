#!/usr/bin/env python3
"""
scripts/attribute_failures.py
Attributes each end-to-end failure to a STAGE, instead of scoring the pipeline
as one opaque number.

THE GAP THIS FILLS. Every existing metric measures either one stage in
isolation or the final result, and nothing connects them:

    M9     supervisor routing accuracy          -- one transfer step
    D-V3   normalizer leaves English untouched  -- one transfer step
    D-V2   proper nouns survive into the SQL    -- a real transfer metric
    M4/EX  end-to-end correctness               -- conflates every stage

So when a query fails there has been no way to say WHERE the user's intent was
lost: misrouted, mangled by the normalizer, given the wrong tables, or written
into bad SQL over the right ones. "Verification tells you something broke.
Diagnosis tells you what to fix" (PROJECT_DIRECTION §2) -- this is that
distinction applied to the last uninstrumented place in the pipeline.

THE PROXY, and its limits stated up front. For each failed query it compares
the SET OF TABLES in the generated SQL against the set in the reference SQL:

    different tables -> intent was lost BEFORE SQL generation. The agent
                        misunderstood what was being asked, or picked the wrong
                        part of the schema. A better SQL writer does not fix it.
    same tables      -> intent arrived intact and the SQL over those tables was
                        wrong (or the reference is). A SQL-capability problem,
                        or a fixture problem.
    no SQL at all    -> routing or generation never produced a query.

This is a PROXY, not a proof. Identical table sets can still mean a
misunderstood question, and a differing set can be a legitimate alternative
formulation -- which is exactly what it found on first use (below). It
separates two populations that were previously one number; it does not
adjudicate individual cases.

WHAT IT FOUND ON ITS FIRST RUN, recorded because it argues for the tool. Across
three models the wrong-table rate falls with model strength (7B 36%, coder:14b
20%, Gemini 17%), so table selection is partly capability. But several
"wrong-table" cases turned out to be the FIXTURE:

  c2g_07  two independent models both reached for order_items/orders because
          "catalog items SOLD" means ordered. The reference counted items
          LISTED. The models were right; the reference was wrong -- confirmed
          independently by the ground-truth audit.
  c2g_09  "which buyer apps..." -- reference joins network_participants purely
          to fetch legal_name, a column the question never asks for.
  c2g_14  same pattern, on all three models.
  c2g_24  "how many catalog items does each seller have..." -- same pattern.

c2g_09 and c2g_24 were NOT found by the manual ground-truth audit. This script
found them on its first execution, which is the argument for having it.

Reads existing diagnose_m4 reports. No model calls, no database, no cost.

    python3 scripts/attribute_failures.py docs/m4_diagnosis_*.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# FROM/JOIN followed by an identifier. Deliberately simple: a full parse via
# pglast would be more precise, but it lives in the orchestrator image and this
# script must run anywhere a report file does. The keyword filter covers the
# constructs these fixtures actually use; anything it mis-parses shows up as a
# table name that is not in the schema, which is visible in the output.
_TABLE_RE = re.compile(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.I)
_NOT_TABLES = {"select", "where", "group", "order", "limit", "on", "as",
               "and", "or", "not", "exists", "lateral", "unnest"}


def tables(sql: str | None) -> set[str]:
    return {t.lower() for t in _TABLE_RE.findall(sql or "")
            if t.lower() not in _NOT_TABLES}


def attribute(report_path: Path) -> dict:
    data = json.loads(report_path.read_text())
    results = data.get("results", [])
    prov = (data.get("flag_provenance") or {}).get("llm", {})
    label = prov.get("provider") or report_path.stem

    buckets: dict[str, list] = {"wrong_tables": [], "same_tables": [], "no_sql": []}
    for r in results:
        if r.get("standard_ex"):
            continue
        gen, ref = tables(r.get("generated_sql")), tables(r.get("ground_truth_sql"))
        if not gen:
            buckets["no_sql"].append((r["id"], gen, ref))
        elif gen == ref:
            buckets["same_tables"].append((r["id"], gen, ref))
        else:
            buckets["wrong_tables"].append((r["id"], gen, ref))

    n_fail = sum(len(v) for v in buckets.values())
    print(f"\n{'='*72}\n{report_path.name}   ({label})\n{'='*72}")
    print(f"  scored {len(results)}, failed {n_fail}")
    if not n_fail:
        return {}
    for key, meaning in (
        ("wrong_tables", "intent lost BEFORE SQL generation (wrong part of schema)"),
        ("same_tables", "intent arrived; SQL over the right tables was wrong (or the reference is)"),
        ("no_sql", "no SQL produced at all (routing/generation)"),
    ):
        items = buckets[key]
        print(f"\n  {key:13s} {len(items):2d}/{n_fail}  ({100*len(items)/n_fail:3.0f}%)  {meaning}")
        for qid, gen, ref in items:
            if key == "wrong_tables":
                print(f"      {qid}: used {sorted(gen)}")
                print(f"      {'':{len(qid)}}  need {sorted(ref)}")
            else:
                print(f"      {qid}")
    return {k: [i[0] for i in v] for k, v in buckets.items()}


def main(argv: list[str]) -> int:
    paths = [Path(p) for p in argv[1:]]
    if not paths:
        print(__doc__)
        return 2
    missing = [p for p in paths if not p.exists()]
    if missing:
        print(f"ERROR: no such report(s): {[str(m) for m in missing]}", file=sys.stderr)
        return 1

    per_run = {}
    for p in paths:
        per_run[p.name] = attribute(p)

    # A query that lands in wrong_tables on EVERY model is not a model problem.
    # That is the signal worth acting on, and it is invisible in a single run.
    if len(per_run) > 1:
        sets = [set(v.get("wrong_tables", [])) for v in per_run.values() if v]
        shared = set.intersection(*sets) if sets else set()
        print(f"\n{'='*72}\nWRONG-TABLE ON EVERY RUN COMPARED\n{'='*72}")
        print(f"  {sorted(shared) if shared else 'none'}")
        if shared:
            print("  A query every model resolves to different tables than the")
            print("  reference is a QUESTION or REFERENCE problem, not a model one.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

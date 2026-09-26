#!/usr/bin/env python3
"""
scripts/run_e2_v1.py
Drives E2-V1 against the LIVE pipeline: "Run all 30 queries. Validate every
response against A7. Score each generated spec against its frozen reference."

Split of responsibility, deliberately:
  this script      -- runs the 30 queries and performs the A7 validation half
  score_specs.py   -- performs the scoring half (already built, --score)

It exists because nothing previously drove E2-V1 end to end. Until the
Synthesizer actually emitted charts (Task 10), every generated spec was null
and the M7 number would have been a measurement of an unbuilt feature rather
than of generation quality.

A7 validation here reuses the orchestrator's own validator by import -- NOT a
reimplementation. A second Python copy of the rules would test the copy, which
is the exact failure mode A7's "two independent validations" rule exists to
prevent (the genuinely independent second validator is the frontend's, written
by hand in JavaScript and exercised in the browser).

Output is {query_id: chart_or_null}, the shape score_specs.py --score expects.
Model-sensitive: the resulting M7 is a LOCAL PROVISIONAL number and is recorded
as such, pending the graded re-baseline (docs/rebaseline_runbook.md).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from shipped_flags import (require_shipped_flags, add_flag_guard_argument,
                           FlagStateError)
from api_auth import auth_headers
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

from config_loader import cfg                                      # noqa: E402
from schemas.synthesizer_output import validate_synthesizer_output  # noqa: E402

FIXTURE = REPO_ROOT / "fixtures" / "golden" / "phase_e2.json"
DEFAULT_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8090")


def run_query(base_url: str, text: str, timeout_s: float) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{base_url}/query",
        data=json.dumps({"query": text}).encode(),
        headers=auth_headers({"Content-Type": "application/json"}),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--out", type=Path, required=True,
                    help="Where to write {query_id: chart}. Refuses to overwrite.")
    # Read below by the append-only check, which tells the user to "pass
    # --force" -- but was never registered, so doing what the message said
    # crashed with an argparse error (audit 007 F-007-6; docs/V3_PLAN.md 0.9).
    ap.add_argument("--force", action="store_true",
                    help="Deliberately replace an existing --out file.")
    # Shipped-flag guard -- see scripts/shipped_flags.py for the two incidents
    # this exists to prevent.
    add_flag_guard_argument(ap)
    args = ap.parse_args()
    try:
        flag_provenance = require_shipped_flags(
            'run_e2_v1.py (E2-V1 / M7 chart quality)',
            allow_nonshipped=args.allow_nonshipped_flags, fixture=FIXTURE)
    except FlagStateError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2)

    # Same append-only rule as every other verification artifact.
    if args.out.exists() and not args.force:
        print(f"ERROR: {args.out} exists. Results are append-only -- choose a new "
              f"--out, or pass --force to deliberately replace it.")
        return 1

    queries = json.loads(FIXTURE.read_text())["queries"]
    timeout_s = float(cfg.evaluation.query_timeout_s)

    generated: dict[str, Any] = {}
    a7_valid = a7_invalid = errors = 0
    invalid_detail: list[str] = []
    chart_present = 0

    print(f"E2-V1: running {len(queries)} queries against {args.url}\n")
    for i, q in enumerate(queries, start=1):
        qid = q["id"]
        started = time.monotonic()
        try:
            resp = run_query(args.url, q["text"], timeout_s)
        except Exception as exc:
            # Never record an empty reason -- the failure mode found in the
            # 5-run trend, where httpx timeouts stringified to "".
            errors += 1
            generated[qid] = None
            print(f"  [{i:2d}/{len(queries)}] {qid}  ERROR {type(exc).__name__}: {exc}")
            continue

        elapsed = time.monotonic() - started

        # E2-V1: "Validate every response against A7."
        payload = {
            "answer_text": resp.get("answer_text"),
            "chart": resp.get("chart"),
            "citations": resp.get("citations"),
        }
        # Returns (True, None) or (False, message) -- one message, not a list.
        ok, err = validate_synthesizer_output(payload)
        if ok:
            a7_valid += 1
        else:
            a7_invalid += 1
            invalid_detail.append(f"{qid}: {err}")

        chart = resp.get("chart")
        if chart is not None:
            chart_present += 1
        generated[qid] = chart

        print(f"  [{i:2d}/{len(queries)}] {qid}  a7={'ok' if ok else 'INVALID'}  "
              f"chart={'yes' if chart is not None else 'null'}  {elapsed:.1f}s")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(generated, indent=2) + "\n")

    print(f"\n── E2-V1 A7 validation half ──")
    print(f"  responses A7-valid:   {a7_valid}/{len(queries)}")
    print(f"  responses A7-invalid: {a7_invalid}")
    print(f"  transport errors:     {errors}")
    print(f"  chart populated:      {chart_present}   (chart:null: {len(queries) - chart_present - errors})")
    for d in invalid_detail:
        print(f"    INVALID {d}")
    print(f"\n  wrote {args.out}")
    print(f"  score with: python3 scripts/score_specs.py --score {args.out}")

    # The A7 half is binary and is this script's own pass condition. The M7
    # score is model-sensitive and is NOT gated here (BD6).
    return 0 if (a7_invalid == 0 and errors == 0) else 1


if __name__ == "__main__":
    sys.exit(main())

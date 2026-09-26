#!/usr/bin/env python3
"""
scripts/demo_olist.py
V2_STRATEGY §7 Real-Corpus Demo Deliverable.

Demonstrates a >=3-turn conversation over the real Olist corpus (1,551,698 rows),
ending in a chart, with conversation rewriting visible in the trace.

=== WHY THIS SCRIPT EXISTS ===
V2_STRATEGY §7 defines the demo acceptance bar:
"A >=3-turn conversation over the real corpus (1,551,698 rows), ending in a chart,
with the trace visible. If a viewer cannot tell it is a conversation, the
workstream is not done."

The shipped default flag state is `schema_retriever.target_schema = "public"`
(the 550-order synthetic ONDC corpus). That MUST remain the shipped default: M3,
M8, and shipped_flags.py are all defined against it. Changing the default would
silently invalidate every guard built to catch wrong-corpus contamination (such as
the 80d35c8 incident).

Therefore, this script:
1. Verifies preconditions (orchestrator health, API auth, live olist row count).
2. Flips `schema_retriever.target_schema = "olist"` using a count-asserted regex.
3. Restarts the orchestrator container to pick up the new flag value.
4. Executes a 3-turn conversation sharing one session_id:
     Turn 1: "How many orders are there in total?"
     Turn 2: "How many of those were delivered?" (elliptical: "those")
     Turn 3: "Break that down by customer state — top 5, as a chart." (elliptical + chart)
5. Prints trace evidence per turn (rewritten_query, rewrite_applied, SQL, rows, chart).
6. Evaluates and reports the 4-clause demo bar:
     Clause 1: >=3 turns completed without error
     Clause 2: >=1 turn had rewrite_applied=True (proves it is a conversation)
     Clause 3: Final turn returned a chart
     Clause 4: >=1 executed SQL is schema-qualified with 'olist.' (proves real corpus)
7. Writes a transcript JSON artifact to --out (default docs/demo_olist_transcript.json).
8. In a `finally:` block, restores the original config/flags.toml bytes VERBATIM,
   restarts the orchestrator, and verifies that `shipped_flags.py` reports shipped: true.

=== SAFETY & MEASUREMENT DISCIPLINE ===
Flag restoration is the single most critical safety requirement. If a run dies
leaving target_schema="olist", every subsequent benchmark or measurement in the
repository would silently measure against the wrong corpus.
Verbatim byte restoration and post-restoration verification guarantee zero contamination.

Usage:
    python3 scripts/demo_olist.py [--out PATH] [--keep-olist]
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

# Ensure scripts directory is on sys.path so sibling imports work reliably
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from api_auth import api_token, auth_headers
from bench_memory import ORCH_URL, restart_orchestrator
from shipped_flags import FLAGS_PATH, read_flag_state, require_shipped_flags

DEFAULT_OUT_PATH = REPO_ROOT / "docs" / "demo_olist_transcript.json"

# Canonical Olist row count asserted across all 9 tables in load_olist.sh:
# customers(99441) + orders(99441) + order_items(112650) + order_payments(103886) +
# order_reviews(100000) + products(32951) + sellers(3095) + geolocation(1000163) +
# product_category_name_translation(71) = 1,551,698 rows.
EXPECTED_OLIST_ROW_COUNT = 1551698

# Generous timeout per query: queries over the 1.55M-row olist corpus can require
# scanning or joining 100k+ rows on local hardware and take 60s+.
QUERY_TIMEOUT_S = 150

# Three turns designed per V2_STRATEGY §7.
# Turns 2 and 3 are deliberately elliptical ("those", "that") so that the rewrite node
# must resolve context from prior turns to produce standalone questions.
CONVERSATION_TURNS = [
    "How many orders are there in total?",
    "How many of those were delivered?",
    "Break that down by customer state — top 5, as a chart.",
]

# Regex matching target_schema within the [schema_retriever] section.
# Requires exactly one match in config/flags.toml to prevent ambiguous edits.
_TARGET_SCHEMA_RE = re.compile(
    r'(\[schema_retriever\]\s*\n(?:[^\[]*?\n)*?\s*target_schema\s*=\s*)"([^"]*)"'
)


def check_orchestrator_health() -> None:
    """Precondition 1: Verify orchestrator /health returns 200 OK."""
    try:
        with urllib.request.urlopen(f"{ORCH_URL}/health", timeout=5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("status") == "ok":
                    return
    except Exception as exc:
        raise RuntimeError(
            f"Precondition failed: orchestrator at {ORCH_URL}/health is not healthy: {exc}"
        )
    raise RuntimeError(
        f"Precondition failed: orchestrator at {ORCH_URL}/health returned unexpected response."
    )


def check_api_token() -> str:
    """
    Precondition 2: Verify an API bearer token resolves via scripts/api_auth.py.
    Fails loudly with actionable guidance if neither env var nor dev secret exists.
    """
    try:
        token = api_token()
        if not token or not token.strip():
            raise RuntimeError("API token resolved to an empty string.")
        return token.strip()
    except Exception as exc:
        raise RuntimeError(
            f"Precondition failed: could not resolve StateSync API token via scripts/api_auth.py:\n  {exc}"
        )


def query_and_verify_olist_corpus() -> int:
    """
    Precondition 3: Query the live database for total row count across all tables
    in the `olist` schema and assert it equals exactly 1551698.

    WHY THIS MATTERS: The demo must VERIFY the corpus it advertises rather than
    hardcoding a claim from memory. Reaches postgres using docker compose exec
    matching scripts/migrate.sh.
    """
    # Dynamic count query summing count(*) over all base tables in schema 'olist'
    sql = """
SELECT sum(c) FROM (
  SELECT (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name), false, true, '')))[1]::text::bigint AS c
  FROM information_schema.tables
  WHERE table_schema = 'olist' AND table_type = 'BASE TABLE'
) t;
"""
    cmd = [
        "docker", "compose", "exec", "-T", "postgres",
        "psql", "-U", "statesync_migrator", "-d", "statesync", "-Atc", sql,
    ]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Precondition failed: unable to query olist row count via postgres:\n"
            f"stderr: {proc.stderr}\nstdout: {proc.stdout}"
        )
    digits = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip().isdigit()]
    if not digits:
        raise RuntimeError(
            f"Precondition failed: no numeric row count returned from psql query:\n{proc.stdout}"
        )
    row_count = int(digits[-1])
    if row_count != EXPECTED_OLIST_ROW_COUNT:
        raise RuntimeError(
            f"Precondition failed: olist corpus row count mismatch! "
            f"Queried {row_count:,} rows, expected exactly {EXPECTED_OLIST_ROW_COUNT:,}."
        )
    return row_count


def set_target_schema(text: str, new_target: str) -> str:
    """
    Mutate schema_retriever.target_schema using a count-asserted regex.
    Enforces bench_multilingual.py's discipline: asserts exactly one replacement
    and refuses to guess if the file layout differs.
    """
    new_text, count = _TARGET_SCHEMA_RE.subn(rf'\g<1>"{new_target}"', text)
    if count != 1:
        raise RuntimeError(
            f"Could not find exactly one [schema_retriever] target_schema line in "
            f"{FLAGS_PATH} (found {count}). Refusing to guess -- edit it by hand."
        )
    return new_text


def restore_flags_and_verify(original_bytes: str) -> None:
    """
    Restore flags.toml by writing original bytes verbatim, restart orchestrator,
    and verify both disk and live state via scripts/shipped_flags.py.

    WHY VERBATIM: A regex restore can subtly alter formatting, comments, or quotes.
    Writing the original bytes captured at start guarantees exact restoration.
    Fails loudly and non-zero if restoration or verification encounters any defect.
    """
    print("\n[cleanup] Restoring original config/flags.toml bytes verbatim...")
    try:
        FLAGS_PATH.write_text(original_bytes)
    except Exception as exc:
        print("\n" + "!" * 78, file=sys.stderr)
        print("CRITICAL ERROR: FAILED TO WRITE ORIGINAL BYTES BACK TO config/flags.toml!", file=sys.stderr)
        print(f"Exception: {exc}", file=sys.stderr)
        print("MANUAL ACTION REQUIRED: Edit config/flags.toml and set:", file=sys.stderr)
        print('  [schema_retriever]', file=sys.stderr)
        print('  target_schema = "public"', file=sys.stderr)
        print("Then run: docker compose restart orchestrator && python3 scripts/shipped_flags.py", file=sys.stderr)
        print("!" * 78 + "\n", file=sys.stderr)
        sys.exit(1)

    print("    Restarting orchestrator container to pick up restored flags...")
    try:
        restart_orchestrator()
    except Exception as exc:
        print("\n" + "!" * 78, file=sys.stderr)
        print("CRITICAL ERROR: FAILED TO RESTART ORCHESTRATOR AFTER RESTORING flags.toml!", file=sys.stderr)
        print(f"Exception: {exc}", file=sys.stderr)
        print("MANUAL ACTION REQUIRED: Run `docker compose restart orchestrator`", file=sys.stderr)
        print("!" * 78 + "\n", file=sys.stderr)
        sys.exit(1)

    # Verification 1: Re-read flags.toml from disk and assert target_schema is back to 'public'
    restored_text = FLAGS_PATH.read_text()
    restored_state = read_flag_state(restored_text)
    actual_target = restored_state.get("schema_retriever.target_schema")
    if actual_target != "public":
        print("\n" + "!" * 78, file=sys.stderr)
        print("CRITICAL ERROR: RESTORATION VERIFICATION FAILED!", file=sys.stderr)
        print(f"config/flags.toml schema_retriever.target_schema is {actual_target!r}, expected 'public'!", file=sys.stderr)
        print("MANUAL ACTION REQUIRED: Edit config/flags.toml by hand, set target_schema = \"public\", then run:", file=sys.stderr)
        print("  docker compose restart orchestrator", file=sys.stderr)
        print("  python3 scripts/shipped_flags.py", file=sys.stderr)
        print("!" * 78 + "\n", file=sys.stderr)
        sys.exit(1)

    # Verification 2: Shell out to scripts/shipped_flags.py and assert JSON has "shipped": true
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "shipped_flags.py")],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print("\n" + "!" * 78, file=sys.stderr)
        print("CRITICAL ERROR: `python3 scripts/shipped_flags.py` FAILED AFTER RESTORATION!", file=sys.stderr)
        print(f"Exit code: {proc.returncode}", file=sys.stderr)
        print(f"stdout:\n{proc.stdout}", file=sys.stderr)
        print(f"stderr:\n{proc.stderr}", file=sys.stderr)
        print("MANUAL ACTION REQUIRED: Restore shipped flags and restart orchestrator:", file=sys.stderr)
        print('  Ensure target_schema = "public" in config/flags.toml', file=sys.stderr)
        print("  docker compose restart orchestrator", file=sys.stderr)
        print("  python3 scripts/shipped_flags.py", file=sys.stderr)
        print("!" * 78 + "\n", file=sys.stderr)
        sys.exit(1)

    try:
        shipped_report = json.loads(proc.stdout)
        if not shipped_report.get("shipped"):
            print("\n" + "!" * 78, file=sys.stderr)
            print("CRITICAL ERROR: `python3 scripts/shipped_flags.py` REPORTED shipped != true!", file=sys.stderr)
            print(json.dumps(shipped_report, indent=2), file=sys.stderr)
            print("MANUAL ACTION REQUIRED: Fix config/flags.toml and restart orchestrator:", file=sys.stderr)
            print('  Ensure target_schema = "public" in config/flags.toml', file=sys.stderr)
            print("  docker compose restart orchestrator", file=sys.stderr)
            print("!" * 78 + "\n", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        print("\n" + "!" * 78, file=sys.stderr)
        print(f"CRITICAL ERROR: COULD NOT PARSE shipped_flags.py JSON OUTPUT: {exc}", file=sys.stderr)
        print(f"Output was:\n{proc.stdout}", file=sys.stderr)
        print("!" * 78 + "\n", file=sys.stderr)
        sys.exit(1)

    print("    Restoration successfully verified: target_schema='public' and shipped=true.")


def send_query(base_url: str, text: str, session_id: str, timeout_s: float = QUERY_TIMEOUT_S) -> dict[str, Any]:
    """
    Submit a query turn to POST /query with bearer token authentication and session_id.
    """
    payload = json.dumps({"query": text, "session_id": session_id}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/query",
        data=payload,
        headers=auth_headers({"Content-Type": "application/json"}),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run a runnable, self-verifying >=3-turn demo conversation on the REAL Olist corpus "
            "(1,551,698 rows) satisfying V2_STRATEGY §7's demo bar."
        )
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT_PATH),
        help=(
            f"Path to write transcript JSON artifact (default: {DEFAULT_OUT_PATH}). "
            "A non-default path refuses to overwrite an existing file (append-only discipline)."
        ),
    )
    parser.add_argument(
        "--keep-olist",
        action="store_true",
        help=(
            "Deliberately skip restoring config/flags.toml to shipped state (for debugging). "
            "Prints a prominent warning and manual fix command if set."
        ),
    )
    args = parser.parse_args()

    out_path = Path(args.out)
    if out_path != DEFAULT_OUT_PATH and out_path.exists():
        print(
            f"ERROR: {out_path} exists. Verification results are append-only -- choose a new --out path.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Capture original flags.toml text in memory before any modification
    original_flags_text = FLAGS_PATH.read_text()
    original_state = read_flag_state(original_flags_text)
    if original_state.get("schema_retriever.target_schema") != "public":
        print(
            f"ERROR: config/flags.toml starts in non-shipped state: target_schema="
            f"{original_state.get('schema_retriever.target_schema')!r} (expected 'public').\n"
            f"Run `python3 scripts/shipped_flags.py` to inspect and fix.",
            file=sys.stderr,
        )
        sys.exit(1)

    flags_modified = False
    run_timestamp = datetime.now(timezone.utc).isoformat()
    session_id = f"demo-olist-{uuid.uuid4().hex[:12]}"

    try:
        print("=" * 72)
        print("StateSync Real-Corpus Demo (V2_STRATEGY §7)")
        print(f"Timestamp:  {run_timestamp}")
        print(f"Session ID: {session_id}")
        print("=" * 72)

        # ── 1. Preconditions ──────────────────────────────────────────────────
        print("\n[preconditions] Verifying prerequisites...")
        check_orchestrator_health()
        print("  ✓ Precondition 1: Orchestrator /health returned 200 OK.")

        check_api_token()
        print("  ✓ Precondition 2: StateSync API token resolved successfully.")

        verified_row_count = query_and_verify_olist_corpus()
        print(f"  ✓ Precondition 3: Olist corpus verified live: {verified_row_count:,} rows across tables.")

        # ── 2. Flip flag and restart orchestrator ──────────────────────────────
        print("\n[flag flip] Setting schema_retriever.target_schema = \"olist\"...")
        flipped_text = set_target_schema(original_flags_text, "olist")
        FLAGS_PATH.write_text(flipped_text)
        flags_modified = True
        print("    config/flags.toml: schema_retriever.target_schema = \"olist\" written.")

        restart_orchestrator()

        # Capture flag provenance while running under the deliberate target_schema=olist deviation
        flag_provenance = require_shipped_flags("demo_olist.py", allow_nonshipped=True)

        # ── 3. Run three-turn conversation ────────────────────────────────────
        print(f"\n[conversation] Running {len(CONVERSATION_TURNS)}-turn conversation...")
        print(f"  Timeout: {QUERY_TIMEOUT_S}s per turn (real corpus scans on 1.55M rows take time)")

        turns_data: list[dict[str, Any]] = []
        for turn_idx, question in enumerate(CONVERSATION_TURNS, start=1):
            print(f"\n--> Submitting Turn {turn_idx}/{len(CONVERSATION_TURNS)}: {question!r}...")
            t0 = time.monotonic()
            try:
                resp = send_query(ORCH_URL, question, session_id, timeout_s=QUERY_TIMEOUT_S)
                latency_ms = int((time.monotonic() - t0) * 1000)
                trace = resp.get("state_trace", {})

                rewritten_query = (
                    trace.get("rewritten_query")
                    or trace.get("normalized_query")
                    or question
                )
                rewrite_applied = bool(trace.get("rewrite_applied", False))
                route = trace.get("route")
                executed_sql = trace.get("executed_sql")
                sql_rows_count = trace.get(
                    "sql_rows_count",
                    len(trace.get("sql_rows", [])) if trace.get("sql_rows") is not None else 0,
                )
                answer_text = resp.get("answer_text", "")
                chart = resp.get("chart")

                chart_mark = None
                chart_data_count = 0
                if chart and isinstance(chart, dict):
                    mark_field = chart.get("mark")
                    if isinstance(mark_field, str):
                        chart_mark = mark_field
                    elif isinstance(mark_field, dict):
                        chart_mark = mark_field.get("type", str(mark_field))
                    values = chart.get("data", {}).get("values", [])
                    chart_data_count = len(values) if isinstance(values, list) else 0

                turn_record = {
                    "turn_index": turn_idx,
                    "question": question,
                    "rewritten_query": rewritten_query,
                    "rewrite_applied": rewrite_applied,
                    "route": route,
                    "executed_sql": executed_sql,
                    "row_count": sql_rows_count,
                    "answer_text": answer_text,
                    "chart": chart,
                    "chart_mark": chart_mark,
                    "chart_data_count": chart_data_count,
                    "latency_ms": latency_ms,
                    "ok": True,
                }
                turns_data.append(turn_record)

                # Format and print turn readable in terminal
                print(f"\n{'-'*72}")
                print(f"TURN {turn_idx}: \"{question}\"")
                print(f"{'-'*72}")
                print(f"  Rewritten query:  \"{rewritten_query}\"")
                print(f"  Rewrite applied:  {rewrite_applied}")
                print(f"  Route:            {route}")
                print(f"  Executed SQL:     {executed_sql}")
                print(f"  Row count:        {sql_rows_count}")
                print(f"  Answer text:      {answer_text}")
                if chart:
                    print(f"  Chart:            YES (Vega-Lite mark: {chart_mark!r}, {chart_data_count} data values)")
                else:
                    print("  Chart:            None")
                print(f"  Latency:          {latency_ms} ms")

            except Exception as exc:
                latency_ms = int((time.monotonic() - t0) * 1000)
                print(f"\n{'-'*72}")
                print(f"TURN {turn_idx}: \"{question}\" -> ERROR: {exc}")
                print(f"{'-'*72}")
                turn_record = {
                    "turn_index": turn_idx,
                    "question": question,
                    "rewritten_query": None,
                    "rewrite_applied": False,
                    "route": None,
                    "executed_sql": None,
                    "row_count": 0,
                    "answer_text": None,
                    "chart": None,
                    "chart_mark": None,
                    "chart_data_count": 0,
                    "latency_ms": latency_ms,
                    "ok": False,
                    "error": str(exc),
                }
                turns_data.append(turn_record)

        # ── 4. Assert and evaluate the §7 demo bar ─────────────────────────────
        clause_1_ok = len(turns_data) >= 3 and all(t["ok"] for t in turns_data)
        clause_1_detail = f"{sum(1 for t in turns_data if t['ok'])}/{len(turns_data)} turns completed without error"

        clause_2_ok = any(t["rewrite_applied"] for t in turns_data)
        rewritten_turns = [t["turn_index"] for t in turns_data if t["rewrite_applied"]]
        clause_2_detail = f"rewrite_applied=True on turn(s) {rewritten_turns}" if clause_2_ok else "No turns had rewrite_applied=True"

        clause_3_ok = len(turns_data) >= 3 and turns_data[-1]["chart"] is not None
        if clause_3_ok:
            c = turns_data[-1]
            clause_3_detail = f"Chart returned on turn {c['turn_index']} (mark: {c['chart_mark']!r}, values: {c['chart_data_count']})"
        else:
            clause_3_detail = "No chart returned on final turn"

        olist_sqls = [
            t["turn_index"] for t in turns_data
            if t["executed_sql"] and "olist." in t["executed_sql"].lower()
        ]
        clause_4_ok = bool(olist_sqls)
        clause_4_detail = f"Schema-qualified 'olist.' present in turn(s) {olist_sqls}" if clause_4_ok else "No executed SQL had 'olist.' schema qualification"

        clauses = [
            ("At least 3 turns completed without error", clause_1_ok, clause_1_detail),
            ("At least one turn had rewrite_applied=true (is a conversation)", clause_2_ok, clause_2_detail),
            ("Final turn returned a chart", clause_3_ok, clause_3_detail),
            ("At least one executed SQL is schema-qualified with 'olist.' (real corpus)", clause_4_ok, clause_4_detail),
        ]

        bar_passed = all(ok for _, ok, _ in clauses)

        print("\n" + "=" * 72)
        print("V2_STRATEGY §7 DEMO BAR EVALUATION")
        print("=" * 72)
        for name, ok, detail in clauses:
            status = "PASS" if ok else "FAIL"
            symbol = "✓" if ok else "✗"
            print(f"  {symbol} [{status}] {name}")
            print(f"         {detail}")

        if bar_passed:
            print("\n  OVERALL: DEMO BAR PASSED — All 4 clauses satisfied.")
        else:
            print("\n  OVERALL: DEMO BAR FAILED — One or more clauses did not meet the bar.", file=sys.stderr)

        # ── 5. Write transcript artifact ──────────────────────────────────────
        out_path.parent.mkdir(parents=True, exist_ok=True)
        transcript = {
            "_about": {
                "description": "V2_STRATEGY §7 demo transcript over real Olist corpus (1,551,698 rows)",
                "corpus": "olist",
                "verified_olist_row_count": verified_row_count,
                "timestamp": run_timestamp,
                "session_id": session_id,
                "bar_passed": bar_passed,
            },
            "deviation_note": (
                "This run deliberately pointed the agent at the olist corpus (schema_retriever.target_schema='olist') "
                "to demonstrate multi-turn query rewriting and chart generation over real data, and restored shipped "
                "state (target_schema='public') immediately afterwards."
            ),
            "flag_provenance": flag_provenance,
            "verified_olist_row_count": verified_row_count,
            "timestamp": run_timestamp,
            "session_id": session_id,
            "bar_evaluation": {
                "passed": bar_passed,
                "clauses": [
                    {"clause": name, "passed": ok, "detail": detail}
                    for name, ok, detail in clauses
                ],
            },
            "turns": [
                {
                    "turn_index": t["turn_index"],
                    "question": t["question"],
                    "rewritten_query": t["rewritten_query"],
                    "rewrite_applied": t["rewrite_applied"],
                    "route": t["route"],
                    "executed_sql": t["executed_sql"],
                    "row_count": t["row_count"],
                    "answer_text": t["answer_text"],
                    "chart": t["chart"],
                    "latency_ms": t["latency_ms"],
                }
                for t in turns_data
            ],
        }
        out_path.write_text(json.dumps(transcript, indent=2, ensure_ascii=False) + "\n")
        print(f"\nTranscript artifact written to: {out_path}")

        if not bar_passed:
            sys.exit(1)

    finally:
        # Restoration is mandatory covering normal exit, exception, and KeyboardInterrupt
        if args.keep_olist:
            print("\n" + "=" * 78, file=sys.stderr)
            print("WARNING: --keep-olist was passed. Shipped flag state is NOT restored!", file=sys.stderr)
            print("config/flags.toml currently has schema_retriever.target_schema = \"olist\".", file=sys.stderr)
            print("To restore shipped defaults, set target_schema = \"public\" in config/flags.toml and run:", file=sys.stderr)
            print("  docker compose restart orchestrator", file=sys.stderr)
            print("  python3 scripts/shipped_flags.py", file=sys.stderr)
            print("=" * 78 + "\n", file=sys.stderr)
        elif flags_modified or FLAGS_PATH.read_text() != original_flags_text:
            restore_flags_and_verify(original_flags_text)


if __name__ == "__main__":
    main()

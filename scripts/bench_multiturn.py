#!/usr/bin/env python3
"""
scripts/bench_multiturn.py
Cycle 007, Workstream A -- runs fixtures/golden/multi_turn.json (12
conversations, 36 turns) through the live orchestrator, one real HTTP
session per conversation (a fresh session_id, turns posted IN ORDER so
rewrite_node has real history to read), and reports:

  QM  (question match)        -- % of individual turns correct
  IM  (interaction match)     -- % of conversations where EVERY turn is correct
  reference resolution rate   -- of turns with needs_context=true in the
                                  fixture, % where the system set
                                  rewrite_applied=true (detected context was
                                  needed at all -- coarser than fidelity)
  rewrite fidelity            -- of turns with needs_context=true, % whose
                                  rewritten_query preserves every fact in
                                  expected_standalone_facts (substring,
                                  case-insensitive). Scored WITHOUT looking
                                  at whether the turn's SQL was correct --
                                  V2_STRATEGY §3b's rule: a well-formed
                                  answer to a question nobody asked must
                                  fail fidelity even if downstream QM passes.
  error propagation rate      -- given a QM-wrong turn N whose successor
                                  needs_context=true, % of the time turn N+1
                                  is ALSO QM-wrong
  guess rate                  -- of turns with needs_context=true, % where
                                  the system reported context_guessed=true
                                  (A5's cut-line: ship without clarification,
                                  record the guess instead)

QM is never reported without IM (§3, binding). Both are reported from TWO
full runs (docs/cycles/007.md C5) -- audit 007 F-007-10 found the sibling
harness (bench_multilingual.py) moves 7.5pp between runs of an unchanged
build on this same local 7B, so a single run here would be exactly the
un-repeated-single-run pattern that finding was about.

QM's dataframe-equality definition reuses bench_multilingual.py's ALIAS-
INSENSITIVE, value-only comparison deliberately, not the stricter
column-aware one in check_consistency.py -- audit 007 F-007-3 found the
loose definition is the CORRECT one when ground truth and generated SQL are
independently authored (this fixture's SQL vs the model's own aliases), not
a defect; re-scoring the shipped build under the strict definition would
manufacture false failures from pure column-naming differences. Reused with
that finding cited, not chosen blind.

Usage:
    python3 scripts/bench_multiturn.py --out <path>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

from shipped_flags import (require_shipped_flags, add_flag_guard_argument,  # noqa: E402
                           FlagStateError)
from config_loader import cfg  # noqa: E402

FIXTURE_PATH = REPO_ROOT / "fixtures" / "golden" / "multi_turn.json"
DEFAULT_URL = "http://localhost:8090"
MCP_URL = "http://localhost:8080/rpc"
TENANT = "t_capstone_default"
QUERY_TIMEOUT_S = float(cfg.evaluation.query_timeout_s)  # same constant run_e2_v1.py uses


def run_query(base_url: str, text: str, session_id: str, timeout_s: float = QUERY_TIMEOUT_S) -> dict:
    req = urllib.request.Request(
        f"{base_url}/query",
        data=json.dumps({"query": text, "session_id": session_id}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read())


def mcp_run_select(sql: str) -> dict:
    req = urllib.request.Request(
        MCP_URL,
        data=json.dumps({"jsonrpc": "2.0", "id": "1", "method": "data.run_select",
                         "params": {"sql": sql}}).encode(),
        headers={"Content-Type": "application/json", "X-Tenant-ID": TENANT,
                 "X-Session-ID": "bench-multiturn-ground-truth"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read()).get("result", {})


def _round_floats(v: Any) -> Any:
    """Found live in run 1 (docs/cycles/007.md DEVIATION 4): this fixture's
    ground truth rounds AVG-style aggregates to 2dp (ROUND(...,2)) so the
    reference is readable; the model's generated SQL has no reason to round
    at all, and Postgres returns full precision (19.9903199289835775 vs the
    ground truth's 19.99) -- same underlying answer, different JSON string,
    scored as a false failure under exact comparison. Same class of gap as
    F-007-3's alias-insensitivity, applied to numeric precision instead of
    column names: legitimate immaterial difference between independently
    produced SQL, not a defect in either query."""
    if isinstance(v, float):
        return round(v, 2)
    return v


def _sig(columns: list[str], rows: list[dict]) -> str:
    """bench_multilingual.py's _signature (audit 007 F-007-3 cites the
    reasoning for reusing this alias-insensitive definition), extended with
    _round_floats -- see its docstring."""
    row_tuples = [
        tuple(sorted(json.dumps(_round_floats(v), sort_keys=True, default=str) for v in row.values()))
        for row in rows
    ]
    return json.dumps({"n_columns": len(columns), "rows": sorted(row_tuples)}, default=str)


def compute_ground_truth(conversations: list[dict]) -> dict[str, dict]:
    print("[ground truth] Executing ground_truth_sql live for each SQL turn...")
    gt: dict[str, dict] = {}
    for c in conversations:
        for t in c["turns"]:
            if t.get("ground_truth_sql") is None:
                continue
            r = mcp_run_select(t["ground_truth_sql"])
            if not r.get("ok"):
                print(f"  [{t['turn_id']}] GROUND TRUTH SQL FAILED: {r.get('error')}")
                gt[t["turn_id"]] = {"ok": False}
                continue
            gt[t["turn_id"]] = {"ok": True, "signature": _sig(r["columns"], r["rows"])}
    print(f"  computed ground truth for {len(gt)} SQL turns.")
    return gt


def score_turn(t: dict, resp: dict, gt: dict[str, dict]) -> dict:
    trace = resp.get("state_trace", {})
    result: dict[str, Any] = {
        "turn_id": t["turn_id"], "route": trace.get("route"),
        "rewrite_applied": bool(trace.get("rewrite_applied")),
        "context_guessed": bool(trace.get("context_guessed")),
        "normalized_query": trace.get("normalized_query"),
    }

    if t["expected_route"] == "docs":
        # mt_06 T1: correctness = the ground truth value appears in the
        # answer text, AND at least one expected chunk was cited (E1-V2
        # style -- both signals, neither alone).
        answer = (resp.get("answer_text") or "").lower()
        value_present = str(t.get("ground_truth_value", "")).lower() in answer
        cited = {c.get("ref") for c in resp.get("citations", []) if c.get("kind") == "chunk"}
        expected = set(t.get("expected_chunk_ids") or [])
        chunk_present = bool(expected & cited)
        result["qm_correct"] = bool(value_present and chunk_present)
        result["detail"] = {"value_present": value_present, "chunk_present": chunk_present,
                            "cited": sorted(cited)}
    else:
        g = gt.get(t["turn_id"], {})
        gen_sig = _sig(trace.get("sql_columns", []), trace.get("sql_rows", []))
        result["qm_correct"] = bool(g.get("ok") and gen_sig == g["signature"])
        result["executed_sql"] = trace.get("executed_sql")

    # Rewrite fidelity (C6) -- independent of qm_correct, per this file's docstring.
    if t["needs_context"]:
        facts = t.get("expected_standalone_facts") or []
        nq = (trace.get("normalized_query") or "").lower()
        result["rewrite_fidelity"] = bool(facts) and all(f.lower() in nq for f in facts)
    else:
        result["rewrite_fidelity"] = None  # not applicable

    return result


def run_conversation(base_url: str, c: dict, gt: dict[str, dict]) -> list[dict]:
    session_id = f"mt-bench-{c['conversation_id']}-{uuid.uuid4().hex[:8]}"
    print(f"\n--- {c['conversation_id']} (session={session_id}) ---")
    turn_results = []
    for t in c["turns"]:
        try:
            resp = run_query(base_url, t["text"], session_id)
        except Exception as exc:
            print(f"  [{t['turn_id']}] ERROR: {exc}")
            # Same key set as the success path (score_turn's return) --
            # summarize() reads rewrite_applied/context_guessed
            # unconditionally on every result. A transport failure (timeout,
            # connection reset) is scored as incorrect and as "no rewrite
            # detected", not specially exempted from either rate.
            turn_results.append({
                "turn_id": t["turn_id"], "route": None,
                "rewrite_applied": False, "context_guessed": False,
                "normalized_query": None, "qm_correct": False,
                "rewrite_fidelity": None, "error": str(exc),
            })
            continue
        r = score_turn(t, resp, gt)
        turn_results.append(r)
        print(f"  [{t['turn_id']}] needs_context={t['needs_context']!s:<5} "
              f"rewrite_applied={r['rewrite_applied']!s:<5} qm_correct={r['qm_correct']!s:<5} "
              f"fidelity={r['rewrite_fidelity']} text={t['text'][:50]}")
    return turn_results


def summarize(conversations: list[dict], all_results: dict[str, list[dict]]) -> dict:
    all_turns = [r for rs in all_results.values() for r in rs]
    n_turns = len(all_turns)
    n_correct = sum(1 for r in all_turns if r["qm_correct"])
    qm = n_correct / n_turns if n_turns else 0.0

    n_convo = len(conversations)
    n_convo_correct = sum(
        1 for c in conversations
        if all(r["qm_correct"] for r in all_results[c["conversation_id"]])
    )
    im = n_convo_correct / n_convo if n_convo else 0.0

    needs_ctx_turns = []
    for c in conversations:
        turns_by_id = {t["turn_id"]: t for t in c["turns"]}
        for r in all_results[c["conversation_id"]]:
            t = turns_by_id.get(r["turn_id"])
            if t and t["needs_context"]:
                needs_ctx_turns.append((t, r))

    n_needs_ctx = len(needs_ctx_turns)
    n_detected = sum(1 for t, r in needs_ctx_turns if r["rewrite_applied"])
    reference_resolution_rate = n_detected / n_needs_ctx if n_needs_ctx else None

    fidelity_scored = [r["rewrite_fidelity"] for t, r in needs_ctx_turns if r["rewrite_fidelity"] is not None]
    rewrite_fidelity_rate = (sum(fidelity_scored) / len(fidelity_scored)) if fidelity_scored else None

    n_guessed = sum(1 for t, r in needs_ctx_turns if r["context_guessed"])
    guess_rate = n_guessed / n_needs_ctx if n_needs_ctx else None
    guessed_but_correct = sum(1 for t, r in needs_ctx_turns if r["context_guessed"] and r["qm_correct"])

    # Error propagation: turn N wrong, turn N+1 needs_context=true -> is N+1 also wrong?
    propagation_opportunities = 0
    propagated = 0
    for c in conversations:
        results = all_results[c["conversation_id"]]
        turns_by_id = {t["turn_id"]: t for t in c["turns"]}
        for i in range(len(results) - 1):
            cur, nxt = results[i], results[i + 1]
            nxt_turn = turns_by_id.get(nxt["turn_id"])
            if not cur["qm_correct"] and nxt_turn and nxt_turn["needs_context"]:
                propagation_opportunities += 1
                if not nxt["qm_correct"]:
                    propagated += 1
    error_propagation_rate = (propagated / propagation_opportunities) if propagation_opportunities else None

    return {
        "qm": {"correct": n_correct, "total": n_turns, "rate": qm},
        "im": {"correct": n_convo_correct, "total": n_convo, "rate": im},
        "reference_resolution_rate": {
            "detected": n_detected, "total": n_needs_ctx, "rate": reference_resolution_rate,
        },
        "rewrite_fidelity_rate": {
            "correct": sum(fidelity_scored), "total": len(fidelity_scored), "rate": rewrite_fidelity_rate,
        },
        "guess_rate": {
            "guessed": n_guessed, "total": n_needs_ctx, "rate": guess_rate,
            "guessed_but_qm_correct": guessed_but_correct,
        },
        "error_propagation_rate": {
            "propagated": propagated, "opportunities": propagation_opportunities,
            "rate": error_propagation_rate,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True,
                    help="Where to write the report. Refuses to overwrite (append-only).")
    ap.add_argument("--url", default=DEFAULT_URL)
    add_flag_guard_argument(ap)
    args = ap.parse_args()

    try:
        flag_provenance = require_shipped_flags(
            "bench_multiturn.py (Workstream A: QM/IM/reference-resolution/fidelity/guess-rate)",
            allow_nonshipped=args.allow_nonshipped_flags, fixture=FIXTURE_PATH)
    except FlagStateError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.out.exists():
        print(f"ERROR: {args.out} exists. Results are append-only -- choose a new --out.")
        return 1

    fixture = json.loads(FIXTURE_PATH.read_text())
    conversations = fixture["conversations"]

    gt = compute_ground_truth(conversations)

    t0 = time.monotonic()
    all_results: dict[str, list[dict]] = {}
    for c in conversations:
        all_results[c["conversation_id"]] = run_conversation(args.url, c, gt)
    elapsed_s = time.monotonic() - t0

    summary = summarize(conversations, all_results)

    print("\n=== Summary ===")
    print(f"  QM (question match):           {summary['qm']['correct']}/{summary['qm']['total']} "
          f"({summary['qm']['rate']:.1%})")
    print(f"  IM (interaction match):        {summary['im']['correct']}/{summary['im']['total']} "
          f"({summary['im']['rate']:.1%})")
    rr = summary["reference_resolution_rate"]
    print(f"  Reference resolution rate:     {rr['detected']}/{rr['total']} "
          f"({rr['rate']:.1%})" if rr["rate"] is not None else "  Reference resolution rate:     n/a")
    fr = summary["rewrite_fidelity_rate"]
    print(f"  Rewrite fidelity rate:         {fr['correct']}/{fr['total']} "
          f"({fr['rate']:.1%})" if fr["rate"] is not None else "  Rewrite fidelity rate:         n/a")
    gr = summary["guess_rate"]
    print(f"  Guess rate:                    {gr['guessed']}/{gr['total']} "
          f"({gr['rate']:.1%}), {gr['guessed_but_qm_correct']} of those still QM-correct"
          if gr["rate"] is not None else "  Guess rate:                    n/a")
    ep = summary["error_propagation_rate"]
    print(f"  Error propagation rate:        {ep['propagated']}/{ep['opportunities']} "
          f"({ep['rate']:.1%})" if ep["rate"] is not None else
          "  Error propagation rate:        n/a (no wrong-turn-followed-by-dependent-turn cases)")
    print(f"  Wall clock:                    {elapsed_s:.0f}s")

    report = {
        "flag_provenance": flag_provenance,
        "elapsed_s": elapsed_s,
        "summary": summary,
        "per_conversation": all_results,
    }
    args.out.write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False))
    print(f"\nReport written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

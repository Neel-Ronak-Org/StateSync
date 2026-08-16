#!/usr/bin/env python3
"""
scripts/bench_multilingual.py
Phase D Deliverable 9 -- runs fixtures/golden/phase_d_multilingual.json (40
queries) through the orchestrator under both normalization.enabled states
(control/treatment, D13), plus the 20 "hi"-group english_equivalent texts as
an upper reference line and as D-V3's both-flag-states data.

This is the data source for:
  D-V1 (M3): control vs treatment accuracy on the 40 native-language queries.
  D-V2 (M3, second half): entity preservation rate on the >=15 proper-noun
    queries, scored from the treatment pass's actual preserved_entities and
    generated SQL -- a query whose entity was translated fails this check
    even if its result set matched ground truth (scored independently of
    D-V1's correctness check, per spec).
  D-V3: english_equivalent texts run under both flag states.

Ground truth is computed once per query id (fresh, direct-mcp execution,
bypassing the LLM) and reused across every pass and every language variant
of the same question -- the underlying answer does not depend on which
language asked it. Comparison is execution-based dataframe equality,
value-only (column names/order ignored), same definition as C1-V3/C2-V8.

Usage:
    python3 scripts/bench_multilingual.py
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from bench_memory import REPO_ROOT, restart_orchestrator, run_query

FIXTURE_PATH = REPO_ROOT / "fixtures" / "golden" / "phase_d_multilingual.json"
FLAGS_PATH = REPO_ROOT / "config" / "flags.toml"
REPORT_PATH = REPO_ROOT / "docs" / "bench_multilingual_report.json"

MCP_URL = "http://localhost:8080"
TENANT = "t_capstone_default"

_NORM_ENABLED_RE = re.compile(r"(\[normalization\]\s*\n\s*enabled\s*=\s*)(true|false)")


def set_normalization_enabled(value: bool) -> None:
    text = FLAGS_PATH.read_text()
    new_text, count = _NORM_ENABLED_RE.subn(rf"\g<1>{'true' if value else 'false'}", text)
    if count != 1:
        raise RuntimeError(
            "Could not find exactly one [normalization] enabled = ... line in "
            f"{FLAGS_PATH} (found {count}). Refusing to guess -- edit it by hand."
        )
    FLAGS_PATH.write_text(new_text)
    print(f"    config/flags.toml: normalization.enabled = {value}")


def mcp_run_select(sql: str) -> dict:
    payload = json.dumps({
        "jsonrpc": "2.0", "id": "1", "method": "data.run_select", "params": {"sql": sql},
    })
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", f"{MCP_URL}/rpc",
         "-H", "Content-Type: application/json",
         "-H", f"X-Tenant-ID: {TENANT}",
         "-H", "X-Session-ID: bench-multilingual-ground-truth",
         "-d", payload],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)["result"]


def _signature(sql_columns: list[str], sql_rows: list[dict]) -> str:
    """Execution-based dataframe equality, value-only (C1-V3/C2-V8 definition):
    ground_truth_sql and the generated SQL are independently authored, so
    column aliases legitimately differ even when the data is identical."""
    row_tuples = [
        tuple(sorted(json.dumps(v, sort_keys=True, default=str) for v in row.values()))
        for row in sql_rows
    ]
    return json.dumps({"n_columns": len(sql_columns), "rows": sorted(row_tuples)}, default=str)


def compute_ground_truth(queries: list[dict]) -> dict[str, dict]:
    print("[ground truth] Executing ground_truth_sql live for each query id...")
    ground_truth: dict[str, dict] = {}
    for q in queries:
        r = mcp_run_select(q["ground_truth_sql"])
        if not r.get("ok"):
            print(f"  [{q['id']}] GROUND TRUTH SQL FAILED: {r.get('error')}")
            ground_truth[q["id"]] = {"ok": False}
            continue
        ground_truth[q["id"]] = {
            "ok": True,
            "signature": _signature(r["columns"], r["rows"]),
            "row_count": r["row_count"],
        }
    print(f"  computed ground truth for {len(ground_truth)} queries.")
    return ground_truth


def run_pass(label: str, items: list[tuple[str, str, str]], ground_truth: dict[str, dict]) -> dict[str, dict]:
    """items: list of (result_key, base_id, text) to submit. result_key may
    differ from base_id (english-equivalent variants use id + '_en'), but
    both are scored against base_id's ground truth."""
    print(f"\n--- {label} ({len(items)} queries) ---")
    results: dict[str, dict] = {}
    for result_key, base_id, text in items:
        gt = ground_truth.get(base_id)
        try:
            data = run_query(text)
        except Exception as exc:
            print(f"  [{result_key}] ERROR: {exc}")
            results[result_key] = {"ok": False, "match": False, "error": str(exc)}
            continue
        trace = data.get("state_trace", {})
        sig = _signature(trace.get("sql_columns", []), trace.get("sql_rows", []))
        match = bool(gt and gt.get("ok") and sig == gt["signature"])
        results[result_key] = {
            "ok": True,
            "match": match,
            "detected_language": trace.get("detected_language"),
            "normalized_query": trace.get("normalized_query"),
            "preserved_entities": trace.get("preserved_entities", {}),
            "executed_sql": trace.get("executed_sql"),
            "candidate_sql": trace.get("candidate_sql"),
            "row_count": trace.get("sql_rows_count", 0),
        }
        print(f"  [{result_key:>12}] match={match!s:<5} lang={trace.get('detected_language')} "
              f"rows={trace.get('sql_rows_count')} text={text[:55]}")
    return results


def accuracy(results: dict[str, dict]) -> tuple[int, int, float]:
    scored = [r for r in results.values() if r.get("ok")]
    correct = sum(1 for r in scored if r["match"])
    total = len(scored)
    return correct, total, (correct / total if total else 0.0)


def score_entity_preservation(treatment_results: dict[str, dict], queries_by_id: dict[str, dict]) -> dict:
    print("\n--- D-V2: entity preservation (treatment pass, native-language queries) ---")
    per_query: dict[str, dict] = {}
    preserved_count = 0
    scored_count = 0
    for qid, q in queries_by_id.items():
        proper_nouns = q.get("proper_nouns") or []
        if not proper_nouns:
            continue
        scored_count += 1
        r = treatment_results.get(qid, {})
        preserved_entities = r.get("preserved_entities", {}) or {}
        sql_text = (r.get("executed_sql") or r.get("candidate_sql") or "")

        all_preserved = True
        detail = []
        for noun in proper_nouns:
            in_map = preserved_entities.get(noun) == noun
            in_sql = noun in sql_text
            ok = in_map and in_sql
            all_preserved = all_preserved and ok
            detail.append({"noun": noun, "in_preserved_entities": in_map, "in_generated_sql": in_sql})

        per_query[qid] = {"all_preserved": all_preserved, "detail": detail}
        if all_preserved:
            preserved_count += 1
        print(f"  [{qid}] preserved={all_preserved} nouns={proper_nouns}")

    rate = preserved_count / scored_count if scored_count else 0.0
    print(f"\n  Entity preservation rate: {preserved_count}/{scored_count} ({rate:.1%})")
    return {"preserved": preserved_count, "scored": scored_count, "rate": rate, "detail": per_query}


def score_english_untouched(control_en: dict[str, dict], treatment_en: dict[str, dict],
                             en_items: list[tuple[str, str, str]]) -> dict:
    print("\n--- D-V3: English untouched (both flag states) ---")
    text_by_key = {key: text for key, _, text in en_items}
    all_unchanged = True
    all_identical = True
    detail = {}
    for key, text in text_by_key.items():
        t = treatment_en.get(key, {})
        c = control_en.get(key, {})
        unchanged = t.get("normalized_query") == text
        same_result = (t.get("row_count") == c.get("row_count"))  # coarse cross-check; match already scored per-pass
        all_unchanged = all_unchanged and unchanged
        detail[key] = {"normalized_query_unchanged": unchanged, "control_rows": c.get("row_count"),
                        "treatment_rows": t.get("row_count")}
        print(f"  [{key}] normalized_query_unchanged={unchanged} "
              f"control_rows={c.get('row_count')} treatment_rows={t.get('row_count')}")
    print(f"\n  All 20 unchanged under treatment: {all_unchanged}")
    return {"all_unchanged": all_unchanged, "detail": detail}


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
    _args, _ = _ap.parse_known_args()
    out_path = Path(_args.out)
    if out_path != REPORT_PATH and out_path.exists():
        print(f"ERROR: {out_path} exists. Verification results are append-only "
              f"-- choose a new --out path.")
        raise SystemExit(1)

    print("=== bench_multilingual.py -- Phase D M3 benchmark (D-V1/D-V2/D-V3 data source) ===")
    fixture = json.loads(FIXTURE_PATH.read_text())
    queries = fixture["queries"]
    queries_by_id = {q["id"]: q for q in queries}
    hi_queries = [q for q in queries if q["lang"] == "hi"]
    assert len(hi_queries) == 20

    ground_truth = compute_ground_truth(queries)

    native_items = [(q["id"], q["id"], q["text"]) for q in queries]
    en_items = [(f"{q['id']}_en", q["id"], q["english_equivalent"]) for q in hi_queries]
    all_items = native_items + en_items

    print("\n[1/2] Control pass: normalization.enabled = false")
    set_normalization_enabled(False)
    restart_orchestrator()
    control_results = run_pass("Control (normalization disabled)", all_items, ground_truth)

    print("\n[2/2] Treatment pass: normalization.enabled = true")
    set_normalization_enabled(True)
    restart_orchestrator()
    treatment_results = run_pass("Treatment (normalization enabled)", all_items, ground_truth)

    # D-V1: native-language (40) accuracy, control vs treatment.
    control_native = {k: v for k, v in control_results.items() if k in queries_by_id}
    treatment_native = {k: v for k, v in treatment_results.items() if k in queries_by_id}
    c_correct, c_total, c_acc = accuracy(control_native)
    t_correct, t_total, t_acc = accuracy(treatment_native)

    # English reference line: the 20 english_equivalent texts, treatment pass.
    treatment_en = {k: v for k, v in treatment_results.items() if k.endswith("_en")}
    control_en = {k: v for k, v in control_results.items() if k.endswith("_en")}
    e_correct, e_total, e_acc = accuracy(treatment_en)

    dv2 = score_entity_preservation(treatment_native, queries_by_id)
    dv3 = score_english_untouched(control_en, treatment_en, en_items)

    print("\n=== D-V1 (M3): control vs treatment accuracy ===")
    print(f"  Control   (normalization=false): {c_correct}/{c_total} ({c_acc:.1%})")
    print(f"  Treatment (normalization=true):  {t_correct}/{t_total} ({t_acc:.1%})")
    print(f"  Delta: {(t_acc - c_acc) * 100:+.1f} pp")
    print(f"  English reference line:          {e_correct}/{e_total} ({e_acc:.1%})")

    report = {
        "dv1_control": {"correct": c_correct, "total": c_total, "accuracy": c_acc},
        "dv1_treatment": {"correct": t_correct, "total": t_total, "accuracy": t_acc},
        "dv1_delta_pp": (t_acc - c_acc) * 100,
        "english_reference": {"correct": e_correct, "total": e_total, "accuracy": e_acc},
        "dv2_entity_preservation": dv2,
        "dv3_english_untouched": dv3,
        "control_detail": control_results,
        "treatment_detail": treatment_results,
    }
    out_path.write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False))
    print(f"\nReport written to {REPORT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()

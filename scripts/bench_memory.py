#!/usr/bin/env python3
"""
scripts/bench_memory.py
C1 Deliverable 7 -- runs fixtures/golden/phase_c1.json (30 queries) twice,
once with memory.enabled=false (control) and once with memory.enabled=true
(treatment), recording tokens_in, tokens_out, llm_calls, trajectory_steps,
and memory_hit per query. Emits a JSON metrics report.

This is also the data source for C1-V1 (M1) and C1-V2: both read from a
single run of this script rather than re-running the fixture again, since
the procedure ("run the 30-query set under both flag states") is identical.

flags.toml is loaded once per orchestrator process (A2: flags_snapshot is
captured at query entry and never re-read mid-query -- this is what makes
the control/treatment comparison valid at all), so toggling memory.enabled
requires restarting the orchestrator container, not just editing the file.
qdrant is unreachable from the host (A1 network isolation), so resetting
the trajectory collection shells out to a one-off `docker compose run` on
the mcp image, the same pattern used to compute the schema fingerprint by
hand during C1 development.

Usage:
    python3 scripts/bench_memory.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FLAGS_PATH = REPO_ROOT / "config" / "flags.toml"
FIXTURE_PATH = REPO_ROOT / "fixtures" / "golden" / "phase_c1.json"
REPORT_PATH = REPO_ROOT / "docs" / "bench_memory_report.json"

ORCH_URL = "http://localhost:8090"
QUERY_TIMEOUT_S = 90
HEALTH_POLL_ATTEMPTS = 60
HEALTH_POLL_INTERVAL_S = 2

_ENABLED_RE = re.compile(r"(\[memory\]\s*\n\s*enabled\s*=\s*)(true|false)")


def set_memory_enabled(value: bool) -> None:
    text = FLAGS_PATH.read_text()
    new_text, count = _ENABLED_RE.subn(rf"\g<1>{'true' if value else 'false'}", text)
    if count != 1:
        raise RuntimeError(
            "Could not find exactly one [memory] enabled = ... line in "
            f"{FLAGS_PATH} (found {count}). Refusing to guess -- edit it by hand."
        )
    FLAGS_PATH.write_text(new_text)
    print(f"    config/flags.toml: memory.enabled = {value}")


def restart_orchestrator() -> None:
    print("    Restarting orchestrator to pick up the new flag value...")
    subprocess.run(
        ["docker", "compose", "restart", "orchestrator"],
        check=True, cwd=REPO_ROOT, capture_output=True,
    )
    for _ in range(HEALTH_POLL_ATTEMPTS):
        try:
            with urllib.request.urlopen(f"{ORCH_URL}/health", timeout=3) as resp:
                if resp.status == 200:
                    print("    orchestrator healthy.")
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(HEALTH_POLL_INTERVAL_S)
    raise RuntimeError("orchestrator did not become healthy after restart.")


def reset_trajectory_collection() -> None:
    print("    Resetting agentsm_trajectories collection...")
    snippet = (
        "import asyncio\n"
        "from qdrant_store import init_qdrant, get_qdrant_client, close_qdrant\n"
        "from config_loader import cfg\n"
        "async def main():\n"
        "    await init_qdrant()\n"
        "    client = get_qdrant_client()\n"
        "    await client.delete_collection(cfg.memory.collection)\n"
        "    await init_qdrant()\n"
        "    await close_qdrant()\n"
        "asyncio.run(main())\n"
    )
    subprocess.run(
        ["docker", "compose", "run", "--rm", "--no-deps", "mcp", "python", "-c", snippet],
        check=True, cwd=REPO_ROOT, capture_output=True,
    )


def run_query(text: str, debug_candidate_sql: str | None = None) -> dict:
    body: dict = {"query": text}
    if debug_candidate_sql is not None:
        body["debug_candidate_sql"] = debug_candidate_sql
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{ORCH_URL}/query", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=QUERY_TIMEOUT_S) as resp:
        return json.loads(resp.read())


def run_fixture_set() -> dict[str, dict]:
    fixture = json.loads(FIXTURE_PATH.read_text())
    results: dict[str, dict] = {}
    for q in fixture["queries"]:
        try:
            data = run_query(q["text"])
            trace = data.get("state_trace", {})
            metrics = trace.get("metrics", {})
            row = {
                "group": q["group"],
                "base_id": q.get("base_id"),
                "ok": True,
                "tokens_in": metrics.get("tokens_in", 0),
                "tokens_out": metrics.get("tokens_out", 0),
                "llm_calls": metrics.get("llm_calls", 0),
                "trajectory_steps": metrics.get("trajectory_steps", 0),
                "latency_ms": metrics.get("latency_ms", 0),
                "memory_hit": bool(trace.get("memory_hit", False)),
                "row_count": trace.get("sql_rows_count", 0),
            }
            results[q["id"]] = row
            print(
                f"    [{q['id']:>20}] hit={row['memory_hit']!s:<5} "
                f"tokens={row['tokens_in']:>5}+{row['tokens_out']:<4} "
                f"steps={row['trajectory_steps']:>3} llm_calls={row['llm_calls']}"
            )
        except Exception as exc:
            print(f"    [{q['id']:>20}] ERROR: {exc}")
            results[q["id"]] = {
                "group": q["group"], "base_id": q.get("base_id"),
                "ok": False, "error": str(exc),
            }
    return results


def summarize(results: dict[str, dict]) -> dict:
    scored = [r for r in results.values() if r.get("ok") and r["group"] in ("repeat", "paraphrase")]
    paraphrases = [r for r in results.values() if r.get("ok") and r["group"] == "paraphrase"]
    return {
        "queries_scored": len(scored),
        "total_tokens": sum(r["tokens_in"] + r["tokens_out"] for r in scored),
        "total_tokens_in": sum(r["tokens_in"] for r in scored),
        "total_tokens_out": sum(r["tokens_out"] for r in scored),
        "total_llm_calls": sum(r["llm_calls"] for r in scored),
        "total_trajectory_steps": sum(r["trajectory_steps"] for r in scored),
        "memory_hits_repeat_and_paraphrase": sum(1 for r in scored if r["memory_hit"]),
        "paraphrase_hits": sum(1 for r in paraphrases if r["memory_hit"]),
        "paraphrase_count": len(paraphrases),
    }


def pct_reduction(control: int, treatment: int) -> float | None:
    if control == 0:
        return None
    return round(100 * (control - treatment) / control, 1)


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

    print("=== bench_memory.py -- C1 memory efficiency benchmark (M1/M2 data source) ===")

    print("\n[1/4] Control run: memory.enabled = false")
    set_memory_enabled(False)
    restart_orchestrator()
    reset_trajectory_collection()
    control_results = run_fixture_set()

    print("\n[2/4] Resetting trajectory collection before treatment run")
    reset_trajectory_collection()

    print("\n[3/4] Treatment run: memory.enabled = true")
    set_memory_enabled(True)
    restart_orchestrator()
    treatment_results = run_fixture_set()

    print("\n[4/4] Summarizing")
    control_summary = summarize(control_results)
    treatment_summary = summarize(treatment_results)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fixture": str(FIXTURE_PATH.relative_to(REPO_ROOT)),
        "comparison_scope": (
            "repeat + paraphrase groups only (20 of 30 queries); the base "
            "group is run in both flag states but excluded from the "
            "comparison, per C1-V1 -- it is cache-cold in both runs."
        ),
        "control": {
            "flag": "memory.enabled=false",
            "per_query": control_results,
            "summary": control_summary,
        },
        "treatment": {
            "flag": "memory.enabled=true",
            "per_query": treatment_results,
            "summary": treatment_summary,
        },
        "delta": {
            "total_tokens_reduction": control_summary["total_tokens"] - treatment_summary["total_tokens"],
            "total_tokens_reduction_pct": pct_reduction(
                control_summary["total_tokens"], treatment_summary["total_tokens"],
            ),
            "total_trajectory_steps_reduction": (
                control_summary["total_trajectory_steps"] - treatment_summary["total_trajectory_steps"]
            ),
            "total_trajectory_steps_reduction_pct": pct_reduction(
                control_summary["total_trajectory_steps"], treatment_summary["total_trajectory_steps"],
            ),
            "paraphrase_hit_count": treatment_summary["paraphrase_hits"],
            "paraphrase_hit_count_of": treatment_summary["paraphrase_count"],
        },
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nReport written to {REPORT_PATH.relative_to(REPO_ROOT)}")
    print(json.dumps(report["delta"], indent=2))

    if report["delta"]["total_tokens_reduction"] <= 0:
        print("\nC1-V1 WARNING: total token reduction is zero or negative -- record honestly, this is a fail.", file=sys.stderr)
    if report["delta"]["total_trajectory_steps_reduction"] <= 0:
        print("\nC1-V1 WARNING: total trajectory_steps reduction is zero or negative -- record honestly, this is a fail.", file=sys.stderr)
    if report["delta"]["paraphrase_hit_count"] < 6:
        print(
            f"\nC1-V2 WARNING: only {report['delta']['paraphrase_hit_count']}/10 paraphrases hit "
            "(< 6 required) -- record honestly, this is a fail.", file=sys.stderr,
        )


if __name__ == "__main__":
    main()

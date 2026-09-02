#!/usr/bin/env python3
"""
scripts/experiment_tool_ceiling.py
F-V2 — Routing determinism (M9) + the tool-ceiling over-exposure experiment.

Two measurements:

1. BASELINE (the actual F-V2 pass condition, >= 90%). Submits the full
   60-query fixed routing set (fixtures/golden/phase_f_routing.json,
   Appendix 2.2) through the LIVE orchestrator /query endpoint -- the real,
   unmodified, production Supervisor -- and compares state_trace.route
   against each query's expected_route. Single pass; this is what gets
   reported as the M9 number.

2. OVER-EXPOSURE EXPERIMENT. Repeats routing at surfaces of
   experiment.tool_ceiling_surfaces (9, 15, 25, 40) by injecting that many
   TOTAL capability descriptions (3 real routes + N decoys) into a
   Supervisor-style classification prompt, run in a dedicated harness that
   calls get_agent_llm("supervisor") directly -- NEVER against the live
   /query endpoint, so the frozen build's own Supervisor prompt is never
   touched (F2 Notes: "the experiment is run in a dedicated harness, never
   against the frozen build").

Why decoys are injected into the SUPERVISOR'S OWN PROMPT rather than
registered as MCP JSON-RPC methods, even though F-V2's surface numbers
(9, 15, 25, 40) match mcp.tool_ceiling's own scale (9 = current registered
tool count, 15 = the ceiling): M9 measures SUPERVISOR ROUTING accuracy, and
D1 deliberately decoupled the Supervisor's 3-route classification from MCP's
tool list entirely -- the Supervisor's system prompt (orchestrator/nodes/
supervisor.py) never mentions a single MCP tool name. Registering decoy MCP
methods would move mcp/main.py's _REGISTERED_TOOLS count with no mechanism
by which that could affect what the Supervisor's LLM call sees or how it
routes. The surface that can actually move M9 is the Supervisor's own
classification prompt, so that is what is crowded here, reusing the spec's
own 9/15/25/40 scale as the total-capability-count target. Recorded as
GAP-7 in docs/spec-gaps.md rather than silently assumed.

Decoy capability descriptions are drawn partly from Strategy Document §6's
own deferred-scope items (a fitting source: several were literally rejected
as peer routing destinations by D1 -- "geocoding" chief among them) plus
invented but plausible logistics-analytics specialist names. None can be
confused with sql/docs/hybrid; they are inert by construction (the parser
only ever accepts sql/docs/hybrid as a valid route, per _extract_route).

Runs per level and the query subsample are configured, not hardcoded (A0.2)
-- see config/statesync.toml [experiment]. The subsample is a stratified
4-per-route set, not the full 60, for local-model runtime; see this script's
own docstring note above and docs/phase_f_log.md for the reasoning.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

from shipped_flags import (require_shipped_flags, add_flag_guard_argument,
                           FlagStateError)
from api_auth import auth_headers
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

from config_loader import cfg                                          # noqa: E402
from llm.provider import get_agent_llm                                 # noqa: E402
from nodes.supervisor import _SYSTEM_PROMPT, _extract_route            # noqa: E402

FIXTURE = REPO_ROOT / "fixtures" / "golden" / "phase_f_routing.json"
ORCH_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8090")
REPORT_PATH = REPO_ROOT / "docs" / "f_v2_report.json"

# Decoy capability descriptions. Never a valid route; _extract_route only
# recognizes sql/docs/hybrid, so any decoy the LLM might report gets caught
# by the regex fallback finding no match and defaulting to "sql" -- itself
# a data point (a decoy-induced misroute), not a script bug.
_DECOYS: list[tuple[str, str]] = [
    ("geocoding", "Decodes a location code to a latitude/longitude pair and reverse-geocodes coordinates to addresses."),
    ("inventory_forecast", "Predicts future stock levels per SKU from historical order velocity."),
    ("carrier_negotiation", "Drafts rate-negotiation talking points for a logistics provider contract renewal."),
    ("price_optimizer", "Recommends catalog price adjustments based on competitor and demand signals."),
    ("warranty_claims", "Processes manufacturer warranty claims for defective catalog items."),
    ("supplier_scorecard", "Computes a composite supplier reliability score from delivery and dispute history."),
    ("route_optimization", "Plans multi-stop delivery routes to minimize total distance for a courier."),
    ("sentiment_analysis", "Scores customer review text for sentiment polarity."),
    ("fraud_detection", "Flags orders with anomalous payment or account-creation patterns."),
    ("demand_planning", "Forecasts regional demand spikes ahead of a promotional event."),
    ("csr_rag", "Hypergraph-based schema retrieval over structural, contextual, and relational pipelines (Strategy §6, deferred)."),
    ("hear_root_cause", "Hypergraph root-cause analysis for supply-chain failure diagnosis (Strategy §6, deferred)."),
    ("vision_score", "VLM-based visual scoring of a rendered chart image (Strategy §6, deferred)."),
    ("cola_normalizer", "GRPO/contrastive-reward fine-tuned multilingual query normalization (Strategy §6, deferred)."),
    ("beckn_ingestion", "Live ONDC/Beckn-protocol network data ingestion (Strategy §6, deferred)."),
    ("address_resolution", "Free-text address resolution and consent-mediated address-label sharing (Strategy §6, deferred)."),
    ("packaging_advisor", "Recommends packaging materials by item fragility class."),
    ("returns_triage", "Auto-classifies a return request into a refund, replace, or repair track."),
    ("seasonal_staffing", "Projects warehouse staffing needs from seasonal order forecasts."),
    ("carbon_footprint", "Estimates shipment carbon footprint from route and vehicle type."),
    ("loyalty_scoring", "Computes buyer loyalty tier from order recency, frequency, and value."),
    ("dispute_mediation", "Drafts a neutral mediation summary for a multi-party dispute."),
    ("catalog_dedup", "Detects duplicate catalog listings across sellers."),
    ("currency_hedging", "Recommends FX hedging positions for cross-border settlement exposure."),
    ("cold_chain_monitor", "Live IoT temperature-excursion alerting for cold-chain shipments."),
    ("courier_scheduling", "Assigns delivery slots to couriers under capacity constraints."),
    ("tax_classification", "Classifies catalog items into GST tax slabs."),
    ("churn_prediction", "Predicts seller churn risk from engagement and dispute trends."),
    ("packaging_compliance", "Checks packaging against category-specific regulatory requirements."),
    ("promo_eligibility", "Determines promotional-offer eligibility per order."),
    ("network_capacity", "Models logistics network capacity against projected order volume."),
    ("returns_fraud", "Flags return requests matching known returns-abuse patterns."),
    ("vendor_onboarding", "Automates document collection for new seller onboarding."),
    ("delivery_eta", "Predicts a delivery ETA independent of the promised SLA window."),
    ("multilingual_support", "Routes customer support chats by detected language."),
    ("inventory_rebalance", "Recommends stock transfers between warehouses."),
    ("promotions_planner", "Schedules promotional campaigns against inventory availability."),
    ("payment_reconciliation", "Reconciles settlement records across payment gateways."),
    ("label_generation", "Generates shipping labels in carrier-specific formats."),
]


def _build_prompt(n_decoys: int) -> str:
    """
    Builds a Supervisor-style system prompt carrying the 3 real routes plus
    `n_decoys` decoy capability descriptions, in the same "Routes:" list
    shape the real prompt uses -- so the crowding is realistic (more lines
    to read before finding the 3 that are real), not merely a longer string.
    """
    assert n_decoys <= len(_DECOYS), f"Need {n_decoys} decoys, only {len(_DECOYS)} defined."
    decoy_lines = "\n".join(
        f"  {name} — {desc}" for name, desc in _DECOYS[:n_decoys]
    )
    # Insert decoys after the real "Routes:" block, before "Examples:" --
    # mirrors how a genuinely over-exposed tool manifest would present:
    # real capabilities interleaved with a much longer list of others.
    marker = "\nExamples:"
    assert marker in _SYSTEM_PROMPT
    head, tail = _SYSTEM_PROMPT.split(marker, 1)
    return (
        f"{head}\n"
        f"Other registered capabilities (NOT valid routes -- for context only; "
        f"you must still answer with exactly one of sql/docs/hybrid):\n"
        f"{decoy_lines}\n"
        f"{marker}{tail}"
    )


async def _classify_direct(llm: Any, query_text: str, system_prompt: str) -> str:
    result = await llm.complete(
        system=system_prompt,
        messages=[{"role": "user", "content": query_text}],
        max_tokens=cfg.sql.max_tokens_supervisor,
    )
    return _extract_route(result.text)


def _http_query(text: str) -> dict[str, Any]:
    payload = json.dumps({"query": text}).encode()
    req = urllib.request.Request(
        f"{ORCH_URL}/query", data=payload,
        headers=auth_headers({"Content-Type": "application/json"}), method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())


def run_baseline(queries: list[dict]) -> dict[str, Any]:
    print(f"── F-V2 baseline: {len(queries)} queries through the live orchestrator ──")
    per_route: dict[str, list[bool]] = defaultdict(list)
    results = []
    for i, q in enumerate(queries, start=1):
        t0 = time.monotonic()
        try:
            resp = _http_query(q["text"])
            got_route = resp.get("state_trace", {}).get("route")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            got_route = None
            print(f"  [{i}/{len(queries)}] {q['id']} ERROR: {exc}")
        correct = got_route == q["expected_route"]
        per_route[q["expected_route"]].append(correct)
        results.append({
            "id": q["id"], "expected": q["expected_route"], "got": got_route,
            "correct": correct, "elapsed_s": round(time.monotonic() - t0, 1),
        })
        status = "OK " if correct else "MISS"
        print(f"  [{i}/{len(queries)}] {status} {q['id']} expected={q['expected_route']} got={got_route}")

    overall = sum(r["correct"] for r in results) / len(results)
    by_route = {route: sum(v) / len(v) for route, v in per_route.items()}
    print(f"\nBaseline overall accuracy: {overall:.3f} ({sum(r['correct'] for r in results)}/{len(results)})")
    for route, acc in sorted(by_route.items()):
        print(f"  {route}: {acc:.3f}")
    return {"overall_accuracy": overall, "by_route": by_route, "results": results}


async def run_over_exposure(subsample: list[dict]) -> dict[str, Any]:
    surfaces: list[int] = list(cfg.experiment.tool_ceiling_surfaces)
    runs_per_level: int = cfg.experiment.tool_ceiling_runs_per_level
    n_real_routes = 3

    print(f"\n── Over-exposure experiment: surfaces={surfaces}, "
          f"runs/level={runs_per_level}, subsample={len(subsample)} queries ──")

    llm = get_agent_llm("supervisor")
    levels: dict[int, list[float]] = {}

    for surface in surfaces:
        n_decoys = max(0, surface - n_real_routes)
        prompt = _build_prompt(n_decoys)
        run_accuracies = []
        for run_idx in range(runs_per_level):
            correct = 0
            for q in subsample:
                got = await _classify_direct(llm, q["text"], prompt)
                if got == q["expected_route"]:
                    correct += 1
            acc = correct / len(subsample)
            run_accuracies.append(acc)
            print(f"  surface={surface:>2} (decoys={n_decoys:>2}) run {run_idx + 1}/{runs_per_level}: "
                  f"accuracy={acc:.3f}")
        levels[surface] = run_accuracies

    mean_by_surface = {s: sum(v) / len(v) for s, v in levels.items()}
    print("\nMean accuracy by surface size:")
    for s in surfaces:
        print(f"  surface={s:>2}: mean={mean_by_surface[s]:.3f}  runs={[round(x, 3) for x in levels[s]]}")

    lowest, highest = min(surfaces), max(surfaces)
    degrades = mean_by_surface[highest] < mean_by_surface[lowest]
    print(f"\nDegradation check: accuracy at {highest} ({mean_by_surface[highest]:.3f}) "
          f"< accuracy at {lowest} ({mean_by_surface[lowest]:.3f})? {degrades}")
    if not degrades:
        print("NON-REPRODUCTION: recorded honestly, not tuned to force a curve (F-V2 instruction).")

    return {
        "surfaces": surfaces, "runs_per_level": runs_per_level,
        "subsample_size": len(subsample), "raw_runs_by_surface": levels,
        "mean_accuracy_by_surface": mean_by_surface, "degrades_at_ceiling": degrades,
    }


def build_subsample(queries: list[dict]) -> list[dict]:
    per_route_n: int = cfg.experiment.tool_ceiling_subsample_per_route
    by_route: dict[str, list[dict]] = defaultdict(list)
    for q in queries:
        by_route[q["expected_route"]].append(q)
    subsample = []
    for route in ("sql", "docs", "hybrid"):
        subsample.extend(by_route[route][:per_route_n])
    return subsample


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--over-exposure-only", action="store_true")
    # Verification results are append-only: a re-run records a NEW result, it
    # does not rewrite the one a previous phase committed. Writing
    # unconditionally to a fixed path silently destroyed the committed Phase F
    # baseline (1.000 on the pre-hardening fixture) together with its
    # hand-written provenance note -- recovered from git, then prevented here.
    parser.add_argument("--out", default=str(REPORT_PATH),
                        help="Report path. Refuses to overwrite unless --force.")
    parser.add_argument("--force", action="store_true",
                        help="Permit overwriting an existing report.")
    # Shipped-flag guard -- see scripts/shipped_flags.py for the two incidents
    # this exists to prevent.
    add_flag_guard_argument(parser)
    args = parser.parse_args()
    try:
        flag_provenance = require_shipped_flags(
            'experiment_tool_ceiling.py (M9 routing determinism)',
            allow_nonshipped=args.allow_nonshipped_flags, fixture=FIXTURE)
    except FlagStateError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2)
    out_path = Path(args.out)
    if out_path.exists() and not args.force:
        print(f"ERROR: {out_path} exists. Verification results are append-only -- "
              f"pass --out with a new filename to record this run alongside it, "
              f"or --force to deliberately replace it.")
        return 1

    fixture = json.loads(FIXTURE.read_text())
    queries = fixture["queries"]
    assert Counter(q["expected_route"] for q in queries) == {"sql": 20, "docs": 20, "hybrid": 20}, (
        "phase_f_routing.json must hold exactly 20 queries per route (Appendix 2.2)."
    )

    report: dict[str, Any] = {"generated_by": "scripts/experiment_tool_ceiling.py",
                              "flag_provenance": flag_provenance}

    if not args.over_exposure_only:
        report["baseline"] = run_baseline(queries)

    if not args.baseline_only:
        subsample = build_subsample(queries)
        report["over_exposure"] = await run_over_exposure(subsample)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2))
    print(f"\nReport written to {REPORT_PATH.relative_to(REPO_ROOT)}")

    if "baseline" in report:
        pass_baseline = report["baseline"]["overall_accuracy"] >= 0.90
        print(f"\nF-V2 baseline pass (>= 0.90): {pass_baseline} "
              f"({report['baseline']['overall_accuracy']:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

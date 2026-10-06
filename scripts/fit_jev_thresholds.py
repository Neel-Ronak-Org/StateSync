#!/usr/bin/env python3
"""
scripts/fit_jev_thresholds.py
Fits the Jev front door's thresholds on the routing set's DEV split only (docs/specs/partd_tasks/T4b_jev_front_door.md,
amendment 3), from the probabilities each dev run recorded (docs/eval/routing_runs.json, run "<arm>|dev|0"): no new
Jev call. For the "jev" arm the re-check's probabilities replace the first call's where it was asked.

Grid (shared by both needs): jev_yes in 0.50..0.95, jev_no in 0.05..(jev_yes - 0.05), step 0.05;
jev_out_of_scope in 0.50..0.95 or never (1.01). Objective, fixed before fitting: route accuracy over dev questions
and dev conversation turns together; ties broken by macro class accuracy, then by distance from the defaults
(0.6 / 0.4 / 0.5). The test split is then run live with the fitted values (a decision can trigger a call that
offline replay cannot).

    python3 scripts/fit_jev_thresholds.py            prints the fit per arm and writes docs/eval/jev_thresholds.json
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "orchestrator"))
RUNS = REPO / "docs" / "eval" / "routing_runs.json"
SET = REPO / "docs" / "eval" / "routing_set.json"
OUT = REPO / "docs" / "eval" / "jev_thresholds.json"
STEP = 0.05                       # noqa: literals - grid step
NEVER = 1.01                      # noqa: literals - an out-of-scope threshold no probability reaches
DEFAULTS = (0.6, 0.4, 0.5)        # noqa: literals - statesync.toml [front_door] jev_yes, jev_no, jev_out_of_scope


def route_for(db: str, docs: str, oos: bool) -> str:
    """orchestrator/front_door.route_for, repeated so this script runs without the orchestrator's imports."""
    if oos:
        return "neither"
    if db == "yes":
        return "hybrid" if docs == "yes" else "sql"
    if docs == "yes":
        return "docs"
    if db == "unsure":
        return "hybrid" if docs == "unsure" else "sql"
    return "docs" if docs == "unsure" else "neither"


def need(p: float, yes: float, no: float) -> str:
    return "yes" if p >= yes else "no" if p <= no else "unsure"


def probs(rec: dict) -> dict | None:
    jev = ((rec.get("front_door") or {}).get("jev")) or {}
    pr = jev.get("probabilities") or {}
    if not pr.get("first") or jev.get("fallback"):
        return None
    return {**pr["first"], **(pr.get("recheck") or {})}


def grid(lo: float, hi: float) -> list[float]:
    n = round((hi - lo) / STEP)
    return [round(lo + i * STEP, 2) for i in range(n + 1)]


def main() -> int:
    rs = json.loads(SET.read_text())
    gold = {i["id"]: i for i in rs["items"]}
    gold.update({f"{c['id']}|{t['turn']}": t for c in rs["conversations"] for t in c["turns"]})
    runs = json.loads(RUNS.read_text())["runs"]
    result = {}
    for arm in ("jev", "jev_rw"):
        run = runs.get(f"{arm}|dev|0")
        if not run:
            continue
        rows = [(gold[k], probs(r)) for k, r in {**run["items"], **run["turns"]}.items()]
        usable = [(g, p) for g, p in rows if p]
        best = None
        for yes in grid(0.5, 0.95):
            for no in grid(0.05, round(yes - STEP, 2)):
                for oos in grid(0.5, 0.95) + [NEVER]:
                    by = defaultdict(lambda: [0, 0])
                    for g, p in usable:
                        r = route_for(need(p["needs_database"], yes, no), need(p["needs_documents"], yes, no),
                                      p["out_of_scope"] >= oos)
                        by[g["class"]][0] += r in g["acceptable"]
                        by[g["class"]][1] += 1
                    acc = sum(a for a, _ in by.values()) / len(usable)
                    macro = sum(a / b for a, b in by.values()) / len(by)
                    dist = abs(yes - DEFAULTS[0]) + abs(no - DEFAULTS[1]) + abs(min(oos, 1.0) - DEFAULTS[2])
                    key = (round(acc, 6), round(macro, 6), -dist)
                    if best is None or key > best[0]:
                        best = (key, {"jev_yes": yes, "jev_no": no, "jev_out_of_scope": oos},
                                {c: f"{a}/{b}" for c, (a, b) in sorted(by.items())})
        (acc, macro, _), thresholds, by_class = best
        result[arm] = {"thresholds": thresholds, "dev_route_accuracy": acc, "dev_macro": macro, "dev_by_class": by_class,
                       "n": len(usable), "excluded_fallback_or_error": len(rows) - len(usable)}
        print(arm, json.dumps(result[arm]))
    OUT.write_text(json.dumps({"objective": "dev route accuracy (questions + turns), then macro, then closeness to defaults",
                               "fits": result}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

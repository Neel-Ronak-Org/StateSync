#!/usr/bin/env python3
"""
scripts/score_specs.py
E2 Deliverable 8 — recall-weighted specification scoring (M7).

Does no scoring of its own: it resolves scorers BY NAME from the registry
(orchestrator/scorers.py, the A8 `Scorer` seam) using cfg.evaluation.scorers,
and applies them. There is no `if scorer == "spec"` here or anywhere else --
adding the D11 vision scorer later is a register_scorer() call plus a config
edit, with this script untouched.

Modes:
  --self-test   E2-V2. Builds two specs against one reference (one missing a
                required encoding channel, one with an extra stylistic title)
                and asserts the missing-encoding spec scores strictly lower.
                No live services needed.
  --score FILE  Scores generated specs against fixtures/golden/phase_e2.json.
                FILE is {"<query_id>": <generated spec or null>, ...}.
                This is the E2-V1 / M7-quality path and is MODEL-DEPENDENT --
                its output is a local-model figure pending the graded
                re-baseline (docs/rebaseline_runbook.md), never a final M7.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

from config_loader import cfg                                    # noqa: E402
from scorers import get_scorer, get_scorers, registered_scorer_names  # noqa: E402

FIXTURE = REPO_ROOT / "fixtures" / "golden" / "phase_e2.json"

SCHEMA_URL = "https://vega.github.io/schema/vega-lite/v5.json"


def _reference_spec() -> dict:
    """A three-channel reference: x, y, and colour."""
    return {
        "$schema": SCHEMA_URL,
        "mark": "bar",
        "data": {"values": [{"k": "A", "v": 1}]},
        "encoding": {
            "x": {"field": "k", "type": "nominal"},
            "y": {"field": "v", "type": "quantitative", "aggregate": "sum"},
            "color": {"field": "k", "type": "nominal"},
        },
    }


def self_test() -> int:
    """E2-V2 — recall weighting is real."""
    scorer = get_scorer("spec")
    reference = _reference_spec()

    missing_encoding = json.loads(json.dumps(reference))
    del missing_encoding["encoding"]["color"]          # drops a required channel

    extra_title = json.loads(json.dumps(reference))
    extra_title["title"] = "Orders by seller"          # stylistic only

    identical = json.loads(json.dumps(reference))

    r_ident = scorer.score(identical, reference)
    r_missing = scorer.score(missing_encoding, reference)
    r_extra = scorer.score(extra_title, reference)

    print("── E2-V2: recall weighting is real ──")
    print(f"  weights: recall={cfg.scoring.recall_weight} precision={cfg.scoring.precision_weight} "
          f"mark={cfg.scoring.mark_weight} encoding={cfg.scoring.encoding_weight} "
          f"transform={cfg.scoring.transform_weight}")
    print(f"  identical spec            score = {r_ident['score']:.6f}")
    print(f"  extra stylistic title     score = {r_extra['score']:.6f}")
    print(f"  missing encoding channel  score = {r_missing['score']:.6f}"
          f"  (encoding recall {r_missing['encoding']['recall']:.3f})")

    failures = []
    if r_ident["score"] != 1.0:
        failures.append(f"identical spec must score 1.0, got {r_ident['score']}")
    if not (r_missing["score"] < r_extra["score"]):
        failures.append(
            f"missing-encoding ({r_missing['score']}) must score STRICTLY LOWER "
            f"than extra-title ({r_extra['score']})"
        )

    # A directional check the spec implies but does not name: with recall
    # weighted above precision, dropping one item must cost more than adding
    # one. Asserted explicitly so a weight swap in config cannot pass silently.
    added_channel = json.loads(json.dumps(reference))
    added_channel["encoding"]["size"] = {"field": "v", "type": "quantitative"}
    r_added = scorer.score(added_channel, reference)
    print(f"  extra encoding channel    score = {r_added['score']:.6f}")
    if not (r_missing["score"] < r_added["score"]):
        failures.append(
            f"a MISSING channel ({r_missing['score']}) must cost more than an "
            f"EXTRA channel ({r_added['score']}) under recall weighting"
        )

    if failures:
        for f in failures:
            print(f"  FAIL: {f}")
        return 1
    print("  PASS: recall weighting is implemented, not just configured.")
    return 0


def score_generated(path: Path) -> int:
    """E2-V1 path. Model-dependent — see module docstring."""
    fixture = json.loads(FIXTURE.read_text())
    generated = json.loads(path.read_text())
    scorer = get_scorer("spec")

    scores, null_count, missing = [], 0, 0
    for q in fixture["queries"]:
        if q["id"] not in generated:
            missing += 1
            continue
        gen = generated[q["id"]]
        if gen is None:
            null_count += 1
        result = scorer.score(gen, q["reference_spec"])
        scores.append(result["score"])
        print(f"  {q['id']}: {result['score']:.4f}"
              f"{'  (chart:null)' if gen is None else ''}")

    mean = sum(scores) / len(scores) if scores else 0.0
    print("\n── M7 (LOCAL MODEL — pending graded re-baseline) ──")
    print(f"  scored:            {len(scores)}/{len(fixture['queries'])}")
    print(f"  mean spec score:   {mean:.4f}")
    print(f"  chart:null count:  {null_count}   (reported separately per E2-V1)")
    if missing:
        print(f"  missing from input: {missing}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true", help="run E2-V2")
    ap.add_argument("--score", type=Path, help="score generated specs (E2-V1, model-dependent)")
    args = ap.parse_args()

    print(f"registered scorers: {registered_scorer_names()}")
    print(f"configured (cfg.evaluation.scorers): {list(cfg.evaluation.scorers)}")
    print(f"resolved: {[s.name for s in get_scorers()]}\n")

    if args.score:
        return score_generated(args.score)
    return self_test()


if __name__ == "__main__":
    raise SystemExit(main())

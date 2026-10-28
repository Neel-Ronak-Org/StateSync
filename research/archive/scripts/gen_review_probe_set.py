#!/usr/bin/env python3
"""
scripts/gen_review_probe_set.py
Cycle 006 Workstream B1. Builds a fixed-seed sample of 20 sellers from the
committed review-digest manifest, each paired with one templated question
naming that seller's id verbatim.

THIS IS NOT A QUALITY FIXTURE. Cycles 003 and 005 both declined to write
ground-truth review-question answers in the same cycle as a corpus change
(tuning against what you report from); that reasoning is unchanged and this
does not revisit it. There is no answer text, no expected claim, no judge
call here -- the "ground truth" is mechanical and given by construction: the
document_id the generator already assigned to that seller. What this probe
set measures is a single, narrow, structural property -- given a query that
names a seller's exact id, where does that seller's own digest rank in
`vector.semantic_search`'s ordering -- not review-answer quality.

WHY THIS EXISTS. Audit 006 F-006-2 found that a query naming a seller's
32-hex id verbatim ranked that seller's own digest 2,365th of 13,161 chunks,
against `semantic_top_k=8` -- one probe, chosen by hand while investigating.
This script exists so "one probe" becomes "a fixed, pre-committed set of 20",
selected BEFORE any ingestion-path change (cycle 006 B1), so the same 20
probes can be re-run after the change (B5) without either set having been
chosen to make either arm look better.

SELECTION METHOD, DETERMINISTIC. Every document_id in the committed manifest
is sorted (stable, no dependency on dict/JSON iteration order), then
`random.Random(SEED).sample(..., 20)` picks 20 of them. Running this twice
against an unchanged manifest produces byte-identical output -- verified
live, not asserted (see the outcome section of docs/cycles/006.md).

WHY A SINGLE TEMPLATE. The question is deliberately the plainest possible
phrasing that names the seller id and nothing else identifying -- "What do
customers say about seller <id>?" -- so the probe measures dense-retrieval
rank on the id itself, not template-wording sensitivity. A richer set of
phrasings is future work, not this cycle's (§2 "Explicitly NOT this cycle").

Usage:
    python3 scripts/gen_review_probe_set.py --out fixtures/golden/review_probe_set.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "fixtures" / "documents" / "olist_review_digests_manifest.json"

# Fixed, arbitrary, committed. Do not change once probes have been used for
# a before-arm measurement -- changing it would silently swap which sellers
# are being measured between B2 and B5, defeating the point of a paired
# comparison.
SEED = 20260823
N_PROBES = 20

QUESTION_TEMPLATE = "What do customers say about seller {seller_id}?"


def build_probe_set() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text())
    docs = manifest["documents"]

    # Stable order independent of JSON/dict iteration -- sort by document_id.
    docs_sorted = sorted(docs, key=lambda d: d["document_id"])
    rng = random.Random(SEED)
    chosen = rng.sample(docs_sorted, N_PROBES)
    # Re-sort the sample itself for a deterministic, readable output order --
    # sample() preserves selection but not a meaningful order.
    chosen = sorted(chosen, key=lambda d: d["document_id"])

    probes = []
    for doc in chosen:
        seller_id = doc["document_id"].removeprefix("doc_review_digest_")
        probes.append({
            "probe_id": f"rp_{seller_id[:8]}",
            "seller_id": seller_id,
            "document_id": doc["document_id"],
            "question": QUESTION_TEMPLATE.format(seller_id=seller_id),
            "review_count": doc["review_count"],
            "content_word_count": len(doc["content_md"].split()),
        })

    return {
        "version": 1,
        "generated_by": "scripts/gen_review_probe_set.py",
        "purpose": (
            "Cycle 006 B1/B2/B5. NOT a quality fixture -- a mechanical "
            "retrieval-rank probe set. Ground truth is the document_id "
            "already assigned by scripts/gen_olist_review_digests.py; no "
            "answer text or judge call is involved. See this script's "
            "module docstring."
        ),
        "seed": SEED,
        "n": N_PROBES,
        "manifest_source": "fixtures/documents/olist_review_digests_manifest.json",
        "probes": probes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="Output probe-set path")
    args = parser.parse_args()

    result = build_probe_set()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")

    print(f"Selected {len(result['probes'])} probes, seed={SEED}")
    for p in result["probes"]:
        print(f"  {p['probe_id']}  seller={p['seller_id']}  "
              f"reviews={p['review_count']}  words={p['content_word_count']}")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()

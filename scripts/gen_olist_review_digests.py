#!/usr/bin/env python3
"""
scripts/gen_olist_review_digests.py
Cycle 005 Workstream C (audit 005; owner direction 2026-08-21: "the review
ingest, redesigned"). Builds one document per seller who has at least one
commented `olist.order_reviews` row -- a "review digest" -- rather than one
document per raw review row.

WHY A DIGEST, NOT A RAW-ROW INGEST. Measured during audit 005: 41,746
commented reviews average 11.9 words (max 45) against this project's
512-token chunk target (`config/statesync.toml [retrieval]`), so a raw
ingest leaves `_chunk_words` exactly as unexercised as F-004-6 found it --
every chunk would still be a whole "document." Grouped per seller instead,
2,567 sellers have >= 1 commented review (42,007 seller x review pairs --
a seller with a multi-item order can pull in the same review more than
once via the order_items join below, so this is NOT the same count as the
41,746 distinct commented reviews above; both are real, they are different
quantities), median digest length 84 words, max 17,704, and **349** exceed
the 512-word chunk target -- so the real `scripts/ingest.py` chunker splits
a document for the first time in this project's history (verified live,
not asserted: see docs/audits/005.md and docs/cycles/005.md C4).
**These figures were re-measured twice, 2026-08-23 (cycle 006 A2/A3, audit
006 F-006-1 and its second amendment).** First correction: the original
docstring's 56/12,426/246 did not reproduce by running this same,
unchanged, verified-deterministic script. Second correction, found while
building A3's pinning test: the FIRST correction's word-count method
(`content_md.count(" ") + 1`, this file's own diagnostic below) itself
undercounts, because content_md is newline-heavy and `count(" ")` does not
treat a newline as a word boundary the way `_chunk_words`' `str.split()`
does. Calling `_chunk_words` directly -- the function whose behaviour this
number is supposed to describe -- against the same manifest text gives
**349**, an exact subset of the 1,054 review documents the live database
shows split, not 313. The diagnostic below now counts words the same way
`_chunk_words` does, so it will not silently drift from the chunker's own
logic again. See docs/audits/006.md's second amendment for the transcript.
Separately -- audit 006 F-006-1a -- **349 is the count that matters for the
"chunker split a document" claim above.** A different figure, 1,072, is
published elsewhere (docs/limitations.md, docs/AGENT_HANDOFF.md) for "how
many documents ended up with more than one chunk in the database"; that
count also includes 18 baseline documents that were already multi-chunk
before this generator ever ran, and 705 documents this generator emitted
as ONE chunk that only became multiple chunks via ingest.py's
`_embed_allowing_split` context-length fallback, not via this script or
`_chunk_words`. Do not conflate the two counts; they measure different
things and neither is a typo for the other. (18 + 349 + 705 = 1,072,
exactly -- the arithmetic reconciles even though the published attribution
did not.)

DETERMINISM (C2). The SQL orders sellers by seller_id and each seller's
reviews by review_id, both stable sort keys already unique in the source
data -- no LIMIT, no random sampling, no wall-clock content. Running this
twice against an unchanged `olist` schema produces byte-identical
document_ids and content_md.

NOT TRANSLATED, NOT SUMMARISED (C2). review_comment_message is reproduced
verbatim, in the original Portuguese. This corpus does not make the RAG
half support Portuguese-language questions -- there is no ground truth for
that, and docs/limitations.md says so (C8). It exists to measure what
~100x the retrieval-space chunk count does to the EXISTING 16 E1 queries
(interference), which is a corpus-scale question, not a translation one.

REVERSIBLE BY CONSTRUCTION (C5). **Corrected 2026-08-23 (cycle 006 A2) --
this paragraph described the plan as frozen, not what shipped; see
DEVIATION 1 below the imports for why it changed mid-build.** Every digest
is written to `documents.tenant_id` / `document_chunks.tenant_id` =
`INGEST_TENANT_ID` (`t_capstone_default`, the SAME value as the baseline
corpus -- distinct tenant_id turned out to make the corpus unretrievable
by the live orchestrator, see DEVIATION 1). Isolation and reversibility
use `doc_type='review_digest'` instead: no baseline document has ever used
that value, so `DELETE ... WHERE doc_type = 'review_digest'` removes
exactly this corpus and the baseline is untouched by construction, not by
care. `REVIEW_DIGEST_TENANT_ID` below is retained as a `provenance_tenant_
label` field for traceability -- it is not what isolates or reverses
anything.

Reads via `statesync_reader` (SELECT-only) directly against postgres,
following the pattern scripts/check_dv6_spatial.py already uses for a
read-only, deterministic reference query -- no ORM, no mcp round-trip,
because this is a fixture generator, not the agent path being tested.

Usage:
    python3 scripts/gen_olist_review_digests.py --out fixtures/documents/olist_review_digests_manifest.json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Cycle 005 DEVIATION 1 (docs/cycles/005.md §3): this is PROVENANCE metadata
# only, not the ingested tenant_id. orchestrator/main.py:108 sets
# tenant_id = cfg.tenancy.default_tenant unconditionally for every query, and
# mcp/tools/vector_tools.py's semantic_search filters document_chunks by that
# same value regardless of tenancy.enforced -- a document actually stored
# under a distinct tenant_id would never be retrieved, which would make C6's
# before/after M6 comparison measure nothing. The manifest records this label
# per document for provenance, but INGEST_TENANT_ID (below) is what actually
# gets written to tenant_id in both tables. Isolation and reversibility use
# doc_type='review_digest' instead -- see build_manifest().
REVIEW_DIGEST_TENANT_ID = "t_olist_reviews"

# What actually gets written to documents.tenant_id / document_chunks.tenant_id
# for every digest -- must match cfg.tenancy.default_tenant so the corpus is
# retrievable by the live orchestrator. Not the same as REVIEW_DIGEST_TENANT_ID.
INGEST_TENANT_ID = "t_capstone_default"

# DISTINCT ON (seller_id, review_id): an order with multiple line items from
# the SAME seller joins that seller to the SAME review multiple times through
# order_items (measured: 48,585 raw join rows, 42,007 distinct (seller,
# review) pairs, one pair repeated 21x) -- without this, a seller's digest
# would repeat that review's text once per extra line item.
#
# order_id then order_item_id, in that order, as the tiebreaker -- NOT
# order_item_id alone. olist.review_id is not a clean key: some review_ids
# are reused across two DIFFERENT order_ids for the same seller (measured:
# review_id 3fb33c49be4d375ddb15913bbb8c0f73 -> two distinct order_ids, both
# with order_item_id=1), so order_item_id alone left a genuine tie and
# DISTINCT ON's pick among tied rows is physical-scan-order-dependent, not
# guaranteed stable -- caught by literally running this generator twice and
# diffing the output (24 of 2,567 digests differed) before trusting C2's
# determinism claim rather than asserting it. order_id is unique per row
# here, so the full sort key now has no remaining ties.
REVIEW_QUERY = """
COPY (
  SELECT DISTINCT ON (oi.seller_id, r.review_id)
         oi.seller_id, r.review_id, r.order_id, r.review_score,
         coalesce(r.review_comment_title, '') AS title,
         r.review_comment_message AS comment,
         r.review_creation_date::date AS created
  FROM olist.order_reviews r
  JOIN olist.order_items oi ON oi.order_id = r.order_id
  WHERE coalesce(trim(r.review_comment_message), '') <> ''
  ORDER BY oi.seller_id, r.review_id, r.order_id, oi.order_item_id
) TO STDOUT WITH (FORMAT csv, HEADER true)
"""


def _reader_password() -> str:
    return (REPO_ROOT / "secrets" / "pg_reader_password").read_text().strip()


def _fetch_reviews() -> list[dict[str, str]]:
    pw = _reader_password()
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "env", f"PGPASSWORD={pw}",
         "psql", "-U", "statesync_reader", "-d", "statesync", "-c", REVIEW_QUERY],
        capture_output=True, text=True, cwd=REPO_ROOT, check=True,
    )
    reader = csv.DictReader(io.StringIO(result.stdout))
    return list(reader)


def _digest_content_md(seller_id: str, reviews: list[dict[str, str]]) -> str:
    lines = [f"# Customer reviews — seller {seller_id}", ""]
    for r in reviews:
        title = r["title"].strip()
        header = f"Review {r['review_id']} (order {r['order_id']}, score {r['review_score']}/5, {r['created']})"
        if title:
            header += f" — {title}"
        lines.append(header)
        lines.append(r["comment"].strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_manifest() -> dict:
    rows = _fetch_reviews()
    by_seller: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_seller.setdefault(row["seller_id"], []).append(row)

    documents = []
    for seller_id in sorted(by_seller):  # sorted(): determinism independent of dict/CSV order
        reviews = by_seller[seller_id]
        documents.append({
            "document_id": f"doc_review_digest_{seller_id}",
            "tenant_id": INGEST_TENANT_ID,
            "provenance_tenant_label": REVIEW_DIGEST_TENANT_ID,
            "title": f"Customer Review Digest — Seller {seller_id}",
            "doc_type": "review_digest",
            "source_uri": f"olist:order_reviews:seller_id={seller_id}",
            "party_id": None,  # olist sellers are not network_participants; no FK target exists
            "content_md": _digest_content_md(seller_id, reviews),
            "review_count": len(reviews),
        })

    return {
        "version": 1,
        "generated_by": "scripts/gen_olist_review_digests.py",
        "tenant_id": INGEST_TENANT_ID,
        "provenance_tenant_label": REVIEW_DIGEST_TENANT_ID,
        "isolation_discriminator": "doc_type='review_digest' (see cycle 005 DEVIATION 1)",
        "source": "olist.order_reviews JOIN olist.order_items (seller_id), commented rows only",
        # Deliverable 2's 15% is_table floor is a property of the baseline PDF
        # corpus, seeded deliberately with tables; a review digest has no
        # tables by construction. See ingest.py's enforce_table_floor check.
        "enforce_table_floor": False,
        "documents": documents,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="Output manifest path")
    args = parser.parse_args()

    manifest = build_manifest()
    docs = manifest["documents"]
    # word count MUST use the same tokenisation as ingest.py::_chunk_words
    # (str.split(), i.e. any whitespace run -- newlines included), not a
    # literal-space count. content_md is newline-heavy (one line per review
    # header, one per body, blank separators), so `.count(" ") + 1`
    # systematically UNDERCOUNTS: verified live, cycle 006 A2/A3 (audit 006
    # F-006-1 amendment) -- the literal-space proxy said 313 documents
    # exceeded the 512-word chunk target; calling the real `_chunk_words`
    # against the same manifest actually splits 349, an exact subset of
    # what the live database shows split. The proxy was wrong; this counts
    # words the way the chunker that acts on this text counts them.
    words = [len(d["content_md"].split()) for d in docs]
    over_512 = sum(1 for w in words if w > 512)

    print(f"Sellers with >= 1 commented review: {len(docs)}")
    print(f"Total commented reviews: {sum(d['review_count'] for d in docs)}")
    print(f"Digest word count: median {sorted(words)[len(words)//2]}, max {max(words)}")
    print(f"Digests exceeding the 512-word chunk target: {over_512}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"Wrote {out_path} ({len(docs)} documents)")


if __name__ == "__main__":
    main()

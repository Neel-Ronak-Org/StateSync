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


def _review_block(r: dict[str, str]) -> str:
    """One review's full text, exactly as it appears in the digest -- header
    line, body, trailing blank line. This is the atomic unit `_digest_sections`
    packs; it is never split across a section boundary."""
    title = r["title"].strip()
    header = f"Review {r['review_id']} (order {r['order_id']}, score {r['review_score']}/5, {r['created']})"
    if title:
        header += f" — {title}"
    return header + "\n" + r["comment"].strip() + "\n\n"


def _digest_content_md(seller_id: str, reviews: list[dict[str, str]]) -> str:
    lines = [f"# Customer reviews — seller {seller_id}", ""]
    for r in reviews:
        lines.append(_review_block(r).rstrip("\n"))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# Cycle 006 B3 (audit 006 F-006-2): the whole-blob content_md above, fed to
# ingest.py's generic `_chunk_words`, is what produced a corpus where 80.4%
# of chunks carried no seller id and 94.9% of continuation chunks began
# mid-review -- the word-window chunker has no notion of a review boundary.
#
# SECTION_WORD_BUDGET, and why 60. `_chunk_words`' 512-WORD-as-token-proxy
# approximation already failed once for this corpus (F-004-6/cycle 005 C3's
# own DEVIATION): a 512-word slice was rejected by the real embedding model
# at 512 TOKENS because Portuguese subword tokenisation is denser than one
# token per word. For THIS corpus specifically the ratio is worse than that
# general finding suggests, because every review's header embeds two 32-hex
# identifiers (review_id, order_id) that a BERT-style tokenizer has no
# whole-word entry for and must split into many subword pieces -- measured
# live: the single densest document in the corpus started failing to embed
# between 120 and 125 WORDS, not anywhere near 512. 60 is chosen with a
# 2x safety margin under that measured floor, verified against the 20
# densest documents in the corpus (zero failures) before being used here --
# not a guessed magic number, matching this project's own stated objection
# to guessing (`ingest.py::_embed_allowing_split`'s docstring). It is not a
# proof for the remaining ~2,547 documents, which is why `ingest.py`'s
# consumer of `sections` (`_embed_one_review_section`) still carries a REAL,
# review-boundary-aware fallback rather than assuming 60 always holds.
SECTION_WORD_BUDGET = 60


def _digest_sections(seller_id: str, reviews: list[dict[str, str]]) -> list[str]:
    """Splits a seller's reviews into sections, each prefixed with the
    seller-identity header, each under SECTION_WORD_BUDGET words where
    possible, and NEVER splitting a single review's header+body across a
    section boundary -- the property audit 006 F-006-2 found missing.
    A single review whose own text alone exceeds the budget still gets its
    own one-review section (never happens on this corpus -- the largest
    single review is ~49 words -- but handled rather than assumed away)."""
    header = f"# Customer reviews — seller {seller_id}\n\n"
    sections: list[str] = []
    current_blocks: list[str] = []
    current_words = len(header.split())

    def flush() -> None:
        if current_blocks:
            sections.append((header + "".join(current_blocks)).rstrip() + "\n")

    for r in reviews:
        block = _review_block(r)
        block_words = len(block.split())
        if current_blocks and current_words + block_words > SECTION_WORD_BUDGET:
            flush()
            current_blocks = []
            current_words = len(header.split())
        current_blocks.append(block)
        current_words += block_words
    flush()
    return sections


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
            # Cycle 006 B3: pre-split, review-boundary-respecting sections.
            # ingest.py chunks EACH section independently (still through
            # _chunk_words/_embed_allowing_split, same functions, same call
            # shape -- see ingest.py's manifest-consumption comment) instead
            # of the whole content_md blob. content_md is kept for
            # readability, provenance, and tests/test_review_chunk_split_
            # attribution.py's pinned property, which describes the OLD
            # whole-blob chunking behaviour and is unaffected by this field's
            # addition -- it does not describe what ships once `sections` is
            # present, and docs/limitations.md says so.
            "sections": _digest_sections(seller_id, reviews),
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

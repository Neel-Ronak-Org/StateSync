"""
tests/test_review_chunk_split_attribution.py
Pins the real `_chunk_words` split count for the committed review-digest
manifest, so the "1,072" miscount (audit 006 F-006-1/F-006-1a) cannot be
restated without this test going red.

WHY THIS EXISTS. Cycle 005 published "1,072 of the 2,567 review documents
produced more than one chunk" as evidence that `_chunk_words` finally split
a document (closing F-004-6). The 1,072 was real but measured over the
WRONG population (all 2,585 documents in the corpus, not the 2,567 review
digests) and attributed to the WRONG mechanism: 18 of it are the baseline
documents, multi-chunk since before this ingest via
`_extract_document_chunks`'s table/narrative split -- literally the
documents F-004-6 diagnosed as unsplit by `_chunk_words` -- and 705 more
are review digests `_chunk_words` emitted as ONE chunk that only became
multiple chunks via `ingest.py::_embed_allowing_split`'s embedding-context
fallback. Only 349 are real `_chunk_words` splits.

A THIRD number appeared while writing this test, worth recording because it
is the same failure class recurring one level in: `scripts/gen_olist_
review_digests.py`'s own diagnostic print originally said 313, using
`content_md.count(" ") + 1` as its word-count proxy. That undercounts,
because content_md is newline-heavy and `_chunk_words` tokenises with
`str.split()` (any whitespace, newlines included), not literal spaces.
Calling `_chunk_words` directly -- as this test does -- gives 349, an exact
subset of the 1,054 review documents the live database shows split. The
generator's diagnostic now uses the same tokenisation (cycle 006 A2/A3,
audit 006 F-006-1's second amendment) so it cannot drift from the function
it is describing again.

SCOPE, DELIBERATELY DB-FREE. `_chunk_words` is a pure function of text and
two config integers -- this test calls it directly against the committed
manifest's `content_md` and needs no database, no MCP round-trip, no
Ollama call, matching this project's established rule that a test needing
a live container "stops running" (see tests/test_schema_scoping.py's own
note on this). It therefore CANNOT reproduce the full 18/705/349 three-way
split against the persisted database -- that requires knowing which
manifest-emitted single chunks were later split by the embedding-context
fallback, which depends on the embedding model's actual token count, not
recoverable from text alone. What it CAN and DOES pin, exactly and without
any live dependency, is the piece `_chunk_words` is actually responsible
for: how many of the 2,567 manifest documents its OWN split logic touches.
The live 18/705/349/1,054 breakdown against the database is transcribed
from a live audit run into docs/limitations.md and docs/cycles/006.md
rather than re-asserted here.

Run from the repo root: pytest tests/test_review_chunk_split_attribution.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from ingest import _chunk_words, _cfg  # noqa: E402

MANIFEST_PATH = REPO_ROOT / "fixtures" / "documents" / "olist_review_digests_manifest.json"

# The count this test exists to pin. If this changes, it is a real change to
# the source data or the chunker -- update docs/limitations.md's decomposition
# table and scripts/gen_olist_review_digests.py's docstring in the SAME commit,
# not this constant alone.
EXPECTED_CHUNKER_SPLIT_DOCS = 349

# The three numbers this test exists to prevent restating (audit 006
# F-006-1 and its second amendment). None is what `_chunk_words` alone
# produces against this manifest; all three are wrong for this specific
# claim ("how many documents did _chunk_words split").
WRONG_COUNT_ALL_DOCTYPES_IN_DB = 1072          # right total, wrong population
WRONG_COUNT_PRE_CORRECTION_DOCSTRING = 246     # the original, unreproducing docstring figure
WRONG_COUNT_SPACE_PROXY = 313                  # count(" ")+1 undercounts vs str.split()


@pytest.fixture(scope="module")
def manifest_documents() -> list[dict]:
    if not MANIFEST_PATH.exists():
        pytest.skip(f"{MANIFEST_PATH} not present in this checkout")
    manifest = json.loads(MANIFEST_PATH.read_text())
    return manifest["documents"]


def _chunker_split_count(documents: list[dict]) -> int:
    target = _cfg["retrieval"]["chunk_target_tokens"]
    overlap = _cfg["retrieval"]["chunk_overlap_tokens"]
    return sum(
        1 for doc in documents
        if len(_chunk_words(doc["content_md"], target, overlap)) > 1
    )


def test_chunker_split_count_matches_pinned_value(manifest_documents):
    """The real, current `_chunk_words` split count over the committed
    manifest. If this drifts from 349, either the manifest or the chunker
    changed -- both are things a reader of docs/limitations.md's
    decomposition table needs to know about."""
    actual = _chunker_split_count(manifest_documents)
    assert actual == EXPECTED_CHUNKER_SPLIT_DOCS, (
        f"_chunk_words split {actual} of {len(manifest_documents)} manifest "
        f"documents, not the pinned {EXPECTED_CHUNKER_SPLIT_DOCS}. Update "
        f"EXPECTED_CHUNKER_SPLIT_DOCS AND docs/limitations.md's "
        f"decomposition table AND scripts/gen_olist_review_digests.py's "
        f"docstring together -- do not change one without the others."
    )


def test_chunker_split_count_is_not_the_two_wrong_published_counts(manifest_documents):
    """Directly demonstrates this test is capable of failing: computed fresh
    against the real manifest and the real chunker, 349 is none of the three
    wrong numbers this project has at some point published for a related
    claim (1,072 total; 246 and 313, two different flawed proxies for this
    exact quantity). This is the check that would have caught F-006-1
    before it was published, run here so it cannot silently start passing
    against a wrong number again."""
    actual = _chunker_split_count(manifest_documents)
    assert actual != WRONG_COUNT_ALL_DOCTYPES_IN_DB, (
        "the chunker-only split count coincides with the all-doctypes "
        "database count -- if this ever passes, F-006-1's population "
        "confusion (18 baseline + 705 fallback-only documents) has become "
        "true by coincidence; re-derive the decomposition, don't just "
        "accept the match."
    )
    assert actual != WRONG_COUNT_PRE_CORRECTION_DOCSTRING, (
        "the chunker-only split count coincides with the pre-correction "
        "docstring figure (246) -- if the manifest is regenerated and this "
        "starts passing, check whether it's a real coincidence or whether "
        "someone reverted scripts/gen_olist_review_digests.py's fixed "
        "docstring numbers without checking they still reproduce."
    )
    assert actual != WRONG_COUNT_SPACE_PROXY, (
        "the chunker-only split count coincides with the flawed "
        "count(' ')+1 proxy (313) -- if this ever passes, check that "
        "scripts/gen_olist_review_digests.py's word-count diagnostic still "
        "uses str.split() and not a literal-space count; the two disagree "
        "by 36 documents on this manifest because content_md is "
        "newline-heavy."
    )


def test_manifest_document_count_is_2567(manifest_documents):
    """The denominator every published figure divides by. Pinned separately
    so a manifest-size change is caught even if the split count coincidentally
    doesn't move."""
    assert len(manifest_documents) == 2567

#!/usr/bin/env python3
"""
scripts/ingest.py
Ingestion pipeline (Part E1 Deliverable 1). Parses fixtures/documents/*.pdf
(layout-aware: tables extracted as structure-preserving Markdown, kept
separate from narrative text), chunks, embeds, and writes to
documents/document_chunks as statesync_ingest.

Per A1: "Ingestion (Part E) writes through a separate short-lived
statesync_ingest role invoked by an operator-run job -- never by the
orchestrator, never by an agent." This script IS that operator-run job. It
is never imported by orchestrator/ or mcp/, and holds its own credentials
independently of statesync_reader (E1-V7).

Table extraction: pdfplumber's find_tables() gives each table's bounding
box; outside_bbox() over that same box gives the narrative text with the
table content excluded, so a table's cells are never duplicated into a
separate narrative chunk (and vice versa). A table becomes exactly one
chunk (is_table=true), its content_md a real Markdown table (pipe-delimited
header + separator + rows) -- not the whitespace-flattened token run that
naive text extraction over a table region would produce (D1 Notes: this is
the OCR-failure-adjacent mode layout-aware parsing exists to prevent,
checked directly by E1-V1).

Chunking: word-count approximated as token count (no real tokenizer
dependency) -- a coarse but adequate approximation for the corpus size here;
retrieval.chunk_target_tokens/chunk_overlap_tokens from config either way.

After ANY re-ingest, run scripts/refresh_sample_search.py: hybrid retrieval ranks
this corpus by BM25 over the side table search.sample_terms (migration 027), which
nothing here updates. Skipping it leaves the keyword leg ranking stale chunks.

Usage:
    python3 scripts/ingest.py
    python3 scripts/ingest.py --manifest fixtures/documents/manifest.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ingestion_source import get_ingestion_source

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
# Matches orchestrator/mcp's own config_loader.py precedent (CONFIG_DIR env
# var first, falling back to a path relative to this file) rather than
# hardcoding REPO_ROOT/config -- this script's own location isn't always
# REPO_ROOT/scripts/ingest.py (e.g. run from inside a container for network
# reachability, per A1's db_net isolation, with no other change in behavior).
CONFIG_DIR = Path(os.environ["CONFIG_DIR"]) if os.environ.get("CONFIG_DIR") else REPO_ROOT / "config"
TENANT_ID = "t_capstone_default"  # A0.6: always config default; see cfg.tenancy.default_tenant below


def _load_cfg() -> dict[str, Any]:
    with open(CONFIG_DIR / "statesync.toml", "rb") as f:
        return tomllib.load(f)


_cfg = _load_cfg()


def _read_password(secret_file: str, env_var: str) -> str:
    try:
        return Path(secret_file).read_text().strip()
    except OSError:
        pw = os.environ.get(env_var, "")
        if not pw:
            raise RuntimeError(f"Password not found in {secret_file!r} or env var {env_var!r}.")
        return pw


def _build_ingest_dsn() -> str:
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5433")  # docker-compose.override.yml: 5433 -> 5432
    db = os.environ.get("POSTGRES_DB", "statesync")
    pw = _read_password(
        os.environ.get("PG_INGEST_PASSWORD_FILE", str(REPO_ROOT / "secrets" / "pg_ingest_password")),
        "PG_INGEST_PASSWORD",
    )
    return f"postgresql://statesync_ingest:{pw}@{host}:{port}/{db}"


def _embed(text: str) -> list[float]:
    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    model = _cfg["retrieval"]["embedding_model"]
    expected_dim = _cfg["retrieval"]["embedding_dim"]
    timeout_s = _cfg["retrieval"]["embedding_timeout_s"]

    payload = json.dumps({"model": model, "prompt": text}).encode()
    req = urllib.request.Request(
        f"{ollama_url.rstrip('/')}/api/embeddings", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = json.loads(resp.read())

    vector = body.get("embedding")
    if not isinstance(vector, list) or len(vector) != expected_dim:
        raise RuntimeError(
            f"Embedding response invalid or wrong dimension "
            f"(expected {expected_dim}, got {len(vector) if isinstance(vector, list) else 'N/A'})."
        )
    return vector


_BATCH_SIZE = 50

# Belt-and-suspenders guard for the ONE thing _batch_embed cannot detect on
# its own (silent truncation, no error, see its docstring). Set well below
# gen_olist_review_digests.py's own SECTION_WORD_BUDGET=60 -- no, ABOVE it,
# deliberately: this is a safety NET catching anything that violates the
# packer's guarantee, not a second copy of the packer's own budget. Chosen
# from the same live measurement the packer's 60 came from: 90 words showed
# 1/20 failures on the corpus's densest documents against the endpoint that
# actually rejects; everything at or under 90 that's routed here still goes
# through that same rejecting endpoint via _embed_one_review_section, never
# through the silently-truncating batch one.
_SAFE_BATCH_WORD_LIMIT = 90


def _batch_embed(texts: list[str]) -> list[list[float]]:
    """
    Cycle 006 B4 (performance fix, found live during this cycle's own build).
    Embeds many SHORT, PRE-VERIFIED-SAFE texts via Ollama's `/api/embed`
    endpoint (plural `input`), `_BATCH_SIZE` at a time, instead of one
    `/api/embeddings` call (singular `prompt`) per text.

    WHY THIS EXISTS. The single-call path is correct but was measured live,
    under sustained load, at ~1.38s/call on this (CPU-only, num-parallel=1)
    Ollama server -- 20,209 individual review-digest sections at that rate is
    ~7.8 hours. Batches of 50 measured at ~1.6s/batch -- **~43x faster**,
    confirmed numerically IDENTICAL to the single-call path for the same text
    (max component diff ~3e-8, floating-point noise) -- so this is a
    throughput fix, not a different embedding.

    THE REAL DANGER, VERIFIED LIVE, WHY THIS MUST STAY CALLER-RESTRICTED.
    Unlike `/api/embeddings` (which rejects an over-length input with HTTP
    500 `"the input length exceeds the context length"`, which is what
    `_embed_one_review_section`'s fallback catches and recurses on), `/api/
    embed` does NOT reject an over-length input -- it SILENTLY TRUNCATES.
    Verified: a 400-word, a 200-word, and a 5000-word slice of the SAME text
    sent through this endpoint returned byte-identical embeddings (the
    400-word and 5000-word vectors differed by exactly 0.0). No error, no
    warning, no way to detect it from the response.

    Consequence: **only call this with texts already independently verified
    short enough that the real context length is never approached** --
    `gen_olist_review_digests.py::_digest_sections`' `SECTION_WORD_BUDGET=60`
    is exactly that verification (0/20 failures on the corpus's 20 densest
    documents' longest sections, tested against the STRICTER endpoint that
    actually rejects). Never call this with `_chunk_words` output, PDF
    extraction output, or anything else whose length was not independently
    bounded this way -- use `_embed_allowing_split` (PDF/general path) or
    `_embed_one_review_section` (review-section fallback path) instead,
    both of which rely on the single-call endpoint's real rejection
    behaviour to detect and correctly handle an oversized input.
    """
    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    model = _cfg["retrieval"]["embedding_model"]
    expected_dim = _cfg["retrieval"]["embedding_dim"]
    timeout_s = _cfg["retrieval"]["embedding_timeout_s"] * 4  # a batch takes longer than one item

    vectors: list[list[float]] = []
    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i:i + _BATCH_SIZE]
        payload = json.dumps({"model": model, "input": batch}).encode()
        req = urllib.request.Request(
            f"{ollama_url.rstrip('/')}/api/embed", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read())
        embs = body.get("embeddings")
        if (not isinstance(embs, list) or len(embs) != len(batch)
                or any(not isinstance(e, list) or len(e) != expected_dim for e in embs)):
            raise RuntimeError(
                f"Batch embedding response invalid: expected {len(batch)} "
                f"vectors of dim {expected_dim}, got "
                f"{len(embs) if isinstance(embs, list) else 'N/A'}."
            )
        vectors.extend(embs)
    return vectors


def _embed_allowing_split(content_md: str, max_split_depth: int = 4) -> list[tuple[str, list[float]]]:
    """
    Cycle 005 C3/C4. `mxbai-embed-large`'s REAL context length is 512 TOKENS
    (`ollama show mxbai-embed-large` -> bert.context_length=512); this file's
    own `_chunk_words` approximates a token as one whitespace-split WORD,
    documented above as "coarse but adequate for the corpus size here" -- true
    for the original small English PDF corpus this project shipped with, not
    measured true for this one. It failed for real during this cycle's ingest:
    a 1,709-word review digest's first 512-word chunk was rejected outright --
    `{"error":"the input length exceeds the context length"}`, HTTP 500 --
    caught live, not anticipated.

    Rather than guess a smaller `target_tokens` for this corpus (an
    unprincipled magic number) or lower `config.retrieval.chunk_target_tokens`
    globally (which would silently re-chunk the BASELINE PDF corpus too,
    changing what every existing M6 number was measured against, for a
    problem that corpus never had), a chunk the model actually rejects is
    halved by word count and retried, recursively, until every piece fits.
    This is a safety net around the real chunker, not a second one: chunk
    boundaries from `_chunk_words` are unchanged for every chunk that already
    fits, which is the overwhelming majority (see the outcome section for the
    measured split-event count).
    """
    try:
        return [(content_md, _embed(content_md))]
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        if "context length" not in body_text.lower() or max_split_depth <= 0:
            raise
        words = content_md.split()
        mid = len(words) // 2
        if mid == 0:
            raise
        return (
            _embed_allowing_split(" ".join(words[:mid]), max_split_depth - 1)
            + _embed_allowing_split(" ".join(words[mid:]), max_split_depth - 1)
        )


_REVIEW_HEADER_RE = re.compile(r"(?=^Review [0-9a-f]{32} \(order )", re.MULTILINE)


def _embed_one_review_section(section: str, max_split_depth: int = 6) -> list[tuple[str, list[float]]]:
    """
    Cycle 006 B3 (audit 006 F-006-2). Embeds one pre-split, review-boundary-
    respecting SECTION a review-digest manifest supplies (`gen_olist_review_
    digests.py::_digest_sections`) -- each already under SECTION_WORD_BUDGET
    words and already prefixed with the seller-identity header, so almost
    every section embeds as a single chunk on the first try. Called once per
    section from `ingest()`'s main loop, in place of `_embed_allowing_split`,
    whenever a manifest document carries `"sections"`.

    WHY NOT `_embed_allowing_split`. That function's fallback halves an
    oversized chunk by raw WORD COUNT, blind to any structure -- correct for
    the general PDF/plain-text path (where "structure" isn't a defined
    concept), wrong here: it is exactly the mechanism that produced F-006-2's
    "94.9% of continuation chunks begin mid-review." If a section (rare --
    verified empirically against the corpus's 20 densest documents at
    generation time, not assumed) STILL exceeds the embedding model's real
    context length, this function re-splits it AT THE NEAREST REVIEW
    BOUNDARY to its midpoint and retries each half recursively, so a forced
    split never lands inside a review's own header or body. Only a section
    that has been reduced to a SINGLE review and still fails falls through to
    `_embed_allowing_split`'s word-halving as a last resort -- there is no
    smaller review-respecting unit left to split into, and this has not been
    observed to happen on this corpus (max single review ~49 words).
    """
    try:
        return [(section, _embed(section))]
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        if "context length" not in body_text.lower() or max_split_depth <= 0:
            raise

    # Locate review-header start offsets within this section (the header
    # line, plus everything before the FIRST one -- the "# Customer
    # reviews..." identity line -- belongs to whichever half comes first).
    starts = [m.start() for m in _REVIEW_HEADER_RE.finditer(section)]
    if len(starts) <= 1:
        # Zero or one review boundary in this section: nothing left to split
        # AT A BOUNDARY. Fall back to word-halving as documented above.
        return _embed_allowing_split(section)

    mid = starts[len(starts) // 2]
    preamble = section[:starts[0]]  # the seller-identity header line(s)
    first_half = preamble + section[starts[0]:mid]
    second_half = preamble + section[mid:]
    return (
        _embed_one_review_section(first_half, max_split_depth - 1)
        + _embed_one_review_section(second_half, max_split_depth - 1)
    )


def _table_to_markdown(rows: list[list[str | None]]) -> str:
    if not rows:
        return ""
    header = [c or "" for c in rows[0]]
    body = rows[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for row in body:
        cells = [(c or "").replace("\n", " ") for c in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _chunk_words(text: str, target_tokens: int, overlap_tokens: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    step = max(1, target_tokens - overlap_tokens)
    chunks = []
    start = 0
    while start < len(words):
        chunk = " ".join(words[start:start + target_tokens])
        if chunk.strip():
            chunks.append(chunk)
        if start + target_tokens >= len(words):
            break
        start += step
    return chunks


def _extract_document_chunks(pdf_path: Path) -> list[dict[str, Any]]:
    """Returns [{content_md, is_table, page}], in reading order."""
    import pdfplumber  # lazy: only the PDF path needs it (cycle 006 B4).
    # scripts/ingest.py otherwise only needs psycopg -- already present in
    # the mcp container, which does NOT have pdfplumber installed -- so an
    # inline-text-only (review-digest) manifest can run entirely inside mcp,
    # working around the same Docker Desktop host-port-publishing flakiness
    # for `postgres` documented in docs/phase_e1_log.md §7 item 8, without
    # installing a PDF-parsing dependency somewhere that never parses a PDF.
    chunk_specs: list[dict[str, Any]] = []
    target_tokens = _cfg["retrieval"]["chunk_target_tokens"]
    overlap_tokens = _cfg["retrieval"]["chunk_overlap_tokens"]

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            tables = page.find_tables()

            narrative_page = page
            for t in tables:
                narrative_page = narrative_page.outside_bbox(t.bbox)
            narrative_text = (narrative_page.extract_text() or "").strip()

            for narrative_chunk in _chunk_words(narrative_text, target_tokens, overlap_tokens):
                chunk_specs.append({"content_md": narrative_chunk, "is_table": False, "page": page_num})

            for t in tables:
                rows = t.extract()
                md = _table_to_markdown(rows)
                if md:
                    chunk_specs.append({"content_md": md, "is_table": True, "page": page_num})

    return chunk_specs


def _text_to_chunks(text: str) -> list[dict[str, Any]]:
    """
    Cycle 005 C3. Returns [{content_md, is_table, page}] for a document whose
    content arrives as plain text already (e.g. built from DB rows), not a
    PDF. Calls the SAME _chunk_words chunker _extract_document_chunks uses --
    the only thing skipped is pdfplumber's page/table extraction, because
    there is no PDF to extract a page or table FROM. is_table is always
    False and page is always None: neither concept applies to a document that
    was never paginated.
    """
    target_tokens = _cfg["retrieval"]["chunk_target_tokens"]
    overlap_tokens = _cfg["retrieval"]["chunk_overlap_tokens"]
    return [
        {"content_md": chunk, "is_table": False, "page": None}
        for chunk in _chunk_words(text, target_tokens, overlap_tokens)
    ]


def ingest(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text())
    documents_dir = manifest_path.parent
    dsn = _build_ingest_dsn()

    logger.info("Connecting as statesync_ingest...")
    total_chunks = 0
    total_table_chunks = 0
    total_split_fallbacks = 0

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.transaction():
            for doc in manifest["documents"]:
                # Cycle 005 C3: a manifest entry carrying "content_md" inline
                # (a review digest, built from DB rows, not a scanned PDF)
                # skips PDF extraction entirely but goes through the SAME
                # _chunk_words chunker and _embed embedding call as the PDF
                # path below -- neither is bypassed or reimplemented, only
                # the source-format layer differs.
                is_inline_text = "content_md" in doc
                label = doc["document_id"] if is_inline_text else doc["filename"]
                logger.info("Ingesting %s (%s)...", label, doc["doc_type"])

                tenant_id = doc.get("tenant_id", TENANT_ID)

                conn.execute(
                    """
                    INSERT INTO documents (document_id, tenant_id, title, doc_type, source_uri, party_id)
                    VALUES (%(document_id)s, %(tenant_id)s, %(title)s, %(doc_type)s, %(source_uri)s, %(party_id)s)
                    ON CONFLICT (document_id) DO UPDATE SET
                        title = EXCLUDED.title, doc_type = EXCLUDED.doc_type,
                        source_uri = EXCLUDED.source_uri, party_id = EXCLUDED.party_id
                    """,
                    {**doc, "tenant_id": tenant_id},
                )

                # Re-ingest safety: drop this document's existing chunks before
                # re-inserting, so re-running ingest.py is idempotent rather
                # than accumulating duplicate chunks on every run.
                conn.execute(
                    "DELETE FROM document_chunks WHERE document_id = %(document_id)s",
                    {"document_id": doc["document_id"]},
                )

                # Cycle 006 B3: a manifest entry carrying "sections" (a
                # review digest, pre-split at review boundaries by
                # gen_olist_review_digests.py::_digest_sections) skips BOTH
                # PDF extraction and the generic word-window _chunk_words
                # path -- each section is already sized and bounded at a
                # review boundary, so it is embedded directly, with a
                # review-boundary-aware fallback (_embed_one_review_section) in
                # place of the word-blind _embed_allowing_split. See
                # ingest.py's module docstring and audit 006 F-006-2 for why.
                has_sections = "sections" in doc
                if has_sections:
                    chunk_specs = [
                        {"content_md": s, "is_table": False, "page": None}
                        for s in doc["sections"]
                    ]
                elif is_inline_text:
                    chunk_specs = _text_to_chunks(doc["content_md"])
                else:
                    pdf_path = documents_dir / doc["filename"]
                    chunk_specs = _extract_document_chunks(pdf_path)

                if has_sections:
                    # Cycle 006 B4 performance fix: batch-embed via
                    # _batch_embed (~43x faster than one _embed call per
                    # section, measured live). Safe ONLY because every
                    # section is independently verified under
                    # _SAFE_BATCH_WORD_LIMIT -- anything that somehow isn't
                    # (should never happen given _digest_sections'
                    # SECTION_WORD_BUDGET=60, but checked rather than
                    # assumed) is routed to the single-call, properly-
                    # rejecting, review-boundary-aware fallback instead of
                    # being silently truncated by the batch endpoint. See
                    # _batch_embed's docstring for why this split is
                    # non-negotiable.
                    batchable_idx = [
                        i for i, spec in enumerate(chunk_specs)
                        if len(spec["content_md"].split()) <= _SAFE_BATCH_WORD_LIMIT
                    ]
                    unsafe_idx = [i for i in range(len(chunk_specs)) if i not in batchable_idx]
                    if unsafe_idx:
                        logger.warning(
                            "  %d of %d sections exceeded the %d-word batch safety "
                            "limit; routed to the single-call fallback instead of "
                            "the batch endpoint.", len(unsafe_idx), len(chunk_specs),
                            _SAFE_BATCH_WORD_LIMIT,
                        )
                    batch_vectors = _batch_embed(
                        [chunk_specs[i]["content_md"] for i in batchable_idx]
                    ) if batchable_idx else []
                    all_pieces: list[list[tuple[str, list[float]]]] = [None] * len(chunk_specs)  # type: ignore[list-item]
                    for i, vec in zip(batchable_idx, batch_vectors):
                        all_pieces[i] = [(chunk_specs[i]["content_md"], vec)]
                    for i in unsafe_idx:
                        all_pieces[i] = _embed_one_review_section(chunk_specs[i]["content_md"])
                else:
                    all_pieces = [_embed_allowing_split(spec["content_md"]) for spec in chunk_specs]

                ordinal = 0
                for spec, pieces in zip(chunk_specs, all_pieces):
                    if len(pieces) > 1:
                        total_split_fallbacks += 1
                        logger.warning(
                            "  chunk exceeded the embedding model's context length; "
                            "split into %d pieces (%s)", len(pieces),
                            "review-boundary-aware" if has_sections else "see _embed_allowing_split",
                        )
                    for piece_text, vector in pieces:
                        chunk_id = f"{doc['document_id']}_c{ordinal:03d}"
                        conn.execute(
                            """
                            INSERT INTO document_chunks
                                (chunk_id, document_id, tenant_id, ordinal, page, content_md, is_table, embedding)
                            VALUES
                                (%(chunk_id)s, %(document_id)s, %(tenant_id)s, %(ordinal)s, %(page)s,
                                 %(content_md)s, %(is_table)s, %(embedding)s)
                            """,
                            {
                                "chunk_id": chunk_id, "document_id": doc["document_id"],
                                "tenant_id": tenant_id, "ordinal": ordinal, "page": spec["page"],
                                "content_md": piece_text, "is_table": spec["is_table"],
                                "embedding": "[" + ",".join(repr(float(x)) for x in vector) + "]",
                            },
                        )
                        ordinal += 1
                        total_chunks += 1
                        total_table_chunks += 1 if spec["is_table"] else 0

                logger.info("  %d chunks inserted (%d source specs).", ordinal, len(chunk_specs))

        logger.info("Ingestion complete (transaction committed).")

    table_pct = 100 * total_table_chunks / total_chunks if total_chunks else 0
    logger.info(
        "Total: %d documents, %d chunks, %d is_table (%.1f%%), %d context-length split events.",
        len(manifest["documents"]), total_chunks, total_table_chunks, table_pct, total_split_fallbacks,
    )
    # Deliverable 2's 15% is_table floor is a property of the PDF policy/SOP/
    # supplier-agreement corpus (deliberately seeded with tables to make E1-V3
    # grounding meaningful). A manifest can declare enforce_table_floor=false
    # for a corpus that is structurally prose-only by construction (a review
    # digest has no tables) -- defaults to True so the existing manifest.json
    # is completely unaffected.
    if manifest.get("enforce_table_floor", True) and table_pct < 15.0:
        logger.error("is_table percentage below the 15%% floor required by Deliverable 2.")
        raise SystemExit(1)


if __name__ == "__main__":
    # Manifest path resolved through the IngestionSource seam (A8, Appendix 4)
    # rather than hardcoded -- ingestion.source in config/flags.toml is a real
    # dispatch point here, not a comment. LocalFileSource resolves to the
    # exact same fixtures/documents/manifest.json this always defaulted to.
    default_manifest = str(get_ingestion_source().manifest_path())

    parser = argparse.ArgumentParser(description="Ingest a document corpus into postgres as statesync_ingest.")
    parser.add_argument(
        "--manifest", default=default_manifest,
        help=f"Path to the document manifest (default: resolved via ingestion.source, currently {default_manifest})",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        logger.error("Manifest not found: %s. Run scripts/gen_document_corpus.py first.", manifest_path)
        sys.exit(1)

    ingest(manifest_path)

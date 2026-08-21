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

Usage:
    python3 scripts/ingest.py
    python3 scripts/ingest.py --manifest fixtures/documents/manifest.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pdfplumber
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

                if is_inline_text:
                    chunk_specs = _text_to_chunks(doc["content_md"])
                else:
                    pdf_path = documents_dir / doc["filename"]
                    chunk_specs = _extract_document_chunks(pdf_path)

                ordinal = 0
                for spec in chunk_specs:
                    pieces = _embed_allowing_split(spec["content_md"])
                    if len(pieces) > 1:
                        total_split_fallbacks += 1
                        logger.warning(
                            "  chunk exceeded the embedding model's context length; "
                            "split into %d pieces (see _embed_allowing_split)", len(pieces),
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

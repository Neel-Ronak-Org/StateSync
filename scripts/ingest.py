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
import urllib.request
from pathlib import Path
from typing import Any

import pdfplumber
import psycopg
from psycopg.rows import dict_row

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


def ingest(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text())
    documents_dir = manifest_path.parent
    dsn = _build_ingest_dsn()

    logger.info("Connecting as statesync_ingest...")
    total_chunks = 0
    total_table_chunks = 0

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.transaction():
            for doc in manifest["documents"]:
                pdf_path = documents_dir / doc["filename"]
                logger.info("Ingesting %s (%s)...", doc["filename"], doc["doc_type"])

                conn.execute(
                    """
                    INSERT INTO documents (document_id, tenant_id, title, doc_type, source_uri, party_id)
                    VALUES (%(document_id)s, %(tenant_id)s, %(title)s, %(doc_type)s, %(source_uri)s, %(party_id)s)
                    ON CONFLICT (document_id) DO UPDATE SET
                        title = EXCLUDED.title, doc_type = EXCLUDED.doc_type,
                        source_uri = EXCLUDED.source_uri, party_id = EXCLUDED.party_id
                    """,
                    {**doc, "tenant_id": TENANT_ID},
                )

                # Re-ingest safety: drop this document's existing chunks before
                # re-inserting, so re-running ingest.py is idempotent rather
                # than accumulating duplicate chunks on every run.
                conn.execute(
                    "DELETE FROM document_chunks WHERE document_id = %(document_id)s",
                    {"document_id": doc["document_id"]},
                )

                chunk_specs = _extract_document_chunks(pdf_path)
                for ordinal, spec in enumerate(chunk_specs):
                    vector = _embed(spec["content_md"])
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
                            "tenant_id": TENANT_ID, "ordinal": ordinal, "page": spec["page"],
                            "content_md": spec["content_md"], "is_table": spec["is_table"],
                            "embedding": "[" + ",".join(repr(float(x)) for x in vector) + "]",
                        },
                    )
                    total_chunks += 1
                    total_table_chunks += 1 if spec["is_table"] else 0

                logger.info("  %d chunks (%d table).", len(chunk_specs), sum(1 for c in chunk_specs if c["is_table"]))

        logger.info("Ingestion complete (transaction committed).")

    table_pct = 100 * total_table_chunks / total_chunks if total_chunks else 0
    logger.info(
        "Total: %d documents, %d chunks, %d is_table (%.1f%%). Deliverable 2 requires >= 15%%.",
        len(manifest["documents"]), total_chunks, total_table_chunks, table_pct,
    )
    if table_pct < 15.0:
        logger.error("is_table percentage below the 15%% floor required by Deliverable 2.")
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest fixtures/documents/ into postgres as statesync_ingest.")
    parser.add_argument(
        "--manifest", default=str(REPO_ROOT / "fixtures" / "documents" / "manifest.json"),
        help="Path to the document manifest (default: fixtures/documents/manifest.json)",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        logger.error("Manifest not found: %s. Run scripts/gen_document_corpus.py first.", manifest_path)
        sys.exit(1)

    ingest(manifest_path)

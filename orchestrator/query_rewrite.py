"""
orchestrator/query_rewrite.py
Search-query rewriting for the docs route (docs/specs/rag_v3.md; pipeline.query_rewrite).

WHY. A user question often carries instructions that are about the ANSWER, not the topic
("Answer as an equity analyst, relying on the cash flow statement, in USD millions"). Embedded
whole, those words pull the query vector toward generic finance prose. With the flag on, the
normaliser model first turns the question into a short search query; retrieval uses it, and the
synthesizer still answers the original question. The prompt is general and fixed before any
measurement: nothing in it is tuned to a test set's wording.
"""

from __future__ import annotations

import logging
from typing import Any

from config_loader import cfg
from llm.provider import get_agent_llm

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Rewrite the user's question as a short search query for finding the passage that answers it "
    "in a document collection. Keep every entity, company, product, year, period and metric name. "
    "Drop instructions about how to format or reason about the answer. Reply with the query only, "
    "on one line, no quotes."
)


def clean_rewrite(text: str | None, original: str) -> str:
    """The model's reply as a one-line query; the original question if the reply is unusable."""
    line = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    line = line.strip().strip('"').strip("'").strip()
    if not line or len(line) > int(cfg.retrieval.query_rewrite_max_chars):
        return original
    return line


async def rewrite_for_search(question: str) -> tuple[str, dict[str, Any]]:
    """(search query, usage). On any model error the original question is searched."""
    try:
        result = await get_agent_llm("normalizer").complete(
            system=SYSTEM_PROMPT, messages=[{"role": "user", "content": question}],
            max_tokens=int(cfg.retrieval.query_rewrite_max_tokens),
        )
    except Exception as exc:  # noqa: BLE001 -- rewriting is an improvement, never a reason to fail retrieval
        logger.warning("query_rewrite: model call failed (%s); searching the original question", type(exc).__name__)
        return question, {"tokens_in": 0, "tokens_out": 0, "llm_calls": 1, "rewritten": False}
    query = clean_rewrite(result.text, question)
    return query, {"tokens_in": result.tokens_in, "tokens_out": result.tokens_out, "llm_calls": 1,
                   "rewritten": query != question}

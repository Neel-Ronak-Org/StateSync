"""
orchestrator/passage_jev.py
Re-orders the documents engine's passages by Jev's probability that each contains what the question needs.

WHY: in Paper B's pre-registered page-finding test (300 new questions; research/paper_b/protocols/
paperb_reruns_prereg.md, rerun R1), ordering the dense search's 25 passages by this probability put the
source page first for 49.3% of questions against 20.3% for the dense order. This module asks the same
question, over the same state text, inside the documents route, so the answer writer reads the passages in
Jev's order. One Jev request per passage, at most [retrieval] jev_rerank_concurrency at once (the API refuses
25 at once). A passage whose request fails keeps no score and stays after the scored ones in its original
order, so a Jev outage leaves the search's own order.
"""

from __future__ import annotations

import asyncio
from typing import Any

import jev_client
from config_loader import cfg

INSTRUCTIONS = "Does this passage contain the information needed to answer the question?"   # R1's exact question


def passage_state(question: str, match: dict[str, Any], text_chars: int) -> str:
    """R1's state text: the question, then the passage's title, page and opening characters."""
    title = match.get("title") or match.get("document_id")
    return (f"Question: {question}\n\nPassage from {title}, page {match.get('page')}:\n"
            f"{(match.get('content_md') or '')[:text_chars]}")


def order_by_scores(matches: list[dict[str, Any]], scores: list[float | None]) -> list[dict[str, Any]]:
    """Scored passages by score, highest first (ties keep their order), then unscored ones in their order;
    each match gains a jev_score."""
    tagged = [({**m, "jev_score": s}, i) for i, (m, s) in enumerate(zip(matches, scores, strict=True))]
    scored = sorted((t for t in tagged if t[0]["jev_score"] is not None), key=lambda t: (-t[0]["jev_score"], t[1]))
    unscored = [t for t in tagged if t[0]["jev_score"] is None]
    return [m for m, _ in scored + unscored]


async def jev_order(question: str, matches: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(matches in Jev's order, trace {"scored", "failed", "ms"})."""
    if not matches:
        return matches, {"scored": 0, "failed": 0, "ms": 0}
    limit = asyncio.Semaphore(int(cfg.retrieval.jev_rerank_concurrency))
    text_chars = int(cfg.retrieval.jev_rerank_text_chars)

    async def one(m: dict[str, Any]) -> tuple[float | None, int]:
        async with limit:
            answer = await jev_client.ask(passage_state(question, m, text_chars), {"contains": INSTRUCTIONS})
        return (None if answer.error else answer.probabilities.get("contains")), answer.ms

    results = await asyncio.gather(*(one(m) for m in matches))
    scores = [s for s, _ in results]
    trace = {"scored": sum(s is not None for s in scores), "failed": sum(s is None for s in scores),
             "ms": max(ms for _, ms in results)}
    return order_by_scores(matches, scores), trace

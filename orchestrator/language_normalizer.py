"""
orchestrator/language_normalizer.py
LanguageNormalizer seam (A8) -- the D2 multilingual provider slot (Appendix 4:
a future GRPO fine-tuned model would plug in as normalization.provider =
"finetuned" without changing call sites). Only PromptNormalizer ships here.

Resolver key: normalization.provider, in config/flags.toml.

Per D1: the hard requirement is entity preservation, not translation quality.
Any proper noun in the raw query -- seller/city/product/provider name, in
whatever script it appears -- must be carried through byte-identical into
normalized_query and recorded as a self-mapping in preserved_entities.
Downstream SQL construction (sql_agent.py) is told these are exact-match
literals, and that a Devanagari value belongs in the matching *_local
column (sellers.display_name_local, locations.city_local,
catalog_items.title_local) rather than the English column.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable

from config_loader import cfg, flags
from llm.provider import get_agent_llm

logger = logging.getLogger(__name__)

_VALID_PROVIDERS = frozenset({"prompt"})
_VALID_LANGUAGES = frozenset({"en", "hi", "hinglish", "other"})

_SYSTEM_PROMPT = """\
You are a language-detection and query-normalization step for an ONDC
logistics analytics system. You will be given one user query, which may be
in English, Hindi (Devanagari script), Hinglish (Hindi written in Roman
script, often code-mixed with English), or something else.

Task:
1. Detect the language: "en", "hi", "hinglish", or "other". Give a
   confidence score from 0.0 to 1.0.
2. If the language is "hi" or "hinglish": rewrite the query as a clear
   English-equivalent question, suitable for a downstream query router and
   SQL generator. Preserve the original meaning and intent exactly.
3. If the language is "en": copy the query through unchanged.

Entity preservation is the most important rule, more important than
translation quality:
- Any proper noun -- a seller/store name, city name, product name,
  logistics provider name, or any other specific named entity -- must be
  copied through EXACTLY as it appears in the original query, in whatever
  script it was written in. Do NOT translate it. Do NOT transliterate it.
  Do NOT normalize spacing or spelling. Copy the exact substring.
- List every such preserved entity in "preserved_entities" as a
  self-mapping: the exact original substring as BOTH key and value. The
  value is NEVER an English gloss or translation of the entity -- it is
  always identical to the key, character for character.
- A query with no proper nouns has an empty preserved_entities object.

Worked example. Input: "विक्रेता 5 ने मुंबई में कितने ऑर्डर भेजे हैं?"
Correct output:
{"detected_language": "hi", "confidence": 0.95,
 "normalized_query": "How many orders has विक्रेता 5 shipped in मुंबई?",
 "preserved_entities": {"विक्रेता 5": "विक्रेता 5", "मुंबई": "मुंबई"}}
Note that "मुंबई" is NOT rewritten to "Mumbai" anywhere, including inside
normalized_query -- and preserved_entities maps it to itself, not to an
English translation.

Respond with ONLY a JSON object, no markdown, no explanation:
{"detected_language": "en"|"hi"|"hinglish"|"other", "confidence": <0.0-1.0>,
 "normalized_query": "<string>", "preserved_entities": {"<original>": "<original>", ...}}"""


@runtime_checkable
class LanguageNormalizer(Protocol):
    async def normalize(self, raw_query: str) -> dict[str, Any]:
        """
        Returns {"detected_language", "normalized_query", "preserved_entities"}.
        normalized_query is never None -- callers get raw_query back verbatim
        when no rewrite applies (English input, low-confidence detection, or
        provider failure).
        """
        ...


def _parse_response(text: str, raw_query: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?|```", "", text).strip()
    try:
        parsed = json.loads(cleaned)
        language = str(parsed.get("detected_language", "other")).lower()
        if language not in _VALID_LANGUAGES:
            language = "other"
        confidence = float(parsed.get("confidence", 0.0))
        normalized_query = str(parsed.get("normalized_query") or raw_query)
        preserved_entities = parsed.get("preserved_entities") or {}
        if not isinstance(preserved_entities, dict):
            preserved_entities = {}
        preserved_entities = {str(k): str(v) for k, v in preserved_entities.items()}
        return {
            "detected_language": language,
            "confidence": confidence,
            "normalized_query": normalized_query,
            "preserved_entities": preserved_entities,
        }
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning(
            "LanguageNormalizer could not parse response %r (%s) -- passing through.",
            text[:200], exc,  # noqa: literals — log truncation width
        )
        return {
            "detected_language": "other",
            "confidence": 0.0,
            "normalized_query": raw_query,
            "preserved_entities": {},
        }


class PromptNormalizer:
    """One LLM call: detect language + confidence, and — only above
    detect_confidence_min and only for hi/hinglish — rewrite with entity
    preservation. English and low-confidence/other inputs pass through
    with normalized_query == raw_query (the rewrite step is skipped, not
    the detection call itself)."""

    async def normalize(self, raw_query: str) -> dict[str, Any]:
        llm = get_agent_llm("normalizer")
        result = await llm.complete(
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": raw_query}],
            max_tokens=cfg.sql.max_tokens_normalizer,
        )
        parsed = _parse_response(result.text, raw_query)

        language = parsed["detected_language"]
        below_threshold = parsed["confidence"] < cfg.normalization.detect_confidence_min
        if below_threshold:
            language = "other"

        if language in ("en", "other"):
            normalized_query = raw_query
            # The rewrite is skipped (query passes through byte-identical),
            # but the model's own entity extraction is still worth keeping --
            # a "hi"/"hinglish" guess that fell below detect_confidence_min
            # (forced to "other" above) or a genuinely English query can
            # still name a real proper noun the SQL Agent should be told not
            # to second-guess. Sanity-filtered to entries whose value is an
            # actual substring of raw_query, since nothing downstream should
            # trust a hallucinated entity that was never in the input.
            preserved_entities: dict[str, str] = {
                k: v for k, v in parsed["preserved_entities"].items() if v in raw_query
            }
            logger.info(
                "LanguageNormalizer: detected_language=%s confidence=%.2f -- "
                "gate skipped (pass-through), normalized_query == raw_query, "
                "%d entities carried through from detection.",
                language, parsed["confidence"], len(preserved_entities),
            )
        else:
            normalized_query = parsed["normalized_query"]
            preserved_entities = parsed["preserved_entities"]
            logger.info(
                "LanguageNormalizer: detected_language=%s confidence=%.2f -- "
                "rewrote query, preserved %d entities.",
                language, parsed["confidence"], len(preserved_entities),
            )

        return {
            "detected_language": language,
            "normalized_query": normalized_query,
            "preserved_entities": preserved_entities,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
        }


@lru_cache(maxsize=1)
def get_language_normalizer() -> "LanguageNormalizer":
    provider: str = str(flags.normalization.provider)
    if provider not in _VALID_PROVIDERS:
        raise RuntimeError(
            f"Unknown normalization.provider {provider!r}. Valid values: "
            f"{sorted(_VALID_PROVIDERS)}. Check config/flags.toml [normalization] provider."
        )
    logger.info("LanguageNormalizer resolved: provider=%s", provider)
    return PromptNormalizer()

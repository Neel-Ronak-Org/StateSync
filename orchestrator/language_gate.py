"""
orchestrator/language_gate.py
Cheap script/word test deciding whether a question needs the language model.

WHY THIS EXISTS. pipeline.language_gate = "script" skips the normalizer call
for questions that are plainly English. The gate must err toward asking the
model: a false "needs model" costs one call, a false "English" would send
Hindi or Hinglish to the SQL agent untranslated. So it fires on Devanagari
script, or on several common Hindi function words written in Latin script
(config: normalization.hinglish_markers / hinglish_min_markers).
"""

from __future__ import annotations

import re

from config_loader import cfg

_DEVANAGARI = re.compile("[ऀ-ॿ]")
_WORD = re.compile(r"[a-z]+")


def needs_language_model(text: str) -> bool:
    """True when the text is Devanagari or looks like romanised Hindi."""
    if _DEVANAGARI.search(text):
        return True
    markers = frozenset(str(m).lower() for m in cfg.normalization.hinglish_markers)
    hits = sum(1 for word in _WORD.findall(text.lower()) if word in markers)
    return hits >= int(cfg.normalization.hinglish_min_markers)

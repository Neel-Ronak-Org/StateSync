"""
orchestrator/classifiers/error_classifier.py
A3 -- deterministic, non-LLM error classifier (D14 hook).

Loads config/error_taxonomy.json. Maps (sqlstate, message, empty_result) to
(ErrorCategory, remediation_hint). Runs before anything is written to
state.errors -- a raw engine string never enters state unclassified;
"unclassified" is itself a classification and carries the fallback hint.

Adding a category means adding a rule to error_taxonomy.json. It never
means changing this module's control flow (A3 rule) -- classify_error()
has no branch that names a specific category; every category comes from
data.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_TAXONOMY_FILENAME = "error_taxonomy.json"

# Terminal categories do not retry (A3): permission_denied because retrying
# it is an agent probing around a security control; unclassified because
# guessing a repair for an unrecognized failure is exactly what A3 forbids
# ("do not guess at a repair"). This pair is a Strategy-level decision (A3
# prose), not a per-category rule, so it lives here rather than in the
# taxonomy file's data.
# no_data_source (routing_docs_fixes.md F-S2) is terminal too: the query read no table because the
# database does not hold what was asked, and a retry only produces another constant or a guess at an
# unrelated table. It is the signal for route recovery, not for a SQL repair.
TERMINAL_CATEGORIES: frozenset[str] = frozenset({"permission_denied", "no_data_source", "unclassified"})


def _taxonomy_path() -> Path:
    env = os.environ.get("CONFIG_DIR")
    if env:
        return Path(env) / _TAXONOMY_FILENAME
    here = Path(__file__).resolve().parent
    for candidate in [here, here.parent, here.parent.parent]:
        p = candidate / "config" / _TAXONOMY_FILENAME
        if p.is_file():
            return p
    raise RuntimeError(
        f"Cannot locate {_TAXONOMY_FILENAME}. Set the CONFIG_DIR environment variable."
    )


@lru_cache(maxsize=1)
def _load_taxonomy() -> dict[str, Any]:
    with open(_taxonomy_path()) as f:
        return json.load(f)


def classify_error(
    *,
    sqlstate: str | None,
    message: str | None,
    empty_result: bool = False,
) -> tuple[str, str]:
    """
    Classify an engine failure, or an empty-but-ok: true result, per A3.

    Args:
        sqlstate:     The engine SQLSTATE, or None if there wasn't one
                      (e.g. a successful-but-empty result).
        message:      The engine error message. Ignored (but should be
                      empty/None) when classifying an empty_result.
        empty_result: True when the caller is classifying an ok: true
                      response with zero rows, not an engine failure.

    Returns:
        (category, remediation_hint) -- hint is the taxonomy's verbatim
        text, never LLM-generated (A3, C2-V1 condition 2).
    """
    taxonomy = _load_taxonomy()
    message = message or ""

    for rule in taxonomy["rules"]:
        match = rule["match"]

        if "empty_result" in match and match["empty_result"] != empty_result:
            continue
        if "sqlstate" in match and match["sqlstate"] != sqlstate:
            continue
        if "message_regex" in match and not re.search(match["message_regex"], message):
            continue

        return rule["category"], rule["hint"]

    fallback = taxonomy["fallback"]
    return fallback["category"], fallback["hint"]

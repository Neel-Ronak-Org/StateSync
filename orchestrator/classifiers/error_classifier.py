"""
orchestrator/classifiers/error_classifier.py
Turns a failed SQL attempt into a category and a fixed hint, with no model call.

The rules are data, in config/error_taxonomy.json: each matches a Postgres SQLSTATE, a message pattern or an
empty result, and gives a category ("unknown_column", "timeout", ...) and the hint the SQL writer is shown on
its retry. The first matching rule wins; anything unmatched is "unclassified". A raw engine message never
reaches the state unclassified, and the hint is always the file's text, never the model's. Adding a category
means adding a rule, not changing this code.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_TAXONOMY_FILENAME = "error_taxonomy.json"

# Categories that never retry: permission_denied (retrying would be probing a security control),
# unclassified (guessing a repair for an unrecognised failure is not a repair), and no_data_source (the query
# read no table because the database does not hold what was asked; it is the signal for route recovery).
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
    Classify an engine failure, or a query that ran and returned no rows.

    Args:
        sqlstate:     The engine SQLSTATE, or None if there wasn't one
                      (e.g. a successful-but-empty result).
        message:      The engine error message. Ignored (but should be
                      empty/None) when classifying an empty_result.
        empty_result: True when the caller is classifying an ok: true
                      response with zero rows, not an engine failure.

    Returns:
        (category, remediation_hint) -- the hint is the taxonomy's text, never the model's.
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

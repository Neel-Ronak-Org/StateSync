"""
scripts/api_auth.py
Shared bearer-token authentication helper for StateSync verification and benchmark scripts.

Why this fails loudly:
Verification and benchmark scripts in this repository must never run unauthenticated
or silently skip authentication. If a verification script silently ran unauthenticated
or silently skipped auth, it would either fail with opaque transport errors or hit
open routes reporting a PASS that means nothing. This repository's norm is that a
verification must never be weakened: if authentication cannot be resolved, we abort
immediately with an actionable RuntimeError naming both the environment variable and
the generation utility.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOKEN_ENV = "STATESYNC_API_TOKEN"
TOKEN_FILE_DEFAULT = REPO_ROOT / "secrets" / "api_token_dev"


def api_token() -> str:
    """
    Resolve the StateSync API bearer token.

    Resolution order:
    1. os.environ[TOKEN_ENV] if set and non-empty.
    2. The contents of TOKEN_FILE_DEFAULT, stripped, if the file exists and is non-empty.
    3. Raise RuntimeError with an actionable message that names both TOKEN_ENV
       and `python3 scripts/gen_api_principal.py` as the fix.
    """
    token = os.environ.get(TOKEN_ENV)
    if token is not None and token.strip():
        return token.strip()

    token_file = Path(TOKEN_FILE_DEFAULT)
    if token_file.is_file():
        try:
            content = token_file.read_text(encoding="utf-8").strip()
            if content:
                return content
        except OSError:
            pass

    raise RuntimeError(
        f"Missing StateSync API token. Set environment variable {TOKEN_ENV} or "
        f"run `python3 scripts/gen_api_principal.py` to generate a local dev token."
    )


def auth_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """
    Returns {"Authorization": f"Bearer {api_token()}"} merged with `extra`
    (extra wins on key collision). Used so callers can write
    auth_headers({"Content-Type": "application/json"}).
    """
    headers = {"Authorization": f"Bearer {api_token()}"}
    if extra:
        headers.update(extra)
    return headers

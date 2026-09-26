"""
orchestrator/auth.py
Bearer-token authentication, per-principal rate limiting, and credential lifecycle.

Architectural decisions:
1. Bearer tokens rather than cookies: The frontend runs at http://localhost:3000
   and calls the orchestrator cross-origin at http://localhost:8090. A cross-origin
   cookie requires SameSite=None; Secure, which mandates HTTPS. Local development
   runs plaintext HTTP, where browsers reject cross-origin Secure cookies. Bearer
   tokens transmitted in the Authorization header are origin-agnostic and operate
   consistently across both local development and production environments.
2. SHA-256 hashed credentials: The principal file stores only SHA-256 hex digests
   rather than plaintext bearer tokens. If the secrets file or container volume is
   inspected or inadvertently exposed, it contains no usable credential. All token
   evaluations hash the presented token and perform constant-time comparisons
   against stored hashes.
3. Fail-closed startup: init_auth() is called eagerly during application lifespan.
   If the principals secret file is missing, empty, unreadable, or malformed,
   startup raises a RuntimeError immediately. A misconfigured deployment cannot
   silently run unauthenticated or serve traffic in a compromised state.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from dataclasses import dataclass

from config_loader import cfg

logger = logging.getLogger(__name__)

PRINCIPALS_FILE_ENV = "STATESYNC_PRINCIPALS_FILE"
PRINCIPALS_FILE_DEFAULT = "/run/secrets/api_principals"

_SHA256_HEX_LEN = 64  # noqa: literals — protocol constant: SHA-256 hex digest length
_HEX_CHARS = frozenset("0123456789abcdef")
_REQUIRED_PRINCIPAL_KEYS = frozenset({"principal_id", "tenant_id", "token_sha256"})


@dataclass(frozen=True)
class Principal:
    principal_id: str
    tenant_id: str


class AuthError(Exception):
    """Raised when authentication credentials are invalid or missing."""


class RateLimited(Exception):
    """Raised when a principal exceeds their configured request rate limit."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Rate limit exceeded. Retry after {retry_after} seconds.")
        self.retry_after = retry_after


def load_principals() -> dict[str, Principal]:
    """
    Load principals from the JSON secrets file into a SHA-256 hash lookup table.

    Fails closed by raising RuntimeError with actionable messages if the file
    is missing, unreadable, empty, not a JSON array, or contains invalid entries.
    Never logs raw tokens or credential hashes.
    """
    path = os.environ.get(PRINCIPALS_FILE_ENV, PRINCIPALS_FILE_DEFAULT)

    if not os.path.isfile(path):
        raise RuntimeError(
            f"Principals secret file missing at {path}. "
            f"Set {PRINCIPALS_FILE_ENV} or mount the secret."
        )

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        raise RuntimeError(
            f"Principals secret file at {path} could not be read: {exc}"
        ) from exc

    if not isinstance(data, list):
        raise RuntimeError(
            f"Principals secret file at {path} must contain a JSON array, "
            f"got {type(data).__name__}."
        )

    if not data:
        raise RuntimeError(
            f"Principals secret file at {path} is empty. "
            "At least one principal is required."
        )

    principals: dict[str, Principal] = {}
    for idx, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise RuntimeError(
                f"Principals entry at index {idx} in {path} is not a JSON object."
            )

        if set(entry.keys()) != _REQUIRED_PRINCIPAL_KEYS:
            raise RuntimeError(
                f"Principals entry at index {idx} in {path} must have exactly "
                f"keys: {sorted(_REQUIRED_PRINCIPAL_KEYS)}."
            )

        pid = entry["principal_id"]
        tid = entry["tenant_id"]
        token_hash = entry["token_sha256"]

        if not isinstance(pid, str) or not pid.strip():
            raise RuntimeError(
                f"Principals entry at index {idx} in {path} has invalid principal_id: {pid!r}."
            )
        if not isinstance(tid, str) or not tid.strip():
            raise RuntimeError(
                f"Principals entry at index {idx} in {path} has invalid tenant_id: {tid!r}."
            )
        if not isinstance(token_hash, str):
            raise RuntimeError(
                f"Principals entry at index {idx} in {path} has non-string token_sha256."
            )

        norm_hash = token_hash.strip().lower()
        if len(norm_hash) != _SHA256_HEX_LEN or not all(c in _HEX_CHARS for c in norm_hash):  # noqa: literals
            raise RuntimeError(
                f"Principals entry at index {idx} (principal_id={pid}) has "
                "token_sha256 that is not 64 lowercase hex characters."
            )

        if norm_hash in principals:
            raise RuntimeError(
                f"Duplicate token_sha256 detected at index {idx} in {path} "
                f"for principal_id={pid}."
            )

        principals[norm_hash] = Principal(
            principal_id=pid.strip(),
            tenant_id=tid.strip(),
        )

    logger.info(
        "Loaded %d principal(s) from %s: %s",
        len(principals),
        path,
        ", ".join(p.principal_id for p in principals.values()),
    )
    return principals


_PRINCIPALS: dict[str, Principal] | None = None


def init_auth() -> None:
    """Populate _PRINCIPALS via load_principals(); called from the FastAPI lifespan."""
    global _PRINCIPALS
    _PRINCIPALS = load_principals()


def authenticate(authorization_header: str | None) -> Principal:
    """
    Authenticate a request using its Authorization header.

    Computes SHA-256 of the presented bearer token and evaluates against all
    known principals in constant time via hmac.compare_digest without early exit.
    Raises AuthError without echoing presented credentials on failure.
    """
    if _PRINCIPALS is None:
        raise AuthError("Authentication subsystem is not initialized.")

    if not authorization_header or not authorization_header.strip():
        raise AuthError("Missing or empty Authorization header.")

    parts = authorization_header.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():  # noqa: literals
        raise AuthError("Invalid Authorization header format. Expected 'Bearer <token>'.")

    token = parts[1].strip()
    candidate_hash = hashlib.sha256(token.encode("utf-8")).hexdigest().lower()

    matched: Principal | None = None
    for known_hash, principal in _PRINCIPALS.items():
        if hmac.compare_digest(candidate_hash, known_hash):
            matched = principal
        # Deliberate: do not break early on match so comparison count remains
        # constant regardless of which principal matched.

    if matched is None:
        raise AuthError("Invalid bearer token.")

    return matched


class RateLimiter:
    """
    Fixed-window in-process rate limiter per principal_id.

    Thread-safe with a threading.Lock across uvicorn worker threads.
    Prunes fully expired windows on access to bound memory.
    Reads limits from cfg.api.rate_limit_max_requests and
    cfg.api.rate_limit_window_seconds without hardcoded numeric constants (A0.2).
    """

    def __init__(self) -> None:
        self._windows: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def check(self, principal_id: str) -> None:
        """
        Check rate limit for principal_id.

        Raises RateLimited(retry_after) if request ceiling is exceeded in the
        current window, otherwise records the hit and returns None.
        """
        max_requests: int = cfg.api.rate_limit_max_requests
        window_seconds: int = cfg.api.rate_limit_window_seconds

        with self._lock:
            now = time.monotonic()

            # Prune windows that have fully expired so the dict cannot grow without bound
            expired = [
                pid for pid, (start, _) in self._windows.items()
                if now - start >= window_seconds
            ]
            for pid in expired:
                del self._windows[pid]

            if principal_id not in self._windows:
                self._windows[principal_id] = (now, 1)
                return

            window_start, count = self._windows[principal_id]
            if count >= max_requests:
                remaining = window_seconds - (now - window_start)
                retry_after = int(remaining) + 1 if remaining > 0 else 1
                raise RateLimited(retry_after)

            self._windows[principal_id] = (window_start, count + 1)


rate_limiter = RateLimiter()

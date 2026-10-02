"""
mcp/service_auth.py
Service-to-service authentication for the MCP's /rpc endpoint: a shared secret the orchestrator
sends as "Authorization: Bearer <token>" on every call.

WHY THIS EXISTS. The MCP trusts its callers for identity: tenant_id comes from the X-Tenant-ID
header (A0.6) and principal_id from the params, both supplied by the orchestrator after it has
authenticated the person. Until 2026-10-02 nothing checked that the caller WAS the orchestrator,
so any container on app_net (the frontend's nginx, ollama, the evaluator) could reach mcp:8080
and act as any tenant and any principal: read their conversations, pin from them, run their
connected databases. The network boundary was the only control; this adds a second one.

THE RULES:
1. The token is a Docker secret (secrets/mcp_token, mounted at /run/secrets/mcp_token in the
   mcp and orchestrator containers only), never an environment variable or a config value.
2. Fail closed: the MCP refuses to START without a token of at least MIN_TOKEN_CHARS
   characters, so a missing secret is a startup failure, not an open server.
3. The comparison is constant-time (hmac.compare_digest) and the token is never logged.
4. /health stays unauthenticated: it returns only the tool names, and the Docker healthcheck
   needs it.
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path

TOKEN_FILE_ENV = "MCP_TOKEN_FILE"
TOKEN_FILE_DEFAULT = "/run/secrets/mcp_token"
MIN_TOKEN_CHARS = 32  # noqa: literals - 128 bits as hex; a shorter secret is refused at startup
SCHEME = "Bearer "


class ServiceTokenMissing(RuntimeError):
    """The MCP has no usable service token; it must not start."""


def load_token(path: str | None = None) -> str:
    """The shared token from the secret file, or ServiceTokenMissing."""
    file = Path(path or os.environ.get(TOKEN_FILE_ENV, TOKEN_FILE_DEFAULT))
    try:
        token = file.read_text().strip()
    except OSError as exc:
        raise ServiceTokenMissing(
            f"No MCP service token at {file}. Create secrets/mcp_token (see secrets/README.md).") from exc
    if len(token) < MIN_TOKEN_CHARS:
        raise ServiceTokenMissing(f"The MCP service token at {file} is shorter than {MIN_TOKEN_CHARS} characters.")
    return token


def authorized(header_value: str | None, token: str) -> bool:
    """Does an Authorization header carry this token? Constant-time; False for anything malformed."""
    if not token or not isinstance(header_value, str) or not header_value.startswith(SCHEME):
        return False
    return hmac.compare_digest(header_value[len(SCHEME):].strip().encode(), token.encode())

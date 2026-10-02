"""
scripts/mcp_auth.py
The MCP service token for operator scripts that call the MCP's /rpc directly from the host
(through the dev override's 127.0.0.1:8080). Since 2026-10-02 the MCP refuses any call without
it (mcp/service_auth.py). Like api_auth.py, this fails loudly: a script that silently ran
without the token would only collect 401s.

curl callers get the header from a private temporary file (`-H @file`), so the token never
appears in the process list; Python callers use header().
"""

from __future__ import annotations

import atexit
import os
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE_ENV = "MCP_TOKEN_FILE"
TOKEN_FILE_DEFAULT = REPO_ROOT / "secrets" / "mcp_token"
_header_file: str | None = None


def token() -> str:
    path = Path(os.environ.get(TOKEN_FILE_ENV) or TOKEN_FILE_DEFAULT)
    try:
        value = path.read_text().strip()
    except OSError as exc:
        raise RuntimeError(f"No MCP service token at {path}. Create it (see secrets/README.md) "
                           f"or set {TOKEN_FILE_ENV}.") from exc
    if not value:
        raise RuntimeError(f"The MCP service token at {path} is empty.")
    return value


def header() -> str:
    """The Authorization header value."""
    return f"Bearer {token()}"


def curl_header_args() -> list[str]:
    """["-H", "@<file>"]: curl reads the Authorization header from a 0600 temp file."""
    global _header_file
    if _header_file is None:
        fd, path = tempfile.mkstemp(prefix="mcp_auth_", suffix=".hdr")
        with os.fdopen(fd, "w") as f:
            f.write(f"Authorization: {header()}\n")
        atexit.register(lambda: os.path.exists(path) and os.remove(path))
        _header_file = path
    return ["-H", f"@{_header_file}"]

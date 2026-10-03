#!/usr/bin/env python3
"""
scripts/make_connection_key.py
Creates secrets/connection_key: the key the MCP uses to encrypt the passwords of
connected databases (mcp/connections.py, migration 026). A Fernet key is 32
random bytes, URL-safe base64. Refuses to overwrite an existing key, because
every stored password would become unreadable.

Usage: python3 scripts/make_connection_key.py
"""

import base64
import os
import secrets
import sys
from pathlib import Path

KEY = Path(__file__).resolve().parent.parent / "secrets" / "connection_key"

if KEY.exists():
    sys.exit(f"{KEY} already exists; not overwriting it (stored passwords would become unreadable).")
KEY.write_bytes(base64.urlsafe_b64encode(secrets.token_bytes(32)) + b"\n")
os.chmod(KEY, 0o600)
print(f"Wrote {KEY}")

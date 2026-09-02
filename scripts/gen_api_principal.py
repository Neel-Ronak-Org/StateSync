#!/usr/bin/env python3
"""
scripts/gen_api_principal.py
Operator utility to generate a bearer token and write the principal file for the orchestrator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "secrets" / "api_principals.json"
DEFAULT_TOKEN_FILE = REPO_ROOT / "secrets" / "api_token_dev"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a bearer token and register the principal for the StateSync orchestrator."
    )
    parser.add_argument(
        "--principal-id",
        default="p_dev_local",
        help="Identifier for the principal (default: 'p_dev_local').",
    )
    parser.add_argument(
        "--tenant-id",
        default="t_capstone_default",
        help="Tenant identifier bound to this principal (default: 't_capstone_default').",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="Path to the principals JSON file read by the orchestrator (default: secrets/api_principals.json).",
    )
    parser.add_argument(
        "--token-file",
        default=str(DEFAULT_TOKEN_FILE),
        help="Path to write the raw token for local dev/scripts (default: secrets/api_token_dev).",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Add to an existing principals file instead of replacing it.",
    )

    args = parser.parse_args()

    raw_token = secrets.token_urlsafe(32)
    token_sha256 = hashlib.sha256(raw_token.encode("utf-8")).hexdigest().lower()

    new_entry = {
        "principal_id": args.principal_id,
        "tenant_id": args.tenant_id,
        "token_sha256": token_sha256,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.append and out_path.is_file():
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
        except Exception as exc:
            print(f"Error reading existing principals file at {out_path}: {exc}", file=sys.stderr)
            return 1

        if not isinstance(existing_data, list):
            print(f"Error: existing principals file at {out_path} does not contain a JSON array.", file=sys.stderr)
            return 1

        for entry in existing_data:
            if isinstance(entry, dict) and entry.get("principal_id") == args.principal_id:
                print(
                    f"Error: Principal '{args.principal_id}' already exists in {out_path}.",
                    file=sys.stderr,
                )
                return 1

        principals = existing_data + [new_entry]
    else:
        principals = [new_entry]

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(principals, indent=2) + "\n")

    token_path = Path(args.token_file)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(fd, "w", encoding="utf-8") as f:
        f.write(raw_token + "\n")
    os.chmod(token_path, 0o600)

    print(f"Generated bearer token for principal '{args.principal_id}' (tenant: '{args.tenant_id}'):")
    print(f"\n  {raw_token}\n")
    print("WARNING: This token is NOT recoverable from the principals file (only its SHA-256 hash is stored).")
    print(f"Raw token written to {token_path} (mode 0600).")
    print(f"Principals file updated at {out_path}.")
    print("Both output paths are gitignored already (secrets/* is ignored except README.md) and will not be committed.")
    print("Reminder: The orchestrator reads this file at STARTUP; `docker compose restart orchestrator` is required for a new principal to take effect.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

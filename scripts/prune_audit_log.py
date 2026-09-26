#!/usr/bin/env python3
"""
scripts/prune_audit_log.py
Operator tool for enforcing the retention policy on session_state.audit_log
(docs/V3_PLAN.md 0.7).

WHY THIS SCRIPT EXISTS. Every /query, /sessions and auth failure writes an
audit row, and nothing ever removed one: the table grew without bound.

WHY AN OPERATOR SCRIPT AND NOT PRUNE-ON-WRITE (the pattern turn_rows uses).
The application role, statesync_history, holds INSERT and SELECT on the audit
log and nothing else (migration 013). Pruning on write would mean granting it
DELETE, so anything able to act as the app could also erase the record of what
it did. Retention runs here instead, as statesync_migrator, dry run by default.

Reuses prune_turn_rows.py's connection plumbing (the same credential and
psql/docker-exec resolution as scripts/migrate.sh).
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from prune_turn_rows import find_psql_runner, load_migrator_password, query_json


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Delete audit_log rows older than the retention window.\n"
            "Dry run by default: reports what would be deleted and changes nothing unless --apply is passed."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Delete the expired rows. Default is a dry run.")
    parser.add_argument("--retention-days", type=int, default=None, metavar="N",
                        help="Override the window (default: mcp.audit_retention_days in config/statesync.toml).")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    try:
        with open(repo_root / "config" / "statesync.toml", "rb") as f:
            cfg_days = tomllib.load(f)["mcp"]["audit_retention_days"]
    except Exception as exc:
        print(f"ERROR: could not read mcp.audit_retention_days from config/statesync.toml: {exc}", file=sys.stderr)
        return 1

    days = args.retention_days if args.retention_days is not None else cfg_days
    if days < 1:
        print(f"ERROR: retention must be at least one day, got {days}", file=sys.stderr)
        return 1

    password = load_migrator_password(repo_root)
    if not password:
        print("ERROR: pg_migrator_password secret not found.", file=sys.stderr)
        return 1
    try:
        runner, env = find_psql_runner(repo_root, password)
    except ConnectionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    cutoff = f"now() - make_interval(days => {int(days)})"
    stats_sql = f"""
    SELECT json_build_object(
        'total_rows', count(*),
        'expired_rows', count(*) FILTER (WHERE ts < {cutoff}),
        'oldest', min(ts)::text
    ) FROM session_state.audit_log;
    """
    before = query_json(runner, env, repo_root, stats_sql)

    deleted = 0
    if args.apply:
        result = query_json(runner, env, repo_root, f"""
        WITH deleted AS (
            DELETE FROM session_state.audit_log WHERE ts < {cutoff} RETURNING 1
        )
        SELECT json_build_object('deleted', count(*)) FROM deleted;
        """)
        deleted = result.get("deleted", 0)
        after = query_json(runner, env, repo_root, stats_sql)
    else:
        after = before

    mode = "APPLY" if args.apply else "DRY RUN (pass --apply to delete)"
    print(f"Audit log retention — {mode} — window {days} days")
    print(f"  rows before: {before['total_rows']}  (expired: {before['expired_rows']}, oldest: {before['oldest']})")
    if args.apply:
        print(f"  deleted:     {deleted}")
        print(f"  rows after:  {after['total_rows']}")
    else:
        print(f"  would delete {before['expired_rows']} rows; nothing changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

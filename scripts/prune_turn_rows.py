#!/usr/bin/env python3
"""
scripts/prune_turn_rows.py
Operator tool for enforcing the full-row retention policy on session_state.turn_rows.

WHY THIS SCRIPT EXISTS:
retention is enforced opportunistically on write (bounded by history_prune_batch_limit),
which is self-maintaining only while the system is being used; an idle or newly-restored
deployment can therefore hold expired rows indefinitely, and there is no cron, scheduler
or periodic worker anywhere in this stack, so this script is the only way to force the
policy.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


def load_migrator_password(repo_root: Path) -> str:
    """
    Read the migrator password matching scripts/migrate.sh credentials logic:
    first from secrets/pg_migrator_password, falling back to PG_MIGRATOR_PASSWORD env var.
    """
    secret_path = repo_root / "secrets" / "pg_migrator_password"
    if secret_path.is_file():
        pw = secret_path.read_text().strip()
        if pw:
            return pw
    return os.environ.get("PG_MIGRATOR_PASSWORD", "")


def find_psql_runner(repo_root: Path, password: str) -> tuple[list[str], dict[str, str] | None]:
    """
    Locate a working psql command matching scripts/migrate.sh:
    1. Prefer host psql (Homebrew libpq paths on macOS, then PATH).
    2. Fall back to docker compose exec into the postgres container.
    Returns (cmd_tokens, env_dict).
    Raises ConnectionError if postgres is unreachable.
    """
    pg_host = os.environ.get("POSTGRES_HOST", "localhost")
    pg_port = os.environ.get("POSTGRES_PORT", "5432")
    pg_db = os.environ.get("POSTGRES_DB", "statesync")
    pg_user = "statesync_migrator"

    host_candidates = [
        "/opt/homebrew/opt/libpq/bin/psql",
        "/usr/local/opt/libpq/bin/psql",
        "psql",
    ]

    host_env = os.environ.copy()
    host_env["PGPASSWORD"] = password

    for psql_bin in host_candidates:
        if psql_bin != "psql" and not os.path.isfile(psql_bin):
            continue
        cmd = [psql_bin, "-h", pg_host, "-p", pg_port, "-U", pg_user, "-d", pg_db]
        try:
            res = subprocess.run(
                cmd + ["-c", "SELECT 1"],
                env=host_env,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0:
                return cmd, host_env
        except Exception:
            continue

    # Fall back to docker compose exec matching scripts/migrate.sh
    docker_cmd = [
        "docker", "compose", "exec", "-T",
        "-e", f"PGPASSWORD={password}",
        "postgres", "psql",
        "-U", pg_user, "-d", pg_db,
    ]
    try:
        res = subprocess.run(
            docker_cmd + ["-c", "SELECT 1"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if res.returncode == 0:
            return docker_cmd, None
    except Exception:
        pass

    raise ConnectionError(
        "PostgreSQL is unreachable: neither host psql nor 'docker compose exec postgres psql' could connect."
    )


def query_json(runner: list[str], env: dict[str, str] | None, cwd: Path, sql: str) -> dict[str, Any]:
    """
    Execute a query returning a single JSON object via psql -t -A.
    Tolerates leading or trailing non-JSON diagnostic messages (e.g. collation warnings).
    """
    cmd = list(runner) + ["-t", "-A", "-c", sql]
    proc = subprocess.run(cmd, env=env, cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Database query failed (exit code {proc.returncode}): {proc.stderr.strip()}")

    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise RuntimeError(f"No valid JSON object found in psql output:\n{proc.stdout}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prune expired turn_rows from session_state.turn_rows according to the retention policy.\n"
            "Dry run by default: reports what would be deleted and changes nothing unless --apply is passed."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply changes and delete expired turn rows. Default is dry run (no deletions).",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=None,
        metavar="N",
        help="Override retention window in days (default: read history_full_rows_retention_days from config/statesync.toml).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Reduce output to a concise summary.",
    )

    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent

    # Parse config directly using tomllib without importing orchestrator config_loader
    config_file = repo_root / "config" / "statesync.toml"
    if not config_file.is_file():
        print(f"ERROR: Configuration file not found at {config_file}", file=sys.stderr)
        return 1

    try:
        with open(config_file, "rb") as f:
            cfg_data = tomllib.load(f)
        cfg_retention_days = cfg_data["mcp"]["history_full_rows_retention_days"]
    except Exception as exc:
        print(f"ERROR: Failed to read history_full_rows_retention_days from {config_file}: {exc}", file=sys.stderr)
        return 1

    retention_days = args.retention_days if args.retention_days is not None else cfg_retention_days
    if retention_days < 0:
        print(f"ERROR: retention-days must be non-negative, got {retention_days}", file=sys.stderr)
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

    stats_sql = f"""
    SELECT json_build_object(
        'total_rows', count(*),
        'expired_rows', count(*) FILTER (WHERE computed_at < now() - make_interval(days => {retention_days})),
        'expired_bytes', coalesce(sum(byte_size) FILTER (WHERE computed_at < now() - make_interval(days => {retention_days})), 0),
        'expired_bytes_pretty', pg_size_pretty(coalesce(sum(byte_size) FILTER (WHERE computed_at < now() - make_interval(days => {retention_days})), 0)::bigint)
    ) FROM session_state.turn_rows;
    """

    try:
        before_stats = query_json(runner, env, repo_root, stats_sql)
    except Exception as exc:
        print(f"ERROR: Failed to query turn_rows stats: {exc}", file=sys.stderr)
        return 1

    reclaimed_rows = 0
    reclaimed_bytes = 0
    reclaimed_bytes_pretty = "0 bytes"

    if args.apply:
        delete_sql = f"""
        WITH deleted AS (
            DELETE FROM session_state.turn_rows
            WHERE computed_at < now() - make_interval(days => {retention_days})
            RETURNING byte_size
        )
        SELECT json_build_object(
            'reclaimed_rows', count(*),
            'reclaimed_bytes', coalesce(sum(byte_size), 0),
            'reclaimed_bytes_pretty', pg_size_pretty(coalesce(sum(byte_size), 0)::bigint)
        ) FROM deleted;
        """
        try:
            delete_result = query_json(runner, env, repo_root, delete_sql)
            reclaimed_rows = delete_result.get("reclaimed_rows", 0)
            reclaimed_bytes = delete_result.get("reclaimed_bytes", 0)
            reclaimed_bytes_pretty = delete_result.get("reclaimed_bytes_pretty", "0 bytes")
            after_stats = query_json(runner, env, repo_root, stats_sql)
        except Exception as exc:
            print(f"ERROR: Failed to prune expired turn_rows: {exc}", file=sys.stderr)
            return 1
    else:
        # Dry run: no changes made; after stats equal before stats
        after_stats = before_stats

    # Output formatting
    if args.quiet:
        if args.apply:
            print(
                f"Reclaimed {reclaimed_rows} rows ({reclaimed_bytes_pretty}). "
                f"Remaining turn_rows: {after_stats['total_rows']}."
            )
        else:
            print(
                f"DRY RUN: {before_stats['expired_rows']} expired rows ({before_stats['expired_bytes_pretty']}) "
                f"older than {retention_days} days would be pruned. Total rows: {before_stats['total_rows']}."
            )
    else:
        mode_label = "APPLY" if args.apply else "DRY RUN (pass --apply to execute)"
        print("=" * 65)
        print(f"Turn Rows Retention Policy — {mode_label}")
        print(f"Retention window: {retention_days} days")
        print("=" * 65)
        print("\nBefore:")
        print(f"  Total turn_rows:       {before_stats['total_rows']}")
        print(f"  Expired turn_rows:     {before_stats['expired_rows']}")
        print(f"  Expired byte size:     {before_stats['expired_bytes_pretty']} ({before_stats['expired_bytes']} bytes)")

        if args.apply:
            print(f"\nAction: Reclaimed {reclaimed_rows} rows ({reclaimed_bytes_pretty}, {reclaimed_bytes} bytes).")
            print("\nAfter:")
            print(f"  Total turn_rows:       {after_stats['total_rows']}")
            print(f"  Expired turn_rows:     {after_stats['expired_rows']}")
            print(f"  Expired byte size:     {after_stats['expired_bytes_pretty']} ({after_stats['expired_bytes']} bytes)")
        else:
            print("\nAfter:")
            print(f"  Total turn_rows:       {after_stats['total_rows']} (unchanged)")
            print(f"  Expired turn_rows:     {after_stats['expired_rows']} (unchanged)")
            print(f"  Expired byte size:     {after_stats['expired_bytes_pretty']} (unchanged)")
            print(f"\nDRY RUN: 0 rows deleted. Pass --apply to delete {before_stats['expired_rows']} expired rows.")

        print(
            "\nReminder: Deleting turn_rows does NOT delete conversation history — "
            "the turn survives and degrades to its stored preview, with sql_rows_count still carrying the true row count."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
tests/test_rls_coverage.py
The RLS-coverage test (docs/V3_PLAN.md Phase 3, §5.5): every table in
StateSync's own store has row-level security.

WHY THIS EXISTS. Row-level security is the backstop behind every per-statement
tenant filter, and a backstop that silently misses one table is worse than
none, because everyone believes it is there. CVE-2025-48757 is that failure at
scale: 170 of 1,645 scanned apps exposed data through a table left without it.
Migration 021 turned RLS on for every table that existed then, in a loop. This
test makes sure no LATER migration adds a table to session_state or
workspace_docs without turning it on and giving it a policy.

The same property is checked against the live database, over pg_class and
pg_policies, by scripts/verify_workspaces.py.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = sorted((REPO_ROOT / "migrations").glob("*.sql"))
RLS_MIGRATION = 21     # the one that enabled RLS for every table existing then
SCHEMAS = ("session_state", "workspace_docs")

CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\.(\w+)", re.IGNORECASE)
ENABLE = re.compile(r"ALTER\s+TABLE\s+(\w+)\.(\w+)\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY", re.IGNORECASE)
POLICY = re.compile(r"CREATE\s+POLICY\s+\w+\s+ON\s+(\w+)\.(\w+)", re.IGNORECASE)


def _number(path: Path) -> int:
    return int(path.name.split("_", 1)[0])


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def test_the_rls_migration_covers_both_schemas_in_a_loop():
    sql = _strip_comments(next(p for p in MIGRATIONS if _number(p) == RLS_MIGRATION).read_text())
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "nspname IN ('session_state', 'workspace_docs')" in sql
    assert "current_setting(''statesync.tenant_id'', true)" in sql


def test_every_later_table_enables_rls_and_has_a_policy():
    created: dict[tuple[str, str], str] = {}
    enabled: set[tuple[str, str]] = set()
    policed: set[tuple[str, str]] = set()
    for path in MIGRATIONS:
        sql = _strip_comments(path.read_text())
        if _number(path) > RLS_MIGRATION:
            for schema, table in CREATE.findall(sql):
                if schema.lower() in SCHEMAS:
                    created[(schema.lower(), table.lower())] = path.name
        enabled.update((s.lower(), t.lower()) for s, t in ENABLE.findall(sql))
        policed.update((s.lower(), t.lower()) for s, t in POLICY.findall(sql))
    missing = [f"{s}.{t} ({m}): {'no RLS' if (s, t) not in enabled else 'no policy'}"
               for (s, t), m in created.items() if (s, t) not in enabled or (s, t) not in policed]
    assert not missing, "tables without row-level security: " + "; ".join(missing)


def test_the_detector_would_catch_a_table_without_rls(tmp_path):
    """The rule itself, on a made-up later migration."""
    sql = "CREATE TABLE IF NOT EXISTS session_state.secrets_log (id TEXT, tenant_id TEXT);"
    found = [(s, t) for s, t in CREATE.findall(sql) if s in SCHEMAS]
    assert found == [("session_state", "secrets_log")]
    assert not ENABLE.findall(sql) and not POLICY.findall(sql)

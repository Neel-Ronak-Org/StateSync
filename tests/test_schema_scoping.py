"""
tests/test_schema_scoping.py
Pins the safety argument behind making a second schema discoverable.

WHY THIS EXISTS. Loading Olist put 9 real tables in the database. They are in
schema `olist`, NOT `public`, for one reason: A5 defines the schema fingerprint
over the application schema and D4 validates that fingerprint on every
trajectory read. A fingerprint change invalidates every stored trajectory --
the same blast radius documented for adding tenant_id.

Making `olist` DISCOVERABLE (schema.list_tables / describe_table can be pointed
at it) is only safe while that separation holds. "It holds because
fingerprint.py hardcodes public" is a claim about source code, and this project
has been bitten repeatedly by claims about source code that nothing checked --
so it is asserted here instead.

These are static assertions over source and config: no database, no container,
no model. They run in CI.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = REPO_ROOT / "mcp"
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(MCP_DIR))

FINGERPRINT_SRC = (MCP_DIR / "fingerprint.py").read_text()
SCHEMA_TOOLS_SRC = (MCP_DIR / "tools" / "schema_tools.py").read_text()


# ── The safety property: the fingerprint cannot see another schema ───────────

def test_fingerprint_is_hardcoded_to_public():
    """
    THE LOAD-BEARING ASSERTION. Every schema filter in fingerprint.py must be a
    LITERAL 'public', never a parameter. If one becomes parameterised, the A5
    fingerprint becomes reachable from a caller-supplied schema and D4's
    guarantee evaporates silently.
    """
    filters = re.findall(r"(?:table_schema|nspname)\s*=\s*(\S+)", FINGERPRINT_SRC)
    assert filters, "no schema filter found in fingerprint.py -- has it been rewritten?"
    for f in filters:
        assert f.startswith("'public'"), (
            f"fingerprint.py has a non-literal schema filter ({f!r}). The A5 "
            f"fingerprint must be structurally incapable of covering another "
            f"schema; see migrations/007_olist_real_data.sql."
        )


def test_fingerprint_takes_no_schema_parameter():
    """compute_fingerprint must not accept a schema argument at all."""
    sig = re.search(r"async def compute_fingerprint\(([^)]*)\)", FINGERPRINT_SRC)
    assert sig, "compute_fingerprint not found"
    assert "schema" not in sig.group(1), (
        "compute_fingerprint accepts a schema parameter -- the A5 fingerprint "
        "must not be caller-steerable."
    )


# ── Discovery scoping ────────────────────────────────────────────────────────

# NOTE: these assert over SOURCE rather than importing schema_tools. Importing
# it pulls mcp/db.py -> psycopg_pool, which exists in the mcp container and not
# on a host or a CI runner. A test that can only run in one container is a test
# that stops running; verify_tenancy.py already carries that constraint and it
# is why the tenancy suite needs `docker compose exec`. These stay static so
# they run in CI on every push.

def test_discovery_defaults_to_public():
    """Every existing caller must be unaffected: the parameter defaults to A5's
    schema, so behaviour is byte-for-byte unchanged unless asked otherwise."""
    assert 'DEFAULT_SCHEMA = "public"' in SCHEMA_TOOLS_SRC
    for fn in ("list_tables", "describe_table"):
        assert re.search(rf"async def {fn}\([^)]*schema: str = DEFAULT_SCHEMA", SCHEMA_TOOLS_SRC), (
            f"{fn} does not default its schema parameter to DEFAULT_SCHEMA")


def test_both_discovery_tools_validate_the_schema():
    """A8's rule applied to schemas: an unresolvable name is an error, never a
    quiet fallback. Both entry points must call the validator."""
    assert SCHEMA_TOOLS_SRC.count("_validate_schema(schema)") >= 2, (
        "list_tables and describe_table must BOTH validate; one unguarded entry "
        "point is the whole hole")
    assert "raise ValueError" in SCHEMA_TOOLS_SRC.split("def _validate_schema")[1][:600]


def test_reconnaissance_schemas_are_not_discoverable():
    """pg_catalog / information_schema are reconnaissance surfaces, not analysis
    ones. They must never be in the allowlist."""
    allow = re.search(r"ALLOWED_SCHEMAS\s*=\s*frozenset\(\{([^}]*)\}\)", SCHEMA_TOOLS_SRC)
    assert allow, "ALLOWED_SCHEMAS not found"
    names = {n.strip().strip('"\'') for n in allow.group(1).split(",") if n.strip()}
    assert names == {"public", "olist"}, f"unexpected discoverable schemas: {names}"
    assert "pg_catalog" not in names and "information_schema" not in names


def test_schema_is_never_string_formatted_into_a_query():
    """
    The schema name reaches the catalog queries as a psycopg PARAMETER. An
    f-string or %-format would make it an injection surface even behind the
    allowlist, and the allowlist is defence in depth, not the primary control.
    """
    for bad in ("nspname = '{", 'nspname = "%s"' % "{", "nspname = '\" + "):
        assert bad not in SCHEMA_TOOLS_SRC
    # every schema filter is a bind parameter
    assert "nspname = %s" in SCHEMA_TOOLS_SRC
    assert re.search(r"nspname\s*=\s*f?['\"]\{", SCHEMA_TOOLS_SRC) is None


def test_list_tables_reports_which_schema_it_listed():
    """A caller must never be able to mistake an olist listing for the A5 one,
    so the response names the schema it covered."""
    assert '"schema": schema' in SCHEMA_TOOLS_SRC

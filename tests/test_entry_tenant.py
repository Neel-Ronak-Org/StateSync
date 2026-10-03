"""
tests/test_entry_tenant.py
The entry node keeps the authenticated principal's tenant.

WHY THIS EXISTS. From the single-tenant phase the entry node always wrote the
config default tenant. Once main.py set the tenant from the principal, every
question from an account in any other tenant failed with "node 'entry'
attempted to mutate immutable field 'tenant_id'" (found in the demo account,
tenant t_demo, 2026-09-30).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

from config_loader import cfg  # noqa: E402
from nodes.entry import entry_node  # noqa: E402


def test_the_principals_tenant_is_kept():
    out = asyncio.run(entry_node({"raw_query": "How many orders?", "tenant_id": "t_demo"}))
    assert out["tenant_id"] == "t_demo"


def test_the_default_stands_in_only_without_a_tenant():
    out = asyncio.run(entry_node({"raw_query": "How many orders?"}))
    assert out["tenant_id"] == cfg.tenancy.default_tenant

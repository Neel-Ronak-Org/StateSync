"""
orchestrator/datasets.py
What kind of dataset a question is about (docs/V3_PLAN.md Phase 3c slice 6).

Three kinds exist: the built-in sample datasets (public, olist, live), and the
owner's own databases, named cn_<16 hex> (mcp/connections.py, migration 026).

Which prompts and switches a dataset gets (the ONDC sample's SQL framing, DIGIPIN, router and
splitter prompts) is decided by profiles.profile_for, not here; this module keeps the neutral
SQL-writer domain phrases that profile_for uses.
"""

from __future__ import annotations

import re
from typing import Any

_CONNECTION_DATASET = re.compile(r"^cn_[0-9a-f]{16}$")
_NEUTRAL_DOMAIN = "business analytics system over a PostgreSQL database"
_OWN_DOMAIN = "business analytics system over the owner's own PostgreSQL database"


def is_connection_dataset(name: Any) -> bool:
    """An owner's own database (mcp/connections.py)."""
    return isinstance(name, str) and bool(_CONNECTION_DATASET.match(name))

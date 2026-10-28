"""
orchestrator/datasets.py
Is a dataset name one of the owner's own databases?

New questions are asked only in workspaces, and a workspace sits on a connected database, named
cn_<16 hex> (mcp/connections.py, migration 026). The old built-in sample schemas (public, olist, live)
remain readable by the MCP for older conversations and pins only.
"""

from __future__ import annotations

import re
from typing import Any

_CONNECTION_DATASET = re.compile(r"^cn_[0-9a-f]{16}$")


def is_connection_dataset(name: Any) -> bool:
    """An owner's own database (mcp/connections.py)."""
    return isinstance(name, str) and bool(_CONNECTION_DATASET.match(name))

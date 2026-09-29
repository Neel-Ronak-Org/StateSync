"""
orchestrator/datasets.py
What kind of dataset a question is about (docs/V3_PLAN.md Phase 3c slice 6).

Three kinds exist: the built-in sample datasets (public, olist, live), and the
owner's own databases, named cn_<16 hex> (mcp/connections.py, migration 026).

Some code was written for the synthetic ONDC corpus: the SQL writer's
"logistics data analytics system (ONDC network)" framing, the DIGIPIN spatial
resolver, the router's ONDC table list. [pipeline].synthetic_schemas says
where it stays on. It ships as the three built-in datasets, so their measured
behaviour is unchanged; an owner's database is never in it, so questions there
get a neutral framing and no DIGIPIN handling.
"""

from __future__ import annotations

import re
from typing import Any

from config_loader import cfg

_CONNECTION_DATASET = re.compile(r"^cn_[0-9a-f]{16}$")
_SYNTHETIC_DOMAIN = "logistics data analytics system (ONDC network)"
_OWN_DOMAIN = "business analytics system over the owner's own PostgreSQL database"


def is_connection_dataset(name: Any) -> bool:
    """An owner's own database (mcp/connections.py)."""
    return isinstance(name, str) and bool(_CONNECTION_DATASET.match(name))


def synthetic_features(name: Any) -> bool:
    """Is the synthetic-corpus handling (ONDC framing, DIGIPIN) on for this dataset?"""
    return isinstance(name, str) and name in list(cfg.pipeline.synthetic_schemas)


def frame_prompt(prompt: str, name: Any) -> str:
    """The SQL writer's prompt, with the domain framing that fits the dataset."""
    return prompt if synthetic_features(name) else prompt.replace(_SYNTHETIC_DOMAIN, _OWN_DOMAIN)

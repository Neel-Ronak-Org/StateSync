"""
orchestrator/spatial_resolver.py
SpatialResolver seam (A8) -- the D2 spatial provider slot (Appendix 4: a
richer resolver would plug in as spatial.provider = "<other>" without
changing call sites). Only DigipinResolver ships here.

Resolver key: spatial.provider, in config/flags.toml.

Per D1: called as a deterministic pre-step INLINE inside the SQL Agent
(nodes/sql_agent.py) -- this module defines no graph node and is not
registered in the routing table (Callout 2; D-V7 checks exactly 3 routes).
Extracts a DIGIPIN token from the *normalized* query (D1: digipin codes are
plain alphanumeric, language-agnostic, so this always runs post-normalization
regardless of detected_language), decodes it via the pinned 'digipin'
library (D2 Notes: do not hand-implement the grid algorithm), and builds a
ready-made ST_DWithin predicate so the SQL Agent's prompt doesn't have to
rely on the model getting SRID/geography casts right itself (C2 already
showed the local model gets this wrong unprompted -- the spatial_invalid
category).
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable

import digipin

from config_loader import cfg, flags

logger = logging.getLogger(__name__)

_VALID_PROVIDERS = frozenset({"digipin"})

# The library's own grid charset (module.py: GRID = ["FC98","J327","K456","LMPT"]).
# Used only as a cheap prefilter to find candidate tokens in free text; decode()
# itself remains the actual authority -- a candidate that fails to decode is
# not treated as a DIGIPIN. This is a heuristic extraction over natural-language
# text, not a security boundary, so an occasional false-positive candidate that
# fails to decode (and is therefore skipped) is an acceptable trade-off.
_DIGIPIN_CHARSET = "FC98J327K456LMPT"
_TOKEN_RE = re.compile(rf"\b[{_DIGIPIN_CHARSET}]{{10}}\b", re.IGNORECASE)

_RADIUS_KM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:km|kilometers?|kilometres?)", re.IGNORECASE)
_RADIUS_M_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:meters?|metres?|\bm\b)", re.IGNORECASE)

_METERS_PER_KM = 1000


@runtime_checkable
class SpatialResolver(Protocol):
    def resolve(self, normalized_query: str) -> dict[str, Any] | None:
        """
        Returns None if no DIGIPIN token is found. Otherwise returns
        {"digipin", "lat", "lon", "radius_m", "predicate_hint"} -- the last
        being ready-made SQL text for the SQL Agent's prompt.
        """
        ...


def _extract_radius_m(query_text: str) -> int:
    km_match = _RADIUS_KM_RE.search(query_text)
    if km_match:
        return int(float(km_match.group(1)) * _METERS_PER_KM)
    m_match = _RADIUS_M_RE.search(query_text)
    if m_match:
        return int(float(m_match.group(1)))
    return cfg.spatial.default_radius_m


class DigipinResolver:
    """Deterministic, non-LLM: regex-find a candidate token, decode via the
    real library, extract a stated radius or fall back to the config default."""

    def resolve(self, normalized_query: str) -> dict[str, Any] | None:
        for match in _TOKEN_RE.finditer(normalized_query):
            candidate = match.group(0).upper()
            try:
                lat, lon = digipin.decode(candidate)
            except ValueError:
                continue

            radius_m = _extract_radius_m(normalized_query)
            predicate_hint = (
                f"ST_DWithin(locations.geom, "
                f"ST_SetSRID(ST_MakePoint({lon}, {lat}), {cfg.spatial.srid})::geography, "
                f"{radius_m})"
            )
            logger.info(
                "DigipinResolver: found %s -> lat=%.6f lon=%.6f radius_m=%d",
                candidate, lat, lon, radius_m,
            )
            return {
                "digipin": candidate,
                "lat": lat,
                "lon": lon,
                "radius_m": radius_m,
                "predicate_hint": predicate_hint,
            }
        return None


@lru_cache(maxsize=1)
def get_spatial_resolver() -> "SpatialResolver":
    provider: str = str(flags.spatial.provider)
    if provider not in _VALID_PROVIDERS:
        raise RuntimeError(
            f"Unknown spatial.provider {provider!r}. Valid values: "
            f"{sorted(_VALID_PROVIDERS)}. Check config/flags.toml [spatial] provider."
        )
    logger.info("SpatialResolver resolved: provider=%s", provider)
    return DigipinResolver()

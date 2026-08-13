"""
orchestrator/schemas/synthesizer_output.py
Pydantic model + JSON Schema validator for the A7 synthesizer output contract.

Two independent validations (A7 enforcement rule):
  1. In the orchestrator (here) before the response leaves the process.
  2. In the frontend before rendering.

If validation fails: return prose answer with chart: null, log a validation
failure. Never return a repaired chart (A7: "silent repair would hide M7
regressions").
"""

from __future__ import annotations

import logging
from typing import Any

import jsonschema
from pydantic import BaseModel, Field, field_validator

from config_loader import cfg

logger = logging.getLogger(__name__)

# ── JSON Schema (verbatim from A7, constants sourced from cfg) ────────────────
def _build_schema() -> dict[str, Any]:
    """Build the A7 JSON Schema dict from config so values stay in sync."""
    return {
        "$id": "statesync://schemas/synthesizer-output.json",
        "type": "object",
        "required": ["answer_text", "chart", "citations"],
        "additionalProperties": False,
        "properties": {
            "answer_text": {"type": "string", "maxLength": cfg.sql.answer_max_chars},
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["kind", "ref"],
                    "additionalProperties": False,
                    "properties": {
                        "kind": {"enum": ["sql", "chunk"]},
                        "ref":  {"type": "string"},
                        "page": {"type": ["integer", "null"]},
                    },
                },
            },
            "chart": {
                "oneOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "required": ["mark", "encoding", "data"],
                        "additionalProperties": False,
                        "properties": {
                            "$schema":  {"const": "https://vega.github.io/schema/vega-lite/v5.json"},
                            "mark":     {"enum": ["bar", "line", "point", "area", "tick", "rect", "arc"]},
                            "data": {
                                "type": "object",
                                "required": ["values"],
                                "additionalProperties": False,
                                "properties": {
                                    "values": {
                                        "type": "array",
                                        "items": {"type": "object"},
                                        "maxItems": cfg.sql.chart_max_data_rows,
                                    },
                                },
                            },
                            "encoding": {
                                "type": "object",
                                "additionalProperties": {
                                    "type": "object",
                                    "properties": {
                                        "field":     {"type": "string"},
                                        "type":      {"enum": ["quantitative", "nominal", "ordinal", "temporal"]},
                                        "aggregate": {"enum": ["sum", "mean", "count", "min", "max", "median"]},
                                        "title":     {"type": "string"},
                                        "sort":      {"type": ["string", "array", "null"]},
                                    },
                                    "additionalProperties": False,
                                },
                            },
                            "transform": {
                                "type": "array",
                                "maxItems": cfg.sql.chart_max_transforms,
                                "items": {"type": "object", "additionalProperties": True},
                            },
                            "title":  {"type": "string"},
                            "width":  {"type": "integer", "minimum": cfg.sql.chart_min_dimension, "maximum": cfg.sql.chart_max_width},
                            "height": {"type": "integer", "minimum": cfg.sql.chart_min_dimension, "maximum": cfg.sql.chart_max_height},
                        },
                    },
                ],
            },
        },
    }


SYNTHESIZER_OUTPUT_SCHEMA: dict[str, Any] = _build_schema()

_validator = jsonschema.Draft7Validator(SYNTHESIZER_OUTPUT_SCHEMA)


# ── Pydantic model (for type-safety inside orchestrator) ──────────────────────
class Citation(BaseModel):
    model_config = {"extra": "forbid"}
    kind: str
    ref:  str
    page: int | None = None

    @field_validator("kind")
    @classmethod
    def kind_must_be_sql_or_chunk(cls, v: str) -> str:
        if v not in ("sql", "chunk"):
            raise ValueError(f"Citation.kind must be 'sql' or 'chunk', got {v!r}")
        return v


class SynthesizerOutput(BaseModel):
    model_config = {"extra": "forbid"}
    answer_text: str = Field(max_length=4000)  # noqa: literals — Pydantic class-level constraint; enforced by JSON Schema validator above
    chart:       dict | None = None
    citations:   list[Citation] = Field(default_factory=list)


# ── Validation function ───────────────────────────────────────────────────────
def validate_synthesizer_output(raw: dict[str, Any]) -> tuple[bool, str | None]:
    """
    Validates raw synthesizer output against the A7 JSON Schema.

    Returns:
        (True, None)  — valid
        (False, error_message) — invalid; caller must nullify chart and log.
    """
    errors = list(_validator.iter_errors(raw))
    if not errors:
        return True, None

    error_messages = "; ".join(
        f"[{'/'.join(str(p) for p in e.absolute_path) or 'root'}] {e.message}"
        for e in errors
    )
    logger.error(
        "Synthesizer output failed A7 schema validation: %s",
        error_messages,
    )
    return False, error_messages


def safe_synthesizer_output(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Returns the output if it validates, or a safe fallback with chart: null
    if validation fails. Never repairs — only nullifies the chart.

    This is the first of two independent validations (A7: in-process validation).
    The frontend performs the second.
    """
    ok, error = validate_synthesizer_output(raw)
    if ok:
        return raw

    # Nullify chart; preserve answer_text and citations if they are strings/lists.
    logger.warning(
        "Returning chart: null due to A7 validation failure. Error: %s", error
    )
    return {
        "answer_text": raw.get("answer_text", ""),
        "chart": None,
        "citations": raw.get("citations", []),
    }
